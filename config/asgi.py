"""
ASGI config for config project.

Multiplayer Copilot Phase 2b — HTTP still goes straight to Django's own
ASGI app (identical to what WSGI already served; REST is untouched).
WebSocket connections are routed through a JWT auth middleware (this
app uses SimpleJWT bearer tokens, not Django's session-cookie auth
Channels' own stock AuthMiddlewareStack expects) into
services/copilot/routing.py. See services/copilot/consumers.py's own
docstring for what actually happens once connected.

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

from services.copilot.jwt_auth_middleware import JWTAuthMiddlewareStack  # noqa: E402
from services.copilot.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": JWTAuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
    }
)
