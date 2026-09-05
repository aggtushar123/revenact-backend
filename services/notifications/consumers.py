"""Real-time push for this app's own notification bell — mirrors
services/copilot/consumers.py's own shape exactly: connect, get pushed
new rows as they're created (see realtime.notify), disconnect. One
group per *user* here (`notifications_<user_id>`) rather than per
conversation — every one of a user's own real notifications reaches the
same group, since there's no per-item access question the way a
Multiplayer Copilot session has (a notification's own `recipient` FK
already is the access control)."""

from channels.generic.websocket import AsyncJsonWebsocketConsumer


class NotificationConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope["user"]
        if user.is_anonymous:
            await self.close(code=4003)
            return

        self.group_name = f"notifications_{user.id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    # Matches realtime._broadcast's own {"type": "notification.new", ...}.
    async def notification_new(self, event):
        await self.send_json(event["payload"])
