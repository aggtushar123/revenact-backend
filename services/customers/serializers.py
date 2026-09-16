from django.utils import timezone
from rest_framework import serializers

from services.accounts.models import User
from services.accounts.serializers import UserSerializer
from services.fx_rates.conversion import convert_to_org_currency

from . import churn
from .models import (
    Account,
    Activity,
    Attachment,
    CalendarEvent,
    Call,
    Canvas,
    Contact,
    Customer,
    Email,
    Headline,
    HealthSnapshot,
    Note,
    Opportunity,
    Product,
    Risk,
    Survey,
    Task,
    Ticket,
    ai_pulse_category,
)

# Reverse of Customer.AI_PULSE_THRESHOLDS: the value a category is written as
# when a client sends the category instead of the number. Each one round-trips
# back to the same category through ai_pulse_category().
AI_PULSE_CATEGORY_VALUES = {
    Customer.AIPulseScore.VERY_SATISFIED: 5,
    Customer.AIPulseScore.SATISFIED: 4,
    Customer.AIPulseScore.MODERATE: 3,
    Customer.AIPulseScore.HIGH_RISK: 1,
}


class AIPulseScoreField(serializers.Field):
    """The AI pulse as its category string, backed by the stored number.

    `ai_pulse_score` used to be its own column and is part of the shipped API —
    the frontend's AI_PULSE_LABELS reads it and clients POST it. The number is
    what's stored now (see Customer.ai_pulse_value), so this keeps the old name
    working in both directions rather than breaking callers: it renders the
    category on read, and on write records the value that category stands for.

    Declared with `source="*"` so the whole instance reaches
    `to_representation`. Pointing it straight at `ai_pulse_value` looks simpler
    but renders an unscored row as `null`, because DRF short-circuits a None
    attribute before the field is ever consulted — and the column this replaced
    was blank-not-null, which is what the frontend's own type still says.

    For the same reason it is not `allow_null`: with `source="*"` a null would
    reach `set_value` as the whole validated dict. An empty string clears the
    score, exactly as it did when this was a blank CharField.
    """

    default_error_messages = {
        "invalid_choice": "'{input}' is not a valid AI pulse score.",
    }

    def to_representation(self, instance):
        return ai_pulse_category(instance.ai_pulse_value)

    def to_internal_value(self, data):
        if data == "":
            return {"ai_pulse_value": None}
        if data not in AI_PULSE_CATEGORY_VALUES:
            self.fail("invalid_choice", input=data)
        return {"ai_pulse_value": AI_PULSE_CATEGORY_VALUES[data]}


class HealthScoreField(serializers.Field):
    """`health_score` as the number in force, writing through to the override.

    Reading returns whatever the customer's score currently is — the rubric's
    own figure, or the pinned one when somebody has overridden it. Writing pins
    it: a client PATCHing `health_score` is saying "I disagree with the
    calculation", which is exactly what `health_score_override` records.

    `source="*"` for the same reason as AIPulseScoreField — and because read and
    write land on two different attributes here.
    """

    #: Bounds live on a real DecimalField so the range is enforced by the same
    #: machinery as everywhere else, rather than a hand-rolled comparison.
    _override = serializers.DecimalField(max_digits=3, decimal_places=1, min_value=0, max_value=10)

    def validate_empty_values(self, data):
        """Accept an explicit null as "clear the override".

        Handled here rather than via `allow_null`: with `source="*"` a null
        validated value reaches `set_value` as the whole validated dict and
        blows up, so the null has to become the dict right here.
        """
        if data is None or data == "":
            return True, {"health_score_override": None}
        return super().validate_empty_values(data)

    def to_representation(self, instance):
        return str(instance.health_score)

    def to_internal_value(self, data):
        # run_validation, not to_internal_value: min_value/max_value are
        # validators, and to_internal_value alone would parse "11.0" happily.
        return {"health_score_override": self._override.run_validation(data)}


