"""The records a digest quoted without citing them (`Message.grounded_records`)
are checked like cited sources: a mentioned-only reader needs each record's
company open to them — an account by its own rule — and the record readable
under its own rule. An organisation page reply without them fails closed; a
reply fed one as history carries them on."""

from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from services.accounts.models import Organisation, User
from services.copilot.context import Grounding
from services.copilot.grounded_records import (
    account_ref,
    record_ref,
    union_records,
    well_formed_records,
)
from services.copilot.models import Conversation, Message
from services.copilot.views import (
    NO_PIPELINE,
    UNKNOWN,
    _reply_readable_by,
    records_snapshot,
)
from services.customers.models import Customer, Note, Task
from services.customers.tests.test_views import blind_to_one_account

from .test_organization_detail_grounding import _in_order

URL = "/api/v1/copilot/messages/"


class RecordShapeTests(SimpleTestCase):
    def test_a_reference_names_the_record_and_the_company_it_hangs_off(self):
        self.assertEqual(
            record_ref("note", 4, customer_id=7),
            {"type": "note", "id": 4, "company_type": "customer", "company_id": 7},
        )
        self.assertEqual(
            record_ref("email", 5, customer_id=7, account_id=9),
            {"type": "email", "id": 5, "company_type": "account", "company_id": 9},
        )
        self.assertEqual(
            account_ref(9), {"type": "account", "id": 9, "company_type": "account", "company_id": 9}
        )

    def test_only_a_list_of_whole_references_is_well_formed(self):
        good = [record_ref("note", 4, customer_id=7)]
        self.assertEqual(well_formed_records(good), good)
        self.assertEqual(well_formed_records([]), [])
        for bad in (
            None,
            {"type": "note"},
            [{"type": "note", "id": 4, "company_type": "customer"}],
            [{**good[0], "title": "leaked"}],
            [{**good[0], "company_type": "team"}],
            [{**good[0], "id": "4"}],
            [{**good[0], "id": True}],
            [{**good[0], "company_id": 0}],
            [{**good[0], "type": 3}],
        ):
            with self.subTest(bad=bad):
                self.assertIsNone(well_formed_records(bad))

    def test_the_union_keeps_each_reference_once_in_one_order(self):
        a = [record_ref("note", 4, customer_id=7), account_ref(9)]
        b = [account_ref(9), record_ref("email", 1, customer_id=7)]

        self.assertEqual(
            union_records(a, b),
            [account_ref(9), record_ref("email", 1, customer_id=7), a[0]],
        )


class RecordFixture(TestCase):
    """Alice sees everything and asks on Globex's page; the viewer, mentioned,
    can open Globex and its Seen account but not its Hidden one."""

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.globex = Customer.objects.create(organisation=self.org, name="Globex")
        self.viewer, self.seen, self.hidden = blind_to_one_account(self.globex)
        self.colleague = self.globex.owner
        self.admin = User.objects.create_user(
            email="admin@acme.io",
            password="supersecret1",
            name="Admin",
            organisation=self.org,
            role=User.Role.ADMIN,
            function=User.Function.LEADERSHIP,
        )
        self.conversation = Conversation.objects.create(
            organisation=self.org, user=self.admin, title="Ask Revenact"
        )

    def ask(self, records, *, account=None, context=None, author=None):
        asked = Message.objects.create(
            conversation=self.conversation,
            role="user",
            content="What is going on here?",
            author=author or self.admin,
            context=context
            or {
                "surface": "organizations",
                "view": "detail",
                "organization": self.globex.pk,
                "account": account,
                "label": "Globex",
                "focus": None,
            },
        )
        reply = Message.objects.create(
            conversation=self.conversation,
            role="assistant",
            content="Here is what is going on.",
            grounded_customer_ids=[self.globex.pk],
            carries_anomaly_text=False,
            grounded_pipeline=NO_PIPELINE,
            grounded_tickets=NO_PIPELINE,
            grounded_records=records,
            reply_to=asked,
        )
        return asked, reply

    def readable(self, reply, asked, user=None):
        return _reply_readable_by(reply, user or self.viewer, asked)

    def note(self, parent, *, author=None):
        field = "account" if parent.__class__.__name__ == "Account" else "customer"
        return Note.objects.create(
            title="Champion left",
            author_name="Seed",
            author=author,
            body="Sam moved on.",
            logged_at=timezone.localdate(),
            **{field: parent},
        )


