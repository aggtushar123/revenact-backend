from decimal import Decimal

from django.http import QueryDict
from django.test import SimpleTestCase

from services.pipelines_portfolio.book import load_book
from services.pipelines_portfolio.fields import fields_for
from services.pipelines_portfolio.kinds import KINDS, OPPORTUNITIES, RISKS
from services.pipelines_portfolio.params import parse_params
from services.pipelines_portfolio.rows import row_payload
from services.pipelines_portfolio.tests.fixtures import PipelineFixture

#: Every model field of both kinds, and the export column that carries it.
#: The kind's own date maps to "date". A new field fails this until it has a
#: column (and a place in the row).
COLUMN_FOR_MODEL_FIELD = {
    "id": "revenactId",
    "customer": "organizations",
    "account": "account",
    "title": "title",
    "mrr": "mrr",
    "stage": "stage",
    "priority": "priority",
    "department": "department",
    "created_at": "createdDate",
    "stage_changed_at": "stageChangedAt",
}


class FieldCoverageTests(SimpleTestCase):
    def test_every_field_of_both_kinds_has_a_column(self):
        for kind in KINDS.values():
            with self.subTest(kind=kind.key):
                expected = {**COLUMN_FOR_MODEL_FIELD, kind.date_field: "date"}
                self.assertEqual(
                    {field.name for field in kind.model._meta.concrete_fields}, set(expected)
                )
                ids = [field.id for field in fields_for(kind)]
                self.assertEqual(len(ids), len(set(ids)))
                self.assertLessEqual(set(expected.values()), set(ids))
                # The owner is the parent's: the one column with no field of its own.
                self.assertEqual(set(ids) - set(expected.values()), {"owner"})


class RowTests(PipelineFixture):
    def row(self, item, kind=OPPORTUNITIES, user=None):
        params = parse_params(QueryDict(f"ids={item.pk}"), kind)
        [entry] = load_book(user or self.csm, kind, params, today=self.today).entries
        return row_payload(entry, kind)

    def test_an_organisation_opportunity(self):
        item = self.opportunity(
            "Upsell", mrr=Decimal("2500.50"), expected_close=self.days(12), priority="high"
        )
        self.assertEqual(
            self.row(item),
            {
                "id": item.pk,
                "kind": "opportunity",
                "title": "Upsell",
                "parent": {"type": "organisation", "id": self.pizza.pk, "name": "Pizza Hut"},
                "companies": [{"id": self.pizza.pk, "name": "Pizza Hut"}],
                "owner": {"id": self.csm.pk, "name": "Carl CSM"},
                "mrr": 2500.5,
                "stage": {"value": "discovery", "label": "Discovery"},
                "priority": {"value": "high", "label": "High"},
                "department": {"value": "cs", "label": "Customer Success"},
                "date": {"value": self.days(12).isoformat(), "days": 12},
                "open": True,
                "overdue": False,
                "signal": {"kind": "high_priority", "label": "High priority"},
                "stage_changed_at": item.stage_changed_at.isoformat(),
                "created_at": item.created_at.isoformat(),
            },
        )

    def test_an_account_risk_with_a_hidden_organisation(self):
        shared = self.account("Shared", customers=[self.pizza, self.taco], owner=None)
        item = self.risk("Budget", account=shared, due_by=self.days(-2), department="")
        row = self.row(item, kind=RISKS)
        self.assertEqual(row["kind"], "risk")
        self.assertEqual(row["parent"], {"type": "account", "id": shared.pk, "name": "Shared"})
        self.assertEqual(row["companies"], [{"id": self.pizza.pk, "name": "Pizza Hut"}])
        self.assertIsNone(row["owner"])
        self.assertEqual(row["department"], {"value": "", "label": ""})
        self.assertEqual((row["date"]["days"], row["overdue"]), (-2, True))
        self.assertEqual(row["signal"], {"kind": "overdue", "label": "Overdue"})

    def test_closed_lost_reads_as_a_closed_stage(self):
        row = self.row(self.opportunity("Gone", stage="closed_lost", priority="high"))
        self.assertEqual(
            (row["stage"], row["open"], row["signal"]),
            ({"value": "closed_lost", "label": "Closed Lost"}, False, None),
        )

    def test_the_export_columns_read_the_row(self):
        shared = self.account("Shared", customers=[self.pizza, self.taco], owner=None)
        item = self.opportunity("Seats", account=shared, expected_close=self.days(3))
        row = self.row(item, user=self.admin)
        values = {field.id: field.value(row) for field in fields_for(OPPORTUNITIES)}
        self.assertEqual(values["organizations"], "Pizza Hut; Taco Bell")
        self.assertEqual(
            (values["account"], values["owner"], values["date"]),
            ("Shared", "Unassigned", self.days(3).isoformat()),
        )
        self.assertIn("Due By", [field.label for field in fields_for(RISKS)])
