"""A person's own mailbox, connected to the company brain.

Every email this product shows or sends comes from *someone's* mailbox:
the CSM's, the engineer's, the CEO's. `MailboxConnection` is that link —
one per person, to whatever provider their company runs on (Google,
Microsoft, or any IMAP/SMTP server). The credentials are theirs, encrypted
at rest, and the emails synced through it are theirs too: visible to them
and to their management chain, never to peers or to people below them
(see visibility.py). The Copilot reads under the same rule.
"""

from django.conf import settings
from django.db import models

from services.accounts.models import Organisation

from . import crypto


class MailboxConnection(models.Model):
    class Provider(models.TextChoices):
        GOOGLE = "google", "Google Workspace / Gmail"
        MICROSOFT = "microsoft", "Microsoft 365 / Outlook"
        IMAP = "imap", "IMAP / SMTP"

    class Status(models.TextChoices):
        CONNECTED = "connected", "Connected"
        ERROR = "error", "Needs attention"

    organisation = models.ForeignKey(
        Organisation, related_name="mailboxes", on_delete=models.CASCADE
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, related_name="mailbox", on_delete=models.CASCADE
    )
    provider = models.CharField(max_length=16, choices=Provider.choices)
    address = models.EmailField(help_text="The mailbox's own address, as the provider reports it.")
    display_name = models.CharField(max_length=150, blank=True)
    #: Fernet-encrypted JSON: OAuth tokens, or IMAP/SMTP host + login. Restricted.
    credentials = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.CONNECTED)
    error = models.CharField(max_length=255, blank=True)
    #: Provider-specific: where the last sync stopped (an epoch, a delta link, a UID).
    sync_cursor = models.CharField(max_length=512, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.address} ({self.get_provider_display()})"

    # Credentials never leave this pair of methods in clear.
    def set_credentials(self, data: dict):
        import json

        self.credentials = crypto.encrypt(json.dumps(data))

    def get_credentials(self) -> dict:
        import json

        return json.loads(crypto.decrypt(self.credentials))


class MailMessage(models.Model):
    """One message in a person's own mailbox, as the provider holds it.

    `customers.Email` is the company's view: mail filed against an account so
    the whole team's picture of that customer includes it. This is the
    person's view: every message the provider handed back, matched or not,
    so the Communications page can be their inbox rather than a subset of it.
    Where a message did match, `email` points at the filed copy and the row
    carries the account context with it.

    It belongs to the connection and dies with it: disconnecting a mailbox
    takes the personal copy away, while the filed `Email` rows stay on the
    customer as before. Visible to the owner only (views scope on `owner`);
    the management chain reads the filed copies, not someone's whole inbox.

    `folder`, `is_read`, `is_starred` and `is_important` come from the
    provider at sync; the store does not write them back (Gmail is connected
    read-only), so a star here is a star *here*. `state` is this product's
    own triage — done and muted leave the inbox without touching the mailbox.
    `category` is decided once at sync by `categorise()`: the provider's own
    tab when it has one, else a small honest heuristic.
    """

    class Folder(models.TextChoices):
        INBOX = "inbox", "Inbox"
        SENT = "sent", "Sent"
        DRAFTS = "drafts", "Drafts"
        SPAM = "spam", "Spam"
        TRASH = "trash", "Trash"

    class Category(models.TextChoices):
        GENERAL = "general", "General"
        FINANCIAL = "financial", "Financial"
        NEWSLETTERS = "newsletters", "Newsletters"
        NOTIFICATIONS = "notifications", "Notifications"
        PROMOTIONS = "promotions", "Promotions"
        SOCIAL = "social", "Social"

    class State(models.TextChoices):
        OPEN = "open", "Open"
        DONE = "done", "Done"
        MUTED = "muted", "Muted"

    class Direction(models.TextChoices):
        SENT = "sent", "Sent"
        RECEIVED = "received", "Received"

    connection = models.ForeignKey(
        MailboxConnection, related_name="messages", on_delete=models.CASCADE
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="mail_messages", on_delete=models.CASCADE
    )
    organisation = models.ForeignKey(
        Organisation, related_name="mail_messages", on_delete=models.CASCADE
    )
    provider_message_id = models.CharField(max_length=255)
    thread_id = models.CharField(max_length=255, blank=True)
    direction = models.CharField(max_length=8, choices=Direction.choices)
    from_name = models.CharField(max_length=150, blank=True)
    from_address = models.EmailField(blank=True)
    to = models.JSONField(default=list, blank=True, help_text="[[name, address], ...]")
    subject = models.CharField(max_length=255)
    snippet = models.CharField(max_length=300, blank=True)
    body = models.TextField(blank=True)
    sent_at = models.DateTimeField()
    folder = models.CharField(max_length=8, choices=Folder.choices, default=Folder.INBOX)
    category = models.CharField(max_length=16, choices=Category.choices, default=Category.GENERAL)
    state = models.CharField(max_length=8, choices=State.choices, default=State.OPEN)
    is_read = models.BooleanField(default=True)
    is_starred = models.BooleanField(default=False)
    is_important = models.BooleanField(default=False)
    #: Set when the person changed a flag or the state here. From then on a
    #: re-sync leaves the provider's flags alone: what they did here wins.
    locally_changed_at = models.DateTimeField(null=True, blank=True)
    email = models.OneToOneField(
        "customers.Email",
        related_name="mail_message",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="The filed copy, when the message matched someone in the book.",
    )
    synced_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-sent_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "provider_message_id"], name="mail_message_once_per_mailbox"
            )
        ]
        indexes = [
            models.Index(fields=["owner", "folder", "state", "-sent_at"]),
            models.Index(fields=["owner", "category"]),
        ]

    def __str__(self):
        return f"{self.subject} ({self.from_address})"

    @property
    def priority(self) -> bool:
        """Provider-important, or from someone in the book."""
        return self.is_important or self.email_id is not None
