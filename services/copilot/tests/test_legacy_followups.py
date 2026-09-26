"""Migration 0015's backfill: a plain reply written before follow-ups carried
the Ask snapshot, after an Ask reply in the same conversation, is marked as
fed Ask history with no snapshot, so slice readers are withheld from it."""

from services.copilot.legacy import mark_legacy_followups
from services.copilot.models import Conversation, Message
from services.copilot.views import REDACTED_REPLY, visible_messages
from services.knowledge.models import Question
from services.knowledge.tests.test_hierarchy import ChartFixture

ASK = {"surface": "organizations", "view": "list", "filters": {}, "labels": [], "focus": None}


class MarkLegacyFollowupsTests(ChartFixture):
    def setUp(self):
        super().setUp()
        self.conversation = Conversation.objects.create(
            organisation=self.org, user=self.carl, title="Legacy"
        )

    def pair(self, content, context=None, **reply):
        asked = Message.objects.create(
            conversation=self.conversation,
            role="user",
            content=content,
            author=self.carl,
            context=context,
        )
        Question.objects.create(
            organisation=self.org,
            asked_by=self.carl,
            assignee=self.priya,
            text=content,
            message=asked,
        )
        return Message.objects.create(
            conversation=self.conversation,
            role="assistant",
            content=f"re {content}",
            reply_to=asked,
            **reply,
        )

    def run_backfill(self):
        mark_legacy_followups(Message)

    def test_a_legacy_plain_reply_after_an_ask_reply_is_marked(self):
        self.pair("ask", ASK, grounded_customer_ids=[self.pizza.pk], carries_anomaly_text=False)
        follow_up = self.pair("summarise the above")

        self.run_backfill()

        follow_up.refresh_from_db()
        self.assertTrue(follow_up.carries_anomaly_text)
        self.assertIsNone(follow_up.grounded_customer_ids)
        replies = [
            m.content
            for m in visible_messages(self.conversation, self.priya)
            if m.role == "assistant"
        ]
        self.assertEqual(replies[-1], REDACTED_REPLY)

    def test_a_plain_reply_with_no_earlier_ask_reply_is_untouched(self):
        plain = self.pair("hello")
        self.pair("ask", ASK, grounded_customer_ids=[self.pizza.pk], carries_anomaly_text=False)

        self.run_backfill()

        plain.refresh_from_db()
        self.assertIsNone(plain.carries_anomaly_text)
        self.assertIsNone(plain.grounded_customer_ids)

    def test_an_ask_reply_is_untouched(self):
        first = self.pair(
            "ask", ASK, grounded_customer_ids=[self.pizza.pk], carries_anomaly_text=False
        )
        second = self.pair("again", ASK)

        self.run_backfill()

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.grounded_customer_ids, [self.pizza.pk])
        self.assertIs(first.carries_anomaly_text, False)
        self.assertIsNone(second.carries_anomaly_text)

    def test_an_already_snapshotted_followup_is_untouched(self):
        self.pair("ask", ASK, grounded_customer_ids=[self.pizza.pk], carries_anomaly_text=False)
        follow_up = self.pair(
            "summarise", grounded_customer_ids=[self.pizza.pk], carries_anomaly_text=False
        )

        self.run_backfill()

        follow_up.refresh_from_db()
        self.assertEqual(follow_up.grounded_customer_ids, [self.pizza.pk])
        self.assertIs(follow_up.carries_anomaly_text, False)
