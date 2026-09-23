from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import OuterRef, Subquery, Value
from django.db.models.functions import Coalesce, Lower

from services.accounts.models import Organisation

from . import taxonomy
from .health import breakdown_from, score_from

#: CSAT bands, worst first, as `(upper_bound_inclusive, label)`.
#:
#: Equal fifths of the 0-100 scale. Survey.score for a CSAT response is a
#: percentage, but the popover this backs has always spoken in five
#: satisfaction bands — so the mapping is the plainest one that can be
#: explained in a sentence, rather than a curve nobody can defend.
CSAT_BANDS = (
    (20, "very_dissatisfied"),
    (40, "dissatisfied"),
    (60, "neutral"),
    (80, "satisfied"),
    (100, "very_satisfied"),
)

CSAT_BAND_LABELS = {
    "very_satisfied": "Very Satisfied",
    "satisfied": "Satisfied",
    "neutral": "Neutral",
    "dissatisfied": "Dissatisfied",
    "very_dissatisfied": "Very Dissatisfied",
}


def csat_band(score):
    """Which band a 0-100 CSAT score falls in, or None if there isn't one."""
    if score is None:
        return None
    for upper, band in CSAT_BANDS:
        if score <= upper:
            return band
    # Above 100 shouldn't happen (the serializer range-checks it), but a
    # stray high score is "very satisfied", not "no band at all".
    return CSAT_BANDS[-1][1]


def health_category_for(score):
    """Map a 0-10 health score onto its display category.

    Coerces first: a DecimalField holds whatever was assigned to it until the
    row is reloaded, so an instance built with `health_score="2.5"` carries a
    str, and comparing that to a threshold raises TypeError rather than
    returning a category. Customer, Account and HealthSnapshot all read through
    here so none of them can drift from the others.
    """
    for threshold, category in Customer.HEALTH_THRESHOLDS:
        if Decimal(score) >= threshold:
            return category
    return Customer.HealthCategory.POOR


def ai_pulse_category(value):
    """Map a 1-5 AI pulse value onto the four categories the API speaks in.

    Lives at module level rather than on Customer because Account, the
    serializers and the snapshot model all need the same mapping, and a second
    copy is exactly how a derived value starts disagreeing with itself.
    """
    if value is None:
        return ""
    for threshold, category in Customer.AI_PULSE_THRESHOLDS:
        if value >= threshold:
            return category
    return Customer.AIPulseScore.HIGH_RISK


