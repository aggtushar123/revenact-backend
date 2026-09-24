from django.conf import settings
from django.db import models

from services.accounts.models import Organisation


class AttentionSnooze(models.Model):
    """A per-user snooze for a dashboard attention item.

    `user` and `key` together are unique. `until` is the time until which
    the snooze is active; null means Done (permanently snoozed).
    `fingerprint` stores the fingerprint of the snoozed item for comparison
    when deciding whether to show it again if the item has changed."""

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "key"], name="attention_snooze_unique_per_user"
            ),
        ]
        indexes = [
            models.Index(fields=["user", "key"]),
        ]

    organisation = models.ForeignKey(
        Organisation, on_delete=models.CASCADE, related_name="attention_snoozes"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="attention_snoozes",
    )
    key = models.CharField(max_length=120)
    until = models.DateTimeField(null=True, blank=True)
    fingerprint = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user_id}:{self.key}"