class HealthBreakdownField(serializers.Field):
    """The five components behind `health_score`, as the popover renders them.

    Read-only. `points`/`weight` are strings for the same reason DRF renders
    DecimalField that way — these are exact tenths, not floats to be summed in
    JavaScript. `available` false means the component had nothing to measure
    and was left out of the score rather than scored zero.
    """

    def __init__(self, **kwargs):
        kwargs["read_only"] = True
        kwargs.setdefault("source", "*")
        super().__init__(**kwargs)

    def to_representation(self, instance):
        return [
            {
                "key": component.key,
                "label": component.label,
                "weight": str(component.weight),
                "points": str(component.points),
                "ratio": component.ratio,
                "available": component.available,
            }
            for component in instance.health_breakdown
        ]


class HealthRecalculationMixin:
    """Refresh `health_score` from the rubric after any write.

    Here rather than in `Model.save()` so an ordinary save stays one query:
    the calculation reads two related-row figures, and most writes to a
    Customer have nothing to do with its health.
    """

    def create(self, validated_data):
        instance = super().create(validated_data)
        instance.recalculate_health()
        return instance

    def update(self, instance, validated_data):
        # The handover note is for the record, not the row (see the view).
        self.context["handover_note"] = validated_data.pop("handover_note", "")

        override_changing = (
            "health_score_override" in validated_data
            and validated_data["health_score_override"] != instance.health_score_override
        )
        score_before = instance.health_score
        instance = super().update(instance, validated_data)
        instance.recalculate_health()
        if override_changing and hasattr(instance, "organisation"):
            # A person overriding the rubric is a correction of it, and the
            # feedback log is where corrections live.
            from services.metrics.feedback import record_health_override

            record_health_override(
                instance,
                score_before,
                instance.health_score_override,
                self.context["request"].user,
            )
        return instance


class PulseWritesMixin:
    """Shared handling for the two ways one pulse can arrive, and for stamping
    the CSM's own edit time.

    `ai_pulse_score` (category) and `ai_pulse_value` (number) both write the
    same column, so a request carrying both is ambiguous and is rejected rather
    than silently resolved. `csm_pulse_modified_at` is stamped here rather than
    by the model, so it tracks the pulse changing and not any other edit.
    """

    def validate(self, attrs):
        attrs = super().validate(attrs)
        initial = getattr(self, "initial_data", {}) or {}
        if "ai_pulse_score" in initial and "ai_pulse_value" in initial:
            raise serializers.ValidationError(
                {
                    "ai_pulse_score": "Send either ai_pulse_score or ai_pulse_value, not both — "
                    "they write the same value."
                }
            )
        return attrs

    def _stamp_csm_pulse(self, validated_data, instance=None):
        if "csm_pulse_score" not in validated_data:
            return validated_data
        unchanged = (
            instance is not None and instance.csm_pulse_score == validated_data["csm_pulse_score"]
        )
        if not unchanged:
            validated_data["csm_pulse_modified_at"] = timezone.now()
        return validated_data

    def create(self, validated_data):
        return super().create(self._stamp_csm_pulse(validated_data))

    def update(self, instance, validated_data):
        return super().update(instance, self._stamp_csm_pulse(validated_data, instance))


