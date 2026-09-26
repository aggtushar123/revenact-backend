"""Who can see which Customers and Accounts — the single definition of
record-level visibility, and the one place to change it.

Until this module existed the only boundary anywhere was the tenant:
every view scoped to `request.user.organisation` and stopped, so any
member could read every customer, account, note, email and ticket in
the organisation. Fine for a two-person team, wrong for a real one.

The rule, for a user *without* `view_all_accounts`:

* A **Customer** is visible if you own it, if you own one of its
  Accounts, or if nobody owns it.
* An **Account** is visible if you own it, if you own one of its parent
  Customers, or if nobody owns it.

Reaching in both directions is deliberate. Owning "Apple Inc" gives you
its divisions; owning "Apple EMEA" lets you see the company it belongs
to — without giving you its sibling divisions. A strictly-own rule
would break the account page, whose header names the parent org and
whose Organizations tab lists it: you'd get 404s inside a page you're
allowed to open.

Unowned records staying visible is also deliberate. An unowned record
is nobody's secret, and hiding it would mean the unassigned queue
becomes invisible to exactly the people meant to work it — including,
before `perform_create` learned to default an owner, a record the
caller had just made themselves.

# Not the same thing as "my book"

`CockpitSummaryView` and Copilot's `build_org_context_summary` filter
`owner=user` strictly, and must keep doing so. That's a different
question — *what am I responsible for*, not *what am I allowed to
open* — and the answers genuinely differ: a customer you don't own but
can see through an account should not count toward your own ARR.
services/customers/tests/test_views.py pins that distinction. Don't
route those two through here.

# A consequence worth knowing

`Account.customers` is a many-to-many. An Account linked to two
Customers with different owners is visible to both of them, and its
children appear in both Customers' rollups. That follows from the rule
above plus the data model saying the two companies share the account —
it's the M2M's meaning, not a hole in this module.
"""

from django.db.models import Q
from django.shortcuts import get_object_or_404

from services.accounts.capabilities import Capability

from .models import Account, Customer


class SystemActor:
    """An organisation acting as itself: the principal a scheduled job uses.

    Every rollup in this app takes a `user` and asks scoping what that user
    may see. A nightly metric snapshot has no user, and picking "some admin"
    to impersonate would tie a job to whichever person happens to hold a
    role. This is the honest alternative: a principal that *is* the
    organisation, sees all of it, and can never be confused with a person.
    Only the two things scoping reads exist on it — `organisation` and
    `has_capability` — so a rollup that quietly started depending on
    anything else about a user would fail loudly here rather than see
    something odd.
    """

    is_authenticated = True
    pk = None
    id = None

    def __init__(self, organisation):
        self.organisation = organisation

    def has_capability(self, capability):
        return True

    def __repr__(self):
        return f"SystemActor({self.organisation!r})"


def sees_everything(user) -> bool:
    """Superusers are covered by `User.has_capability` itself, which
    returns True for them before it ever looks at a Role."""
    return user.has_capability(Capability.VIEW_ALL_ACCOUNTS)


def visible_customers(user):
    """Every Customer this user may open, as a queryset.

    Archived customers are *not* excluded here — `CustomerListCreateView`
    filters those out itself while `CustomerDetailView` deliberately
    doesn't (you can still open an archived record by id). Folding that
    in would quietly change the detail view."""

    base = Customer.objects.filter(organisation=user.organisation)
    if sees_everything(user):
        return base
    # Beyond the CSM's own book: a customer someone answers for in another
    # function, or was asked or asked about in — the knowledge layer
    # (services.knowledge) brings engineers, sales and analysts to an
    # account's page, and a notification that links there must open it.
    # The org chart: a manager sees the customers their reports own, all
    # the way down (services.accounts.hierarchy).
    from services.accounts.hierarchy import subtree_ids

    return base.filter(
        Q(owner=user)
        | Q(owner_id__in=subtree_ids(user))
        | Q(accounts__owner=user)
        | Q(owner__isnull=True)
        | Q(function_owners__user=user)
        | Q(questions__assignee=user)
        | Q(questions__asked_by=user)
        | Q(contributions__author=user)
    ).distinct()


