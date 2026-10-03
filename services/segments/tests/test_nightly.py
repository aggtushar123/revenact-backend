"""The nightly step: entries and exits as the owner sees their book, with
the field keys that moved them; one run per date; one alert per segment per
day; an inactive owner pauses the segment. Then the history a viewer reads:
their own records named, the rest counted. Dates are passed in, never
waited for."""

from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from services.accounts.models import User
from services.customers.models import Customer
from services.notifications.models import Notification
from services.segments import nightly, views
from services.segments.baseline import rebaseline
from services.segments.models import Segment, SegmentChange
from services.segments.nightly import alert_message, evaluate_nightly
from services.segments.tests.fixtures import SegmentFixture, rule

POOR = rule("health_category", "is", "poor")
URL = "/api/v1/segments/"


class NightlyFixture(SegmentFixture):
    """Alice watches "Poor health" with alerts on. Its baseline was taken
    yesterday: Taco Bell (3.0) is in, Pizza Hut (8.0) is not."""

    def setUp(self):
        self.yesterday = self.today - timedelta(days=1)
        self.watch = self.segment(
            owner=self.admin, rules=POOR, alert_on_changes=True, name="Poor health"
        )
        rebaseline(self.watch, today=self.yesterday)

    def set_health(self, customer, score):
        Customer.objects.filter(pk=customer.pk).update(health_score=Decimal(score))

    def changes(self, segment=None):
        rows = SegmentChange.objects.filter(segment=segment or self.watch)
        return sorted(rows.values_list("record_id", "change", "changed_on", "reason"))


