"""From a verified identity to a seat at the table.

Phase 2 ended a corporate sign-in with `NO_ACCOUNT`: the address was real, the
provider vouched for it, and there was nothing for the person to enter. This
closes that loop.

    verified address
        -> domain, if it is not a personal one
        -> a tenant, through a verified domain or the one workspace claiming it
        -> a User, created here, belonging to nothing yet
        -> an AccessRequest, pending
        -> an administrator decides
        -> a membership, and only then any access at all

    or, when nobody has claimed the domain at all:

    verified address
        -> the workspace form
        -> an Organisation, its founder as admin, the domain as an unverified claim

Three rules hold throughout, and each exists because its opposite is a real
failure:

**Authenticating is not joining.** Proving control of `john@accenture.com`
proves where John works, not that Accenture wants him in their tenant, nor with
what role. Until an admin says so he has a user, no membership, and therefore no
capabilities and no visible records.

**A pending request takes no seat.** Twenty people may wait while one seat
remains. The seat is taken at approval, where the check belongs.

**Approval writes both representations.** Phase 3 left `User.organisation` and
`User.role` as the record of what someone may do while the membership carries
standing, and `check_membership_consistency` enforces that they agree. Approving
therefore sets both, in one transaction, or neither.
"""

from django.db import transaction
from django.utils import timezone

from core import audit
from services.accounts.models import User

from . import domains
from .models import AccessRequest, Department, OrganizationMembership


