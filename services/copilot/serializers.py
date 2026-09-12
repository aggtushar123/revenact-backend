from rest_framework import serializers

from .models import (
    Conversation,
    CopilotSession,
    Message,
    SessionEvent,
    SessionInvite,
    SessionParticipant,
)


class MessageSerializer(serializers.ModelSerializer):
    """`sources` is always present but empty on user turns, so the
    client can render citations without branching on role first."""

    questions = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = ["id", "role", "content", "sources", "questions", "ask_suggestions", "created_at"]

    def get_questions(self, obj):
        # The people this turn routed a question to (services.knowledge) —
        # empty on assistant turns and on turns that mentioned nobody.
        return [
            {
                "id": q.id,
                "assignee": {"id": q.assignee.id, "name": q.assignee.name},
                "status": q.status,
            }
            for q in obj.questions.select_related("assignee").order_by("id")
        ]


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


class _ActorSerializer(serializers.Serializer):
    """A minimal `{id, name}` for whoever did something — every
    Session*/Event serializer below nests this rather than the full
    UserSerializer (avatar/role/etc.) other apps use — nothing here
    needs more than a label."""

    id = serializers.IntegerField()
    name = serializers.CharField()


class SessionParticipantSerializer(serializers.ModelSerializer):
    user = _ActorSerializer()

    class Meta:
        model = SessionParticipant
        fields = ["user", "joined_at", "left_at"]


class SessionEventSerializer(serializers.ModelSerializer):
    actor = _ActorSerializer(allow_null=True)
    message = MessageSerializer(allow_null=True)

    class Meta:
        model = SessionEvent
        fields = ["id", "kind", "actor", "message", "payload", "created_at"]


class CopilotSessionSerializer(serializers.ModelSerializer):
    """The real session snapshot GET .../session/ returns — participants
    (currently present, per SessionParticipant.left_at) and events since
    whatever `?since_id=` the poller asked for (annotated onto the
    instance by the view, not derived here — see SessionDetailView)."""

    owner = _ActorSerializer(source="conversation.user")
    participants = serializers.SerializerMethodField()
    events = serializers.SerializerMethodField()
    customer_name = serializers.SerializerMethodField()
    account_name = serializers.SerializerMethodField()

    class Meta:
        model = CopilotSession
        fields = [
            "id",
            "conversation_id",
            "owner",
            "customer_id",
            "customer_name",
            "account_id",
            "account_name",
            "status",
            "participants",
            "events",
            "created_at",
            "closed_at",
        ]

    def get_participants(self, obj):
        # Currently present, not everyone who's ever joined — someone
        # who's left stays a real historical SessionParticipant row
        # (see that model's own docstring) but drops out of the live
        # presence strip this powers.
        return SessionParticipantSerializer(
            obj.participants.filter(left_at__isnull=True), many=True
        ).data

    def get_events(self, obj):
        # `_events_page` is set by the view from the real `?since_id=`
        # filter — falling back to every event only protects against a
        # serializer used outside that view (e.g. a shell/admin check).
        events = getattr(obj, "_events_page", None)
        if events is None:
            events = obj.events.all()
        return SessionEventSerializer(events, many=True).data

    def get_customer_name(self, obj):
        return obj.customer.name if obj.customer_id else None

    def get_account_name(self, obj):
        return obj.account.name if obj.account_id else None


class SessionInviteSerializer(serializers.ModelSerializer):
    """What GET .../sessions/invites/ returns — real context (who
    invited you, which real company this is about) without the
    invitee needing accepted access to the conversation itself yet;
    accepting is exactly what grants that (see RespondToInviteView)."""

    invited_by = _ActorSerializer(allow_null=True)
    conversation_id = serializers.IntegerField(source="session.conversation_id", read_only=True)
    conversation_title = serializers.CharField(source="session.conversation.title", read_only=True)
    account_label = serializers.SerializerMethodField()

    class Meta:
        model = SessionInvite
        fields = [
            "id",
            "status",
            "invited_by",
            "conversation_id",
            "conversation_title",
            "account_label",
            "created_at",
        ]

    def get_account_label(self, obj):
        session = obj.session
        if session.customer_id:
            return session.customer.name
        if session.account_id:
            return session.account.name
        return None
