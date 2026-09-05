"""WebSocket URL routing for this app's own real-time push — mirrors
services/copilot/routing.py's own shape."""

from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(r"^ws/notifications/$", consumers.NotificationConsumer.as_asgi()),
]