class CustomerSerializer(HealthRecalculationMixin, PulseWritesMixin, serializers.ModelSerializer):
    """Read: owner/created_by/modified_by nested (id/name/avatar/role/...).
    Write: owner_id, validated against the caller's own organisation in
    the view (a Customer can't be assigned to a CSM from a different
    tenant). created_by/modified_by are never client-settable — the view
    sets them from request.user."""

    health_category = serializers.ChoiceField(
        choices=Customer.HealthCategory.choices, read_only=True
    )
    health_score = HealthScoreField(source="*", required=False)
    health_score_is_overridden = serializers.BooleanField(read_only=True)
    account_pulse = serializers.SerializerMethodField(
        help_text="How the relationship feels right now — pulse.py over the organisation's "
        "own signals plus everything logged on its accounts."
    )
    health_breakdown = HealthBreakdownField()
    csat_breakdown = serializers.JSONField(read_only=True)
    ai_pulse_score = AIPulseScoreField(source="*", required=False)
    seat_utilization_percentage = serializers.FloatField(read_only=True)
    currency_display = serializers.CharField(source="get_currency_display", read_only=True)
    # The label beside the stored value, so screens don't each keep their own
    # copy of the taxonomy and drift from it. Blank stays blank: "nobody
    # recorded why" is not a reason.
    churn_reason_display = serializers.CharField(source="get_churn_reason_display", read_only=True)
    # Products are the tenant's own rows, so this is an id on write and a name
    # on read — a screen that only wants to print what they bought shouldn't
    # have to fetch the product list to find out. Validated against the
    # caller's organisation in validate_primary_product, the same shape as
    # owner_id: a PrimaryKeyRelatedField's queryset spans every tenant.
    primary_product_name = serializers.CharField(
        source="primary_product.name", read_only=True, default=""
    )

    owner = UserSerializer(read_only=True)
    handover_note = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        help_text="Why the account owner is changing — written down as a contribution.",
    )
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
            "industry",
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
            "health_score_is_overridden",
            "health_breakdown",
            "health_category",
            "csat_breakdown",
            "pulse",
            "ai_pulse_score",
            "ai_pulse_value",
            "ai_pulse_reason",
            "account_pulse",
            "csm_pulse_score",
            "csm_pulse_modified_at",
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
            "primary_product_name",
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
            "churn_reason_display",
            "churn_comment",
            "is_archived",
            "handover_note",
        ]
        read_only_fields = ["created_at", "updated_at", "csm_pulse_modified_at"]

    def validate_primary_product(self, product):
        request = self.context["request"]
        if product is not None and product.organisation_id != request.user.organisation_id:
            raise serializers.ValidationError("Product must belong to your own organisation.")
        return product

    def get_account_pulse(self, obj):
        return obj.account_pulse().as_payload()

    def validate_owner_id(self, owner):
        request = self.context["request"]
        if owner is not None and owner.organisation_id != request.user.organisation_id:
            raise serializers.ValidationError("Owner must be a member of your own organisation.")
        # Changing who is accountable is gated: the current owner, someone
        # above them, or an org-settings manager (services.knowledge.ownership).
        if self.instance is not None and owner != self.instance.owner:
            from services.knowledge.ownership import may_change_owner

            if not may_change_owner(request.user, self.instance):
                raise serializers.ValidationError(
                    "Only the current account owner, their management chain or an "
                    "organisation-settings manager can reassign this account."
                )
        return owner

    def create(self, validated_data):
        request = self.context["request"]
        validated_data.pop("handover_note", "")
        validated_data["organisation"] = request.user.organisation
        validated_data["created_by"] = request.user
        validated_data["modified_by"] = request.user
        # A new customer defaults to the org's own currency unless the
        # caller explicitly picks a different one (e.g. a US-HQ org
        # billing this particular customer in EUR) — same "real default,
        # still overridable" shape as Organisation.default_lifecycle_stage.
        validated_data.setdefault("currency", request.user.organisation.currency)
        # And to the person creating it, unless they named someone else.
        # Records are visible by ownership now (see
        # services/customers/scoping.py), so without this a CSM would
        # add a customer and immediately have it drop into the unowned
        # pool — technically still visible, but listed as nobody's.
        # "Whoever added it owns it until told otherwise" is also just
        # the right default.
        validated_data.setdefault("owner", request.user)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data["modified_by"] = self.context["request"].user
        return super().update(instance, validated_data)


class HealthSnapshotSerializer(serializers.ModelSerializer):
    """One recorded month, as the Movement view's flow chart consumes it."""

    health_category = serializers.ChoiceField(
        choices=Customer.HealthCategory.choices, read_only=True
    )

    class Meta:
        model = HealthSnapshot
        fields = [
            "captured_on",
            "health_score",
            "health_category",
            "csm_pulse_score",
            "ai_pulse_value",
        ]


