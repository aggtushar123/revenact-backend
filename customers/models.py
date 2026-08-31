from django.conf import settings
from django.db import models


class Customer(models.Model):
    """One of a tenant Organisation's own customers — the company a CSM is
    tracking. Not to be confused with accounts.Organisation, which is the
    tenant itself (the company that pays for Revenact). See
    docs/API_CONTRACTS.md for why these are modeled as separate,
    deliberately differently-named things.

    Field set matches the frontend's original `tableData.ts` mock schema
    (`react-ts-app/src/components/organizations/tableData.ts`) column for
    column — this model exists to eventually replace that mock data, one
    field at a time. The frontend is NOT wired to any of this yet (still
    on mock data) — this pass is backend schema only.

    "Revenact ID" (a column in the frontend table) is just this row's own
    `id` — there's no separate field for it.

    A few fields are *derived*, not stored, to avoid ever disagreeing with
    the values they're computed from: `health_category` (from
    `health_score`) and `seat_utilization_percentage` (from
    `total_active_seats` / `total_contracted_seats`). Financial figures
    (ARR, TCV, renewal forecast, etc.) are stored independently even where
    the mock data happens to make them look additive — in a real CS/RevOps
    system these are often independently negotiated numbers, not always a
    strict formula, so they aren't safe to derive.
    """

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

    class AIPulseScore(models.TextChoices):
        VERY_SATISFIED = "very_satisfied", "Very Satisfied"
        SATISFIED = "satisfied", "Satisfied"
        MODERATE = "moderate", "Moderate"
        HIGH_RISK = "high_risk", "High Risk"

    # --- Identity, ownership, provenance -------------------------------------

    organisation = models.ForeignKey(
        "accounts.Organisation", related_name="customers", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255, blank=True, help_text='"Name / Address" column.')
    domain = models.CharField(max_length=255, blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="owned_customers",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="The CSM (or admin) assigned to this customer. Must be in the same organisation.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="+", on_delete=models.SET_NULL, null=True, blank=True
    )
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="+", on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)  # "Created Date"
    updated_at = models.DateTimeField(auto_now=True)  # "Modified Date"

    # --- Lifecycle & health ---------------------------------------------------

    lifecycle_stage = models.CharField(
        max_length=20, choices=LifecycleStage.choices, default=LifecycleStage.ONBOARDING
    )
    health_score = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        default=5.0,
        help_text="0.0-10.0. health_category is derived from this, not stored.",
    )
    pulse = models.JSONField(
        default=list, blank=True, help_text="Recent pulse-history dots, e.g. [1,1,0,2,1]."
    )
    ai_pulse_score = models.CharField(max_length=20, choices=AIPulseScore.choices, blank=True)
    ai_pulse_reason = models.TextField(blank=True)
    nps_score = models.IntegerField(null=True, blank=True, help_text="-100 to 100.")
    csat_score = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True, help_text="0-100 (%)."
    )

    # --- Dates -----------------------------------------------------------------

    joined_date = models.DateField(null=True, blank=True)
    renewal_date = models.DateField(null=True, blank=True)
    contract_start_date = models.DateField(null=True, blank=True)
    contract_end_date = models.DateField(null=True, blank=True)

    # --- Financials --------------------------------------------------------------

    arr_billed_at_account = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    arr_billed_at_hq = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    implementation_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_contract_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_forecasted_renewal_revenue = models.DecimalField(
        max_digits=12, decimal_places=2, default=0
    )

    # --- Product & usage -----------------------------------------------------------

    primary_product = models.CharField(max_length=255, blank=True)
    additional_products_count = models.PositiveSmallIntegerField(null=True, blank=True)
    top_source_channel = models.CharField(max_length=255, blank=True)
    total_contracted_seats = models.PositiveIntegerField(null=True, blank=True)
    total_active_seats = models.PositiveIntegerField(null=True, blank=True)
    total_hires = models.PositiveIntegerField(null=True, blank=True)
    scope_web_app = models.CharField(max_length=255, blank=True)
    ces_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True, help_text="0-100 (%)."
    )

    # --- Churn ---------------------------------------------------------------------

    churn_date = models.DateField(null=True, blank=True)
    churn_reason = models.CharField(max_length=255, blank=True)
    churn_comment = models.TextField(blank=True)

    # score >= 7.0 -> good, 4.0-6.9 -> average, < 4.0 -> poor.
    HEALTH_THRESHOLDS = ((7, HealthCategory.GOOD), (4, HealthCategory.AVERAGE))

    @property
    def health_category(self):
        for threshold, category in self.HEALTH_THRESHOLDS:
            if self.health_score >= threshold:
                return category
        return self.HealthCategory.POOR

    @property
    def seat_utilization_percentage(self):
        if not self.total_contracted_seats or self.total_active_seats is None:
            return None
        return round(self.total_active_seats / self.total_contracted_seats * 100, 2)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
