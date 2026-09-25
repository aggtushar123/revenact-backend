from rest_framework import serializers

from services.customers.models import Customer

from .params import MAX_IDS

ACTIONS = ("set_owner", "set_lifecycle", "archive")


class BulkRequestSerializer(serializers.Serializer):
    ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), min_length=1, max_length=MAX_IDS
    )
    action = serializers.ChoiceField(choices=ACTIONS)
    value = serializers.JSONField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        action, value = attrs["action"], attrs.get("value")
        if action == "set_owner" and "value" not in self.initial_data:
            # Unassigning is `null`, said out loud: a forgotten key must not
            # strip the owner from every selected organization.
            raise serializers.ValidationError(
                {"value": "An owner is a user id, or null to unassign."}
            )
        if action == "set_owner" and value is not None:
            if isinstance(value, bool) or not isinstance(value, int):
                raise serializers.ValidationError(
                    {"value": "An owner is a user id, or null to unassign."}
                )
        if action == "set_lifecycle":
            if not isinstance(value, str) or value not in Customer.LifecycleStage.values:
                raise serializers.ValidationError({"value": "Not a lifecycle stage."})
            if value == Customer.LifecycleStage.CHURN:
                raise serializers.ValidationError(
                    {
                        "value": "Churn has its own flow, which records the date and reason: "
                        "churn each organization from its churn action."
                    }
                )
        if action == "archive":
            attrs["value"] = True
        attrs["ids"] = list(dict.fromkeys(attrs["ids"]))
        return attrs
