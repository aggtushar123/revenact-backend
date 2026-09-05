"""The push half of Phase 2b — see consumers.py's own docstring for the
receiving half. Every call here is a pure broadcast of state a view has
already committed to the database; nothing here is itself a source of
truth, so it degrades silently — no channel layer configured, or a real
one that's unreachable (Redis down, a network blip) — rather than ever
failing the real request that triggered it. A real REST poll still
catches up within its own ~3s cycle either way (see react-ts-app's own
Index.tsx), so a missed push is never a correctness problem, only a
latency one — the same reasoning a failed metrics/logging call gets
elsewhere, not a discipline unique to this module."""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from .serializers import CopilotSessionSerializer

logger = logging.getLogger(__name__)


def broadcast_session_update(session):
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    session._events_page = session.events.all()
    payload = CopilotSessionSerializer(session).data
    try:
        async_to_sync(channel_layer.group_send)(
            f"session_{session.conversation_id}",
            {"type": "session.update", "payload": payload},
        )
    except Exception:
        # Deliberately broad: any real transport failure here (Redis
        # unreachable, a dropped connection, ...) is a lost real-time
        # push, not a broken request — the caller's own real database
        # write already committed. Logged so a persistently-down channel
        # layer is still visible in the logs, not silently invisible.
        logger.warning("Multiplayer Copilot: real-time push failed", exc_info=True)
