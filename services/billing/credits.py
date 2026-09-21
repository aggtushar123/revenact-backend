"""AI credits at the one chokepoint every model call goes through."""

from django.conf import settings

from . import ledger
from .accounts import ensure


class CreditsExhausted(Exception):
    """Raised before a model call when the organisation has no credits.

    Subclassed from nothing in copilot to avoid a circular import; the
    client re-raises it as its own BudgetExceeded so every existing caller's
    handling (a 429, a skipped agent run) applies unchanged.
    """


def charge(organisation, *, purpose: str, reference: str, actor=None) -> bool:
    """Take this call's credits before it is made. Returns False when billing
    is not enforced (recorded anyway when affordable, refused never)."""
    cost = settings.BILLING_CREDITS_PER_MODEL_CALL
    if cost <= 0 or organisation is None:
        return False
    account = ensure(organisation)
    try:
        ledger.consume(
            account, cost, reason=f"model call: {purpose}", reference=reference, actor=actor
        )
        return True
    except ledger.BillingError as exc:
        if exc.code == "DUPLICATE_REFERENCE":
            return True
        if not settings.BILLING_ENFORCED:
            return False
        raise CreditsExhausted(
            "This organisation has no AI credits left. "
            "Upgrade the plan, or wait for the next period."
        ) from exc


def refund(organisation, *, purpose: str, reference: str, actor=None) -> None:
    """A failed call costs nothing. Keyed on the charge's reference so a
    double refund is impossible."""
    cost = settings.BILLING_CREDITS_PER_MODEL_CALL
    if cost <= 0 or organisation is None:
        return
    account = ensure(organisation)
    try:
        ledger.refund(
            account,
            cost,
            reason=f"refund: failed model call: {purpose}",
            reference=f"{reference}:refund",
            actor=actor,
        )
    except ledger.BillingError:
        pass
