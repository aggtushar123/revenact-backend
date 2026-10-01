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
LIST_URL = "/api/v1/pipelines/{}/"
EVERY_STAGE = ",".join(OPPORTUNITIES.stages)


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


class ListExportParityTests(PipelineFixture):
    """Controller ruling (carried from Task 11's review): for the same query
    params, paging the list endpoint to its end must name exactly the ids
    export.csv names — a guard against the two paths drifting apart (one
    gaining a filter the other lacks). Two distinct filter combinations, so a
    parity break in either direction gets caught."""

    def setUp(self):
        super().setUp()
        emea = self.account("Pizza EMEA")
        self.zero = self.opportunity("Deal Zero", mrr=Decimal("500"), priority="medium")
        self.one = self.opportunity("Deal One", account=emea, mrr=Decimal("2000"), priority="high")
        self.two = self.opportunity(
            "Deal Two", mrr=Decimal("100"), priority="low", stage="closed_won"
        )
        self.three = self.opportunity(
            "Deal Three", mrr=Decimal("700"), priority="high", stage="closed_lost"
        )
        self.four = self.opportunity("Deal Four", mrr=Decimal("1500"), priority="low")
        self.five = self.opportunity(
            "Deal Five", account=emea, mrr=Decimal("300"), priority="medium"
        )

    def list_ids(self, kind="opportunities", **query):
        api = APIClient()
        api.force_authenticate(self.csm)
        ids, cursor = [], None
        for _ in range(20):
            page_query = {**query, "limit": "2"}
            if cursor:
                page_query["cursor"] = cursor
            response = api.get(LIST_URL.format(kind), page_query)
            self.assertEqual(response.status_code, 200, response.content)
            ids += [row["id"] for row in response.data["results"]]
            cursor = response.data["next_cursor"]
            if cursor is None:
                return ids
        raise AssertionError("cursor never reached the end of the list")

    def export_ids(self, kind="opportunities", **query):
        api = APIClient()
        api.force_authenticate(self.csm)
        response = api.get(URL.format(kind), query)
        self.assertEqual(response.status_code, 200, response.content)
        table = list(csv.reader(io.StringIO(response.content.decode())))
        column = table[0].index("Revenact ID")
        return [int(row[column]) for row in table[1:]]

    def test_default_open_stages(self):
        list_ids = self.list_ids()
        self.assertEqual(list_ids, self.export_ids())
        self.assertEqual(
            sorted(list_ids), sorted(o.pk for o in (self.zero, self.one, self.four, self.five))
        )

    def test_every_stage_with_a_priority_filter(self):
        query = {"stage": EVERY_STAGE, "priority": "high,low"}
        list_ids = self.list_ids(**query)
        self.assertEqual(list_ids, self.export_ids(**query))
        self.assertEqual(
            sorted(list_ids), sorted(o.pk for o in (self.one, self.two, self.three, self.four))
        )
