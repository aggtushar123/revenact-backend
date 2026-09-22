"""Replying to a queue email from the person's own mailbox."""

from unittest.mock import patch

from services.customers.models import Email
from services.mail.tests.test_mail import NOW, FakeProvider, Fixture


def url(email):
    return f"/api/v1/communications/emails/{email.id}/reply/"


class QueueReply(Fixture):
    def setUp(self):
        super().setUp()
        self.connection = self.connect(self.dana)
        self.inbound = Email.objects.create(
            customer=self.pizza,
            subject="Renewal terms",
            sender_name="Sam Pizza",
            recipient_name="Dana",
            body="Can we get the multi-year option?",
            sent_at=NOW,
            mailbox=self.connection,
            mailbox_owner=self.dana,
            direction=Email.Direction.RECEIVED,
            from_address="sam@pizzahut.com",
            thread_id="t1",
            provider_message_id="m1",
        )
        self.client.force_authenticate(self.dana)

    def test_reply_sends_from_my_mailbox_and_files_on_the_customer(self):
        with patch("services.mail.sync.get_provider", return_value=FakeProvider()):
            response = self.client.post(
                url(self.inbound), {"body": "Yes, on Thursday."}, format="json"
            )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            FakeProvider.sent[-1][1:],
            (["sam@pizzahut.com"], "Re: Renewal terms", "Yes, on Thursday."),
        )
        sent = Email.objects.get(direction=Email.Direction.SENT)
        self.assertEqual(
            (sent.customer, sent.mailbox_owner, sent.thread_id), (self.pizza, self.dana, "t-sent")
        )
        self.assertEqual(response.data["id"], sent.id)
        self.assertEqual(response.data["direction"], "sent")

    def test_without_a_mailbox_it_is_409(self):
        self.client.force_authenticate(self.carl)  # Dana's manager: may read it, has no mailbox
        response = self.client.post(url(self.inbound), {"body": "Hi"}, format="json")
        self.assertEqual(response.status_code, 409)

    def test_an_email_outside_the_chain_is_404(self):
        self.client.force_authenticate(self.priya)
        self.assertEqual(
            self.client.post(url(self.inbound), {"body": "Hi"}, format="json").status_code, 404
        )

    def test_a_sent_email_has_nobody_to_reply_to(self):
        sent = Email.objects.create(
            customer=self.pizza,
            subject="Terms",
            sender_name="Dana",
            recipient_name="Sam",
            body="Attached.",
            sent_at=NOW,
            mailbox=self.connection,
            mailbox_owner=self.dana,
            direction=Email.Direction.SENT,
            from_address="dana@acme.io",
            to_addresses=["sam@pizzahut.com"],
        )
        self.assertEqual(
            self.client.post(url(sent), {"body": "Hi"}, format="json").status_code, 400
        )
