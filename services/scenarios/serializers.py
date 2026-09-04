from rest_framework import serializers

from services.customers.models import Customer

from .models import Scenario, ScenarioRun


class ScenarioSerializer(serializers.ModelSerializer):
    """Read/write. `nodes`/`edges` round-trip verbatim — see Scenario's
    own docstring on why they're untyped JSON rather than serializer
    fields. `apply_to_display` is the human label ("Organizations", not
    "organizations") the header's radio labels already render; `apply_to`
    itself stays too, since the frontend keys the radio selection off
    the raw value the same way Opportunity's `stage`/`stage_display`
    pair works."""

    apply_to_display = serializers.CharField(source="get_apply_to_display", read_only=True)

    class Meta:
        model = Scenario
        fields = [
            "id",
            "name",
            "apply_to",
            "apply_to_display",
            "nodes",
            "edges",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class ScenarioRunCustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = ["id", "name"]


class ScenarioRunSerializer(serializers.ModelSerializer):
    """Read-only — a run is a fact about what already happened, created
    by engine.run_scenario, never by a client PATCHing one directly."""

    customer = ScenarioRunCustomerSerializer(read_only=True)

    class Meta:
        model = ScenarioRun
        fields = [
            "id",
            "scenario",
            "customer",
            "triggered_by",
            "status",
            "log",
            "started_at",
            "finished_at",
        ]
