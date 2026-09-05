from django.utils import timezone
from rest_framework import serializers

from services.accounts.models import User
from services.accounts.serializers import UserSerializer

from .models import (
    Account,
    Activity,
    CalendarEvent,
    Contact,
    Customer,
    Email,
    Note,
    Opportunity,
    Risk,
    Survey,
    Task,
    Ticket,
)


class CustomerSerializer(serializers.ModelSerializer):
    """Read: owner/created_by/modified_by nested (id/name/avatar/role/...).
    Write: owner_id, validated against the caller's own organisation in
    the view (a Customer can't be assigned to a CSM from a different
    tenant). created_by/modified_by are never client-settable — the view
    sets them from request.user."""

    health_category = serializers.ChoiceField(
        choices=Customer.HealthCategory.choices, read_only=True
    )
    seat_utilization_percentage = serializers.FloatField(read_only=True)
    currency_display = serializers.CharField(source="get_currency_display", read_only=True)

    owner = UserSerializer(read_only=True)
    owner_id = serializers.PrimaryKeyRelatedField(
        source="owner",
        queryset=User.objects.all(),
        write_only=True,
        required=False,
        allow_null=True,
    )
    created_by = UserSerializer(read_only=True)
    modified_by = UserSerializer(read_only=True)

    class Meta:
        model = Customer
        fields = [
            "id",
            "name",
            "address",
            "domain",
            "email",
            "phone",
            "owner",
            "owner_id",
            "created_by",
            "modified_by",
            "created_at",
            "updated_at",
            "lifecycle_stage",
            "health_score",
            "health_category",
            "pulse",
            "ai_pulse_score",
            "ai_pulse_reason",
            "nps_score",
            "csat_score",
            "joined_date",
            "renewal_date",
            "contract_start_date",
            "contract_end_date",
            "currency",
            "currency_display",
            "arr_billed_at_account",
            "arr_billed_at_hq",
            "implementation_fee",
            "total_contract_value",
            "total_forecasted_renewal_revenue",
            "primary_product",
            "additional_products_count",
            "top_source_channel",
            "total_contracted_seats",
            "total_active_seats",
            "seat_utilization_percentage",
            "total_hires",
            "scope_web_app",
            "ces_percentage",
            "churn_date",
            "churn_reason",
            "churn_comment",
            "is_archived",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def validate_owner_id(self, owner):
        request = self.context["request"]
        if owner is not None and owner.organisation_id != request.user.organisation_id:
            raise serializers.ValidationError("Owner must be a member of your own organisation.")
        return owner

    def create(self, validated_data):
        request = self.context["request"]
        validated_data["organisation"] = request.user.organisation
        validated_data["created_by"] = request.user
        validated_data["modified_by"] = request.user
        # A new customer defaults to the org's own currency unless the
        # caller explicitly picks a different one (e.g. a US-HQ org
        # billing this particular customer in EUR) — same "real default,
        # still overridable" shape as Organisation.default_lifecycle_stage.
        validated_data.setdefault("currency", request.user.organisation.currency)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data["modified_by"] = self.context["request"].user
        return super().update(instance, validated_data)


class AccountSerializer(serializers.ModelSerializer):
    """Shaped to mirror CustomerSerializer's own conventions (nested
    owner, derived health_category, an `owner_id` write field validated
    same-organisation-only) since an account's health/lifecycle mean the
    same thing as a customer's, just at a finer grain.

    `customers` (read) is every linked Customer as `{id, name}` — plural
    now that Account.customers is a many-to-many (see that model's own
    docstring for why). `customer_ids` (write-only) fully replaces the
    linked set on save when given at all — every id must belong to the
    caller's own organisation (validate_customer_ids below), the same
    invariant AccountListCreateView/AccountDetailView's own views rely
    on for their `customer_id`-in-the-URL scoping to mean anything.
    Optional on write: the nested Add-Account endpoints
    (AccountListCreateView.perform_create) still set the URL's own
    customer_id programmatically without the client sending
    `customer_ids` at all; it exists here for adding/removing
    *additional* linked organisations afterward (the standalone Account
    page's own Organizations tab)."""

    health_category = serializers.ChoiceField(
        choices=Customer.HealthCategory.choices, read_only=True
    )
    owner = UserSerializer(read_only=True)
    owner_id = serializers.PrimaryKeyRelatedField(
        source="owner",
        queryset=User.objects.all(),
        write_only=True,
        required=False,
        allow_null=True,
    )
    customers = serializers.SerializerMethodField()
    customer_ids = serializers.PrimaryKeyRelatedField(
        source="customers",
        queryset=Customer.objects.all(),
        many=True,
        write_only=True,
        required=False,
    )

    class Meta:
        model = Account
        fields = [
            "id",
            "customers",
            "customer_ids",
            "name",
            "domain",
            "address",
            "email",
            "phone",
            "owner",
            "owner_id",
            "created_at",
            "updated_at",
            "lifecycle_stage",
            "health_score",
            "health_category",
            "pulse",
            "ai_pulse_score",
            "ai_pulse_reason",
            "nps_score",
            "csat_score",
            "renewal_date",
            "arr",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def get_customers(self, obj):
        return [{"id": c.id, "name": c.name} for c in obj.customers.all()]

    def validate_owner_id(self, owner):
        request = self.context["request"]
        if owner is not None and owner.organisation_id != request.user.organisation_id:
            raise serializers.ValidationError("Owner must be a member of your own organisation.")
        return owner

    def validate_customer_ids(self, customers):
        if not customers:
            raise serializers.ValidationError(
                "An account must belong to at least one organization."
            )
        request = self.context["request"]
        outside = [c for c in customers if c.organisation_id != request.user.organisation_id]
        if outside:
            raise serializers.ValidationError(
                "Every linked organization must be in your own organisation."
            )
        return customers


class ActivitySerializer(serializers.ModelSerializer):
    """Read-only — see Activity model's docstring. `type_display` is the
    card's title text (the human label, e.g. "Health Check Review");
    `type` itself (the enum value) is included too in case a future
    frontend pass wants to key off it (icon/color per type, filtering)."""

    type_display = serializers.CharField(source="get_type_display", read_only=True)

    class Meta:
        model = Activity
        fields = ["id", "type", "type_display", "occurred_at", "links", "watchers"]


class EmailSerializer(serializers.ModelSerializer):
    """Read-only — see Email model's docstring."""

    class Meta:
        model = Email
        fields = [
            "id",
            "subject",
            "sender_name",
            "recipient_name",
            "body",
            "sent_at",
            "links",
            "watchers",
            "is_starred",
        ]


class TaskSerializer(serializers.ModelSerializer):
    """Read-only — see Task model's docstring. No "group" field —
    the frontend derives the Overdue/This Week/Next Week/Later bucket
    from `due_date` at render time."""

    class Meta:
        model = Task
        fields = ["id", "title", "assignee_name", "due_date", "priority", "status"]


class NoteSerializer(serializers.ModelSerializer):
    """Read-only — see Note model's docstring."""

    class Meta:
        model = Note
        fields = ["id", "title", "author_name", "body", "logged_at", "links"]


class TicketSerializer(serializers.ModelSerializer):
    """Read-only — see Ticket model's docstring."""

    class Meta:
        model = Ticket
        fields = [
            "id",
            "ticket_number",
            "title",
            "assignee_name",
            "status",
            "priority",
            "opened_at",
            "links",
        ]


class CalendarEventSerializer(serializers.ModelSerializer):
    """Read-only — see CalendarEvent model's docstring."""

    class Meta:
        model = CalendarEvent
        fields = [
            "id",
            "title",
            "description",
            "type",
            "event_date",
            "start_time",
            "end_time",
            "attendee_count",
        ]


class ContactSerializer(serializers.ModelSerializer):
    """Read-only — see Contact model's docstring. `companies` is every
    ultimate parent Customer regardless of whether this is an
    organization- or account-level contact (see Contact.companies) —
    plural (not the old singular `company_id`/`company_name`) since an
    account-level contact's own Account can now belong to more than one
    Customer at once (see Account's own docstring). The nested Customer/
    Account-scoped list views below don't strictly need this (the page
    already knows its own scope) but get it for free since it's the
    same serializer; the standalone top-level ContactListView does need
    it, since it spans every Customer. `account_name` is set only for
    an account-level contact, so the standalone page can show which
    account within the company it belongs to (a plain
    SerializerMethodField rather than `source="account.name"`, since a
    dotted source would raise on a null `account` rather than reliably
    falling back)."""

    role_display = serializers.CharField(source="get_role_display", read_only=True)
    companies = serializers.SerializerMethodField()
    account_name = serializers.SerializerMethodField()

    class Meta:
        model = Contact
        fields = [
            "id",
            "name",
            "role",
            "role_display",
            "email",
            "phone",
            "status",
            "sentiment",
            "last_contacted_at",
            "companies",
            "account_name",
        ]

    def get_companies(self, obj):
        return [{"id": c.id, "name": c.name} for c in obj.companies]

    def get_account_name(self, obj):
        return obj.account.name if obj.account_id else None


class OpportunitySerializer(serializers.ModelSerializer):
    """See Opportunity model's docstring. `companies`/`account_name`
    mirror ContactSerializer's own fields exactly, same reasoning (the
    standalone Pipelines board spans every Customer, so it can't assume
    which parent FK is set the way a nested Customer/Account-scoped
    view can). Plural `companies` (not the old singular `company_id`/
    `company_name`) for the same reason as ContactSerializer's own —
    an account-level Opportunity's own Account can now belong to more
    than one Customer at once. `stage_display`/`priority_display` are
    the human labels ("Solution Validation", not "solution_validation")
    the board's own column headers/priority pills render; `stage`/
    `priority` themselves are included too since the frontend keys
    drag-and-drop and filtering off the raw value."""

    stage_display = serializers.CharField(source="get_stage_display", read_only=True)
    priority_display = serializers.CharField(source="get_priority_display", read_only=True)
    companies = serializers.SerializerMethodField()
    account_name = serializers.SerializerMethodField()

    class Meta:
        model = Opportunity
        fields = [
            "id",
            "title",
            "mrr",
            "stage",
            "stage_display",
            "priority",
            "priority_display",
            "companies",
            "account_name",
        ]

    def get_companies(self, obj):
        return [{"id": c.id, "name": c.name} for c in obj.companies]

    def get_account_name(self, obj):
        return obj.account.name if obj.account_id else None


class RiskSerializer(serializers.ModelSerializer):
    """See Risk model's docstring. Field-for-field identical shape to
    OpportunitySerializer, same reasoning — the standalone Pipelines
    board's "Risks" tab spans every Customer/Account the same way its
    "Opportunities" tab does. Plural `companies` for the same reason as
    OpportunitySerializer's own."""

    stage_display = serializers.CharField(source="get_stage_display", read_only=True)
    priority_display = serializers.CharField(source="get_priority_display", read_only=True)
    companies = serializers.SerializerMethodField()
    account_name = serializers.SerializerMethodField()

    class Meta:
        model = Risk
        fields = [
            "id",
            "title",
            "mrr",
            "stage",
            "stage_display",
            "priority",
            "priority_display",
            "companies",
            "account_name",
        ]

    def get_companies(self, obj):
        return [{"id": c.id, "name": c.name} for c in obj.companies]

    def get_account_name(self, obj):
        return obj.account.name if obj.account_id else None


class SurveySerializer(serializers.ModelSerializer):
    """See Survey model's docstring. `companies`/`account_name` mirror
    Opportunity/RiskSerializer's own fields exactly, same reasoning —
    the standalone Surveys page spans every Customer/Account the same
    way the Pipelines board does. Unlike Opportunity/Risk, this also
    exposes `account_id` (a plain passthrough of the FK, not a
    SerializerMethodField) — Opportunity/Risk rows never navigate
    anywhere on click, but the standalone Surveys page's own row-click
    does (into that Account's own Details page), and `account_name`
    alone isn't enough to build that link.

    `score` is required, and range-checked against `survey_type`, the
    moment `status` becomes RESPONDED — not enforced at any other time,
    so a `sent` survey can be created (and stay) with no score at all."""

    survey_type_display = serializers.CharField(source="get_survey_type_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    companies = serializers.SerializerMethodField()
    account_name = serializers.SerializerMethodField()

    class Meta:
        model = Survey
        fields = [
            "id",
            "survey_type",
            "survey_type_display",
            "status",
            "status_display",
            "score",
            "sent_at",
            "responded_at",
            "companies",
            "account_id",
            "account_name",
            "created_at",
        ]
        read_only_fields = ["created_at", "account_id"]

    def get_companies(self, obj):
        return [{"id": c.id, "name": c.name} for c in obj.companies]

    def get_account_name(self, obj):
        return obj.account.name if obj.account_id else None

    def validate(self, attrs):
        # `status`/`survey_type` may come from `attrs` (this call) or
        # already be on `self.instance` (a PATCH that only sends
        # `score`, e.g.) — same "fall back to the existing instance"
        # reasoning any partial-update validator needs.
        status = attrs.get("status", getattr(self.instance, "status", None))
        survey_type = attrs.get("survey_type", getattr(self.instance, "survey_type", None))
        score = attrs.get("score", getattr(self.instance, "score", None))

        if status == Survey.Status.RESPONDED:
            if score is None:
                raise serializers.ValidationError(
                    {"score": "A score is required once a survey is marked responded."}
                )
            lo, hi = (-100, 100) if survey_type == Survey.SurveyType.NPS else (0, 100)
            if not (lo <= score <= hi):
                raise serializers.ValidationError(
                    {"score": f"Must be between {lo} and {hi} for {survey_type.upper()}."}
                )
        return attrs

    def update(self, instance, validated_data):
        # One less date for a CSM to pick — "I got a response today" is
        # the overwhelmingly common case; still overridable by sending
        # responded_at explicitly (e.g. logging a response that came in
        # yesterday).
        if validated_data.get("status") == Survey.Status.RESPONDED and not validated_data.get(
            "responded_at"
        ):
            validated_data["responded_at"] = timezone.localdate()
        return super().update(instance, validated_data)