class RecordReadabilityTests(RecordFixture):
    def test_a_reply_that_drew_on_a_hidden_account_is_withheld(self):
        asked, reply = self.ask([account_ref(self.seen.pk), account_ref(self.hidden.pk)])

        self.assertFalse(self.readable(reply, asked))
        self.assertTrue(self.readable(reply, asked, self.admin))

    def test_a_reply_narrowed_to_an_account_the_reader_sees_is_readable(self):
        note = self.note(self.seen)
        asked, reply = self.ask(
            [
                account_ref(self.seen.pk),
                record_ref("note", note.pk, customer_id=self.globex.pk, account_id=self.seen.pk),
            ],
            account=self.seen.pk,
        )

        self.assertTrue(self.readable(reply, asked))

    def test_a_quoted_record_the_reader_may_not_read_withholds_the_reply(self):
        private = self.note(self.seen, author=self.colleague)
        task = Task.objects.create(
            account=self.seen,
            title="Colleague's own",
            assignee_name="Owner",
            assignee=self.colleague,
            due_date=timezone.localdate(),
        )
        for kind, pk in (("note", private.pk), ("task", task.pk)):
            with self.subTest(kind=kind):
                asked, reply = self.ask(
                    [
                        account_ref(self.seen.pk),
                        record_ref(kind, pk, customer_id=self.globex.pk, account_id=self.seen.pk),
                    ],
                    account=self.seen.pk,
                )

                self.assertFalse(self.readable(reply, asked))
                self.assertTrue(self.readable(reply, asked, self.colleague))

    def test_an_organisation_page_reply_with_no_records_fails_closed(self):
        asked, reply = self.ask(None, account=self.seen.pk)

        self.assertFalse(self.readable(reply, asked))
        self.assertTrue(self.readable(reply, asked, self.admin))

    def test_a_malformed_records_list_fails_closed_even_off_the_page(self):
        list_context = {"surface": "organizations", "view": "list", "filters": {}, "labels": []}
        for context in (None, list_context):
            with self.subTest(context=context and context["view"]):
                asked, reply = self.ask([{"type": "note", "id": "x"}], context=context)

                self.assertFalse(self.readable(reply, asked))

    def test_a_list_reply_from_before_the_field_existed_reads_as_before(self):
        list_context = {"surface": "organizations", "view": "list", "filters": {}, "labels": []}
        asked, reply = self.ask(None, context=list_context)

        self.assertTrue(self.readable(reply, asked))

    def test_the_asker_always_reads_their_own_reply(self):
        asked, reply = self.ask([account_ref(self.hidden.pk)], author=self.viewer)

        self.assertTrue(self.readable(reply, asked))

    def test_a_follow_up_fed_the_reply_carries_its_records(self):
        hidden = [account_ref(self.hidden.pk)]
        asked, reply = self.ask(hidden)
        own = Grounding("digest", records=[account_ref(self.seen.pk)])

        self.assertEqual(
            records_snapshot({"surface": "organizations"}, own, [asked, reply]),
            union_records(own.records, hidden),
        )
        self.assertEqual(records_snapshot(None, Grounding(""), [asked, reply]), hidden)

    def test_a_fed_page_reply_with_no_records_leaves_none(self):
        asked, reply = self.ask(None)

        self.assertIsNone(records_snapshot(None, Grounding(""), [asked, reply]))
        self.assertIsNone(
            records_snapshot({"surface": "organizations"}, Grounding("d"), [asked, reply])
        )

    def test_a_context_less_fed_reply_with_no_records_of_its_own_fails_closed(self):
        """An earlier reply asked with no context of its own (a plain
        follow-up on an Ask exchange) that stores no records — written before
        `grounded_records` existed — is unknown, not empty: folding it in
        must not silently drop its unknown records and read as safe."""
        asked, reply = self.ask([account_ref(self.hidden.pk)])
        follow_up = Message.objects.create(
            conversation=self.conversation,
            role="user",
            content="Summarise the above",
            author=self.admin,
        )
        plain_reply = Message.objects.create(
            conversation=self.conversation,
            role="assistant",
            content="In short…",
            carries_anomaly_text=False,
            reply_to=follow_up,
        )

        self.assertIsNone(
            records_snapshot(None, Grounding(""), [asked, reply, follow_up, plain_reply])
        )

    def test_a_context_less_follow_up_with_hidden_records_is_withheld_from_its_own_asker(self):
        _asked, _reply = self.ask([account_ref(self.hidden.pk)])
        follow_up = Message.objects.create(
            conversation=self.conversation,
            role="user",
            content="Summarise the above",
            author=self.viewer,
        )
        reply = Message.objects.create(
            conversation=self.conversation,
            role="assistant",
            content="In short…",
            grounded_customer_ids=[self.globex.pk],
            carries_anomaly_text=False,
            grounded_pipeline=NO_PIPELINE,
            grounded_tickets=NO_PIPELINE,
            grounded_records=[account_ref(self.hidden.pk)],
            reply_to=follow_up,
        )

        self.assertFalse(_reply_readable_by(reply, self.viewer, follow_up))
        self.assertTrue(_reply_readable_by(reply, self.admin, follow_up))


