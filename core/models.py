from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    """One security-relevant thing that happened (SOC2:LOG-01, LOG-02).

    Append-only: rows are created through core.audit.record and never
    updated or deleted from application code (save() and delete() refuse).
    Kept in its own table, separate from application logs, and every row
    carries actor, action, target, outcome, source IP, UTC timestamp and
    the request id that links it to the server logs for that request.

    `metadata` is for small structured context (which fields changed,
    which role was granted). Never put a password, token or secret in it —
    core.audit.record refuses the obvious keys as a safety net.
    """

    class Outcome(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILURE = "failure", "Failure"
        DENIED = "denied", "Denied"

    organisation = models.ForeignKey(
        "accounts.Organisation",
        related_name="audit_events",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="audit_events",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    # Denormalised so the row still says who acted after the user is deleted.
    actor_email = models.CharField(max_length=254, blank=True)
    action = models.CharField(max_length=64, db_index=True)
    target_type = models.CharField(max_length=100, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    target_repr = models.CharField(max_length=255, blank=True)
    outcome = models.CharField(max_length=16, choices=Outcome.choices, default=Outcome.SUCCESS)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    request_id = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organisation", "created_at"])]

    def __str__(self):
        return (
            f"{self.created_at:%Y-%m-%d %H:%M:%S} {self.action} {self.outcome} {self.actor_email}"
        )

    def save(self, *args, **kwargs):
        # SOC2:LOG-02 append-only
        if not self._state.adding:
            raise TypeError("AuditEvent rows are append-only and cannot be modified.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise TypeError("AuditEvent rows are append-only and cannot be deleted.")
