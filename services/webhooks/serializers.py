from rest_framework import serializers

from .engine import UnsafeWebhookURLError, validate_webhook_url
from .models import WebhookDelivery, WebhookSubscription


class WebhookDeliverySerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookDelivery
        fields = ["id", "success", "status_code", "error", "sent_at"]


class WebhookSubscriptionSerializer(serializers.ModelSerializer):
    """`secret` is generated server-side (see the model's own
    `_generate_secret` default) and never client-settable — it's
    returned on every read since this whole endpoint is already
    admin-only (see WebhookListCreateView/WebhookDetailView), not
    shown-once like some webhook platforms do it. `event_display` is
    the human label ("Organization Created", not "customer.created"),
    same `_display` convention as Scenario/Opportunity/Risk's own."""

    event_display = serializers.CharField(source="get_event_display", read_only=True)
    recent_deliveries = serializers.SerializerMethodField()

    def get_recent_deliveries(self, obj):
        # Most recent 10 (Meta.ordering on WebhookDelivery is already
        # newest-first) — a webhook that's been failing for months
        # shouldn't make this endpoint's response grow without bound.
        return WebhookDeliverySerializer(obj.deliveries.all()[:10], many=True).data

    class Meta:
        model = WebhookSubscription
        fields = [
            "id",
            "url",
            "event",
            "event_display",
            "secret",
            "is_active",
            "created_at",
            "recent_deliveries",
        ]
        read_only_fields = ["id", "secret", "created_at"]

    def validate_url(self, value):
        try:
            validate_webhook_url(value)
        except UnsafeWebhookURLError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return value
