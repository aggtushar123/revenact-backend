from rest_framework import serializers

from .models import MailboxConnection, MailMessage


class MailboxConnectionSerializer(serializers.ModelSerializer):
    """What the person sees about their own connection. Never the credentials."""

    provider_display = serializers.CharField(source="get_provider_display", read_only=True)

    class Meta:
        model = MailboxConnection
        fields = [
            "id",
            "provider",
            "provider_display",
            "address",
            "display_name",
            "status",
            "error",
            "last_synced_at",
            "created_at",
        ]
        read_only_fields = fields


class ComposeSerializer(serializers.Serializer):
    to = serializers.ListField(child=serializers.EmailField(), min_length=1, max_length=20)
    subject = serializers.CharField(max_length=255)
    body = serializers.CharField(max_length=20000)


class MailMessageSerializer(serializers.ModelSerializer):
    """One row of the person's inbox. `account` is the filed copy's parent,
    when the message matched someone in the book; `priority` is that or the
    provider's own importance flag."""

    priority = serializers.BooleanField(read_only=True)
    account = serializers.SerializerMethodField()

    class Meta:
        model = MailMessage
        fields = [
            "id",
            "thread_id",
            "direction",
            "from_name",
            "from_address",
            "to",
            "subject",
            "snippet",
            "sent_at",
            "folder",
            "category",
            "state",
            "is_read",
            "is_starred",
            "is_important",
            "priority",
            "account",
        ]
        read_only_fields = [f for f in fields if f not in ("state", "is_read", "is_starred")]

    def get_account(self, message):
        email = message.email
        if email is None:
            return None
        if email.customer_id is not None:
            return {"id": email.customer_id, "name": email.customer.name, "type": "customer"}
        if email.account_id is not None:
            return {"id": email.account_id, "name": email.account.name, "type": "account"}
        return None


class MailMessageDetailSerializer(MailMessageSerializer):
    class Meta(MailMessageSerializer.Meta):
        fields = MailMessageSerializer.Meta.fields + ["body"]
        read_only_fields = MailMessageSerializer.Meta.read_only_fields + ["body"]


class MailMessageUpdateSerializer(serializers.ModelSerializer):
    """What the person may change here: read, starred, and this product's
    own triage state. Nothing is written back to the provider."""

    class Meta:
        model = MailMessage
        fields = ["is_read", "is_starred", "state"]


class ReplySerializer(serializers.Serializer):
    body = serializers.CharField(max_length=20000)
