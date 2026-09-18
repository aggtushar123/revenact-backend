"""The Communications queue: what counts as waiting, and who may see it."""

from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.capabilities import Capability
from services.accounts.models import Organisation, Role, User
from services.customers import communications
from services.customers.models import Account, Call, Customer, Email, Ticket
from services.knowledge.models import Question
from services.mail.models import MailboxConnection


def ago(days):
    return timezone.now() - timedelta(days=days)


class CommunicationsBase(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.other_org = Organisation.objects.create(name="Rival Ltd")

        def mk(email, name, org=None, **kw):
            return User.objects.create_user(
                email=email, password="x", name=name, organisation=org or self.org, **kw
            )

        self.alice = mk("alice@acme.io", "Alice", role=User.Role.ADMIN)
        self.carl = mk("carl@acme.io", "Carl", reports_to=self.alice)
        self.dana = mk("dana@acme.io", "Dana", reports_to=self.carl)
        self.priya = mk(
            "priya@acme.io", "Priya", reports_to=self.alice, function=User.Function.ENGINEERING
        )
        self.outsider = mk("zed@rival.io", "Zed", org=self.other_org, role=User.Role.ADMIN)

        seeing = Role.objects.create(
            organisation=self.org,
            name="Lead",
            slug="lead",
            permissions=[Capability.VIEW_ALL_ACCOUNTS],
        )
        User.objects.filter(pk__in=[self.carl.pk, self.dana.pk, self.priya.pk]).update(role=seeing)
        for person in (self.carl, self.dana, self.priya):
            person.refresh_from_db()

        self.pizza = Customer.objects.create(
            organisation=self.org, name="Pizza Hut", owner=self.carl, health_score="5.2"
        )

        self.mailbox = MailboxConnection.objects.create(
            organisation=self.org,
            user=self.carl,
            provider=MailboxConnection.Provider.GOOGLE,
            address="carl@acme.io",
        )

    def email(self, *, thread, direction, days, owner=None, subject="Subject", parent=None):
        return Email.objects.create(
            customer=parent or self.pizza,
            subject=subject,
            sender_name="Dana Whitfield",
            recipient_name="Carl",
            body="Body text",
            sent_at=ago(days),
            mailbox=self.mailbox,
            mailbox_owner=owner or self.carl,
            direction=direction,
            thread_id=thread,
            provider_message_id=f"{thread}-{direction}-{days}",
        )


class WaitingPredicateTests(CommunicationsBase):
    """What the queue counts as owed."""

    def test_an_unanswered_inbound_email_is_owed(self):
        self.email(thread="t1", direction=Email.Direction.RECEIVED, days=9)
        owed = communications.replies_owed(self.carl)
        self.assertEqual(owed.count(), 1)

    def test_a_thread_answered_after_they_wrote_is_not_owed(self):
        self.email(thread="t1", direction=Email.Direction.RECEIVED, days=9)
        self.email(thread="t1", direction=Email.Direction.SENT, days=8)
        self.assertEqual(communications.replies_owed(self.carl).count(), 0)

    def test_they_wrote_again_after_our_reply_so_it_is_owed_once(self):
        """Three inbound messages in one thread are one debt, not three."""
        self.email(thread="t1", direction=Email.Direction.RECEIVED, days=9)
        self.email(thread="t1", direction=Email.Direction.SENT, days=8)
        self.email(thread="t1", direction=Email.Direction.RECEIVED, days=7)
        self.email(thread="t1", direction=Email.Direction.RECEIVED, days=6)

        owed = list(communications.replies_owed(self.carl))
        self.assertEqual(len(owed), 1)
        # The newest inbound message is the one shown, so the wait is 6 days.
        self.assertEqual(owed[0].sent_at.date(), ago(6).date())

    def test_mail_with_no_mailbox_owner_is_nobody_s_debt(self):
        Email.objects.create(
            customer=self.pizza,
            subject="Seeded",
            sender_name="Someone",
            recipient_name="Someone",
            body="b",
            sent_at=ago(30),
            direction=Email.Direction.RECEIVED,
            thread_id="seed",
        )
        self.assertEqual(communications.replies_owed(self.carl).count(), 0)

    def test_an_email_without_a_thread_id_is_not_counted(self):
        self.email(thread="", direction=Email.Direction.RECEIVED, days=5)
        self.assertEqual(communications.replies_owed(self.carl).count(), 0)

    def test_only_open_questions_assigned_to_me(self):
        mine = Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.alice,
            assignee=self.carl,
            text="Which seat number?",
        )
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.alice,
            assignee=self.dana,
            text="Someone else's",
        )
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.alice,
            assignee=self.carl,
            text="Already answered",
            status=Question.Status.ANSWERED,
        )
        self.assertEqual([q.pk for q in communications.questions_for(self.carl)], [mine.pk])

    def test_resolved_tickets_are_not_owed(self):
        Ticket.objects.create(
            customer=self.pizza,
            ticket_number="T-1",
            title="Open one",
            assignee_name="x",
            status="open",
            priority="high",
            opened_at=timezone.localdate() - timedelta(days=3),
        )
        Ticket.objects.create(
            customer=self.pizza,
            ticket_number="T-2",
            title="Done",
            assignee_name="x",
            status="resolved",
            priority="high",
            opened_at=timezone.localdate() - timedelta(days=3),
        )
        self.assertEqual(
            [t.title for t in communications.open_tickets(self.carl)],
            ["Open one"],
        )

    def test_only_calls_without_a_summary_and_only_mine(self):
        Call.objects.create(
            customer=self.pizza,
            title="No summary",
            host_name="Carl",
            occurred_at=ago(2),
            summary="",
            logged_by=self.carl,
        )
        Call.objects.create(
            customer=self.pizza,
            title="Written up",
            host_name="Carl",
            occurred_at=ago(2),
            summary="We agreed a plan.",
            logged_by=self.carl,
        )
        Call.objects.create(
            customer=self.pizza,
            title="Dana's",
            host_name="Dana",
            occurred_at=ago(2),
            summary="",
            logged_by=self.dana,
        )
        self.assertEqual(
            [c.title for c in communications.calls_to_wrap_up(self.carl)],
            ["No summary"],
        )


