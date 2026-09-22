from django.conf import settings
from django.db import models

from services.accounts.models import Organisation
from services.customers.models import Account, Customer


class FeatureRequest(models.Model):
    """One thing customers keep asking for, named once.

    The evidence is the classified emails, tickets and calls the
    classifier tagged as feature requests (services.customers.taxonomy);
    gather groups them by meaning and asks the model for a title, and from
    then on new asks attach to the nearest existing request by embedding
    distance, without another model call. `embedding` is the request's
    centroid, kept so that matching needs no re-reading of its evidence."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        PLANNED = "planned", "Planned"
        SHIPPED = "shipped", "Shipped"
        DECLINED = "declined", "Declined"

    class Meta:
        ordering = ["-updated_at"]

    organisation = models.ForeignKey(
        Organisation, related_name="feature_requests", on_delete=models.CASCADE
    )
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="owned_feature_requests",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    embedding = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.title} ({self.organisation_id})"


class RequestEvidence(models.Model):
    """One classified interaction, filed under a request or dismissed.

    `kind` + `record_id` name the email, ticket or call (a snapshot of its
    text is kept so the request page reads without touching the record).
    A dismissed row keeps its place so the next gather does not file the
    same interaction again. Exactly one of customer/account, like every
    other record that hangs off a company."""

    class Kind(models.TextChoices):
        EMAIL = "email", "Email"
        TICKET = "ticket", "Ticket"
        CALL = "call", "Call"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "record_id"], name="one_filing_per_interaction"
            ),
            models.CheckConstraint(
                check=(
                    models.Q(customer__isnull=False, account__isnull=True)
                    | models.Q(customer__isnull=True, account__isnull=False)
                ),
                name="request_evidence_belongs_to_exactly_one_parent",
            ),
        ]
        ordering = ["-occurred_at", "-id"]

    request = models.ForeignKey(
        FeatureRequest, related_name="evidence", on_delete=models.CASCADE, null=True, blank=True
    )
    organisation = models.ForeignKey(
        Organisation, related_name="request_evidence", on_delete=models.CASCADE
    )
    kind = models.CharField(max_length=8, choices=Kind.choices)
    record_id = models.PositiveIntegerField()
    customer = models.ForeignKey(
        Customer, related_name="request_evidence", on_delete=models.CASCADE, null=True, blank=True
    )
    account = models.ForeignKey(
        Account, related_name="request_evidence", on_delete=models.CASCADE, null=True, blank=True
    )
    snippet = models.CharField(max_length=500)
    occurred_at = models.DateTimeField()
    dismissed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def company(self):
        return self.customer or self.account

    def __str__(self):
        return f"{self.kind} {self.record_id} → {self.request_id or 'dismissed'}"
