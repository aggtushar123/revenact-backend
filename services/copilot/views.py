from django.conf import settings
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from services.accounts.models import Organisation, User
from services.accounts.permissions import CanManageOrgSettings, CanViewAllAccounts
from services.customers.models import Account, Customer, Email
from services.knowledge.gaps import record_unanswered
from services.knowledge.mentions import (
    ask_suggestions_for,
    resolve_routes,
    route_questions,
    routing_summary,
)
from services.mail.models import MailMessage
from services.mail.visibility import visible_emails
from services.notifications.models import Notification
from services.notifications.realtime import notify as send_notification

from .anthropic_client import (
    BudgetExceeded,
    CopilotNotConfigured,
    CopilotRequestFailed,
    get_completion,
)
from .context import build_grounding
from .dashboard_context import DashboardContextSerializer, origin_of
from .dashboard_grounding import build_dashboard_grounding, dashboard_system_prompt
from .models import (
    Conversation,
    CopilotSession,
    Message,
    SessionEvent,
    SessionInvite,
    SessionParticipant,
)
from .realtime import broadcast_session_update
from .retrieval import retrieve_with_sources
from .serializers import (
    ConversationDetailSerializer,
    ConversationListSerializer,
    CopilotSessionSerializer,
    SessionInviteSerializer,
)

MAX_MESSAGE_LENGTH = 8000
HISTORY_WINDOW = 20

TONE_INSTRUCTIONS = {
    Organisation.AgentTone.PROFESSIONAL: "Keep a professional, businesslike tone.",
    Organisation.AgentTone.FRIENDLY: "Keep a warm, friendly, conversational tone.",
    Organisation.AgentTone.CONCISE: "Be concise — short, direct answers, no filler.",
}

SYSTEM_PERSONA = (
    "You are Copilot, an AI assistant built into Revenact, a customer-success "
    "and revenue platform used by the whole company — customer success, "
    "engineering, sales, analytics and leadership. You're given a real-data "
    "summary below: the asker's own customers and accounts if they own any, "
    "and what the company knows about the customer their question is about, "
    "including notes from other functions, each marked with the function and "
    "the person who wrote it. Ground your answers in it and say who said what. "
    "Say plainly when the summary doesn't cover something, and when it doesn't, "
    "suggest asking the person responsible for that function on the account "
    "if the summary names one; never invent a figure, an event or a name."
)


def conversations_visible_to(user):
    """Every Conversation `user` may read or post into — Phase 2a's real
    fix for M0's own "one shared localStorage login" limit (see
    CopilotSession's own docstring): its own, plus any other user's
    whose CopilotSession they've been invited to *and* accepted, and are
    still an active (not-left) participant of. Two separate reverse
    relations (`session__participants`, `session__invites`) joined in
    one filter — each independently matches at most one row per user
    per session (see those models' own unique constraints), so this is
    a real AND, not an accidental OR."""

    return Conversation.objects.filter(
        Q(user=user)
        | Q(
            session__participants__user=user,
            session__participants__left_at__isnull=True,
            session__invites__invited_user=user,
            session__invites__status=SessionInvite.Status.ACCEPTED,
        )
        # Mentioned in it: a question routed to them from one of its turns
        # (services.knowledge) — or to someone who reports to them, directly
        # or through the chain: a manager sees what was asked of their team
        # and what the team replied. They see a slice, not the whole — see
        # visible_messages.
        | Q(messages__questions__assignee_id__in=_me_and_my_reports(user))
    ).distinct()


def _me_and_my_reports(user):
    from services.accounts.hierarchy import subtree_ids

    return {user.id, *subtree_ids(user)}


def sees_whole_conversation(conversation, user):
    """The owner and accepted, present participants see every turn."""
    if conversation.user_id == user.id:
        return True
    session = getattr(conversation, "session", None)
    if session is None:
        return False
    return (
        session.participants.filter(user=user, left_at__isnull=True).exists()
        and session.invites.filter(invited_user=user, status=SessionInvite.Status.ACCEPTED).exists()
    )


