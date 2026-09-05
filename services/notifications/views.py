from django.shortcuts import get_object_or_404
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Notification
from .serializers import NotificationSerializer


class NotificationListView(generics.ListAPIView):
    """GET /api/v1/notifications/ — the caller's own real notifications,
    newest first. Pagination off — same "one person's own short personal
    list, not meant to be paged through" reasoning as
    services.copilot.views.MyInvitesView/ConversationListView."""

    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return Notification.objects.filter(recipient=self.request.user)


class MarkNotificationReadView(APIView):
    """POST /api/v1/notifications/<id>/read/ — the recipient themselves
    only (404 otherwise, same "don't even confirm it exists" posture
    used throughout this codebase's own personal-resource endpoints)."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
        if not notification.is_read:
            notification.is_read = True
            notification.save(update_fields=["is_read"])
        return Response(NotificationSerializer(notification).data)


class MarkAllNotificationsReadView(APIView):
    """POST /api/v1/notifications/read-all/ — marks every one of the
    caller's own currently-unread notifications read in one real
    write, for the bell dropdown's own "Mark all as read" action."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
        return Response({"detail": "ok"})
