import secrets

from django.db import models


def _generate_secret():
    # 43 URL-safe chars (~256 bits) — used as the HMAC key signing every
    # delivery's own X-Revenact-Signature header (see engine.py). Never
    # client-settable — see WebhookSubscriptionSerializer's own docstring.
    return secrets.token_urlsafe(32)


class WebhookSubscription(models.Model):
    """A tenant's own "call this URL when X happens" configuration —
    backs Settings > Webhooks (react-ts-app's src/pages/settings/
    WebhooksPage.tsx). Deliberately one real event for v1 (see
    `Event.CUSTOMER_CREATED`'s own docstring) rather than every event
    name a bigger webhooks product might eventually support.

    Runs synchronously, in-request, same as scenarios' own On Event
    dispatch (see services.scenarios.signals) — there's no task queue
    in this codebase (see requirements.txt), so a slow or dead
    receiving URL adds real latency to whatever request triggered it
    (e.g. creating a Customer). engine.py's own send_webhook enforces a
    short timeout specifically to bound that, not eliminate it."""

    class Event(models.TextChoices):
        CUSTOMER_CREATED = "customer.created", "Organization Created"

    organisation = models.ForeignKey(
        "accounts.Organisation", related_name="webhooks", on_delete=models.CASCADE
    )
    url = models.URLField(max_length=500)
    event = models.CharField(max_length=32, choices=Event.choices)
    secret = models.CharField(max_length=64, default=_generate_secret, editable=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_event_display()} -> {self.url}"


class WebhookDelivery(models.Model):
    """One outbound POST attempt — the audit trail behind each
    WebhookSubscription, same reasoning as scenarios.ScenarioRun for
    scenario executions. Lets Settings > Webhooks show whether a
    configured webhook is actually reaching its own URL rather than
    silently failing forever."""

    webhook = models.ForeignKey(
        WebhookSubscription, related_name="deliveries", on_delete=models.CASCADE
    )
    success = models.BooleanField()
    status_code = models.IntegerField(null=True, blank=True)
    error = models.CharField(max_length=255, blank=True)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-sent_at"]
        verbose_name_plural = "webhook deliveries"

    def __str__(self):
        return f"{self.webhook_id} @ {self.sent_at} ({'ok' if self.success else 'failed'})"