def visible_messages(conversation, user):
    """The turns `user` may read. Everything for the owner and participants;
    for someone who is only mentioned, the turns written by people in
    their scope (their team, their reports, leadership above them — see
    services.accounts.hierarchy) that are not addressed to someone else,
    the turns that mention them or anyone who reports to them, their own,
    and the Copilot's replies to those turns. A manager therefore sees what
    was asked of their team and what the team replied."""
    from services.accounts.hierarchy import scope_ids

    turns = list(conversation.messages.select_related("reply_to").order_by("created_at", "id"))
    if sees_whole_conversation(conversation, user):
        return turns
    from services.knowledge.models import Question

    allowed = scope_ids(user)
    # Who each turn routed a question to. A turn addressed to someone else
    # — "@Raj, what did procurement say?" — is theirs, not the room's: a
    # viewer who sees only a slice gets it only if it is addressed to them
    # too, however senior its author.
    addressed = {}
    for message_id, assignee_id in Question.objects.filter(
        message__conversation=conversation
    ).values_list("message_id", "assignee_id"):
        addressed.setdefault(message_id, set()).add(assignee_id)

    mine = _me_and_my_reports(user)
    kept, previous_kept = [], False
    last_user_turn = None
    for turn in turns:
        if turn.role == Message.Role.USER:
            last_user_turn = turn
            targets = addressed.get(turn.id, set())
            if turn.author_id == user.id or targets & mine:
                previous_kept = True
            elif targets:
                previous_kept = False
            else:
                previous_kept = turn.author_id in allowed or turn.author_id is None
            if previous_kept:
                kept.append(turn)
        elif previous_kept:
            # The reply's own `reply_to` is the real question it answers;
            # only a legacy row written before that FK existed falls back to
            # whichever user turn happens to sort immediately before it.
            answered = turn.reply_to if turn.reply_to_id else last_user_turn
            kept.append(turn if _reply_readable_by(turn, user, answered) else _redacted(turn))
    return kept


REDACTED_REPLY = "This reply isn't shared with you: it draws on records outside what you may see."


def _reply_readable_by(turn, user, user_turn):
    """A Copilot reply was written from the asker's scope, not the viewer's.
    It is shown to a viewer who sees only a slice when every record it
    cites is one they could read themselves — a contribution within their
    scope, a customer's record on a customer they may open — and withheld
    otherwise, so a reply cannot quote what its reader may not read.

    A dashboard reply (`user_turn.context` set) also carries aggregates and
    company names drawn from the *asker's* filtered book — totals, top
    lists, facts on companies never individually cited — not just the
    records `turn.sources` names. A partial-visibility viewer therefore
    needs the asker's whole filtered book to be inside their own visible
    customers, not merely the cited records; the asker always reads their
    own dashboard reply regardless. A context-less (Communications) reply
    keeps exactly the per-source checks below for every viewer, the asker
    included — there is no whole-book aggregate to guard there.

    `user_turn` is the real user turn this reply answers (`turn.reply_to`
    when set; the immediately preceding user turn only for a legacy row
    with none — see `visible_messages`), or None."""
    from services.customers.scoping import visible_customers
    from services.knowledge.models import Contribution
    from services.knowledge.views import visible_contributions

    if user_turn is not None and user_turn.context and user_turn.author_id is not None:
        if user_turn.author_id == user.id:
            return True
        from services.customers import forecast

        filters = user_turn.context.get("filters") or {}
        filtered = forecast.filtered_customers(user_turn.author, filters)
        if filtered.exclude(pk__in=visible_customers(user)).exists():
            return False

    for source in turn.sources or []:
        if source.get("type") == "contribution":
            rows = Contribution.objects.filter(pk=source.get("id"))
            if rows.exists() and not visible_contributions(user, rows).exists():
                return False
        elif source.get("type") == "email":
            from services.customers.models import Email
            from services.mail.visibility import visible_emails

            rows = Email.objects.filter(pk=source.get("id"))
            if rows.exists() and not visible_emails(user, rows).exists():
                return False
        elif source.get("type") == "note":
            from services.customers.models import Note
            from services.customers.personal import visible_notes

            rows = Note.objects.filter(pk=source.get("id"))
            if rows.exists() and not visible_notes(user, rows).exists():
                return False
        elif source.get("type") == "ticket":
            from services.customers.models import Ticket
            from services.customers.personal import visible_tickets

            rows = Ticket.objects.filter(pk=source.get("id"))
            if rows.exists() and not visible_tickets(user, rows).exists():
                return False
        elif source.get("company_type") == "customer":
            if not visible_customers(user).filter(pk=source.get("company_id")).exists():
                return False
        elif source.get("company_type") == "account":
            from services.customers.models import Account

            if not Account.objects.filter(
                pk=source.get("company_id"), customers__in=visible_customers(user)
            ).exists():
                return False
    return True


def _redacted(turn):
    """The same turn, with its words withheld — unsaved, never written back."""
    return Message(
        id=turn.id,
        conversation=turn.conversation,
        role=turn.role,
        content=REDACTED_REPLY,
        sources=[],
        ask_suggestions=[],
        created_at=turn.created_at,
    )


class ConversationListView(generics.ListAPIView):
    """GET /api/v1/copilot/conversations/ — every Copilot conversation
    the caller may read: their own, plus the sessions they were invited
    into and accepted and are still present in (`conversations_visible_to`,
    the same rule the detail and send views apply). Not shared org-wide —
    see Conversation's own docstring. Before this used the shared rule an
    accepted participant lost the conversation from their sidebar the
    moment the invite card went away, and had no way back to a session
    they were part of. Powers the sidebar's "Chat history" list.
    Pagination off — same reasoning as ScenarioListCreateView's own: one
    person's conversation list, not meant to be paged through."""

    serializer_class = ConversationListSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return conversations_visible_to(self.request.user)