class Product(models.Model):
    """What this tenant sells.

    `Customer.primary_product` was free text until migration 0030, for the
    same reason `churn_reason` was: nobody had decided where the list of
    products lived. So the Product Usage dashboard grouped "Product A" and
    "product a" by folding case, reported how many spellings it had folded,
    and could never have merged "Integrations Module" with "Integrations
    module (EU)".

    Unlike churn reasons, products cannot be a `TextChoices` enum: every
    tenant sells something different, and a list compiled into the code would
    be this organisation's list imposed on all of them. So they are rows,
    scoped to an organisation like everything else here.

    Uniqueness is **case-insensitive per organisation**, which is the entire
    point: "Product A" and "product a" cannot both exist, so they cannot both
    appear on a dashboard.

    Retire a product with `is_active=False` rather than deleting it.
    `Customer.primary_product` is `PROTECT`ed, so a product customers are
    still on cannot be deleted at all — the alternative is a dashboard that
    quietly loses the history of what those customers bought.
    """

    organisation = models.ForeignKey(
        Organisation, related_name="products", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(
        default=True,
        help_text="False retires a product from the pickers without touching "
        "the customers already on it, so last year's figures still say what "
        "they said.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                Lower("name"),
                "organisation",
                name="product_name_unique_per_organisation_ci",
            )
        ]

    def __str__(self):
        return self.name


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

    `email`/`phone` aren't part of that original mock schema — they back
    ActivityFeed's Overview tab (Domain/Location/Email/Phone), which
    used to show a fabricated `contact@<domain>` and a hardcoded phone
    number identical for every organization.

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

    class ChurnReason(models.TextChoices):
        """Why a customer left.

        A closed list, because the free-text field it replaces could not be
        counted. "Budget cuts", "budget CUTS " and "Budget Cut" were three
        rows on the Customer Overview, and no amount of folding in the
        dashboard could merge the last one honestly — while "Price" and "Too
        expensive" stayed apart forever. The nuance a CSM wants to record
        still has a home: `churn_comment`, right below this.

        These eleven are the reasons a CS team can act on differently. Price
        and budget are separate because the answer differs — one is a
        discount conversation, the other is waiting for their next fiscal
        year. `OTHER` exists so nobody is forced to lie, and a rising `OTHER`
        count is the signal that this list needs another entry.
        """

        PRICE = "price", "Price"
        BUDGET = "budget", "Budget cut"
        PRODUCT_GAP = "product_gap", "Missing capability"
        ADOPTION = "adoption", "Never adopted"
        COMPETITOR = "competitor", "Switched to a competitor"
        CHAMPION_LEFT = "champion_left", "Champion left"
        ACQUIRED = "acquired", "Acquired or merged"
        SHUT_DOWN = "shut_down", "Went out of business"
        CONSOLIDATION = "consolidation", "Vendor consolidation"
        SUPPORT = "support", "Service or support"
        OTHER = "other", "Other"

    # --- Identity, ownership, provenance -------------------------------------

    organisation = models.ForeignKey(
        "accounts.Organisation", related_name="customers", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255, blank=True, help_text='"Name / Address" column.')
    domain = models.CharField(max_length=255, blank=True)
    industry = models.CharField(
        max_length=255,
        blank=True,
        help_text="Free-text, hand-entered (e.g. 'Video conferencing software'). "
        "Shown on Overview, and — the reason it exists — folded into what "
        "services/copilot/embeddings.py embeds for semantic company "
        "matching, so a company described but not named by its own name "
        "(e.g. 'that video conferencing account') can still be recognized "
        "once a CSM has actually filled this in; blank falls back to "
        "matching on the bare name alone, same as before this field "
        "existed.",
    )
    email = models.EmailField(blank=True, help_text="Primary contact email, shown on Overview.")
    phone = models.CharField(
        max_length=32, blank=True, help_text="Primary contact phone, shown on Overview."
    )
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
    is_archived = models.BooleanField(
        default=False,
        help_text="Soft-hide from the default list/stats/renewal views without deleting. "
        "Distinct from lifecycle_stage=churn — archiving is 'stop showing me this', "
        "churning is a business outcome.",
    )

    # --- Lifecycle & health ---------------------------------------------------

    lifecycle_stage = models.CharField(
        max_length=20, choices=LifecycleStage.choices, default=LifecycleStage.ONBOARDING
    )
    health_score = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        default=5.0,
        help_text="0.0-10.0. Maintained by recalculate_health() from the five "
        "components in health.py — or set straight from health_score_override when "
        "that is set. Stored rather than derived so the list endpoint can still "
        "order and filter on it in SQL. health_category is derived from it.",
    )
    health_score_override = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        null=True,
        blank=True,
        help_text="A score pinned by hand, overriding the rubric. Null means the "
        "score is whatever the components add up to. Kept as its own column rather "
        "than just writing health_score, so 'a human decided this' stays "
        "distinguishable from 'the maths happened to land here' — the popover says "
        "which, and clearing this returns the customer to the calculation.",
    )
    pulse = models.JSONField(
        default=list, blank=True, help_text="Recent pulse-history dots, e.g. [1,1,0,2,1]."
    )
    ai_pulse_value = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="1-5, produced by the model. ai_pulse_score (the category) is "
        "derived from this, not stored — same reasoning as health_score/health_category. "
        "Null means the model hasn't scored this one yet, which is not the same as a 1.",
    )
    ai_pulse_reason = models.TextField(blank=True)
    csm_pulse_score = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="1-5, set by hand by the CSM who owns this. Deliberately the same "
        "scale as ai_pulse_value so the two are directly comparable — the gap between "
        "them is what the Health Overview's Divergence view reads. Null means the CSM "
        "hasn't rated it, which is not the same as a 1.",
    )
    csm_pulse_modified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When csm_pulse_score was last changed. Not auto_now — it tracks the "
        "pulse specifically, not any edit to the row, so a stale CSM read stays visibly stale.",
    )
    pulse_recorded_on = models.DateField(
        null=True,
        blank=True,
        help_text="The day the daily job last appended this customer's computed pulse "
        "category to `pulse` — see run_health_maintenance and pulse.py.",
    )
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

    currency = models.CharField(
        max_length=3,
        choices=Organisation.Currency.choices,
        default=Organisation.Currency.USD,
        help_text="The currency this customer's own contract/financial fields "
        "below are denominated in — independent of Organisation.currency, "
        "the tenant's own reporting currency. Defaults to the org's currency "
        "at creation (see CustomerSerializer.create()) but can differ from "
        "it, e.g. a US-HQ org billing one customer in EUR. Rollups that sum "
        "across customers with different currencies convert via "
        "services.fx_rates.conversion.convert_to_org_currency().",
    )
    arr_billed_at_account = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    arr_billed_at_hq = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    implementation_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_contract_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_forecasted_renewal_revenue = models.DecimalField(
        max_digits=12, decimal_places=2, default=0
    )

    # --- Product & usage -----------------------------------------------------------

    primary_product = models.ForeignKey(
        "Product",
        related_name="primary_customers",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        help_text="The product this customer is led by, from the tenant's own "
        "Product table — free text until migration 0030. Null means nobody "
        "recorded one. PROTECT rather than SET_NULL: losing the record of what "
        "a customer bought is worse than being made to retire the product "
        "instead (Product.is_active). Only one product per customer is "
        "recorded, which is the limit the Product Usage dashboard states on "
        "screen; additional_products_count below counts the rest without "
        "naming them.",
    )
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
    churn_reason = models.CharField(
        max_length=32,
        choices=ChurnReason.choices,
        blank=True,
        help_text="Why they left, from a closed list — blank means nobody "
        "recorded it, which is not the same as ChurnReason.OTHER. The detail "
        "goes in churn_comment; this field exists to be counted.",
    )
    churn_comment = models.TextField(blank=True)

    # score >= 7.0 -> good, 4.0-6.9 -> average, < 4.0 -> poor.
    HEALTH_THRESHOLDS = ((7, HealthCategory.GOOD), (4, HealthCategory.AVERAGE))

    # value 5 -> very satisfied, 4 -> satisfied, 3 -> moderate, 1-2 -> high risk.
    # The API still speaks in these four categories; the number behind them is
    # what the dashboards compare against csm_pulse_score.
    AI_PULSE_THRESHOLDS = (
        (5, AIPulseScore.VERY_SATISFIED),
        (4, AIPulseScore.SATISFIED),
        (3, AIPulseScore.MODERATE),
    )

    @property
    def csat_breakdown(self):
        """How this customer's answered CSAT surveys fall across the five bands.

        Reads `surveys` — prefetched as `_csat_responses` where the queryset
        supplied it, queried otherwise, the same arrangement as
        `health_inputs`. Returns every band, including the empty ones, so the
        popover renders a stable five-row shape rather than a list that
        changes length per customer.
        """
        if hasattr(self, "_csat_responses"):
            responses = self._csat_responses
        else:
            responses = self.surveys.filter(
                survey_type=Survey.SurveyType.CSAT, status=Survey.Status.RESPONDED
            )

        counts = {band: 0 for _, band in CSAT_BANDS}
        total = 0
        for survey in responses:
            band = csat_band(survey.score)
            if band is None:
                continue
            counts[band] += 1
            total += 1

        # Worst band last, matching the popover's own top-to-bottom order.
        ordered = [band for _, band in reversed(CSAT_BANDS)]
        return {
            "responses": total,
            "bands": [
                {
                    "key": band,
                    "label": CSAT_BAND_LABELS[band],
                    "count": counts[band],
                    "share": round(counts[band] / total * 100, 2) if total else 0.0,
                }
                for band in ordered
            ],
        }

    @property
    def health_score_is_overridden(self):
        return self.health_score_override is not None

    def health_inputs(self, today=None):
        """Everything health.py needs, as plain values.

        Reads the two related-row figures off annotations when the queryset
        supplied them (see `with_health_inputs`) and falls back to querying
        otherwise — so a single customer works standalone, and a list page
        doesn't run two extra queries per row.
        """
        from django.utils import timezone

        today = today or timezone.localdate()

        if hasattr(self, "_last_touch_on"):
            last_touch_on = self._last_touch_on
        else:
            # Any kind of contact — the same rule Activity Tracking reads.
            from .contact import last_contact_by_customer

            last_touch_on = last_contact_by_customer([self.pk]).get(self.pk)

        if hasattr(self, "_open_ticket_count"):
            open_ticket_count = self._open_ticket_count
        else:
            open_ticket_count = self.tickets.exclude(status__in=Ticket.RESOLVED_STATUSES).count()

        # An untouched customer is measured from when it arrived, not from
        # never — a logo onboarded last week hasn't been neglected.
        reference = last_touch_on or self.joined_date or self.created_at.date()

        return {
            "days_since_touch": (today - reference).days,
            "ai_pulse_value": self.ai_pulse_value,
            "active_seats": self.total_active_seats,
            "contracted_seats": self.total_contracted_seats,
            # Only whether there is one, not which: the rubric counts breadth.
            # Reading the id keeps a list page from fetching every Product row
            # to ask a yes/no question.
            "primary_product": bool(self.primary_product_id),
            "additional_products_count": self.additional_products_count,
            "open_ticket_count": open_ticket_count,
        }

    def pulse_inputs(self, today=None):
        """Everything pulse.py needs for the organisation: its own signals,
        with the conversations, last contact and tickets logged on any of
        its accounts counted as its own — so account pulses roll up.
        Reads annotations when the queryset supplied them (see
        `with_health_inputs` for the last touch and
        `with_customer_pulse_inputs` for the rest) and queries otherwise."""
        from datetime import timedelta

        from django.utils import timezone

        from . import pulse as pulse_rules

        today = today or timezone.localdate()
        if hasattr(self, "_last_touch_on"):
            last_touch_on = self._last_touch_on
        else:
            from .contact import last_contact_by_customer

            last_touch_on = last_contact_by_customer([self.pk]).get(self.pk)
        if hasattr(self, "_pulse_open_ticket_count"):
            open_ticket_count = self._pulse_open_ticket_count
        else:
            from .contact import parent_q

            open_ticket_count = (
                Ticket.objects.filter(parent_q([self.pk]))
                .exclude(status__in=Ticket.RESOLVED_STATUSES)
                .distinct()
                .count()
            )
        if hasattr(self, "_positive_count"):
            positive, negative, classified = (
                self._positive_count,
                self._negative_count,
                self._classified_count,
            )
        else:
            from .contact import parent_q

            since = today - timedelta(days=pulse_rules.SENTIMENT_WINDOW_DAYS)
            positive = negative = classified = 0
            for model, field in SENTIMENT_SOURCES:
                lookup = f"{field}__date__gte" if _is_datetime(model, field) else f"{field}__gte"
                for row in (
                    model.objects.filter(parent_q([self.pk]), **{lookup: since})
                    .distinct()
                    .values("sentiment")
                    .annotate(n=models.Count("id", distinct=True))
                ):
                    classified += row["n"]
                    if row["sentiment"] == taxonomy.Sentiment.POSITIVE:
                        positive += row["n"]
                    elif row["sentiment"] == taxonomy.Sentiment.NEGATIVE:
                        negative += row["n"]
        csm_age = None
        if self.csm_pulse_modified_at is not None:
            csm_age = (today - self.csm_pulse_modified_at.date()).days
        return {
            "ai_pulse_value": self.ai_pulse_value,
            "csm_pulse_score": self.csm_pulse_score,
            "csm_pulse_age_days": csm_age,
            "positive_count": positive,
            "negative_count": negative,
            "classified_count": classified,
            "days_since_touch": (today - last_touch_on).days if last_touch_on else None,
            "open_ticket_count": open_ticket_count,
        }

    def account_pulse(self, today=None):
        """The computed pulse (pulse.py) for this organisation."""
        from . import pulse as pulse_rules

        return pulse_rules.compute(**self.pulse_inputs(today))

    @property
    def health_breakdown(self):
        """The five components behind this customer's score."""
        return breakdown_from(**self.health_inputs())

    def recalculate_health(self, save=True):
        """Recompute `health_score` from the rubric (or apply the override).

        Called explicitly rather than from save(): the calculation needs two
        related-row figures, and running them on every write — including writes
        that touch nothing health-related — would be a surprise cost on a model
        this widely saved.

        Leaves the existing score alone when nothing at all could be measured,
        rather than dropping the customer to zero for having an empty CRM row.
        """
        if self.health_score_override is not None:
            self.health_score = self.health_score_override
        else:
            computed = score_from(self.health_breakdown)
            if computed is None:
                return self.health_score
            self.health_score = computed

        if save:
            self.save(update_fields=["health_score"])
        return self.health_score

    @property
    def health_category(self):
        return health_category_for(self.health_score)

    @property
    def ai_pulse_score(self):
        """The AI pulse as one of the four display categories.

        Was a stored CharField. It is derived now so it can never disagree with
        `ai_pulse_value`, which is the number the Divergence view plots against
        the CSM's own read. Blank (not null) when unscored, matching what the
        column used to hold and what the frontend's AI_PULSE_LABELS expects.
        """
        return ai_pulse_category(self.ai_pulse_value)

    @property
    def seat_utilization_percentage(self):
        if not self.total_contracted_seats or self.total_active_seats is None:
            return None
        return round(self.total_active_seats / self.total_contracted_seats * 100, 2)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Account(models.Model):
    """A named sub-account under one of the tenant's Customers — e.g. a
    regional or business-unit deployment of that customer, tracked with
    its own health/pulse/NPS/CSAT distinct from the parent Customer's own
    aggregate numbers. A Customer can have any number of these (one-to-
    many); an Account belongs to exactly one Customer.

    Field set mirrors the frontend's `accountsData.ts` mock schema
    (`AccountRow`) column for column, the same way Customer mirrors
    `tableData.ts`. Two mock fields deliberately have no column here:
    `orgName` is just the parent customer's own `name` (available via the
    `customer` FK — no need to duplicate it), and `revenactId` is this
    row's own `id`, same convention as Customer's "Revenact ID".

    Reuses Customer's LifecycleStage/AIPulseScore choices and
    HEALTH_THRESHOLDS rather than redefining them — an account's
    lifecycle stage and health mean exactly the same thing as a
    customer's, just at a finer grain.

    `domain`/`industry`/`address`/`email`/`phone` all fall back to the
    parent Customer's own value when blank — the frontend's own
    mapAccountToAccountRow.ts does the falling back for display (same as
    it already did for `domain` alone before `address`/`email`/`phone`
    existed here), not this model or its serializer; a sub-account
    with no info of its own is assumed to share its parent's contact
    details rather than have none, matching ActivityFeed's Overview
    tab for the standalone Account page. `industry` gets a second, real
    fallback resolution server-side too — see
    services/copilot/retrieval.py's own `_effective_industry` — since
    Copilot's semantic company matching runs in Python, not the browser.

    Add/Edit Account is wired (AccountListCreateView/AccountDetailView) —
    see those views' docstrings for exactly which fields the UI sends.

    `customers` is a many-to-many, not a single FK — real-world account
    ownership isn't always a clean tree (a joint venture co-owned by two
    Customers, a shared subsidiary serviced by both a vendor's and a
    reseller's own Customer record, a holding-company restructuring that
    attaches an existing Account to a new parent without detaching the
    old one). Every `Customer` an Account is linked to must belong to
    the same `Organisation` as every other one — enforced in
    AccountSerializer.validate_customer_ids, not at the DB level (a
    plain M2M can't express a same-tenant constraint on its own) —
    letting an Account span two different tenants would break every
    other endpoint's own `request.user.organisation` scoping.

    Every "nested under one Customer" endpoint in this file (Contacts/
    Opportunities/Risks/Activities/etc. `.../accounts/<id>/...`, and
    `/customers/<customer_id>/accounts/<id>/` itself) still resolves an
    Account through *one* `customer_id` from the URL — that URL means
    "an Account this Customer is one of the (possibly several) owners
    of", not "the Account's only owner". Any Customer an Account is
    linked to can reach it that way."""

    customers = models.ManyToManyField(
        Customer,
        related_name="accounts",
        blank=True,
        help_text="Every Customer this Account belongs to — see this model's own "
        "docstring for why this is a many-to-many, not a single FK.",
    )
    name = models.CharField(max_length=255)
    domain = models.CharField(
        max_length=255,
        blank=True,
        help_text="Falls back to the parent customer's domain (for the logo) when blank.",
    )
    industry = models.CharField(
        max_length=255,
        blank=True,
        help_text="Falls back to the parent customer's industry when blank — same "
        "convention as domain/address/email/phone below. See Customer.industry's "
        "own help_text for what this is actually for.",
    )
    address = models.CharField(
        max_length=255,
        blank=True,
        help_text="Falls back to the parent customer's address (Overview's Location) when blank.",
    )
    email = models.EmailField(
        blank=True,
        help_text="Falls back to the parent customer's email (Overview) when blank.",
    )
    phone = models.CharField(
        max_length=32,
        blank=True,
        help_text="Falls back to the parent customer's phone (Overview) when blank.",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="owned_accounts",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="The CSM (or admin) assigned to this account. Must be in the same organisation.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    lifecycle_stage = models.CharField(
        max_length=20,
        choices=Customer.LifecycleStage.choices,
        default=Customer.LifecycleStage.ONBOARDING,
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
    ai_pulse_value = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="1-5, produced by the model. See Customer.ai_pulse_value.",
    )
    ai_pulse_reason = models.TextField(blank=True)
    csm_pulse_score = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="1-5, set by hand by the CSM. See Customer.csm_pulse_score.",
    )
    csm_pulse_modified_at = models.DateTimeField(
        null=True, blank=True, help_text="See Customer.csm_pulse_modified_at."
    )
    pulse_recorded_on = models.DateField(
        null=True,
        blank=True,
        help_text="The day the daily job last appended this account's computed pulse "
        "category to `pulse` — see run_health_maintenance and pulse.py.",
    )
    nps_score = models.IntegerField(null=True, blank=True, help_text="-100 to 100.")
    csat_score = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True, help_text="0-100 (%)."
    )
    renewal_date = models.DateField(null=True, blank=True)
    arr = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="MRR is derived (arr / 12) rather than stored, same as Customer.",
    )

    @property
    def health_category(self):
        return health_category_for(self.health_score)

    @property
    def ai_pulse_score(self):
        """See Customer.ai_pulse_score — same derivation, same categories."""
        return ai_pulse_category(self.ai_pulse_value)

    def pulse_inputs(self, today=None):
        """Everything pulse.py needs, as plain values. Reads the related-row
        figures off annotations when the queryset supplied them (see
        `with_pulse_inputs`) and queries otherwise."""
        from datetime import timedelta

        from django.utils import timezone

        from . import pulse as pulse_rules

        today = today or timezone.localdate()
        if hasattr(self, "_last_touch_on"):
            last_touch_on = self._last_touch_on
        else:
            from .contact import last_contact_by_account

            last_touch_on = last_contact_by_account([self.pk]).get(self.pk)
        if hasattr(self, "_open_ticket_count"):
            open_ticket_count = self._open_ticket_count
        else:
            open_ticket_count = self.tickets.exclude(status__in=Ticket.RESOLVED_STATUSES).count()
        if hasattr(self, "_positive_count"):
            positive, negative, classified = (
                self._positive_count,
                self._negative_count,
                self._classified_count,
            )
        else:
            since = today - timedelta(days=pulse_rules.SENTIMENT_WINDOW_DAYS)
            positive = negative = classified = 0
            for model, field in SENTIMENT_SOURCES:
                lookup = f"{field}__date__gte" if _is_datetime(model, field) else f"{field}__gte"
                for row in (
                    model.objects.filter(account=self, **{lookup: since})
                    .values("sentiment")
                    .annotate(n=models.Count("id"))
                ):
                    classified += row["n"]
                    if row["sentiment"] == taxonomy.Sentiment.POSITIVE:
                        positive += row["n"]
                    elif row["sentiment"] == taxonomy.Sentiment.NEGATIVE:
                        negative += row["n"]
        csm_age = None
        if self.csm_pulse_modified_at is not None:
            csm_age = (today - self.csm_pulse_modified_at.date()).days
        return {
            "ai_pulse_value": self.ai_pulse_value,
            "csm_pulse_score": self.csm_pulse_score,
            "csm_pulse_age_days": csm_age,
            "positive_count": positive,
            "negative_count": negative,
            "classified_count": classified,
            "days_since_touch": (today - last_touch_on).days if last_touch_on else None,
            "open_ticket_count": open_ticket_count,
        }

    def account_pulse(self, today=None):
        """The computed pulse (pulse.py) for this account."""
        from . import pulse as pulse_rules

        return pulse_rules.compute(**self.pulse_inputs(today))

    class Meta:
        ordering = ["name"]

    def __str__(self):
        names = ", ".join(self.customers.values_list("name", flat=True)) or "no organisation"
        return f"{self.name} ({names})"


