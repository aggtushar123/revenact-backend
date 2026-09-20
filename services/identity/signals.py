"""Keep memberships in step with the column while both exist.

Phase 1 backfilled everyone who existed then. Users created afterwards — by a
serializer, a seed command, the Django admin — would have a row in one
representation and not the other, and the equivalence guard would start failing
for reasons nobody could act on.

A signal rather than `UserManager.create_user`, because users are created down
several paths and only a signal catches all of them.

This writes nothing that the column does not already say, so it changes no
behaviour: it keeps the second representation true.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from services.accounts.models import User


@receiver(post_save, sender=User, dispatch_uid="identity.membership_for_new_user")
def create_membership_for_new_user(sender, instance, created, **kwargs):
    if not created or instance.organisation_id is None:
        return

    from .models import LIVE_MEMBERSHIP_STATUSES, OrganizationMembership

    exists = OrganizationMembership.objects.filter(
        organisation_id=instance.organisation_id,
        user_id=instance.pk,
        status__in=LIVE_MEMBERSHIP_STATUSES,
    ).exists()
    if exists:
        return

    OrganizationMembership.objects.create(
        organisation_id=instance.organisation_id,
        user_id=instance.pk,
        status=OrganizationMembership.Status.ACTIVE,
        role_id=instance.role_id,
        approved_at=instance.date_joined,
    )
