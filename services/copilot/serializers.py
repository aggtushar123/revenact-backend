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
    author = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            "id",
            "role",
            "content",
            "author",
            "sources",
            "questions",
            "ask_suggestions",
            "context",
            "created_at",
        ]

    def get_author(self, obj):
        # Who wrote a user turn; null on assistant turns.
        if obj.author_id is None:
            return None
        return {"id": obj.author.id, "name": obj.author.name, "function": obj.author.function}

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
        fields = ["id", "title", "origin", "created_at", "updated_at"]


class ConversationDetailSerializer(serializers.ModelSerializer):
    """`messages` are the turns the caller may read — set on the instance
    by the view (`_visible_messages`, see copilot.views.visible_messages);
    every turn when read outside a view. `visibility` says whether that is
    the whole conversation ("full") or the slice a mentioned person gets
    ("partial"), so the screen can say so."""

    messages = serializers.SerializerMethodField()
    visibility = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = ["id", "title", "origin", "messages", "visibility", "created_at", "updated_at"]

    def get_messages(self, obj):
        turns = getattr(obj, "_visible_messages", None)
        if turns is None:
            turns = obj.messages.all()
        return MessageSerializer(turns, many=True).data

    def get_visibility(self, obj):
        return getattr(obj, "_visibility", "full")


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


class _MessageRefSerializer(serializers.ModelSerializer):
    """Which turn an event is about — never what it says. Session state
    reaches everyone conversations_visible_to admits, including people who
    are only mentioned and may read just a slice of the turns; the text
    travels only through ConversationDetailSerializer.messages, which
    applies visible_messages. A client that wants the turn refetches the
    conversation."""

    class Meta:
        model = Message
        fields = ["id", "role", "created_at"]


class SessionEventSerializer(serializers.ModelSerializer):
    """`payload.note` (a hand-off's free text from the owner) is shown
    only when the context says `sees_note` — the owner and participants
    on the per-viewer poll — and is null otherwise, the WebSocket push
    included (see CopilotSessionSerializer)."""

    actor = _ActorSerializer(allow_null=True)
    message = _MessageRefSerializer(allow_null=True)
    payload = serializers.SerializerMethodField()

    class Meta:
        model = SessionEvent
        fields = ["id", "kind", "actor", "message", "payload", "created_at"]

    def get_payload(self, obj):
        payload = dict(obj.payload or {})
        if "note" in payload and not self.context.get("sees_note", False):
            payload["note"] = None
        return payload


class CopilotSessionSerializer(serializers.ModelSerializer):
    """The real session snapshot GET .../session/ returns — participants
    (currently present, per SessionParticipant.left_at) and events since
    whatever `?since_id=` the poller asked for (annotated onto the
    instance by the view, not derived here — see SessionView). The same
    snapshot is pushed over the WebSocket (realtime.py), so it carries no
    turn text: each event's `message` is a reference ({id, role,
    created_at}) — see _MessageRefSerializer.

    Per viewer: pass `context={"viewer": user}`. A hand-off note is shown
    only to a viewer who sees the whole conversation (owner, accepted
    present participant); `customer_name`/`account_name` only to a viewer
    who may open that customer/account (visible_customers /
    visible_accounts), null otherwise. Without a viewer — the one-group
    WebSocket broadcast — the note and both names are always null; the
    ids stay, and clients read the rest from the per-viewer REST poll."""

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
        return SessionEventSerializer(
            events, many=True, context={"sees_note": self._sees_note(obj)}
        ).data

    def _sees_note(self, obj):
        from .views import sees_whole_conversation

        viewer = self.context.get("viewer")
        return viewer is not None and sees_whole_conversation(obj.conversation, viewer)

    def get_customer_name(self, obj):
        from services.customers.scoping import visible_customers

        viewer = self.context.get("viewer")
        if not obj.customer_id or viewer is None:
            return None
        if not visible_customers(viewer).filter(pk=obj.customer_id).exists():
            return None
        return obj.customer.name

    def get_account_name(self, obj):
        from services.customers.scoping import visible_accounts

        viewer = self.context.get("viewer")
        if not obj.account_id or viewer is None:
            return None
        if not visible_accounts(viewer).filter(pk=obj.account_id).exists():
            return None
        return obj.account.name


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