def with_health_inputs(queryset):
    """Annotate `queryset` with the two related-row figures the rubric needs.

    Subqueries rather than `annotate(Max(...), Count(...))`: aggregating over
    different reverse relations in one annotate joins them together first, so
    every activity multiplies every ticket and the count comes back inflated.
    Correlated subqueries give the right numbers and still cost one query for
    the page.

    `Customer.health_inputs` picks these up automatically when they're present.
    """
    # Imported here: contact.py imports the models, so the models module
    # cannot import it at the top.
    from .contact import last_contact_annotation

    open_tickets = (
        Ticket.objects.filter(customer=OuterRef("pk"))
        .exclude(status__in=Ticket.RESOLVED_STATUSES)
        .order_by()
        .values("customer")
        .annotate(value=models.Count("id"))
        .values("value")[:1]
    )
    return queryset.annotate(
        # The newest contact of any kind — calls, emails, notes, meetings,
        # activities — on the company or any of its accounts. Was Activity
        # rows only, which let a customer emailed every week decay to "no
        # touch". contact.py owns the list of what counts.
        _last_touch_on=last_contact_annotation(),
        # No open tickets at all means the subquery returns nothing, not 0.
        _open_ticket_count=Coalesce(Subquery(open_tickets, output_field=models.IntegerField()), 0),
    ).prefetch_related(
        # `csat_breakdown` buckets these in Python. A prefetch rather than five
        # more subqueries: one query for the page either way, and the banding
        # rule stays in one place instead of being restated in SQL.
        models.Prefetch(
            "surveys",
            queryset=Survey.objects.filter(
                survey_type=Survey.SurveyType.CSAT, status=Survey.Status.RESPONDED
            ),
            to_attr="_csat_responses",
        )
    )


class HealthSnapshot(models.Model):
    """What one Customer's (or Account's) health looked like on one date.

    `Customer.health_score` / `csm_pulse_score` / `ai_pulse_value` only ever
    hold *today's* reading — updating them overwrites yesterday's. This is the
    row that remembers, so the Health Overview's Movement view can count how
    many accounts moved between Good/Average/Poor from one month to the next
    rather than just how many sit in each today. A month where nine accounts
    fell and nine recovered is indistinguishable from a quiet one without it.

    One row per parent per `captured_on` (enforced below). Nothing writes these
    on a schedule yet — `capture_health_snapshot` is the entry point, and the
    seed command backfills a year so the charts have something real to draw.

    `customer`/`account` are both nullable FKs with a CheckConstraint that
    exactly one is set, the same shape and for the same reasons as Activity —
    see that model's docstring.

    The three readings are stored, not derived from the parent: that is the
    whole point of a snapshot. `health_category` and `ai_pulse_score` *are*
    derived, from this row's own stored numbers, so a snapshot reports its
    categories exactly the way a live row does.
    """

    customer = models.ForeignKey(
        Customer,
        related_name="health_snapshots",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Set for an organization-level snapshot. Exactly one of "
        "customer/account is set, never both — see the model's own CheckConstraint.",
    )
    account = models.ForeignKey(
        Account,
        related_name="health_snapshots",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Set for an account-level snapshot. Exactly one of "
        "customer/account is set, never both — see the model's own CheckConstraint.",
    )
    captured_on = models.DateField(
        help_text="The date this reading is for. Month-end when backfilled, so a "
        "series of these lines up as monthly columns."
    )
    health_score = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        help_text="0.0-10.0 as it stood on captured_on. health_category is derived.",
    )
    csm_pulse_score = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="1-5 as it stood on captured_on, or null if unrated then.",
    )
    ai_pulse_value = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="1-5 as it stood on captured_on, or null if unscored then.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def health_category(self):
        return health_category_for(self.health_score)

    @property
    def ai_pulse_score(self):
        return ai_pulse_category(self.ai_pulse_value)

    class Meta:
        ordering = ["captured_on", "id"]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(customer__isnull=False, account__isnull=True)
                    | models.Q(customer__isnull=True, account__isnull=False)
                ),
                name="healthsnapshot_belongs_to_exactly_one_parent",
            ),
            # Partial uniques rather than unique_together: a null parent must not
            # collide with every other null parent on the same date.
            models.UniqueConstraint(
                fields=["customer", "captured_on"],
                condition=models.Q(customer__isnull=False),
                name="healthsnapshot_one_per_customer_per_date",
            ),
            models.UniqueConstraint(
                fields=["account", "captured_on"],
                condition=models.Q(account__isnull=False),
                name="healthsnapshot_one_per_account_per_date",
            ),
        ]
        indexes = [
            models.Index(fields=["customer", "captured_on"]),
            models.Index(fields=["account", "captured_on"]),
        ]

    def __str__(self):
        parent = self.customer or self.account
        return f"{parent} @ {self.captured_on} ({self.health_category})"


def capture_health_snapshot(parent, captured_on=None):
    """Record `parent`'s current health as a snapshot for `captured_on`.

    Upserts: re-running for a date that already has a row overwrites it rather
    than raising, so a scheduled job that fires twice in a day is harmless.
    `parent` is a Customer or an Account; which FK gets set follows from that.
    """
    from django.utils import timezone

    captured_on = captured_on or timezone.localdate()
    key = "customer" if isinstance(parent, Customer) else "account"

    snapshot, _ = HealthSnapshot.objects.update_or_create(
        **{key: parent},
        captured_on=captured_on,
        defaults={
            "health_score": parent.health_score,
            "csm_pulse_score": parent.csm_pulse_score,
            "ai_pulse_value": parent.ai_pulse_value,
        },
    )
    return snapshot


