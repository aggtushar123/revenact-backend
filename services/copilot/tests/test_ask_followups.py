"""What an Ask reply can reach through what comes after it, read through the
real API. A reply written with no context of its own (a plain follow-up in
the same conversation) is fed the earlier Ask replies as history and can
repeat them, so it carries their snapshot and is read against it; a
conversation's `origin` is the first turn's context and is shown only to a
reader who may read that turn; the model is fed the newest turns, not the
oldest; and a slice reader's read does not cost more per Ask reply."""

from unittest.mock import patch

from django.db import connection
from django.test.utils import CaptureQueriesContext

from services.copilot.models import Conversation, Message
from services.copilot.views import HISTORY_WINDOW, REDACTED_REPLY
from services.customers.models import Customer
from services.knowledge.models import Question
from services.knowledge.tests.test_hierarchy import ChartFixture

SEND = "/api/v1/copilot/messages/"
LIST = "/api/v1/copilot/conversations/"


def _in_order(query, candidates):
    return [(index, 1.0) for index in range(len(candidates))]


def organizations(**filters):
    return {"surface": "organizations", "view": "list", "filters": filters, "focus": None}


def dashboard_overview():
    return {
        "surface": "dashboard",
        "area": "overview",
        "view": None,
        "filters": {"owner": "", "lifecycle": "", "customer": ""},
        "focus": None,
    }


