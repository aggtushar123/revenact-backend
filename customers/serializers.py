from rest_framework import serializers

from accounts.models import User
from accounts.serializers import UserSerializer

from .models import Customer


class CustomerSerializer(serializers.ModelSerializer):
    """Read: owner nested (id/name/avatar/role/...). Write: owner_id,
    validated against the caller's own organisation in the view (a
    Customer can't be assigned to a CSM from a different tenant)."""

    health_category = serializers.ChoiceField(
        choices=Customer.HealthCategory.choices, read_only=True
    )
    owner = UserSerializer(read_only=True)
    owner_id = serializers.PrimaryKeyRelatedField(
        source="owner",
        queryset=User.objects.all(),
        write_only=True,
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Customer
        fields = [
            "id",
            "name",
            "health_score",
            "health_category",
            "arr",
            "renewal_date",
            "lifecycle_stage",
            "owner",
            "owner_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def validate_owner_id(self, owner):
        request = self.context["request"]
        if owner is not None and owner.organisation_id != request.user.organisation_id:
            raise serializers.ValidationError("Owner must be a member of your own organisation.")
        return owner

    def create(self, validated_data):
        validated_data["organisation"] = self.context["request"].user.organisation
        return super().create(validated_data)
