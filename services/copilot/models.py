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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.role}: {self.content[:40]}"
