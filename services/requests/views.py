from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core import audit
from services.accounts.permissions import CanViewAllAccounts
from services.copilot.anthropic_client import BudgetExceeded, CopilotNotConfigured

from .gather import Companies, GatherStopped, gather, recentre, summarise, visible_evidence
from .models import FeatureRequest, RequestEvidence
from .serializers import FeatureRequestWriteSerializer, evidence_row, request_row


def _org_requests(request):
    return FeatureRequest.objects.filter(organisation=request.user.organisation).select_related(
        "owner"
    )


class FeatureRequestListView(APIView):
    """GET /api/v1/requests/?status= — every request the reader has evidence
    for, with the revenue and counts computed over the companies they may
    open, most revenue first.

    A request whose evidence is all outside their book is left out entirely
    rather than shown with a zero: its title and summary were written from
    records they may not read."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        organisation = request.user.organisation
        wanted = request.query_params.get("status")
        if wanted and wanted not in FeatureRequest.Status.values:
            return Response({"detail": f"Unknown status {wanted!r}."}, status=400)
        rows = _org_requests(request)
        if wanted:
            rows = rows.filter(status=wanted)
        evidence = list(visible_evidence(organisation, request.user))
        companies = Companies(evidence, organisation, request.user)
        by_request = {}
        for row in evidence:
            by_request.setdefault(row.request_id, []).append(row)
        out = [
            request_row(feature, summarise(by_request[feature.id], companies))
            for feature in rows
            if feature.id in by_request
        ]
        out.sort(key=lambda r: (-float(r["arr"]), -r["interactions"], r["title"]))
        return Response(out)


class FeatureRequestDetailView(APIView):
    """GET /api/v1/requests/<id>/ — the request with the evidence and the
    companies asking that this reader may see. A request they have no
    evidence for is a 404, the same "404, not an empty page" rule the
    customer-scoped views use.

    PATCH title, summary, status, owner — leadership only, audited."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        feature = get_object_or_404(_org_requests(request), pk=pk)
        organisation = request.user.organisation
        rows = list(visible_evidence(organisation, request.user, request=feature))
        if not rows:
            raise Http404("No evidence you can read.")
        companies = Companies(rows, organisation, request.user)
        summary = summarise(rows, companies)
        asking = sorted(
            (
                {"id": c.id, "name": c.name, "arr": str(companies.arr_of(c))}
                for c in summary["customers"].values()
            ),
            key=lambda c: -float(c["arr"]),
        )
        return Response(
            {
                **request_row(feature, summary),
                # `companies` stays the count from request_row; the list is
                # its own field so one name never means two shapes.
                "companies_asking": asking,
                "evidence": [evidence_row(r) for r in rows],
            }
        )

    def patch(self, request, pk):
        if not CanViewAllAccounts().has_permission(request, self):
            return Response({"detail": "Only leadership curates requests."}, status=403)
        feature = get_object_or_404(_org_requests(request), pk=pk)
        serializer = FeatureRequestWriteSerializer(
            feature, data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        audit.record(
            "request.update",
            request=request,
            target=feature,
            # Only what the serializer accepted: a client may send anything.
            metadata={key: str(value) for key, value in serializer.validated_data.items()},
        )
        return self.get(request, pk)


class GatherView(APIView):
    """POST /api/v1/requests/gather/ — file every unfiled classified ask:
    into an existing request when one is close, else into new titled
    requests (one model call each). Leadership only: it spends credits."""

    permission_classes = [IsAuthenticated, CanViewAllAccounts]

    def post(self, request):
        organisation = request.user.organisation
        if not organisation.ai_agent_enabled:
            return Response({"detail": "AI Copilot is disabled for your organisation."}, status=403)
        try:
            return Response(gather(organisation, actor=request.user, request=request))
        except GatherStopped as stopped:
            code = (
                status.HTTP_429_TOO_MANY_REQUESTS
                if isinstance(stopped.cause, BudgetExceeded)
                else status.HTTP_503_SERVICE_UNAVAILABLE
                if isinstance(stopped.cause, CopilotNotConfigured)
                else status.HTTP_502_BAD_GATEWAY
            )
            return Response({**stopped.result, "detail": str(stopped.cause)}, status=code)


class MergeView(APIView):
    """POST /api/v1/requests/<id>/merge/ {into} — move every piece of
    evidence to `into` and delete this request. Leadership only."""

    permission_classes = [IsAuthenticated, CanViewAllAccounts]

    def post(self, request, pk):
        source = get_object_or_404(_org_requests(request), pk=pk)
        target = get_object_or_404(_org_requests(request), pk=request.data.get("into") or 0)
        if source.id == target.id:
            return Response({"detail": "A request cannot be merged into itself."}, status=400)
        with transaction.atomic():
            moved = source.evidence.update(request=target)
            audit.record(
                "request.merge",
                request=request,
                target=target,
                metadata={"from": source.title, "into": target.title, "evidence": moved},
            )
            source.delete()
            # Without this the merge would undo itself: the survivor's
            # centroid would still not match the asks folded into it.
            recentre(target)
        return Response({"id": target.id, "moved": moved})


class EvidenceMoveView(APIView):
    """POST /api/v1/requests/evidence/<id>/move/ {request} — refile one ask."""

    permission_classes = [IsAuthenticated, CanViewAllAccounts]

    def post(self, request, pk):
        row = get_object_or_404(
            RequestEvidence.objects.filter(organisation=request.user.organisation), pk=pk
        )
        target = get_object_or_404(_org_requests(request), pk=request.data.get("request") or 0)
        was = row.request
        row.request, row.dismissed = target, False
        row.save(update_fields=["request", "dismissed"])
        audit.record(
            "request.evidence_move",
            request=request,
            target=target,
            metadata={"evidence": row.id, "from": was.title if was else None, "into": target.title},
        )
        recentre(target)
        if was and was.id != target.id:
            recentre(was)
        return Response(evidence_row(row))


class EvidenceDismissView(APIView):
    """POST /api/v1/requests/evidence/<id>/dismiss/ — not a feature request
    after all; it stays filed as dismissed so gather leaves it alone."""

    permission_classes = [IsAuthenticated, CanViewAllAccounts]

    def post(self, request, pk):
        row = get_object_or_404(
            RequestEvidence.objects.filter(organisation=request.user.organisation), pk=pk
        )
        was = row.request
        row.request, row.dismissed = None, True
        row.save(update_fields=["request", "dismissed"])
        audit.record(
            "request.evidence_dismiss",
            request=request,
            target=was,
            metadata={"evidence": row.id, "kind": row.kind, "from": was.title if was else None},
        )
        if was:
            recentre(was)
        return Response({"id": row.id, "dismissed": True})
