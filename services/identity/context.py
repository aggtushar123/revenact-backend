"""Which tenant a request is acting in, resolved in exactly one place.

Phase 1 added memberships and backfilled them. This is where the application
starts *reading* them, and it does so at the one point that matters:
`User.has_capability`, which its own docstring calls "the single authorization
question this codebase asks". Routing that through here puts every permission
check in the application behind one resolution path instead of 122 scattered
reads of `user.organisation`.

**Those 122 reads are deliberately left alone.** They ask "which organisation
does this person belong to", the column still answers that correctly, and
rewriting them all in one change is precisely the wide, hard-to-review edit that
turns a refactor into a cross-tenant leak. They move when the column is dropped,
one app at a time, with the equivalence guard below watching.

**What changes in behaviour, and what does not.**

Nothing, for every organisation that exists today, because the backfill made the
two representations agree and `check_membership_consistency` proves it. Two
rules become *enforceable* that were not expressible before:

- a membership that is pending or suspended grants nothing, even though the
  person still has a role on their user row
- a suspended or archived tenant grants nothing to anybody, without touching a
  single membership row

Both are inert right now: every organisation is active and every membership the
backfill created is active. They exist so that suspending a customer for
non-payment is one field, not a thousand.

Superusers keep bypassing all of it. They are platform staff with no
organisation and so no membership, which is the separation the architecture note
asks for: an operator is not a member of a customer's tenant.
"""

from .models import LIVE_MEMBERSHIP_STATUSES, OrganizationMembership


def active_membership(user) -> OrganizationMembership | None:
    """The membership this person is currently acting under, if any.

    Memoised on the user instance. `has_capability` is called several times per
    request by the DRF permission classes, and `request.user` is one object for
    the life of a request, so without this the authorization path would add a
    query per check — which the query-count tests in `services/customers`
    correctly refuse to allow.

    While `User.organisation` is still the column of record, the membership that
    matches it is the one chosen. That keeps this a strict no-op against today's
    data, and it is the honest position for this phase: real organisation
    switching needs somewhere to *put* the choice, which arrives with session
    context. Until then a person acts in the tenant they already belonged to.
    """
    if user is None or not getattr(user, "pk", None):
        return None

    cached = getattr(user, "_identity_membership_cache", None)
    if cached is not None:
        return cached[0]

    memberships = list(
        OrganizationMembership.objects.filter(
            user=user, status__in=LIVE_MEMBERSHIP_STATUSES
        ).select_related("organisation", "role")
    )
    if not memberships:
        chosen = None
    elif len(memberships) == 1:
        chosen = memberships[0]
    else:
        # More than one live membership: pick the one the column already names,
        # rather than guessing. Ambiguity resolved by data, not by ordering.
        organisation_id = getattr(user, "organisation_id", None)
        chosen = next(
            (m for m in memberships if m.organisation_id == organisation_id),
            # Nothing to disambiguate with. Oldest wins, deterministically, so
            # the same request never resolves two different ways.
            min(memberships, key=lambda m: (m.created_at, m.pk)),
        )

    # A one-tuple, so a resolved None is still a cache hit.
    user._identity_membership_cache = (chosen,)
    return chosen


def organisation_for(user):
    """The tenant this person is acting in.

    Falls back to the column for anyone without a membership, which is how a
    user created before the signal below existed, or a superuser, still resolves.
    """
    membership = active_membership(user)
    if membership is not None:
        return membership.organisation
    return getattr(user, "organisation", None)


def capabilities_for(user) -> list:
    """What this person may do, right now, in the tenant they are acting in.

    **The role still comes from the column, deliberately.** `User.role` remains
    the record of what someone may do in this phase, and it is changed in places
    by `User.objects.filter(...).update(role=...)`, which bypasses model signals
    entirely. Reading permissions from `membership.role` while that is true
    silently strips capabilities from anyone whose role was changed that way —
    which is not a hypothetical: it is what the existing suite caught the first
    time this was written the other way round.

    The membership's own role moves to being authoritative in the phase that
    also moves every role-assignment path onto it. Until then this reads the
    membership for *standing* and the column for *permissions*, and
    `check_membership_consistency` proves the two agree.

    Returns a list rather than raising, so callers stay a simple `in` check. An
    empty list means "nothing here", which is the correct answer for a suspended
    person and for a suspended tenant alike.
    """
    from services.accounts.models import Organisation

    membership = active_membership(user)

    # Standing, from the membership: pending and suspended hold the slot but
    # grant nothing, even though the role is still on the user row.
    if membership is not None and membership.status != OrganizationMembership.Status.ACTIVE:
        return []

    organisation = (
        membership.organisation if membership is not None else getattr(user, "organisation", None)
    )

    # A suspended tenant grants nothing to anyone in it, checked here so that
    # suspending a customer never means editing every membership they have.
    if organisation is not None and organisation.status != Organisation.Status.ACTIVE:
        return []

    role = getattr(user, "role", None)
    return list(getattr(role, "permissions", None) or [])
