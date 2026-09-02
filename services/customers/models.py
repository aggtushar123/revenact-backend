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

    Add/Edit Account is wired (AccountListCreateView/AccountDetailView) —
    see those views' docstrings for exactly which fields the UI sends."""

    customer = models.ForeignKey(Customer, related_name="accounts", on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    domain = models.CharField(
        max_length=255,
        blank=True,
        help_text="Falls back to the parent customer's domain (for the logo) when blank.",
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
        return f"{self.name} ({self.customer.name})"


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
    links = models.PositiveIntegerField(
        default=0, help_text="Count shown on the card's link icon."
    )
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
    from before this model existed."""

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
    subject = models.CharField(max_length=255)
    sender_name = models.CharField(max_length=150)
    recipient_name = models.CharField(max_length=150)
    body = models.TextField(help_text="The summarized preview shown on the card.")
    sent_at = models.DateTimeField()
    links = models.PositiveIntegerField(
        default=0, help_text="Count shown on the card's link icon."
    )
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
