"""
ASGI config for config project.

Multiplayer Copilot Phase 2b and this app's own notification bell both
route WebSocket connections through the same JWT auth middleware
(core/ws_auth.py — this app uses SimpleJWT bearer tokens, not Django's
session-cookie auth Channels' own stock AuthMiddlewareStack expects)
into their own routing module. HTTP still goes straight to Django's own
ASGI app — identical to what WSGI already served, REST untouched.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Django's own apps must be loaded (via get_asgi_application()) before
# anything below imports code that touches models — same ordering
# Channels' own tutorial uses.
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from core.ws_auth import JWTAuthMiddlewareStack  # noqa: E402
from services.copilot.routing import websocket_urlpatterns as copilot_ws_urlpatterns  # noqa: E402
from services.notifications.routing import (  # noqa: E402
    websocket_urlpatterns as notifications_ws_urlpatterns,
)

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": JWTAuthMiddlewareStack(
            URLRouter([*copilot_ws_urlpatterns, *notifications_ws_urlpatterns])
        ),
    }
)
