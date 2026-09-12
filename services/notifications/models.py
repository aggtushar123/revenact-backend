from django.conf import settings
from django.db import models


class Notification(models.Model):
    """A real, personal notification — the first real backend behind
    the Navbar's own bell icon, which used to show a fixed, fake "25"
    badge (see docs/API_CONTRACTS.md). Deliberately scoped: only the two
    real event sources this app actually has a genuine recipient for —
    Multiplayer Copilot invites/hand-offs (services.copilot.views) and
    Customer/Account owner assignment (services.customers.views) — not
    Task/Ticket/Risk/Opportunity assignment, which use a plain-text
    assignee name rather than a real `User`, so there's no real account
    to notify there yet.

    `message` is a real string rendered once, at creation time (see
    services.notifications.realtime.notify) — not re-derived from
    `actor`/whatever it's about later, so it stays meaningful even if
    the underlying company is renamed or a session is long closed by
    the time someone reads it. `link` is a real relative frontend path
    the bell's own dropdown navigates to on click (e.g.
    `/copilot?session=<id>`, `/organizations/<id>`) — never an absolute
    URL, since it's always a route within this same app."""

    class Kind(models.TextChoices):
        COPILOT_INVITE = "copilot_invite", "Copilot invite"
        COPILOT_HANDOFF = "copilot_handoff", "Copilot hand-off"
        CUSTOMER_ASSIGNED = "customer_assigned", "Customer assigned"
        ACCOUNT_ASSIGNED = "account_assigned", "Account assigned"
        QUESTION_ASKED = "question_asked", "Question asked"
        QUESTION_ANSWERED = "question_answered", "Question answered"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="notifications", on_delete=models.CASCADE
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="+", on_delete=models.SET_NULL, null=True, blank=True
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    message = models.CharField(max_length=255)
    link = models.CharField(max_length=255, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.kind} -> {self.recipient_id}: {self.message}"
