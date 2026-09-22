from django.conf import settings
from django.db import models

from services.accounts.models import Organisation
from services.customers.models import Account, Customer


class AIAttribute(models.Model):
    """A question an admin asks of every company, answered by the model.

    "Which tier do they use?", "How many seats are active?": the prompt is
    plain English, the answer is typed (`value_type`), and each answer is
    kept with its reasoning and the records it leaned on (AIAttributeValue).
    Sits beside custom objects rather than inside them: a custom field is a
    value someone types, an AI attribute is a value the evidence implies,
    and the two are read, refreshed and trusted differently."""

    class ValueType(models.TextChoices):
        TEXT = "text", "Text"
        NUMBER = "number", "Number"
        BOOLEAN = "boolean", "Yes / No"
        PICKLIST = "picklist", "Picklist"

    class Refresh(models.TextChoices):
        MANUAL = "manual", "When someone asks"
        NIGHTLY = "nightly", "Nightly, when there is new activity"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "api_name"], name="unique_ai_attribute_api_name_per_org"
            ),
            models.CheckConstraint(
                check=models.Q(applies_to_customer=True) | models.Q(applies_to_account=True),
                name="ai_attribute_applies_to_at_least_one_parent_type",
            ),
        ]
        ordering = ["name"]

    organisation = models.ForeignKey(
        Organisation, related_name="ai_attributes", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=255)
    api_name = models.SlugField(max_length=255)
    prompt = models.TextField()
    value_type = models.CharField(max_length=16, choices=ValueType.choices, default=ValueType.TEXT)
    picklist_options = models.JSONField(default=list, blank=True)
    applies_to_customer = models.BooleanField(default=True)
    applies_to_account = models.BooleanField(default=False)
    refresh = models.CharField(max_length=16, choices=Refresh.choices, default=Refresh.MANUAL)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="+", on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.organisation_id})"

    def applies_to(self, company) -> bool:
        if isinstance(company, Account):
            return self.applies_to_account
        return self.applies_to_customer


class AIAttributeValue(models.Model):
    """One answer, never edited: the history of an attribute on a company is
    every row, newest first, and the current value is the newest row.

    An AI row carries the reasoning and the sources it cited; a human row
    (`origin=human`) carries who set it. Both sit in the same timeline so a
    person can see the model's answer, their colleague's correction and the
    model's next answer in order. `status` says whether the model found an
    answer, found nothing to go on, or gave an answer the type rejected."""

    class Status(models.TextChoices):
        FILLED = "filled", "Filled"
        INSUFFICIENT = "insufficient", "Not enough evidence"
        FAILED = "failed", "Failed"

    class Origin(models.TextChoices):
        AI = "ai", "AI"
        HUMAN = "human", "Person"

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(customer__isnull=False, account__isnull=True)
                    | models.Q(customer__isnull=True, account__isnull=False)
                ),
                name="ai_attribute_value_belongs_to_exactly_one_parent",
            )
        ]
        ordering = ["-computed_at", "-id"]
        indexes = [
            models.Index(fields=["attribute", "customer", "-computed_at"]),
            models.Index(fields=["attribute", "account", "-computed_at"]),
        ]

    attribute = models.ForeignKey(AIAttribute, related_name="values", on_delete=models.CASCADE)
    customer = models.ForeignKey(
        Customer,
        related_name="ai_attribute_values",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    account = models.ForeignKey(
        Account, related_name="ai_attribute_values", on_delete=models.CASCADE, null=True, blank=True
    )
    value = models.JSONField(null=True, blank=True)
    reasoning = models.TextField(blank=True)
    sources = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.FILLED)
    origin = models.CharField(max_length=8, choices=Origin.choices, default=Origin.AI)
    set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="+", on_delete=models.SET_NULL, null=True, blank=True
    )
    computed_at = models.DateTimeField(auto_now_add=True)

    @property
    def company(self):
        return self.customer or self.account

    def __str__(self):
        return f"{self.attribute.name} = {self.value!r} on {self.company}"
