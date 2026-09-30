"""POST /api/v1/copilot/messages/ from Accounts, and what a mentioned reader
of a shared conversation is shown. The model call is stubbed; the tests read
the prompt it was given, what was stored, and `views._reply_readable_by` —
the check the conversation read runs.

The privacy setup is "blind to one account": Globex is owned by a colleague,
the viewer owns its Seen account and cannot open its Hidden one. Alice (an
admin in Leadership, who sees everything) or the colleague asks; the viewer
reads."""

from datetime import timedelta
from unittest.mock import patch

from rest_framework.test import APIClient

from services.account_story.tests.fixtures import AccountStoryFixture
from services.copilot.accounts_context import NOT_A_STORY_ITEM, NOT_OPEN_ACCOUNT
from services.copilot.grounded_records import account_ref, record_ref
from services.copilot.models import Conversation, Message
from services.copilot.views import (
    UNKNOWN,
    _reply_readable_by,
    ask_snapshot,
    pipeline_snapshot,
    records_snapshot,
    tickets_snapshot,
)
from services.customers.models import Account, Customer, Ticket
from services.customers.tests.test_views import blind_to_one_account

URL = "/api/v1/copilot/messages/"


def _in_order(query, candidates):
    return [(index, 1.0) for index in range(len(candidates))]


def listing(view="list", **filters):
    return {"surface": "accounts", "view": view, "filters": filters}


def detail(account, focus=None):
    return {"surface": "accounts", "view": "detail", "account": account.pk, "focus": focus}


class AccountsSendFixture(AccountStoryFixture):
    def setUp(self):
        super().setUp()
        patcher = patch("services.copilot.retrieval.rank_by_similarity", side_effect=_in_order)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.api = APIClient()
        self.api.force_authenticate(self.csm)

    def send(self, context, content="What is going on?", user=None, **extra):
        api = self.api
        if user is not None:
            api = APIClient()
            api.force_authenticate(user)
        return api.post(URL, {"content": content, "context": context, **extra}, format="json")


@patch("services.copilot.views.get_completion", return_value="EMEA is waiting on terms.")
class AccountsSendTests(AccountsSendFixture):
    def test_an_account_page_answer_is_grounded_on_it_and_metered_as_accounts(self, completion):
        self.note(self.emea, title="Terms pending")

        response = self.send(detail(self.emea))

        self.assertEqual(response.status_code, 200, response.data)
        kwargs = completion.call_args.kwargs
        self.assertEqual(kwargs["purpose"], "accounts")
        self.assertIn("Account page data:\n<dashboard_data>", kwargs["system"])
        self.assertIn("Screen: Accounts › EMEA (one account's page)", kwargs["system"])
        self.assertIn("Terms pending", kwargs["system"])

    def test_the_context_is_stored_with_the_servers_label_and_becomes_the_origin(self, completion):
        note = self.note(self.emea)
        context = {**detail(self.emea, {"kind": "note", "id": note.pk}), "label": "Spoofed"}

        data = self.send(context).data

        origin = {"surface": "accounts", "view": "detail", "account": self.emea.pk, "label": "EMEA"}
        self.assertEqual(data["origin"], origin)
        self.assertEqual(
            data["messages"][0]["context"], {**origin, "focus": {"kind": "note", "id": note.pk}}
        )
        listed = self.api.get("/api/v1/copilot/conversations/").data
        self.assertEqual(listed[0]["origin"], origin)

    def test_a_list_answer_stores_the_pages_filters_and_label(self, completion):
        data = self.send(listing("board", health="average"), content="Who needs me?").data

        self.assertEqual(
            data["origin"],
            {
                "surface": "accounts",
                "view": "board",
                "filters": {"health": "average"},
                "label": "Accounts · Health: Average",
            },
        )
        system = completion.call_args.kwargs["system"]
        self.assertIn("Accounts data:\n<dashboard_data>", system)
        self.assertIn("Screen: Accounts › Board", system)

    def test_the_reply_stores_what_a_shared_reader_is_checked_against(self, completion):
        self.send(detail(self.emea))

        reply = Message.objects.get(role="assistant")
        self.assertEqual(reply.grounded_customer_ids, [self.pizza.pk])
        self.assertFalse(reply.carries_anomaly_text)
        self.assertEqual(reply.grounded_pipeline, {"account_ids": [], "departments": []})
        self.assertEqual(reply.grounded_tickets, {"account_ids": [], "departments": []})
        self.assertEqual(reply.grounded_records, [account_ref(self.emea.pk)])

    def test_a_follow_up_without_context_carries_the_pages_records(self, completion):
        first = self.send(detail(self.emea)).data

        self.api.post(
            URL, {"conversation_id": first["id"], "content": "Summarise that"}, format="json"
        )

        follow_up = Message.objects.filter(role="assistant").order_by("id").last()
        self.assertEqual(follow_up.grounded_records, [account_ref(self.emea.pk)])
        self.assertEqual(follow_up.grounded_customer_ids, [self.pizza.pk])

    def test_what_the_asker_cannot_open_is_refused_before_the_model_is_called(self, completion):
        carls = self.account("Carl's own", owner=self.csm)
        private = self.note(self.emea, author=self.other)
        cases = (
            (detail(carls), self.other, {"account": [NOT_OPEN_ACCOUNT]}),
            ({**detail(self.emea), "account": 999999}, self.csm, {"account": [NOT_OPEN_ACCOUNT]}),
            (
                detail(self.emea, {"kind": "note", "id": private.pk}),
                self.csm,
                {"focus": [NOT_A_STORY_ITEM]},
            ),
            (
                detail(self.emea, {"kind": "note", "id": 999999}),
                self.csm,
                {"focus": [NOT_A_STORY_ITEM]},
            ),
        )
        for context, user, errors in cases:
            with self.subTest(errors=errors, user=user.name):
                response = self.send(context, user=user)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data, {"context": errors})
        completion.assert_not_called()
        self.assertFalse(Message.objects.exists())
        self.assertFalse(Conversation.objects.exists())

    def test_an_item_filed_on_a_sibling_account_is_the_same_400_as_a_missing_one(self, completion):
        apac_note = self.note(self.apac, title="APAC only")

        response = self.send(detail(self.emea, {"kind": "note", "id": apac_note.pk}))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {"context": {"focus": [NOT_A_STORY_ITEM]}})
        completion.assert_not_called()


