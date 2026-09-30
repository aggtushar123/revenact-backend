from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .book import filter_options, load_book
from .kinds import KINDS
from .params import parse_params
from .shape import build_listing


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
