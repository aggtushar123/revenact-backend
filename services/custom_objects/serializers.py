import re

from django.db.models import Max
from django.utils.text import slugify
from rest_framework import serializers

from services.customers.models import Account, Customer

from .models import CustomFieldDefinition, CustomObjectDefinition, CustomObjectRecord

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _unique_slug(name: str, existing: set[str], fallback: str) -> str:
    """A real, collision-free api_name derived from a human-typed name —
    shared by both definitions (unique per organisation) and fields
    (unique per object) rather than duplicated, same "generic helper,
    not copy-pasted per model" reasoning as this app's own record
    validation below."""
    base = slugify(name).replace("-", "_") or fallback
    if base not in existing:
        return base
    n = 2
    while f"{base}_{n}" in existing:
        n += 1
    return f"{base}_{n}"


class CustomFieldDefinitionSerializer(serializers.ModelSerializer):
    """Read: every real column. Write (nested under one
    CustomObjectDefinition's own /fields/ endpoint — see views.py):
    name/field_type/is_required/picklist_options only — api_name and
    order are always derived server-side (see create() below), never
    client-set, so two fields can't collide and the display order
    always reflects real creation order."""

    field_type_display = serializers.CharField(source="get_field_type_display", read_only=True)

    class Meta:
        model = CustomFieldDefinition
        fields = [
            "id",
            "name",
            "api_name",
            "field_type",
            "field_type_display",
            "is_required",
            "picklist_options",
            "order",
            "created_at",
        ]
        read_only_fields = ["api_name", "order", "created_at"]

    def validate(self, attrs):
        field_type = attrs.get("field_type", getattr(self.instance, "field_type", None))
        options = attrs.get("picklist_options", getattr(self.instance, "picklist_options", None))
        if field_type == CustomFieldDefinition.FieldType.PICKLIST and not options:
            raise serializers.ValidationError(
                {"picklist_options": "A Picklist field needs at least one option."}
            )
        return attrs

    def create(self, validated_data):
        object_definition = self.context["object_definition"]
        existing = set(object_definition.fields.values_list("api_name", flat=True))
        validated_data["object_definition"] = object_definition
        validated_data["api_name"] = _unique_slug(validated_data["name"], existing, "field")
        current_max = object_definition.fields.aggregate(Max("order"))["order__max"] or 0
        validated_data["order"] = current_max + 1
        return super().create(validated_data)


class CustomObjectDefinitionSerializer(serializers.ModelSerializer):
    """Read: every real column, plus this org's own `fields` nested
    (read-only here — fields are managed through their own
    CustomFieldDefinitionListCreateView/CustomFieldDefinitionDetailView,
    same "child collection has its own endpoint, not accepted inline on
    the parent's own write" shape as Contact's `companies` vs.
    Customer/Account's own create endpoints) and a real `records_count`
    (how many records exist across every Customer/Account this
    definition applies to — not scoped to one parent, since this is the
    Settings-page-level view of the object, not one entity's own tab).

    Write: name + applies_to_customer/applies_to_account only —
    api_name/organisation/created_by are always derived server-side."""

    fields = CustomFieldDefinitionSerializer(many=True, read_only=True)
    records_count = serializers.SerializerMethodField()

    class Meta:
        model = CustomObjectDefinition
        fields = [
            "id",
            "name",
            "api_name",
            "applies_to_customer",
            "applies_to_account",
            "fields",
            "records_count",
            "created_at",
        ]
        read_only_fields = ["api_name", "created_at"]

    def get_records_count(self, obj) -> int:
        return obj.records.count()

    def validate(self, attrs):
        applies_to_customer = attrs.get(
            "applies_to_customer", getattr(self.instance, "applies_to_customer", None)
        )
        applies_to_account = attrs.get(
            "applies_to_account", getattr(self.instance, "applies_to_account", None)
        )
        if not applies_to_customer and not applies_to_account:
            raise serializers.ValidationError(
                "A custom object must apply to Organizations, Accounts, or both."
            )
        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        organisation = request.user.organisation
        existing = set(
            CustomObjectDefinition.objects.filter(organisation=organisation).values_list(
                "api_name", flat=True
            )
        )
        validated_data["organisation"] = organisation
        validated_data["created_by"] = request.user
        validated_data["api_name"] = _unique_slug(validated_data["name"], existing, "object")
        return super().create(validated_data)


