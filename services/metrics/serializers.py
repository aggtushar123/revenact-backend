from django.utils import timezone
from rest_framework import serializers

from services.accounts.models import User

from . import initiatives
from .models import Initiative
from .registry import BY_KEY, DIMENSION_LABELS, compute_slice


class InitiativeSerializer(serializers.ModelSerializer):
    """One decision, with where its number stands read live from the
    registry through a `Figures` in the context (see the views)."""

    owner_id = serializers.PrimaryKeyRelatedField(
        source="owner",
        queryset=User.objects.all(),
        write_only=True,
        required=False,
        allow_null=True,
    )
    owner = serializers.SerializerMethodField()
    metric_label = serializers.SerializerMethodField()
    dimension_label = serializers.SerializerMethodField()
    member_label = serializers.SerializerMethodField()
    progress = serializers.SerializerMethodField()
    history = serializers.SerializerMethodField()
    work = serializers.SerializerMethodField()
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Initiative
        fields = [
            "id",
            "title",
            "hypothesis",
            "metric",
            "metric_label",
            "dimension",
            "dimension_label",
            "member",
            "member_label",
            "target_value",
            "target_by",
            "owner",
            "owner_id",
            "status",
            "status_display",
            "outcome",
            "baseline_value",
            "baseline_as_of",
            "progress",
            "history",
            "work",
            "created_at",
            "updated_at",
            "closed_at",
        ]
        read_only_fields = [
            "baseline_value",
            "baseline_as_of",
            "created_at",
            "updated_at",
            "closed_at",
        ]

    # ── reads ────────────────────────────────────────────────────────

    def _figures(self):
        return self.context["figures"]

    def get_owner(self, initiative):
        if initiative.owner is None:
            return None
        return {"id": initiative.owner.id, "name": initiative.owner.name}

    def get_work(self, initiative):
        """The tasks under this decision — open first, soonest due first —
        so an initiative shows the work being done on it, not only the
        number. Approving a proposal that serves the initiative links its
        task here (see proposals.approve)."""
        tasks = list(initiative.tasks.select_related("customer", "account"))
        done = [t for t in tasks if t.status == t.Status.COMPLETED]
        open_ = [t for t in tasks if t.status != t.Status.COMPLETED]
        ordered = sorted(open_, key=lambda t: (t.due_date, -t.id)) + sorted(
            done, key=lambda t: (-t.id,)
        )
        return {
            "open": len(open_),
            "done": len(done),
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "parent_name": (t.customer or t.account).name,
                    "parent_type": "customer" if t.customer_id else "account",
                    "parent_id": t.customer_id or t.account_id,
                    "assignee_name": t.assignee_name,
                    "due_date": t.due_date.isoformat(),
                    "priority": t.priority,
                    "status": t.status,
                }
                for t in ordered
            ],
        }

    def get_metric_label(self, initiative):
        metric = BY_KEY.get(initiative.metric)
        return metric.label if metric else initiative.metric

    def get_dimension_label(self, initiative):
        return DIMENSION_LABELS.get(initiative.dimension, "") if initiative.dimension else ""

    def get_member_label(self, initiative):
        return self._figures().label_of(initiative)

    def get_progress(self, initiative):
        return initiatives.progress(initiative, self._figures().value_of(initiative))

    def get_history(self, initiative):
        return initiatives.history(initiative)

    # ── writes ───────────────────────────────────────────────────────

    def validate_metric(self, key):
        if key not in BY_KEY:
            raise serializers.ValidationError(f"No metric called {key!r}.")
        return key

    def validate_owner_id(self, owner):
        request = self.context["request"]
        if owner is not None and owner.organisation_id != request.user.organisation_id:
            raise serializers.ValidationError("Owner must be a member of your own organisation.")
        return owner

    def validate(self, attrs):
        metric_key = attrs.get("metric", getattr(self.instance, "metric", None))
        dimension = attrs.get("dimension", getattr(self.instance, "dimension", ""))
        member = attrs.get("member", getattr(self.instance, "member", ""))
        metric = BY_KEY[metric_key]

        if dimension:
            if dimension not in metric.slices:
                raise serializers.ValidationError(
                    {
                        "dimension": f"{metric.label} cannot be cut by {dimension!r}; it can by "
                        f"{', '.join(sorted(metric.slices)) or 'nothing'}."
                    }
                )
            if not member:
                raise serializers.ValidationError({"member": "Pick a member of that cut."})
            organisation = self.context["request"].user.organisation
            members = {m: label for m, label, _v in compute_slice(organisation, metric, dimension)}
            if member not in members:
                raise serializers.ValidationError(
                    {
                        "member": f"No {DIMENSION_LABELS[dimension].lower()} with id {member!r} "
                        "in that cut."
                    }
                )
            attrs["member_label"] = members[member]
        elif member:
            raise serializers.ValidationError({"dimension": "A member needs a dimension."})

        if (
            self.instance is None
            and "target_by" in attrs
            and attrs["target_by"] < timezone.localdate()
        ):
            raise serializers.ValidationError({"target_by": "The target date is in the past."})
        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        # The starting line is captured inside create_initiative, from the
        # registry as it stands today — the same figure the overview shows.
        return initiatives.create_initiative(
            request.user.organisation, request.user, **validated_data
        )

    def update(self, instance, validated_data):
        closing = {Initiative.Status.DONE, Initiative.Status.ABANDONED}
        new_status = validated_data.get("status", instance.status)
        if new_status in closing and instance.status not in closing:
            validated_data["closed_at"] = timezone.now()
        elif new_status not in closing:
            validated_data["closed_at"] = None
        return super().update(instance, validated_data)
