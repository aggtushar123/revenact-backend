"""The billing account behind an organisation, and the plan it is on."""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core import audit

from . import ledger
from .models import BillingAccount, Plan

TRIAL_CODE = "trial"


def trial_plan() -> Plan:
    """The plan every organisation starts on. Created on first use with the
    settings' allowances, so a fresh database needs no fixture."""
    plan, _ = Plan.objects.get_or_create(
        code=TRIAL_CODE,
        defaults={
            "name": "Trial",
            "seats_included": settings.BILLING_TRIAL_SEATS,
            "monthly_credits": settings.BILLING_TRIAL_CREDITS,
            "price_cents": 0,
            "is_trial": True,
            "is_public": False,
            "sort_order": 0,
        },
    )
    return plan


def ensure(organisation) -> BillingAccount:
    """The organisation's account, created on the trial if it has none.

    Idempotent and safe to call from anywhere: the signal on Organisation
    creation, the seat check, the views. The trial's credits are granted once,
    with a reference, so a second call cannot grant them again.
    """
    try:
        return organisation.billing_account
    except BillingAccount.DoesNotExist:
        pass
    with transaction.atomic():
        plan = trial_plan()
        account, created = BillingAccount.objects.get_or_create(
            organisation=organisation,
            defaults={
                "plan": plan,
                "status": BillingAccount.Status.TRIALING,
                "seats_limit": plan.seats_included,
                "trial_ends_at": timezone.now() + timedelta(days=settings.BILLING_TRIAL_DAYS),
            },
        )
        if created and plan.monthly_credits:
            ledger.grant(
                account,
                plan.monthly_credits,
                reason=(
                    f"Trial: {plan.monthly_credits} credits for {settings.BILLING_TRIAL_DAYS} days"
                ),
                reference=f"trial:{organisation.pk}",
            )
    return account


def change_plan(
    account: BillingAccount,
    plan: Plan,
    *,
    actor,
    reason: str = "",
    request=None,
    reference: str = "",
):
    """Put the account on a plan: its seat allowance and this period's
    credits. Used by platform staff now and by the payment webhook next."""
    previous = account.plan
    with transaction.atomic():
        locked = BillingAccount.objects.select_for_update().get(pk=account.pk)
        locked.plan = plan
        locked.seats_limit = plan.seats_included
        locked.status = (
            BillingAccount.Status.TRIALING if plan.is_trial else BillingAccount.Status.ACTIVE
        )
        if not plan.is_trial:
            locked.trial_ends_at = None
            locked.current_period_end = timezone.now() + timedelta(days=30)
        locked.save()
        if plan.monthly_credits:
            try:
                ledger.grant(
                    locked,
                    plan.monthly_credits,
                    reason=f"{plan.name}: {plan.monthly_credits} credits for this period",
                    reference=reference
                    or f"plan:{plan.code}:{timezone.now():%Y%m%d%H%M%S}:{locked.pk}",
                    actor=actor,
                )
            except ledger.BillingError as exc:
                if exc.code != "DUPLICATE_REFERENCE":
                    raise
    audit.record(  # SOC2:LOG-01
        "billing.plan.changed",
        request=request,
        actor=actor,
        organisation=account.organisation,
        target=account,
        metadata={
            "from": previous.code,
            "to": plan.code,
            "seats_limit": plan.seats_included,
            "reason": reason[:200],
        },
    )
    account.refresh_from_db()
    return account


def set_seats(account: BillingAccount, seats_limit: int, *, actor, reason: str, request=None):
    """Staff granting (or trimming) seats beyond the plan. Cannot go below
    the seats in use: people are not evicted by a number."""
    from . import seats

    if seats_limit < 0:
        raise ledger.BillingError("INVALID_AMOUNT", "Seats cannot be negative.")
    if not (reason or "").strip():
        raise ledger.BillingError("REASON_REQUIRED", "Say why; it goes on the record.")
    with transaction.atomic():
        locked = BillingAccount.objects.select_for_update().get(pk=account.pk)
        in_use = seats.used(locked)
        if seats_limit < in_use:
            raise ledger.BillingError(
                "SEATS_IN_USE", f"{in_use} seats are in use; free some before lowering the limit."
            )
        previous = locked.seats_limit
        locked.seats_limit = seats_limit
        locked.save(update_fields=["seats_limit", "updated_at"])
    audit.record(  # SOC2:LOG-01
        "billing.seats.changed",
        request=request,
        actor=actor,
        organisation=account.organisation,
        target=account,
        metadata={"from": previous, "to": seats_limit, "reason": reason[:200]},
    )
    account.refresh_from_db()
    return account


def summary(account: BillingAccount) -> dict:
    from . import seats

    return {
        "plan": {
            "code": account.plan.code,
            "name": account.plan.name,
            "seats_included": account.plan.seats_included,
            "monthly_credits": account.plan.monthly_credits,
            "price_cents": account.plan.price_cents,
            "currency": account.plan.currency,
            "is_trial": account.plan.is_trial,
        },
        "status": account.status,
        "seats": {"used": seats.used(account), "limit": account.seats_limit},
        "credits": {"balance": ledger.balance(account)},
        "trial_ends_at": account.trial_ends_at,
        "current_period_end": account.current_period_end,
        "enforced": settings.BILLING_ENFORCED,
    }
