from datetime import timedelta

from django.utils import timezone

from services.accounts.models import User
from services.anomalies.models import Anomaly, AnomalyEvidence
from services.customers.models import Customer, Task, Ticket
from services.knowledge.models import Question

from .story_fixtures import StoryFixture


class AttentionTests(StoryFixture):
    def attention(self, user=None, customer=None, **query):
        return self.story(user, customer, **query)["attention"]

    def anomaly(self, title, *, seen_days_ago=0, status=Anomaly.Status.LIVE):
        now = timezone.now()
        return Anomaly.objects.create(
            organisation=self.org,
            title=title,
            status=status,
            first_seen_at=now - timedelta(days=seen_days_ago + 3),
            last_seen_at=now - timedelta(days=seen_days_ago),
        )

    def evidence(self, anomaly, parent, *, record_id, kind=AnomalyEvidence.Kind.CALL, **fields):
        return AnomalyEvidence.objects.create(
            anomaly=anomaly,
            organisation=self.org,
            kind=kind,
            record_id=record_id,
            snippet="checkout fails",
            occurred_at=timezone.now(),
            **self.on(parent),
            **fields,
        )

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

    def test_a_renewal_overdue_or_due_within_thirty_days(self):
        for days, overdue in ((-5, True), (0, False), (30, False)):
            renewal_date = self.today + timedelta(days=days)
            Customer.objects.filter(pk=self.pizza.pk).update(renewal_date=renewal_date)
            self.assertEqual(
                self.attention()["renewal"],
                {"date": renewal_date.isoformat(), "days": days, "overdue": overdue},
            )
        Customer.objects.filter(pk=self.pizza.pk).update(
            renewal_date=self.today + timedelta(days=31)
        )
        self.assertIsNone(self.attention()["renewal"])

    def test_a_churned_organisation_has_no_renewal_to_chase(self):
        Customer.objects.filter(pk=self.pizza.pk).update(
            renewal_date=self.days_ago(5), churn_date=self.days_ago(1)
        )
        self.assertIsNone(self.attention()["renewal"])
        Customer.objects.filter(pk=self.pizza.pk).update(
            churn_date=None, lifecycle_stage=Customer.LifecycleStage.CHURN
        )
        self.assertIsNone(self.attention()["renewal"])

    def test_open_high_and_critical_tickets_the_viewer_may_read(self):
        self.ticket(self.pizza, priority=Ticket.Priority.HIGH, day=self.days_ago(9))
        self.ticket(self.emea, priority=Ticket.Priority.CRITICAL, day=self.days_ago(2))
        self.ticket(self.pizza, priority=Ticket.Priority.MEDIUM, day=self.days_ago(20))
        self.ticket(
            self.pizza,
            priority=Ticket.Priority.HIGH,
            status=Ticket.Status.RESOLVED,
            day=self.days_ago(30),
        )
        self.ticket(
            self.apac,
            priority=Ticket.Priority.HIGH,
            department="engineering",
            day=self.days_ago(40),
        )
        self.assertEqual(self.attention()["tickets"], {"count": 2, "oldest_days": 9})
        self.assertEqual(self.attention(self.admin)["tickets"], {"count": 3, "oldest_days": 40})
        self.assertEqual(
            self.attention(account=str(self.emea.pk))["tickets"], {"count": 1, "oldest_days": 2}
        )

    def test_overdue_tasks_the_viewer_may_read(self):
        self.task(self.pizza, due=self.days_ago(4))
        self.task(self.emea, due=self.days_ago(1))
        self.task(self.pizza, due=self.today)
        self.task(self.pizza, due=self.days_ago(8), status=Task.Status.COMPLETED)
        self.task(self.pizza, due=self.days_ago(12), created_by=self.other, assignee=self.other)
        self.assertEqual(self.attention()["overdue_tasks"], {"count": 2, "oldest_days": 4})
        self.assertEqual(
            self.attention(account="none")["overdue_tasks"], {"count": 1, "oldest_days": 4}
        )

    def test_unanswered_questions_the_viewer_may_read(self):
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.csm,
            assignee=self.engineer,
            text="Why is usage down?",
        )
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.csm,
            assignee=self.engineer,
            text="Answered already",
            status=Question.Status.ANSWERED,
        )
        self.assertEqual(self.attention()["questions"], {"count": 1})

        open_co = self.customer("Open Co", owner=None)
        colleague = User.objects.create_user(
            email="eli@acme.io",
            password="supersecret1",
            name="Eli Engineer",
            organisation=self.org,
            role=User.Role.CSM,
            function=User.Function.ENGINEERING,
        )
        Question.objects.create(
            organisation=self.org,
            customer=open_co,
            asked_by=self.engineer,
            assignee=colleague,
            text="Between engineers",
        )
        self.assertIsNone(self.attention(customer=open_co)["questions"])
        self.assertEqual(self.attention(self.engineer, customer=open_co)["questions"], {"count": 1})

    def test_the_latest_live_anomaly_with_its_title_withheld_unless_seeing_everything(self):
        older = self.anomaly("Checkout fails at Pizza Hut and Taco Co", seen_days_ago=5)
        latest = self.anomaly("SSO outage at Pizza Hut", seen_days_ago=1)
        resolved = self.anomaly("Old fault", status=Anomaly.Status.RESOLVED)
        self.evidence(older, self.pizza, record_id=1)
        self.evidence(latest, self.emea, record_id=2)
        self.evidence(resolved, self.pizza, record_id=3)

        mine = self.attention()["anomaly"]
        self.assertEqual(set(mine), {"id", "title", "first_seen_at", "last_seen_at"})
        self.assertEqual(mine["id"], latest.pk)
        self.assertEqual(mine["title"], "Similar reports across 1 of your companies")
        self.assertEqual(self.attention(self.admin)["anomaly"]["title"], "SSO outage at Pizza Hut")
        self.assertIsNone(self.attention(account=str(self.apac.pk))["anomaly"])
        self.assertEqual(self.attention(account="none")["anomaly"]["id"], older.pk)

    def test_an_anomaly_the_viewer_may_not_read_or_on_another_organisation_is_not_shown(self):
        hidden = self.anomaly("Pricing complaints")
        self.evidence(
            hidden,
            self.pizza,
            record_id=4,
            kind=AnomalyEvidence.Kind.EMAIL,
            mailbox_owner=self.other,
        )
        elsewhere = self.anomaly("Only at Taco")
        self.evidence(elsewhere, self.customer("Taco Co"), record_id=5)
        self.assertIsNone(self.attention()["anomaly"])