def live_customers(user):
    """The book as it stands: visible, not archived, **and not churned**.

    What every dashboard means by "the book". A customer with a `churn_date`
    has left — they do not belong in a triage queue, a seat-utilisation rate,
    or an ARR forecast's opening balance. Archiving alone was the filter for a
    while, which let a churned-but-unarchived customer keep contributing live
    ARR to the Revenue Forecast; churn and archive are separate actions by
    design (you can wind an account down while keeping it visible), so
    excluding one is not excluding the other.

    Not used by the Organizations list, which applies a broader rule of its
    own: it hides churned rows by default — a `churn_date` *or* the Churn
    stage (`organizations.book.CHURNED`) — and shows them on
    `include_churned=1`, a `churn` lifecycle filter, or by `ids`, so a churned
    record stays reachable. Nor by the Customer Overview, whose whole subject
    is logo retention and churn reasons, and which would compute 100%
    retention over survivors only.
    """
    return visible_customers(user).filter(is_archived=False, churn_date__isnull=True)


def visible_accounts(user):
    """Every Account this user may open, as a queryset.

    The organisation and ownership predicates are separate `.filter()`
    calls on purpose: chaining gives each its own join through the M2M,
    so this reads "has some Customer in my org AND has some Customer
    owned by me" rather than requiring one single Customer to satisfy
    both at once. Within a tenant the two happen to coincide, but the
    chained form is the one that stays correct if that ever stops being
    true."""

    base = Account.objects.filter(customers__organisation=user.organisation)
    if sees_everything(user):
        return base.distinct()
    return base.filter(Q(owner=user) | Q(customers__owner=user) | Q(owner__isnull=True)).distinct()


def pipeline_visible_q(user) -> Q:
    """Opportunities and risks are read department-wise: a person sees
    their own department's plus the undeparted ones; a role that may view
    all accounts, and Leadership, see every department's."""
    from services.accounts.models import User

    if sees_everything(user) or user.function == User.Function.LEADERSHIP:
        return Q()
    # SOC2:AUTH-02 object-level rule for departmental pipeline items
    return Q(department="") | Q(department=user.function)


def visible_children_q(user) -> Q:
    """For the models that hang off a Customer *or* an Account — Task,
    Note, Email, Ticket, Contact, Opportunity, Risk, Survey, Canvas,
    Headline, CustomObjectRecord.

    Drops straight into the place those views currently write
    `Q(customer__organisation=org) | Q(account__customers__organisation=org)`,
    and means the same thing when the caller holds the capability."""

    return Q(customer__in=visible_customers(user)) | Q(account__in=visible_accounts(user))


def customer_rollup_q(user, customer) -> Q:
    """For the `/customers/<id>/…` roll-ups of the models that hang off a
    Customer *or* an Account: this organisation's own records, plus those on
    its accounts that the user may open.

    Being able to open the organisation is not enough for an account-level
    record: an account owned by a colleague stays theirs (`visible_accounts`),
    the same rule `get_visible_account` applies to the account's own nested
    routes and the organisation story applies to its sources."""

    # SOC2:AUTH-02 an account-level record follows its own account's visibility
    return Q(customer=customer) | Q(account__in=visible_accounts(user).filter(customers=customer))


def get_visible_customer(request, customer_id):
    """The one place Customer-scoped nested views resolve their parent.

    A customer outside your visibility is a 404, not a 403 and not an
    empty list — the same "404, not an empty list" convention these
    views already used for another organisation's ids, now applied to
    another owner's. It also avoids the detail view confirming that a
    record exists to someone who can't see it."""

    return get_object_or_404(visible_customers(request.user), pk=customer_id)


def get_visible_account(request, customer_id, account_id):
    """Account-scoped equivalent. `customers=customer_id` pins the
    Account under the URL's own Customer, so the nested route keeps
    meaning what it says — visibility is necessary but not sufficient."""

    return get_object_or_404(visible_accounts(request.user), pk=account_id, customers=customer_id)