class NightlyTests(NightlyFixture):
    def test_entries_and_exits_are_recorded_with_the_fields_that_moved_them(self):
        self.set_health(self.pizza, "2.0")
        self.set_health(self.taco, "8.0")
        result = evaluate_nightly(today=self.today)
        self.assertEqual((result.evaluated, result.changes, result.alerts), (1, 2, 1))
        self.assertEqual(
            self.changes(),
            [
                (self.pizza.pk, "entered", self.today, ["health_category"]),
                (self.taco.pk, "left", self.today, ["health_category"]),
            ],
        )
        self.watch.refresh_from_db()
        self.assertEqual(
            (self.watch.last_members, self.watch.member_count, self.watch.last_evaluated_on),
            ([self.pizza.pk], 1, self.today),
        )

    def test_a_first_run_only_sets_the_baseline(self):
        fresh = self.segment(owner=self.admin, rules=POOR, name="Fresh")
        evaluate_nightly(today=self.today)
        fresh.refresh_from_db()
        self.assertEqual((fresh.last_members, fresh.member_count), ([self.taco.pk], 1))
        self.assertEqual(self.changes(fresh), [])

    def test_one_run_per_date(self):
        self.set_health(self.pizza, "2.0")
        evaluate_nightly(today=self.today)
        again = evaluate_nightly(today=self.today)
        self.assertEqual((again.evaluated, again.changes, again.alerts), (0, 0, 0))
        self.assertEqual(len(self.changes()), 1)

    def test_one_alert_per_segment_per_day_with_counts_only(self):
        self.set_health(self.pizza, "2.0")
        self.set_health(self.taco, "8.0")
        evaluate_nightly(today=self.today)
        evaluate_nightly(today=self.today)
        alert = Notification.objects.get()
        self.assertEqual(
            (alert.recipient, alert.kind, alert.message, alert.link),
            (
                self.admin,
                "segment_changes",
                "Poor health: 1 entered, 1 left",
                f"/segments/{self.watch.pk}?tab=changes",
            ),
        )
        # Nothing moves the next day, so nothing is sent.
        evaluate_nightly(today=self.today + timedelta(days=1))
        self.assertEqual(Notification.objects.count(), 1)

    def test_no_alert_when_the_owner_did_not_ask_for_one(self):
        Segment.objects.filter(pk=self.watch.pk).update(alert_on_changes=False)
        self.set_health(self.pizza, "2.0")
        evaluate_nightly(today=self.today)
        self.assertEqual((len(self.changes()), Notification.objects.count()), (1, 0))

    def test_the_alert_quotes_at_most_two_hundred_characters_of_the_name(self):
        self.watch.name = "x" * 120
        self.assertEqual(alert_message(self.watch, 3, 1), f"{'x' * 120}: 3 entered, 1 left")
        self.watch.name = "y" * 300
        self.assertEqual(alert_message(self.watch, 3, 1), f"{'y' * 200}: 3 entered, 1 left")

    def test_an_inactive_owner_pauses_and_a_returning_owner_resumes_from_that_day(self):
        User.objects.filter(pk=self.admin.pk).update(is_active=False)
        self.set_health(self.pizza, "2.0")
        result = evaluate_nightly(today=self.today)
        self.watch.refresh_from_db()
        self.assertEqual((result.paused, result.evaluated), (1, 0))
        self.assertEqual((self.watch.paused, self.changes()), (True, []))
        self.assertFalse(Notification.objects.exists())

        User.objects.filter(pk=self.admin.pk).update(is_active=True)
        tomorrow = self.today + timedelta(days=1)
        evaluate_nightly(today=tomorrow)
        self.watch.refresh_from_db()
        self.assertEqual(
            (self.watch.paused, self.watch.last_members, self.watch.last_evaluated_on),
            (False, sorted([self.pizza.pk, self.taco.pk]), tomorrow),
        )
        self.assertEqual(self.changes(), [])

    def test_evaluated_as_the_owner(self):
        carls = self.segment(owner=self.csm, rules=POOR, name="Carl's")
        rebaseline(carls, today=self.yesterday)
        # Both are poor now; Carl can open Pizza Hut and not Taco Bell.
        self.set_health(self.pizza, "2.0")
        evaluate_nightly(today=self.today)
        carls.refresh_from_db()
        self.assertEqual(
            (carls.last_members, self.changes(carls)),
            ([self.pizza.pk], [(self.pizza.pk, "entered", self.today, ["health_category"])]),
        )

    def test_reasons_for_a_deleted_record_lost_access_and_the_churn_default(self):
        segment = self.segment(owner=self.csm, rules=rule("health_score", "gt", 0), name="Book")
        brief, moved, gone = (
            Customer.objects.create(organisation=self.org, name=name, owner=self.csm)
            for name in ("Brief", "Moved", "Gone")
        )
        rebaseline(segment, today=self.yesterday)
        brief_id = brief.pk
        brief.delete()
        Customer.objects.filter(pk=moved.pk).update(owner=self.other)
        Customer.objects.filter(pk=gone.pk).update(churn_date=self.today)
        evaluate_nightly(today=self.today)
        reasons = {
            record: (change, reason) for record, change, _day, reason in self.changes(segment)
        }
        self.assertEqual(
            reasons,
            {
                brief_id: ("left", ["deleted"]),
                moved.pk: ("left", ["access"]),
                gone.pk: ("left", ["churned"]),
            },
        )

    def test_a_pin_the_owner_can_open_again_enters_as_pinned(self):
        segment = self.segment(
            owner=self.csm, rules=rule("health_score", "gt", 100), pinned_ids=[self.taco.pk]
        )
        rebaseline(segment, today=self.yesterday)
        self.assertEqual(segment.last_members, [])
        Customer.objects.filter(pk=self.taco.pk).update(owner=self.csm)
        evaluate_nightly(today=self.today)
        self.assertEqual(self.changes(segment), [(self.taco.pk, "entered", self.today, ["pinned"])])

    def test_a_segment_too_large_to_track_is_counted_not_tracked(self):
        with patch.object(nightly, "MAX_TRACKED_MEMBERS", 0):
            evaluate_nightly(today=self.today)
        self.watch.refresh_from_db()
        self.assertEqual((self.watch.last_members, self.watch.member_count), (None, 1))
        evaluate_nightly(today=self.today + timedelta(days=1))
        self.watch.refresh_from_db()
        self.assertEqual((self.watch.last_members, self.changes()), ([self.taco.pk], []))

    def test_a_failing_segment_is_logged_skipped_and_retried_on_the_next_run(self):
        other = self.segment(owner=self.csm, rules=POOR, name="Carl's", alert_on_changes=True)
        self.set_health(self.pizza, "2.0")
        real = nightly.member_ids

        def flaky(segment, user, *, today):
            if segment.pk == self.watch.pk:
                raise RuntimeError("boom")
            return real(segment, user, today=today)

        with (
            patch.object(nightly, "member_ids", side_effect=flaky),
            self.assertLogs("services.segments.nightly", "ERROR"),
        ):
            result = evaluate_nightly(today=self.today)
        self.assertEqual((result.failed, result.evaluated), (1, 1))
        self.watch.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(
            (self.watch.last_evaluated_on, other.last_evaluated_on), (self.yesterday, self.today)
        )
        # The same night, run again: the failed one is evaluated; the other,
        # already done today, is skipped and writes nothing more.
        again = evaluate_nightly(today=self.today)
        self.watch.refresh_from_db()
        self.assertEqual((again.evaluated, again.changes, again.alerts), (1, 1, 1))
        self.assertEqual(self.watch.last_evaluated_on, self.today)
        self.assertEqual(self.changes(other), [])
        self.assertEqual(
            list(Notification.objects.values_list("message", flat=True)),
            ["Poor health: 1 entered, 0 left"],
        )

    def test_a_segment_deleted_after_the_due_list_was_read_is_skipped_not_failed(self):
        gone = self.segment(owner=self.csm, rules=POOR, name="Gone")
        real = nightly.evaluate_segment

        def deleted_first(segment_id, *, today):
            if segment_id == gone.pk:
                Segment.objects.filter(pk=gone.pk).delete()
            return real(segment_id, today=today)

        with (
            patch.object(nightly, "evaluate_segment", side_effect=deleted_first),
            self.assertNoLogs("services.segments.nightly", "ERROR"),
        ):
            result = evaluate_nightly(today=self.today)
        self.assertEqual((result.failed, result.evaluated), (0, 1))

    def test_an_edit_during_the_night_keeps_what_the_step_wrote(self):
        stale = Segment.objects.select_related("owner").get(pk=self.watch.pk)
        self.set_health(self.pizza, "2.0")
        evaluate_nightly(today=self.today)
        client = APIClient()
        client.force_authenticate(self.admin)
        # The edit read the segment before the step ran.
        with patch.object(views, "get_owned", return_value=stale):
            response = client.patch(f"{URL}{self.watch.pk}/", {"name": "Renamed"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.watch.refresh_from_db()
        self.assertEqual(
            (self.watch.name, self.watch.last_members, self.watch.last_evaluated_on),
            ("Renamed", sorted([self.pizza.pk, self.taco.pk]), self.today),
        )
        evaluate_nightly(today=self.today)
        self.assertEqual(Notification.objects.count(), 1)

    def test_one_workspace_at_a_time(self):
        evaluate_nightly(self.other_org, today=self.today)
        self.watch.refresh_from_db()
        self.assertEqual(self.watch.last_evaluated_on, self.yesterday)

    def test_the_maintenance_command_runs_the_step(self):
        out = StringIO()
        call_command("run_health_maintenance", org_email="alice@acme.io", stdout=out)
        self.watch.refresh_from_db()
        self.assertEqual(self.watch.last_evaluated_on, self.today)
        self.assertIn("segment(s)", out.getvalue())

    def test_a_dry_run_does_not_call_the_step(self):
        with patch.object(nightly, "evaluate_nightly") as step:
            call_command(
                "run_health_maintenance", "--dry-run", org_email="alice@acme.io", stdout=StringIO()
            )
        step.assert_not_called()


class ChangesEndpointTests(NightlyFixture):
    def get(self, user, segment, **query):
        client = APIClient()
        client.force_authenticate(user)
        return client.get(f"{URL}{segment.pk}/changes/", query)

    def test_a_shared_viewer_reads_their_own_records_and_a_count(self):
        Segment.objects.filter(pk=self.watch.pk).update(sharing="workspace")
        self.set_health(self.pizza, "2.0")
        self.set_health(self.taco, "8.0")
        evaluate_nightly(today=self.today)
        self.assertEqual(
            self.get(self.csm, self.watch).data,
            {
                "kind": "customer",
                "days": [
                    {
                        "date": self.today.isoformat(),
                        "entered": [
                            {
                                "id": self.pizza.pk,
                                "name": "Pizza Hut",
                                "reason": ["health_category"],
                            }
                        ],
                        "left": [],
                        "totals": {"entered": 1, "left": 0},
                        "more": {"entered": 0, "left": 0},
                    }
                ],
                "hidden_count": 1,
            },
        )
        admin = self.get(self.admin, self.watch).data
        self.assertEqual([row["name"] for row in admin["days"][0]["left"]], ["Taco Bell"])
        self.assertEqual(admin["hidden_count"], 0)

    def test_the_window_is_thirty_days_by_default_and_at_most_ninety(self):
        SegmentChange.objects.create(
            segment=self.watch, record_id=self.pizza.pk, change="entered",
            changed_on=self.today - timedelta(days=40),
        )  # fmt: skip
        self.assertEqual(self.get(self.admin, self.watch).data["days"], [])
        self.assertEqual(len(self.get(self.admin, self.watch, days="60").data["days"]), 1)
        self.assertEqual(len(self.get(self.admin, self.watch, days="9999").data["days"]), 1)
        self.assertEqual(self.get(self.admin, self.watch, days="soon").data["days"], [])

    def test_a_hidden_segment_reads_like_a_missing_one(self):
        hidden = self.get(self.csm, self.watch)
        missing = APIClient()
        missing.force_authenticate(self.csm)
        missing = missing.get(f"{URL}999999/changes/")
        self.assertEqual((hidden.status_code, hidden.data), (404, missing.data))

    def test_the_query_count_is_pinned(self):
        """Six queries for an admin loaded fresh (ruling S1): the segment, the
        changes in the window, the caller's organisation (a fresh `User`
        loads `user.organisation` for visibility), their membership and
        role (capabilities), and the names of the records among the changes
        the caller may open."""
        SegmentChange.objects.create(
            segment=self.watch, record_id=self.pizza.pk, change="entered", changed_on=self.today
        )
        client = APIClient()
        client.force_authenticate(User.objects.get(pk=self.admin.pk))
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(f"{URL}{self.watch.pk}/changes/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(ctx.captured_queries), 6)

    def capped_day(self):
        """Carl owns 105 more organisations, and all of them entered today,
        with Taco Bell (Dana's); Pizza Hut left. Above the 100-name cap."""
        many = Customer.objects.bulk_create(
            Customer(organisation=self.org, name=f"Org {i:03}", owner=self.csm) for i in range(105)
        )
        SegmentChange.objects.bulk_create(
            [
                SegmentChange(
                    segment=self.watch, record_id=record, change="entered", changed_on=self.today
                )
                for record in [c.pk for c in many] + [self.taco.pk]
            ]
            + [
                SegmentChange(
                    segment=self.watch, record_id=self.pizza.pk, change="left",
                    changed_on=self.today,
                )
            ]
        )  # fmt: skip
        Segment.objects.filter(pk=self.watch.pk).update(sharing="workspace")
        return sorted(c.pk for c in many)

    def test_a_day_names_at_most_a_hundred_each_way_and_counts_the_rest(self):
        many = self.capped_day()
        body = self.get(self.csm, self.watch).data
        [day] = body["days"]
        self.assertEqual([row["id"] for row in day["entered"]], many[:100])
        self.assertEqual(day["entered"][0]["name"], "Org 000")
        self.assertEqual([row["name"] for row in day["left"]], ["Pizza Hut"])
        self.assertEqual(
            (day["totals"], day["more"], body["hidden_count"]),
            ({"entered": 105, "left": 1}, {"entered": 5, "left": 0}, 1),
        )

    def test_the_query_count_stays_flat_above_the_cap(self):
        self.capped_day()
        client = APIClient()
        client.force_authenticate(User.objects.get(pk=self.admin.pk))
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(f"{URL}{self.watch.pk}/changes/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["days"][0]["entered"]), 100)
        self.assertEqual(len(ctx.captured_queries), 6)
        # No id list: the names are looked up by subquery, never `pk IN (…105 ids…)`.
        self.assertLess(max(q["sql"].count(",") for q in ctx.captured_queries), 100)
