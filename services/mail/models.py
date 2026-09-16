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
