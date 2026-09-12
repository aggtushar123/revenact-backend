from django.conf import settings
from django.db import models


class Conversation(models.Model):
    """One Copilot chat thread — the first real AI/LLM feature in this
    codebase (see services.copilot.anthropic_client). Scoped to one
    `User`, not shared org-wide: each CSM's own Copilot history is
    theirs alone, the same way a person's own chat history with any
    assistant is private to them, not pooled across their whole team.

    Its own app, same "tenant-wide, not owned by one Customer/Account"
    reasoning as scenarios/campaigns — a conversation isn't about one
    company, it's a CSM's own working session that may touch many.

    Lazily created: `SendMessageView` creates one on the first message
    a user actually sends, same "no ghost rows for content that was
    never really started" convention as Canvas/Campaign's own editors
    (POST on first Save, not on page load). `title` is derived from
    that first message's own text (see SendMessageView), not editable
    by the user — there's no rename UI, matching the frontend's own
    sidebar, which has never offered one."""

    organisation = models.ForeignKey(
        "accounts.Organisation", related_name="copilot_conversations", on_delete=models.CASCADE
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="copilot_conversations", on_delete=models.CASCADE
    )
    title = models.CharField(max_length=255, default="New Chat")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title


class Message(models.Model):
    """One turn in a Conversation — `role` mirrors the Anthropic
    Messages API's own two-role shape exactly (it has no third "system"
    message role stored per-turn; the system prompt is built fresh per
    request in SendMessageView, not persisted here), so this table can
    be replayed back to the API almost verbatim (see
    SendMessageView.post's own history-building)."""

    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"

    conversation = models.ForeignKey(
        Conversation, related_name="messages", on_delete=models.CASCADE
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    content = models.TextField()
    sources = models.JSONField(
        default=list,
        blank=True,
        help_text="Assistant turns only: the records this answer was built "
        "from, as snapshots — see services/copilot/retrieval.py's _source_ref "
        "for why a snapshot rather than a foreign key. Empty on user turns, "
        "and on assistant turns where retrieval found nothing to quote.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.role}: {self.content[:40]}"


class CopilotSession(models.Model):
    """Multiplayer Copilot, Phase 2a — the real, shared, persistent
    counterpart to what the frontend's M0 build (see
    features/copilotSessions/ in react-ts-app) kept entirely in one
    browser's own `localStorage`. A `Conversation`'s query/thinking/
    answer content stays exactly as real as it always was (this table
    adds nothing there); what's new is a real place for "who else can
    see and act in this conversation" to live, so two *different* real
    logins can actually share one — M0's own real, documented limit
    (one shared `localStorage` login token) meant that could never work
    before this.

    `customer`/`account` are a real, if redundant-with-the-frontend-URL,
    snapshot of which company this session is about — same "belongs to
    exactly one of Customer or Account" nullable-pair pattern used
    throughout services.customers (never GenericForeignKey, per this
    codebase's own established convention) — captured at "make this
    live" time from whatever the frontend's own entry point already
    knew, so an invite (see SessionInviteSerializer) can show real
    context before the invitee has ever opened the conversation itself.
    Both may be null: a session started from a plain New Chat has no
    company context, same as the Conversation it wraps.

    No separate "owner" field: `conversation.user` is the permanent
    creator and never changes, including through a hand-off — see this
    app's own `conversations_visible_to` for what hand-off actually
    grants (an invite + participancy), not a literal ownership swap."""

    class Status(models.TextChoices):
        PRIVATE = "private", "Private"
        LIVE = "live", "Live"
        AWAITING_HANDOFF = "awaiting_handoff", "Awaiting hand-off"
        CLOSED = "closed", "Closed"

    conversation = models.OneToOneField(
        Conversation, related_name="session", on_delete=models.CASCADE
    )
    customer = models.ForeignKey(
        "customers.Customer",
        related_name="copilot_sessions",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    account = models.ForeignKey(
        "customers.Account",
        related_name="copilot_sessions",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PRIVATE)
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Session for {self.conversation_id} ({self.status})"


class SessionInvite(models.Model):
    """Grants a real user real eligibility to join a live session —
    invite-only means this row, not a generic "any org member" check,
    is what actually gates access (see conversations_visible_to). A
    hand-off (SessionEvent.kind='handed_off') creates or reuses one of
    these for its target rather than a separate access path — handing
    off to someone real-implies inviting them."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"

    session = models.ForeignKey(CopilotSession, related_name="invites", on_delete=models.CASCADE)
    invited_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="copilot_session_invites", on_delete=models.CASCADE
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="+", on_delete=models.SET_NULL, null=True
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["session", "invited_user"], name="one_invite_per_user_per_session"
            )
        ]

    def __str__(self):
        return f"Invite for {self.invited_user_id} to session {self.session_id} ({self.status})"


class SessionParticipant(models.Model):
    """Currently-present, not just eligible — distinct from
    SessionInvite: an accepted invite grants the *right* to join, this
    row means the user actually has (`left_at` null) or once did.
    Re-joining after leaving reactivates the same row rather than
    creating a second one (see RespondToInviteView)."""

    session = models.ForeignKey(
        CopilotSession, related_name="participants", on_delete=models.CASCADE
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="copilot_session_participations",
        on_delete=models.CASCADE,
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["session", "user"], name="one_participant_row_per_user_per_session"
            )
        ]

    def __str__(self):
        return f"{self.user_id} in session {self.session_id}"


class SessionEvent(models.Model):
    """A real, durable presence/lifecycle log for a session's own
    transcript — deliberately NOT a duplicate of chat content
    (query/redirect/answer text already lives in `Message`; a
    `redirected` event just points at the real Message it tags, via
    `message`, rather than re-storing its text). What's genuinely new
    here — who joined when, who handed off to whom — has no other real
    home. Polled via `?since_id=` (see SessionDetailView) rather than
    pushed live — see this app's own Phase 2a/2b split for why."""

    class Kind(models.TextChoices):
        JOINED = "joined", "Joined"
        LEFT = "left", "Left"
        REDIRECTED = "redirected", "Redirected"
        HANDED_OFF = "handed_off", "Handed off"
        MADE_LIVE = "made_live", "Made live"
        CLOSED = "closed", "Closed"

    session = models.ForeignKey(CopilotSession, related_name="events", on_delete=models.CASCADE)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="+", on_delete=models.SET_NULL, null=True, blank=True
    )
    message = models.ForeignKey(
        Message,
        related_name="session_events",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Set only for kind=redirected — which real Message this event tags.",
    )
    payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="kind=handed_off: {to_user_id, to_user_name, note}. Empty for every other kind.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.kind} on session {self.session_id}"


class ModelCall(models.Model):
    """One call to a real model, whatever asked for it.

    The audit trail. Every path that reaches Claude — the Copilot, Headlines,
    the classifier, the brief, the Ops agent — goes through one function
    (`anthropic_client.get_completion`), and that function writes one of
    these per call: who asked, on whose behalf, for what purpose, how many
    tokens in and out, how long it took, and whether it worked. Failures and
    "not configured" are rows too, because a brain whose calls quietly fail
    is worse than one whose calls are visible.
    """

    class Outcome(models.TextChoices):
        OK = "ok", "OK"
        FAILED = "failed", "Failed"
        UNCONFIGURED = "unconfigured", "Not configured"
        OVER_BUDGET = "over_budget", "Over budget"

    organisation = models.ForeignKey(
        "accounts.Organisation",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="model_calls",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    purpose = models.CharField(
        max_length=32,
        help_text='"copilot", "headlines", "classification", "brief", "proposals", "facilitator", "explain".',
    )
    provider = models.CharField(max_length=16, blank=True)
    model = models.CharField(max_length=128, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    max_tokens = models.PositiveIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    outcome = models.CharField(max_length=16, choices=Outcome.choices)
    error = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organisation", "purpose", "created_at"])]

    def __str__(self):
        return f"{self.purpose} {self.outcome} ({self.input_tokens}+{self.output_tokens})"


class ModelBudget(models.Model):
    """How many tokens one purpose may spend per calendar month, per
    organisation. Absent, the default from settings applies. Checked before
    every call; a call that would start over budget is refused and logged
    as `over_budget`, never made."""

    organisation = models.ForeignKey(
        "accounts.Organisation", on_delete=models.CASCADE, related_name="model_budgets"
    )
    purpose = models.CharField(max_length=32)
    monthly_tokens = models.PositiveIntegerField(
        help_text="Input plus output tokens, per calendar month."
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "purpose"], name="modelbudget_one_per_purpose"
            )
        ]

    def __str__(self):
        return f"{self.organisation} {self.purpose}: {self.monthly_tokens:,}/month"
