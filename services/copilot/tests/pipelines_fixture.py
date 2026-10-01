"""Shared set-up for Ask Revenact on Pipelines: the Pipelines fixture's
people (Carl and Dana, CSMs in Acme; Sid in Sales; Alice, its Leadership
admin; Globex, another tenant; Carl owns Pizza Hut, Dana owns Taco Bell)
and, after `book()`, a book for Carl that fills every tile. Opportunities:
an overdue High deal, a deal on his EMEA account closing in 12 days, one in
200 days, one with no date, one won and one lost. Risks: one due in 20
days, one overdue on EMEA, one mitigated. It also holds a Sales deal, Dana's
Taco Bell deal and another tenant's deal, which Carl must never see."""

from decimal import Decimal

from rest_framework.test import APIClient

from services.accounts.models import User
from services.copilot.pipelines_context import clean_filters
from services.pipelines_portfolio.kinds import KINDS
from services.pipelines_portfolio.tests.fixtures import PipelineFixture

PIPELINE_URL = "/api/v1/pipelines/{}/"


class PipelinesAskFixture(PipelineFixture):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.csm)

    def book(self):
        self.emea = self.account("EMEA")
        self.upsell = self.opportunity(
            "Upsell",
            mrr=Decimal("3000"),
            stage="negotiation",
            priority="high",
            expected_close=self.days(-5),
        )
        self.seats = self.opportunity(
            "EMEA seats", account=self.emea, mrr=Decimal("2000"), expected_close=self.days(12)
        )
        self.later = self.opportunity(
            "Next year", mrr=Decimal("800"), expected_close=self.days(200)
        )
        self.someday = self.opportunity("Someday", mrr=Decimal("500"))
        self.won = self.opportunity(
            "Won deal", mrr=Decimal("4000"), stage="closed_won", expected_close=self.days(-2)
        )
        self.lost = self.opportunity("Lost deal", mrr=Decimal("700"), stage="closed_lost")
        self.sales_deal = self.opportunity(
            "Sales deal",
            mrr=Decimal("9000"),
            department=User.Function.SALES,
            expected_close=self.days(3),
        )
        self.taco_deal = self.opportunity(
            "Taco deal", customer=self.taco, mrr=Decimal("8000"), expected_close=self.days(4)
        )
        self.globex_deal = self.opportunity(
            "Globex deal", customer=self.globex, mrr=Decimal("7000"), department=""
        )
        self.budget = self.risk("Budget cut", mrr=Decimal("600"), due_by=self.days(20))
        self.late = self.risk(
            "Late fix", account=self.emea, mrr=Decimal("400"), due_by=self.days(-3)
        )
        self.handled = self.risk("Handled", mrr=Decimal("300"), stage="mitigated")

    @staticmethod
    def context(kind="opportunities", view="list", focus=None, **filters):
        return {
            "surface": "pipelines",
            "kind": kind,
            "view": view,
            "filters": clean_filters(filters, KINDS[kind], view),
            "label": "Pipelines",
            "focus": focus,
        }

    def pipeline(self, kind="opportunities", **query):
        response = self.api.get(PIPELINE_URL.format(kind), query)
        assert response.status_code == 200, response.data
        return response.data
