from rest_framework import serializers

from .models import Campaign


class CampaignSerializer(serializers.ModelSerializer):
    """`recipients` is read-only here — a real M2M to Contact, but
    writing it takes a list of ids, not nested objects, so that's
    handled in the view layer (`recipient_ids` read straight off raw
    request data, same "not a real serializer field" convention
    Survey/Canvas's own flat create views already use for
    `customer_id`/`account_id`, generalized to a list here). `nodes`/
    `edges`-style round-tripping doesn't apply here — there's nothing
    for the frontend to own the shape of; `recipients` is just real
    Contact rows, and `send_log` is write-once by CampaignSendView."""

    status_display = serializers.CharField(source="get_status_display", read_only=True)
    recipients = serializers.SerializerMethodField()
    sent_count = serializers.SerializerMethodField()
    skipped_count = serializers.SerializerMethodField()

    class Meta:
        model = Campaign
        fields = [
            "id",
            "name",
            "subject",
            "body",
            "status",
            "status_display",
            "recipients",
            "send_log",
            "sent_count",
            "skipped_count",
            "sent_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["status", "send_log", "sent_at", "created_at", "updated_at"]

    def get_recipients(self, obj):
        return [{"id": c.id, "name": c.name, "email": c.email} for c in obj.recipients.all()]

    def get_sent_count(self, obj):
        return sum(1 for entry in obj.send_log if entry.get("status") == "sent")

    def get_skipped_count(self, obj):
        return sum(1 for entry in obj.send_log if entry.get("status") == "skipped")