class AIClassified(models.Model):
    """What the AI Trending Topics dashboard needs from an interaction:
    how it felt, and where it sits in the taxonomy (see taxonomy.py).

    Abstract, shared by Email/Call/Ticket — the three record types that
    dashboard counts. One definition rather than three, because its
    whole job is to group all three together in one chart: a donut
    slicing emails, calls and tickets by sentiment is only meaningful if
    "negative" means the same thing in all three, and three separate
    copies of these fields is exactly how that stops being true.

    Not on Activity/Note/Task. An Activity is something *we* did (a
    health check, a QBR), with no customer voice in it to read a
    sentiment from, and the dashboard is about inbound conversations.

    `ai_classified_at` is null for a row nothing has looked at yet, and
    that matters: it's how `classify_interactions` finds its work, and
    it's what separates "no opinion yet" from a deliberate blank. The
    charts count only what's classified, so an unclassified row is
    absent from the AI breakdowns rather than lumped into a bucket it
    was never put in."""

    #: Aliased rather than redefined: the vocabulary lives in taxonomy.py
    #: with the rest of it. Keeping the name here is what lets the ~30
    #: existing `Ticket.Sentiment.POSITIVE` call sites go on working.
    Sentiment = taxonomy.Sentiment

    sentiment = models.CharField(
        max_length=16,
        choices=Sentiment.choices,
        default=Sentiment.NEUTRAL,
        help_text="How the customer sounds here. Backs the AI Trending Topics "
        "dashboard's sentiment donut and trend line, and (for tickets) the "
        "Ticket Overview dashboard's own positive/negative counts.",
    )
    ai_area = models.CharField(
        max_length=32,
        choices=taxonomy.AIArea.choices,
        blank=True,
        help_text="Which side of the business owns this conversation. Blank "
        "until something classifies it.",
    )
    ai_category = models.CharField(
        max_length=32,
        choices=taxonomy.AICategory.choices,
        blank=True,
        help_text="What kind of conversation this is.",
    )
    ai_subcategory = models.CharField(
        max_length=32,
        choices=taxonomy.AISubcategory.choices,
        blank=True,
        help_text="The specific flavour. Must belong to ai_category — see "
        "taxonomy.SUBCATEGORIES_BY_CATEGORY.",
    )
    ai_classified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the taxonomy above was last written. Null means this "
        "row has never been classified, which is what classify_interactions "
        "looks for.",
    )
    classification_corrected_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When a person last corrected the tags by hand. Set, the row is "
        "skipped by classify_interactions --reclassify: a correction outranks "
        "the model, and the next run must not quietly put the model's answer "
        "back. The correction itself is in metrics.Feedback.",
    )

    class Meta:
        abstract = True

    @property
    def is_classified(self):
        return self.ai_classified_at is not None

    def clean(self):
        super().clean()
        error = taxonomy.validate_classification(
            ai_category=self.ai_category, ai_subcategory=self.ai_subcategory
        )
        if error:
            raise ValidationError({"ai_subcategory": error})


class Activity(models.Model):
    """A timeline entry — a CSM-visible event belonging to either a
    Customer (organization-level) or one of its Accounts (account-
    level), never both. Backs the "Activities" filter within
    ActivityFeed on both the Organization Details page's General tab
    and the standalone Account page — same component, same card shape,
    reading a different scope depending on which entity it's mounted
    under (see AccountListView's own docstring for the analogous
    one-model-two-scopes reasoning).

    Mirrors the frontend's mock ActivityItem shape
    (react-ts-app/src/components/organizations/activityData.ts): `type`
    is the card's title, `occurred_at` its date, `links`/`watchers` the
    two small counters on the card. No `pulse` field — the card's
    "Pulse" badge is decorative in the mock (always shown, tied to
    nothing) and stays that way here; it wasn't asked for as a real
    value, just described as part of what's already on the card.

    `customer`/`account` are both nullable FKs rather than a single
    generic relation — simpler for exactly two possible parents, and
    the CheckConstraint below enforces exactly one is set at the DB
    level (not just app-level validation, since there's no
    create/update endpoint yet to run that validation through)."""

    class ActivityType(models.TextChoices):
        VALUE_REINFORCEMENT = "value_reinforcement", "Value Reinforcement"
        ENABLEMENT_RETRAINING = "enablement_retraining", "Enablement or Re-Training"
        HEALTH_CHECK_REVIEW = "health_check_review", "Health Check Review"
        PRODUCT_USAGE_ANALYSIS = "product_usage_analysis", "Product Usage Analysis"
        ESCALATION_TRIGGERED = "escalation_triggered", "Escalation Triggered"
        ONBOARDING_MILESTONE = "onboarding_milestone", "Onboarding Milestone Reached"
        SUCCESS_PLAN_CREATED = "success_plan_created", "Success Plan Created"
        SUCCESS_PLAN_UPDATED = "success_plan_updated", "Success Plan Updated"
        EXECUTIVE_ALIGNMENT_SESSION = "executive_alignment_session", "Executive Alignment Session"
        RENEWAL_PROPOSAL_SUBMITTED = "renewal_proposal_submitted", "Renewal Proposal Submitted"
        OTHER = "other", "Other"

    customer = models.ForeignKey(
        Customer,
        related_name="activities",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Set for an organization-level activity. Exactly one of "
        "customer/account is set, never both — see the model's own CheckConstraint.",
    )
    account = models.ForeignKey(
        Account,
        related_name="activities",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Set for an account-level activity. Exactly one of "
        "customer/account is set, never both — see the model's own CheckConstraint.",
    )
    type = models.CharField(max_length=32, choices=ActivityType.choices)
    occurred_at = models.DateField()
    links = models.PositiveIntegerField(default=0, help_text="Count shown on the card's link icon.")
    watchers = models.PositiveIntegerField(
        default=0, help_text="Count shown on the card's eye icon."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(customer__isnull=False, account__isnull=True)
                    | models.Q(customer__isnull=True, account__isnull=False)
                ),
                name="activity_belongs_to_exactly_one_parent",
            )
        ]

    def __str__(self):
        parent = self.customer or self.account
        return f"{self.get_type_display()} — {parent}"


class Email(AIClassified):
    """A logged email — same "belongs to exactly one of Customer or
    Account" shape as Activity above (see that model's own docstring
    for why two nullable FKs + a CheckConstraint rather than a
    GenericForeignKey), backing the "Emails" filter within ActivityFeed
    on both the Organization Details page's General tab and the
    standalone Account page.

    Mirrors the frontend's mock EmailItem shape
    (react-ts-app/src/components/organizations/activityData.ts):
    `subject`/`sender_name`/`recipient_name`/`body` are the card's
    text, `sent_at` is formatted into the card's separate date and
    time displays on the frontend rather than stored as two different
    strings, `links`/`watchers` the two small counters, `is_starred`
    the star icon — unlike Activity's decorative "Pulse" badge, the
    mock's star is a real per-item flag (shown only when true), so it
    stays a real field here too.

    `sender_name`/`recipient_name` are plain text, not a FK to a
    Contact — there's no Contact model yet (the frontend's own Contacts
    tab is still 100% mock), and the mock data itself often names a
    team rather than a person (e.g. "Support Team", "Product Team").
    `sender_avatar` isn't stored either — the frontend derives a
    placeholder avatar straight from sender_name today, no different
    from before this model existed.

    `campaign` is nullable — set only for a row CampaignSendView itself
    created as a byproduct of a real send (see that view's own
    docstring), never client-writable. Every Email row created before
    Campaigns existed, and every one logged some other way, simply has
    it as None."""

    customer = models.ForeignKey(
        Customer,
        related_name="emails",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Set for an organization-level email. Exactly one of "
        "customer/account is set, never both — see the model's own CheckConstraint.",
    )
    account = models.ForeignKey(
        Account,
        related_name="emails",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Set for an account-level email. Exactly one of "
        "customer/account is set, never both — see the model's own CheckConstraint.",
    )
    campaign = models.ForeignKey(
        "campaigns.Campaign",
        related_name="emails",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Set only when this row was created by a real Campaign send.",
    )
    subject = models.CharField(max_length=255)
    sender_name = models.CharField(max_length=150)
    recipient_name = models.CharField(max_length=150)
    body = models.TextField(help_text="The summarized preview shown on the card.")
    sent_at = models.DateTimeField()
    links = models.PositiveIntegerField(default=0, help_text="Count shown on the card's link icon.")
    watchers = models.PositiveIntegerField(
        default=0, help_text="Count shown on the card's eye icon (views)."
    )
    is_starred = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    # --- Synced mail (services.mail) -----------------------------------------
    # Set when the row came through someone's connected mailbox. Whose it is
    # decides who may read it (services.mail.visibility): the owner and
    # their management chain. Rows logged by hand or seeded have none and
    # stay visible to everyone who may open the customer.
    class Direction(models.TextChoices):
        SENT = "sent", "Sent"
        RECEIVED = "received", "Received"

    mailbox = models.ForeignKey(
        "mail.MailboxConnection",
        related_name="emails",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    mailbox_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="mailbox_emails",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Whose mailbox this came through; decides who may read it.",
    )
    direction = models.CharField(max_length=8, choices=Direction.choices, blank=True)
    from_address = models.EmailField(blank=True)
    to_addresses = models.JSONField(default=list, blank=True)
    thread_id = models.CharField(max_length=255, blank=True)
    provider_message_id = models.CharField(max_length=255, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-sent_at", "-id"]
        verbose_name_plural = "emails"
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(customer__isnull=False, account__isnull=True)
                    | models.Q(customer__isnull=True, account__isnull=False)
                ),
                name="email_belongs_to_exactly_one_parent",
            ),
            models.UniqueConstraint(
                fields=["mailbox", "provider_message_id"],
                condition=models.Q(mailbox__isnull=False) & ~models.Q(provider_message_id=""),
                name="email_once_per_mailbox_message",
            ),
        ]
        indexes = [
            models.Index(fields=["mailbox_owner", "-sent_at"]),
            # The Communications queue asks, per received email, whether a
            # later email exists in the same thread for the same mailbox.
            models.Index(
                fields=["mailbox_owner", "thread_id", "sent_at"],
                name="email_thread_lookup_idx",
            ),
        ]

    def __str__(self):
        parent = self.customer or self.account
        return f"{self.subject} — {parent}"


class Task(models.Model):
    """A CSM to-do item — same "belongs to exactly one of Customer or
    Account" shape as Activity/Email above, backing the "Tasks" filter
    within ActivityFeed on both the Organization Details page's General
    tab and the standalone Account page.

    Mirrors the frontend's mock TaskItem shape
    (react-ts-app/src/components/organizations/activityData.ts):
    `title`/`assignee_name`/`due_date`/`priority`/`status` are the
    card's fields. No `group` field — the card's "Overdue"/"This
    Week"/"Next Week"/"Later" bucket is a function of `due_date` and
    the current date, not a fixed value, so it's computed on the
    frontend at render time rather than stored (a stored bucket would
    go stale the moment a week rolls over).

    `assignee_name` is plain text, not a FK to `accounts.User` — same
    reasoning as Email's `sender_name`/`recipient_name`: the mock's
    names (Edgar Holmes, Natalie Reyes, Sarah Chen) are demo flavor
    text, not real signed-up users in any seeded organisation."""

    class Priority(models.TextChoices):
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in-progress", "In Progress"
        COMPLETED = "completed", "Completed"

    customer = models.ForeignKey(
        Customer,
        related_name="tasks",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Set for an organization-level task. Exactly one of "
        "customer/account is set, never both — see the model's own CheckConstraint.",
    )
    account = models.ForeignKey(
        Account,
        related_name="tasks",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Set for an account-level task. Exactly one of "
        "customer/account is set, never both — see the model's own CheckConstraint.",
    )
    title = models.CharField(max_length=255)
    assignee_name = models.CharField(max_length=150)
    # Personal, like a note (services.customers.personal): readable by whoever
    # created it, whoever it is assigned to, and the management chain above
    # either — a manager hands work down and sees it; a report sees what was
    # handed to them, never a senior's other tasks. Seeded tasks have neither.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="tasks_created",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="tasks_assigned",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    due_date = models.DateField()
    priority = models.CharField(max_length=8, choices=Priority.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    initiative = models.ForeignKey(
        "metrics.Initiative",
        related_name="tasks",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="The decision this work serves, if any — set when a proposal linked to "
        "an initiative is approved, so the initiative can show the work under it. "
        "SET_NULL: closing or deleting a decision does not delete the to-do.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["due_date", "-id"]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(customer__isnull=False, account__isnull=True)
                    | models.Q(customer__isnull=True, account__isnull=False)
                ),
                name="task_belongs_to_exactly_one_parent",
            )
        ]

    def __str__(self):
        parent = self.customer or self.account
        return f"{self.title} — {parent}"