class ScopeTests(CommunicationsBase):
    """Mine is the person; team is the person and everyone below them."""

    def test_team_scope_includes_a_report_s_mail_mine_does_not(self):
        dana_box = MailboxConnection.objects.create(
            organisation=self.org,
            user=self.dana,
            provider=MailboxConnection.Provider.GOOGLE,
            address="dana@acme.io",
        )
        Email.objects.create(
            customer=self.pizza,
            subject="For Dana",
            sender_name="Them",
            recipient_name="Dana",
            body="b",
            sent_at=ago(4),
            mailbox=dana_box,
            mailbox_owner=self.dana,
            direction=Email.Direction.RECEIVED,
            thread_id="dana-thread",
            provider_message_id="d1",
        )
        self.email(thread="carl-thread", direction=Email.Direction.RECEIVED, days=2)

        self.assertEqual(communications.replies_owed(self.carl, "mine").count(), 1)
        self.assertEqual(communications.replies_owed(self.carl, "team").count(), 2)
        # Dana cannot see up the chain, only her own.
        self.assertEqual(communications.replies_owed(self.dana, "team").count(), 1)


class RowTests(CommunicationsBase):
    """The merged list: order, shape and the context that travels with a row."""

    def test_rows_are_ordered_by_longest_wait_across_kinds(self):
        self.email(thread="t1", direction=Email.Direction.RECEIVED, days=9)
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.alice,
            assignee=self.carl,
            text="A question",
        )
        Ticket.objects.create(
            customer=self.pizza,
            ticket_number="T-9",
            title="A ticket",
            assignee_name="x",
            status="open",
            priority="high",
            opened_at=timezone.localdate() - timedelta(days=5),
        )

        rows, truncated = communications.rows(communications.waiting_querysets(self.carl))
        self.assertFalse(truncated)
        self.assertEqual([r["kind"] for r in rows], ["email", "ticket", "question"])
        self.assertEqual([r["waiting_days"] for r in rows], [9, 5, 0])

    def test_a_row_carries_the_account_context_for_the_reply(self):
        self.pizza.renewal_date = timezone.localdate() + timedelta(days=34)
        self.pizza.arr_billed_at_account = "128400.00"
        self.pizza.save()
        self.email(thread="t1", direction=Email.Direction.RECEIVED, days=9)

        rows, _ = communications.rows(communications.waiting_querysets(self.carl))
        context = rows[0]["context"]
        self.assertEqual(context["days_to_renewal"], 34)
        self.assertEqual(context["arr"], 128400.0)
        self.assertEqual(context["owner"], "Carl")
        self.assertEqual(rows[0]["account"]["name"], "Pizza Hut")

    def test_the_cap_is_reported_rather_than_silently_trimming(self):
        for index in range(4):
            self.email(thread=f"t{index}", direction=Email.Direction.RECEIVED, days=index + 1)
        querysets = communications.waiting_querysets(self.carl, kinds=["email"])
        rows, truncated = communications.rows(querysets, limit_per_kind=2)
        self.assertEqual(len(rows), 2)
        self.assertTrue(truncated)

    def test_an_account_level_record_names_its_account(self):
        account = Account.objects.create(name="Pizza Hut EMEA")
        account.customers.add(self.pizza)
        Call.objects.create(
            account=account,
            title="EMEA sync",
            host_name="Carl",
            occurred_at=ago(2),
            summary="",
            logged_by=self.carl,
        )
        rows, _ = communications.rows(communications.waiting_querysets(self.carl, kinds=["call"]))
        self.assertEqual(
            rows[0]["account"], {"id": account.id, "name": "Pizza Hut EMEA", "type": "account"}
        )