class OnboardingError(Exception):
    """Carries a code the frontend can act on, like `login.LoginError`."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def request_access(identity_info, *, organisation=None, request=None):
    """Create (or find) the pending request for a verified corporate address.

    Returns `(user, access_request)`. Raises when the address cannot lead
    anywhere, so the caller can tell the person *why* rather than leaving them
    on a spinner.

    `organisation` is the tenant the caller has already routed the address to
    (`login._route_newcomer` does this, and may pass one holding only an
    unverified claim, because a request there still needs the founder's
    explicit approval). Left out, only a verified domain will do.
    """
    email = identity_info.email

    if domains.is_personal(email):
        raise OnboardingError(
            "PERSONAL_EMAIL_NOT_SUPPORTED",
            "Sign in with your work address, or ask a colleague to invite you.",
        )

    if organisation is None:
        organisation = domains.organisation_for_email(email)
    if organisation is None:
        raise OnboardingError(
            "DOMAIN_NOT_VERIFIED",
            "No organisation here has verified that email domain yet.",
        )

    from services.accounts.models import Organisation

    if organisation.status != Organisation.Status.ACTIVE:
        raise OnboardingError("ORGANIZATION_SUSPENDED", "That organisation is not active.")

    with transaction.atomic():
        user = _user_for(identity_info)

        # Already a member: nothing to request. The caller treats this as a
        # normal sign-in.
        live = OrganizationMembership.objects.filter(
            organisation=organisation, user=user, status__in=LIVE_STATUSES
        ).first()
        if live is not None:
            return user, None

        access_request, created = AccessRequest.objects.get_or_create(
            organisation=organisation,
            user=user,
            status=AccessRequest.Status.PENDING,
            defaults={"email": email},
        )

    if created:
        audit.record(  # SOC2:LOG-01
            "access_request.created",
            request=request,
            actor=user,
            organisation=organisation,
            target=user,
            metadata={"email": email},
        )
    return user, access_request


LIVE_STATUSES = OrganizationMembership.LIVE_STATUSES


def _user_for(identity_info) -> User:
    """The person behind a verified address, created if this is their first time.

    Created with **no organisation and no role**, which is what keeps this safe:
    until a membership exists they hold nothing. The column is filled in at
    approval, together with the membership, so the two never disagree.
    """
    existing = User.objects.filter(email__iexact=identity_info.email).first()
    if existing is not None:
        return existing

    name = identity_info.name or identity_info.email.split("@")[0]
    # `create_user` resolves a default role from the organisation; there is no
    # organisation yet, so the user is built directly and left holding nothing.
    user = User(email=identity_info.email.lower(), name=name, organisation=None, role=None)
    user.set_unusable_password()  # they sign in through the provider, not a password
    user.save()
    return user


def create_workspace(identity_info, *, organisation_name: str, name: str = "", request=None):
    """The first person from an unclaimed corporate domain starts a workspace.

    What they get is deliberately small: an organisation with themselves as its
    only member and administrator, and their domain attached as a *claim* that
    proves nothing yet. Nobody is routed into it by domain until they publish
    the DNS record, and the company can take the domain from them at any time
    by verifying it under its own workspace (`domains.verify`). So an employee
    who starts "Acme" ahead of Acme has an empty room with their name on the
    door, and nothing Acme has to fight for.

    One transaction: organisation, founder, membership (raised by the same
    signal every other creation path uses), domain claim, linked identity.
    Either the workspace exists whole or it does not exist.
    """
    from services.accounts.models import Organisation

    from .models import Identity, OrganizationDomain

    email = identity_info.email
    organisation_name = (organisation_name or "").strip()
    if not organisation_name:
        raise OnboardingError("INVALID_ORGANISATION_NAME", "Give the workspace a name.")

    # Re-checked here, not only at sign-in: two colleagues may both have been
    # handed a setup code before either submitted. The second finds a claim
    # and is sent back to sign in, where they will be routed to the first.
    route = domains.route_for_email(email)
    if route.kind == "personal":
        raise OnboardingError(
            "PERSONAL_EMAIL_NOT_SUPPORTED", "A workspace needs a company address."
        )
    if route.kind != "unclaimed":
        raise OnboardingError(
            "WORKSPACE_CLAIMED", "Somebody has already started a workspace for that domain."
        )
    if User.objects.filter(email__iexact=email).exists():
        raise OnboardingError("WORKSPACE_CLAIMED", "That address already belongs to an account.")

    with transaction.atomic():
        organisation = Organisation.objects.create(name=organisation_name)
        user = User.objects.create_user(
            email=email.lower(),
            password=None,  # unusable: they sign in through the provider
            name=(name or identity_info.name or email.split("@")[0]).strip(),
            organisation=organisation,
            role=User.Role.ADMIN,
        )
        OrganizationDomain.objects.create(
            organisation=organisation,
            domain=domains.domain_of(email),
            is_primary=True,
            verification_token=domains.new_token(),
        )
        Identity.objects.create(
            user=user,
            provider=identity_info.provider,
            provider_user_id=identity_info.subject,
            email=email,
            email_verified=True,
            last_used_at=timezone.now(),
        )

    audit.record(  # SOC2:LOG-01
        "auth.signup",
        request=request,
        actor=user,
        organisation=organisation,
        target=organisation,
        metadata={
            "organisation": organisation.name,
            "via": "oauth",
            "provider": identity_info.provider,
        },
    )
    audit.record(  # SOC2:LOG-01
        "domain.added",
        request=request,
        actor=user,
        organisation=organisation,
        target=organisation,
        metadata={"domain": domains.domain_of(email), "claimed_at_signup": True},
    )
    return user


def approve(access_request: AccessRequest, *, reviewer: User, role, department=None, request=None):
    """Turn a pending request into an active membership.

    One transaction: the membership, both columns, the request's own status.
    Either the person is in with a role, or nothing happened.

    Seat allocation joins this transaction in the billing phase, between the
    authorization check and the membership write, which is why the ordering
    below already leaves room for it.
    """
    if access_request.status != AccessRequest.Status.PENDING:
        raise OnboardingError("ACCESS_REQUEST_DECIDED", "That request has already been decided.")

    organisation = access_request.organisation

    if role is None or role.organisation_id != organisation.id:
        raise OnboardingError("INVALID_ROLE", "That role does not belong to this organisation.")
    if department is not None and department.organisation_id != organisation.id:
        raise OnboardingError(
            "INVALID_DEPARTMENT", "That department does not belong to this organisation."
        )

    # An admin must not hand out more than they hold, or `manage_users` would
    # quietly be a route to full administration.
    granting = set(role.permissions or [])
    held = set(_capabilities_of(reviewer))
    if not reviewer.is_superuser and not granting.issubset(held):
        raise OnboardingError(
            "INSUFFICIENT_PERMISSION",
            "You cannot grant a capability you do not hold yourself.",
        )

    user = access_request.user
    now = timezone.now()

    with transaction.atomic():
        # (billing phase: allocate a seat here, and fail the whole thing if
        # none is available)
        membership, _ = OrganizationMembership.objects.update_or_create(
            organisation=organisation,
            user=user,
            status=OrganizationMembership.Status.ACTIVE,
            defaults={
                "role": role,
                "department": department,
                "approved_at": now,
                "approved_by": reviewer,
            },
        )
        # Both representations, together: phase 3's consistency guard requires
        # the column to agree with the membership.
        User.objects.filter(pk=user.pk).update(organisation=organisation, role=role)

        AccessRequest.objects.filter(pk=access_request.pk).update(
            status=AccessRequest.Status.APPROVED, reviewed_at=now, reviewed_by=reviewer
        )

    audit.record(  # SOC2:LOG-01
        "access_request.approved",
        request=request,
        actor=reviewer,
        organisation=organisation,
        target=user,
        metadata={"role": role.slug, "department": department.name if department else None},
    )
    return membership


def reject(access_request: AccessRequest, *, reviewer: User, reason: str = "", request=None):
    """Decline a request. The person keeps their account and no membership."""
    if access_request.status != AccessRequest.Status.PENDING:
        raise OnboardingError("ACCESS_REQUEST_DECIDED", "That request has already been decided.")

    AccessRequest.objects.filter(pk=access_request.pk).update(
        status=AccessRequest.Status.REJECTED,
        reviewed_at=timezone.now(),
        reviewed_by=reviewer,
        rejection_reason=(reason or "")[:500],
    )
    audit.record(  # SOC2:LOG-01
        "access_request.rejected",
        request=request,
        actor=reviewer,
        organisation=access_request.organisation,
        target=access_request.user,
        metadata={"reason": (reason or "")[:200]},
    )


def _capabilities_of(user) -> list:
    from .context import capabilities_for

    return capabilities_for(user)


def default_department(organisation, name: str) -> Department | None:
    """Find a department by name within one tenant, or None."""
    if not name:
        return None
    return Department.objects.filter(organisation=organisation, name__iexact=name).first()
