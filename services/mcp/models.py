import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

#: What every token starts with, so one found in a log or a config file is
#: recognisable as ours and can be revoked rather than puzzled over.
PREFIX = "rvn_mcp_"


class McpToken(models.Model):
    """One person's own key for an agent that reads Revenact on their
    behalf.

    Stored as a hash, never in the clear: the secret is shown once, when it
    is issued, and after that nobody — not an admin, not this table — can
    recover it. Losing it means issuing another.

    The token *is* the person. Every tool the MCP server runs is scoped to
    this user's own visibility, so an agent holding it reads exactly what
    they could read in the app and nothing else."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="mcp_tokens", on_delete=models.CASCADE
    )
    label = models.CharField(max_length=120)
    token_hash = models.CharField(max_length=64, unique=True)
    #: The last few characters, so a person can tell two tokens apart.
    hint = models.CharField(max_length=16)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    @staticmethod
    def hash(raw: str) -> str:
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def issue(cls, user, label: str):
        """(row, secret). The secret is returned once and never stored."""
        raw = f"{PREFIX}{secrets.token_urlsafe(32)}"
        row = cls.objects.create(
            user=user,
            label=(label or "Agent").strip()[:120],
            token_hash=cls.hash(raw),
            hint=f"…{raw[-4:]}",
        )
        return row, raw

    @classmethod
    def resolve(cls, raw: str):
        """The live token for this secret, or None. Constant work either
        way: the lookup is by hash, so a wrong secret costs one index hit
        and tells an attacker nothing."""
        if not raw or not raw.startswith(PREFIX):
            return None
        return (
            cls.objects.filter(
                token_hash=cls.hash(raw),
                revoked_at__isnull=True,
                # A key is its owner's own access. Somebody who has been
                # deactivated has lost theirs, and the key goes with it the
                # same moment — not whenever somebody remembers to revoke it.
                user__is_active=True,
            )
            .select_related("user", "user__organisation")
            .first()
        )

    @property
    def is_live(self) -> bool:
        return self.revoked_at is None

    def touch(self):
        McpToken.objects.filter(pk=self.pk).update(last_used_at=timezone.now())

    def __str__(self):
        return f"{self.label} ({self.user})"
