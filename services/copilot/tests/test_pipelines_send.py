"""POST /api/v1/copilot/messages/ from Pipelines, and what a mentioned reader
of a shared conversation is shown. The model call is stubbed. The tests read
the prompt it was given, what was stored, and `views._reply_readable_by` —
the check the conversation read runs.

The privacy setup is "blind to one account": Pizza Hut is owned by a
colleague, and the viewer owns its Seen account but cannot open its Hidden
one. Alice (an admin in Leadership, who sees everything) asks; the viewer
(a CSM in Customer Success) reads."""

from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from services.accounts.models import User
from services.copilot.grounded_records import account_ref, record_ref, union_records
from services.copilot.models import Conversation, Message, ModelCall
from services.copilot.pipelines_context import (
    NOT_OPEN_ACCOUNT,
    NOT_OPEN_ITEM,
    NOT_OPEN_ORGANISATION,
)
from services.copilot.views import (
    REDACTED_REPLY,
    UNKNOWN,
    _reply_readable_by,
    ask_snapshot,
    pipeline_snapshot,
    records_snapshot,
    tickets_snapshot,
)
from services.customers.models import Opportunity, Risk
from services.customers.tests.test_views import blind_to_one_account

from .pipelines_fixture import PipelinesAskFixture, listing

URL = "/api/v1/copilot/messages/"


class PipelinesSendFixture(PipelinesAskFixture):
    def send(self, context, content="What is going on?", user=None, **extra):
        api = self.api
        if user is not None:
            api = APIClient()
            api.force_authenticate(user)
        return api.post(URL, {"content": content, "context": context, **extra}, format="json")


@patch("services.copilot.views.get_completion", return_value="Chase Upsell first.")
class PipelinesSendTests(PipelinesSendFixture):
    def test_an_answer_is_grounded_on_the_book_and_metered_as_pipelines(self, completion):
        self.opportunity("Upsell", expected_close=self.days(-5))

        response = self.send(listing())

        self.assertEqual(response.status_code, 200, response.data)
        kwargs = completion.call_args.kwargs
        self.assertEqual(kwargs["purpose"], "pipelines")
        self.assertIn("Pipelines data:\n<dashboard_data>", kwargs["system"])
        self.assertIn("Screen: Pipelines › Opportunities › List", kwargs["system"])
        self.assertIn("  - Upsell — Pizza Hut (organisation)", kwargs["system"])

    def test_the_context_is_stored_with_the_servers_label_and_the_origin_drops_the_focus(
        self, completion
    ):
        risk = self.risk("Budget cut")
        context = {
            **listing("risks", "board", {"kind": "risk", "id": risk.pk}, priority="medium"),
            "label": "Spoofed",
        }

        data = self.send(context).data

        origin = {
            "surface": "pipelines",
            "kind": "risks",
            "view": "board",
            "filters": {"priority": "medium"},
            "label": "Pipelines · Risks · Priority: Medium",
        }
        self.assertEqual(data["origin"], origin)
        self.assertEqual(
            data["messages"][0]["context"], {**origin, "focus": {"kind": "risk", "id": risk.pk}}
        )
        listed = self.api.get("/api/v1/copilot/conversations/").data
        self.assertEqual(listed[0]["origin"], origin)

    def test_one_conversation_spans_both_kinds_and_both_views(self, completion):
        self.opportunity("Upsell")
        self.risk("Budget cut")
        first = self.send(listing()).data

        second = self.send(listing("risks", "board"), "And the risks?", conversation_id=first["id"])

        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(second.data["origin"], first["origin"])
        asked = [m["context"] for m in second.data["messages"] if m["role"] == "user"]
        self.assertEqual(
            [(c["kind"], c["view"]) for c in asked],
            [("opportunities", "list"), ("risks", "board")],
        )
        self.assertIn("Screen: Pipelines › Risks › Board", completion.call_args.kwargs["system"])

    def test_the_reply_stores_what_a_shared_reader_is_checked_against(self, completion):
        upsell = self.opportunity("Upsell")
        emea = self.account("EMEA")
        seats = self.opportunity("EMEA seats", account=emea)

        self.send(listing())

        reply = Message.objects.get(role="assistant")
        self.assertEqual(reply.grounded_customer_ids, [self.pizza.pk])
        self.assertFalse(reply.carries_anomaly_text)
        self.assertEqual(reply.grounded_pipeline, {"account_ids": [emea.pk], "departments": ["cs"]})
        self.assertEqual(reply.grounded_tickets, {"account_ids": [], "departments": []})
        self.assertEqual(
            reply.grounded_records,
            union_records(
                [
                    record_ref("opportunity", upsell.pk, customer_id=self.pizza.pk),
                    record_ref("opportunity", seats.pk, customer_id=None, account_id=emea.pk),
                ]
            ),
        )

    def test_what_the_asker_cannot_open_is_refused_before_the_model_is_called(self, completion):
        danas = self.account("Dana's", customers=[self.taco], owner=self.other)
        sales = self.opportunity("Sales'", department=User.Function.SALES)
        organisation = {"filters": {"organisation": [NOT_OPEN_ORGANISATION]}}
        account = {"filters": {"account": [NOT_OPEN_ACCOUNT]}}
        item = {"focus": [NOT_OPEN_ITEM]}
        cases = (
            (listing(organisation=str(self.taco.pk)), organisation),
            (listing(organisation="999999"), organisation),
            (listing(account=str(danas.pk)), account),
            (listing(account="999999"), account),
            (listing(focus={"kind": "opportunity", "id": sales.pk}), item),
            (listing(focus={"kind": "opportunity", "id": 999999}), item),
        )
        for context, errors in cases:
            with self.subTest(errors=errors, context=context):
                response = self.send(context)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data, {"context": errors})
        completion.assert_not_called()
        self.assertFalse(Message.objects.exists())
        self.assertFalse(Conversation.objects.exists())
        self.assertFalse(ModelCall.objects.exists())


