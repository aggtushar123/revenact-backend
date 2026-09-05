from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from services.accounts.models import Organisation

from .anthropic_client import CopilotNotConfigured, CopilotRequestFailed, get_completion
from .context import build_org_context_summary
from .models import Conversation, Message
from .serializers import ConversationDetailSerializer, ConversationListSerializer

MAX_MESSAGE_LENGTH = 8000
HISTORY_WINDOW = 20

TONE_INSTRUCTIONS = {
    Organisation.AgentTone.PROFESSIONAL: "Keep a professional, businesslike tone.",
    Organisation.AgentTone.FRIENDLY: "Keep a warm, friendly, conversational tone.",
    Organisation.AgentTone.CONCISE: "Be concise — short, direct answers, no filler.",
}

SYSTEM_PERSONA = (
    "You are Copilot, an AI assistant built into Revenact, a customer-success "
    "and revenue platform. You help CSMs and account managers understand "
    "their book of business — customer health, pipeline, tickets, risk. "
    "You're given a real-data summary of the caller's own organisation "
    "below; ground your answers in it, and say so plainly when a question "
    "asks about something the summary doesn't cover rather than guessing."
)


class ConversationListView(generics.ListAPIView):
    """GET /api/v1/copilot/conversations/ — every Copilot conversation
    the caller has started, private to them (not shared org-wide — see
    Conversation's own docstring). Powers the sidebar's "Chat history"
    list. Pagination off — same reasoning as ScenarioListCreateView's
    own: one CSM's own conversation list, not meant to be paged through."""

    serializer_class = ConversationListSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return Conversation.objects.filter(user=self.request.user)


class ConversationDetailView(generics.RetrieveDestroyAPIView):
    """GET/DELETE /api/v1/copilot/conversations/<id>/ — scoped to the
    caller's own conversations. No update — a conversation's title and
    messages are only ever set by SendMessageView."""

    serializer_class = ConversationDetailSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Conversation.objects.filter(user=self.request.user)


class SendMessageView(APIView):
    """POST /api/v1/copilot/messages/ — the real send. Body:
    `{"conversation_id": <id>?, "content": "..."}`. Runs synchronously,
    in-request — no task queue or streaming exists in this codebase (see
    anthropic_client's own docstring) — the response IS the completed
    turn, assistant reply included.

    Nothing is written to the database until the Anthropic call actually
    succeeds — a failed send (not configured, rate limited, ...) leaves
    no trace: no orphaned Conversation invisible to the sidebar, no
    stray user Message with no reply. Same "don't record a failed
    action as if it happened" discipline as CampaignSendView's own
    (a failed recipient never gets an Email row) — surfaced by live
    testing during Copilot Tier 0's own rollout, where an eagerly-
    created Conversation from a 503 send sat in the database with no
    way for the frontend to ever learn its id.

    Creates a new Conversation (titled from this first message) when
    `conversation_id` is omitted, same "lazy-create on first real
    content" convention as Canvas/Campaign's own editors — just
    deferred until after the real reply comes back, not before. Reads
    `Organisation.ai_agent_enabled`/`ai_agent_tone` for real — the first
    backend consumer of either field (see that model's own docstring)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        organisation = request.user.organisation
        content = (request.data.get("content") or "").strip()

        if not content:
            return Response(
                {"detail": "Message can't be empty."}, status=status.HTTP_400_BAD_REQUEST
            )
        if len(content) > MAX_MESSAGE_LENGTH:
            return Response(
                {"detail": f"Message is too long (max {MAX_MESSAGE_LENGTH} characters)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not organisation.ai_agent_enabled:
            return Response(
                {
                    "detail": "AI Copilot is disabled for your organisation — "
                    "enable it in Settings > AI Agent."
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        conversation_id = request.data.get("conversation_id")
        conversation = None
        if conversation_id:
            conversation = get_object_or_404(Conversation, pk=conversation_id, user=request.user)

        default_tone = TONE_INSTRUCTIONS[Organisation.AgentTone.PROFESSIONAL]
        tone_instruction = TONE_INSTRUCTIONS.get(organisation.ai_agent_tone, default_tone)
        system = (
            f"{SYSTEM_PERSONA}\n\n"
            f"{tone_instruction}\n\n"
            f"Organisation data summary:\n{build_org_context_summary(organisation)}"
        )
        prior_history = (
            [
                {"role": m.role, "content": m.content}
                for m in conversation.messages.order_by("created_at", "id")[:HISTORY_WINDOW]
            ]
            if conversation
            else []
        )
        history = [*prior_history, {"role": Message.Role.USER, "content": content}]

        try:
            reply = get_completion(system=system, messages=history)
        except CopilotNotConfigured as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except CopilotRequestFailed as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        if conversation is None:
            conversation = Conversation.objects.create(
                organisation=organisation, user=request.user, title=content[:50]
            )
        Message.objects.create(conversation=conversation, role=Message.Role.USER, content=content)
        Message.objects.create(
            conversation=conversation, role=Message.Role.ASSISTANT, content=reply
        )
        conversation.save(update_fields=["updated_at"])

        return Response(ConversationDetailSerializer(conversation).data)
