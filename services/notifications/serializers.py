from rest_framework import serializers

from .models import Notification


class _ActorSerializer(serializers.Serializer):
    """A minimal `{id, name}` — same convention as
    services.copilot.serializers's own, and for the same reason: nothing
    here needs the full UserSerializer (avatar/role/etc.)."""

    id = serializers.IntegerField()
    name = serializers.CharField()


class NotificationSerializer(serializers.ModelSerializer):
    actor = _ActorSerializer(allow_null=True)

    class Meta:
        model = Notification
        fields = ["id", "kind", "message", "link", "actor", "is_read", "created_at"]
