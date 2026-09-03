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
        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data["modified_by"] = self.context["request"].user
        return super().update(instance, validated_data)


class AccountSerializer(serializers.ModelSerializer):
    """Shaped to mirror CustomerSerializer's own conventions (nested
    owner, derived health_category, an `owner_id` write field validated
    same-organisation-only) since an account's health/lifecycle mean the
    same thing as a customer's, just at a finer grain.

    `customer` is read-only here — never client-supplied. AccountListCreateView
    sets it from the URL's customer_id on create; there's no way to move
    an account to a different customer via this serializer."""

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

    class Meta:
        model = Account
        fields = [
            "id",
            "customer",
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
        read_only_fields = ["customer", "created_at", "updated_at"]

    def validate_owner_id(self, owner):
        request = self.context["request"]
        if owner is not None and owner.organisation_id != request.user.organisation_id:
            raise serializers.ValidationError("Owner must be a member of your own organisation.")
        return owner


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
    """Read-only — see Contact model's docstring. `company_id`/
    `company_name` are the ultimate parent Customer regardless of
    whether this is an organization- or account-level contact (see
    Contact.company) — the nested Customer/Account-scoped list views
    below don't strictly need them (the page already knows its own
    scope) but get them for free since it's the same serializer; the
    standalone top-level ContactListView does need them, since it spans
    every Customer. `account_name` is set only for an account-level
    contact, so the standalone page can show which account within the
    company it belongs to (a plain SerializerMethodField rather than
    `source="account.name"`, since a dotted source would raise on a
    null `account` rather than reliably falling back)."""

    role_display = serializers.CharField(source="get_role_display", read_only=True)
    company_id = serializers.SerializerMethodField()
    company_name = serializers.SerializerMethodField()
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
            "company_id",
            "company_name",
            "account_name",
        ]

    def get_company_id(self, obj):
        return obj.company.id

    def get_company_name(self, obj):
        return obj.company.name

    def get_account_name(self, obj):
        return obj.account.name if obj.account_id else None


class OpportunitySerializer(serializers.ModelSerializer):
    """See Opportunity model's docstring. `company_id`/`company_name`/
    `account_name` mirror ContactSerializer's own fields exactly, same
    reasoning (the standalone Pipelines board spans every Customer, so
    it can't assume which parent FK is set the way a nested
    Customer/Account-scoped view can). `stage_display`/`priority_display`
    are the human labels ("Solution Validation", not
    "solution_validation") the board's own column headers/priority
    pills render; `stage`/`priority` themselves are included too since
    the frontend keys drag-and-drop and filtering off the raw value."""

    stage_display = serializers.CharField(source="get_stage_display", read_only=True)
    priority_display = serializers.CharField(source="get_priority_display", read_only=True)
    company_id = serializers.SerializerMethodField()
    company_name = serializers.SerializerMethodField()
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
            "company_id",
            "company_name",
            "account_name",
        ]

    def get_company_id(self, obj):
        return obj.company.id

    def get_company_name(self, obj):
        return obj.company.name

    def get_account_name(self, obj):
        return obj.account.name if obj.account_id else None


class RiskSerializer(serializers.ModelSerializer):
    """See Risk model's docstring. Field-for-field identical shape to
    OpportunitySerializer, same reasoning — the standalone Pipelines
    board's "Risks" tab spans every Customer/Account the same way its
    "Opportunities" tab does."""

    stage_display = serializers.CharField(source="get_stage_display", read_only=True)
    priority_display = serializers.CharField(source="get_priority_display", read_only=True)
    company_id = serializers.SerializerMethodField()
    company_name = serializers.SerializerMethodField()
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
            "company_id",
            "company_name",
            "account_name",
        ]

    def get_company_id(self, obj):
        return obj.company.id

    def get_company_name(self, obj):
        return obj.company.name

    def get_account_name(self, obj):
        return obj.account.name if obj.account_id else None
