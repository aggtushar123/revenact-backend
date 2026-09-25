from django.conf import settings
from django.db import models

from services.accounts.models import Organisation
from services.customers.models import Account, Customer


class Anomaly(models.Model):
    """Several companies hitting the same thing at once.

    Not a ticket count going up: a cluster of interactions that mean the
    same thing, across enough different companies, and clearly more of
    them than the same subject drew in the fortnight before. One fault
    reported by eight accounts reads as eight tickets everywhere else in
    this product; here it reads as one thing to fix.

    `embedding` is the cluster's centroid, so a later report joins it
    without another model call — the same trick as a feature request's."""

    class Status(models.TextChoices):
        LIVE = "live", "Live"
        ACKNOWLEDGED = "acknowledged", "Acknowledged"
        RESOLVED = "resolved", "Resolved"

    organisation = models.ForeignKey(
        Organisation, related_name="anomalies", on_delete=models.CASCADE
    )
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.LIVE)
    embedding = models.JSONField(default=list, blank=True)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="+",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-last_seen_at", "-id"]
        indexes = [models.Index(fields=["organisation", "status", "-last_seen_at"])]

    def __str__(self):
        # Not the title: this is what an audit row records as its target,
        # and platform staff read that. The title is model-written from
        # customer reports.
        return f"Anomaly {self.pk} ({self.organisation_id})"


class AnomalyEvidence(models.Model):
    """One interaction in a cluster.

    Carries copies of `mailbox_owner` and `department` for the same reason
    a feature request's evidence does: the snippet is the record's own
    text, and must stay behind the rule that record lives by even after the
    record is gone (services.customers.personal.readable_evidence_q)."""

    class Kind(models.TextChoices):
        EMAIL = "email", "Email"
        TICKET = "ticket", "Ticket"
        CALL = "call", "Call"

    anomaly = models.ForeignKey(Anomaly, related_name="evidence", on_delete=models.CASCADE)
    organisation = models.ForeignKey(
        Organisation, related_name="anomaly_evidence", on_delete=models.CASCADE
    )
    kind = models.CharField(max_length=8, choices=Kind.choices)
    record_id = models.PositiveIntegerField()
    customer = models.ForeignKey(
        Customer, related_name="anomaly_evidence", on_delete=models.CASCADE, null=True, blank=True
    )
    account = models.ForeignKey(
        Account, related_name="anomaly_evidence", on_delete=models.CASCADE, null=True, blank=True
    )
    snippet = models.CharField(max_length=500)
    mailbox_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="+",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    department = models.CharField(max_length=16, blank=True, default="")
    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "record_id"], name="one_anomaly_filing_per_interaction"
            ),
            models.CheckConstraint(
                check=(
                    models.Q(customer__isnull=False, account__isnull=True)
                    | models.Q(customer__isnull=True, account__isnull=False)
                ),
                name="anomaly_evidence_belongs_to_exactly_one_parent",
            ),
        ]

    @property
    def company(self):
        return self.customer or self.account

    def __str__(self):
        return f"{self.kind} {self.record_id} → {self.anomaly_id}"
