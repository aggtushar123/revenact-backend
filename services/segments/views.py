"""The segment endpoints. Every read is computed for the person asking;
only the owner writes. See docs/API_CONTRACTS.md, `segments`."""

from django.db import transaction
from django.http import Http404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core import audit
from services.accounts.models import User
from services.organizations.export import CSVRenderer
from services.organizations.params import int_or_none

from .access import get_owned, get_readable, readable_segments
from .baseline import rebaseline
from .evaluate import Draft, openable_ids, visible_records
from .history import DEFAULT_DAYS, MAX_DAYS, change_history
from .members import LIST_KEYS, members_listing, members_table, preview
from .models import MAX_OWNED, MAX_PINNED, Segment
from .payloads import list_rows, segment_payload
from .rules import present_rules
from .serializers import MemberStateSerializer, PreviewSerializer, SegmentWriteSerializer

LIMIT_REACHED = f"You can own at most {MAX_OWNED} segments."
SHARING_FIELDS = frozenset({"sharing", "shared_with"})
PIN_LIMIT = f"A segment can pin at most {MAX_PINNED} records, and keep out as many."


def _limit_reached(user):
    """Whether `user` already owns the most segments allowed. Call inside the
    transaction that creates the new one: the owner's User row is locked
    first, so two creates at once queue up instead of both passing the
    count."""
    list(User.objects.select_for_update().filter(pk=user.pk).values_list("pk", flat=True))
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
        serializer = SegmentWriteSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            if _limit_reached(request.user):
                return Response({"detail": LIMIT_REACHED}, status=status.HTTP_400_BAD_REQUEST)
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
        rules, _labels = present_rules(source.rules, source.kind, user=request.user)
        with transaction.atomic():
            if _limit_reached(request.user):
                return Response({"detail": LIMIT_REACHED}, status=status.HTTP_400_BAD_REQUEST)
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


class SegmentMembersView(APIView):
    """GET /api/v1/segments/<id>/members/ — the members the caller may open,
    as the kind's own list reads them (rows, groups, sort, search, cursor),
    with the tiles and `hidden_count`. See docs/API_CONTRACTS.md."""

    # SOC2:AUTH-02 authentication here; `get_readable` checks the segment and
    # `members_queryset` (services/segments/evaluate.py) every record
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        segment = get_readable(request.user, pk)
        body = members_listing(
            segment, request.user, request.query_params, today=timezone.localdate()
        )
        return Response(body)


class SegmentMembersExportView(APIView):
    """GET /api/v1/segments/<id>/members/export.csv — every member the caller
    may open, in list order, with the kind's export columns. Audited: this is
    confidential data leaving the app."""

    # SOC2:AUTH-02 authentication here; the same checks as the members view
    permission_classes = [IsAuthenticated]
    renderer_classes = [CSVRenderer]

    def get(self, request, pk):
        segment = get_readable(request.user, pk)
        today = timezone.localdate()
        rows, count = members_table(segment, request.user, request.query_params, today=today)
        audit.record(  # SOC2:LOG-01
            "segment.exported",
            request=request,
            target=segment,
            # Parameter names only, and only the ones the list reads: a search
            # term (or any other key) is the user's own words.
            metadata={
                "count": count,
                "params": [key for key in sorted(LIST_KEYS) if key in request.query_params],
            },
        )
        response = Response(rows)
        response["Content-Disposition"] = (
            f'attachment; filename="segment-{segment.pk}-{today.isoformat()}.csv"'
        )
        return response


class SegmentMemberView(APIView):
    """PATCH /api/v1/segments/<id>/members/<record_id>/ — `{"state": "pinned"
    | "excluded" | "none"}`. Owner only, and only a record the owner may
    open: a missing one and a hidden one read the same 404."""

    # SOC2:AUTH-02 authentication here; `get_owned` and the record check below
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk, record_id):
        segment = get_owned(request.user, pk)
        serializer = MemberStateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        state = serializer.validated_data["state"]
        # SOC2:AUTH-02 only a record the owner may open; missing and hidden read the same
        if not visible_records(segment.kind, request.user).filter(pk=record_id).exists():
            raise Http404
        with transaction.atomic():
            # The lists are read under a row lock, so two PATCHes at once
            # neither lose one another's change nor pass the limit together.
            segment = Segment.objects.select_for_update().get(pk=segment.pk)
            was_pinned = record_id in segment.pinned_ids
            was_excluded = record_id in segment.excluded_ids
            pinned = [pk for pk in segment.pinned_ids if pk != record_id]
            excluded = [pk for pk in segment.excluded_ids if pk != record_id]
            if state == "pinned":
                pinned.append(record_id)
            elif state == "excluded":
                excluded.append(record_id)
            if len(pinned) > MAX_PINNED or len(excluded) > MAX_PINNED:
                return Response({"detail": PIN_LIMIT}, status=status.HTTP_400_BAD_REQUEST)
            events = []
            if (state == "pinned") != was_pinned:
                events.append(
                    ("segment.member_pinned", {"record_id": record_id, "pinned": state == "pinned"})
                )
            if (state == "excluded") != was_excluded:
                events.append(
                    (
                        "segment.member_excluded",
                        {"record_id": record_id, "excluded": state == "excluded"},
                    )
                )
            if events:
                segment.pinned_ids, segment.excluded_ids = sorted(pinned), sorted(excluded)
                segment.save(update_fields=["pinned_ids", "excluded_ids", "updated_at"])
                rebaseline(segment, today=timezone.localdate())
                for action, metadata in events:
                    audit.record(  # SOC2:LOG-01
                        action, request=request, target=segment, metadata=metadata
                    )
        return Response(
            {
                "pinned_ids": openable_ids(segment.kind, request.user, segment.pinned_ids),
                "excluded_ids": openable_ids(segment.kind, request.user, segment.excluded_ids),
            }
        )


class SegmentPreviewView(APIView):
    """POST /api/v1/segments/preview/ — `{kind, rules, pinned_ids?,
    excluded_ids?}` evaluated for the caller without saving: the count, the
    first ten members and the totals. The rules are checked as a save would
    check them."""

    # SOC2:AUTH-02 authentication here; `validate_rules` and `members_queryset`
    # do the record checks
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PreviewSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        draft = Draft(data["kind"], data["rules"], data["pinned_ids"], data["excluded_ids"])
        return Response(preview(draft, request.user, today=timezone.localdate()))


class SegmentChangesView(APIView):
    """GET /api/v1/segments/<id>/changes/?days=30 — the entry and exit
    history over the last `days` (1–90), naming only records the caller may
    open, plus `hidden_count` for the rest."""

    # SOC2:AUTH-02 authentication here; `get_readable` and `change_history` check the rest
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        segment = get_readable(request.user, pk)
        days = int_or_none(request.query_params.get("days"))
        days = DEFAULT_DAYS if days is None or days < 1 else min(days, MAX_DAYS)
        return Response(
            change_history(segment, request.user, today=timezone.localdate(), days=days)
        )