class SharedReaderTests(PipelinesSendFixture):
    """A reply is shown to a mentioned reader only when every organisation,
    account, department and quoted item it was built from is theirs to read."""

    def setUp(self):
        super().setUp()
        self.viewer, self.seen, self.hidden = blind_to_one_account(self.pizza)
        self.pizza.refresh_from_db()
        self.on_seen = self.opportunity("On seen", account=self.seen)
        self.on_hidden = self.opportunity("On hidden", account=self.hidden, mrr=Decimal("9000"))
        self.conversation = Conversation.objects.create(
            organisation=self.org, user=self.admin, title="Ask Revenact"
        )

    def ask(self, context, *, author=None, question="What is going on?"):
        """Validate, ground and snapshot as SendMessageView does, then check
        that the asker reads the reply."""
        from services.copilot.ask import SURFACES, AskContextSerializer

        author = author or self.admin
        checked = AskContextSerializer(data=context, context={"user": author})
        self.assertTrue(checked.is_valid(), checked.errors)
        ask = checked.validated_data
        grounding = SURFACES["pipelines"].ground(author, ask, question)
        asked = Message.objects.create(
            conversation=self.conversation,
            role="user",
            content=question,
            author=author,
            context=ask,
        )
        grounded, carries = ask_snapshot(author, ask, grounding, [])
        reply = Message.objects.create(
            conversation=self.conversation,
            role="assistant",
            content="Here is what is going on.",
            sources=grounding.sources,
            grounded_customer_ids=grounded,
            carries_anomaly_text=carries,
            grounded_pipeline=pipeline_snapshot(ask, grounding, []),
            grounded_tickets=tickets_snapshot(ask, grounding, []),
            grounded_records=records_snapshot(ask, grounding, []),
            reply_to=asked,
        )
        self.assertTrue(_reply_readable_by(reply, author, asked))
        return asked, reply

    def readable(self, pair, user=None):
        asked, reply = pair
        return _reply_readable_by(reply, user or self.viewer, asked)

    def test_a_book_counting_an_item_the_reader_cannot_open_is_withheld(self):
        self.assertFalse(self.readable(self.ask(listing())))

    def test_a_book_of_only_what_the_reader_opens_is_shown(self):
        self.assertTrue(self.readable(self.ask(listing(account=str(self.seen.pk)))))

    def test_an_organisation_item_the_reader_opens_is_shown(self):
        on_pizza = self.opportunity("On Pizza Hut")

        pair = self.ask(listing(ids=str(on_pizza.pk)))

        self.assertEqual(pair[1].grounded_customer_ids, [self.pizza.pk])
        self.assertTrue(self.readable(pair))

    def test_a_closed_item_counted_but_not_quoted_still_withholds_it(self):
        Opportunity.objects.filter(pk=self.on_hidden.pk).delete()
        lost = self.opportunity("Lost too", account=self.hidden, stage="closed_lost")

        pair = self.ask(listing())

        refs = {(ref["type"], ref["id"]) for ref in pair[1].grounded_records}
        self.assertNotIn(("opportunity", lost.pk), refs)
        self.assertIn(self.hidden.pk, pair[1].grounded_pipeline["account_ids"])
        self.assertFalse(self.readable(pair))

    def test_a_filter_naming_an_organisation_the_reader_cannot_open_is_withheld(self):
        pair = self.ask(listing(organisation=str(self.taco.pk)))

        self.assertEqual(pair[1].grounded_customer_ids, [self.taco.pk])
        self.assertFalse(self.readable(pair))

    def test_a_filter_naming_an_account_the_reader_cannot_open_is_withheld(self):
        pair = self.ask(listing(account=str(self.hidden.pk), priority="low"))

        self.assertEqual(pair[1].grounded_records, [account_ref(self.hidden.pk)])
        self.assertFalse(self.readable(pair))

    def test_a_counted_item_of_another_department_is_withheld(self):
        # Closed Lost on the List: counted by the tiles but never quoted, so
        # only the department snapshot (`pipeline_readable_by`) can withhold it.
        sales = self.opportunity(
            "Sales on seen",
            account=self.seen,
            department=User.Function.SALES,
            stage="closed_lost",
        )

        pair = self.ask(listing(account=str(self.seen.pk)))

        refs = {(ref["type"], ref["id"]) for ref in pair[1].grounded_records}
        self.assertNotIn(("opportunity", sales.pk), refs)
        self.assertEqual(pair[1].grounded_pipeline["departments"], ["cs", "sales"])
        self.assertFalse(self.readable(pair))

    def test_a_focus_the_reader_cannot_open_is_withheld(self):
        pair = self.ask(
            listing(
                account=str(self.seen.pk),
                focus={"kind": "opportunity", "id": self.on_hidden.pk},
            )
        )

        self.assertFalse(self.readable(pair))

    def test_risks_are_checked_the_same_way(self):
        self.risk("Risk on hidden", account=self.hidden)
        self.risk("Risk on seen", account=self.seen)

        self.assertFalse(self.readable(self.ask(listing("risks"))))
        self.assertTrue(self.readable(self.ask(listing("risks", account=str(self.seen.pk)))))

    # The record rules: a quoted item is re-read under the book's two rules.

    def quoted_on_seen(self, model):
        """A reply quoting one item on Seen, read by the viewer; returns the
        pair and the item."""
        kind = "opportunities" if model is Opportunity else "risks"
        item = (
            self.on_seen
            if model is Opportunity
            else self.risk("Risk on seen", account=self.seen, due_by=self.days(-2))
        )
        pair = self.ask(listing(kind, account=str(self.seen.pk)))
        refs = {(ref["type"], ref["id"]) for ref in pair[1].grounded_records}
        self.assertIn((model.__name__.lower(), item.pk), refs)
        self.assertTrue(self.readable(pair))
        return pair, item

    def test_a_quoted_item_moved_to_another_department_after_the_reply_is_withheld(self):
        for model in (Opportunity, Risk):
            with self.subTest(model=model.__name__):
                pair, item = self.quoted_on_seen(model)

                model.objects.filter(pk=item.pk).update(department=User.Function.SALES)

                self.assertFalse(self.readable(pair))

    def test_a_quoted_item_moved_to_an_account_the_reader_cannot_open_is_withheld(self):
        for model in (Opportunity, Risk):
            with self.subTest(model=model.__name__):
                pair, item = self.quoted_on_seen(model)

                model.objects.filter(pk=item.pk).update(account=self.hidden)

                self.assertFalse(self.readable(pair))

    def test_a_quoted_item_deleted_after_the_reply_reads_as_one_the_reader_cannot_open(self):
        for model in (Opportunity, Risk):
            with self.subTest(model=model.__name__):
                pair, item = self.quoted_on_seen(model)

                model.objects.filter(pk=item.pk).delete()

                self.assertFalse(self.readable(pair))

    # Fail closed.

    def test_a_pipelines_reply_with_any_snapshot_missing_is_withheld(self):
        # Every snapshot `_reply_readable_by` reads: the organisations
        # (`_grounded_ids`), the anomaly flag (a missing one reads as "could
        # carry one" for a reader who does not see everything), the quoted
        # records, the counted pipeline and the counted tickets.
        fields = (
            "grounded_customer_ids",
            "carries_anomaly_text",
            "grounded_records",
            "grounded_pipeline",
            "grounded_tickets",
        )
        for kind in ("opportunities", "risks"):
            asked, reply = self.ask(listing(kind, account=str(self.seen.pk)))
            stored = {field: getattr(reply, field) for field in fields}
            self.assertTrue(self.readable((asked, reply)))
            for field in fields:
                with self.subTest(kind=kind, field=field):
                    Message.objects.filter(pk=reply.pk).update(**{field: None})
                    reply.refresh_from_db()
                    self.assertFalse(self.readable((asked, reply)))
                    Message.objects.filter(pk=reply.pk).update(**stored)
                    reply.refresh_from_db()

    def test_a_malformed_or_unknown_snapshot_is_withheld(self):
        asked, reply = self.ask(listing(account=str(self.seen.pk)))
        for field, value in (
            ("grounded_records", [{"type": "opportunity"}]),
            ("grounded_records", UNKNOWN),
            ("grounded_records", "garbage"),
            ("grounded_pipeline", {"account_ids": "x", "departments": []}),
            ("grounded_pipeline", UNKNOWN),
            ("grounded_tickets", {"account_ids": "x", "departments": []}),
            ("grounded_tickets", UNKNOWN),
        ):
            with self.subTest(field=field, value=value):
                stored = getattr(reply, field)
                Message.objects.filter(pk=reply.pk).update(**{field: value})
                reply.refresh_from_db()
                self.assertFalse(self.readable((asked, reply)))
                Message.objects.filter(pk=reply.pk).update(**{field: stored})
                reply.refresh_from_db()

    def test_another_tenants_member_never_reads_it(self):
        stranger = User.objects.create_user(
            email="gus@globex.io",
            password="supersecret1",
            name="Gus",
            organisation=self.other_org,
            role=User.Role.ADMIN,
        )

        pair = self.ask(listing(account=str(self.seen.pk)))

        self.assertFalse(self.readable(pair, stranger))


