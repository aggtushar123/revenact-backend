"""Who may read an Organizations reply in a shared conversation. The reply
carries the tiles, sections and top lists of the asker's whole filtered list,
so a mentioned-only reader must see every account in it — rebuilt by the
portfolio's filter rules, not the Dashboard's. Owners and grant holders read
it whole; a missing asker or an unknown surface fails closed."""

from datetime import timedelta

from django.utils import timezone

from services.copilot.models import (
    Conversation,
    CopilotSession,
    Message,
    SessionInvite,
    SessionParticipant,
)
from services.copilot.views import REDACTED_REPLY, visible_messages
from services.customers.models import Customer
from services.knowledge.models import Question
from services.knowledge.tests.test_hierarchy import ChartFixture


class OrganizationsReplyReadabilityTests(ChartFixture):
    def setUp(self):
        super().setUp()
        # Carl's second account; Priya has no reason to see it.
        self.secret = Customer.objects.create(
            organisation=self.org, name="Secret Corp", owner=self.carl, health_score=8
        )
        Customer.objects.filter(pk=self.pizza.pk).update(
            health_score=2, renewal_date=timezone.localdate() + timedelta(days=10)
        )
        self.conversation = Conversation.objects.create(
            organisation=self.org, user=self.carl, title="Ask Revenact"
        )

    def ask(self, filters, *, author=None):
        author = author or self.carl
        content = "@Priya Nair which accounts need us?"
        asked = Message.objects.create(
            conversation=self.conversation,
            role="user",
            content=content,
            author=author,
            context={
                "surface": "organizations",
                "view": "list",
                "filters": filters,
                "labels": [],
                "focus": None,
            },
        )
        # Routing the question to Priya makes her a mentioned-only reader who
        # may also open Pizza Hut (questions__assignee).
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=author,
            assignee=self.priya,
            text=content,
            message=asked,
        )
        return Message.objects.create(
            conversation=self.conversation,
            role="assistant",
            content=f"Answer for {filters}",
            sources=[],
            reply_to=asked,
        )

    def read(self, user):
        return [message.content for message in visible_messages(self.conversation, user)]

    def test_a_reader_who_cannot_see_the_askers_whole_list_is_withheld(self):
        reply = self.ask({})

        kept = self.read(self.priya)

        self.assertNotIn(reply.content, kept)
        self.assertIn(REDACTED_REPLY, kept)

    def test_the_list_is_rebuilt_by_the_portfolio_rules_not_the_dashboards(self):
        # `health` is a portfolio filter the Dashboard's rule ignores: read by
        # that rule the list would be all of Carl's, Secret Corp included.
        reply = self.ask({"health": "poor"})

        self.assertIn(reply.content, self.read(self.priya))

    def test_ids_narrow_the_list_as_on_the_page(self):
        reply = self.ask({"ids": str(self.pizza.pk)})

        self.assertIn(reply.content, self.read(self.priya))

    def test_renews_within_is_widened_when_the_reply_is_read(self):
        # Asked with "renews within 30" the list was only Pizza Hut; the date
        # filter is dropped when read, so Secret Corp is in it and it fails closed.
        reply = self.ask({"renews_within": "30"})

        self.assertNotIn(reply.content, self.read(self.priya))

    def test_the_asker_reads_their_own_reply(self):
        reply = self.ask({})

        self.assertIn(reply.content, self.read(self.carl))

    def test_an_accepted_grant_holder_reads_it_whole(self):
        reply = self.ask({})
        session = CopilotSession.objects.create(
            conversation=self.conversation, status=CopilotSession.Status.LIVE
        )
        SessionInvite.objects.create(
            session=session,
            invited_user=self.dana,
            invited_by=self.carl,
            status=SessionInvite.Status.ACCEPTED,
        )
        SessionParticipant.objects.create(session=session, user=self.dana)

        self.assertIn(reply.content, self.read(self.dana))

    def test_a_deleted_askers_reply_fails_closed(self):
        reply = self.ask({"health": "poor"})
        Message.objects.filter(role="user").update(author=None)

        self.assertNotIn(reply.content, self.read(self.priya))
        self.assertIn(reply.content, self.read(self.carl))

    def test_an_unknown_surface_fails_closed(self):
        reply = self.ask({"health": "poor"})
        Message.objects.filter(role="user").update(
            context={"surface": "pipelines", "filters": {"health": "poor"}}
        )

        self.assertNotIn(reply.content, self.read(self.priya))

    def test_a_missing_surface_fails_closed(self):
        reply = self.ask({"health": "poor"})
        Message.objects.filter(role="user").update(context={"filters": {"health": "poor"}})

        self.assertNotIn(reply.content, self.read(self.priya))

    def test_the_anomaly_gate_does_not_apply_because_no_anomaly_text_is_carried(self):
        # Alice sees everything. The Dashboard withholds her Overview replies from
        # Priya (stored anomaly titles are org-wide); an Organizations digest never
        # carries one (test_organizations_grounding), so the book check decides.
        reply = self.ask({"ids": str(self.pizza.pk)}, author=self.alice)

        self.assertIn(reply.content, self.read(self.priya))


