"""Activity Tracking: coverage, cadence and follow-through.

The tests are about the definitions, because that is where this screen can
mislead: what counts as a touch, whose book a touch belongs to, and what "last
contact" means when the health rubric already uses that phrase for something
narrower.
"""

from datetime import timedelta

from django.test import SimpleTestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers import activity_tracking as tracking
from services.customers import churn
from services.customers.models import (
    Account,
    Activity,
    CalendarEvent,
    Call,
    Customer,
    Email,
    Note,
    Task,
    Ticket,
)


class WindowTests(SimpleTestCase):
    def test_the_window_is_clamped_rather_than_rejected(self):
        self.assertEqual(tracking.window_days({}), tracking.DEFAULT_WINDOW_DAYS)
        self.assertEqual(tracking.window_days({"days": "1"}), 7)
        self.assertEqual(tracking.window_days({"days": "99999"}), 730)
        self.assertEqual(tracking.window_days({"days": "abc"}), tracking.DEFAULT_WINDOW_DAYS)

    def test_going_dark_uses_the_same_threshold_as_the_churn_rule(self):
        # Two screens disagreeing about what "stale" means is the bug this
        # import exists to prevent.
        self.assertEqual(tracking.GOING_DARK_DAYS, churn.CONTACT_COLD_DAYS)