class Note(models.Model):
    """A logged note — same "belongs to exactly one of Customer or
    Account" shape as Activity/Email/Task above, backing the "Notes"
    filter within ActivityFeed on both the Organization Details page's
    General tab and the standalone Account page.

    Mirrors the frontend's mock NoteItem shape
    (react-ts-app/src/components/organizations/activityData.ts):
    `title`/`author_name`/`body`/`logged_at` are the card's fields.
    `links` is a real field, unlike Activity's decorative "Pulse"
    badge — the mock's card always showed a hardcoded "1 Links"
    regardless of the note, which was a bug, not a deliberate
    decoration; this makes it a real per-note count, shown on the
    card only when greater than zero ("links if any").

    No `tags` field — the mock carries one, but the component that
    renders NoteItem never displays it, so there's no card field to
    back. No `group` field either — the card's date-group header is
    derived from `logged_at` at render time, same as Activity/Email/
    Task's own date fields.

    `author_name` is plain text, not a FK — same reasoning as Task's
    `assignee_name`/Email's `sender_name`: the mock's names aren't
    real signed-up users in any seeded organisation."""

    customer = models.ForeignKey(
        Customer,
        related_name="notes",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Set for an organization-level note. Exactly one of "
        "customer/account is set, never both — see the model's own CheckConstraint.",
    )
    account = models.ForeignKey(
        Account,
        related_name="notes",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Set for an account-level note. Exactly one of "
        "customer/account is set, never both — see the model's own CheckConstraint.",
    )
    title = models.CharField(max_length=255)
    author_name = models.CharField(max_length=150)
    # Who wrote it, when written in the app. A note is personal: readable
    # by its author and their management chain (services.customers.personal),
    # never by peers or seniors. Seeded/legacy notes have no author and stay
    # visible to everyone who may open the record.
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="notes_written",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    body = models.TextField()
    logged_at = models.DateField()
    links = models.PositiveIntegerField(
        default=0, help_text="Count shown on the card's link line — only rendered when > 0."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-logged_at", "-id"]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(customer__isnull=False, account__isnull=True)
                    | models.Q(customer__isnull=True, account__isnull=False)
                ),
                name="note_belongs_to_exactly_one_parent",
            )
        ]

    def __str__(self):
        parent = self.customer or self.account
        return f"{self.title} — {parent}"


class Ticket(AIClassified):
    """A support ticket — same "belongs to exactly one of Customer or
    Account" shape as Activity/Email/Task/Note above, backing the
    "Tickets" filter within ActivityFeed on both the Organization
    Details page's General tab and the standalone Account page.

    Field set was reverse-engineered from the frontend card
    (react-ts-app/src/components/organizations/activity/TicketsTab.tsx)
    rather than dictated up front — the mock's own TicketItem type
    carries a `description` too, but no component anywhere renders it
    (no ticket detail view exists), so it's left out here the same way
    Note's unrendered `tags` field was. `status` and `priority` are
    both real fields the card visibly depends on: `status` already
    colors the status icon (open/in-progress vs resolved/closed);
    `priority` did NOT actually drive anything in the mock (the flag
    icon rendered identically regardless of priority) even though the
    mock data carried 4 real priority values — same bug shape as the
    old hardcoded "1 Links" text Note/Email/Activity had, so this pass
    wires the flag icon to real priority too, in addition to fixing
    the link count.

    `assignee_name` is plain text, not a FK — same reasoning as
    Task/Email's own assignee/sender fields (the mock's names are
    often a team, e.g. "Support Team", "Engineering"). No `group`
    field — the card's date-group header is derived from `opened_at`
    at render time."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in-progress", "In Progress"
        # Hyphenated to match IN_PROGRESS above, not the underscore
        # Headline.Status happens to use — a value is read alongside its
        # own model's neighbours, not another model's.
        ON_HOLD = "on-hold", "On Hold"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"

    class Priority(models.TextChoices):
        CRITICAL = "critical", "Critical"
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"

    # Terminal statuses — the ones that mean the work is finished.
    # Named once because the resolution-rate KPI and `resolved_at`'s
    # own meaning both depend on the same answer.
    RESOLVED_STATUSES = ("resolved", "closed")

    customer = models.ForeignKey(
        Customer,
        related_name="tickets",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Set for an organization-level ticket. Exactly one of "
        "customer/account is set, never both — see the model's own CheckConstraint.",
    )
    account = models.ForeignKey(
        Account,
        related_name="tickets",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Set for an account-level ticket. Exactly one of "
        "customer/account is set, never both — see the model's own CheckConstraint.",
    )
    ticket_number = models.CharField(max_length=32, help_text='e.g. "TKT-1042".')
    title = models.CharField(max_length=255)
    assignee_name = models.CharField(max_length=150)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    priority = models.CharField(max_length=8, choices=Priority.choices)
    connector = models.ForeignKey(
        "connectors.Connector",
        related_name="tickets",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Where this ticket came from. Null means it was raised in "
        "Revenact itself, which is a real case rather than missing data — and "
        "SET_NULL so removing a connector doesn't delete real support history.",
    )
    opened_at = models.DateField()
    resolved_at = models.DateField(
        null=True,
        blank=True,
        help_text="When the work finished. Null while the ticket is still open. "
        "Without this neither average ticket lifetime nor resolution rate can "
        "be computed at all.",
    )
    links = models.PositiveIntegerField(
        default=0, help_text="Count shown on the card's link line — only rendered when > 0."
    )
    # Which department the ticket belongs to — stamped from the connector
    # it was synced through. Blank means everyone may read it. See
    # personal.py:visible_tickets.
    department = models.CharField(max_length=16, blank=True, default="")
    # The ticket as the source system knows it.
    external_id = models.CharField(max_length=128, blank=True, default="")
    external_url = models.URLField(max_length=500, blank=True, default="")
    description = models.TextField(blank=True, default="")
    requester_name = models.CharField(max_length=150, blank=True, default="")
    requester_email = models.EmailField(blank=True, default="")
    synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-opened_at", "-id"]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(customer__isnull=False, account__isnull=True)
                    | models.Q(customer__isnull=True, account__isnull=False)
                ),
                name="ticket_belongs_to_exactly_one_parent",
            ),
            models.UniqueConstraint(
                fields=["connector", "external_id"],
                condition=models.Q(connector__isnull=False) & ~models.Q(external_id=""),
                name="ticket_unique_per_connector_external_id",
            ),
        ]

    def __str__(self):
        parent = self.customer or self.account
        return f"{self.ticket_number} {self.title} — {parent}"

    def clean(self):
        """A ticket can't come from a connector that doesn't cover its
        own company.

        Enforced here rather than in TicketSerializer because there is
        no ticket create/update endpoint — that serializer is read-only,
        so validation there would be code nothing can reach. `clean()`
        is what Django admin calls, and what the seed commands and any
        future write path go through, so the invariant lives where it
        can actually run.

        Not a database CheckConstraint either: the answer depends on an
        M2M traversal (see Connector.covers), which SQL-level
        constraints can't express."""

        super().clean()
        if self.connector_id is None:
            return

        parent_org_id = (
            self.customer.organisation_id
            if self.customer_id
            else self.account.customers.values_list("organisation_id", flat=True).first()
        )
        if self.connector.organisation_id != parent_org_id:
            raise ValidationError(
                {"connector": "That connector belongs to a different organisation."}
            )
        if not self.connector.covers(customer=self.customer, account=self.account):
            raise ValidationError(
                {
                    "connector": f"{self.connector.name} isn't connected for "
                    f"{self.customer or self.account}."
                }
            )


def attachment_path(instance, filename):
    """Where a file lands on disk: under its organisation, named by a random
    id. The original name is kept on the row, never in the path, so a name
    can't traverse anywhere and two "contract.pdf"s never collide."""
    import uuid

    from .files import extension_of

    return f"attachments/{instance.organisation_id}/{uuid.uuid4().hex}{extension_of(filename)}"


class Attachment(models.Model):
    """A file on a customer or account: the contract, the QBR deck, the
    call transcript. Same "belongs to exactly one of Customer or Account"
    shape as Note/Email/Ticket.

    The bytes live in MEDIA_ROOT (a private volume) and are served only by
    the authenticated download view — `file.url` is never exposed. What
    may be uploaded is a closed list of document, image, transcript and
    audio types (files.py); size is capped; the stored name is sanitised.
    `organisation` is denormalised so the on-disk path and the tenant
    check don't need a join."""

    class Source(models.TextChoices):
        UPLOAD = "upload", "Uploaded"
        TRANSCRIPT = "transcript", "Call transcript"

    organisation = models.ForeignKey(
        Organisation, related_name="attachments", on_delete=models.CASCADE
    )
    customer = models.ForeignKey(
        Customer, related_name="attachments", on_delete=models.CASCADE, null=True, blank=True
    )
    account = models.ForeignKey(
        Account, related_name="attachments", on_delete=models.CASCADE, null=True, blank=True
    )
    file = models.FileField(upload_to=attachment_path, max_length=255)
    name = models.CharField(max_length=255, help_text="The original file name, sanitised.")
    content_type = models.CharField(max_length=100)
    size = models.PositiveBigIntegerField()
    description = models.CharField(max_length=500, blank=True, default="")
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.UPLOAD)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="uploaded_attachments",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(customer__isnull=False, account__isnull=True)
                    | models.Q(customer__isnull=True, account__isnull=False)
                ),
                name="attachment_belongs_to_exactly_one_parent",
            )
        ]

    def __str__(self):
        return self.name

    def delete(self, *args, **kwargs):
        storage, path = self.file.storage, self.file.name
        super().delete(*args, **kwargs)
        if path:
            storage.delete(path)


