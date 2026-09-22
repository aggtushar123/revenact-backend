"""Drafting a reply with the Copilot: written as the person, grounded in
the thread and the account's history, and cited."""

from unittest.mock import patch

from services.copilot.anthropic_client import BudgetExceeded
from services.customers.models import Email, Note
from services.mail import sync
from services.mail.tests.test_mail import NOW, FakeProvider, Fixture, message

URL = "/api/v1/copilot/draft-reply/"


class DraftReply(Fixture):
    def setUp(self):
        super().setUp()
        self.connection = self.connect(self.dana)
        self.inbound = Email.objects.create(
            customer=self.pizza,
            subject="Renewal terms",
            sender_name="Sam Pizza",
            recipient_name="Dana",
            body="Can we get the multi-year option before the 28th?",
            sent_at=NOW,
            mailbox=self.connection,
            mailbox_owner=self.dana,
            direction=Email.Direction.RECEIVED,
            from_address="sam@pizzahut.com",
            thread_id="t1",
            provider_message_id="m1",
        )
        Note.objects.create(
            customer=self.pizza,
            author=self.dana,
            title="Billing preference",
            logged_at=NOW,
            body="Priya said they prefer annual billing with a multi-year discount.",
        )
        self.client.force_authenticate(self.dana)

    @patch(
        "services.copilot.views.get_completion",
        return_value="Hi Sam,\n\nYes, the multi-year option is available.",
    )
    def test_drafts_from_the_thread_and_cites_the_accounts_history(self, completion):
        response = self.client.post(URL, {"kind": "email", "id": self.inbound.id}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            response.data["draft"], "Hi Sam,\n\nYes, the multi-year option is available."
        )
        self.assertTrue(response.data["sources"])
        self.assertTrue(all(s["company"] == "Pizza Hut" for s in response.data["sources"]))
        self.assertIn("Billing preference", {s["label"] for s in response.data["sources"]})
        system = completion.call_args.kwargs["system"]
        self.assertIn("Renewal terms", system)
        self.assertIn("multi-year option before the 28th", system)
        self.assertIn("Dana", system)
        self.assertEqual(completion.call_args.kwargs["purpose"], "draft_reply")

    @patch(
        "services.copilot.views.get_completion", return_value="Hi Jean-Pierre, refreshing helps."
    )
    def test_a_mailbox_message_drafts_for_its_owner_only(self, completion):
        FakeProvider.queued = [
            message(
                provider_id="x1",
                from_address="jp@else.io",
                subject="Blank screen",
                labels=["inbox"],
            )
        ]
        with patch("services.mail.sync.get_provider", return_value=FakeProvider()):
            sync.sync_mailbox(self.connection)
        row = self.connection.messages.get()
        response = self.client.post(URL, {"kind": "mail_message", "id": row.id}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["sources"], [])  # nobody in the book: nothing to cite
        self.assertIn("Blank screen", completion.call_args.kwargs["system"])
        self.client.force_authenticate(self.carl)  # Dana's manager reads filed mail, not her inbox
        self.assertEqual(
            self.client.post(
                URL, {"kind": "mail_message", "id": row.id}, format="json"
            ).status_code,
            404,
        )

    def test_an_email_outside_the_chain_is_404(self):
        self.client.force_authenticate(self.priya)
        response = self.client.post(URL, {"kind": "email", "id": self.inbound.id}, format="json")
        self.assertEqual(response.status_code, 404)

    def test_unknown_kind_and_missing_id_are_400(self):
        self.assertEqual(
            self.client.post(URL, {"kind": "ticket", "id": 1}, format="json").status_code, 400
        )
        self.assertEqual(self.client.post(URL, {"kind": "email"}, format="json").status_code, 400)

    @patch(
        "services.copilot.views.get_completion",
        side_effect=BudgetExceeded("Monthly budget reached"),
    )
    def test_budget_exceeded_is_429(self, completion):
        response = self.client.post(URL, {"kind": "email", "id": self.inbound.id}, format="json")
        self.assertEqual(response.status_code, 429)
        self.assertIn("budget", response.data["detail"].lower())
