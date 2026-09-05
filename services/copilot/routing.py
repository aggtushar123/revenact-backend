"""WebSocket URL routing for Multiplayer Copilot Phase 2b — mirrors
urls.py's own REST routes in spirit, mounted separately (see
config/asgi.py) since WebSocket connections don't go through Django's
normal URLconf."""

from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(
        r"^ws/copilot/sessions/(?P<conversation_id>\d+)/$",
        consumers.SessionConsumer.as_asgi(),
    ),
]
