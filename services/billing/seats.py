"""Seats: one per active member, counted where they are granted.

`allocate` is the check the brief's invariant rests on. It runs inside the
caller's transaction, takes the account lock, counts open assignments and
either records one more or raises INSUFFICIENT_SEATS. Two concurrent
approvals for the last seat serialise on the lock; the second counts the
first's row and fails. Nothing here ever decrements a number.
"""

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .ledger import BillingError
from .models import BillingAccount, SeatAssignment


def account_for(organisation) -> BillingAccount:
    from .accounts import ensure

    return ensure(organisation)


def used(account: BillingAccount) -> int:
    return SeatAssignment.objects.filter(account=account, released_at__isnull=True).count()


def available(account: BillingAccount) -> int:
    return max(account.seats_limit - used(account), 0)


def allocate(membership, *, enforce: bool | None = None) -> SeatAssignment:
    """Give this membership a seat, or refuse. Idempotent for an open seat.

    `enforce` defaults to settings.BILLING_ENFORCED. The bookkeeping signal
    passes False so raw `create_user` paths (seeds, tests, the admin) keep
    working; the paths that admit people (approval, invitation, founding,
    adding a member, reactivation) leave it True.
    """
    if enforce is None:
        enforce = settings.BILLING_ENFORCED
    with transaction.atomic():
        account = BillingAccount.objects.select_for_update().get(
            organisation_id=membership.organisation_id
        )
        existing = SeatAssignment.objects.filter(
            membership=membership, released_at__isnull=True
        ).first()
        if existing is not None:
            return existing
        if enforce and used(account) >= account.seats_limit:
            raise BillingError(
                "INSUFFICIENT_SEATS",
                f"Every seat is taken ({account.seats_limit} of {account.seats_limit}). "
                "Free one, or add seats to the plan.",
            )
        return SeatAssignment.objects.create(account=account, membership=membership)


def reserve(organisation, *, enforce: bool | None = None) -> None:
    """The check for a membership about to be written. Takes the lock and
    refuses when the allowance is full; the caller's transaction then holds
    the lock until the membership (and its seat, via the signal) is in, so a
    concurrent reservation waits and then sees it."""
    if enforce is None:
        enforce = settings.BILLING_ENFORCED
    account = BillingAccount.objects.select_for_update().get(organisation_id=organisation.pk)
    if enforce and used(account) >= account.seats_limit:
        raise BillingError(
            "INSUFFICIENT_SEATS",
            f"Every seat is taken ({account.seats_limit} of {account.seats_limit}). "
            "Free one, or add seats to the plan.",
        )


def release(membership) -> int:
    """Close this membership's open seat, if any. Returns how many were closed."""
    return SeatAssignment.objects.filter(membership=membership, released_at__isnull=True).update(
        released_at=timezone.now()
    )


def ensure_account_exists(organisation):
    from .accounts import ensure

    return ensure(organisation)
