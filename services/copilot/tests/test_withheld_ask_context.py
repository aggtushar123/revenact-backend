"""A withheld Ask reply's question hides its Ask context too, read through the
real API on every Ask surface.

A mentioned reader who may not read a reply (`views._reply_readable_by`) gets
`REDACTED_REPLY` in its place. The question it answers is still theirs to
read, but its `context` — the server-built label ("Accounts · Search:
"Hidden""), the filters and the asker's free-text search, a focus — names
what the asker was looking at, so it is withheld with the reply: `context` is
null for that reader. The conversation's `origin` is the first Ask turn's
context, so the header and the History list give that reader `origin: None`
when the first Ask turn's reply is withheld from them. The title is the
question's opening words (never the label), so it stays.

The privacy setup is "blind to one account": Globex is owned by a colleague,
the Viewer owns its Seen account and cannot open its Hidden one, nor Carl's
Secret Corp. Alice (an admin in Leadership, who sees everything) asks and
mentions both the Viewer and Zoe, another admin who sees everything."""

from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.test.utils import CaptureQueriesContext

from services.accounts.models import User
from services.copilot.models import Conversation, Message
from services.copilot.views import REDACTED_REPLY
from services.customers.models import Contact, Customer, Opportunity
from services.customers.tests.test_views import blind_to_one_account
from services.knowledge.tests.test_hierarchy import ChartFixture

SEND = "/api/v1/copilot/messages/"
LIST = "/api/v1/copilot/conversations/"
ANSWER = "An answer."
QUESTION = "@Viewer @Zoe Admin what is going on here?"


def _in_order(query, candidates):
    return [(index, 1.0) for index in range(len(candidates))]


class WithheldAskContextFixture(ChartFixture):
    def setUp(self):
        super().setUp()
        patcher = patch("services.copilot.retrieval.rank_by_similarity", side_effect=_in_order)
        patcher.start()
        self.addCleanup(patcher.stop)
        completion = patch("services.copilot.views.get_completion", return_value=ANSWER)
        completion.start()
        self.addCleanup(completion.stop)
        self.globex = Customer.objects.create(organisation=self.org, name="Globex")
        self.viewer, self.seen, self.hidden = blind_to_one_account(self.globex)
        self.secret = Customer.objects.create(
            organisation=self.org, name="Secret Corp", owner=self.carl
        )
        self.zoe = User.objects.create_user(
            email="zoe@acme.io",
            password="x",
            name="Zoe Admin",
            organisation=self.org,
            role=User.Role.ADMIN,
            function="leadership",
        )
        Contact.objects.create(account=self.hidden, name="Hal Hidden")

    def send(self, context, conversation=None, content=QUESTION, user=None):
        self.client.force_authenticate(user or self.alice)
        body = {"content": content, "context": context}
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

    def listed(self, user, conversation):
        self.client.force_authenticate(user)
        response = self.client.get(LIST)
        self.assertEqual(response.status_code, 200, response.data)
        return next(c for c in response.data if c["id"] == conversation)


class WithheldAskContextMixin:
    """One surface's context, asked by Alice; subclasses name it."""

    def context(self):
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.conversation = self.send(self.context())
        self.stored = Message.objects.get(role="user", conversation_id=self.conversation).context
        self.origin = Conversation.objects.get(pk=self.conversation).origin
        # Precondition: the reply is withheld from the Viewer, and only from them.
        self.assertEqual(self.replies(self.viewer), [REDACTED_REPLY])
        self.assertEqual(self.replies(self.zoe), [ANSWER])
        self.assertEqual(self.replies(self.alice), [ANSWER])

    def replies(self, user):
        messages = self.detail(user, self.conversation)["messages"]
        return [m["content"] for m in messages if m["role"] == "assistant"]

    def question(self, user):
        messages = self.detail(user, self.conversation)["messages"]
        return next(m for m in messages if m["role"] == "user")

    def test_the_withheld_replys_question_carries_no_ask_context(self):
        turn = self.question(self.viewer)

        self.assertIsNone(turn["context"])
        # The question's own words are theirs to read, as before.
        self.assertEqual(turn["content"], QUESTION)

    def test_the_header_names_no_origin(self):
        self.assertIsNone(self.detail(self.viewer, self.conversation)["origin"])

    def test_the_history_names_no_origin(self):
        self.assertIsNone(self.listed(self.viewer, self.conversation)["origin"])

    def test_the_title_is_the_question_not_the_label(self):
        title = QUESTION[:50]
        self.assertEqual(self.detail(self.viewer, self.conversation)["title"], title)
        self.assertEqual(self.listed(self.viewer, self.conversation)["title"], title)

    def test_the_owner_keeps_the_whole_context(self):
        self.assertEqual(self.question(self.alice)["context"], self.stored)
        self.assertEqual(self.detail(self.alice, self.conversation)["origin"], self.origin)
        self.assertEqual(self.listed(self.alice, self.conversation)["origin"], self.origin)

    def test_a_mentioned_reader_who_reads_the_reply_keeps_the_whole_context(self):
        self.assertEqual(self.question(self.zoe)["context"], self.stored)
        self.assertEqual(self.detail(self.zoe, self.conversation)["origin"], self.origin)
        self.assertEqual(self.listed(self.zoe, self.conversation)["origin"], self.origin)