class SharedReaderTests(AccountsSendFixture):
    """A reply is shown to a mentioned reader only when every organisation,
    account, record and ticket department it was built from is theirs to
    read."""

    def setUp(self):
        super().setUp()
        self.globex = Customer.objects.create(organisation=self.org, name="Globex")
        self.viewer, self.seen, self.hidden = blind_to_one_account(self.globex)
        self.globex.refresh_from_db()
        self.colleague = self.globex.owner
        self.conversation = Conversation.objects.create(
            organisation=self.org, user=self.admin, title="Ask Revenact"
        )

    def ask(self, context, *, author=None, question="What is going on?"):
        """Validate, ground and snapshot as SendMessageView does, then report
        whether the asker and the viewer may read the reply."""
        from services.copilot.ask import SURFACES, AskContextSerializer

        author = author or self.admin
        checked = AskContextSerializer(data=context, context={"user": author})
        self.assertTrue(checked.is_valid(), checked.errors)
        ask = checked.validated_data
        grounding = SURFACES["accounts"].ground(author, ask, question)
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

    @staticmethod
    def no_retrieval_sources():
        """`setUp` patches `rank_by_similarity` to keep every candidate, so
        an unpatched `retrieve_with_sources` would return the withheld
        record as a `source` too — withholding would then pass even if
        `grounded_records` had stopped carrying it. Tests that mean to prove
        `grounded_records` is what closes the reply use this so `sources` is
        empty and cannot be the reason."""
        return patch(
            "services.copilot.account_detail_grounding.retrieve_with_sources", return_value=[]
        )

    # The list and the Board.

    def test_a_list_counting_an_account_the_reader_cannot_open_is_withheld(self):
        # Narrowed to just the seen and hidden accounts, so only the hidden
        # one can be the reason the reply is withheld.
        ids = f"{self.seen.pk},{self.hidden.pk}"
        self.assertFalse(self.readable(self.ask(listing(ids=ids))))

    def test_a_list_of_only_what_the_reader_opens_is_shown(self):
        self.assertTrue(self.readable(self.ask(listing(ids=str(self.seen.pk)))))

    def test_a_list_naming_an_organisation_the_reader_cannot_open_is_withheld(self):
        hooli = Customer.objects.create(organisation=self.org, name="Hooli", owner=self.colleague)
        # A second organisation the viewer does own, so "Shared" itself is
        # one they can open (`accounts__owner`) — the account's own `owner`
        # stays unset, so that visibility does not also open Hooli. Only
        # Hooli, hidden from them, can be why the reply is withheld.
        viewers_org = Customer.objects.create(
            organisation=self.org, name="Viewer Co", owner=self.viewer
        )
        shared = Account.objects.create(name="Shared", renewal_date=self.today + timedelta(days=10))
        shared.customers.add(hooli, viewers_org)

        pair = self.ask(listing(ids=str(shared.pk)))

        self.assertEqual(pair[1].grounded_customer_ids, [hooli.pk])
        self.assertFalse(self.readable(pair))

    def test_a_list_counting_another_departments_urgent_ticket_is_withheld(self):
        self.ticket(self.seen, priority=Ticket.Priority.HIGH, department="engineering")

        pair = self.ask(listing(ids=str(self.seen.pk)))

        self.assertEqual(pair[1].grounded_tickets["departments"], ["engineering"])
        self.assertFalse(self.readable(pair))

    # One account's page.

    def test_a_page_the_reader_cannot_open_is_withheld_and_they_cannot_ask_it(self):
        self.call(self.hidden, title="Hidden QBR")

        self.assertFalse(self.readable(self.ask(detail(self.hidden))))
        response = self.send(detail(self.hidden), user=self.viewer)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {"context": {"account": [NOT_OPEN_ACCOUNT]}})

    def test_a_page_built_only_from_what_the_reader_sees_is_shown(self):
        self.call(self.seen, title="Seen QBR")
        self.ticket(self.seen, priority=Ticket.Priority.HIGH, department="cs")

        self.assertTrue(self.readable(self.ask(detail(self.seen))))

    def test_a_quoted_note_the_reader_may_not_read_withholds_it(self):
        note = self.note(self.seen, title="Colleague's own note", author=self.colleague)

        with self.no_retrieval_sources():
            pair = self.ask(detail(self.seen), author=self.colleague)

        ref = record_ref("note", note.pk, customer_id=None, account_id=self.seen.pk)
        self.assertIn(ref, pair[1].grounded_records)
        self.assertFalse(self.readable(pair))

    def test_a_quoted_task_the_reader_may_not_read_withholds_it(self):
        task = self.task(self.seen, title="Colleague's own task", created_by=self.colleague)

        with self.no_retrieval_sources():
            pair = self.ask(detail(self.seen), author=self.colleague)

        ref = record_ref("task", task.pk, customer_id=None, account_id=self.seen.pk)
        self.assertIn(ref, pair[1].grounded_records)
        self.assertFalse(self.readable(pair))

    def test_quoted_mail_from_a_mailbox_outside_the_readers_chain_withholds_it(self):
        email = self.email(self.seen, subject="Colleague's mailbox", mailbox_owner=self.colleague)

        with self.no_retrieval_sources():
            pair = self.ask(detail(self.seen), author=self.colleague)

        ref = record_ref("email", email.pk, customer_id=None, account_id=self.seen.pk)
        self.assertIn(ref, pair[1].grounded_records)
        self.assertFalse(self.readable(pair))

    def test_a_ticket_of_another_department_withholds_it(self):
        ticket = self.ticket(self.seen, title="Engineering outage", department="engineering")

        with self.no_retrieval_sources():
            pair = self.ask(detail(self.seen))

        ref = record_ref("ticket", ticket.pk, customer_id=None, account_id=self.seen.pk)
        self.assertIn(ref, pair[1].grounded_records)
        self.assertFalse(self.readable(pair))

    def test_a_focus_on_a_record_the_reader_may_not_read_withholds_it(self):
        old = self.note(self.seen, title="Old", day=self.days_ago(90), author=self.colleague)

        with self.no_retrieval_sources():
            pair = self.ask(
                detail(self.seen, {"kind": "note", "id": old.pk}), author=self.colleague
            )

        ref = record_ref("note", old.pk, customer_id=None, account_id=self.seen.pk)
        self.assertIn(ref, pair[1].grounded_records)
        self.assertFalse(self.readable(pair))

    # Fail closed.

    def test_a_reply_missing_or_with_a_malformed_snapshot_field_is_withheld(self):
        asked, reply = self.ask(detail(self.seen))
        full = {
            field: getattr(reply, field)
            for field in (
                "grounded_customer_ids",
                "carries_anomaly_text",
                "grounded_pipeline",
                "grounded_tickets",
                "grounded_records",
            )
        }
        # With every snapshot as written, the mentioned reader reads it.
        self.assertTrue(self.readable((asked, reply)))
        broken = {
            "grounded_customer_ids": (None, "garbage", [True], [str(self.globex.pk)]),
            "carries_anomaly_text": (None,),
            "grounded_pipeline": (None, UNKNOWN, "garbage", {"account_ids": ["1"]}),
            "grounded_tickets": (
                None,
                UNKNOWN,
                "garbage",
                {"account_ids": [self.seen.pk], "departments": [1]},
            ),
            "grounded_records": (None, UNKNOWN, "garbage", [{"type": "note"}]),
        }
        for field, values in broken.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    Message.objects.filter(pk=reply.pk).update(**{**full, field: value})
                    reply.refresh_from_db()
                    self.assertFalse(self.readable((asked, reply)))
        Message.objects.filter(pk=reply.pk).update(**full)
        reply.refresh_from_db()
        self.assertTrue(self.readable((asked, reply)))

    def test_another_tenants_member_never_reads_it(self):
        from services.accounts.models import User

        stranger = User.objects.create_user(
            email="gus@globex.io",
            password="supersecret1",
            name="Gus",
            organisation=self.other_org,
            role=User.Role.ADMIN,
        )
        pair = self.ask(detail(self.seen))

        self.assertFalse(self.readable(pair, stranger))
