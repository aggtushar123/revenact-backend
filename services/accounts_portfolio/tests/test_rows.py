from datetime import timedelta
from decimal import Decimal

from django.test import SimpleTestCase

from services.accounts_portfolio import book, rows
from services.accounts_portfolio.fields import FIELDS
from services.accounts_portfolio.params import parse_params
from services.accounts_portfolio.tests.fixtures import AccountPortfolioFixture
from services.customers.models import Account, Customer

#: Every Account field, and the export column that carries it. The spec's
#: promise is that none is lost; a new model field fails this until it has a
#: column (and a place in the row or panels).
COLUMN_FOR_MODEL_FIELD = {
    "id": "revenactId",
    "customers": "organizations",
    "name": "account",
    "domain": "domain",
    "industry": "industry",
    "address": "address",
    "email": "email",
    "phone": "phone",
    "owner": "owner",
    "created_at": "createdDate",
    "updated_at": "modifiedDate",
    "lifecycle_stage": "lifecycleStage",
    "health_score": "health",
    "pulse": "pulse",
    "ai_pulse_value": "aiPulseValue",
    "ai_pulse_reason": "aiPulseReason",
    "csm_pulse_score": "csmPulseScore",
    "csm_pulse_modified_at": "csmPulseModifiedAt",
    "pulse_recorded_on": "pulseRecordedOn",
    "nps_score": "nps",
    "csat_score": "csatScore",
    "renewal_date": "renewalDate",
    "arr": "arr",
}


class FieldCoverageTests(SimpleTestCase):
    def test_every_account_field_has_a_column(self):
        model_fields = {f.name for f in Account._meta.concrete_fields} | {
            f.name for f in Account._meta.many_to_many
        }
        self.assertEqual(model_fields, set(COLUMN_FOR_MODEL_FIELD))
        ids = [field.id for field in FIELDS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertLessEqual(set(COLUMN_FOR_MODEL_FIELD.values()), set(ids))
        # The derived AI pulse label is the one column with no field of its own.
        self.assertEqual(set(ids) - set(COLUMN_FOR_MODEL_FIELD.values()), {"aiPulseScore"})


class RowTests(AccountPortfolioFixture):
    def setUp(self):
        super().setUp()
        self.emea = self.account(
            "Pizza Hut EMEA",
            customers=[self.pizza, self.taco],
            domain="emea.pizzahut.example",
            industry="Food",
            address="1 Main St, London",
            email="emea@pizzahut.example",
            phone="+44 20 0000",
            lifecycle_stage="live",
            health_score=Decimal("4.9"),
            pulse=[1, 2, 2],
            ai_pulse_value=1,
            ai_pulse_reason="Quiet since the outage.",
            csm_pulse_score=3,
            nps_score=-80,
            csat_score=Decimal("72.50"),
            renewal_date=self.today - timedelta(days=47),
            arr=Decimal("69600"),
            pulse_recorded_on=self.today,
        )

    def row(self, account=None, user=None):
        account = account or self.emea
        portfolio = book.load_portfolio(user or self.admin, parse_params({}), today=self.today)
        entry = next(e for e in portfolio.entries if e.account.pk == account.pk)
        return rows.row_payload(entry)

    def test_the_header(self):
        row = self.row()
        self.assertEqual(
            set(row),
            {
                "id", "name", "initials", "owner", "lifecycle", "health", "renewal", "arr",
                "risk", "pulse", "last_touch_days", "urgent_tickets", "signal",
                "organisation", "extra_organisations", "details",
            },
        )  # fmt: skip
        self.assertEqual(
            (row["id"], row["name"], row["initials"]), (self.emea.pk, "Pizza Hut EMEA", "PH")
        )
        self.assertEqual(row["owner"], {"id": self.csm.pk, "name": "Carl CSM"})
        self.assertEqual(row["lifecycle"], {"value": "live", "label": "Live"})
        self.assertEqual(row["health"], {"score": 4.9, "category": "average", "trend": [4.9]})
        self.assertEqual(
            row["renewal"],
            {"date": (self.today - timedelta(days=47)).isoformat(), "days": -47},
        )
        self.assertEqual(row["arr"], 69600.0)
        self.assertEqual(set(row["risk"]), {"score", "direction"})
        self.assertEqual(
            row["pulse"],
            {
                "csm": 3,
                "ai": 1,
                "ai_category": "high_risk",
                "ai_label": "High Risk",
                "reason": "Quiet since the outage.",
                "history": [1, 2, 2],
                "disagree": True,
            },
        )
        self.assertIsNone(row["last_touch_days"])
        self.assertEqual(row["urgent_tickets"], 0)
        self.assertEqual(row["signal"], {"kind": "renewal_overdue", "label": "Renewal overdue"})
        self.assertEqual(row["organisation"], {"id": self.pizza.pk, "name": "Pizza Hut"})
        self.assertEqual(row["extra_organisations"], 1)

    def test_the_panels_hold_the_other_fields(self):
        details = self.row()["details"]
        self.assertEqual(set(details), {"commercial", "voice", "profile", "history"})
        self.assertEqual(
            details["commercial"],
            {"arr": 69600.0, "renewal_date": (self.today - timedelta(days=47)).isoformat()},
        )
        self.assertEqual(
            details["voice"],
            {"nps_score": -80, "csat_score": 72.5, "ai_pulse_reason": "Quiet since the outage."},
        )
        self.assertEqual(
            details["profile"],
            {
                "revenact_id": self.emea.pk,
                "domain": "emea.pizzahut.example",
                "industry": "Food",
                "email": "emea@pizzahut.example",
                "phone": "+44 20 0000",
                "address": "1 Main St, London",
                "organisations": [
                    {"id": self.pizza.pk, "name": "Pizza Hut"},
                    {"id": self.taco.pk, "name": "Taco Bell"},
                ],
            },
        )
        self.emea.refresh_from_db()
        self.assertEqual(
            details["history"],
            {
                "created_at": self.emea.created_at.isoformat(),
                "updated_at": self.emea.updated_at.isoformat(),
                "pulse_recorded_on": self.today.isoformat(),
                "csm_pulse_modified_at": None,
            },
        )

    def test_an_account_whose_organisations_are_all_closed_to_the_viewer(self):
        # A customer Carl has no path to at all: owned by Dana, and (unlike
        # Taco Bell, which self.emea's Carl ownership opens up in setUp)
        # holding no account of Carl's. Only this way is it actually closed
        # to him, proving the privacy rule rather than an artifact of setUp.
        wendys = Customer.objects.create(organisation=self.org, name="Wendy's", owner=self.other)
        pool = self.account("Pool", customers=[wendys], owner=None)
        row = self.row(pool, user=self.csm)
        self.assertIsNone(row["organisation"])
        self.assertEqual(row["extra_organisations"], 0)
        self.assertEqual(row["details"]["profile"]["organisations"], [])
        self.assertIsNone(row["owner"])

    def test_the_export_fields_read_the_row(self):
        values = {field.id: field.value(self.row()) for field in FIELDS}
        self.assertEqual(values["account"], "Pizza Hut EMEA")
        self.assertEqual(values["organizations"], "Pizza Hut; Taco Bell")
        self.assertEqual(values["owner"], "Carl CSM")
        self.assertEqual(values["pulse"], "1 2 2")
        self.assertEqual(values["aiPulseScore"], "High Risk")
        self.assertEqual(values["arr"], 69600.0)
        self.assertEqual(values["nps"], -80)
