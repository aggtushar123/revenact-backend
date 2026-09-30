import csv
import io
from decimal import Decimal

from rest_framework.test import APIClient

from core.models import AuditEvent
from services.accounts.models import User
from services.customers.tests.test_views import blind_to_one_account
from services.pipelines_portfolio.fields import fields_for
from services.pipelines_portfolio.kinds import OPPORTUNITIES
from services.pipelines_portfolio.tests.fixtures import PipelineFixture

URL = "/api/v1/pipelines/{}/export.csv"


class ExportEndpointTests(PipelineFixture):
    def download(self, user=None, kind="opportunities", **query):
        api = APIClient()
        api.force_authenticate(user or self.csm)
        response = api.get(URL.format(kind), query)
        return response, list(csv.reader(io.StringIO(response.content.decode())))

    def test_requires_authentication(self):
        response = APIClient().get(URL.format("risks"))
        self.assertEqual(response.status_code, 401)
        self.assertTrue(response["Content-Type"].startswith("text/csv"))

    def test_every_field_every_row_no_pagination(self):
        for i in range(60):
            self.opportunity(f"Deal {i:02d}", mrr=Decimal(i))
        response, table = self.download()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/csv"))
        self.assertIn(
            f'filename="opportunities-{self.today.isoformat()}.csv"',
            response["Content-Disposition"],
        )
        self.assertIn("attachment;", response["Content-Disposition"])
        header, rows = table[0], table[1:]
        self.assertEqual(
            header, [field.label for field in fields_for(OPPORTUNITIES)] + ["Currency"]
        )
        self.assertEqual(len(header), 13)
        self.assertEqual(len(rows), 60)
        self.assertEqual((rows[0][0], rows[0][-1]), ("Deal 59", "USD"))

    def test_risks_carry_their_due_by(self):
        self.risk("Budget", due_by=self.days(3))
        _response, table = self.download(kind="risks")
        column = table[0].index("Due By")
        self.assertEqual(table[1][column], self.days(3).isoformat())

    def test_formula_cells_are_neutralised(self):
        self.opportunity('=HYPERLINK("http://evil")')
        _response, table = self.download()
        self.assertEqual(table[1][0], '\'=HYPERLINK("http://evil")')

    def test_every_formula_prefix_is_escaped(self):
        for i, prefix in enumerate(("=", "+", "-", "@")):
            self.opportunity(f"{prefix}Deal {i}")
        _response, table = self.download()
        titles = {row[0] for row in table[1:]}
        self.assertEqual(
            titles, {f"'{prefix}Deal {i}" for i, prefix in enumerate(("=", "+", "-", "@"))}
        )

    def test_the_export_is_the_viewers_own(self):
        viewer, seen, hidden = blind_to_one_account(self.pizza)
        self.opportunity("On seen", account=seen)
        self.opportunity("On hidden", account=hidden)
        self.opportunity("Sales'", department=User.Function.SALES)
        _response, table = self.download(viewer)
        self.assertEqual([row[0] for row in table[1:]], ["On seen"])

    def test_the_export_is_audited_without_the_search_term(self):
        self.opportunity("Upsell")
        self.download(search="secret words", stage=",".join(OPPORTUNITIES.stages))
        event = AuditEvent.objects.get(action="pipelines.exported")
        self.assertEqual(event.actor, self.csm)
        self.assertEqual(
            event.metadata, {"kind": "opportunities", "count": 0, "params": ["search", "stage"]}
        )
