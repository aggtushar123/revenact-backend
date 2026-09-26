"""Who may read an Organizations reply in a shared conversation. The reply
carries the tiles, sections and top lists of the asker's whole filtered list,
so a mentioned-only reader must see every account it was grounded on — the
ids fixed on the reply when it was written, never a list rebuilt later, when
health, owners, churn or the asker's own seat may have moved. Owners and
grant holders read it whole; a missing asker, a missing snapshot, an unknown
surface or malformed stored filters fail closed."""

from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

from services.accounts.models import Organisation
from services.copilot.models import (
    Conversation,
    CopilotSession,
    Message,
    SessionInvite,
    SessionParticipant,
)
from services.copilot.organizations_grounding import build_organizations_grounding
from services.copilot.views import REDACTED_REPLY, visible_messages
from services.customers.models import Customer
from services.knowledge.models import Question
from services.knowledge.tests.test_hierarchy import ChartFixture


def _in_order(query, candidates):
    return [(index, 1.0) for index in range(len(candidates))]


class OrganizationsReplyReadabilityTests(ChartFixture):
    def setUp(self):
        super().setUp()
        patcher = patch("services.copilot.retrieval.rank_by_similarity", side_effect=_in_order)
        patcher.start()
        self.addCleanup(patcher.stop)
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
        """The turn and its reply, grounded as SendMessageView grounds them."""
        author = author or self.carl
        content = "@Priya Nair which accounts need us?"
        context = {
            "surface": "organizations",
            "view": "list",
            "filters": filters,
            "labels": [],
            "focus": None,
        }
        asked = Message.objects.create(
            conversation=self.conversation,
            role="user",
            content=content,
            author=author,
            context=context,
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
        grounding = build_organizations_grounding(author, context, content)
        return Message.objects.create(
            conversation=self.conversation,
            role="assistant",
            content=f"Answer for {filters}",
            sources=grounding.sources,
            grounded_customer_ids=grounding.customer_ids,
            reply_to=asked,
        )

    def read(self, user):
        return [message.content for message in visible_messages(self.conversation, user)]

    def test_a_reader_who_cannot_see_the_askers_whole_list_is_withheld(self):
        reply = self.ask({})

        kept = self.read(self.priya)

        self.assertNotIn(reply.content, kept)
        self.assertIn(REDACTED_REPLY, kept)

    def test_the_list_is_the_portfolios_not_the_dashboards(self):
        # `health` is a portfolio filter the Dashboard's rule ignores: read by
        # that rule the list would be all of Carl's, Secret Corp included.
        reply = self.ask({"health": "poor"})

        self.assertEqual(reply.grounded_customer_ids, [self.pizza.pk])
        self.assertIn(reply.content, self.read(self.priya))

    def test_ids_narrow_the_list_as_on_the_page(self):
        reply = self.ask({"ids": str(self.pizza.pk)})

        self.assertIn(reply.content, self.read(self.priya))

    def test_renews_within_is_read_as_it_was_asked(self):
        # The snapshot is the list the reply was built from: only Pizza Hut
        # renews within 30 days, so nothing about Secret Corp reached it.
        reply = self.ask({"renews_within": "30"})

        self.assertEqual(reply.grounded_customer_ids, [self.pizza.pk])
        self.assertIn(reply.content, self.read(self.priya))

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
        # carries one (test_organizations_grounding), so the snapshot decides.
        reply = self.ask({"ids": str(self.pizza.pk)}, author=self.alice)

        self.assertIn(reply.content, self.read(self.priya))

    # -- The book drifts after the ask: the reply is read as it was written.

    def test_drift_health_recomputed_after_a_poor_health_ask(self):
        Customer.objects.filter(pk=self.secret.pk).update(health_score=2)
        reply = self.ask({"health": "poor"})
        Customer.objects.filter(pk=self.secret.pk).update(health_score=8)

        self.assertNotIn(reply.content, self.read(self.priya))

    def test_drift_the_account_is_reassigned(self):
        reply = self.ask({"owner": str(self.carl.pk)})
        Customer.objects.filter(pk=self.secret.pk).update(owner=self.raj)

        self.assertNotIn(reply.content, self.read(self.priya))

    def test_drift_the_account_churns_after_the_ask(self):
        reply = self.ask({})
        Customer.objects.filter(pk=self.secret.pk).update(
            lifecycle_stage=Customer.LifecycleStage.CHURN, churn_date=timezone.localdate()
        )

        self.assertNotIn(reply.content, self.read(self.priya))

    def test_drift_the_asker_moves_to_another_organisation(self):
        reply = self.ask({})
        elsewhere = Organisation.objects.create(name="Elsewhere Ltd")
        type(self.carl).objects.filter(pk=self.carl.pk).update(organisation=elsewhere)

        self.assertNotIn(reply.content, self.read(self.priya))

    def test_drift_the_asker_is_deactivated_and_their_accounts_reassigned(self):
        reply = self.ask({})
        type(self.carl).objects.filter(pk=self.carl.pk).update(is_active=False)
        Customer.objects.filter(owner=self.carl).update(owner=self.raj)

        self.assertNotIn(reply.content, self.read(self.priya))

    # -- Fail closed on what cannot be trusted.

    def test_a_reply_with_no_snapshot_is_withheld(self):
        reply = self.ask({"health": "poor"})
        Message.objects.filter(pk=reply.pk).update(grounded_customer_ids=None)

        self.assertNotIn(reply.content, self.read(self.priya))
        self.assertIn(reply.content, self.read(self.carl))

    def test_a_malformed_snapshot_is_withheld(self):
        reply = self.ask({"health": "poor"})
        for bad in ({"ids": [self.pizza.pk]}, [str(self.pizza.pk)], [True], "1"):
            Message.objects.filter(pk=reply.pk).update(grounded_customer_ids=bad)
            self.assertNotIn(reply.content, self.read(self.priya), bad)

    def test_malformed_stored_filters_are_withheld_without_an_error(self):
        reply = self.ask({"health": "poor"})
        for bad in ("health=poor", ["poor"], {"health": ["poor"]}, {"ids": {"x": 1}}):
            context = {"surface": "organizations", "view": "list", "filters": bad, "focus": None}
            Message.objects.filter(role="user").update(context=context)
            self.assertNotIn(reply.content, self.read(self.priya), bad)

    def test_a_reader_who_sees_every_stored_id_reads_it(self):
        reply = self.ask({})
        # Priya is also routed a question on Secret Corp, so she may open it.
        Question.objects.create(
            organisation=self.org,
            customer=self.secret,
            asked_by=self.carl,
            assignee=self.priya,
            text="And Secret Corp?",
        )

        self.assertIn(reply.content, self.read(self.priya))


class OrganizationsChurnedBookReadabilityTests(OrganizationsReplyReadabilityTests):
    """Secret Corp has churned before the ask, so Carl's live list is only
    Pizza Hut, which Priya may open. A list that brings churned or archived
    rows back — as the grounding did — is read with them."""

    def setUp(self):
        super().setUp()
        Customer.objects.filter(pk=self.secret.pk).update(
            lifecycle_stage=Customer.LifecycleStage.CHURN,
            churn_date=timezone.localdate() - timedelta(days=30),
        )

    def test_a_reader_who_cannot_see_the_askers_whole_list_is_withheld(self):
        # Secret Corp is not on the live list, so Priya sees all of it.
        reply = self.ask({})

        self.assertIn(reply.content, self.read(self.priya))

    def test_drift_health_recomputed_after_a_poor_health_ask(self):
        Customer.objects.filter(pk=self.secret.pk).update(health_score=2)
        reply = self.ask({"health": "poor", "include_churned": "1"})
        Customer.objects.filter(pk=self.secret.pk).update(health_score=8)

        self.assertNotIn(reply.content, self.read(self.priya))

    def test_drift_the_account_churns_after_the_ask(self):
        # Already churned: the live list never held it.
        reply = self.ask({})

        self.assertIn(reply.content, self.read(self.priya))

    def test_drift_the_account_is_reassigned(self):
        reply = self.ask({"owner": str(self.carl.pk), "include_churned": "1"})
        Customer.objects.filter(pk=self.secret.pk).update(owner=self.raj)

        self.assertNotIn(reply.content, self.read(self.priya))

    def test_drift_the_asker_moves_to_another_organisation(self):
        reply = self.ask({"include_churned": "1"})
        elsewhere = Organisation.objects.create(name="Elsewhere Ltd")
        type(self.carl).objects.filter(pk=self.carl.pk).update(organisation=elsewhere)

        self.assertNotIn(reply.content, self.read(self.priya))

    def test_drift_the_asker_is_deactivated_and_their_accounts_reassigned(self):
        reply = self.ask({"include_churned": "1"})
        type(self.carl).objects.filter(pk=self.carl.pk).update(is_active=False)
        Customer.objects.filter(owner=self.carl).update(owner=self.raj)

        self.assertNotIn(reply.content, self.read(self.priya))

    def test_include_churned_brings_the_churned_account_into_the_list(self):
        reply = self.ask({"include_churned": "1"})

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

    def test_a_reader_who_sees_every_stored_id_reads_it(self):
        reply = self.ask({"include_churned": "1"})
        Question.objects.create(
            organisation=self.org,
            customer=self.secret,
            asked_by=self.carl,
            assignee=self.priya,
            text="And Secret Corp?",
        )

        self.assertIn(reply.content, self.read(self.priya))
