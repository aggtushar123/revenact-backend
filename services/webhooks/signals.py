"""Fires WebhookSubscription.Event.CUSTOMER_CREATED — the one real
event this feature supports in v1 (see WebhookSubscription's own
docstring). Same `transaction.on_commit` reasoning as
services.scenarios.signals' own: a receiver fires mid-transaction,
before the new row is guaranteed committed, and there's no correctness
reason to race that when nothing here is on the critical path of the
request the caller is actually waiting on."""

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from services.customers.models import Customer

from .engine import send_webhook
from .models import WebhookSubscription


@receiver(post_save, sender=Customer)
def notify_customer_created_webhooks(sender, instance, created, **kwargs):
    if not created:
        return

    webhooks = list(
        WebhookSubscription.objects.filter(
            organisation=instance.organisation,
            event=WebhookSubscription.Event.CUSTOMER_CREATED,
            is_active=True,
        )
    )
    if not webhooks:
        return

    payload = {
        "id": instance.id,
        "name": instance.name,
        "domain": instance.domain,
        "lifecycle_stage": instance.lifecycle_stage,
        "created_at": instance.created_at.isoformat(),
    }

    def _send_all():
        for webhook in webhooks:
            send_webhook(webhook, WebhookSubscription.Event.CUSTOMER_CREATED, payload)

    transaction.on_commit(_send_all)
