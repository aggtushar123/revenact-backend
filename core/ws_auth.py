"""Real WebSocket authentication, shared by every real-time feature in
this app (Multiplayer Copilot's services/copilot/consumers.py, this
app's own services/notifications/consumers.py) — moved here from
services/copilot/ once a second app needed the exact same thing, since
it was never really copilot-specific.

This app's REST API authenticates via SimpleJWT bearer tokens (an
`Authorization: Bearer <token>` header), not Django's session/cookie
auth — so Channels' own stock `AuthMiddlewareStack` (built for
session-cookie auth) doesn't apply here. A browser also can't attach
custom headers to a WebSocket handshake request at all, so the token
travels as `?token=<access_token>` in the connection URL instead — the
exact same access token the REST client already holds (see
features/copilotSessions/sessionSocket.ts / features/notifications/
notificationSocket.ts on the frontend), just passed a different way
because the transport is different.

Real, not a stub: an invalid, expired, or missing token resolves to a
genuine `AnonymousUser`, and each consumer's own connect() rejects that
the same way it rejects anyone else it shouldn't let in."""

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from services.accounts.models import User


@database_sync_to_async
def _resolve_user(token_value):
    if not token_value:
        return AnonymousUser()
    try:
        validated = AccessToken(token_value)
        return User.objects.get(pk=validated["user_id"])
    except (TokenError, User.DoesNotExist, KeyError):
        return AnonymousUser()


class JWTAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        query_params = parse_qs(scope.get("query_string", b"").decode())
        token = query_params.get("token", [None])[0]
        scope["user"] = await _resolve_user(token)
        return await super().__call__(scope, receive, send)


def JWTAuthMiddlewareStack(inner):
    return JWTAuthMiddleware(inner)