class CustomerHealthRowSerializer(serializers.ModelSerializer):
    """One customer as the Health Overview dashboard needs it.

    Deliberately not `CustomerSerializer`: that carries forty-odd fields
    including every financial figure, and this endpoint returns the whole book
    with a year of history attached. Only what the four tabs actually read.

    `csm_pulse_score` and `ai_pulse_value` stay **nullable** all the way to the
    browser. "Not rated yet" is a real state — the Divergence view must not read
    an unrated account as one both parties agree is terrible.
    """

    owner_name = serializers.CharField(source="owner.name", default=None, read_only=True)
    lifecycle_stage_display = serializers.CharField(
        source="get_lifecycle_stage_display", read_only=True
    )
    health_category = serializers.ChoiceField(
        choices=Customer.HealthCategory.choices, read_only=True
    )
    history = HealthSnapshotSerializer(source="health_snapshots", many=True, read_only=True)
    arr = serializers.SerializerMethodField()
    days_since_touch = serializers.SerializerMethodField()
    risk_of_loss = serializers.SerializerMethodField()
    risk_factors = serializers.SerializerMethodField()

    def _risk(self, customer):
        """Computed once per row, cached on the instance: the field pair below
        would otherwise run the same rule twice for every customer."""
        if not hasattr(customer, "_risk_of_loss"):
            customer._risk_of_loss = churn.risk_of_loss(
                customer, days_since_touch=customer.health_inputs()["days_since_touch"]
            )
        return customer._risk_of_loss

    def get_risk_of_loss(self, customer):
        """Probability this renewal is lost, from `churn.py`.

        Served rather than computed in the browser so the Renewal Date tab and
        the Revenue Forecast cannot drift apart — they are the same claim about
        the same account, and two implementations of it is a discrepancy with a
        date on it. See that module on why this is a stated business rule and
        not a fitted model.
        """
        return self._risk(customer)[0]

    def get_risk_factors(self, customer):
        """What produced that number, each with its own contribution. The
        Renewal tab prints them on the row: a ranking nobody can interrogate is
        a ranking nobody acts on."""
        return self._risk(customer)[1]

    def get_arr(self, customer):
        """`arr_billed_at_account`, converted into the organisation's own
        reporting currency — **null when it can't be converted**.

        The Renewal Date tab adds money up across the whole book, and a book
        can hold contracts in several currencies (Customer.currency is
        independent of the org's). Treating an unconverted figure as though it
        were already in the org's currency is the one outcome worse than
        leaving it out, so a missing FX rate returns null and the view counts
        it in `unconverted_count` — the same arrangement CustomerStatsView
        uses, through the same single conversion hook.
        """

        organisation = self.context["organisation"]
        converted = convert_to_org_currency(
            customer.arr_billed_at_account,
            customer.currency,
            organisation,
            # Pre-fetched by the view: one query for the request rather than
            # one per row of a five-hundred-row book.
            rates=self.context.get("fx_rates"),
        )
        return None if converted is None else float(converted)

    def get_days_since_touch(self, customer):
        """Days since the last logged activity — how stale the relationship is.

        Already computed for the health rubric (Customer Touch is 4 of its 10
        points), so this is the same number the score is built from rather than
        a second definition of "touched". An account nobody has touched is
        measured from when it arrived, not from never: see `health_inputs`.
        """
        return customer.health_inputs()["days_since_touch"]

    class Meta:
        model = Customer
        fields = [
            "id",
            "name",
            # Both: the name is what a filter chip shows, the id is what it
            # filters by. Two CSMs called "John Smith" is an ordinary thing in
            # a real org, and keying a filter on the label would merge their
            # books without anyone noticing.
            "owner_id",
            "owner_name",
            "lifecycle_stage",
            "lifecycle_stage_display",
            "renewal_date",
            # Money and staleness: the Renewal Date tab ranks by ARR at risk
            # and calls out renewals nobody has touched.
            "arr",
            "days_since_touch",
            "risk_of_loss",
            "risk_factors",
            "health_score",
            "health_category",
            "csm_pulse_score",
            "csm_pulse_modified_at",
            "ai_pulse_value",
            "ai_pulse_reason",
            # The dashboard sizes its scatter dots by this. It is seats, not
            # "recruiters" — the mock it replaces invented that field.
            "total_active_seats",
            "history",
        ]


