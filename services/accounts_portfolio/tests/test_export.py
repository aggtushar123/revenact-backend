import csv
import io
from decimal import Decimal

from rest_framework.test import APIClient

from core.models import AuditEvent
from services.accounts_portfolio.fields import FIELDS
from services.accounts_portfolio.tests.fixtures import AccountPortfolioFixture
from services.customers.models import Customer
from services.customers.tests.test_views import blind_to_one_account

URL = "/api/v1/accounts/portfolio/export.csv"


class ExportEndpointTests(AccountPortfolioFixture):
    def download(self, user=None, **query):
        api = APIClient()
        api.force_authenticate(user or self.csm)
        response = api.get(URL, query)
        return response, list(csv.reader(io.StringIO(response.content.decode())))

    def test_requires_authentication(self):
        response = APIClient().get(URL)
        self.assertEqual(response.status_code, 401)
        self.assertTrue(response["Content-Type"].startswith("text/csv"))

    def test_every_field_every_row_no_pagination(self):
        for i in range(60):
            self.account(f"Div {i:02d}")
        response, table = self.download()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/csv"))
        self.assertIn(
            f'filename="accounts-{self.today.isoformat()}.csv"', response["Content-Disposition"]
        )
        self.assertIn("attachment;", response["Content-Disposition"])
        header, rows = table[0], table[1:]
        self.assertEqual(header, [field.label for field in FIELDS] + ["Currency"])
        self.assertEqual(len(header), 25)
        self.assertEqual(len(rows), 60)

    def test_same_params_same_rows_and_visibility(self):
        self.account("Poor one", health_score=Decimal("2.0"))
        self.account("Good one")
        self.account("Dana's", customers=[self.taco], owner=self.other, health_score=Decimal("2.0"))
        _response, table = self.download(health="poor", sort="name")
        self.assertEqual([row[0] for row in table[1:]], ["Poor one"])

    def test_blind_to_one_account(self):
        viewer, _seen, _hidden = blind_to_one_account(self.pizza)
        _response, table = self.download(viewer)
        self.assertEqual([row[0] for row in table[1:]], ["Seen"])

    def test_values_and_formula_injection(self):
        evil = Customer.objects.create(organisation=self.org, name="+SUM(A1)", owner=self.csm)
        self.account(
            '=HYPERLINK("http://evil.example")',
            customers=[self.pizza, evil],
            owner=None,
            nps_score=-80,
            email="@ops.example",
        )
        _response, table = self.download(self.admin)
        row = dict(zip(table[0], table[1], strict=True))
        self.assertTrue(row["Account"].startswith("'="))
        self.assertEqual(row["Organizations"], "Pizza Hut; +SUM(A1)")
        self.assertEqual(row["Email"], "'@ops.example")
        self.assertEqual(row["Owner"], "Unassigned")
        self.assertEqual(row["NPS"], "-80")
        self.assertEqual(row["ARR"], "12000.0")
        self.assertEqual(row["Currency"], "USD")

    def test_the_export_is_audited(self):
        self.account("A")
        self.account("B")
        self.download(health="good", sort="name")
        event = AuditEvent.objects.get(action="accounts.exported")
        self.assertEqual(event.actor, self.csm)
        self.assertEqual(event.organisation, self.org)
        self.assertEqual(event.metadata, {"count": 2, "params": ["health", "sort"]})