class ActivityTrackingViewTests(APITestCase):
    url = "/api/v1/customers/activity/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="supersecret1",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.other = User.objects.create_user(
            email="dana@acme.io",
            password="supersecret1",
            name="Dana",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.today = timezone.localdate()
        self.customer = Customer.objects.create(
            organisation=self.org, name="Acme", owner=self.csm, arr_billed_at_account=100_000
        )
        self.client.force_authenticate(self.csm)

    def _activity(self, days_ago, customer=None):
        return Activity.objects.create(
            customer=customer or self.customer,
            type=Activity.ActivityType.HEALTH_CHECK_REVIEW,
            occurred_at=self.today - timedelta(days=days_ago),
        )

    def _ticket(self, days_ago, n=1):
        return Ticket.objects.create(
            customer=self.customer,
            ticket_number=f"TKT-{n}",
            title="Broken",
            assignee_name="Support",
            priority=Ticket.Priority.LOW,
            opened_at=self.today - timedelta(days=days_ago),
        )

    def test_unauthenticated_is_refused(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_another_owners_book_is_invisible(self):
        Customer.objects.create(organisation=self.org, name="Theirs", owner=self.other)

        self.assertEqual(self.client.get(self.url).data["kpis"]["accounts"], 1)

    def test_a_churned_customer_is_not_in_the_coverage_denominator(self):
        # Nobody should be marked down for not calling an account that left.
        Customer.objects.create(
            organisation=self.org,
            name="Left",
            owner=self.csm,
            churn_date=self.today - timedelta(days=10),
        )
        self._activity(3)

        kpis = self.client.get(self.url).data["kpis"]

        self.assertEqual(kpis["accounts"], 1)
        self.assertEqual(kpis["coverage"], 100.0)

    # ── what counts as a touch ───────────────────────────────────────

    def test_every_kind_of_logged_work_counts_as_a_touch(self):
        self._activity(3)
        Call.objects.create(
            customer=self.customer,
            title="Check-in",
            host_name="Carl",
            occurred_at=timezone.now() - timedelta(days=4),
        )
        Email.objects.create(
            customer=self.customer,
            subject="Following up",
            sender_name="Carl",
            recipient_name="Them",
            body="Hi",
            sent_at=timezone.now() - timedelta(days=5),
        )
        Note.objects.create(
            customer=self.customer,
            title="Call notes",
            author_name="Carl",
            body="Went well",
            logged_at=self.today - timedelta(days=6),
        )
        CalendarEvent.objects.create(
            customer=self.customer,
            title="QBR",
            description="Quarterly",
            type=CalendarEvent.EventType.MEETING,
            event_date=self.today - timedelta(days=7),
            start_time="10:00",
            end_time="11:00",
        )

        data = self.client.get(self.url).data

        self.assertEqual(data["kpis"]["touches"], 5)
        self.assertEqual({row["name"]: row["count"] for row in data["sources"]}["Calls"], 1)

    def test_a_ticket_is_inbound_not_a_touch(self):
        """A customer raising a ticket is not evidence anyone called them back.
        Counting it would let a screen full of complaints read as coverage."""
        self._ticket(5)

        data = self.client.get(self.url).data

        self.assertEqual(data["kpis"]["touches"], 0)
        self.assertEqual(data["kpis"]["inbound"], 1)

    def test_work_outside_the_window_is_not_counted(self):
        self._activity(200)

        self.assertEqual(self.client.get(self.url).data["kpis"]["touches"], 0)
        self.assertEqual(self.client.get(self.url, {"days": 365}).data["kpis"]["touches"], 1)

    def test_a_touch_on_an_account_counts_for_its_company(self):
        # Otherwise a worked account reads as neglected because the work was
        # logged one level down.
        account = Account.objects.create(name="Acme EMEA", owner=self.csm)
        account.customers.add(self.customer)
        Activity.objects.create(
            account=account,
            type=Activity.ActivityType.HEALTH_CHECK_REVIEW,
            occurred_at=self.today - timedelta(days=2),
        )

        data = self.client.get(self.url).data

        self.assertEqual(data["kpis"]["touches"], 1)
        self.assertEqual(data["kpis"]["touched_accounts"], 1)

    # ── coverage and cadence ─────────────────────────────────────────

    def test_coverage_is_accounts_touched_over_accounts_held(self):
        self._activity(3)
        Customer.objects.create(organisation=self.org, name="Ignored", owner=self.csm)

        kpis = self.client.get(self.url).data["kpis"]

        self.assertEqual(kpis["touched_accounts"], 1)
        self.assertEqual(kpis["accounts"], 2)
        self.assertEqual(kpis["coverage"], 50.0)

    def test_coverage_is_null_rather_than_zero_on_an_empty_book(self):
        Customer.objects.all().delete()

        self.assertIsNone(self.client.get(self.url).data["kpis"]["coverage"])

    def test_cadence_buckets_are_all_present_and_floor_inclusive(self):
        self._activity(3)

        cadence = {row["key"]: row for row in self.client.get(self.url).data["cadence"]}

        self.assertEqual(cadence["week"]["accounts"], 1)
        # Every bucket comes back, including the empties — the shape of the
        # book is the point, and a vanishing bar hides it.
        for key, _label, _floor, _ceiling in tracking.CADENCE_BUCKETS:
            self.assertIn(key, cadence)
        self.assertIn("never", cadence)

    def test_an_account_with_no_contact_at_all_is_its_own_bucket(self):
        # Not "90+ days", which would imply a date nobody has.
        cadence = {row["key"]: row for row in self.client.get(self.url).data["cadence"]}

        self.assertEqual(cadence["never"]["accounts"], 1)
        self.assertEqual(cadence["cold"]["accounts"], 0)

    # ── going dark ───────────────────────────────────────────────────

    def test_accounts_past_the_threshold_are_listed_with_their_arr(self):
        self._activity(tracking.GOING_DARK_DAYS + 10)

        data = self.client.get(self.url, {"days": 365}).data

        self.assertEqual(data["kpis"]["dark_accounts"], 1)
        self.assertEqual(data["kpis"]["dark_arr"], 100_000.0)
        self.assertEqual(data["going_dark"][0]["name"], "Acme")

    def test_never_contacted_sorts_above_merely_stale(self):
        """An account nobody has ever logged a contact for is the worst case,
        not a missing value to sort to the end."""
        self._activity(90)
        Customer.objects.create(organisation=self.org, name="Untouched", owner=self.csm)

        rows = self.client.get(self.url, {"days": 365}).data["going_dark"]

        self.assertEqual([row["name"] for row in rows], ["Untouched", "Acme"])
        self.assertIsNone(rows[0]["days_since_contact"])

    def test_a_recently_worked_account_is_not_going_dark(self):
        self._activity(3)

        data = self.client.get(self.url).data

        self.assertEqual(data["kpis"]["dark_accounts"], 0)
        self.assertEqual(data["going_dark"], [])

    def test_last_contact_is_the_same_number_the_health_rubric_uses(self):
        """This screen and the Customer Touch component read one rule
        (contact.py). The rubric used to count logged Activities only, so a
        call left it measuring from the customer's own arrival while this row
        said 70 days — and the row carried a second figure to admit it."""
        Call.objects.create(
            customer=self.customer,
            title="Long call",
            host_name="Carl",
            occurred_at=timezone.now() - timedelta(days=70),
        )

        row = self.client.get(self.url, {"days": 365}).data["going_dark"][0]

        self.assertEqual(row["days_since_contact"], 70)
        self.assertEqual(self.customer.health_inputs()["days_since_touch"], 70)
        self.assertNotIn("days_since_activity", row)

    # ── owners and tasks ─────────────────────────────────────────────

    def test_per_owner_figures_are_by_account_owner_not_by_logged_name(self):
        # The names on records are free text; grouping on them would split
        # "J. Smith" from "John Smith" and invent a person.
        Note.objects.create(
            customer=self.customer,
            title="Note",
            author_name="Somebody Else Entirely",
            body="…",
            logged_at=self.today - timedelta(days=2),
        )

        by_owner = self.client.get(self.url).data["by_owner"]

        self.assertEqual([row["owner"] for row in by_owner], ["Carl"])
        self.assertEqual(by_owner[0]["touched"], 1)

    def test_unowned_accounts_are_their_own_row(self):
        Customer.objects.create(organisation=self.org, name="Orphan")

        owners = {row["owner"]: row for row in self.client.get(self.url).data["by_owner"]}

        self.assertIn("Unassigned", owners)
        self.assertEqual(owners["Unassigned"]["accounts"], 1)

    def test_overdue_tasks_exclude_completed_ones(self):
        Task.objects.create(
            customer=self.customer,
            title="Late",
            assignee_name="Carl",
            due_date=self.today - timedelta(days=5),
            status=Task.Status.PENDING,
        )
        Task.objects.create(
            customer=self.customer,
            title="Late but done",
            assignee_name="Carl",
            due_date=self.today - timedelta(days=5),
            status=Task.Status.COMPLETED,
        )

        kpis = self.client.get(self.url).data["kpis"]

        self.assertEqual(kpis["overdue_tasks"], 1)
        self.assertEqual(kpis["open_tasks"], 1)
        self.assertEqual(kpis["completed_tasks"], 1)

    # ── the timeline and filters ─────────────────────────────────────

    def test_the_timeline_buckets_by_week_and_by_source(self):
        self._activity(3)
        self._activity(4)
        Note.objects.create(
            customer=self.customer,
            title="Note",
            author_name="Carl",
            body="…",
            logged_at=self.today - timedelta(days=3),
        )

        timeline = self.client.get(self.url).data["timeline"]

        self.assertEqual(sum(week["activities"] for week in timeline), 2)
        self.assertEqual(sum(week["notes"] for week in timeline), 1)

    def test_filters_narrow_the_book(self):
        other = Customer.objects.create(
            organisation=self.org,
            name="Churned",
            owner=self.csm,
            lifecycle_stage=Customer.LifecycleStage.CHURN,
        )

        self.assertEqual(
            self.client.get(self.url, {"customer": other.id}).data["kpis"]["accounts"], 1
        )
        self.assertEqual(
            self.client.get(self.url, {"lifecycle": "churn"}).data["kpis"]["accounts"], 1
        )
        self.assertEqual(
            self.client.get(self.url, {"owner": "unassigned"}).data["kpis"]["accounts"], 0
        )

    def test_filter_options_are_scoped_to_the_visible_book(self):
        Customer.objects.create(organisation=self.org, name="Theirs", owner=self.other)

        options = self.client.get(self.url).data["filters"]

        self.assertEqual([row["name"] for row in options["customers"]], ["Acme"])
        self.assertEqual([row["name"] for row in options["owners"]], ["Carl"])