class DashboardTests(WithheldAskContextMixin, WithheldAskContextFixture):
    def context(self):
        return {
            "surface": "dashboard",
            "area": "overview",
            "view": None,
            "filters": {"owner": "", "lifecycle": "", "customer": str(self.secret.pk)},
            "focus": None,
        }

    def test_the_stored_context_names_the_hidden_organisation(self):
        self.assertEqual(self.stored["filters"]["customer"], str(self.secret.pk))


class OrganizationsTests(WithheldAskContextMixin, WithheldAskContextFixture):
    def context(self):
        return {
            "surface": "organizations",
            "view": "list",
            "filters": {"search": "Secret"},
            "focus": None,
        }

    def test_the_stored_context_carries_the_search(self):
        self.assertIn('Search: "Secret"', self.stored["labels"])
        self.assertEqual(self.origin["filters"]["search"], "Secret")

    def test_the_redacted_reply_can_never_be_saved(self):
        from services.copilot.views import visible_messages

        conversation = Conversation.objects.get(pk=self.conversation)
        viewer = User.objects.get(pk=self.viewer.pk)
        redacted = [m for m in visible_messages(conversation, viewer) if m.role == "assistant"]
        self.assertEqual([m.content for m in redacted], [REDACTED_REPLY])

        with self.assertRaises(TypeError):
            redacted[0].save()

        stored = Message.objects.get(pk=redacted[0].pk)
        self.assertEqual(stored.content, ANSWER)


class ContactsTests(WithheldAskContextMixin, WithheldAskContextFixture):
    def context(self):
        return {"surface": "contacts", "view": "list", "filters": {"q": "Hal"}}

    def test_the_stored_context_carries_the_search(self):
        self.assertIn("Hal", self.stored["label"])
        self.assertIn("Hal", self.origin["label"])


class AccountsTests(WithheldAskContextMixin, WithheldAskContextFixture):
    def context(self):
        return {"surface": "accounts", "view": "list", "filters": {"search": "Hidden"}}

    def test_the_stored_context_carries_the_search(self):
        self.assertIn("Hidden", self.stored["label"])
        self.assertIn("Hidden", self.origin["label"])


class PipelinesTests(WithheldAskContextMixin, WithheldAskContextFixture):
    def context(self):
        # A deal on the account the Viewer is blind to: the reply counts it,
        # so it is withheld from them, and so is the question's Ask context.
        Opportunity.objects.create(
            title="Hidden seats",
            mrr=Decimal("1000"),
            stage=Opportunity.Stage.NEGOTIATION,
            account=self.hidden,
        )
        return {
            "surface": "pipelines",
            "kind": "opportunities",
            "view": "list",
            "filters": {"search": "Hidden"},
        }

    def test_the_stored_context_carries_the_search(self):
        self.assertIn("Hidden", self.stored["label"])
        self.assertIn("Hidden", self.origin["label"])


class WithheldContextQueryCountTests(WithheldAskContextFixture):
    """Hiding a withheld reply's context reuses the readability decision the
    read already made: a slice reader's detail and History reads still cost a
    flat extra over the owner's, however many withheld Ask turns there are."""

    def costs(self, turns):
        context = {"surface": "organizations", "view": "list", "filters": {"search": "Secret"}}
        conversation = None
        for _ in range(turns):
            conversation = self.send(context, conversation=conversation)
        costs = {}
        for who in (self.alice, self.viewer):
            who = User.objects.get(pk=who.pk)
            self.client.force_authenticate(who)
            with CaptureQueriesContext(connection) as detail:
                response = self.client.get(f"{LIST}{conversation}/")
            self.assertEqual(response.status_code, 200)
            with CaptureQueriesContext(connection) as listed:
                self.assertEqual(self.client.get(LIST).status_code, 200)
            costs[who.pk] = (len(detail), len(listed))
        replies = [m["content"] for m in self.detail(self.viewer, conversation)["messages"]]
        self.assertEqual(replies.count(REDACTED_REPLY), turns)
        Conversation.objects.all().delete()
        owner, reader = costs[self.alice.pk], costs[self.viewer.pk]
        return reader[0] - owner[0], reader[1] - owner[1]

    def test_a_slice_reader_pays_a_flat_extra_for_withheld_turns(self):
        self.assertEqual(self.costs(1), self.costs(4))