class CustomObjectRecordSerializer(serializers.ModelSerializer):
    """Read: real object_definition/customer/account ids plus `data`,
    plus `parent_name`/`parent_type` — same pair as TaskListSerializer's
    own, needed here for the same reason: the org-wide per-object page
    (pages/customObjects/CustomObjectRecordsPage.tsx, via
    CustomObjectRecordListCreateView with no `?customer=`/`?account=`)
    spans every parent at once, so each row needs to say *which*
    Organization/Account it belongs to. Harmless on the parent-scoped
    responses too (CustomObjectsTab.tsx's own calls, where the parent
    is already known) — not worth a second serializer for two extra
    strings, unlike Task's own TaskSerializer/TaskListSerializer split.

    Write: object_definition_id + exactly one of customer_id/account_id
    (object_definition fixed at create, never changed on update — see
    validate()) plus `data`, validated field by field against the
    definition's own real CustomFieldDefinitions — every `is_required`
    field present, every value type-correct for its `field_type`, no
    unmapped keys. See `_validate_data`/`_coerce_value` for exactly what
    "type-correct" means per field_type."""

    object_definition_id = serializers.PrimaryKeyRelatedField(
        source="object_definition", queryset=CustomObjectDefinition.objects.all()
    )
    customer_id = serializers.PrimaryKeyRelatedField(
        source="customer", queryset=Customer.objects.all(), required=False, allow_null=True
    )
    account_id = serializers.PrimaryKeyRelatedField(
        source="account", queryset=Account.objects.all(), required=False, allow_null=True
    )
    parent_name = serializers.SerializerMethodField()
    parent_type = serializers.SerializerMethodField()

    class Meta:
        model = CustomObjectRecord
        fields = [
            "id",
            "object_definition_id",
            "customer_id",
            "account_id",
            "parent_name",
            "parent_type",
            "data",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def get_parent_name(self, obj) -> str:
        return obj.customer.name if obj.customer_id else obj.account.name

    def get_parent_type(self, obj) -> str:
        return "customer" if obj.customer_id else "account"

    def validate(self, attrs):
        request = self.context["request"]
        org_id = request.user.organisation_id

        object_definition = attrs.get(
            "object_definition", getattr(self.instance, "object_definition", None)
        )
        if object_definition.organisation_id != org_id:
            raise serializers.ValidationError("That custom object isn't in your own organisation.")

        customer = attrs.get("customer", getattr(self.instance, "customer", None))
        account = attrs.get("account", getattr(self.instance, "account", None))
        if bool(customer) == bool(account):
            raise serializers.ValidationError(
                "A record must belong to exactly one of an Organization or an Account."
            )
        if customer is not None:
            if customer.organisation_id != org_id:
                raise serializers.ValidationError(
                    "That organization isn't in your own organisation."
                )
            if not object_definition.applies_to_customer:
                raise serializers.ValidationError(
                    "This custom object doesn't apply to Organizations."
                )
        else:
            if not account.customers.filter(organisation_id=org_id).exists():
                raise serializers.ValidationError("That account isn't in your own organisation.")
            if not object_definition.applies_to_account:
                raise serializers.ValidationError("This custom object doesn't apply to Accounts.")

        data = attrs.get("data", getattr(self.instance, "data", None) if self.instance else {})
        attrs["data"] = self._validate_data(object_definition, data or {})
        return attrs

    def _validate_data(self, object_definition, data: dict) -> dict:
        field_defs = {f.api_name: f for f in object_definition.fields.all()}
        unknown = set(data.keys()) - set(field_defs.keys())
        if unknown:
            raise serializers.ValidationError(
                {"data": f"Unknown field(s): {', '.join(sorted(unknown))}."}
            )

        cleaned = {}
        for api_name, field in field_defs.items():
            value = data.get(api_name)
            if value is None or value == "":
                if field.is_required:
                    raise serializers.ValidationError({"data": f"{field.name} is required."})
                continue
            cleaned[api_name] = self._coerce_value(field, value)
        return cleaned

    def _coerce_value(self, field: CustomFieldDefinition, value):
        FieldType = CustomFieldDefinition.FieldType
        if field.field_type in (FieldType.NUMBER, FieldType.CURRENCY):
            try:
                return float(value)
            except (TypeError, ValueError):
                raise serializers.ValidationError({"data": f"{field.name} must be a number."})
        if field.field_type == FieldType.BOOLEAN:
            if not isinstance(value, bool):
                raise serializers.ValidationError({"data": f"{field.name} must be true or false."})
            return value
        if field.field_type == FieldType.DATE:
            if not isinstance(value, str) or not _DATE_RE.match(value):
                raise serializers.ValidationError(
                    {"data": f"{field.name} must be a YYYY-MM-DD date."}
                )
            return value
        if field.field_type == FieldType.PICKLIST:
            if value not in field.picklist_options:
                raise serializers.ValidationError(
                    {"data": f"{field.name} must be one of {field.picklist_options}."}
                )
            return value
        return str(value)  # TEXT

    def create(self, validated_data):
        validated_data["created_by"] = self.context["request"].user
        return super().create(validated_data)
