"""POST /api/v1/copilot/messages/ from Contacts. The model call is stubbed;
the tests read the prompt it was given, what was stored, and what a
mentioned reader of a shared conversation is shown."""

from unittest.mock import patch

from rest_framework.test import APIClient

from services.accounts.models import User
from services.copilot.contacts_context import NOT_A_PERSON, NOT_OPEN_ACCOUNT
from services.copilot.models import Conversation, Message, ModelCall
from services.copilot.views import _reply_readable_by

from .test_contacts_grounding import PersonFixture

URL = "/api/v1/copilot/messages/"


def person(contact, focus=None):
    return {"surface": "contacts", "view": "person", "contact": contact.pk, "focus": focus}


def listing(**filters):
    return {"surface": "contacts", "view": "list", "filters": filters}


@patch("services.copilot.views.get_completion", return_value="Sam sounds unhappy.")
class ContactsSendTests(PersonFixture):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.viewer)

    def send(self, context, content="How is Sam?", **extra):
        return self.api.post(URL, {"content": content, "context": context, **extra}, format="json")

    def test_a_person_answer_is_grounded_on_them_and_metered_as_contacts(self, completion):
        self.call("Kickoff", sentiment="negative")

        response = self.send(person(self.sam, focus="sentiment"))

        self.assertEqual(response.status_code, 200, response.data)
        kwargs = completion.call_args.kwargs
        self.assertEqual(kwargs["purpose"], "contacts")
        self.assertIn("Contacts data:\n<dashboard_data>", kwargs["system"])
        self.assertIn("Screen: Contacts › Sam Pizza · Pizza Hut", kwargs["system"])
        self.assertIn("Kickoff", kwargs["system"])

    def test_the_context_is_stored_with_the_servers_label_and_becomes_the_origin(self, completion):
        data = self.send({**person(self.sam, focus="sentiment"), "label": "Spoofed"}).data

        origin = {
            "surface": "contacts",
            "view": "person",
            "contact": self.sam.pk,
            "label": "Sam Pizza · Pizza Hut",
        }
        self.assertEqual(data["origin"], origin)
        self.assertEqual(data["messages"][0]["context"], {**origin, "focus": "sentiment"})

    def test_a_list_answer_stores_the_pages_filters(self, completion):
        data = self.send(listing(sentiment="negative"), content="Who is unhappy?").data

        self.assertEqual(
            data["origin"],
            {
                "surface": "contacts",
                "view": "list",
                "filters": {"sentiment": "negative"},
                "label": "Contacts · Negative",
            },
        )
        self.assertIn("Sam Pizza", completion.call_args.kwargs["system"])

    def test_the_reply_stores_what_a_shared_reader_is_checked_against(self, completion):
        call = self.call("Seen call", account=self.seen)

        self.send(person(self.sam))

        reply = Message.objects.get(role="assistant")
        self.assertEqual(reply.grounded_customer_ids, [self.pizza.pk])
        self.assertFalse(reply.carries_anomaly_text)
        self.assertEqual(reply.grounded_tickets, {"account_ids": [], "departments": []})
        self.assertIn(call.pk, [r["id"] for r in reply.grounded_records if r["type"] == "call"])

    def test_what_the_asker_cannot_open_is_refused_before_the_model_is_called(self, completion):
        for context, errors in (
            (person(self.hal), {"contact": [NOT_A_PERSON]}),
            ({**person(self.sam), "contact": 999999}, {"contact": [NOT_A_PERSON]}),
            (listing(account=str(self.hidden.pk)), {"filters": {"account": [NOT_OPEN_ACCOUNT]}}),
        ):
            with self.subTest(errors=errors):
                response = self.send(context)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data, {"context": errors})
        completion.assert_not_called()
        self.assertFalse(Message.objects.exists())
        self.assertFalse(Conversation.objects.exists())
        self.assertFalse(ModelCall.objects.exists())


@patch("services.copilot.views.get_completion", return_value="Answer.")
class SharedReaderTests(PersonFixture):
    """A reply is shown to a mentioned reader only if every organisation,
    account and record it was built from is theirs to read. An admin in
    Leadership (who sees everything) asks; the viewer, who cannot open the
    Hidden account or the colleague's mail, is the reader. The check is the
    one the conversation read uses (`views._reply_readable_by`), as in
    test_grounded_records.py."""

    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user(
            email="admin@acme.io",
            password="supersecret1",
            name="Admin",
            organisation=self.org,
            role=User.Role.ADMIN,
            function=User.Function.LEADERSHIP,
        )
        # Mail visibility is chain-based (services.mail.visibility), not the
        # account-level "sees everything" a Leadership function otherwise
        # gets: the asker must manage the mailbox owner to have the mail
        # quoted in their own grounding at all, or the mail-withheld test
        # below would find nothing on the reply to withhold in the first
        # place. The viewer, with nobody reporting to them, still can't
        # read the colleague's mail either way.
        self.colleague.reports_to = self.admin
        self.colleague.save(update_fields=["reports_to"])

    def viewer_reads(self, context):
        api = APIClient()
        api.force_authenticate(self.admin)
        response = api.post(URL, {"content": "How is Sam?", "context": context}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        reply = Message.objects.filter(role="assistant").order_by("id").last()
        self.assertTrue(_reply_readable_by(reply, self.admin, reply.reply_to))
        return _reply_readable_by(reply, self.viewer, reply.reply_to)

    def test_a_reply_quoting_a_call_on_an_account_the_reader_cannot_open_is_withheld(self, _):
        self.call("Hidden call", account=self.hidden)
        self.assertFalse(self.viewer_reads(person(self.sam)))

    def test_a_reply_quoting_mail_the_reader_cannot_read_is_withheld(self, _):
        self.email("Colleague's mail", mailbox_owner=self.colleague)
        self.assertFalse(self.viewer_reads(person(self.sam)))

    def test_a_reply_counting_tickets_of_another_department_is_withheld(self, _):
        self.ticket("ZD-9", department=User.Function.ENGINEERING)
        self.assertFalse(self.viewer_reads(person(self.sam)))

    def test_a_reply_about_a_person_on_a_hidden_account_is_withheld(self, _):
        self.assertFalse(self.viewer_reads(person(self.hal)))

    def test_a_list_reply_naming_a_hidden_accounts_person_is_withheld(self, _):
        self.assertFalse(self.viewer_reads(listing()))  # the admin's list includes Hal

    def test_a_reply_built_only_from_what_the_reader_sees_is_shown(self, _):
        self.call("Org call")
        self.call("Seen call", account=self.seen)
        self.email("Mine", mailbox_owner=self.viewer)
        self.ticket("ZD-2", department=User.Function.CS)
        self.assertTrue(self.viewer_reads(person(self.sam)))