class AccountSerializer(PulseWritesMixin, serializers.ModelSerializer):
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
    ai_pulse_score = AIPulseScoreField(source="*", required=False)
    owner = UserSerializer(read_only=True)
    owner_id = serializers.PrimaryKeyRelatedField(
        source="owner",
        queryset=User.objects.all(),
        write_only=True,
        required=False,
        allow_null=True,
    )
    handover_note = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        help_text="Why the account owner is changing — written down as a contribution "
        "on every organisation the account belongs to.",
    )
    customers = serializers.SerializerMethodField()
    account_pulse = serializers.SerializerMethodField(
        help_text="How the relationship feels right now — pulse.py: value 1-5, label, "
        "history category and the per-signal breakdown."
    )
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
            "industry",
            "address",
            "email",
            "phone",
            "owner",
            "owner_id",
            "handover_note",
            "account_pulse",
            "created_at",
            "updated_at",
            "lifecycle_stage",
            "health_score",
            "health_category",
            "pulse",
            "ai_pulse_score",
            "ai_pulse_value",
            "ai_pulse_reason",
            "csm_pulse_score",
            "csm_pulse_modified_at",
            "nps_score",
            "csat_score",
            "renewal_date",
            "arr",
        ]
        read_only_fields = ["created_at", "updated_at", "csm_pulse_modified_at"]

    def get_customers(self, obj):
        return [{"id": c.id, "name": c.name} for c in obj.customers.all()]

    def get_account_pulse(self, obj):
        return obj.account_pulse().as_payload()

    def validate_owner_id(self, owner):
        request = self.context["request"]
        if owner is not None and owner.organisation_id != request.user.organisation_id:
            raise serializers.ValidationError("Owner must be a member of your own organisation.")
        # Same rule as an organisation's owner: the current owner, someone
        # above them, or an org-settings manager (services.knowledge.ownership).
        if self.instance is not None and owner != self.instance.owner:
            from services.knowledge.ownership import may_change_owner

            if not may_change_owner(request.user, self.instance):
                raise serializers.ValidationError(
                    "Only the current account owner, their management chain or an "
                    "organisation-settings manager can reassign this account."
                )
        return owner

    def create(self, validated_data):
        validated_data.pop("handover_note", "")
        return super().create(validated_data)

    def update(self, instance, validated_data):
        # The handover note is for the record, not the row (see the view).
        self.context["handover_note"] = validated_data.pop("handover_note", "")
        return super().update(instance, validated_data)

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
    """Read-only — see Email model's docstring. `mailbox_owner` and
    `direction` are set on rows synced through someone's mailbox."""

    mailbox_owner = serializers.SerializerMethodField()

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
            "direction",
            "from_address",
            "to_addresses",
            "thread_id",
            "mailbox_owner",
        ]

    def get_mailbox_owner(self, obj):
        if obj.mailbox_owner_id is None:
            return None
        return {"id": obj.mailbox_owner_id, "name": obj.mailbox_owner.name}


