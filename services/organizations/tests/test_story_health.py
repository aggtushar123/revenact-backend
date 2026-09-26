from datetime import UTC, datetime, time
from decimal import Decimal

from django.test import SimpleTestCase

from services.customers.models import HealthSnapshot
from services.organizations.story.health import describe_change, health_entries

from .story_fixtures import StoryFixture


def reading(score, ai=None, csm=None):
    return HealthSnapshot(health_score=Decimal(score), ai_pulse_value=ai, csm_pulse_score=csm)


class DescribeChangeTests(SimpleTestCase):
    def test_a_category_change_names_its_direction(self):
        self.assertEqual(
            describe_change(reading("7.5"), reading("5.0")),
            ("Health fell to Average", "Health 7.5 → 5.0"),
        )
        self.assertEqual(
            describe_change(reading("3.0"), reading("7.0")),
            ("Health rose to Good", "Health 3.0 → 7.0"),
        )

    def test_a_pulse_change_alone_is_a_change(self):
        self.assertEqual(
            describe_change(reading("8.0", ai=4, csm=4), reading("8.2", ai=2, csm=4)),
            ("Pulse changed", "Health 8.0 → 8.2 · AI pulse 4 → 2"),
        )
        self.assertEqual(
            describe_change(reading("8.0"), reading("8.0", csm=3)),
            ("Pulse changed", "Health 8.0 → 8.0 · CSM pulse — → 3"),
        )

    def test_movement_inside_one_category_is_not_a_change(self):
        self.assertIsNone(describe_change(reading("8.0", ai=3), reading("7.1", ai=3)))


class HealthEntriesTests(StoryFixture):
    def test_each_change_between_consecutive_readings_is_one_item(self):
        self.snapshot(self.pizza, self.days_ago(90), "8.0")
        self.snapshot(self.pizza, self.days_ago(60), "7.6")
        fell = self.snapshot(self.pizza, self.days_ago(30), "3.5")
        self.snapshot(self.emea, self.days_ago(60), "5.0", ai=3)
        moved = self.snapshot(self.emea, self.days_ago(30), "5.0", ai=1)

        by_id = {item["id"]: (key, item) for key, item in health_entries(self.scope())}
        self.assertEqual(set(by_id), {fell.pk, moved.pk})

        key, item = by_id[fell.pk]
        midnight = datetime.combine(self.days_ago(30), time.min, tzinfo=UTC)
        self.assertEqual(key, (midnight, "health", fell.pk))
        self.assertEqual(item["kind"], "health")
        self.assertEqual(item["title"], "Health fell to Poor")
        self.assertEqual(item["summary"], "Health 7.6 → 3.5")
        self.assertEqual(item["occurred_at"], f"{self.days_ago(30).isoformat()}T00:00:00+00:00")
        self.assertTrue(item["all_day"])
        self.assertIsNone(item["account"])
        self.assertIsNone(item["actor"])

        _key, item = by_id[moved.pk]
        self.assertEqual(item["account"], {"id": self.emea.pk, "name": "EMEA"})
        self.assertEqual(item["summary"], "Health 5.0 → 5.0 · AI pulse 3 → 1")

    def test_only_this_organisation_and_the_accounts_the_viewer_may_open(self):
        taco = self.customer("Taco Co")
        open_co = self.customer("Open Co", owner=None)
        danas = self.account("Dana's div", customers=[open_co], owner=self.other)
        for parent in (taco, self.account("Taco div", customers=[taco]), danas):
            self.snapshot(parent, self.days_ago(60), "8.0")
            self.snapshot(parent, self.days_ago(30), "2.0")
        self.assertEqual(health_entries(self.scope()), [])
        self.assertEqual(health_entries(self.scope(customer=open_co)), [])