class Call(AIClassified):
    """A customer call that happened — the record CallSense is about.

    Third of the three interaction types the AI Trending Topics dashboard
    counts (Email and Ticket are the other two), and the one that had no
    model at all: the dashboard's source donut has always had a "Call"
    slice, and CallSenseTab.tsx has always rendered a hardcoded
    CALLSENSE_DATA array behind it.

    Same "belongs to exactly one of Customer or Account" shape as
    Activity/Email/Ticket above — see Activity's own docstring for why
    two nullable FKs and a CheckConstraint rather than a
    GenericForeignKey.

    Distinct from CalendarEvent below, which is a *scheduled* meeting:
    that one is a plan and can be in the future, this one is a
    conversation that took place and has a recording, a duration and a
    sentiment to read. A scheduled call that happened produces both, the
    same way a calendar invite and a meeting recording are two different
    objects in every tool this product integrates with.

    `occurred_at` is a DateTimeField, unlike Activity/Ticket's plain
    dates: the CallSense card renders a time ("Jan 21st 5:12 PM") and
    two calls on one day are ordinary, so the time is real data here
    rather than display sugar.

    `host_name` is plain text, not a FK to Contact or User — same
    reasoning as Ticket.assignee_name and Email.sender_name, and the
    host is as often an external facilitator as one of ours.

    No transcript field. A transcript is the one piece of this that
    genuinely lives in the recorder (tl;dv, Zoom, Gong) and would be
    megabytes per row; nothing in the UI renders one, and storing a copy
    we can't keep in sync would be a liability rather than a feature.
    `summary` is what the cards actually show."""

    customer = models.ForeignKey(
        Customer,
        related_name="calls",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Set for an organization-level call. Exactly one of "
        "customer/account is set, never both — see the model's own CheckConstraint.",
    )
    account = models.ForeignKey(
        Account,
        related_name="calls",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Set for an account-level call. Exactly one of "
        "customer/account is set, never both — see the model's own CheckConstraint.",
    )
    title = models.CharField(max_length=255, help_text='e.g. "EMEA Retail - Renewal Readiness".')
    host_name = models.CharField(max_length=150, help_text="Who ran the call.")
    occurred_at = models.DateTimeField()
    duration_minutes = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Null when the recorder didn't report one — an unknown "
        "length, which is not the same as a zero-minute call.",
    )
    summary = models.TextField(
        blank=True,
        help_text="The recap shown on the CallSense card. Blank for a call nobody has summarised.",
    )
    connector = models.ForeignKey(
        "connectors.Connector",
        related_name="calls",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="The recorder this call came from. Null means it was logged "
        "by hand in Revenact, a real case rather than missing data — and "
        "SET_NULL so removing a connector doesn't delete call history.",
    )
    links = models.PositiveIntegerField(
        default=0, help_text="Count shown on the card's link line — only rendered when > 0."
    )
    # Logged from the CallSense tab: who logged it, the transcript they
    # attached (a text/VTT/SRT file, kept as an Attachment so it is served
    # and backed up like every other file), and a link to the recording
    # wherever it lives.
    logged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="logged_calls",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    transcript = models.OneToOneField(
        Attachment, related_name="call", on_delete=models.SET_NULL, null=True, blank=True
    )
    recording_url = models.URLField(max_length=500, blank=True, default="")
    # Who from the customer's side was on the call. The call's sentiment
    # is theirs: it feeds each participant's own computed sentiment
    # (contact_sentiment.py).
    participants = models.ManyToManyField("Contact", related_name="calls", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(customer__isnull=False, account__isnull=True)
                    | models.Q(customer__isnull=True, account__isnull=False)
                ),
                name="call_belongs_to_exactly_one_parent",
            )
        ]

    def __str__(self):
        parent = self.customer or self.account
        return f"{self.title} — {parent}"

    def clean(self):
        """A call can't come from a recorder that isn't connected for its
        own company — the same invariant Ticket.clean() enforces, for the
        same reason and in the same place. See that method's own
        docstring for why this is model-level rather than in a serializer
        or a CheckConstraint."""

        super().clean()
        if self.connector_id is None:
            return

        parent_org_id = (
            self.customer.organisation_id
            if self.customer_id
            else self.account.customers.values_list("organisation_id", flat=True).first()
        )
        if self.connector.organisation_id != parent_org_id:
            raise ValidationError(
                {"connector": "That connector belongs to a different organisation."}
            )
        if not self.connector.covers(customer=self.customer, account=self.account):
            raise ValidationError(
                {
                    "connector": f"{self.connector.name} isn't connected for "
                    f"{self.customer or self.account}."
                }
            )


class CalendarEvent(models.Model):
    """A scheduled meeting/call/review/demo — same "belongs to exactly
    one of Customer or Account" shape as Activity/Email/Task/Note/
    Ticket above, backing the "Calendar Events" filter within
    ActivityFeed on both the Organization Details page's General tab
    and the standalone Account page.

    Field set was reverse-engineered from the frontend card
    (react-ts-app/src/components/organizations/activity/
    CalendarEventsTab.tsx) — unlike Ticket's card, nothing here was
    found unwired; `type` already colored the icon/badge for real, and
    every other field the card touches maps to a real value. The one
    departure from the mock's own shape: the mock's `attendees` field
    carries a full array of names, but the card only ever renders
    `attendees.length` ("N attendees") — never the names themselves —
    so this stores `attendee_count` directly rather than a list no UI
    surface displays (same reasoning as Note's excluded `tags` and
    Ticket's excluded `description`, applied to the part of a field
    that isn't shown rather than the whole field for once, since the
    *count* clearly is shown)."""

    class EventType(models.TextChoices):
        MEETING = "meeting", "Meeting"
        CALL = "call", "Call"
        REVIEW = "review", "Review"
        DEMO = "demo", "Demo"

    customer = models.ForeignKey(
        Customer,
        related_name="calendar_events",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Set for an organization-level event. Exactly one of "
        "customer/account is set, never both — see the model's own CheckConstraint.",
    )
    account = models.ForeignKey(
        Account,
        related_name="calendar_events",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Set for an account-level event. Exactly one of "
        "customer/account is set, never both — see the model's own CheckConstraint.",
    )
    title = models.CharField(max_length=255)
    description = models.CharField(max_length=500)
    type = models.CharField(max_length=8, choices=EventType.choices)
    event_date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    attendee_count = models.PositiveIntegerField(
        default=0, help_text='Shown on the card as "N attendees" — not a list of names.'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-event_date", "start_time", "-id"]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(customer__isnull=False, account__isnull=True)
                    | models.Q(customer__isnull=True, account__isnull=False)
                ),
                name="calendarevent_belongs_to_exactly_one_parent",
            )
        ]

    def __str__(self):
        parent = self.customer or self.account
        return f"{self.title} — {parent}"


class Contact(models.Model):
    """A person at a Customer or one of its Accounts — there can be an
    organisation-level contact (this Customer's own, `customer` set)
    and, separately, each of its Accounts can have its own individual
    contacts (`account` set) — same "belongs to exactly one of Customer
    or Account" shape as Activity/Email/Task/Note/Ticket/CalendarEvent
    above (two nullable FKs + a CheckConstraint), but unlike those,
    which each back one filter *within* ActivityFeed, Contact backs its
    own sibling tab: the "Contacts" tab on the Organization Details
    page (which rolls both levels up together — see
    CustomerContactListView below), the "Contacts" tab on the
    standalone Account page (that one Account's own contacts only), and
    the global /contacts/list page (which lists every Contact across
    every Customer/Account the caller's organisation owns — see
    ContactListView below, the one List view here that isn't nested
    under a single Customer/Account).

    Mirrors the frontend's mock Contact shape
    (react-ts-app/src/components/organizations/contactsData.ts):
    `role` is the mock's own closed set of 7 MEDDIC-style stakeholder
    roles plus an `OTHER` catch-all; `status`/`sentiment` are the
    pill/dot the card already renders. `last_contacted_at` is a real
    datetime rather than the mock's frozen "2 hours ago" string — the
    frontend formats it relative-to-now itself (date-fns), so the text
    stays accurate as time passes instead of drifting stale the way a
    stored string would. `avatar` isn't stored — same as every other
    entity's avatar in this codebase (e.g. Customer/Account owners),
    derived from `name` on the frontend."""

    class Role(models.TextChoices):
        EXECUTIVE_SPONSOR = "executive_sponsor", "Executive Sponsor"
        CHAMPION = "champion", "Champion"
        ECONOMIC_BUYER = "economic_buyer", "Economic Buyer"
        TECHNICAL_LEAD = "technical_lead", "Technical Lead"
        DECISION_MAKER = "decision_maker", "Decision Maker"
        INFLUENCER = "influencer", "Influencer"
        FINANCE_MANAGER = "finance_manager", "Finance Manager"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    class Sentiment(models.TextChoices):
        POSITIVE = "positive", "Positive"
        NEUTRAL = "neutral", "Neutral"
        NEGATIVE = "negative", "Negative"

    customer = models.ForeignKey(
        Customer,
        related_name="contacts",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Set for an organization-level contact. Exactly one of "
        "customer/account is set, never both — see the model's own CheckConstraint.",
    )
    account = models.ForeignKey(
        Account,
        related_name="contacts",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Set for an account-level contact. Exactly one of "
        "customer/account is set, never both — see the model's own CheckConstraint.",
    )
    name = models.CharField(max_length=150)
    role = models.CharField(max_length=32, choices=Role.choices, default=Role.OTHER)
    email = models.EmailField()
    phone = models.CharField(max_length=32, blank=True)
    #: The language this person writes in, as a short code ("fr", "pt-br").
    #: Set by hand, or learned the first time one of their messages is
    #: translated — a detection only ever fills a blank, never overrules a
    #: person (see services.translation.translate.remember_language).
    language = models.CharField(max_length=12, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    sentiment = models.CharField(
        max_length=16, choices=Sentiment.choices, default=Sentiment.NEUTRAL
    )

    class SentimentSource(models.TextChoices):
        MANUAL = "manual", "Set by hand"
        COMPUTED = "computed", "From their calls, emails and tickets"

    # How this person actually sounds: once they have classified calls
    # (as a participant), emails from their address or tickets they raised,
    # `sentiment` is computed from those (contact_sentiment.py) and the
    # hand-set value is the fallback for a contact with no evidence yet.
    sentiment_source = models.CharField(
        max_length=16, choices=SentimentSource.choices, default=SentimentSource.MANUAL
    )
    sentiment_evidence = models.JSONField(
        default=dict,
        blank=True,
        help_text="What the computed sentiment rests on: score, counts per kind "
        "and per sentiment, and when the latest interaction was.",
    )
    sentiment_computed_at = models.DateTimeField(null=True, blank=True)
    last_contacted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Formatted as relative time ('2 hours ago') on the frontend.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "contacts"
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(customer__isnull=False, account__isnull=True)
                    | models.Q(customer__isnull=True, account__isnull=False)
                ),
                name="contact_belongs_to_exactly_one_parent",
            )
        ]

    def __str__(self):
        parent = self.customer or self.account
        return f"{self.name} — {parent}"

    @property
    def companies(self) -> list["Customer"]:
        """Every ultimate parent Customer: itself alone if this is an
        organization-level contact, else its account's own (possibly
        several, now that Account.customers is a many-to-many) ones.
        Used by ContactSerializer's company_ids/company_names fields —
        needed by the standalone /contacts/list page, which spans every
        Customer and so can't assume which FK is set the way the
        nested Customer/Account-scoped list views can. Plural (not the
        old singular `company`) since an account-level Contact's own
        Account can now belong to more than one Customer at once — see
        Account's own docstring."""
        if self.customer_id:
            return [self.customer]
        return list(self.account.customers.all())


