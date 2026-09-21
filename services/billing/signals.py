"""Bookkeeping that keeps the seat table true without enforcing anything.

- An organisation gets its billing account (on the trial) the moment it
  exists, whichever path created it: signup, a self-serve workspace, the
  portal, a seed, a test.
- An active membership holds an open seat; a membership that stops being
  active releases it. This receiver never refuses: raw `create_user` paths
  (seeds, tests, the Django admin) must keep working. The paths that admit
  people call `seats.allocate` themselves, *before* writing the membership,
  and that call is the one that says no.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from services.accounts.models import Organisation
from services.identity.models import OrganizationMembership


@receiver(post_save, sender=Organisation, dispatch_uid="billing.account_for_new_organisation")
def account_for_new_organisation(sender, instance, created, **kwargs):
    if created:
        from .accounts import ensure

        ensure(instance)


@receiver(post_save, sender=OrganizationMembership, dispatch_uid="billing.seat_follows_membership")
def seat_follows_membership(sender, instance, **kwargs):
    from . import seats

    seats.ensure_account_exists(instance.organisation)
    if instance.status == OrganizationMembership.Status.ACTIVE:
        seats.allocate(instance, enforce=False)
    else:
        seats.release(instance)
