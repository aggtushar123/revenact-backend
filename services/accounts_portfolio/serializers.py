from rest_framework import serializers

from services.customers.models import Customer
from services.organizations.params import MAX_IDS

#: Owner and lifecycle: the spec's two bulk edits. Accounts have no archive or
#: churn, so there is nothing else to apply in bulk.
ACTIONS = ("set_owner", "set_lifecycle")
OWNER_VALUE = "An owner is a user id, or null to unassign."


class BulkRequestSerializer(serializers.Serializer):
    ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), min_length=1, max_length=MAX_IDS
    )
    action = serializers.ChoiceField(choices=ACTIONS)
    value = serializers.JSONField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        action, value = attrs["action"], attrs.get("value")
        if action == "set_owner":
            # Unassigning is `null`, said out loud: a forgotten key must not
            # strip the owner from every selected account.
            if "value" not in self.initial_data:
                raise serializers.ValidationError({"value": OWNER_VALUE})
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise serializers.ValidationError({"value": OWNER_VALUE})
        if action == "set_lifecycle":
            # Every stage the single edit accepts, Churn included: on an
            # account it is only a stage, with no churn flow behind it.
            if not isinstance(value, str) or value not in Customer.LifecycleStage.values:
                raise serializers.ValidationError({"value": "Not a lifecycle stage."})
        attrs["ids"] = list(dict.fromkeys(attrs["ids"]))
        return attrs
