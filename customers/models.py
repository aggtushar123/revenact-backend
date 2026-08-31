from django.conf import settings
from django.db import models


class Customer(models.Model):
    """One of a tenant Organisation's own customers — the company a CSM is
    tracking for health/ARR/renewal. Not to be confused with
    accounts.Organisation, which is the tenant itself (the company that
    pays for Revenact). See docs/API_CONTRACTS.md for why these are
    modeled as separate, deliberately differently-named things.

    `health_category` is derived from `health_score`, not stored — avoids
    the two ever disagreeing. See HEALTH_THRESHOLDS below."""

    class LifecycleStage(models.TextChoices):
        ONBOARDING = "onboarding", "Onboarding"
        KICKOFF = "kickoff", "Kickoff"
        ADOPTION = "adoption", "Adoption"
        LIVE = "live", "Live"
        RENEWAL = "renewal", "Renewal"
        CHURN = "churn", "Churn"
        EXPANSION = "expansion", "Expansion"
        OTHER = "other", "Other"

    class HealthCategory(models.TextChoices):
        GOOD = "good", "Good"
        AVERAGE = "average", "Average"
        POOR = "poor", "Poor"

    organisation = models.ForeignKey(
        "accounts.Organisation", related_name="customers", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=255)
    health_score = models.PositiveSmallIntegerField(
        default=50, help_text="0-100. health_category is derived from this, not stored."
    )
    arr = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Annual recurring revenue, in dollars.",
    )
    renewal_date = models.DateField(null=True, blank=True)
    lifecycle_stage = models.CharField(
        max_length=20, choices=LifecycleStage.choices, default=LifecycleStage.ONBOARDING
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="owned_customers",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="The CSM (or admin) assigned to this customer. Must be in the same organisation.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # score >= 70 -> good, 40-69 -> average, < 40 -> poor.
    HEALTH_THRESHOLDS = ((70, HealthCategory.GOOD), (40, HealthCategory.AVERAGE))

    @property
    def health_category(self):
        for threshold, category in self.HEALTH_THRESHOLDS:
            if self.health_score >= threshold:
                return category
        return self.HealthCategory.POOR

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