LIST_ASK = {"surface": "organizations", "view": "list", "filters": {}, "labels": [], "focus": None}
REVENUE_ASK = {
    "surface": "dashboard",
    "area": "revenue",
    "filters": {},
    "labels": [],
    "focus": None,
}


@patch("services.copilot.views.get_completion", return_value="Globex is waiting on terms.")
@patch("services.copilot.retrieval.rank_by_similarity", side_effect=_in_order)
class UnknownFoldTests(RecordFixture):
    """A fold that is unknown when a reply is written is stored as a marker
    every well-formed check rejects, never as a null a later reader could
    take for "written before the field existed" — which on a list or
    Dashboard reply reads as empty and would fail open."""

    def follow_up(self, *, author=None, **folds):
        """A reply to a turn with no context of its own, fed an Ask reply."""
        asked = Message.objects.create(
            conversation=self.conversation,
            role="user",
            content="Summarise the above",
            author=author or self.admin,
        )
        reply = Message.objects.create(
            conversation=self.conversation,
            role="assistant",
            content="In short…",
            grounded_customer_ids=[self.globex.pk],
            carries_anomaly_text=False,
            reply_to=asked,
            **folds,
        )
        return asked, reply

    def send(self, context):
        api = APIClient()
        api.force_authenticate(self.admin)
        response = api.post(
            URL,
            {"conversation_id": self.conversation.pk, "content": "And?", "context": context},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        return Message.objects.filter(role="assistant").order_by("id").last()

    def assert_withheld_from_the_viewer(self, reply):
        self.assertFalse(_reply_readable_by(reply, self.viewer, reply.reply_to))
        self.assertTrue(_reply_readable_by(reply, self.admin, reply.reply_to))

    def test_a_legacy_context_less_follow_up_fed_through_a_page_reply_fails_closed(self, *_):
        """F0 (a context-less follow-up from before grounded_records) → a page
        reply D fed F0 → a list reply A fed D: A may repeat D's story."""
        self.follow_up(grounded_pipeline=NO_PIPELINE, grounded_tickets=NO_PIPELINE)
        page_reply = self.send(self.detail_context())
        list_reply = self.send(LIST_ASK)

        for reply in (page_reply, list_reply):
            with self.subTest(reply=reply.pk):
                self.assert_withheld_from_the_viewer(reply)
                self.assertEqual(reply.grounded_records, UNKNOWN)

    def test_a_list_reply_fed_a_page_reply_with_unknown_records_fails_closed(self, *_):
        self.ask(None)  # a page reply whose records were unknown when written

        list_reply = self.send(LIST_ASK)

        self.assert_withheld_from_the_viewer(list_reply)
        self.assertEqual(list_reply.grounded_records, UNKNOWN)

    def test_a_list_reply_fed_a_legacy_revenue_reply_fails_closed_on_pipeline(self, *_):
        """A Revenue reply from before grounded_pipeline existed could carry
        pipeline; a list reply fed it must not read as having counted none."""
        self.ask(None, context=REVENUE_ASK)
        Message.objects.filter(role="assistant").update(
            grounded_pipeline=None, grounded_tickets=None
        )

        list_reply = self.send(LIST_ASK)

        self.assert_withheld_from_the_viewer(list_reply)
        self.assertEqual(list_reply.grounded_pipeline, UNKNOWN)
        self.assertNotEqual(list_reply.grounded_tickets, UNKNOWN)
        self.assertEqual(list_reply.grounded_records, [])

    def test_a_follow_ups_own_asker_still_reads_it_with_an_unknown_fold(self, *_):
        self.ask(None)
        asked, reply = self.follow_up(
            author=self.viewer,
            grounded_pipeline=NO_PIPELINE,
            grounded_tickets=NO_PIPELINE,
            grounded_records=UNKNOWN,
        )

        self.assertTrue(_reply_readable_by(reply, self.viewer, asked))
        self.assertFalse(_reply_readable_by(reply, self.colleague, asked))

    def detail_context(self):
        return {
            "surface": "organizations",
            "view": "detail",
            "organization": self.globex.pk,
            "account": None,
            "label": "Globex",
            "focus": None,
        }
