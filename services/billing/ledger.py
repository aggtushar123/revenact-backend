"""Credit movements. Every function here locks the account row first, reads
the last balance, and appends one row. Nothing else writes `CreditLedger`."""

from django.db import IntegrityError, transaction

from core import audit

from .models import BillingAccount, CreditLedger


class BillingError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def balance(account: BillingAccount) -> int:
    """The current balance: the last row's `balance_after`, or zero."""
    row = CreditLedger.objects.filter(account=account).order_by("-created_at", "-id").first()
    return row.balance_after if row else 0


def _locked(account_or_id) -> BillingAccount:
    pk = account_or_id.pk if isinstance(account_or_id, BillingAccount) else account_or_id
    return BillingAccount.objects.select_for_update().get(pk=pk)


def _append(account, *, kind, amount, reason, reference, actor):
    """Caller holds the transaction and the lock."""
    current = balance(account)
    after = current + amount
    if after < 0:
        raise BillingError("INSUFFICIENT_CREDITS", "This organisation has no AI credits left.")
    try:
        # A savepoint, so a duplicate reference leaves the caller's
        # transaction usable rather than poisoned.
        with transaction.atomic():
            return CreditLedger.objects.create(
                account=account,
                kind=kind,
                amount=amount,
                balance_after=after,
                reason=(reason or "")[:255],
                reference=(reference or "")[:160],
                actor=actor,
            )
    except IntegrityError as exc:
        # The same reference again: already applied, nothing to do.
        if reference and CreditLedger.objects.filter(account=account, reference=reference).exists():
            raise BillingError(
                "DUPLICATE_REFERENCE", "That movement was already recorded."
            ) from exc
        raise


def grant(account, amount: int, *, reason: str, reference: str = "", actor=None):
    """Add credits: a trial, a plan period, a verified payment."""
    if amount <= 0:
        raise BillingError("INVALID_AMOUNT", "A grant must be positive.")
    with transaction.atomic():
        locked = _locked(account)
        return _append(
            locked,
            kind=CreditLedger.Kind.GRANT,
            amount=amount,
            reason=reason,
            reference=reference,
            actor=actor,
        )


def consume(account, amount: int, *, reason: str, reference: str = "", actor=None):
    """Spend credits. Raises INSUFFICIENT_CREDITS rather than going negative."""
    if amount <= 0:
        raise BillingError("INVALID_AMOUNT", "A charge must be positive.")
    with transaction.atomic():
        locked = _locked(account)
        return _append(
            locked,
            kind=CreditLedger.Kind.CONSUME,
            amount=-amount,
            reason=reason,
            reference=reference,
            actor=actor,
        )


def refund(account, amount: int, *, reason: str, reference: str = "", actor=None):
    with transaction.atomic():
        locked = _locked(account)
        return _append(
            locked,
            kind=CreditLedger.Kind.REFUND,
            amount=amount,
            reason=reason,
            reference=reference,
            actor=actor,
        )


def adjust(account, amount: int, *, reason: str, actor, request=None):
    """Platform staff moving credits by hand, either way. A reason is
    mandatory because this is the one movement no system event explains."""
    if amount == 0:
        raise BillingError("INVALID_AMOUNT", "An adjustment cannot be zero.")
    if not (reason or "").strip():
        raise BillingError("REASON_REQUIRED", "Say why; it goes on the record.")
    with transaction.atomic():
        locked = _locked(account)
        row = _append(
            locked,
            kind=CreditLedger.Kind.ADJUST,
            amount=amount,
            reason=reason,
            reference="",
            actor=actor,
        )
    audit.record(  # SOC2:LOG-01
        "billing.credits.adjusted",
        request=request,
        actor=actor,
        organisation=account.organisation,
        target=account,
        metadata={"amount": amount, "balance_after": row.balance_after, "reason": reason[:200]},
    )
    return row


def expire_all(account, *, reason: str, reference: str = ""):
    """Bring the balance to zero, e.g. at trial end. No-op when already zero."""
    with transaction.atomic():
        locked = _locked(account)
        current = balance(locked)
        if current <= 0:
            return None
        return _append(
            locked,
            kind=CreditLedger.Kind.EXPIRE,
            amount=-current,
            reason=reason,
            reference=reference,
            actor=None,
        )
