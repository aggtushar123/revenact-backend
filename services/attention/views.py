"""The Dashboard Overview's "Needs attention" list, and per-user snoozing.

`AttentionListView` is read-only: it builds every candidate item
(`rules.build_items`), drops what the viewer has snoozed (`snooze.visible_items`),
and returns the top 25 by score. The two snooze views let a viewer act on one
row — snooze it for a while, mark it Done, or bring it back — without ever
touching another user's copy of the list.
"""

from datetime import timedelta

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core import audit
from services.customers import forecast

from .models import AttentionSnooze
from .rules import build_items
from .snooze import visible_items

#: How many rows the "Needs attention" list ever shows.
ITEM_LIMIT = 25


class AttentionListView(APIView):
    """GET /api/v1/dashboard/attention/?owner=&lifecycle=&customer= — the
    viewer's own candidate items, snoozed ones dropped, top 25 by score
    (ties broken by title so the order is stable)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        organisation = request.user.organisation
        today = timezone.localdate()
        items = build_items(request.user, request.query_params, today=today)
        items = visible_items(request.user, items, now=timezone.now())
        items.sort(key=lambda item: (-item["score"], item["title"]))
        return Response(
            {
                "items": items[:ITEM_LIMIT],
                "currency": organisation.currency,
                "filters": forecast.filter_options(request.user),
            }
        )


class SnoozeSerializer(serializers.Serializer):
    key = serializers.CharField()
    days = serializers.IntegerField(required=False, min_value=1, max_value=90)
    done = serializers.BooleanField(required=False, default=False)

    def validate(self, data):
        if not data.get("done") and "days" not in data:
            raise serializers.ValidationError("Either days or done is required.")
        return data


class AttentionSnoozeView(APIView):
    """POST /api/v1/dashboard/attention/snooze/ {key, days} or {key, done:
    true} — snooze one of the viewer's own current items. `key` must be on
    their list right now (built with no filters, same as the full list);
    anything else, including a key that only exists for a different
    organisation, is refused rather than silently accepted."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = SnoozeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = serializer.validated_data["key"]
        done = serializer.validated_data["done"]

        today = timezone.localdate()
        items = {item["key"]: item for item in build_items(request.user, {}, today=today)}
        item = items.get(key)
        if item is None:
            raise serializers.ValidationError({"key": ["Not an item on your list."]})

        until = None if done else timezone.now() + timedelta(days=serializer.validated_data["days"])
        snooze, _created = AttentionSnooze.objects.update_or_create(
            user=request.user,
            key=key,
            defaults={
                "organisation": request.user.organisation,
                "until": until,
                "fingerprint": item["fingerprint"],
            },
        )
        audit.record(
            "attention.snoozed",
            request=request,
            target=snooze,
            metadata={"key": key, "done": True}
            if done
            else {"key": key, "days": serializer.validated_data["days"]},
        )
        return Response({"key": snooze.key, "until": snooze.until}, status=status.HTTP_201_CREATED)

    def delete(self, request, key):
        snooze = get_object_or_404(AttentionSnooze, user=request.user, key=key)
        snooze.delete()
        audit.record("attention.unsnoozed", request=request, metadata={"key": key})
        return Response(status=status.HTTP_204_NO_CONTENT)