class TaskSerializer(serializers.ModelSerializer):
    """Read, and create. No "group" field — the frontend derives the
    Overdue/This Week/Next Week/Later bucket from `due_date` at render
    time. On write, `assignee_id` names a member of the caller's
    organisation (default: the caller); `created_by` is always the caller."""

    initiative = serializers.SerializerMethodField()
    assignee = serializers.SerializerMethodField()
    created_by = serializers.SerializerMethodField()
    assignee_id = serializers.PrimaryKeyRelatedField(
        source="assignee",
        queryset=User.objects.all(),
        write_only=True,
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Task
        fields = [
            "id",
            "title",
            "assignee_name",
            "assignee",
            "assignee_id",
            "created_by",
            "due_date",
            "priority",
            "status",
            "initiative",
        ]
        read_only_fields = ["assignee_name"]

    @staticmethod
    def _person(user):
        return None if user is None else {"id": user.id, "name": user.name}

    def get_assignee(self, task):
        return self._person(task.assignee)

    def get_created_by(self, task):
        return self._person(task.created_by)

    def validate_assignee_id(self, assignee):
        request = self.context["request"]
        if assignee is not None and assignee.organisation_id != request.user.organisation_id:
            raise serializers.ValidationError("Assignee must be a member of your own organisation.")
        return assignee

    def get_initiative(self, task):
        # The decision this work serves — see Task.initiative.
        if task.initiative_id is None:
            return None
        return {"id": task.initiative.id, "title": task.initiative.title}


class TaskListSerializer(serializers.ModelSerializer):
    """Powers the standalone, cross-company `GET /api/v1/tasks/`
    (TaskListView) — unlike TaskSerializer above (used by the nested
    per-Customer/per-Account endpoints, where the parent is already
    known from the URL), a flat list spanning every Customer/Account
    needs to say *which* company each row belongs to, same reasoning
    as OpportunitySerializer/RiskSerializer's own `account_name`.
    `parent_name`/`parent_type` are a simpler pair than those two's own
    plural `companies` — a Task's own parent is always exactly one
    Customer or one Account (never an Account shared across several
    Customers the way Opportunity/Risk's own `companies` accounts for),
    so there's nothing to pluralize here."""

    priority_display = serializers.CharField(source="get_priority_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    parent_name = serializers.SerializerMethodField()
    parent_type = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            "id",
            "title",
            "assignee_name",
            "due_date",
            "priority",
            "priority_display",
            "status",
            "status_display",
            "parent_name",
            "parent_type",
        ]

    def get_parent_name(self, obj):
        return obj.customer.name if obj.customer_id else obj.account.name

    def get_parent_type(self, obj):
        return "customer" if obj.customer_id else "account"


class NoteSerializer(serializers.ModelSerializer):
    """Read, and write a new note. `author` is set from the caller; a note
    is readable by them and their management chain only."""

    author = serializers.SerializerMethodField()
    logged_at = serializers.DateField(required=False)

    class Meta:
        model = Note
        fields = ["id", "title", "author_name", "author", "body", "logged_at", "links"]
        read_only_fields = ["author_name", "links"]

    def get_author(self, obj):
        if obj.author_id is None:
            return None
        return {"id": obj.author_id, "name": obj.author.name}


class AttachmentSerializer(serializers.ModelSerializer):
    """A file on a customer or account. Never the storage path: the bytes
    come from the download endpoint, which checks who is asking."""

    uploaded_by = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = Attachment
        fields = [
            "id",
            "name",
            "content_type",
            "size",
            "description",
            "source",
            "uploaded_by",
            "download_url",
            "created_at",
        ]
        read_only_fields = fields

    def get_uploaded_by(self, obj):
        if obj.uploaded_by_id is None:
            return None
        return {"id": obj.uploaded_by_id, "name": obj.uploaded_by.name}

    def get_download_url(self, obj):
        return f"/api/v1/files/{obj.id}/download/"


class CallSerializer(serializers.ModelSerializer):
    """Read, and log a new call. On create, `transcript_text` (pasted) or
    `transcript` (an uploaded .txt/.vtt/.srt/.md file) may stand in for
    `summary`: the model writes the summary from it, and the transcript is
    kept as an Attachment. `host_name` defaults to the caller."""

    connector_name = serializers.CharField(source="connector.name", read_only=True, default=None)
    connector_provider = serializers.CharField(
        source="connector.provider", read_only=True, default=None
    )
    logged_by = serializers.SerializerMethodField()
    transcript = serializers.SerializerMethodField()
    host_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    transcript_text = serializers.CharField(write_only=True, required=False, allow_blank=True)
    participants = serializers.SerializerMethodField()
    participant_ids = serializers.PrimaryKeyRelatedField(
        queryset=Contact.objects.all(), many=True, write_only=True, required=False
    )

    class Meta:
        model = Call
        fields = [
            "id",
            "title",
            "host_name",
            "occurred_at",
            "duration_minutes",
            "summary",
            "sentiment",
            "ai_area",
            "ai_category",
            "recording_url",
            "connector_name",
            "connector_provider",
            "logged_by",
            "transcript",
            "transcript_text",
            "participants",
            "participant_ids",
            "links",
            "created_at",
        ]
        read_only_fields = ["sentiment", "ai_area", "ai_category", "links", "created_at"]
        extra_kwargs = {"summary": {"required": False, "allow_blank": True}}

    def get_participants(self, obj):
        return [
            {
                "id": c.id,
                "name": c.name,
                "role_display": c.get_role_display(),
                "sentiment": c.sentiment,
            }
            for c in obj.participants.all()
        ]

    def get_logged_by(self, obj):
        if obj.logged_by_id is None:
            return None
        return {"id": obj.logged_by_id, "name": obj.logged_by.name}

    def get_transcript(self, obj):
        if obj.transcript_id is None:
            return None
        return AttachmentSerializer(obj.transcript).data


class TicketSerializer(serializers.ModelSerializer):
    """Read-only — see Ticket model's docstring.

    `connector_name`/`connector_provider` are flattened rather than
    nested: a ticket card wants "Zendesk" as a label, not an object,
    and the full connector is available from /connectors/ for anything
    that needs more. Both are null for a ticket raised in Revenact
    itself."""

    connector_name = serializers.CharField(source="connector.name", read_only=True, default=None)
    connector_provider = serializers.CharField(
        source="connector.provider", read_only=True, default=None
    )
    department_display = serializers.SerializerMethodField()

    class Meta:
        model = Ticket
        fields = [
            "id",
            "ticket_number",
            "title",
            "assignee_name",
            "status",
            "priority",
            "sentiment",
            "opened_at",
            "resolved_at",
            "links",
            "connector_name",
            "connector_provider",
            "department",
            "department_display",
            "description",
            "requester_name",
            "requester_email",
            "external_url",
            "synced_at",
        ]

    def get_department_display(self, obj):
        from services.accounts.models import User

        return dict(User.Function.choices).get(obj.department, "") if obj.department else ""


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
            "sentiment_source",
            "sentiment_evidence",
            "sentiment_computed_at",
            "last_contacted_at",
            "companies",
            "account_name",
        ]
        read_only_fields = ["sentiment_source", "sentiment_evidence", "sentiment_computed_at"]

    def update(self, instance, validated_data):
        # A hand-set sentiment is the fallback for a contact with no evidence;
        # once their calls, emails or tickets are classified it is recomputed.
        if "sentiment" in validated_data:
            validated_data["sentiment_source"] = Contact.SentimentSource.MANUAL
            validated_data["sentiment_evidence"] = {}
            validated_data["sentiment_computed_at"] = None
        return super().update(instance, validated_data)

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

        # SurveyDetailView.perform_update re-syncs the parent's score
        # field on every update while already RESPONDED, keyed off the
        # survey's *current* survey_type — letting an edit change that
        # type post-response would sync the score onto the new field
        # while leaving the old one stale. Simplest fix: don't allow it.
        if (
            self.instance
            and self.instance.status == Survey.Status.RESPONDED
            and "survey_type" in attrs
            and attrs["survey_type"] != self.instance.survey_type
        ):
            raise serializers.ValidationError(
                {"survey_type": "Can't change a survey's type after it's been responded to."}
            )

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