class DroppedReplyTests(WithheldAskContextFixture):
    """Fail closed: a question whose reply the reader is not shown at all — not
    redacted, dropped, because a turn they may not read was sent in between
    (two participants interleaving) — keeps its words but not its Ask
    context, and as the first Ask turn gives no origin either."""

    def setUp(self):
        super().setUp()
        self.ask_context = {
            "surface": "accounts",
            "view": "list",
            "filters": {"search": "Hidden"},
            "label": 'Accounts · Search: "Hidden"',
        }
        self.conversation = Conversation.objects.create(
            organisation=self.org,
            user=self.alice,
            title=QUESTION[:50],
            origin=dict(self.ask_context),
        )
        self.asked = Message.objects.create(
            conversation=self.conversation,
            role="user",
            content=QUESTION,
            author=self.alice,
            context=dict(self.ask_context),
        )
        from services.knowledge.models import Question

        Question.objects.create(
            organisation=self.org,
            asked_by=self.alice,
            assignee=self.viewer,
            text=QUESTION,
            message=self.asked,
        )
        # Carl's turn, sent before the reply landed and addressed to Priya:
        # not the Viewer's to read, so neither is what follows it.
        aside = Message.objects.create(
            conversation=self.conversation,
            role="user",
            content="@Priya Nair Carl's own aside",
            author=self.carl,
        )
        Question.objects.create(
            organisation=self.org,
            asked_by=self.carl,
            assignee=self.priya,
            text=aside.content,
            message=aside,
        )
        Message.objects.create(
            conversation=self.conversation,
            role="assistant",
            content=ANSWER,
            reply_to=self.asked,
        )

    def test_the_question_carries_no_ask_context_and_the_header_no_origin(self):
        body = self.detail(self.viewer, self.conversation.pk)

        # Precondition: the question is theirs, its reply is not shown at all.
        self.assertEqual([m["content"] for m in body["messages"]], [QUESTION])
        self.assertIsNone(body["messages"][0]["context"])
        self.assertIsNone(body["origin"])
        self.assertIsNone(self.listed(self.viewer, self.conversation.pk)["origin"])

    def test_the_owner_keeps_the_whole_context(self):
        body = self.detail(self.alice, self.conversation.pk)

        self.assertEqual(body["messages"][0]["context"], self.ask_context)
        self.assertEqual(body["origin"], self.ask_context)

    def test_the_stripped_copy_can_never_be_saved(self):
        from services.copilot.views import visible_messages

        conversation = Conversation.objects.get(pk=self.conversation.pk)
        viewer = User.objects.get(pk=self.viewer.pk)
        (stripped,) = visible_messages(conversation, viewer)
        self.assertIsNone(stripped.context)

        with self.assertRaises(TypeError):
            stripped.save()
        with self.assertRaises(TypeError):
            stripped.save(update_fields=["context"])

        self.asked.refresh_from_db()
        self.assertEqual(self.asked.context, self.ask_context)


class MultiTurnTests(WithheldAskContextFixture):
    """Two Ask turns, one reply readable to the Viewer and one withheld: each
    question's context follows its own reply, and the origin follows the
    first Ask turn's. A later Ask reply is fed the earlier ones as history and
    folds in their snapshot, so after a withheld first reply the second is
    readable only when the Viewer asked it themselves (an asker always reads
    their own Ask reply)."""

    READABLE = {"surface": "accounts", "view": "list", "filters": {"search": "Seen"}}
    WITHHELD = {"surface": "accounts", "view": "list", "filters": {"search": "Hidden"}}

    def converse(self, first, second, second_by=None):
        conversation = self.send(first, content=QUESTION + " (1)")
        self.send(second, conversation=conversation, content=QUESTION + " (2)", user=second_by)
        stored = {
            m.content: m.context
            for m in Message.objects.filter(conversation_id=conversation, role="user")
        }
        origin = Conversation.objects.get(pk=conversation).origin
        body = self.detail(self.viewer, conversation)
        questions = [m for m in body["messages"] if m["role"] == "user"]
        replies = [m["content"] for m in body["messages"] if m["role"] == "assistant"]
        listed = self.listed(self.viewer, conversation)
        return stored, origin, body, questions, replies, listed

    def test_a_readable_first_and_a_withheld_second(self):
        stored, origin, body, questions, replies, listed = self.converse(
            self.READABLE, self.WITHHELD
        )

        self.assertEqual(replies, [ANSWER, REDACTED_REPLY])
        self.assertEqual(questions[0]["context"], stored[QUESTION + " (1)"])
        self.assertIsNotNone(questions[0]["context"])
        self.assertIsNone(questions[1]["context"])
        self.assertEqual(body["origin"], origin)
        self.assertIsNotNone(body["origin"])
        self.assertEqual(listed["origin"], origin)

    def test_a_withheld_first_and_a_readable_second(self):
        stored, origin, body, questions, replies, listed = self.converse(
            self.WITHHELD, self.READABLE, second_by=self.viewer
        )

        self.assertEqual(replies, [REDACTED_REPLY, ANSWER])
        self.assertIsNone(questions[0]["context"])
        self.assertEqual(questions[1]["context"], stored[QUESTION + " (2)"])
        self.assertIsNotNone(questions[1]["context"])
        self.assertIsNone(body["origin"])
        self.assertIsNone(listed["origin"])
