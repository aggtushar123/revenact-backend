from datetime import date, timedelta
from types import SimpleNamespace

from django.test import SimpleTestCase

from services.account_story.attention import account_renewal
from services.customers.models import Account, Customer, Task, Ticket
from services.knowledge.models import Question
from services.organizations.story.attention import RENEWAL_WINDOW_DAYS

from .fixtures import AccountStoryFixture

TODAY = date(2026, 9, 30)


class AccountRenewalTests(SimpleTestCase):
    def renewal(self, days):
        due = None if days is None else TODAY + timedelta(days=days)
        return account_renewal(SimpleNamespace(renewal_date=due), TODAY)

    def test_no_date_is_nothing_to_renew(self):
        self.assertIsNone(self.renewal(None))

    def test_beyond_the_window_is_not_yet_attention(self):
        self.assertIsNone(self.renewal(RENEWAL_WINDOW_DAYS + 1))

    def test_overdue_or_due_within_thirty_days(self):
        for days, overdue in ((-5, True), (0, False), (30, False)):
            with self.subTest(days=days):
                self.assertEqual(
                    self.renewal(days),
                    {
                        "date": (TODAY + timedelta(days=days)).isoformat(),
                        "days": days,
                        "overdue": overdue,
                    },
                )


class AccountAttentionTests(AccountStoryFixture):
    def attention(self, user=None, account=None):
        return self.account_story(user, account)["attention"]

    def test_nothing_needs_attention(self):
        self.assertEqual(
            self.attention(),
            {
                "renewal": None,
                "tickets": None,
                "overdue_tasks": None,
                "questions": None,
                "anomaly": None,
            },
        )

    def test_the_account_s_own_renewal_not_its_organisation_s(self):
        Customer.objects.filter(pk=self.pizza.pk).update(
            renewal_date=self.today - timedelta(days=3)
        )
        self.assertIsNone(self.attention()["renewal"])
        due = self.today + timedelta(days=12)
        Account.objects.filter(pk=self.emea.pk).update(renewal_date=due)
        self.assertEqual(
            self.attention()["renewal"], {"date": due.isoformat(), "days": 12, "overdue": False}
        )

    def test_open_high_and_critical_tickets_on_this_account_only(self):
        self.ticket(self.emea, number="T-1", priority=Ticket.Priority.HIGH, day=self.days_ago(4))
        self.ticket(
            self.emea, number="T-2", priority=Ticket.Priority.CRITICAL, day=self.days_ago(1)
        )
        self.ticket(self.emea, number="T-3", priority=Ticket.Priority.MEDIUM, day=self.days_ago(9))
        self.ticket(
            self.emea,
            number="T-4",
            priority=Ticket.Priority.CRITICAL,
            status=Ticket.Status.RESOLVED,
            day=self.days_ago(20),
        )
        self.ticket(
            self.pizza, number="T-5", priority=Ticket.Priority.CRITICAL, day=self.days_ago(30)
        )
        self.ticket(
            self.apac, number="T-6", priority=Ticket.Priority.CRITICAL, day=self.days_ago(30)
        )
        self.assertEqual(self.attention()["tickets"], {"count": 2, "oldest_days": 4})

    def test_a_ticket_outside_the_viewer_s_department_is_not_counted(self):
        self.ticket(
            self.emea,
            priority=Ticket.Priority.CRITICAL,
            department="engineering",
            day=self.days_ago(6),
        )
        self.assertIsNone(self.attention()["tickets"])
        self.assertEqual(self.attention(self.engineer)["tickets"], {"count": 1, "oldest_days": 6})

    def test_overdue_tasks_on_this_account_the_viewer_may_read(self):
        self.task(self.emea, due=self.days_ago(3))
        self.task(self.emea, due=self.days_ago(8), status=Task.Status.COMPLETED)
        self.task(self.emea, due=self.days_ago(10), created_by=self.other, assignee=self.other)
        self.task(self.emea, due=self.today)
        self.task(self.pizza, due=self.days_ago(5))
        self.assertEqual(self.attention()["overdue_tasks"], {"count": 1, "oldest_days": 3})

    def test_no_knowledge_questions_and_no_anomaly_on_an_account(self):
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.csm,
            assignee=self.engineer,
            text="Why?",
        )
        attention = self.attention()
        self.assertIsNone(attention["questions"])
        self.assertIsNone(attention["anomaly"])
