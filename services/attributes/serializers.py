from rest_framework import serializers

from services.custom_objects.serializers import _unique_slug

from .fill import coerce
from .models import AIAttribute, AIAttributeValue


class AIAttributeSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIAttribute
        fields = [
            "id",
            "name",
            "api_name",
            "prompt",
            "value_type",
            "picklist_options",
            "applies_to_customer",
            "applies_to_account",
            "refresh",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["api_name", "created_at", "updated_at"]

    def validate(self, attrs):
        current = self.instance
        value_type = attrs.get("value_type", current.value_type if current else None)
        options = attrs.get("picklist_options", current.picklist_options if current else [])
        if value_type == AIAttribute.ValueType.PICKLIST and not options:
            raise serializers.ValidationError(
                {"picklist_options": "A picklist needs at least one option."}
            )
        to_customer = attrs.get(
            "applies_to_customer", current.applies_to_customer if current else True
        )
        to_account = attrs.get(
            "applies_to_account", current.applies_to_account if current else False
        )
        if not (to_customer or to_account):
            raise serializers.ValidationError(
                "An attribute must apply to organizations, accounts, or both."
            )
        return attrs

    def create(self, validated_data):
        organisation = self.context["request"].user.organisation
        existing = set(
            AIAttribute.objects.filter(organisation=organisation).values_list("api_name", flat=True)
        )
        validated_data["api_name"] = _unique_slug(validated_data["name"], existing, "attribute")
        validated_data["organisation"] = organisation
        validated_data["created_by"] = self.context["request"].user
        return super().create(validated_data)


class AttributeBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIAttribute
        fields = ["id", "name", "api_name", "prompt", "value_type", "picklist_options", "refresh"]


class AIAttributeValueSerializer(serializers.ModelSerializer):
    set_by = serializers.SerializerMethodField()

    class Meta:
        model = AIAttributeValue
        fields = [
            "id",
            "attribute",
            "customer",
            "account",
            "value",
            "reasoning",
            "sources",
            "status",
            "origin",
            "set_by",
            "computed_at",
        ]
        read_only_fields = fields

    def get_set_by(self, row):
        return {"id": row.set_by.id, "name": row.set_by.name} if row.set_by else None


class OverrideSerializer(serializers.Serializer):
    """A person's own answer: typed like the model's, but never null."""

    attribute = serializers.PrimaryKeyRelatedField(queryset=AIAttribute.objects.none())
    customer = serializers.IntegerField(required=False)
    account = serializers.IntegerField(required=False)
    value = serializers.JSONField()

    def __init__(self, *args, organisation=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["attribute"].queryset = AIAttribute.objects.filter(organisation=organisation)

    def validate(self, attrs):
        if bool(attrs.get("customer")) == bool(attrs.get("account")):
            raise serializers.ValidationError("Give exactly one of customer or account.")
        attribute = attrs["attribute"]
        try:
            value = coerce(attribute.value_type, attrs["value"], attribute.picklist_options)
        except ValueError as exc:
            raise serializers.ValidationError({"value": str(exc)}) from exc
        if value is None:
            raise serializers.ValidationError({"value": "A value is required."})
        attrs["value"] = value
        return attrs