class CommunicationsViewTests(CommunicationsBase):
    """The endpoints."""

    url = "/api/v1/communications/"
    stats_url = "/api/v1/communications/stats/"

    def test_authentication_is_required(self):
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(self.client.get(self.stats_url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_the_queue_comes_back_in_the_list_shape_the_app_already_walks(self):
        self.email(thread="t1", direction=Email.Direction.RECEIVED, days=9)
        self.client.force_authenticate(self.carl)
        body = self.client.get(self.url).data

        self.assertEqual(body["count"], 1)
        self.assertIsNone(body["next"])
        self.assertIsNone(body["previous"])
        self.assertEqual(body["mode"], "needs")
        self.assertEqual(body["scope"], "mine")
        self.assertFalse(body["truncated"])
        self.assertEqual(body["results"][0]["kind"], "email")
        self.assertEqual(body["results"][0]["waiting_days"], 9)

    def test_filtering_by_kind(self):
        self.email(thread="t1", direction=Email.Direction.RECEIVED, days=9)
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.alice,
            assignee=self.carl,
            text="A question",
        )
        self.client.force_authenticate(self.carl)
        body = self.client.get(self.url, {"kind": "question"}).data
        self.assertEqual([row["kind"] for row in body["results"]], ["question"])

    def test_search_narrows_the_queue(self):
        self.email(thread="t1", direction=Email.Direction.RECEIVED, days=9, subject="Renewal terms")
        self.email(thread="t2", direction=Email.Direction.RECEIVED, days=8, subject="Invoice 8841")
        self.client.force_authenticate(self.carl)

        body = self.client.get(self.url, {"q": "invoice"}).data
        self.assertEqual([row["subject"] for row in body["results"]], ["Invoice 8841"])

    def test_pagination_links_carry_the_other_filters(self):
        for index in range(30):
            self.email(thread=f"t{index}", direction=Email.Direction.RECEIVED, days=index + 1)
        self.client.force_authenticate(self.carl)

        body = self.client.get(self.url, {"kind": "email"}).data
        self.assertEqual(body["count"], 30)
        self.assertEqual(len(body["results"]), 25)
        self.assertIn("page=2", body["next"])
        self.assertIn("kind=email", body["next"])

    def test_stats_returns_the_four_tiles_and_the_worst_wait(self):
        self.email(thread="t1", direction=Email.Direction.RECEIVED, days=9)
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.alice,
            assignee=self.carl,
            text="A question",
        )
        self.client.force_authenticate(self.carl)
        body = self.client.get(self.stats_url).data

        self.assertEqual(body["counts"], {"email": 1, "question": 1, "ticket": 0, "call": 0})
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["oldest_waiting_days"], 9)
        self.assertTrue(body["has_mailbox"])

    def test_stats_says_when_no_mailbox_is_connected(self):
        self.client.force_authenticate(self.dana)
        self.assertFalse(self.client.get(self.stats_url).data["has_mailbox"])

    def test_another_tenant_sees_nothing_of_ours(self):
        self.email(thread="t1", direction=Email.Direction.RECEIVED, days=9)
        self.client.force_authenticate(self.outsider)
        body = self.client.get(self.url).data
        self.assertEqual(body["count"], 0)

    def test_the_everything_tab_is_a_different_question(self):
        self.email(thread="t1", direction=Email.Direction.RECEIVED, days=9)
        self.email(thread="t1", direction=Email.Direction.SENT, days=1)
        self.client.force_authenticate(self.carl)

        queue = self.client.get(self.url).data
        everything = self.client.get(self.url, {"needs": "false"}).data

        # Answered, so nothing is owed; both messages still happened.
        self.assertEqual(queue["count"], 0)
        self.assertEqual(everything["count"], 2)
        self.assertEqual(everything["mode"], "everything")
