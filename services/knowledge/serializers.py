from rest_framework import serializers

from .models import Contribution, KnowledgeGap, Question


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


class QuestionSerializer(serializers.ModelSerializer):
    customer = serializers.SerializerMethodField()
    asked_by = serializers.SerializerMethodField()
    assignee = serializers.SerializerMethodField()
    answer = ContributionSerializer(read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    days_open = serializers.SerializerMethodField()

    class Meta:
        model = Question
        fields = [
            "id",
            "customer",
            "asked_by",
            "assignee",
            "text",
            "status",
            "status_display",
            "answer",
            "message_id",
            "days_open",
            "created_at",
            "answered_at",
        ]

    @staticmethod
    def _person(user):
        return {"id": user.id, "name": user.name, "function": user.function}

    def get_customer(self, obj):
        return {"id": obj.customer.id, "name": obj.customer.name} if obj.customer_id else None

    def get_asked_by(self, obj):
        return self._person(obj.asked_by)

    def get_assignee(self, obj):
        return self._person(obj.assignee)

    def get_days_open(self, obj):
        from django.utils import timezone

        end = obj.answered_at or timezone.now()
        return (end - obj.created_at).days


class KnowledgeGapSerializer(serializers.ModelSerializer):
    """A gap is a question and a count, never any record's words."""

    customer = serializers.SerializerMethodField()
    assignee = serializers.SerializerMethodField()
    function_display = serializers.CharField(source="get_function_display", read_only=True)

    class Meta:
        model = KnowledgeGap
        fields = [
            "id",
            "subject",
            "customer",
            "function",
            "function_display",
            "assignee",
            "source",
            "times_asked",
            "status",
            "first_asked_at",
            "last_asked_at",
        ]
        read_only_fields = fields

    def get_customer(self, gap):
        return {"id": gap.customer_id, "name": gap.customer.name}

    def get_assignee(self, gap):
        return {"id": gap.assignee.id, "name": gap.assignee.name} if gap.assignee_id else None