class Opportunity(models.Model):
    """A sales opportunity moving through the pipeline board's
    "Opportunities" tab (react-ts-app's src/pages/pipelines/
    PipelinesPage.tsx) — same "belongs to exactly one of Customer or
    Account" shape as Contact/Activity/Email/... above (two nullable
    FKs + a CheckConstraint): there can be an organisation-level
    opportunity and, separately, an opportunity tied to one specific
    Account (the mock's own card list already mixed both — "Apple Inc"
    alongside "Apple EMEA" — before this model existed).

    Mirrors the frontend's mock `PipelineCard`/`Column` shape: `stage`
    is the closed set of 6 columns the board already has (Kanban
    columns, not a separate model — there's no per-tenant pipeline
    customisation asked for, so a fixed enum is enough, same reasoning
    as Task/Ticket's own status enums). `priority` is a real field —
    every mock card already had one, same as Task/Ticket. `mrr` mirrors
    Account's own ARR-family fields' `DecimalField(max_digits=12,
    decimal_places=2)` shape. The mock's own `orgColor`/`orgInitials`
    aren't stored — decorative, derived from the company name on the
    frontend the same way EntityAvatar already derives initials/color
    for every other entity in this codebase. The mock's own per-column
    `count` isn't stored either — it never actually matched
    `cards.length` in the mock (e.g. "Discovery" claimed 12 while only
    listing 3), a stale hardcoded number rather than real data; the
    frontend derives a real count from how many Opportunities it
    actually fetched per stage."""

    class Stage(models.TextChoices):
        DISCOVERY = "discovery", "Discovery"
        QUALIFICATION = "qualification", "Qualification"
        SOLUTION_VALIDATION = "solution_validation", "Solution Validation"
        PROPOSAL_PRICE_REVIEW = "proposal_price_review", "Proposal / Price Review"
        NEGOTIATION = "negotiation", "Negotiation"
        CLOSED_WON = "closed_won", "Closed Won"

    class Priority(models.TextChoices):
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"

    customer = models.ForeignKey(
        Customer,
        related_name="opportunities",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Set for an organization-level opportunity. Exactly one of "
        "customer/account is set, never both — see the model's own CheckConstraint.",
    )
    account = models.ForeignKey(
        Account,
        related_name="opportunities",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Set for an account-level opportunity. Exactly one of "
        "customer/account is set, never both — see the model's own CheckConstraint.",
    )
    title = models.CharField(max_length=255)
    mrr = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    stage = models.CharField(max_length=32, choices=Stage.choices, default=Stage.DISCOVERY)
    priority = models.CharField(max_length=8, choices=Priority.choices, default=Priority.MEDIUM)
    # Whose pipeline this is on (User.Function). Read department-wise, the
    # same rule as tickets: a Sales opportunity is Sales's, a CS risk is
    # CS's; blank is everyone's, and a role that may view all accounts
    # (or Leadership) sees every department. See scoping.pipeline_visible_q.
    department = models.CharField(max_length=16, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(customer__isnull=False, account__isnull=True)
                    | models.Q(customer__isnull=True, account__isnull=False)
                ),
                name="opportunity_belongs_to_exactly_one_parent",
            )
        ]

    def __str__(self):
        parent = self.customer or self.account
        return f"{self.title} — {parent}"

    @property
    def companies(self) -> list["Customer"]:
        """Every ultimate parent Customer — same reasoning as Contact's
        own `companies` property, needed by OpportunitySerializer's
        company_ids/company_names for the same reason."""
        if self.customer_id:
            return [self.customer]
        return list(self.account.customers.all())


class Risk(models.Model):
    """A customer-health risk tracked on the pipeline board's "Risks"
    tab (react-ts-app's src/pages/pipelines/PipelinesPage.tsx) — same
    "belongs to exactly one of Customer or Account" shape as
    Opportunity above (two nullable FKs + a CheckConstraint): there can
    be an organisation-level risk and, separately, a risk tied to one
    specific Account.

    Mirrors the frontend's mock `PipelineCard`/`Column` shape used for
    Risks: `stage` is the closed set of 4 columns the board already has
    (Open/Mitigated/Realised/Abandoned — a fixed enum, same reasoning
    as Opportunity's own `stage`). `priority` and `mrr` are real fields,
    same shape as Opportunity's. The mock's own Risk cards named
    placeholder companies ("Digital Operations", "Culinary Innovation
    Lab (HCIL)", ...) that don't exist as real Customers/Accounts in
    this codebase's seed data at all (unlike Opportunity's mock, which
    did name some real ones) — so, per seed_demo_risks.py's own
    docstring, none of that mock data could be carried over verbatim;
    the demo rows are new content against real seeded Customers/
    Accounts instead. `orgColor`/`orgInitials` aren't stored, same
    reasoning as Opportunity — derived via EntityAvatar on the
    frontend."""

    class Stage(models.TextChoices):
        OPEN = "open", "Open"
        MITIGATED = "mitigated", "Mitigated"
        REALISED = "realised", "Realised"
        ABANDONED = "abandoned", "Abandoned"

    class Priority(models.TextChoices):
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"

    customer = models.ForeignKey(
        Customer,
        related_name="risks",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Set for an organization-level risk. Exactly one of "
        "customer/account is set, never both — see the model's own CheckConstraint.",
    )
    account = models.ForeignKey(
        Account,
        related_name="risks",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Set for an account-level risk. Exactly one of "
        "customer/account is set, never both — see the model's own CheckConstraint.",
    )
    title = models.CharField(max_length=255)
    mrr = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    stage = models.CharField(max_length=16, choices=Stage.choices, default=Stage.OPEN)
    priority = models.CharField(max_length=8, choices=Priority.choices, default=Priority.MEDIUM)
    # Whose pipeline this is on (User.Function). Read department-wise, the
    # same rule as tickets: a Sales opportunity is Sales's, a CS risk is
    # CS's; blank is everyone's, and a role that may view all accounts
    # (or Leadership) sees every department. See scoping.pipeline_visible_q.
    department = models.CharField(max_length=16, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(customer__isnull=False, account__isnull=True)
                    | models.Q(customer__isnull=True, account__isnull=False)
                ),
                name="risk_belongs_to_exactly_one_parent",
            )
        ]

    def __str__(self):
        parent = self.customer or self.account
        return f"{self.title} — {parent}"

    @property
    def companies(self) -> list["Customer"]:
        """Every ultimate parent Customer — same reasoning as
        Opportunity's own `companies` property."""
        if self.customer_id:
            return [self.customer]
        return list(self.account.customers.all())


class Survey(models.Model):
    """One sent instance of asking a Customer or Account for an NPS/CSAT/
    CES score — same "belongs to exactly one of Customer or Account"
    shape as Opportunity/Risk above. Backs the Activity Feed's own
    "Surveys" filter (react-ts-app's ActivityFeed.tsx — previously an
    unimplemented chip with no data behind it at all) and the standalone
    Surveys page (src/pages/surveys/SurveysPage.tsx).

    `score` is one field for all three types rather than three separate
    columns — NPS is -100..100, CSAT/CES are 0..100, the exact same
    ranges `Customer.nps_score`/`csat_score`/`ces_percentage` already
    use (validated per-type in the serializer, not here). Responding to
    a Survey (`status` -> RESPONDED with a `score`) writes that score
    onto the parent's own matching field — see
    SurveyDetailView.perform_update on the view side — so those
    previously-provenance-free fields become "the latest completed
    survey's result" going forward, without a full response-history
    table this doesn't need yet (no multi-question surveys, no per-
    respondent records — see this app's own design notes in
    docs/API_CONTRACTS.md).

    CES has nowhere to sync on an Account — `Account` has no
    `ces_percentage` field (a real, pre-existing asymmetry with
    Customer, not new here) — so a CES Survey is only ever valid at the
    Customer level; enforced in the views that could set `account`
    (AccountSurveyListView, SurveyListView.perform_create when
    account_id is given), not here, since neither FK is required at the
    model layer the way a plain field constraint could check."""

    class SurveyType(models.TextChoices):
        NPS = "nps", "NPS"
        CSAT = "csat", "CSAT"
        CES = "ces", "CES"

    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        RESPONDED = "responded", "Responded"
        EXPIRED = "expired", "Expired"

    customer = models.ForeignKey(
        Customer,
        related_name="surveys",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Set for an organization-level survey. Exactly one of "
        "customer/account is set, never both — see the model's own CheckConstraint.",
    )
    account = models.ForeignKey(
        Account,
        related_name="surveys",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Set for an account-level survey. Exactly one of "
        "customer/account is set, never both — see the model's own CheckConstraint. "
        "Never set together with survey_type=CES — see the model's own docstring.",
    )
    survey_type = models.CharField(max_length=8, choices=SurveyType.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SENT)
    score = models.IntegerField(
        null=True,
        blank=True,
        help_text="-100..100 for NPS, 0..100 for CSAT/CES. Required once status=RESPONDED "
        "(validated in the serializer, which also range-checks it against survey_type).",
    )
    sent_at = models.DateField()
    responded_at = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-sent_at", "-id"]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(customer__isnull=False, account__isnull=True)
                    | models.Q(customer__isnull=True, account__isnull=False)
                ),
                name="survey_belongs_to_exactly_one_parent",
            )
        ]

    def __str__(self):
        parent = self.customer or self.account
        return f"{self.get_survey_type_display()} survey — {parent}"

    @property
    def companies(self) -> list["Customer"]:
        """Every ultimate parent Customer — same reasoning as
        Opportunity's own `companies` property."""
        if self.customer_id:
            return [self.customer]
        return list(self.account.customers.all())


