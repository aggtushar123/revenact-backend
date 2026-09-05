"""Real-time push, Phase 2b — the same real session state Phase 2a's
own REST endpoints already serve, just pushed instantly instead of
waited for on the frontend's next ~3s poll. No new data model here, and
no new "who's currently online" concept beyond Phase 2a's own
SessionParticipant (see that model's own docstring) — this consumer is
a pure notification channel: connect, get pushed the current
CopilotSessionSerializer snapshot whenever a real backend action
(make-live, invite-accept, redirect, hand-off, close) creates a new
SessionEvent (see realtime.broadcast_session_update, called from those
same views), disconnect. The frontend still fetches full message
content over the existing REST endpoints once notified — nothing about
*sending* a message or acting in a session moves onto the socket, only
this "something changed, here's the new state" push does."""

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .views import conversations_visible_to


class SessionConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.conversation_id = self.scope["url_route"]["kwargs"]["conversation_id"]
        user = self.scope["user"]

        if user.is_anonymous or not await self._is_visible_to(user):
            # Same "don't even confirm it exists" posture as the REST
            # endpoints' own 404s — a close code here, not a 404, since
            # WebSocket has no HTTP status to hand back.
            await self.close(code=4003)
            return

        self.group_name = f"session_{self.conversation_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    # Matches group_send's own {"type": "session.update", ...} — Channels
    # maps dots to underscores when dispatching to a handler method.
    async def session_update(self, event):
        await self.send_json(event["payload"])

    @database_sync_to_async
    def _is_visible_to(self, user):
        return conversations_visible_to(user).filter(pk=self.conversation_id).exists()