class ConversationDetailView(generics.RetrieveDestroyAPIView):
    """GET/DELETE /api/v1/copilot/conversations/<id>/ — GET is scoped to
    every conversation `conversations_visible_to` the caller (Phase 2a:
    the owner, or an accepted+active Multiplayer Copilot session
    participant — see that function's own docstring); DELETE stays
    narrower, owner-only (a participant can leave a session, see
    RespondToInviteView, but never deletes someone else's conversation
    outright). No update — a conversation's title and messages are only
    ever set by SendMessageView."""

    serializer_class = ConversationDetailSerializer
    permission_classes = [IsAuthenticated]

    def retrieve(self, request, *args, **kwargs):
        conversation = self.get_object()
        conversation._visible_messages = visible_messages(conversation, request.user)
        conversation._visibility = (
            "full" if sees_whole_conversation(conversation, request.user) else "partial"
        )
        return Response(ConversationDetailSerializer(conversation).data)

    def get_queryset(self):
        if self.request.method == "DELETE":
            return Conversation.objects.filter(user=self.request.user)
        return conversations_visible_to(self.request.user)


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
    backend consumer of either field (see that model's own docstring).

    Phase 2a: `conversation_id` is looked up via `conversations_visible_to`
    rather than owner-only, so a real Multiplayer Copilot session's other
    accepted participants can genuinely post into it too — not just the
    original owner. A closed session refuses new messages (403) but stays
    readable via ConversationDetailView. Every message sent into a
    conversation that already has a session is, by definition, a real
    redirect (the session's own opening query was necessarily the first
    message on that conversation, sent before any session could exist —
    see CopilotSession's own docstring) — logged as a `redirected`
    SessionEvent tagging the real new Message, not re-storing its text.

    Dashboard: an optional `context` ({surface, area, view, filters, focus})
    says where on the Dashboard the question was asked. It is validated by
    DashboardContextSerializer (a 400 `{"context": {...}}` otherwise), the
    answer is grounded by dashboard_grounding in that screen's recomputed
    figures and records, metered as `dashboard`, and the validated context is
    stored on the user turn; the conversation's `origin` is set from the first
    one and never changed."""

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
            conversation = get_object_or_404(
                conversations_visible_to(request.user), pk=conversation_id
            )
            session = getattr(conversation, "session", None)
            if session is not None and session.status == CopilotSession.Status.CLOSED:
                return Response(
                    {"detail": "This session has been closed."}, status=status.HTTP_403_FORBIDDEN
                )

        # A send from the Dashboard says where it was asked; the server
        # recomputes what is there (services/copilot/dashboard_grounding.py).
        # Absent or null, this is the Communications/Copilot send, unchanged.
        dashboard = None
        raw_context = request.data.get("context")
        if raw_context is not None:
            checked = DashboardContextSerializer(data=raw_context, context={"user": request.user})
            if not checked.is_valid():
                return Response({"context": checked.errors}, status=status.HTTP_400_BAD_REQUEST)
            dashboard = checked.validated_data

        default_tone = TONE_INSTRUCTIONS[Organisation.AgentTone.PROFESSIONAL]
        tone_instruction = TONE_INSTRUCTIONS.get(organisation.ai_agent_tone, default_tone)
        if dashboard is not None:
            grounding = build_dashboard_grounding(request.user, dashboard, content)
        else:
            grounding = build_grounding(organisation, user=request.user, query=content)
        # "@Mei, why is usage down?" or "@engineering, does SSO still break?"
        # — the people named, or responsible for the identified customer in
        # the named function, become a routed question once the turn is
        # stored (below); the model is told now, with the reason each was
        # reached, so its reply acknowledges the routing instead of
        # answering for them.
        company = grounding.company
        asked_about = company if company.__class__.__name__ == "Customer" else None
        routes = resolve_routes(content, organisation, exclude=request.user, customer=asked_about)
        asked = [route.user for route in routes]
        routing_note = ""
        if routes:
            routing_note = (
                f"\n\nThe asker has routed this question to "
                f"{routing_summary(routes, asked_about)}; they will be "
                "notified and their answer will be recorded. Acknowledge that in one "
                "sentence, then answer whatever the summary already covers."
            )
        if dashboard is not None:
            system = dashboard_system_prompt(tone_instruction, grounding.summary) + routing_note
        else:
            system = (
                f"{SYSTEM_PERSONA}\n\n{tone_instruction}\n\nReal-data summary:\n"
                f"{grounding.summary}{routing_note}"
            )
        prior_history = (
            [
                {"role": m.role, "content": m.content}
                for m in visible_messages(conversation, request.user)[:HISTORY_WINDOW]
            ]
            if conversation
            else []
        )
        history = [*prior_history, {"role": Message.Role.USER, "content": content}]

        try:
            reply = get_completion(
                system=system,
                messages=history,
                purpose="dashboard" if dashboard is not None else "copilot",
                organisation=request.user.organisation,
                user=request.user,
            )
        except BudgetExceeded as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        except CopilotNotConfigured as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except CopilotRequestFailed as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        if conversation is None:
            conversation = Conversation.objects.create(
                organisation=organisation, user=request.user, title=content[:50]
            )
        user_message = Message.objects.create(
            conversation=conversation,
            role=Message.Role.USER,
            content=content,
            author=request.user,
            context=dashboard,
        )
        Message.objects.create(
            conversation=conversation,
            role=Message.Role.ASSISTANT,
            content=reply,
            # Stored on the turn rather than recomputed on read: the
            # records are what the answer was built from *at the time*,
            # and re-running retrieval later would cite whatever is
            # relevant now instead.
            sources=grounding.sources,
            ask_suggestions=ask_suggestions_for(grounding.company, exclude=request.user),
            # The real turn this reply answers, not just "whichever user turn
            # happens to sort immediately before it" — two participants
            # sending concurrently can interleave a second user turn in
            # between (see _reply_readable_by's own docstring).
            reply_to=user_message,
        )
        # Set once, from the first dashboard message, and never overwritten:
        # the history's tag says where a conversation started. A conditional
        # update, not an in-memory `origin is None` check — two concurrent
        # first sends into the same conversation can't both win the race.
        if dashboard is not None:
            changed = Conversation.objects.filter(pk=conversation.pk, origin__isnull=True).update(
                origin=origin_of(dashboard), updated_at=timezone.now()
            )
            if changed:
                conversation.refresh_from_db(fields=["origin", "updated_at"])
            else:
                conversation.save(update_fields=["updated_at"])
        else:
            conversation.save(update_fields=["updated_at"])

        if asked:
            route_questions(
                organisation=organisation,
                asked_by=request.user,
                text=content,
                customer=asked_about,
                message=user_message,
                assignees=asked,
            )
        elif asked_about is not None and not grounding.sources:
            # The question was about a company and retrieval found nothing
            # to answer it from. That is not a failure of the model, it is
            # something the company does not know about its own customer —
            # see services.knowledge.gaps.
            record_unanswered(
                organisation=organisation,
                customer=asked_about,
                question=content,
                asked_by=request.user,
            )

        session = getattr(conversation, "session", None)
        if session is not None:
            SessionEvent.objects.create(
                session=session,
                kind=SessionEvent.Kind.REDIRECTED,
                actor=request.user,
                message=user_message,
            )
            broadcast_session_update(session)

        conversation._visible_messages = visible_messages(conversation, request.user)
        conversation._visibility = (
            "full" if sees_whole_conversation(conversation, request.user) else "partial"
        )
        return Response(ConversationDetailSerializer(conversation).data)


def _session_subject_label(session):
    """A real, human "about X" fragment for a session's own real
    customer/account context — used only for real notification text
    (see the two call sites below); None when the session has no
    company context at all (a plain New Chat session)."""

    if session.customer_id:
        return session.customer.name
    if session.account_id:
        return session.account.name
    return None


def _same_org_member(organisation, user_id):
    """Real, same-tenant-only lookup for a target user id — same
    discipline as CustomerSerializer.validate_owner_id. None on any
    failure (bad id, wrong tenant); the caller turns that into a 400."""

    try:
        target = User.objects.get(pk=user_id)
    except (User.DoesNotExist, TypeError, ValueError):
        return None
    return target if target.organisation_id == organisation.id else None


def _get_or_create_session(conversation, request_data):
    """Shared lazy-creation for both real entry points into a session's
    own existence — SessionView.post ("Make this a live session") and
    SessionHandoffView.post (hand-off is its own independent way to
    start one, not gated behind clicking Make Live first — see that
    view's own docstring). Both can only ever be reached for a
    session-less conversation by its own owner (conversations_visible_to
    only lets the owner see a conversation with no session), so no
    extra ownership check is needed here.

    Optional `customer_id`/`account_id` from the request body, sourced
    from whatever the frontend's own entry point already knew (see
    CopilotSession's own docstring) — only used the first time a
    session is created for this conversation; ignored on an existing
    one, same as every other "first send" convention in this app."""

    session = getattr(conversation, "session", None)
    if session is not None:
        return session

    customer_id = request_data.get("customer_id")
    account_id = request_data.get("account_id")
    customer = (
        Customer.objects.filter(pk=customer_id, organisation=conversation.organisation).first()
        if customer_id
        else None
    )
    account = (
        Account.objects.filter(
            pk=account_id, customers__organisation=conversation.organisation
        ).first()
        if account_id
        else None
    )
    return CopilotSession.objects.create(
        conversation=conversation, customer=customer, account=account
    )


class SessionView(APIView):
    """GET/POST /api/v1/copilot/conversations/<id>/session/ — the real,
    shared Multiplayer Copilot session state for one Conversation (see
    CopilotSession's own docstring for what "real" fixes here versus
    M0's own frontend-only, one-browser version).

    GET: a snapshot (status, active participants, events since
    `?since_id=`) for the frontend's own polling loop — 404 unless the
    conversation is visible to the caller AND a session actually
    exists. No session existing isn't an error: "explicit opt-in only,
    never automatic" (the same product decision from M0) means most
    conversations never get one.

    POST: owner-only — "Make this a live session". Creates the
    CopilotSession on first call, optionally scoped to a real
    `customer_id`/`account_id` from the request body (sourced from
    whatever the frontend's own entry point already knew — see
    CopilotSession's own docstring on why that's captured here rather
    than assumed), or reactivates an existing non-closed one to
    `status=live`. Auto-adds the owner as an active SessionParticipant
    and logs a `made_live` event."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        conversation = get_object_or_404(conversations_visible_to(request.user), pk=pk)
        session = getattr(conversation, "session", None)
        if session is None:
            return Response(
                {"detail": "This conversation has no live session."},
                status=status.HTTP_404_NOT_FOUND,
            )

        events = session.events.all()
        since_id = request.query_params.get("since_id")
        if since_id is not None:
            try:
                events = events.filter(id__gt=int(since_id))
            except ValueError:
                return Response(
                    {"detail": "since_id must be an integer."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        session._events_page = events

        return Response(CopilotSessionSerializer(session).data)

    def post(self, request, pk):
        conversation = get_object_or_404(Conversation, pk=pk, user=request.user)

        existing = getattr(conversation, "session", None)
        if existing is not None and existing.status == CopilotSession.Status.CLOSED:
            return Response(
                {"detail": "This session has been closed — start a new one from a fresh message."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session = _get_or_create_session(conversation, request.data)
        session.status = CopilotSession.Status.LIVE
        session.save(update_fields=["status"])
        SessionParticipant.objects.update_or_create(
            session=session, user=request.user, defaults={"left_at": None}
        )
        SessionEvent.objects.create(
            session=session, kind=SessionEvent.Kind.MADE_LIVE, actor=request.user
        )
        broadcast_session_update(session)

        session._events_page = session.events.all()
        return Response(CopilotSessionSerializer(session).data)


class SessionInviteCreateView(APIView):
    """POST /api/v1/copilot/conversations/<id>/session/invite/ —
    owner-only. Body: `{"user_id": <id>}`. Invite-only is the real
    access gate (see conversations_visible_to) — this is how someone
    besides the owner ever becomes eligible to join at all. Re-inviting
    a previously-declined user resets them back to pending rather than
    erroring."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        conversation = get_object_or_404(Conversation, pk=pk, user=request.user)
        session = getattr(conversation, "session", None)
        if session is None or session.status == CopilotSession.Status.CLOSED:
            return Response(
                {"detail": "Make this a live session before inviting anyone."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        target = _same_org_member(conversation.organisation, request.data.get("user_id"))
        if target is None:
            return Response(
                {"detail": "Pick a real member of your own organisation."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        invite, _ = SessionInvite.objects.update_or_create(
            session=session,
            invited_user=target,
            defaults={
                "invited_by": request.user,
                "status": SessionInvite.Status.PENDING,
                "responded_at": None,
            },
        )
        subject = _session_subject_label(session)
        message = f"{request.user.name} invited you to a live Copilot session" + (
            f" about {subject}" if subject else ""
        )
        send_notification(
            recipient=target,
            actor=request.user,
            kind=Notification.Kind.COPILOT_INVITE,
            message=message,
            link=f"/copilot?session={conversation.id}",
        )
        return Response(SessionInviteSerializer(invite).data, status=status.HTTP_201_CREATED)


class SessionHandoffView(APIView):
    """POST /api/v1/copilot/conversations/<id>/session/handoff/ —
    anyone the session is already visible to (owner or an active
    participant — see conversations_visible_to), not owner-only: real
    hand-off is meant to happen mid-session, from whoever's currently
    driving it. Body: `{"to_user_id": <id>, "note": "...", "customer_id"?,
    "account_id"?}`. Hand-off is its own independent way for a session to
    start existing — not gated behind "Make this a live session" first
    (see _get_or_create_session's own docstring); a session-less
    conversation can only reach this via its own owner regardless (same
    reasoning as SessionView.post). Also implies invite — creates or
    resets a SessionInvite for the target exactly like
    SessionInviteCreateView, since there's no reason to force a separate
    manual invite step first for the real UX."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        conversation = get_object_or_404(conversations_visible_to(request.user), pk=pk)
        existing = getattr(conversation, "session", None)
        if existing is not None and existing.status == CopilotSession.Status.CLOSED:
            return Response(
                {"detail": "This session has been closed."}, status=status.HTTP_400_BAD_REQUEST
            )
        session = _get_or_create_session(conversation, request.data)
        # Whoever's handing off is, by definition, an active participant
        # — same as SessionView.post auto-adding the owner. Real for a
        # brand-new (handoff-created) session too, not just an existing
        # live one, so the presence strip is never missing its own actor.
        SessionParticipant.objects.update_or_create(
            session=session, user=request.user, defaults={"left_at": None}
        )

        target = _same_org_member(conversation.organisation, request.data.get("to_user_id"))
        if target is None:
            return Response(
                {"detail": "Pick a real member of your own organisation."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        note = (request.data.get("note") or "").strip()

        SessionInvite.objects.update_or_create(
            session=session,
            invited_user=target,
            defaults={
                "invited_by": request.user,
                "status": SessionInvite.Status.PENDING,
                "responded_at": None,
            },
        )
        session.status = CopilotSession.Status.AWAITING_HANDOFF
        session.save(update_fields=["status"])
        SessionEvent.objects.create(
            session=session,
            kind=SessionEvent.Kind.HANDED_OFF,
            actor=request.user,
            payload={"to_user_id": target.id, "to_user_name": target.name, "note": note},
        )
        broadcast_session_update(session)
        subject = _session_subject_label(session)
        message = f"{request.user.name} handed off a Copilot session to you" + (
            f" — {subject}" if subject else ""
        )
        if note:
            message += f': "{note}"'
        send_notification(
            recipient=target,
            actor=request.user,
            kind=Notification.Kind.COPILOT_HANDOFF,
            message=message,
            link=f"/copilot?session={conversation.id}",
        )

        session._events_page = session.events.all()
        return Response(CopilotSessionSerializer(session).data)


class SessionCloseView(APIView):
    """POST /api/v1/copilot/conversations/<id>/session/close/ —
    owner-only. A closed session stops accepting new messages (see
    SendMessageView's own guard) but stays fully readable — real
    history, not deleted.

    Body `{"capture_decisions": true}` runs the facilitator in the same
    request once the session is closed, so the moment a session ends is
    the moment its decisions reach the review queue rather than a button
    someone has to remember. The close always stands: a capture that
    fails (nothing said, budget spent, provider down) comes back as
    `decisions_error` beside the closed session, never as an error that
    undoes the close."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        conversation = get_object_or_404(Conversation, pk=pk, user=request.user)
        session = getattr(conversation, "session", None)
        if session is None:
            return Response(
                {"detail": "This conversation has no live session."},
                status=status.HTTP_404_NOT_FOUND,
            )

        session.status = CopilotSession.Status.CLOSED
        session.closed_at = timezone.now()
        session.save(update_fields=["status", "closed_at"])
        SessionEvent.objects.create(
            session=session, kind=SessionEvent.Kind.CLOSED, actor=request.user
        )
        broadcast_session_update(session)

        session._events_page = session.events.all()
        payload = CopilotSessionSerializer(session).data
        if request.data.get("capture_decisions"):
            payload.update(self._capture(session, request.user))
        return Response(payload)

    @staticmethod
    def _capture(session, user):
        from services.metrics.facilitator import NothingToDecideFrom, capture_decisions
        from services.metrics.proposals import NothingToProposeFrom
        from services.metrics.views import _proposal_payload

        try:
            stored = capture_decisions(session, requested_by=user)
        except (
            NothingToDecideFrom,
            NothingToProposeFrom,
            BudgetExceeded,
            CopilotNotConfigured,
            CopilotRequestFailed,
            ValueError,
        ) as exc:
            return {"decisions": [], "decisions_error": str(exc)}
        return {"decisions": [_proposal_payload(p) for p in stored], "decisions_error": None}


class SessionDecisionsView(APIView):
    """GET/POST /api/v1/copilot/conversations/<id>/session/decisions/ —
    the facilitator. POST reads the session (transcript with authors,
    participants, hand-offs) beside the Ops agent's figures and writes what
    the people decided into the review queue as proposals tagged with this
    session; GET lists the ones already written. Anyone who can read the
    conversation may ask — the decisions were theirs — but approving still
    happens in the review queue, under its own permission. A real, paid
    call; error mapping as the Ops agent's, plus `422` for a session where
    nobody has said anything."""

    permission_classes = [IsAuthenticated]

    def _session(self, request, pk):
        conversation = get_object_or_404(conversations_visible_to(request.user), pk=pk)
        session = getattr(conversation, "session", None)
        if session is None:
            raise Http404("This conversation has no live session.")
        return session

    def get(self, request, pk):
        from services.metrics.views import _proposal_payload

        session = self._session(request, pk)
        rows = session.proposals.select_related(
            "initiative", "decided_by", "generated_by", "session__conversation"
        ).order_by("id")
        return Response({"proposals": [_proposal_payload(p) for p in rows]})

    def post(self, request, pk):
        from services.metrics.facilitator import NothingToDecideFrom, capture_decisions
        from services.metrics.proposals import NothingToProposeFrom
        from services.metrics.views import _proposal_payload

        session = self._session(request, pk)
        try:
            stored = capture_decisions(session, requested_by=request.user)
        except (NothingToDecideFrom, NothingToProposeFrom) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        except BudgetExceeded as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        except CopilotNotConfigured as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except (CopilotRequestFailed, ValueError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(
            {"proposals": [_proposal_payload(p) for p in stored]}, status=status.HTTP_201_CREATED
        )


class MyInvitesView(generics.ListAPIView):
    """GET /api/v1/copilot/sessions/invites/ — the caller's own pending
    Multiplayer Copilot invites, real data behind what used to be
    M0's own locally-derived "Handed off to you" sidebar section.
    Deliberately visible before the invitee has accepted (and so before
    conversations_visible_to would let them near the conversation
    itself) — SessionInviteSerializer carries just enough real context
    (who invited them, which real company) to decide without it."""

    serializer_class = SessionInviteSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return SessionInvite.objects.filter(
            invited_user=self.request.user, status=SessionInvite.Status.PENDING
        )


class RespondToInviteView(APIView):
    """POST /api/v1/copilot/sessions/invites/<id>/respond/ — the
    invitee themselves only. Body: `{"status": "accepted"|"declined"}`.
    Accepting is what actually grants access (see
    conversations_visible_to) — it creates or reactivates the real
    SessionParticipant row and logs a `joined` event; declining just
    records the answer."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        invite = get_object_or_404(SessionInvite, pk=pk, invited_user=request.user)
        new_status = request.data.get("status")
        if new_status not in (SessionInvite.Status.ACCEPTED, SessionInvite.Status.DECLINED):
            return Response(
                {"detail": "status must be 'accepted' or 'declined'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        invite.status = new_status
        invite.responded_at = timezone.now()
        invite.save(update_fields=["status", "responded_at"])

        if new_status == SessionInvite.Status.ACCEPTED:
            SessionParticipant.objects.update_or_create(
                session=invite.session, user=request.user, defaults={"left_at": None}
            )
            SessionEvent.objects.create(
                session=invite.session,
                kind=SessionEvent.Kind.JOINED,
                actor=request.user,
            )
            broadcast_session_update(invite.session)

        return Response(SessionInviteSerializer(invite).data)


class ModelUsageView(APIView):
    """GET /api/v1/copilot/usage/ — what the brain has spent this month, per
    purpose, against its budget, plus the last fifty calls. The audit trail
    for every model call the codebase makes. Management-facing."""

    permission_classes = [CanViewAllAccounts]

    def get(self, request):
        from . import usage
        from .models import ModelCall

        organisation = request.user.organisation
        recent = (
            ModelCall.objects.filter(organisation=organisation)
            .select_related("user")
            .order_by("-created_at")[:50]
        )
        return Response(
            {
                **usage.summary(organisation),
                "default_budget": settings.MODEL_BUDGET_DEFAULT_TOKENS,
                "recent": [
                    {
                        "id": call.id,
                        "purpose": call.purpose,
                        "purpose_label": usage.PURPOSES.get(call.purpose, call.purpose),
                        "user": call.user.name if call.user else None,
                        "model": call.model,
                        "input_tokens": call.input_tokens,
                        "output_tokens": call.output_tokens,
                        "latency_ms": call.latency_ms,
                        "outcome": call.outcome,
                        "error": call.error,
                        "created_at": call.created_at.isoformat(),
                    }
                    for call in recent
                ],
            }
        )


class SkillsView(APIView):
    """GET /api/v1/copilot/skills/ — the catalogue of what the brain's
    agents may do (`services/copilot/skills.py`), each with this month's
    usage against its budget, its last run and what it has produced.
    Organisation-wide, so gated like the usage view."""

    permission_classes = [CanViewAllAccounts]

    def get(self, request):
        from .skills import catalogue

        return Response(catalogue(request.user.organisation))


class ModelBudgetView(APIView):
    """PATCH /api/v1/copilot/usage/budgets/ — set one purpose's monthly token
    budget for this organisation (`{"purpose": ..., "monthly_tokens": n}`),
    or clear it back to the default with `monthly_tokens: null`. Spend is
    organisation configuration, so `manage_org_settings`."""

    permission_classes = [CanManageOrgSettings]

    def patch(self, request):
        from . import usage
        from .models import ModelBudget

        purpose = request.data.get("purpose")
        if purpose not in usage.PURPOSES:
            return Response(
                {"detail": f"No purpose {purpose!r}; one of {', '.join(usage.PURPOSES)}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        organisation = request.user.organisation
        raw = request.data.get("monthly_tokens")
        if raw is None:
            ModelBudget.objects.filter(organisation=organisation, purpose=purpose).delete()
        else:
            try:
                tokens = int(raw)
            except (TypeError, ValueError):
                return Response({"detail": "monthly_tokens must be a whole number."}, status=400)
            if tokens < 0:
                return Response({"detail": "monthly_tokens cannot be negative."}, status=400)
            ModelBudget.objects.update_or_create(
                organisation=organisation, purpose=purpose, defaults={"monthly_tokens": tokens}
            )
        return Response(usage.summary(organisation))


#: How many records of the account's history a draft may lean on.
DRAFT_SOURCES = 8


def _org_emails(user):
    """Every filed email the person may read: their organisation's, then
    the mailbox rule (owner and management chain) on top."""
    organisation = user.organisation
    rows = Email.objects.filter(
        Q(customer__organisation=organisation) | Q(account__customers__organisation=organisation)
    ).distinct()
    return visible_emails(user, rows)


class DraftReplyView(APIView):
    """POST /api/v1/copilot/draft-reply/ {kind: email|mail_message, id}

    A reply written as the person, from the thread and the account's
    history, with the records it leaned on. Nothing is sent and no
    conversation is stored: the draft lands in the reply box for the
    person to edit and send. Charged as a model call (`draft_reply`).

    `email` is a filed `customers.Email` under the mailbox visibility rule;
    `mail_message` is a row of the person's own inbox, owner only.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        kind = request.data.get("kind")
        pk = request.data.get("id")
        if kind not in ("email", "mail_message") or not pk:
            return Response(
                {"detail": "kind must be email or mail_message, with an id."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        organisation = request.user.organisation
        if not organisation.ai_agent_enabled:
            return Response(
                {"detail": "AI Copilot is disabled for your organisation."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if kind == "email":
            email = get_object_or_404(_org_emails(request.user), pk=pk)
            company = email.customer or email.account
            subject = email.subject
            thread = (
                list(
                    _org_emails(request.user)
                    .filter(mailbox=email.mailbox, thread_id=email.thread_id)
                    .order_by("sent_at")
                )
                if email.mailbox_id and email.thread_id
                else [email]
            )
            thread_text = "\n\n".join(
                f"{e.sender_name or e.from_address} ({e.sent_at:%Y-%m-%d}): {e.body}"
                for e in thread
            )
            sender = email.sender_name or email.from_address
        else:
            row = get_object_or_404(MailMessage.objects.filter(owner=request.user), pk=pk)
            company = (row.email.customer or row.email.account) if row.email_id else None
            subject = row.subject
            sender = row.from_name or row.from_address
            thread_text = f"{sender} ({row.sent_at:%Y-%m-%d}): {row.body or row.snippet}"

        items = (
            retrieve_with_sources(
                company, DRAFT_SOURCES, query=f"{subject}\n{thread_text}", viewer=request.user
            )
            if company is not None
            else []
        )
        history = "\n".join(item.line for item in items) or "none on record"
        tone = TONE_INSTRUCTIONS.get(
            organisation.ai_agent_tone, TONE_INSTRUCTIONS[Organisation.AgentTone.PROFESSIONAL]
        )
        first_name = (request.user.name or "").split(" ")[0]
        system = (
            f"{SYSTEM_PERSONA}\n\n{tone}\n\n"
            f"You are drafting a reply that {request.user.name} will send from their own "
            f"mailbox to {sender}. Write only the body of the reply, in plain text, in the "
            f"first person as {request.user.name}, and sign off with '{first_name}'. No subject "
            "line, no preamble, no placeholders. Use only what the thread and the account "
            "history say; where something is not known, say you will confirm it rather than "
            "inventing it.\n\n"
            f"Subject: {subject}\n\nThread:\n{thread_text}\n\n"
            f"Account history{f' ({company.name})' if company is not None else ''}:\n{history}"
        )
        try:
            draft = get_completion(
                system=system,
                messages=[{"role": "user", "content": "Draft the reply to the latest message."}],
                purpose="draft_reply",
                organisation=organisation,
                user=request.user,
            )
        except BudgetExceeded as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        except CopilotNotConfigured as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except CopilotRequestFailed as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({"draft": draft.strip(), "sources": [item.source for item in items]})
