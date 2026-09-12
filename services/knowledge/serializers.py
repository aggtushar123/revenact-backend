from rest_framework import serializers

from .models import Contribution


class ContributionSerializer(serializers.ModelSerializer):
    author = serializers.SerializerMethodField()
    function_display = serializers.CharField(source="get_function_display", read_only=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True)

    class Meta:
        model = Contribution
        fields = [
            "id",
            "customer_id",
            "customer_name",
            "author",
            "function",
            "function_display",
            "body",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["function", "created_at", "updated_at"]

    def get_author(self, obj):
        return {"id": obj.author.id, "name": obj.author.name}

    def validate_body(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Say something.")
        return value
