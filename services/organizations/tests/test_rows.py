from datetime import date, timedelta
from decimal import Decimal

from django.test import SimpleTestCase

from services.customers.models import Product
from services.organizations import book, rows
from services.organizations.fields import FIELDS
from services.organizations.params import parse_params
from services.organizations.tests.fixtures import PortfolioFixture

#: `ColumnId` in react-ts-app `src/components/organizations/tableData.ts`, in
#: `ALL_COLUMNS` order. The page's promise is that none of these is lost.
FRONTEND_COLUMN_IDS = [
    "organization", "revenactId", "owner", "lifecycleStage", "health", "pulse",
    "aiPulseScore", "aiPulseReason", "nps", "csatScore", "joinedDate", "renewalDate",
    "arrAccount", "arrHQ", "implFee", "tcv", "tcvRenewal", "contractStart", "contractEnd",
    "productsUtilized", "topSourceChannel", "totalContractedSeats", "totalActiveSeats",
    "totalSeatUtilization", "totalHires", "scopeWebApp", "cesPercentage", "churnDate",
    "churnReason", "churnComment", "domain", "createdBy", "modifiedBy", "nameAddress",
]  # fmt: skip


class InitialsTests(SimpleTestCase):
    def test_the_frontend_rule(self):
        self.assertEqual(rows.initials("Pizza Hut"), "PH")
        self.assertEqual(rows.initials("acme"), "A")
        self.assertEqual(rows.initials("Bank of the West"), "BO")
        self.assertEqual(rows.initials("   "), "?")


class RowFixture(PortfolioFixture):
    """A fully filled-in customer, and `row()` to read one customer's row."""

    def setUp(self):
        super().setUp()
        core = Product.objects.create(organisation=self.org, name="Core")
        self.pizza = self.customer(
            "Pizza Hut",
            address="1 Main St, Dallas",
            domain="pizzahut.example",
            created_by=self.admin,
            modified_by=self.csm,
            lifecycle_stage="live",
            health_score=Decimal("4.9"),
            pulse=[1, 2, 2],
            ai_pulse_value=1,
            ai_pulse_reason="Quiet since the outage.",
            csm_pulse_score=3,
            nps_score=-80,
            csat_score=Decimal("72.50"),
            joined_date=date(2024, 1, 15),
            renewal_date=self.today - timedelta(days=47),
            contract_start_date=date(2024, 2, 1),
            contract_end_date=date(2027, 1, 31),
            arr_billed_at_account=Decimal("69600"),
            arr_billed_at_hq=Decimal("10000"),
            implementation_fee=Decimal("5000"),
            total_contract_value=Decimal("208800"),
            total_forecasted_renewal_revenue=Decimal("70000"),
            primary_product=core,
            additional_products_count=2,
            top_source_channel="Referral",
            total_contracted_seats=100,
            total_active_seats=16,
            total_hires=40,
            scope_web_app="Full",
            ces_percentage=Decimal("61.00"),
        )
        self.core = core

    def row(self, customer, **query):
        portfolio = book.load_portfolio(self.csm, parse_params(query), today=self.today)
        entry = next(e for e in portfolio.entries if e.customer.pk == customer.pk)
        return rows.row_payload(entry)