@patch("services.copilot.views.get_completion", return_value="Here is what is going on.")
class SharedReadQueryCountTests(PipelinesSendFixture):
    """A mentioned-only reader's GET of a conversation holding Pipelines
    replies costs a fixed number of queries, not one more per quoted
    opportunity or risk — the same discipline as test_ask_followups'
    SliceReadQueryCountTests, pinned with an equality check as
    AccountQueryCountTests does. Every item here sits on Seen, which the
    viewer (`blind_to_one_account`) may open, so `_reply_readable_by`
    walks the full per-quoted-item path instead of short-circuiting."""

    #: Measured with CaptureQueriesContext on this fixture: flat at 22,
    #: whether the two replies quote 30 records (10 opportunities + 10
    #: risks per kind) or 75 (40 per kind). A change is a regression to
    #: explain, not absorb.
    PINNED_QUERIES = 22

    def setUp(self):
        super().setUp()
        self.viewer, self.seen, self.hidden = blind_to_one_account(self.pizza)
        self.pizza.refresh_from_db()

    def fill(self, n, *, start=0):
        for i in range(start, start + n):
            self.opportunity(
                f"Opp {i}",
                account=self.seen,
                mrr=Decimal(1000 + i),
                expected_close=self.days(-(i + 1)),
            )
            self.risk(
                f"Risk {i}",
                account=self.seen,
                mrr=Decimal(1000 + i),
                due_by=self.days(-(i + 1)),
            )

    def ask_and_read(self, n, *, start=0):
        self.fill(n, start=start)
        first = self.send(listing("opportunities"), "@Viewer how is this?", user=self.admin)
        self.assertEqual(first.status_code, 200, first.data)
        conversation_id = first.data["id"]
        second = self.send(
            listing("risks"),
            "@Viewer and the risks?",
            user=self.admin,
            conversation_id=conversation_id,
        )
        self.assertEqual(second.status_code, 200, second.data)

        quoted = sum(
            len(reply.grounded_records)
            for reply in Message.objects.filter(conversation_id=conversation_id, role="assistant")
        )

        # A fresh instance: nothing from a previous read memoised on it.
        viewer = User.objects.get(pk=self.viewer.pk)
        api = APIClient()
        api.force_authenticate(viewer)
        with CaptureQueriesContext(connection) as queries:
            response = api.get(f"/api/v1/copilot/conversations/{conversation_id}/")
        self.assertEqual(response.status_code, 200, response.data)
        replies = [m for m in response.data["messages"] if m["role"] == "assistant"]
        self.assertEqual(len(replies), 2)
        for reply in replies:
            self.assertNotEqual(reply["content"], REDACTED_REPLY)

        return len(queries), quoted

    def test_a_mentioned_reader_pays_a_flat_cost_as_quoted_items_grow(self, completion):
        small_queries, small_quoted = self.ask_and_read(10)
        Conversation.objects.all().delete()
        large_queries, large_quoted = self.ask_and_read(40, start=10)

        # The growth is real — not an accident of caching everything away —
        # before the flat-cost claim about it means anything.
        self.assertGreater(large_quoted, small_quoted)
        self.assertEqual(small_queries, large_queries)
        self.assertEqual(small_queries, self.PINNED_QUERIES)
