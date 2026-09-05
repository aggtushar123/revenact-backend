from django.conf import settings
from django.db import models

from services.accounts.models import Organisation


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

    # --- Identity, ownership, provenance -------------------------------------

    organisation = models.ForeignKey(
        "accounts.Organisation", related_name="customers", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255, blank=True, help_text='"Name / Address" column.')
    domain = models.CharField(max_length=255, blank=True)
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

    `domain`/`address`/`email`/`phone` all fall back to the parent
    Customer's own value when blank — the frontend's own
    mapAccountToAccountRow.ts does the falling back (same as it
    already did for `domain` alone before `address`/`email`/`phone`
    existed here), not this model or its serializer; a sub-account
    with no info of its own is assumed to share its parent's contact
    details rather than have none, matching ActivityFeed's Overview
    tab for the standalone Account page.

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
    ai_pulse_score = models.CharField(
        max_length=20, choices=Customer.AIPulseScore.choices, blank=True
    )
    ai_pulse_reason = models.TextField(blank=True)
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
        for threshold, category in Customer.HEALTH_THRESHOLDS:
            if self.health_score >= threshold:
                return category
        return Customer.HealthCategory.POOR

    class Meta:
        ordering = ["name"]

    def __str__(self):
        names = ", ".join(self.customers.values_list("name", flat=True)) or "no organisation"
        return f"{self.name} ({names})"


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


class Email(models.Model):
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
            )
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
    due_date = models.DateField()
    priority = models.CharField(max_length=8, choices=Priority.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
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


class Ticket(models.Model):
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
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"

    class Priority(models.TextChoices):
        CRITICAL = "critical", "Critical"
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"

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
    opened_at = models.DateField()
    links = models.PositiveIntegerField(
        default=0, help_text="Count shown on the card's link line — only rendered when > 0."
    )
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
            )
        ]

    def __str__(self):
        parent = self.customer or self.account
        return f"{self.ticket_number} {self.title} — {parent}"


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
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    sentiment = models.CharField(
        max_length=16, choices=Sentiment.choices, default=Sentiment.NEUTRAL
    )
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
