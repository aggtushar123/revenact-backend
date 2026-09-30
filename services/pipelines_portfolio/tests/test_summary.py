from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from django.db import connection
from django.http import QueryDict
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from services.accounts.models import User
from services.customers.models import Opportunity, Risk
from services.pipelines_portfolio.book import load_book, quarter_bounds
from services.pipelines_portfolio.kinds import OPPORTUNITIES, RISKS
from services.pipelines_portfolio.params import parse_params
from services.pipelines_portfolio.shape import build_listing, build_summary, select
from services.pipelines_portfolio.tests.fixtures import PipelineFixture

EVERY_STAGE = "stage=" + ",".join(OPPORTUNITIES.stages)


class SummaryTests(PipelineFixture):
    def summary(self, query="", kind=OPPORTUNITIES, user=None, today=None):
        params = parse_params(QueryDict(query), kind)
        book = load_book(user or self.csm, kind, params, today=today or self.today)
        return build_summary(book, today=today or self.today)

    def count(self, query, kind=OPPORTUNITIES, today=None):
        params = parse_params(QueryDict(query), kind)
        book = load_book(self.csm, kind, params, today=today or self.today)
        return len(select(book, params)[0])

    def before_this_quarter(self, item):
        start, _end = quarter_bounds(self.today)
        before = timezone.make_aware(datetime.combine(start - timedelta(days=1), time(12)))
        type(item).objects.filter(pk=item.pk).update(stage_changed_at=before)

    def seed(self):
        self.opportunity("Soon", mrr=Decimal("100"), expected_close=self.days(10))
        self.opportunity(
            "Quarter away", mrr=Decimal("200"), expected_close=self.days(60), stage="negotiation"
        )
        self.opportunity("Late", mrr=Decimal("300"), expected_close=self.days(-4))
        self.opportunity("Undated", mrr=Decimal("400"))
        self.opportunity("Won", mrr=Decimal("500"), stage="closed_won")
        self.opportunity(
            "Lost", mrr=Decimal("600"), stage="closed_lost", expected_close=self.days(-9)
        )
        self.before_this_quarter(
            self.opportunity("Won before", mrr=Decimal("700"), stage="closed_won")
        )

    def seed_risks(self):
        self.risk("Open soon", mrr=Decimal("10"), due_by=self.days(5))
        self.risk("Open later", mrr=Decimal("15"), due_by=self.days(45))
        self.risk("Open late", mrr=Decimal("20"), due_by=self.days(-1))
        self.risk("Mitigated", mrr=Decimal("30"), stage="mitigated", due_by=self.days(-3))
        self.risk("Realised", mrr=Decimal("40"), stage="realised")
        self.before_this_quarter(
            self.risk("Mitigated before", mrr=Decimal("50"), stage="mitigated")
        )

    def assert_each_tile_lists_exactly_its_n(self, kind, other=""):
        summary = self.summary(other, kind=kind)
        prefix = f"{other}&" if other else ""
        self.assertEqual(self.count(other, kind=kind), summary["open"]["count"])
        for days, bucket in summary["within"].items():
            self.assertEqual(self.count(f"{prefix}date={days}", kind=kind), bucket["count"])
        self.assertEqual(
            self.count(f"{prefix}date=overdue", kind=kind), summary["overdue"]["count"]
        )
        done = summary["done_this_quarter"]
        self.assertEqual(
            self.count(f"{prefix}stage={done['stage']}&changed=quarter", kind=kind), done["count"]
        )
        for stage in summary["stages"]:
            self.assertEqual(
                self.count(f"{prefix}stage={stage['value']}", kind=kind), stage["count"]
            )

    def test_the_opportunity_tiles(self):
        self.seed()
        summary = self.summary()
        self.assertEqual((summary["items"], summary["mrr"]), (7, 2800.0))
        self.assertEqual(summary["open"], {"count": 4, "mrr": 1000.0})
        self.assertEqual(
            summary["within"],
            {"30": {"count": 1, "mrr": 100.0}, "90": {"count": 2, "mrr": 300.0}},
        )
        self.assertEqual(summary["overdue"], {"count": 1, "mrr": 300.0})
        self.assertEqual(
            summary["done_this_quarter"], {"stage": "closed_won", "count": 1, "mrr": 500.0}
        )
        stages = {stage["value"]: (stage["count"], stage["mrr"]) for stage in summary["stages"]}
        self.assertEqual(list(stages), list(OPPORTUNITIES.stages))
        self.assertEqual(stages["discovery"], (3, 800.0))
        self.assertEqual(stages["qualification"], (0, 0.0))
        self.assertEqual(stages["closed_won"], (2, 1200.0))
        self.assertEqual(stages["closed_lost"], (1, 600.0))

    def test_the_risk_tiles(self):
        self.seed_risks()
        summary = self.summary(kind=RISKS)
        self.assertEqual((summary["items"], summary["mrr"]), (6, 165.0))
        self.assertEqual(summary["open"], {"count": 3, "mrr": 45.0})
        self.assertEqual(
            summary["within"],
            {"30": {"count": 1, "mrr": 10.0}, "90": {"count": 2, "mrr": 25.0}},
        )
        self.assertEqual(summary["overdue"], {"count": 1, "mrr": 20.0})
        self.assertEqual(
            summary["done_this_quarter"], {"stage": "mitigated", "count": 1, "mrr": 30.0}
        )
        self.assertEqual(
            [(stage["value"], stage["count"]) for stage in summary["stages"]],
            [("open", 3), ("mitigated", 2), ("realised", 1), ("abandoned", 0)],
        )

    def test_each_opportunity_tile_lists_exactly_its_n(self):
        self.seed()
        self.assert_each_tile_lists_exactly_its_n(OPPORTUNITIES)

    def test_each_risk_tile_lists_exactly_its_n(self):
        self.seed_risks()
        self.assert_each_tile_lists_exactly_its_n(RISKS)

    def test_each_tile_lists_exactly_its_n_under_another_filter(self):
        self.seed()
        self.opportunity(
            "High soon", priority="high", mrr=Decimal("5"), expected_close=self.days(3)
        )
        self.opportunity(
            "High late", priority="high", mrr=Decimal("6"), expected_close=self.days(-2)
        )
        self.opportunity("High won", priority="high", mrr=Decimal("7"), stage="closed_won")
        self.assertEqual(self.summary("priority=high")["items"], 3)
        self.assert_each_tile_lists_exactly_its_n(OPPORTUNITIES, other="priority=high")

    def test_the_summary_equals_the_rows_it_covers(self):
        self.seed()
        params = parse_params(QueryDict(EVERY_STAGE), OPPORTUNITIES)
        book = load_book(self.csm, OPPORTUNITIES, params, today=self.today)
        entries, _groups = select(book, params)
        summary = build_summary(book, today=self.today)
        self.assertEqual(summary["items"], len(entries))
        self.assertEqual(summary["mrr"], round(sum(entry.mrr for entry in entries), 2))
        self.assertEqual(sum(stage["count"] for stage in summary["stages"]), len(entries))
        self.assertEqual(
            sum(stage["mrr"] for stage in summary["stages"]), round(sum(e.mrr for e in entries), 2)
        )

    def test_a_stage_filter_changes_the_rows_but_not_the_tiles(self):
        self.seed()
        self.assertEqual(self.count("stage=closed_lost"), 1)
        self.assertEqual(self.count(""), 4)
        self.assertEqual(self.summary("stage=closed_lost"), self.summary())

    def test_every_other_filter_changes_the_rows_and_the_tiles(self):
        self.seed()
        self.opportunity("High one", priority="high", mrr=Decimal("50"))
        self.opportunity("Taco's", customer=self.taco, mrr=Decimal("9"))
        self.opportunity("Sales'", department=User.Function.SALES, mrr=Decimal("8"))
        account = self.account("Carl's account")
        self.opportunity("On the account", account=account, mrr=Decimal("70"))
        cases = {
            "priority=high": (1, 50.0),
            "search=soon": (1, 100.0),
            f"organisation={self.pizza.pk}": (9, 2920.0),
            f"account={account.pk}": (1, 70.0),
            f"owner={self.csm.pk}": (9, 2920.0),
            "owner=unassigned": (0, 0.0),
            "department=sales": (0, 0.0),
            "date=overdue": (1, 300.0),
            "date=none": (5, 1720.0),
            "changed=quarter": (8, 2220.0),
            f"ids={Opportunity.objects.get(title='Won').pk}": (1, 500.0),
        }
        everything = self.summary()
        self.assertEqual((everything["items"], everything["mrr"]), (9, 2920.0))
        for query, (items, mrr) in cases.items():
            with self.subTest(query=query):
                summary = self.summary(query)
                self.assertEqual((summary["items"], summary["mrr"]), (items, mrr))
                self.assertEqual(self.count(f"{query}&{EVERY_STAGE}"), items)

    def test_this_quarter_is_the_utc_calendar_quarter_in_any_time_zone(self):
        today = date(2026, 11, 15)
        moments = {
            "Last second of Q3": datetime(2026, 9, 30, 23, 59, 59, tzinfo=UTC),
            "First second of Q4": datetime(2026, 10, 1, 0, 0, 0, tzinfo=UTC),
            "Last second of Q4": datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC),
            "First second of Q1": datetime(2027, 1, 1, 0, 0, 0, tzinfo=UTC),
        }
        for title, moment in moments.items():
            item = self.opportunity(title, stage="closed_won", mrr=Decimal("1"))
            Opportunity.objects.filter(pk=item.pk).update(stage_changed_at=moment)
        query = "stage=closed_won&changed=quarter"
        for zone in ("UTC", "Asia/Kolkata", "America/Los_Angeles"):
            with self.subTest(zone=zone), timezone.override(zone):
                self.assertEqual(self.summary(today=today)["done_this_quarter"]["count"], 2)
                params = parse_params(QueryDict(query), OPPORTUNITIES)
                book = load_book(self.csm, OPPORTUNITIES, params, today=today)
                self.assertEqual(
                    sorted(entry.item.title for entry in select(book, params)[0]),
                    ["First second of Q4", "Last second of Q4"],
                )

    def test_the_risk_quarter_tile_counts_only_mitigated(self):
        self.risk("Realised now", stage="realised")
        self.risk("Abandoned now", stage="abandoned")
        self.risk("Open now")
        self.assertEqual(self.summary(kind=RISKS)["done_this_quarter"]["count"], 0)
        Risk.objects.filter(title="Open now").update(stage="mitigated")
        self.assertEqual(self.summary(kind=RISKS)["done_this_quarter"]["count"], 1)

    def test_the_summary_is_the_viewers_own(self):
        self.opportunity("Mine", mrr=Decimal("1"))
        self.opportunity("Taco's", customer=self.taco, mrr=Decimal("2"))
        self.opportunity("Sales'", department=User.Function.SALES, mrr=Decimal("4"))
        self.assertEqual(self.summary()["mrr"], 1.0)
        self.assertEqual(self.summary(user=self.admin)["mrr"], 7.0)


