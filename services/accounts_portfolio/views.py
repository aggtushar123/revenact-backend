from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .book import filter_options, load_portfolio
from .params import parse_params
from .shape import build_listing


class AccountPortfolioView(APIView):
    """GET /api/v1/accounts/portfolio/ — the Accounts list and Board: the
    viewer's visible accounts (no archive or churn to hide), filtered,
    sorted, optionally grouped, and paged by an opaque cursor, with the
    summary tiles and group totals over the whole filtered set. Row signals
    come from the dashboard's own code. Unknown parameter values are ignored.
    See docs/API_CONTRACTS.md."""

    # SOC2:AUTH-02 authentication only; `load_portfolio`/`filter_options`
    # (services/accounts_portfolio/book.py) do the actual record-visibility
    # checks through `visible_accounts`/`visible_customers`/`visible_tickets`.
    permission_classes = [IsAuthenticated]

    def get(self, request):
        params = parse_params(request.query_params)
        portfolio = load_portfolio(request.user, params, today=timezone.localdate())
        return Response(build_listing(portfolio, params, filters=filter_options(request.user)))
