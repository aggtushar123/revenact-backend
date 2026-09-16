from rest_framework import serializers

from .models import MailboxConnection


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