class ListingTests(PipelineFixture):
    def listing(self, query, kind=OPPORTUNITIES):
        params = parse_params(QueryDict(query), kind)
        book = load_book(self.csm, kind, params, today=self.today)
        return build_listing(book, params, filters={"x": []}, today=self.today)

    def test_the_body(self):
        self.opportunity("Upsell")
        body = self.listing("group=stage")
        self.assertEqual(
            set(body),
            {"kind", "results", "next_cursor", "count", "groups", "summary", "filters", "currency"},
        )
        self.assertEqual(
            (body["kind"], body["count"], body["currency"], body["filters"]),
            ("opportunities", 1, "USD", {"x": []}),
        )
        self.assertEqual(body["results"][0]["title"], "Upsell")
        self.assertEqual(body["groups"][0]["key"], "discovery")
        self.assertIsNone(body["next_cursor"])

    def test_the_risk_body_names_its_kind(self):
        self.risk("Churn risk")
        body = self.listing("", kind=RISKS)
        self.assertEqual((body["kind"], body["results"][0]["kind"]), ("risks", "risk"))

    def test_the_summary_covers_every_row_not_the_page(self):
        for n in range(3):
            self.opportunity(f"Deal {n}", mrr=Decimal("10"))
        self.opportunity("Won", stage="closed_won", mrr=Decimal("5"))
        body = self.listing("limit=1")
        self.assertEqual((len(body["results"]), body["count"]), (1, 3))
        self.assertIsNotNone(body["next_cursor"])
        self.assertEqual((body["summary"]["items"], body["summary"]["mrr"]), (4, 35.0))
        self.assertEqual(body["summary"]["open"], {"count": 3, "mrr": 30.0})

    def test_the_body_reads_without_a_query_per_row(self):
        self.opportunity("One")
        params = parse_params(QueryDict(""), OPPORTUNITIES)
        book = load_book(self.csm, OPPORTUNITIES, params, today=self.today)
        with CaptureQueriesContext(connection) as few:
            build_listing(book, params, filters={}, today=self.today)
        for n in range(5):
            self.opportunity(f"More {n}", account=self.account(f"Account {n}"))
        book = load_book(self.csm, OPPORTUNITIES, params, today=self.today)
        with CaptureQueriesContext(connection) as many:
            build_listing(book, params, filters={}, today=self.today)
        self.assertEqual(len(few), 0)
        self.assertEqual(len(many), 0)


class EmptyBucketTests(PipelineFixture):
    def test_an_empty_book_totals_every_bucket_as_a_float(self):
        for kind in (OPPORTUNITIES, RISKS):
            book = load_book(self.csm, kind, parse_params(QueryDict(""), kind), today=self.today)
            summary = build_summary(book, today=self.today)
            buckets = [
                summary["open"],
                summary["overdue"],
                summary["done_this_quarter"],
                *summary["within"].values(),
                *summary["stages"],
            ]
            for bucket in buckets:
                self.assertIsInstance(bucket["mrr"], float, bucket)
                self.assertEqual(bucket["mrr"], 0.0)
            self.assertIsInstance(summary["mrr"], float)

    def test_an_empty_stage_beside_a_full_one_totals_a_float(self):
        self.opportunity("Only", mrr=Decimal("250"))
        book = load_book(
            self.csm, OPPORTUNITIES, parse_params(QueryDict(""), OPPORTUNITIES), today=self.today
        )
        stages = {s["value"]: s for s in build_summary(book, today=self.today)["stages"]}
        self.assertIsInstance(stages["closed_won"]["mrr"], float)
        self.assertEqual(stages["discovery"]["mrr"], 250.0)
