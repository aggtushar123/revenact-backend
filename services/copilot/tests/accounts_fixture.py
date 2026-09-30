"""Shared set-up for Ask Revenact on Accounts' list and Board: the portfolio
fixture's people (Carl and Dana, CSMs in Acme; Alice, its Leadership admin;
Globex, another tenant; Carl owns Pizza Hut, Dana owns Taco Bell) and, after
`book()`, a book for Carl that exercises every tile — an overdue renewal, a
Poor account renewing in 20 days, an unowned account renewing later — plus
Dana's account and another tenant's, which Carl must never see."""

from datetime import timedelta
from decimal import Decimal

from rest_framework.test import APIClient

from services.accounts_portfolio.tests.fixtures import AccountPortfolioFixture
from services.copilot.accounts_context import clean_filters

GOOD, AVERAGE, POOR = Decimal("8.0"), Decimal("5.0"), Decimal("2.0")
PORTFOLIO_URL = "/api/v1/accounts/portfolio/"


class AccountsAskFixture(AccountPortfolioFixture):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.csm)

    def book(self):
        self.emea = self.account(
            "EMEA",
            health_score=AVERAGE,
            renewal_date=self.today - timedelta(days=47),
            arr=Decimal("69600"),
            lifecycle_stage="live",
            nps_score=-80,
        )
        self.apac = self.account(
            "APAC",
            health_score=POOR,
            renewal_date=self.today + timedelta(days=20),
            lifecycle_stage="renewal",
            nps_score=40,
            csm_pulse_score=4,
            ai_pulse_value=1,
        )
        self.latam = self.account(
            "LATAM",
            owner=None,
            renewal_date=self.today + timedelta(days=120),
            lifecycle_stage="adoption",
        )
        self.danas = self.account(
            "Dana's Taco",
            customers=[self.taco],
            owner=self.other,
            health_score=POOR,
            renewal_date=self.today + timedelta(days=3),
        )
        self.globex_eu = self.account(
            "Globex EU",
            customers=[self.globex],
            owner=None,
            renewal_date=self.today + timedelta(days=5),
        )

    @staticmethod
    def context(view="list", **filters):
        return {
            "surface": "accounts",
            "view": view,
            "filters": clean_filters(filters),
            "label": "Accounts",
        }

    def portfolio(self, **query):
        response = self.api.get(PORTFOLIO_URL, query)
        assert response.status_code == 200, response.data
        return response.data