class CanvasSerializer(serializers.ModelSerializer):
    """`companies`/`account_name` mirror Opportunity/Risk/SurveySerializer's
    own fields exactly — the standalone Canvas gallery spans every
    Customer/Account the same way. `nodes`/`edges` round-trip as raw
    JSON, same as ScenarioSerializer's own — the backend never inspects
    them, see the Canvas model's own docstring."""

    companies = serializers.SerializerMethodField()
    account_name = serializers.SerializerMethodField()

    class Meta:
        model = Canvas
        fields = [
            "id",
            "name",
            "nodes",
            "edges",
            "companies",
            "account_id",
            "account_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at", "account_id"]

    def get_companies(self, obj):
        return [{"id": c.id, "name": c.name} for c in obj.companies]

    def get_account_name(self, obj):
        return obj.account.name if obj.account_id else None


class HeadlineSerializer(serializers.ModelSerializer):
    """See Headline model's docstring. Writable, unlike NoteSerializer —
    a headline can be hand-written or corrected, not only generated.

    Three read-only fields exist so the card doesn't have to re-derive
    what the model already knows:

    * `group` — the pill above a feed card ('January'), derived from
      `period_end` the same way NotesTab derives its own date headers.
      Empty for a SUMMARY (pinned above the groups) and for a HEADLINE
      with no `period_end` to group under.
    * `status_display` / `kind_display` — the human labels, same
      `get_<field>_display` passthrough as Activity/Task/Survey.
    * `data_sources_display` — the footer's prose list ("Notes, Emails,
      Call Transcripts and Tickets"), built from the stored keys so the
      claim always matches what was actually read."""

    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    group = serializers.SerializerMethodField()
    data_sources_display = serializers.SerializerMethodField()

    class Meta:
        model = Headline
        fields = [
            "id",
            "kind",
            "kind_display",
            "title",
            "content",
            "status",
            "status_display",
            "period_start",
            "period_end",
            "time_period_label",
            "data_sources",
            "data_sources_display",
            "group",
            "generated_at",
            "created_at",
        ]
        read_only_fields = ["generated_at", "created_at"]

    def get_group(self, obj) -> str:
        if obj.kind == Headline.Kind.SUMMARY or obj.period_end is None:
            return ""
        return obj.period_end.strftime("%B %Y")

    def get_data_sources_display(self, obj) -> str:
        labels = [
            label
            for value, label in Headline.DataSource.choices
            if value in (obj.data_sources or [])
        ]
        if not labels:
            return ""
        if len(labels) == 1:
            return labels[0]
        return f"{', '.join(labels[:-1])} and {labels[-1]}"

    def validate_data_sources(self, value):
        """The field is a JSONField, so DRF won't check its contents —
        without this any string at all could be stored and then rendered
        as a source the generator never read."""
        if not isinstance(value, list):
            raise serializers.ValidationError("Expected a list of data source keys.")
        allowed = set(Headline.DataSource.values)
        unknown = [item for item in value if item not in allowed]
        if unknown:
            raise serializers.ValidationError(
                f"Unknown data source(s): {', '.join(map(str, unknown))}. "
                f"Choose from: {', '.join(sorted(allowed))}."
            )
        return value

    def validate(self, attrs):
        """Mirrors the model's own `headline_summary_has_no_status`
        constraint so a bad payload is a 400 naming the field, not a
        500 from the database."""
        kind = attrs.get("kind", getattr(self.instance, "kind", Headline.Kind.HEADLINE))
        status = attrs.get("status", getattr(self.instance, "status", ""))
        if kind == Headline.Kind.SUMMARY and status:
            raise serializers.ValidationError(
                {"status": "A summary card has no status — leave it blank."}
            )

        start = attrs.get("period_start", getattr(self.instance, "period_start", None))
        end = attrs.get("period_end", getattr(self.instance, "period_end", None))
        if start and end and start > end:
            raise serializers.ValidationError(
                {"period_start": "The period can't start after it ends."}
            )
        return attrs


