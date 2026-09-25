import csv
import io
from decimal import Decimal

from django.test import SimpleTestCase
from rest_framework.test import APIClient

from core.models import AuditEvent
from services.organizations import export
from services.organizations.fields import FIELDS
from services.organizations.tests.fixtures import PortfolioFixture

URL = "/api/v1/organizations/portfolio/export.csv"


class CellTests(SimpleTestCase):
    def test_formula_cells_are_neutralised(self):
        self.assertEqual(export.cell("=HYPERLINK(1)"), "'=HYPERLINK(1)")
        for prefix in ("+", "-", "@", "\t", "\r"):
            self.assertEqual(export.cell(f"{prefix}x"), f"'{prefix}x")
        self.assertEqual(export.cell("Pizza Hut"), "Pizza Hut")

    def test_numbers_stay_numbers_and_blanks_are_empty(self):
        self.assertEqual(export.cell(-80), -80)
        self.assertEqual(export.cell(4.9), 4.9)
        self.assertEqual(export.cell(None), "")
        self.assertEqual(export.cell(True), "Yes")


class ExportEndpointTests(PortfolioFixture):
    def download(self, user=None, **query):
        api = APIClient()
        api.force_authenticate(user or self.csm)
        response = api.get(URL, query)
        return response, list(csv.reader(io.StringIO(response.content.decode())))

    def test_requires_authentication(self):
        self.assertEqual(APIClient().get(URL).status_code, 401)

    def test_every_field_every_row_no_pagination(self):
        for i in range(60):
            self.customer(f"Co {i:02d}")
        response, table = self.download()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/csv"))
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertIn(
            f"organizations-{self.today.isoformat()}.csv", response["Content-Disposition"]
        )
        header, rows = table[0], table[1:]
        self.assertEqual(header, [field.label for field in FIELDS] + ["Currency"])
        self.assertEqual(len(header), 35)
        self.assertEqual(len(rows), 60)

    def test_same_params_same_rows_and_visibility(self):
        self.customer("Poor one", health_score=Decimal("2.0"))
        self.customer("Good one")
        self.customer("Dana's", owner=self.other, health_score=Decimal("2.0"))
        _response, table = self.download(health="poor", sort="name")
        self.assertEqual([row[0] for row in table[1:]], ["Poor one"])

    def test_values_and_injection(self):
        self.customer(
            '=HYPERLINK("http://evil.example")', currency="EUR", nps_score=-80, owner=None
        )
        _response, table = self.download()
        row = dict(zip(table[0], table[1], strict=True))
        self.assertTrue(row["Organization"].startswith("'="))
        self.assertEqual(row["Owner"], "Unassigned")
        self.assertEqual(row["NPS"], "-80")
        self.assertEqual(row["Currency"], "EUR")
        self.assertEqual(row["Total ARR Billed At Account"], "12000.0")

    def test_the_export_is_audited(self):
        self.customer("A")
        self.customer("B")
        self.download(health="good", sort="name")
        event = AuditEvent.objects.get(action="organizations.exported")
        self.assertEqual(event.actor, self.csm)
        self.assertEqual(event.organisation, self.org)
        self.assertEqual(event.metadata, {"count": 2, "params": ["health", "sort"]})
