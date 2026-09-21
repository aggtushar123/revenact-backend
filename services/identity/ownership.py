"""Who an organisation belongs to.

One membership per organisation carries `is_owner`. The owner is the billing
contact and the last line of authority: other administrators cannot deactivate
or demote them, and ownership moves only when the owner hands it over or
platform staff do. Everything that touches the flag goes through here, so the
"exactly one owner, always" rule has one keeper.
"""

from django.db import transaction
from django.utils import timezone

from core import audit
from services.accounts.models import User

from .models import OrganizationMembership


class OwnershipError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def owner_membership(organisation):
    """The owning membership, or None for an organisation with nobody yet."""
    if organisation is None:
        return None
    return (
        OrganizationMembership.objects.select_related("user")
        .filter(organisation=organisation, is_owner=True)
        .first()
    )


def is_owner(user, organisation=None) -> bool:
    organisation = organisation or getattr(user, "organisation", None)
    if user is None or organisation is None:
        return False
    return OrganizationMembership.objects.filter(
        organisation=organisation, user=user, is_owner=True
    ).exists()


def claim(user, organisation) -> None:
    """Make `user` the owner of an organisation that has none: the founder,
    at signup or workspace creation. Never steals from an existing owner."""
    if OrganizationMembership.objects.filter(organisation=organisation, is_owner=True).exists():
        return
    OrganizationMembership.objects.filter(
        organisation=organisation, user=user, status=OrganizationMembership.Status.ACTIVE
    ).update(is_owner=True)


def transfer(organisation, *, to_user_id, actor: User, request=None, by_platform=False):
    """Hand the organisation to another active member.

    `actor` must be the current owner, unless platform staff are doing it
    (`by_platform`), in which case the actor is recorded as such. The new
    owner is given the built-in admin role so ownership never lands on
    someone who cannot administer what they own.
    """
    if organisation is None:
        raise OwnershipError("ORGANIZATION_NOT_FOUND", "No organisation.")

    current = owner_membership(organisation)
    if not by_platform and (current is None or current.user_id != actor.pk):
        raise OwnershipError("NOT_OWNER", "Only the owner can hand the organisation over.")

    target = (
        OrganizationMembership.objects.select_related("user")
        .filter(
            organisation=organisation,
            user_id=to_user_id,
            status=OrganizationMembership.Status.ACTIVE,
        )
        .first()
    )
    if target is None or not target.user.is_active:
        raise OwnershipError("NOT_A_MEMBER", "The new owner must be an active member.")
    if current is not None and current.pk == target.pk:
        raise OwnershipError("ALREADY_OWNER", "They already own it.")

    admin_role = organisation.ensure_system_roles()[User.Role.ADMIN]
    with transaction.atomic():
        if current is not None:
            OrganizationMembership.objects.filter(pk=current.pk).update(is_owner=False)
        OrganizationMembership.objects.filter(pk=target.pk).update(is_owner=True, role=admin_role)
        User.objects.filter(pk=target.user_id).update(role=admin_role)

    audit.record(  # SOC2:LOG-01
        "organisation.owner_transferred",
        request=request,
        actor=actor,
        organisation=organisation,
        target=target.user,
        metadata={
            "from": current.user_id if current else None,
            "to": target.user_id,
            "by_platform": by_platform,
            "at": timezone.now().isoformat(),
        },
    )
    return target
