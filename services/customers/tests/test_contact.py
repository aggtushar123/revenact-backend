"""One definition of contact, two renderings, one answer.

`contact.py` owns what counts as contact. The Python aggregate serves Activity
Tracking; the SQL annotation serves every list page through
`with_health_inputs`. These tests exist so the two cannot drift: the
annotation is built from the same TOUCH_SOURCES table, but a GREATEST that
started returning NULL for a NULL input would silently strip the touch
component from every health score, and only a parity test notices that.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from services.accounts.models import Organisation, User
from services.customers import contact
from services.customers.models import (
    Account,
    Activity,
    CalendarEvent,
    Call,
    Customer,
    Email,
    Note,
    Ticket,
    with_health_inputs,
)


class ContactRuleTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="supersecret1",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.today = timezone.localdate()
        self.now = timezone.now()

    def _customer(self, name="Globex", **overrides):
        return Customer.objects.create(
            organisation=self.org, name=name, owner=self.csm, **overrides
        )

    def _annotated(self, customer):
        return with_health_inputs(Customer.objects.filter(pk=customer.pk)).get()._last_touch_on

    def _days_ago(self, days):
        return self.today - timedelta(days=days)

    def _meeting(self, customer, on):
        return CalendarEvent.objects.create(
            customer=customer,
            title="QBR",
            description="Quarterly",
            type=CalendarEvent.EventType.MEETING,
            event_date=on,
            start_time="10:00",
            end_time="11:00",
        )

    # ── every source counts ──────────────────────────────────────────

    def test_each_kind_of_contact_counts_as_touch(self):
        """The change: the rubric counted Activity rows only, so a customer
        emailed every week could decay to "no touch"."""
        cases = {
            "email": lambda c: Email.objects.create(
                customer=c,
                subject="Checking in",
                sender_name="Carl",
                sent_at=self.now - timedelta(days=3),
            ),
            "call": lambda c: Call.objects.create(
                customer=c, title="Sync", host_name="Carl", occurred_at=self.now - timedelta(days=3)
            ),
            "note": lambda c: Note.objects.create(
                customer=c, title="Notes", author_name="Carl", logged_at=self._days_ago(3)
            ),
            "meeting": lambda c: self._meeting(c, self._days_ago(3)),
            "activity": lambda c: Activity.objects.create(
                customer=c,
                type=Activity.ActivityType.HEALTH_CHECK_REVIEW,
                occurred_at=self._days_ago(3),
            ),
        }
        for kind, make in cases.items():
            customer = self._customer(name=f"Only a {kind}", joined_date=self._days_ago(400))
            make(customer)

            self.assertEqual(
                customer.health_inputs()["days_since_touch"], 3, f"{kind} did not count"
            )
            self.assertEqual(self._annotated(customer), self._days_ago(3), f"{kind} in SQL")

    def test_the_newest_contact_wins_whatever_its_kind(self):
        customer = self._customer(joined_date=self._days_ago(400))
        Activity.objects.create(
            customer=customer,
            type=Activity.ActivityType.HEALTH_CHECK_REVIEW,
            occurred_at=self._days_ago(60),
        )
        Email.objects.create(
            customer=customer,
            subject="Newer",
            sender_name="Carl",
            sent_at=self.now - timedelta(days=5),
        )
        Note.objects.create(
            customer=customer, title="Older", author_name="Carl", logged_at=self._days_ago(30)
        )

        self.assertEqual(customer.health_inputs()["days_since_touch"], 5)
        self.assertEqual(self._annotated(customer), self._days_ago(5))

    def test_a_touch_on_a_division_is_a_touch_on_the_company(self):
        customer = self._customer(joined_date=self._days_ago(400))
        account = Account.objects.create(name="Globex EMEA", owner=self.csm)
        account.customers.add(customer)
        Call.objects.create(
            account=account,
            title="Regional sync",
            host_name="Carl",
            occurred_at=self.now - timedelta(days=8),
        )

        self.assertEqual(customer.health_inputs()["days_since_touch"], 8)
        self.assertEqual(self._annotated(customer), self._days_ago(8))

    def test_a_ticket_is_not_a_touch(self):
        # Inbound: the customer reaching us is not evidence anyone reached them.
        customer = self._customer(joined_date=self._days_ago(40))
        Ticket.objects.create(
            customer=customer,
            ticket_number="TKT-1",
            title="Broken",
            assignee_name="Support",
            priority=Ticket.Priority.LOW,
            opened_at=self._days_ago(1),
        )

        self.assertEqual(customer.health_inputs()["days_since_touch"], 40)
        self.assertIsNone(self._annotated(customer))

    def test_no_contact_at_all_is_none_not_a_date(self):
        customer = self._customer(joined_date=self._days_ago(40))

        self.assertIsNone(self._annotated(customer))
        self.assertEqual(contact.last_contact_by_customer([customer.pk]), {})
        # And the rubric then measures from arrival, as before.
        self.assertEqual(customer.health_inputs()["days_since_touch"], 40)

    # ── the two renderings agree ─────────────────────────────────────

    def test_sql_and_python_renderings_agree_across_a_mixed_book(self):
        """The test that matters. Both read TOUCH_SOURCES, but the SQL side
        leans on GREATEST skipping NULLs; if that ever stopped being true
        every customer missing one kind of contact would lose its touch
        component, and this is where it would show."""
        quiet = self._customer(name="Quiet")
        emailed = self._customer(name="Emailed")
        Email.objects.create(
            customer=emailed, subject="Hi", sender_name="Carl", sent_at=self.now - timedelta(days=2)
        )
        mixed = self._customer(name="Mixed")
        Note.objects.create(
            customer=mixed, title="n", author_name="Carl", logged_at=self._days_ago(20)
        )
        self._meeting(mixed, self._days_ago(9))
        via_account = self._customer(name="Via account")
        account = Account.objects.create(name="Div", owner=self.csm)
        account.customers.add(via_account)
        Activity.objects.create(
            account=account,
            type=Activity.ActivityType.HEALTH_CHECK_REVIEW,
            occurred_at=self._days_ago(12),
        )

        ids = [quiet.pk, emailed.pk, mixed.pk, via_account.pk]
        python_side = contact.last_contact_by_customer(ids)
        sql_side = {
            row.pk: row._last_touch_on
            for row in with_health_inputs(Customer.objects.filter(pk__in=ids))
        }

        self.assertEqual(sql_side[quiet.pk], None)
        self.assertEqual(python_side.get(quiet.pk), None)
        for pk in (emailed.pk, mixed.pk, via_account.pk):
            self.assertEqual(sql_side[pk], python_side[pk], pk)
        self.assertEqual(sql_side[emailed.pk], self._days_ago(2))
        self.assertEqual(sql_side[mixed.pk], self._days_ago(9))
        self.assertEqual(sql_side[via_account.pk], self._days_ago(12))

    def test_the_source_list_is_the_one_activity_tracking_reads(self):
        from services.customers import activity_tracking

        self.assertIs(activity_tracking.TOUCH_SOURCES, contact.TOUCH_SOURCES)
        self.assertIs(activity_tracking.last_contact_by_customer, contact.last_contact_by_customer)
