from rest_framework import serializers

from services.accounts.models import User
from services.accounts.serializers import UserSerializer

from .models import Account, Customer


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
    """Read-only for now (see AccountListView) — no `owner_id`/create/
    update yet. Shaped to mirror CustomerSerializer's own conventions
    (nested owner, derived health_category) since an account's health/
    lifecycle mean the same thing as a customer's, just at a finer grain."""

    health_category = serializers.ChoiceField(
        choices=Customer.HealthCategory.choices, read_only=True
    )
    owner = UserSerializer(read_only=True)

    class Meta:
        model = Account
        fields = [
            "id",
            "customer",
            "name",
            "domain",
            "owner",
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