class RowPayloadTests(RowFixture):
    def test_header(self):
        row = self.row(self.pizza)
        self.assertEqual(row["id"], self.pizza.pk)
        self.assertEqual(row["name"], "Pizza Hut")
        self.assertEqual(row["initials"], "PH")
        self.assertEqual(row["owner"], {"id": self.csm.pk, "name": "Carl CSM"})
        self.assertEqual(row["lifecycle"], {"value": "live", "label": "Live"})
        self.assertEqual(row["health"], {"score": 4.9, "category": "average", "trend": [4.9]})
        self.assertEqual(
            row["renewal"],
            {"date": (self.today - timedelta(days=47)).isoformat(), "days": -47},
        )
        self.assertEqual(row["arr"], 69600.0)
        self.assertEqual(row["pulse"], {
            "csm": 3,
            "ai": 1,
            "ai_category": "high_risk",
            "ai_label": "High Risk",
            "reason": "Quiet since the outage.",
            "history": [1, 2, 2],
            "disagree": True,
        })  # fmt: skip
        self.assertIsNone(row["last_touch_days"])
        self.assertEqual(row["urgent_tickets"], 0)
        self.assertEqual(row["signal"], {"kind": "renewal_overdue", "label": "Renewal overdue"})
        self.assertEqual(set(row["risk"]), {"score", "direction"})
        self.assertFalse(row["is_archived"])
        self.assertFalse(row["churned"])

    def test_details_groups(self):
        details = self.row(self.pizza)["details"]
        self.assertEqual(
            list(details), ["commercial", "contract", "adoption", "voice", "profile", "history"]
        )
        self.assertEqual(details["commercial"], {
            "currency": "USD",
            "arr_billed_at_account": 69600.0,
            "arr_billed_at_hq": 10000.0,
            "total_contract_value": 208800.0,
            "total_forecasted_renewal_revenue": 70000.0,
            "implementation_fee": 5000.0,
        })  # fmt: skip
        self.assertEqual(details["contract"], {
            "joined_date": "2024-01-15",
            "contract_start_date": "2024-02-01",
            "renewal_date": (self.today - timedelta(days=47)).isoformat(),
            "contract_end_date": "2027-01-31",
        })  # fmt: skip
        self.assertEqual(details["adoption"], {
            "total_contracted_seats": 100,
            "total_active_seats": 16,
            "seat_utilization_percentage": 16.0,
            "total_hires": 40,
            "products": {
                "primary": {"id": self.core.pk, "name": "Core"},
                "additional_count": 2,
            },
            "scope_web_app": "Full",
        })  # fmt: skip
        self.assertEqual(details["voice"], {
            "nps_score": -80,
            "csat_score": 72.5,
            "ces_percentage": 61.0,
            "ai_pulse_reason": "Quiet since the outage.",
        })  # fmt: skip
        self.assertEqual(details["profile"], {
            "revenact_id": self.pizza.pk,
            "domain": "pizzahut.example",
            "address": "1 Main St, Dallas",
            "top_source_channel": "Referral",
        })  # fmt: skip
        history = details["history"]
        self.assertEqual(history["created_by"], {"id": self.admin.pk, "name": "Alice Admin"})
        self.assertEqual(history["modified_by"], {"id": self.csm.pk, "name": "Carl CSM"})
        self.assertEqual(history["created_at"], self.pizza.created_at.isoformat())
        self.assertEqual(history["churn_date"], None)
        self.assertEqual(history["churn_reason"], "")

    def test_churn_fields_and_an_empty_row(self):
        left = self.customer(
            "Left Co",
            owner=None,
            lifecycle_stage="churn",
            churn_date=date(2026, 8, 1),
            churn_reason="price",
            churn_comment="Moved to a cheaper tool.",
        )
        row = self.row(left, include_churned="1")
        self.assertIsNone(row["owner"])
        self.assertTrue(row["churned"])
        self.assertEqual(row["details"]["history"]["churn_date"], "2026-08-01")
        self.assertEqual(row["details"]["history"]["churn_reason_label"], "Price")
        self.assertEqual(row["details"]["history"]["churn_comment"], "Moved to a cheaper tool.")
        self.assertIsNone(row["details"]["adoption"]["products"]["primary"])
        self.assertIsNone(row["details"]["adoption"]["seat_utilization_percentage"])
        self.assertFalse(row["pulse"]["disagree"])


class FieldCatalogueTests(RowFixture):
    def test_every_table_column_is_named_once_in_order(self):
        self.assertEqual([field.id for field in FIELDS], FRONTEND_COLUMN_IDS)
        self.assertEqual(len({field.label for field in FIELDS}), 34)

    def test_every_field_reads_from_a_full_and_an_empty_row(self):
        full = self.row(self.pizza)
        empty = self.row(self.customer("Blank", owner=None))
        values = {field.id: field.value(full) for field in FIELDS}
        for field in FIELDS:
            field.value(empty)
        self.assertEqual(values["organization"], "Pizza Hut")
        self.assertEqual(values["revenactId"], self.pizza.pk)
        self.assertEqual(values["owner"], "Carl CSM")
        self.assertEqual(values["lifecycleStage"], "Live")
        self.assertEqual(values["health"], 4.9)
        self.assertEqual(values["pulse"], "1 2 2")
        self.assertEqual(values["aiPulseScore"], "High Risk")
        self.assertEqual(values["productsUtilized"], "Core (+2)")
        self.assertEqual(values["totalSeatUtilization"], 16.0)
        self.assertEqual(
            values["createdBy"], f"Alice Admin / {self.pizza.created_at.date().isoformat()}"
        )
        self.assertEqual(values["nameAddress"], "1 Main St, Dallas")
        self.assertEqual(FIELDS[4].value(empty), 8.0)
        self.assertEqual(next(f for f in FIELDS if f.id == "owner").value(empty), "Unassigned")