class ProductSerializer(serializers.ModelSerializer):
    """One of the tenant's own products.

    `customers` is the reason Delete is refused rather than cascading: it says
    how many customers are recorded against this product, and the answer is
    also the answer to "may I delete it?".
    """

    customers = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = ["id", "name", "is_active", "customers", "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at"]

    def get_customers(self, product):
        """How many customers this product leads, annotation-first.

        Counted in one query for a list page (see ProductListView) and by
        asking for a single row, so neither path runs a query per product.
        """
        if hasattr(product, "customer_count"):
            return product.customer_count
        return product.primary_customers.count()

    def validate_name(self, name):
        """One product per name per organisation, case-insensitively.

        The database enforces it too (Product's own constraint), but a 500 on a
        duplicate is not an answer — this is the message that tells somebody
        the product they are adding is the one already in the list under a
        different capitalisation.
        """
        cleaned = name.strip()
        if not cleaned:
            raise serializers.ValidationError("A product needs a name.")

        organisation = self.context["request"].user.organisation
        clash = Product.objects.filter(organisation=organisation, name__iexact=cleaned)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(
                f'"{clash.first().name}" already exists — products are unique per '
                "organisation, ignoring case."
            )
        return cleaned

    def create(self, validated_data):
        validated_data["organisation"] = self.context["request"].user.organisation
        return super().create(validated_data)
