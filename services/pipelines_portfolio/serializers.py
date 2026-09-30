from datetime import date

from rest_framework import serializers

from services.accounts.models import User
from services.organizations.params import MAX_IDS

#: Stage, priority, department and date: the spec's four bulk edits.
ACTIONS = ("set_stage", "set_priority", "set_department", "set_date")
DATE_VALUE = "A date is YYYY-MM-DD, or null to clear it."


class BulkRequestSerializer(serializers.Serializer):
    """`context["kind"]` is the route's kind: a stage is checked against that
    kind's own stages (Closed Lost is an opportunity's, Mitigated a risk's).
    Each id is then saved through the kind's single-edit serializer, which
    checks the value again."""

    ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), min_length=1, max_length=MAX_IDS
    )
    action = serializers.ChoiceField(choices=ACTIONS)
    value = serializers.JSONField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        kind = self.context["kind"]
        action, value = attrs["action"], attrs.get("value")
        allowed = {
            "set_stage": (kind.stages, "Not a stage."),
            "set_priority": (tuple(kind.model.Priority.values), "Not a priority."),
            # Blank is "everyone's", as on the single edit.
            "set_department": (("", *User.Function.values), "Not a department."),
        }
        if action in allowed:
            values, message = allowed[action]
            if not isinstance(value, str) or value not in values:
                raise serializers.ValidationError({"value": message})
        if action == "set_date":
            # Clearing is `null`, said out loud: a forgotten key must not
            # strip the date from every selected item.
            if "value" not in self.initial_data:
                raise serializers.ValidationError({"value": DATE_VALUE})
            if value is not None:
                try:
                    date.fromisoformat(value)
                except (TypeError, ValueError):
                    raise serializers.ValidationError({"value": DATE_VALUE}) from None
        attrs["ids"] = list(dict.fromkeys(attrs["ids"]))
        return attrs
