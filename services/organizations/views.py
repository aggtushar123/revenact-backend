from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core import audit
from core.models import AuditEvent

from . import bulk
from .book import filter_options, load_portfolio
from .export import CSVRenderer, table
from .params import parse_params
from .rows import row_payload
from .serializers import BulkRequestSerializer
from .shape import build_listing, select


class PortfolioView(APIView):
    """GET /api/v1/organizations/portfolio/ — the Organizations list: the
    viewer's visible book (archived out, churned out unless asked for),
    filtered, sorted, optionally grouped, and paged by an opaque cursor, with
    the summary tiles and group totals over the whole filtered set. Every row
    signal comes from the code the dashboard uses, so the two cannot disagree.
    Unknown parameter values are ignored. See docs/API_CONTRACTS.md."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        params = parse_params(request.query_params)
        portfolio = load_portfolio(request.user, params, today=timezone.localdate())
        return Response(build_listing(portfolio, params, filters=filter_options(request.user)))


class PortfolioExportView(APIView):
    """GET /api/v1/organizations/portfolio/export.csv — the same query as the
    list, every row (no pagination), in list order, with all 34 fields.
    Audited: this is confidential data leaving the app."""

    permission_classes = [IsAuthenticated]
    renderer_classes = [CSVRenderer]

    def get(self, request):
        params = parse_params(request.query_params)
        today = timezone.localdate()
        portfolio = load_portfolio(request.user, params, today=today)
        entries, _groups = select(portfolio, params)
        audit.record(  # SOC2:LOG-01
            "organizations.exported",
            request=request,
            # Parameter names only: a search term is the user's own words.
            metadata={"count": len(entries), "params": sorted(request.query_params.keys())},
        )
        response = Response(table([row_payload(entry) for entry in entries]))
        response["Content-Disposition"] = (
            f'attachment; filename="organizations-{today.isoformat()}.csv"'
        )
        return response


class BulkUpdateView(APIView):
    """POST /api/v1/organizations/bulk/ — `{ids, action, value}` from the
    selection bar. Applied per id under the single-edit rules; returns
    `{updated, failed: [{id, reason}]}` with a 200 even when some failed.
    Churn is not an action here: it keeps its own modal and endpoint."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = BulkRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = bulk.apply(request, ids=data["ids"], action=data["action"], value=data["value"])
        audit.record(  # SOC2:LOG-01
            "organizations.bulk_updated",
            request=request,
            outcome=(
                AuditEvent.Outcome.SUCCESS if result["updated"] else AuditEvent.Outcome.FAILURE
            ),
            metadata={
                "action": data["action"],
                "value": data["value"],
                "ids": result["updated"],
                "failed_ids": [row["id"] for row in result["failed"]],
            },
        )
        return Response(result)