class OrganizationsChurnedBookReadabilityTests(OrganizationsReplyReadabilityTests):
    """Secret Corp has churned, so Carl's live list is only Pizza Hut, which
    Priya may open. A list that brings churned or archived rows back — as the
    grounding did — must be read with them: the Dashboard's live-only rule
    would let Priya read prose naming an account she cannot see."""

    def setUp(self):
        super().setUp()
        Customer.objects.filter(pk=self.secret.pk).update(
            lifecycle_stage=Customer.LifecycleStage.CHURN,
            churn_date=timezone.localdate() - timedelta(days=30),
        )

    def test_a_reader_who_cannot_see_the_askers_whole_list_is_withheld(self):
        # Secret Corp is no longer on the live list, so Priya sees all of it.
        reply = self.ask({})

        self.assertIn(reply.content, self.read(self.priya))

    def test_renews_within_is_widened_when_the_reply_is_read(self):
        # Widened, the live list is still only Pizza Hut.
        reply = self.ask({"renews_within": "30"})

        self.assertIn(reply.content, self.read(self.priya))

    def test_include_churned_brings_the_churned_account_into_the_list(self):
        reply = self.ask({"include_churned": "1"})

        self.assertNotIn(reply.content, self.read(self.priya))

    def test_include_churned_survives_the_renews_within_widening(self):
        reply = self.ask({"include_churned": "1", "renews_within": "30"})

        self.assertNotIn(reply.content, self.read(self.priya))

    def test_a_churn_lifecycle_filter_lists_the_churned_account(self):
        reply = self.ask({"lifecycle": "churn"})

        self.assertNotIn(reply.content, self.read(self.priya))

    def test_ids_name_the_churned_account(self):
        reply = self.ask({"ids": f"{self.pizza.pk},{self.secret.pk}"})

        self.assertNotIn(reply.content, self.read(self.priya))

    def test_ids_name_an_archived_account(self):
        Customer.objects.filter(pk=self.secret.pk).update(
            lifecycle_stage=Customer.LifecycleStage.LIVE, churn_date=None, is_archived=True
        )
        reply = self.ask({"ids": f"{self.pizza.pk},{self.secret.pk}"})

        self.assertNotIn(reply.content, self.read(self.priya))

    def test_a_reader_who_sees_the_whole_list_reads_it_churned_included(self):
        reply = self.ask({"include_churned": "1"})
        # Priya is also routed a question on Secret Corp, so she may open it.
        Question.objects.create(
            organisation=self.org,
            customer=self.secret,
            asked_by=self.carl,
            assignee=self.priya,
            text="And Secret Corp?",
        )

        self.assertIn(reply.content, self.read(self.priya))
