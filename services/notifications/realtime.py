"""Creates a real Notification row and pushes it live — mirrors
services/copilot/realtime.py's own split (a durable write plus a
best-effort real-time push) exactly, just keyed by recipient user
rather than by conversation."""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from .models import Notification
from .serializers import NotificationSerializer

logger = logging.getLogger(__name__)


def notify(*, recipient, actor, kind, message, link=""):
    """The one real entry point every trigger site calls — see
    Notification's own docstring for why `message` is rendered here,
    once, rather than derived again later."""

    notification = Notification.objects.create(
        recipient=recipient, actor=actor, kind=kind, message=message, link=link
    )
    _broadcast(notification)
    return notification


def _broadcast(notification):
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    payload = NotificationSerializer(notification).data
    try:
        async_to_sync(channel_layer.group_send)(
            f"notifications_{notification.recipient_id}",
            {"type": "notification.new", "payload": payload},
        )
    except Exception:
        # Same "never fail the real request that triggered this" posture
        # as services.copilot.realtime's own broadcast_session_update —
        # a missed push just means the recipient sees it on their next
        # real GET /notifications/ instead of instantly.
        logger.warning("Notifications: real-time push failed", exc_info=True)
