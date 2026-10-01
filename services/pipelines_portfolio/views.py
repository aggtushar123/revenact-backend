from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core import audit
from services.organizations.export import CSVRenderer
from services.portfolio_core.bulk import audited_batch

from . import bulk
from .book import filter_options, load_book
from .export import table
from .kinds import KINDS
from .params import parse_params
from .rows import row_payload
from .serializers import BulkRequestSerializer
from .shape import build_listing, select


class PipelineView(APIView):
    """GET /api/v1/pipelines/opportunities/ and /api/v1/pipelines/risks/ —
    the Pipelines list and Board: every opportunity or risk the viewer may
    read (twice filtered, `book.scope`), filtered, sorted, optionally
    grouped and paged by an opaque cursor, with the tiles over every stage
    of the filtered set and the group totals. Unknown parameter values are
    ignored. See docs/API_CONTRACTS.md."""

    # SOC2:AUTH-02 authentication only; `load_book`/`filter_options`
    # (services/pipelines_portfolio/book.py) do the record-visibility checks
    # through `visible_children_q`, `pipeline_visible_q`, `visible_customers`
    # and `visible_accounts`.
    permission_classes = [IsAuthenticated]

    def get(self, request, kind_key):
        kind = KINDS[kind_key]
        params = parse_params(request.query_params, kind)
        today = timezone.localdate()
        book = load_book(request.user, kind, params, today=today)
        filters = filter_options(request.user, kind)
        return Response(build_listing(book, params, filters=filters, today=today))


class PipelineExportView(APIView):
    """GET /api/v1/pipelines/<kind>/export.csv — the same query as the list,
    every row (no pagination; the chosen stages and `group_value` apply), in
    list order, with every field. Audited: this is confidential data leaving
    the app."""

    # SOC2:AUTH-02 authentication only; `load_book` (book.py) does the
    # record-visibility checks — the export is the same twice-filtered set
    # the list endpoint returns.
    permission_classes = [IsAuthenticated]
    renderer_classes = [CSVRenderer]

    def get(self, request, kind_key):
        kind = KINDS[kind_key]
        params = parse_params(request.query_params, kind)
        today = timezone.localdate()
        book = load_book(request.user, kind, params, today=today)
        entries, _groups = select(book, params)
        audit.record(  # SOC2:LOG-01
            "pipelines.exported",
            request=request,
            # Parameter names only: a search term is the user's own words.
            metadata={
                "kind": kind.key,
                "count": len(entries),
                "params": sorted(request.query_params.keys()),
            },
        )
        rows = [row_payload(entry, kind) for entry in entries]
        response = Response(table(rows, kind=kind, currency=book.organisation.currency))
        response["Content-Disposition"] = (
            f'attachment; filename="{kind.key}-{today.isoformat()}.csv"'
        )
        return response


class PipelineBulkUpdateView(APIView):
    """POST /api/v1/pipelines/<kind>/bulk/ — `{ids, action, value}` from the
    selection bar: `set_stage`, `set_priority`, `set_department` or
    `set_date`. Applied per id under the single-edit rules; returns
    `{updated, failed: [{id, reason}]}` with a 200 even when some failed."""

    # SOC2:AUTH-02 authentication only; the per-id visibility checks are in
    # `bulk.apply` (services/pipelines_portfolio/bulk.py).
    permission_classes = [IsAuthenticated]

    def post(self, request, kind_key):
        kind = KINDS[kind_key]
        serializer = BulkRequestSerializer(data=request.data, context={"kind": kind})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        # SOC2:LOG-01 one `pipelines.bulk_updated` event, recorded even if
        # something escapes mid-batch (`audited_batch`). Ids and the field
        # only, never the value (spec §2).
        result = audited_batch(
            request,
            "pipelines.bulk_updated",
            lambda result: bulk.apply(
                request,
                kind,
                ids=data["ids"],
                action=data["action"],
                value=data["value"],
                result=result,
            ),
            metadata={
                "kind": kind.key,
                "action": data["action"],
                "field": bulk.field_for(kind, data["action"]),
            },
        )
        return Response(result)
