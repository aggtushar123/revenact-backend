"""The Organizations helpers the Accounts portfolio imports, pinned on their
own so a change to one is seen by both lists."""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from services.customers.models import Account, HealthSnapshot
from services.organizations import book, bulk, rows, shape
from services.organizations.params import comma_list, int_or_none
from services.organizations.tests.fixtures import PortfolioFixture


def rank_by_arr(descending):
    def rank(entry):
        value = shape.wrap_desc(entry["arr"], descending)
        return ((), 0, value, (entry["name"], entry["id"]))

    return rank


class KeysetPageTests(SimpleTestCase):
    """`keyset_page` pages any kind's entries — here, plain dicts."""

    ENTRIES = [{"id": i, "name": f"n{i}", "arr": float(i)} for i in range(5)]

    def page(self, entries, cursor, *, descending=False, fingerprint="f", grouped=False):
        return shape.keyset_page(
            entries,
            rank=rank_by_arr(descending),
            cursor=cursor,
            limit=2,
            descending=descending,
            fingerprint=fingerprint,
            grouped=grouped,
        )

    def follow(self, *, descending):
        entries = sorted(self.ENTRIES, key=rank_by_arr(descending))
        seen, cursor = [], ""
        for _ in range(10):
            page, cursor = self.page(entries, cursor, descending=descending)
            seen += [entry["id"] for entry in page]
            if cursor is None:
                return seen
        self.fail(f"the cursor never ended: {seen}")

    def test_every_entry_once_in_either_direction(self):
        self.assertEqual(self.follow(descending=False), [0, 1, 2, 3, 4])
        self.assertEqual(self.follow(descending=True), [4, 3, 2, 1, 0])

    def test_a_cursor_cut_from_another_list_reads_the_first_page(self):
        entries = sorted(self.ENTRIES, key=rank_by_arr(False))
        _page, cursor = self.page(entries, "")
        self.assertIsNotNone(cursor)
        other_list, _next = self.page(entries, cursor, fingerprint="g")
        self.assertEqual([entry["id"] for entry in other_list], [0, 1])
        now_grouped, _next = self.page(entries, cursor, grouped=True)
        self.assertEqual([entry["id"] for entry in now_grouped], [0, 1])
        garbage, _next = self.page(entries, "%%%")
        self.assertEqual([entry["id"] for entry in garbage], [0, 1])


class GroupRankTests(SimpleTestCase):
    def test_fixed_orders_and_names_with_the_empty_bucket_last(self):
        self.assertLess(
            shape.group_rank("poor", "Poor", "health"), shape.group_rank("good", "Good", "health")
        )
        self.assertLess(
            shape.group_rank("onboarding", "Onboarding", "lifecycle"),
            shape.group_rank("live", "Live", "lifecycle"),
        )
        self.assertLess(
            shape.group_rank("7", "Zed", "owner"),
            shape.group_rank("unassigned", "Unassigned", "owner"),
        )


class HealthTrendTests(SimpleTestCase):
    def test_six_points_ending_at_todays_score(self):
        today = date(2026, 9, 29)
        snapshots = [
            (today - timedelta(days=30 * months), Decimal(score))
            for months, score in (
                (8, "9.0"),
                (6, "7.0"),
                (5, "6.2"),
                (4, "5.8"),
                (3, "5.5"),
                (2, "5.1"),
                (1, "5.0"),
            )
        ]
        self.assertEqual(
            book.health_trend(snapshots, Decimal("4.9"), today=today),
            [6.2, 5.8, 5.5, 5.1, 5.0, 4.9],
        )

    def test_no_snapshots_is_todays_score_alone(self):
        self.assertEqual(book.health_trend([], Decimal("4.9"), today=date(2026, 9, 29)), [4.9])


class PulsePayloadTests(SimpleTestCase):
    def record(self, **fields):
        values = {
            "csm_pulse_score": 3,
            "ai_pulse_value": 1,
            "ai_pulse_score": "high_risk",
            "ai_pulse_reason": "Quiet since the outage.",
            "pulse": [1, 2, 2],
            **fields,
        }
        return SimpleNamespace(**values)

    def test_both_pulses_the_label_and_the_disagreement(self):
        self.assertEqual(
            rows.pulse_payload(self.record()),
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

    def test_one_side_unrated_never_disagrees(self):
        payload = rows.pulse_payload(
            self.record(ai_pulse_value=None, ai_pulse_score="", pulse=None)
        )
        self.assertEqual((payload["disagree"], payload["ai_label"]), (False, ""))
        self.assertEqual(payload["history"], [])


class SnapshotHistoryTests(PortfolioFixture):
    def test_reads_one_parent_kind(self):
        customer = self.customer("Pizza Hut")
        account = Account.objects.create(name="Pizza EMEA")
        account.customers.add(customer)
        day = self.today - timedelta(days=30)
        HealthSnapshot.objects.create(
            customer=customer, captured_on=day, health_score=Decimal("6.0")
        )
        HealthSnapshot.objects.create(account=account, captured_on=day, health_score=Decimal("3.0"))
        since = self.today - timedelta(days=365)
        self.assertEqual(
            dict(book.snapshot_history([customer.pk], since=since)),
            {customer.pk: [(day, Decimal("6.0"))]},
        )
        self.assertEqual(
            dict(book.snapshot_history([account.pk], since=since, parent="account")),
            {account.pk: [(day, Decimal("3.0"))]},
        )

    def test_an_unknown_parent_is_refused(self):
        with self.assertRaises(ValueError):
            book.snapshot_history([1], since=self.today, parent="contact")


class SmallHelperTests(SimpleTestCase):
    def test_params_helpers(self):
        self.assertEqual(int_or_none("7"), 7)
        self.assertIsNone(int_or_none("x"))
        self.assertIsNone(int_or_none(None))
        self.assertEqual(comma_list(" a, ,b "), ["a", "b"])
        self.assertEqual(comma_list(None), [])

    def test_first_reason(self):
        self.assertEqual(bulk.first_reason({"owner_id": ["Nope."]}), "Nope.")
        self.assertEqual(bulk.first_reason({}), bulk.NOT_UPDATED)
