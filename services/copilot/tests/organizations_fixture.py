"""Shared set-up for Ask Revenact on Organizations: PortfolioFixture's people
(Carl and Dana, CSMs in Acme; Alice, its admin; Globex, another tenant) and,
after `book()`, one book for Carl that exercises every tile — an overdue
renewal, a Poor account renewing in 20 days, an unassigned account renewing
later, a churned account — plus Dana's account, which Carl must never see."""

from datetime import timedelta
from decimal import Decimal

from rest_framework.test import APIClient

from services.customers.models import HealthSnapshot
from services.organizations.tests.fixtures import PortfolioFixture

GOOD, AVERAGE, POOR = Decimal("8.0"), Decimal("5.0"), Decimal("2.0")
PORTFOLIO_URL = "/api/v1/organizations/portfolio/"


class OrganizationsAskFixture(PortfolioFixture):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.csm)

    def book(self):
        self.pizza = self.customer(
            "Pizza Hut",
            health_score=AVERAGE,
            renewal_date=self.today - timedelta(days=47),
            arr_billed_at_account=Decimal("69600"),
            lifecycle_stage="live",
            nps_score=-80,
        )
        self.hooli = self.customer(
            "Hooli",
            health_score=POOR,
            renewal_date=self.today + timedelta(days=20),
            lifecycle_stage="renewal",
            nps_score=40,
            csm_pulse_score=4,
            ai_pulse_value=1,
        )
        self.initech = self.customer(
            "Initech",
            owner=None,
            renewal_date=self.today + timedelta(days=120),
            lifecycle_stage="adoption",
        )
        self.umbrella = self.customer(
            "Umbrella",
            health_score=POOR,
            churn_date=self.today - timedelta(days=5),
            lifecycle_stage="churn",
        )
        self.danas = self.customer(
            "Dana's Co",
            owner=self.other,
            health_score=POOR,
            renewal_date=self.today + timedelta(days=3),
        )
        HealthSnapshot.objects.create(
            customer=self.pizza,
            captured_on=self.today - timedelta(days=40),
            health_score=GOOD,
        )

    def context(self, view="list", focus=None, **filters):
        return {"surface": "organizations", "view": view, "filters": filters, "focus": focus}

    def portfolio(self, user=None, **query):
        api = APIClient()
        api.force_authenticate(user or self.csm)
        response = api.get(PORTFOLIO_URL, {"limit": 100, **query})
        self.assertEqual(response.status_code, 200, response.content)
        return response.data
