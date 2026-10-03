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
    answer = serializers.SerializerMethodField()
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
        if not obj.customer_id:
            return None
        # SOC2:AUTH-02 a question is read more widely than its customer (the
        # asker's chart, the assignee's managers): its customer is named only
        # to a reader who may open it. No reader in context (a shell, a
        # script) reads it as before.
        visible = self._visible_customer_ids()
        if visible is not None and obj.customer_id not in visible:
            return None
        return {"id": obj.customer.id, "name": obj.customer.name}

    def _visible_customer_ids(self):
        """The reader's visible customer ids, read once per response (the
        list's rows share this context); None for no reader or one who sees
        every customer."""
        request = self.context.get("request")
        viewer = getattr(request, "user", None) or self.context.get("viewer")
        if viewer is None or not viewer.is_authenticated:
            return None
        if "_visible_customer_ids" not in self.context:
            from services.customers.scoping import sees_everything, visible_customers

            self.context["_visible_customer_ids"] = (
                None
                if sees_everything(viewer)
                else set(visible_customers(viewer).values_list("pk", flat=True))
            )
        return self.context["_visible_customer_ids"]

    def get_answer(self, obj):
        if obj.answer is None:
            return None
        data = ContributionSerializer(obj.answer).data
        # SOC2:AUTH-02 the answer is filed on the question's customer: a reader
        # the question does not name it to (get_customer) gets it unnamed
        if obj.customer_id and self.get_customer(obj) is None:
            data["customer_id"] = None
            data["customer_name"] = None
        return data

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
