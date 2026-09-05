from rest_framework import serializers

from .models import Conversation, Message


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = ["id", "role", "content", "created_at"]


class ConversationListSerializer(serializers.ModelSerializer):
    """No nested `messages` — powers the sidebar's own chat-history list,
    which only ever shows a title and a relative time, never a preview
    of the content itself."""

    class Meta:
        model = Conversation
        fields = ["id", "title", "created_at", "updated_at"]


class ConversationDetailSerializer(serializers.ModelSerializer):
    messages = MessageSerializer(many=True, read_only=True)

    class Meta:
        model = Conversation
        fields = ["id", "title", "messages", "created_at", "updated_at"]
