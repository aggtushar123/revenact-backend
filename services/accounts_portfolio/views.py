from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core import audit
from services.organizations.export import CSVRenderer

from .book import filter_options, load_portfolio
from .export import table
from .params import parse_params
from .rows import row_payload
from .shape import build_listing, select


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


class AccountPortfolioExportView(APIView):
    """GET /api/v1/accounts/portfolio/export.csv — the same query as the
    list, every row (no pagination), in list order, with every Account field.
    Audited: this is confidential data leaving the app."""

    # SOC2:AUTH-02 authentication only; `load_portfolio`/`select` (via
    # `book.load_portfolio`) do the actual record-visibility checks through
    # `visible_accounts`/`visible_customers`/`visible_tickets` — the export
    # is the same filtered, visible set the list endpoint returns.
    permission_classes = [IsAuthenticated]
    renderer_classes = [CSVRenderer]

    def get(self, request):
        params = parse_params(request.query_params)
        today = timezone.localdate()
        portfolio = load_portfolio(request.user, params, today=today)
        entries, _groups = select(portfolio, params)
        audit.record(  # SOC2:LOG-01
            "accounts.exported",
            request=request,
            # Parameter names only: a search term is the user's own words.
            metadata={"count": len(entries), "params": sorted(request.query_params.keys())},
        )
        response = Response(
            table(
                [row_payload(entry) for entry in entries],
                currency=portfolio.organisation.currency,
            )
        )
        response["Content-Disposition"] = f'attachment; filename="accounts-{today.isoformat()}.csv"'
        return response