class AskFixture(ChartFixture):
    def setUp(self):
        super().setUp()
        patcher = patch("services.copilot.retrieval.rank_by_similarity", side_effect=_in_order)
        patcher.start()
        self.addCleanup(patcher.stop)
        completion = patch("services.copilot.views.get_completion", return_value="An answer.")
        self.completion = completion.start()
        self.addCleanup(completion.stop)
        # Carl's second account; Priya has no reason to see it.
        self.secret = Customer.objects.create(
            organisation=self.org, name="Secret Corp", owner=self.carl
        )
        # Priya may open Pizza Hut, and only that.
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.carl,
            assignee=self.priya,
            text="Pizza Hut?",
        )

    def send(self, user, content, context=None, conversation=None, reply="An answer."):
        self.completion.return_value = reply
        self.client.force_authenticate(user)
        body = {"content": content}
        if context is not None:
            body["context"] = context
        if conversation is not None:
            body["conversation_id"] = conversation
        response = self.client.post(SEND, body, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        return response.data["id"]

    def detail(self, user, conversation):
        self.client.force_authenticate(user)
        response = self.client.get(f"{LIST}{conversation}/")
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def replies(self, user, conversation):
        messages = self.detail(user, conversation)["messages"]
        return [m["content"] for m in messages if m["role"] == "assistant"]


class ContextlessFollowUpTests(AskFixture):
    """I1: "@Priya Nair summarise the above" has no context, but the model
    was fed the Ask reply above it."""

    LEAK = "Secret Corp ARR is 1M."

    def test_a_followup_after_an_organizations_ask_is_withheld_from_a_slice_reader(self):
        first = self.send(
            self.carl,
            "@Priya Nair how is it going?",
            organizations(search="Secret"),
            reply=self.LEAK,
        )
        self.send(self.carl, "@Priya Nair summarise the above", conversation=first, reply=self.LEAK)

        follow_up = Message.objects.filter(role="assistant").order_by("id").last()
        self.assertEqual(follow_up.grounded_customer_ids, [self.secret.pk])
        self.assertEqual(self.replies(self.priya, first), [REDACTED_REPLY, REDACTED_REPLY])
        self.assertEqual(self.replies(self.carl, first), [self.LEAK, self.LEAK])

    def test_a_followup_after_a_dashboard_ask_is_withheld_from_a_slice_reader(self):
        first = self.send(
            self.carl, "@Priya Nair what needs us?", dashboard_overview(), reply=self.LEAK
        )
        self.send(self.carl, "@Priya Nair summarise the above", conversation=first, reply=self.LEAK)

        self.assertEqual(self.replies(self.priya, first), [REDACTED_REPLY, REDACTED_REPLY])
        self.assertEqual(self.replies(self.carl, first), [self.LEAK, self.LEAK])

    def test_a_followup_fed_only_what_the_reader_sees_is_readable(self):
        first = self.send(
            self.carl, "@Priya Nair Pizza Hut?", organizations(ids=str(self.pizza.pk))
        )
        self.send(self.carl, "@Priya Nair summarise the above", conversation=first)

        follow_up = Message.objects.filter(role="assistant").order_by("id").last()
        self.assertEqual(follow_up.grounded_customer_ids, [self.pizza.pk])
        self.assertEqual(self.replies(self.priya, first), ["An answer.", "An answer."])

    def test_a_followup_fed_a_legacy_ask_reply_fails_closed(self):
        first = self.send(
            self.carl, "@Priya Nair Pizza Hut?", organizations(ids=str(self.pizza.pk))
        )
        Message.objects.filter(role="assistant").update(
            grounded_customer_ids=None, carries_anomaly_text=None
        )
        self.send(self.carl, "@Priya Nair summarise the above", conversation=first)

        follow_up = Message.objects.filter(role="assistant").order_by("id").last()
        self.assertIsNone(follow_up.grounded_customer_ids)
        self.assertEqual(self.replies(self.priya, first), [REDACTED_REPLY, REDACTED_REPLY])

    def test_a_second_followup_still_carries_the_ask_snapshot(self):
        # The first follow-up is itself fed to the second; what it carried
        # travels on, whatever the window holds.
        first = self.send(
            self.carl,
            "@Priya Nair how is it going?",
            organizations(search="Secret"),
            reply=self.LEAK,
        )
        self.send(self.carl, "@Priya Nair summarise the above", conversation=first, reply=self.LEAK)
        self.send(self.carl, "@Priya Nair and shorter?", conversation=first, reply=self.LEAK)

        last = Message.objects.filter(role="assistant").order_by("id").last()
        self.assertEqual(last.grounded_customer_ids, [self.secret.pk])
        self.assertEqual(self.replies(self.priya, first), [REDACTED_REPLY] * 3)

    def test_a_followup_with_no_ask_before_it_keeps_the_per_source_rule(self):
        first = self.send(self.carl, "@Priya Nair how is engineering?")
        self.send(self.carl, "@Priya Nair and now?", conversation=first)

        follow_up = Message.objects.filter(role="assistant").order_by("id").last()
        self.assertIsNone(follow_up.grounded_customer_ids)
        self.assertIsNone(follow_up.carries_anomaly_text)
        self.assertEqual(self.replies(self.priya, first), ["An answer.", "An answer."])


class OriginGateTests(AskFixture):
    """I2: `origin` is the first turn's context; a reader who may not read
    that turn gets `origin: null`, as they get the neutral title."""

    def setUp(self):
        super().setUp()
        # Carl's first turn is not addressed to Priya, and Carl is outside
        # her scope: she reads only the later turn that mentions her.
        self.conversation = self.send(
            self.carl, "Which accounts need us?", organizations(search="Secret Corp")
        )
        self.send(
            self.carl,
            "@Priya Nair can engineering help?",
            conversation=self.conversation,
        )

    def listed(self, user):
        self.client.force_authenticate(user)
        response = self.client.get(LIST)
        self.assertEqual(response.status_code, 200, response.data)
        return next(row for row in response.data if row["id"] == self.conversation)

    def test_a_slice_reader_gets_no_origin_in_the_list(self):
        row = self.listed(self.priya)

        self.assertEqual(row["title"], "Shared conversation")
        self.assertIsNone(row["origin"])

    def test_a_slice_reader_gets_no_origin_in_the_detail(self):
        data = self.detail(self.priya, self.conversation)

        self.assertEqual(data["title"], "Shared conversation")
        self.assertIsNone(data["origin"])

    def test_the_owner_gets_the_origin_in_both(self):
        self.assertEqual(self.listed(self.carl)["origin"]["filters"], {"search": "Secret Corp"})
        origin = self.detail(self.carl, self.conversation)["origin"]
        self.assertEqual(origin["labels"], ['Search: "Secret Corp"'])

    def test_a_slice_reader_who_reads_the_first_turn_gets_the_origin(self):
        opened = self.send(
            self.carl, "@Priya Nair Pizza Hut?", organizations(ids=str(self.pizza.pk))
        )

        self.assertEqual(self.detail(self.priya, opened)["origin"]["surface"], "organizations")
        row = next(r for r in self.listed_all(self.priya) if r["id"] == opened)
        self.assertEqual(row["origin"]["filters"], {"ids": str(self.pizza.pk)})

    def test_a_slice_reader_of_the_first_turn_but_not_the_first_ask_gets_no_origin(self):
        opened = self.send(self.carl, "@Priya Nair a quick one")
        self.send(
            self.carl,
            "Which accounts need us?",
            organizations(search="Secret Corp"),
            conversation=opened,
        )

        data = self.detail(self.priya, opened)
        self.assertEqual(data["title"], "@Priya Nair a quick one")
        self.assertIsNone(data["origin"])
        row = next(r for r in self.listed_all(self.priya) if r["id"] == opened)
        self.assertIsNone(row["origin"])
        self.assertEqual(
            self.detail(self.carl, opened)["origin"]["filters"], {"search": "Secret Corp"}
        )

    def listed_all(self, user):
        self.client.force_authenticate(user)
        return self.client.get(LIST).data


class HistoryWindowTests(AskFixture):
    """M6: the model is fed the newest HISTORY_WINDOW turns, oldest first,
    and the snapshot folds in exactly those."""

    def setUp(self):
        super().setUp()
        self.conversation = Conversation.objects.create(
            organisation=self.org, user=self.carl, title="Long"
        )

    def turn(self, n, *, context=None, grounded=None):
        asked = Message.objects.create(
            conversation=self.conversation,
            role="user",
            content=f"question {n}",
            author=self.carl,
            context=context,
        )
        Message.objects.create(
            conversation=self.conversation,
            role="assistant",
            content=f"answer {n}",
            reply_to=asked,
            grounded_customer_ids=grounded,
            carries_anomaly_text=False if context else None,
        )

    def test_the_newest_turns_are_fed_in_order(self):
        for n in range(12):
            self.turn(n)

        self.send(self.carl, "and now?", conversation=self.conversation.pk)

        fed = self.completion.call_args.kwargs["messages"]
        self.assertEqual(len(fed), HISTORY_WINDOW + 1)
        self.assertEqual(fed[0]["content"], "question 2")
        self.assertEqual(fed[-2]["content"], "answer 11")
        self.assertEqual(fed[-1]["content"], "and now?")

    def test_the_snapshot_folds_the_same_window(self):
        # The oldest Ask reply has no snapshot and falls outside the window;
        # the ten newest were grounded on Pizza Hut only.
        ask = organizations(ids=str(self.pizza.pk))
        self.turn(0, context=ask, grounded=None)
        for n in range(1, 12):
            self.turn(n, context=ask, grounded=[self.pizza.pk])

        self.send(self.carl, "summarise", conversation=self.conversation.pk)

        last = Message.objects.filter(role="assistant").order_by("id").last()
        self.assertEqual(last.grounded_customer_ids, [self.pizza.pk])


class SliceReadQueryCountTests(AskFixture):
    """M1: reading Ask replies costs a slice reader a fixed few queries more
    than the owner, not a few more per reply."""

    def read_costs(self, replies):
        first = None
        for _ in range(replies):
            first = self.send(
                self.carl,
                "@Priya Nair how is Pizza Hut?",
                organizations(ids=str(self.pizza.pk)),
                conversation=first,
            )
        costs = {}
        for who in (self.carl, self.priya):
            # A fresh user each read: nothing cached on the instance from before.
            who = type(who).objects.get(pk=who.pk)
            self.client.force_authenticate(who)
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(f"{LIST}{first}/")
            self.assertEqual(response.status_code, 200)
            costs[who.pk] = len(queries)
        self.assertEqual(self.replies(self.priya, first), ["An answer."] * replies)
        Conversation.objects.all().delete()
        return costs[self.carl.pk], costs[self.priya.pk]

    def test_a_slice_reader_pays_a_flat_extra(self):
        owner_one, slice_one = self.read_costs(1)
        owner_five, slice_five = self.read_costs(5)

        self.assertEqual(slice_five - owner_five, slice_one - owner_one)