class Canvas(models.Model):
    """A stakeholder/relationship-map board for a Customer or Account —
    same "belongs to exactly one of Customer or Account" shape as
    Opportunity/Risk/Survey above. Backs the sidebar's "Canvas" gallery
    (react-ts-app's src/pages/canvas/CanvasPage.tsx) and the "Canvas
    List" tab on both Details pages (previously two labels with nothing
    behind either).

    `nodes`/`edges` are stored verbatim exactly as React Flow gives
    them, same "the graph shape is the frontend's concern" philosophy
    as `scenarios.Scenario.nodes`/`edges` — the backend never inspects
    them. A node's own `data` holds only a `contact_id` reference, never
    a name/role/sentiment snapshot: `Contact` already carries real
    `role`/`sentiment` fields, so editing a Contact anywhere in the app
    is reflected on every Canvas it appears on, without a sync step.
    A Customer/Account can have several Canvases (e.g. "Renewal
    Strategy Q3," "Post-Reorg Map") — this is deliberately a list, not
    a one-per-company singleton."""

    customer = models.ForeignKey(
        Customer,
        related_name="canvases",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Set for an organization-level canvas. Exactly one of "
        "customer/account is set, never both — see the model's own CheckConstraint.",
    )
    account = models.ForeignKey(
        Account,
        related_name="canvases",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Set for an account-level canvas. Exactly one of "
        "customer/account is set, never both — see the model's own CheckConstraint.",
    )
    name = models.CharField(max_length=255, default="Untitled Canvas")
    nodes = models.JSONField(default=list, blank=True)
    edges = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(customer__isnull=False, account__isnull=True)
                    | models.Q(customer__isnull=True, account__isnull=False)
                ),
                name="canvas_belongs_to_exactly_one_parent",
            )
        ]

    def __str__(self):
        parent = self.customer or self.account
        return f"{self.name} — {parent}"

    @property
    def companies(self) -> list["Customer"]:
        """Every ultimate parent Customer — same reasoning as
        Opportunity/Risk/Survey's own `companies` property."""
        if self.customer_id:
            return [self.customer]
        return list(self.account.customers.all())


class Headline(models.Model):
    """An AI-written summary card on the Headlines tab — same "belongs to
    exactly one of Customer or Account" shape as Note/Survey/Canvas
    above. Backs the "Headlines" sub-tab of ActivityFeed on both the
    Organization Details page and the standalone Account page
    (react-ts-app's src/components/organizations/activity/HeadlinesTab.tsx,
    previously a hardcoded two-item HEADLINES_DATA array).

    Two card shapes, distinguished by `kind` rather than the mock's
    `isSummary` boolean — the mock only ever needed "is this the
    TL;DR?", but the two genuinely differ in which fields apply, and a
    choice field says that better than a bool:

    * SUMMARY — the pinned TL;DR at the top. Carries
      `time_period_label` ("Last 3 months") and `data_sources`, which
      the card renders in its footer. Never has a `status` — there's
      nothing open or closed about a rolling summary — enforced by
      `headline_summary_has_no_status` below.
    * HEADLINE — one themed storyline in the feed, with a `status` and
      a `period_start`/`period_end` span shown on the card.

    `data_sources` is a list of `DataSource` values, not the mock's
    free-text "Notes, Emails, Call Transcripts and Tickets" string.
    The string was decorative — nothing computed it — whereas the
    generator (see services/customers/headline_generation.py) actually
    knows which of the account's records it read, so this records that
    honestly and the serializer renders the display string from it.
    A hand-written Headline can leave it empty and the footer line
    simply doesn't render, same "only when non-empty" rule as Note's
    own `links` count.

    No `group` field, though the mock carried one ('January'): the
    card's group pill is derived from `period_end` at render time,
    same as Note/Activity/Email/Task's own date-group headers. The
    mock's `orgId` is likewise gone — the parent is the FK, and its
    absence is what made every account show Apple's headlines.

    `generated_at` is set only when a Headline came from the model, so
    a regenerate can replace what it wrote previously without touching
    anything a CSM typed by hand — see HeadlineGenerateView."""

    class Kind(models.TextChoices):
        SUMMARY = "summary", "Summary"
        HEADLINE = "headline", "Headline"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In Progress"
        CLOSED = "closed", "Closed"

    class DataSource(models.TextChoices):
        """The record types the generator can read. Deliberately a
        closed set matching real models in this app (plus
        CALL_TRANSCRIPTS, which CallSense will own) — an open free-text
        field would let the card claim a source that was never read."""

        NOTES = "notes", "Notes"
        EMAILS = "emails", "Emails"
        CALL_TRANSCRIPTS = "call_transcripts", "Call Transcripts"
        TICKETS = "tickets", "Tickets"
        ACTIVITIES = "activities", "Activities"

    customer = models.ForeignKey(
        Customer,
        related_name="headlines",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Set for an organization-level headline. Exactly one of "
        "customer/account is set, never both — see the model's own CheckConstraint.",
    )
    account = models.ForeignKey(
        Account,
        related_name="headlines",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Set for an account-level headline. Exactly one of "
        "customer/account is set, never both — see the model's own CheckConstraint.",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.HEADLINE)
    title = models.CharField(max_length=255)
    content = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        blank=True,
        help_text="HEADLINE cards only — always blank for a SUMMARY, "
        "enforced by the model's own CheckConstraint.",
    )
    period_start = models.DateField(
        null=True, blank=True, help_text="Start of the span the card covers."
    )
    period_end = models.DateField(
        null=True,
        blank=True,
        help_text="End of the span the card covers. Also what the card's "
        "group pill is derived from, so a HEADLINE meant to appear under a "
        "group heading needs this set.",
    )
    time_period_label = models.CharField(
        max_length=100,
        blank=True,
        help_text="SUMMARY cards only — the rolling window in words "
        "('Last 3 months'), which no pair of dates conveys on its own.",
    )
    data_sources = models.JSONField(
        default=list,
        blank=True,
        help_text="List of DataSource values actually read to write this card. "
        "Rendered in the footer only when non-empty.",
    )
    generated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when written by the model, null when hand-written — "
        "regeneration only replaces its own previous output.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Grouped by kind (so "headline" before "summary", alphabetically)
        # and newest-first within each, same "-<date field>, -id" shape as
        # Note/Activity/Email. Which kind comes first is not a contract:
        # the tab splits the list on `kind` and renders the TL;DR above
        # the feed regardless of the order it arrived in.
        ordering = ["kind", "-period_end", "-id"]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(customer__isnull=False, account__isnull=True)
                    | models.Q(customer__isnull=True, account__isnull=False)
                ),
                name="headline_belongs_to_exactly_one_parent",
            ),
            models.CheckConstraint(
                check=~models.Q(kind="summary") | models.Q(status=""),
                name="headline_summary_has_no_status",
            ),
        ]

    def __str__(self):
        parent = self.customer or self.account
        return f"{self.title} — {parent}"

    @property
    def companies(self) -> list["Customer"]:
        """Every ultimate parent Customer — same reasoning as
        Opportunity/Risk/Survey/Canvas's own `companies` property."""
        if self.customer_id:
            return [self.customer]
        return list(self.account.customers.all())


#: Where an account's conversations carry a sentiment, and the date to window them by.
SENTIMENT_SOURCES = ((Email, "sent_at"), (Ticket, "opened_at"), (Call, "occurred_at"))


def _is_datetime(model, field):
    return isinstance(model._meta.get_field(field), models.DateTimeField)


def with_pulse_inputs(queryset):
    """Annotate an Account queryset with the related-row figures pulse.py
    needs: newest contact on the account, open tickets, and the last 30
    days' positive / negative / all conversations. Correlated subqueries,
    for the same reason as `with_health_inputs`. `Account.pulse_inputs`
    picks them up when present."""
    from datetime import timedelta

    from django.utils import timezone

    from . import pulse as pulse_rules
    from .contact import last_account_contact_annotation

    since = timezone.localdate() - timedelta(days=pulse_rules.SENTIMENT_WINDOW_DAYS)

    def count(model, field, **extra):
        lookup = f"{field}__date__gte" if _is_datetime(model, field) else f"{field}__gte"
        rows = (
            model.objects.filter(account=OuterRef("pk"), **{lookup: since}, **extra)
            .order_by()
            .values("account")
            .annotate(value=models.Count("id"))
            .values("value")[:1]
        )
        return Coalesce(Subquery(rows, output_field=models.IntegerField()), 0)

    open_tickets = (
        Ticket.objects.filter(account=OuterRef("pk"))
        .exclude(status__in=Ticket.RESOLVED_STATUSES)
        .order_by()
        .values("account")
        .annotate(value=models.Count("id"))
        .values("value")[:1]
    )
    positive = sum(
        (
            count(model, field, sentiment=taxonomy.Sentiment.POSITIVE)
            for model, field in SENTIMENT_SOURCES
        ),
        Value(0),
    )
    negative = sum(
        (
            count(model, field, sentiment=taxonomy.Sentiment.NEGATIVE)
            for model, field in SENTIMENT_SOURCES
        ),
        Value(0),
    )
    classified = sum((count(model, field) for model, field in SENTIMENT_SOURCES), Value(0))
    return queryset.annotate(
        _last_touch_on=last_account_contact_annotation(),
        _open_ticket_count=Coalesce(Subquery(open_tickets, output_field=models.IntegerField()), 0),
        _positive_count=positive,
        _negative_count=negative,
        _classified_count=classified,
    )


def with_customer_pulse_inputs(queryset):
    """Annotate a Customer queryset with the pulse figures that count the
    organisation's own rows and its accounts' together: open tickets and
    the last 30 days' positive / negative / all conversations. The last
    touch comes from `with_health_inputs` (already parent-wide). Grouped on
    a constant rather than a parent column, because the parent is either
    the customer or one of its accounts."""
    from datetime import timedelta

    from django.db.models import Q
    from django.utils import timezone

    from . import pulse as pulse_rules

    since = timezone.localdate() - timedelta(days=pulse_rules.SENTIMENT_WINDOW_DAYS)
    parent = Q(customer=OuterRef("pk")) | Q(account__customers=OuterRef("pk"))

    def count(rows):
        rows = (
            rows.order_by()
            .annotate(_k=Value(1))
            .values("_k")
            .annotate(value=models.Count("id", distinct=True))
            .values("value")[:1]
        )
        return Coalesce(Subquery(rows, output_field=models.IntegerField()), 0)

    def windowed(model, field, **extra):
        lookup = f"{field}__date__gte" if _is_datetime(model, field) else f"{field}__gte"
        return count(model.objects.filter(parent, **{lookup: since}, **extra))

    return queryset.annotate(
        _pulse_open_ticket_count=count(
            Ticket.objects.filter(parent).exclude(status__in=Ticket.RESOLVED_STATUSES)
        ),
        _positive_count=sum(
            (windowed(m, f, sentiment=taxonomy.Sentiment.POSITIVE) for m, f in SENTIMENT_SOURCES),
            Value(0),
        ),
        _negative_count=sum(
            (windowed(m, f, sentiment=taxonomy.Sentiment.NEGATIVE) for m, f in SENTIMENT_SOURCES),
            Value(0),
        ),
        _classified_count=sum((windowed(m, f) for m, f in SENTIMENT_SOURCES), Value(0)),
    )
