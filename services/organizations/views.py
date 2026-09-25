from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .book import filter_options, load_portfolio
from .params import parse_params
from .shape import build_listing


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
