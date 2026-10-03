from rest_framework import serializers

from services.accounts.models import User

from .models import Segment, default_rules
from .rules import RuleError, validate_rules

#: How many teammates one segment may be shared with by name.
MAX_SHARED = 50
NO_TEAMMATES = "Choose at least one teammate."


class SegmentWriteSerializer(serializers.ModelSerializer):
    """Create and edit. Pins and keep-outs have their own endpoint; the
    owner and workspace come from the request, never the body."""

    shared_with = serializers.PrimaryKeyRelatedField(
        many=True, queryset=User.objects.none(), required=False
    )

    class Meta:
        model = Segment
        fields = [
            "name",
            "description",
            "kind",
            "rules",
            "sharing",
            "shared_with",
            "alert_on_changes",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = self.context["request"].user
        # SOC2:AUTH-02 shared only with active teammates in the owner's workspace
        self.fields["shared_with"].child_relation.queryset = User.objects.filter(
            organisation_id=user.organisation_id, is_active=True
        ).exclude(pk=user.pk)

    def validate(self, attrs):
        instance = self.instance
        if instance is not None and attrs.get("kind", instance.kind) != instance.kind:
            raise serializers.ValidationError({"kind": ["A segment's kind cannot change."]})
        kind = instance.kind if instance is not None else attrs["kind"]
        if instance is None or "rules" in attrs:
            try:
                attrs["rules"] = validate_rules(
                    attrs.get("rules", default_rules()), kind, user=self.context["request"].user
                )
            except RuleError as exc:
                raise serializers.ValidationError({"rules": [str(exc)]}) from exc
        default_sharing = instance.sharing if instance is not None else Segment.Sharing.PRIVATE
        if attrs.get("sharing", default_sharing) == Segment.Sharing.PEOPLE:
            if "shared_with" in attrs:
                people = attrs["shared_with"]
            else:
                people = list(instance.shared_with.all()) if instance is not None else []
            if not people:
                raise serializers.ValidationError({"shared_with": [NO_TEAMMATES]})
            if len(people) > MAX_SHARED:
                raise serializers.ValidationError(
                    {"shared_with": [f"Share with at most {MAX_SHARED} teammates."]}
                )
        elif "sharing" in attrs or "shared_with" in attrs:
            attrs["shared_with"] = []
        return attrs
