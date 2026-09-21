"""Money, seats and credits: what an organisation has paid for and used.

Two meters, both server-side, as decided in review:

- **Seats** are people. A plan includes a number of seats; an active member
  holds one. Allocation happens where membership is granted (approval,
  invitation, founding) under a row lock on the organisation's
  `BillingAccount`, so two concurrent approvals for the last seat serialise
  and exactly one succeeds. That is the brief's hard invariant, and the
  concurrent-approval test in `tests/test_seats.py` is the gate.
- **Credits** are AI. Every model call the codebase makes (`get_completion`)
  charges one credit before it is sent, and refunds it if the call fails.

Three rules keep the numbers honest:

1. **The ledger is the truth.** `CreditLedger` is append-only, one row per
   movement, each carrying `balance_after`. Nothing ever does
   `account.credits -= 1`.
2. **One serialisation point.** Every write that depends on a balance or a
   seat count first takes `SELECT ... FOR UPDATE` on the `BillingAccount`.
3. **A database check constraint** on `balance_after >= 0`, so even a logic
   bug cannot persist a negative balance.

Credits are granted only by a plan change made by platform staff or by a
verified payment webhook (the next phase). Never because a client said it paid.
"""

from django.conf import settings
from django.db import models

from services.accounts.models import Organisation


class Plan(models.Model):
    """What can be bought. Data, not code: prices and allowances change
    without a deploy, and the trial is just the plan a new workspace starts
    on. `stripe_price_id` binds it to the payment provider in the next phase."""

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=80)
    seats_included = models.PositiveIntegerField()
    monthly_credits = models.PositiveIntegerField(help_text="Granted at each period start.")
    price_cents = models.PositiveIntegerField(default=0)
    currency = models.CharField(max_length=3, default="USD")
    is_trial = models.BooleanField(default=False)
    is_public = models.BooleanField(
        default=True, help_text="Offered to customers, not only assignable by staff."
    )
    stripe_price_id = models.CharField(max_length=120, blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "price_cents"]

    def __str__(self):
        return f"{self.name} ({self.code})"


class BillingAccount(models.Model):
    """One per organisation: the plan it is on and the row every seat and
    credit decision locks. Created the moment an organisation exists
    (`signals.py`), on the trial plan."""

    class Status(models.TextChoices):
        TRIALING = "trialing", "Trialing"
        ACTIVE = "active", "Active"
        PAST_DUE = "past_due", "Past due"
        CANCELED = "canceled", "Canceled"

    organisation = models.OneToOneField(
        Organisation, related_name="billing_account", on_delete=models.CASCADE
    )
    plan = models.ForeignKey(Plan, related_name="accounts", on_delete=models.PROTECT)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.TRIALING)
    #: The seat allowance in force. Usually the plan's, but staff may grant more.
    seats_limit = models.PositiveIntegerField()
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    stripe_customer_id = models.CharField(max_length=120, blank=True)
    stripe_subscription_id = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.organisation.name}: {self.plan.code} ({self.status})"


class CreditLedger(models.Model):
    """Every credit movement, forever. Append-only like `core.AuditEvent`.

    `reference` is the idempotency key: a webhook delivered twice, or a call
    retried, writes one row. `balance_after` is what the check constraint
    guards; it is computed under the account lock from the previous row.
    """

    class Kind(models.TextChoices):
        GRANT = "grant", "Grant"  # a plan period, a payment, a trial
        CONSUME = "consume", "Consume"  # a model call
        REFUND = "refund", "Refund"  # a failed model call gives its credit back
        ADJUST = "adjust", "Adjustment"  # platform staff, with a reason
        EXPIRE = "expire", "Expire"  # trial or period end

    account = models.ForeignKey(BillingAccount, related_name="ledger", on_delete=models.CASCADE)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    amount = models.IntegerField(help_text="Signed: positive adds, negative removes.")
    balance_after = models.IntegerField()
    reason = models.CharField(max_length=255, blank=True)
    reference = models.CharField(max_length=160, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            # Even a logic bug cannot persist a negative balance.
            models.CheckConstraint(
                condition=models.Q(balance_after__gte=0), name="credit_balance_never_negative"
            ),
            # Idempotency: the same reference writes one row per account.
            models.UniqueConstraint(
                fields=["account", "reference"],
                condition=~models.Q(reference=""),
                name="one_ledger_row_per_reference",
            ),
        ]
        indexes = [models.Index(fields=["account", "created_at"])]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise TypeError("CreditLedger rows are append-only and cannot be modified.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise TypeError("CreditLedger rows are append-only and cannot be deleted.")

    def __str__(self):
        return f"{self.kind} {self.amount:+d} -> {self.balance_after}"


class SeatAssignment(models.Model):
    """One row per membership that holds (or held) a seat. An open row
    (`released_at` null) is a seat in use; the count of open rows against
    `seats_limit` is the seat check. History rows accumulate: who held a seat
    when is an audit question."""

    account = models.ForeignKey(BillingAccount, related_name="seats", on_delete=models.CASCADE)
    membership = models.ForeignKey(
        "identity.OrganizationMembership", related_name="seat_assignments", on_delete=models.CASCADE
    )
    allocated_at = models.DateTimeField(auto_now_add=True)
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["membership"],
                condition=models.Q(released_at__isnull=True),
                name="one_open_seat_per_membership",
            )
        ]
        indexes = [models.Index(fields=["account", "released_at"])]

    def __str__(self):
        state = "open" if self.released_at is None else "released"
        return f"seat for membership {self.membership_id} ({state})"


class PaymentWebhookEvent(models.Model):
    """Every delivery from a payment provider, once. `(provider, event_id)` is
    unique, so a duplicate delivery is recorded as such and never grants
    credits twice. Filled in by the provider phase; the table exists now so
    the idempotency rule is part of the model from the start."""

    provider = models.CharField(max_length=32)
    event_id = models.CharField(max_length=160)
    event_type = models.CharField(max_length=80, blank=True)
    payload_sha256 = models.CharField(max_length=64, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=32, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "event_id"], name="one_webhook_event_per_provider"
            )
        ]

    def __str__(self):
        return f"{self.provider}:{self.event_id} ({self.outcome or 'pending'})"
