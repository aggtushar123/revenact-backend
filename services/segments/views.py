"""The segment endpoints. Every read is computed for the person asking;
only the owner writes. See docs/API_CONTRACTS.md, `segments`."""

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core import audit

from .access import get_owned, get_readable, readable_segments
from .baseline import rebaseline
from .evaluate import openable_ids
from .models import MAX_OWNED, Segment
from .payloads import list_rows, segment_payload
from .rules import present_rules
from .serializers import SegmentWriteSerializer

LIMIT_REACHED = f"You can own at most {MAX_OWNED} segments."
SHARING_FIELDS = frozenset({"sharing", "shared_with"})


def _limit_reached(user):
    return Segment.objects.filter(owner=user).count() >= MAX_OWNED


def _changed(segment, data):
    """The names of the fields `data` would change, never their values."""
    changed = []
    for name, value in data.items():
        if name == "shared_with":
            before = set(segment.shared_with.values_list("pk", flat=True))
            if {user.pk for user in value} != before:
                changed.append(name)
        elif getattr(segment, name) != value:
            changed.append(name)
    return sorted(changed)


def _record_shared(request, segment):
    audit.record(  # SOC2:LOG-01
        "segment.shared",
        request=request,
        target=segment,
        metadata={
            "sharing": segment.sharing,
            "shared_with": sorted(segment.shared_with.values_list("pk", flat=True)),
        },
    )


class SegmentListCreateView(APIView):
    """GET /api/v1/segments/?scope=mine|shared|all&search= — the segments the
    caller may read, by name. POST — a new segment, owned by the caller."""

    # SOC2:AUTH-02 authentication here; `readable_segments` scopes every read
    permission_classes = [IsAuthenticated]

    def get(self, request):
        segments = readable_segments(request.user)
        scope = request.query_params.get("scope")
        if scope == "mine":
            segments = segments.filter(owner=request.user)
        elif scope == "shared":
            segments = segments.exclude(owner=request.user)
        search = (request.query_params.get("search") or "").strip()
        if search:
            segments = segments.filter(name__icontains=search)
        segments = (
            segments.select_related("owner")
            .defer("rules", "pinned_ids", "excluded_ids", "last_members")
            .order_by("name", "pk")
        )
        return Response(list_rows(segments, request.user, today=timezone.localdate()))

    def post(self, request):
        if _limit_reached(request.user):
            return Response({"detail": LIMIT_REACHED}, status=status.HTTP_400_BAD_REQUEST)
        serializer = SegmentWriteSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            segment = serializer.save(
                owner=request.user, organisation_id=request.user.organisation_id
            )
            rebaseline(segment, today=timezone.localdate())
            audit.record(  # SOC2:LOG-01
                "segment.created", request=request, target=segment, metadata={"kind": segment.kind}
            )
            if segment.sharing != Segment.Sharing.PRIVATE:
                _record_shared(request, segment)
        return Response(segment_payload(segment, request.user), status=status.HTTP_201_CREATED)


class SegmentDetailView(APIView):
    """GET/PATCH/DELETE /api/v1/segments/<id>/. Readers read; only the owner
    writes (403 for another reader, 404 for anyone who may not read it)."""

    # SOC2:AUTH-02 authentication here; `get_readable`/`get_owned` check the segment
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        return Response(segment_payload(get_readable(request.user, pk), request.user))

    def patch(self, request, pk):
        segment = get_owned(request.user, pk)
        serializer = SegmentWriteSerializer(
            segment, data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        changed = _changed(segment, serializer.validated_data)
        with transaction.atomic():
            segment = serializer.save()
            if "rules" in changed:
                rebaseline(segment, today=timezone.localdate())
            if changed:
                audit.record(  # SOC2:LOG-01
                    "segment.updated", request=request, target=segment, metadata={"fields": changed}
                )
            if SHARING_FIELDS & set(changed):
                _record_shared(request, segment)
        return Response(segment_payload(segment, request.user))

    def delete(self, request, pk):
        segment = get_owned(request.user, pk)
        with transaction.atomic():
            audit.record(  # SOC2:LOG-01
                "segment.deleted", request=request, target=segment, metadata={"kind": segment.kind}
            )
            segment.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SegmentDuplicateView(APIView):
    """POST /api/v1/segments/<id>/duplicate/ — a private copy owned by the
    caller. Its rules are as the caller reads them (ids they cannot open are
    null), and it keeps only the pins and keep-outs they may open."""

    # SOC2:AUTH-02 authentication here; `get_readable` checks the source
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        source = get_readable(request.user, pk)
        if _limit_reached(request.user):
            return Response({"detail": LIMIT_REACHED}, status=status.HTTP_400_BAD_REQUEST)
        rules, _labels = present_rules(source.rules, source.kind, user=request.user)
        with transaction.atomic():
            duplicate = Segment.objects.create(
                organisation_id=request.user.organisation_id,
                owner=request.user,
                name=f"{source.name} (copy)"[:120],
                description=source.description,
                kind=source.kind,
                rules=rules,
                pinned_ids=openable_ids(source.kind, request.user, source.pinned_ids),
                excluded_ids=openable_ids(source.kind, request.user, source.excluded_ids),
            )
            rebaseline(duplicate, today=timezone.localdate())
            audit.record(  # SOC2:LOG-01
                "segment.duplicated",
                request=request,
                target=duplicate,
                metadata={"source": source.pk},
            )
        return Response(segment_payload(duplicate, request.user), status=status.HTTP_201_CREATED)
