"""A filed email on an account is readable only when that account is one
the reader may open — seeing its organisation is not enough (the
twice-filter). Covers the two single-email fetches that started from the
tenant rather than from visibility: drafting a reply with the Copilot, and
replying from the Communications queue."""

from unittest.mock import patch

from django.utils import timezone
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers.models import Customer, Email
from services.customers.tests.test_views import blind_to_one_account


class FiledEmailOnAHiddenAccount(APITestCase):
    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc", ai_agent_enabled=True)
        customer = Customer.objects.create(organisation=org, name="Globex")
        self.viewer, self.seen, self.hidden = blind_to_one_account(customer)
        self.admin = User.objects.create_user(
            email="admin@acme.io",
            password="supersecret1",
            name="Admin",
            organisation=org,
            role=User.Role.ADMIN,
        )

    def email(self, account):
        # No mailbox owner: the mail rule lets everyone read it, so only the
        # account's own visibility stands between it and the reader.
        return Email.objects.create(
            account=account,
            subject="Renewal terms",
            sender_name="Sam",
            body="Can we get the multi-year option?",
            sent_at=timezone.now(),
            direction=Email.Direction.RECEIVED,
            from_address="sam@globex.com",
        )

    @patch("services.copilot.views.get_completion", return_value="Hi Sam")
    def test_the_copilot_does_not_draft_a_reply_to_a_hidden_accounts_email(self, _completion):
        url = "/api/v1/copilot/draft-reply/"
        hidden, seen = self.email(self.hidden), self.email(self.seen)

        self.client.force_authenticate(self.viewer)
        self.assertEqual(
            self.client.post(url, {"kind": "email", "id": hidden.id}, format="json").status_code,
            404,
        )
        self.assertEqual(
            self.client.post(url, {"kind": "email", "id": seen.id}, format="json").status_code,
            200,
        )
        self.client.force_authenticate(self.admin)
        self.assertEqual(
            self.client.post(url, {"kind": "email", "id": hidden.id}, format="json").status_code,
            200,
        )

    def test_the_queue_does_not_reply_to_a_hidden_accounts_email(self):
        hidden, seen = self.email(self.hidden), self.email(self.seen)
        self.client.force_authenticate(self.viewer)

        def reply(email):
            return self.client.post(
                f"/api/v1/communications/emails/{email.id}/reply/",
                {"body": "Yes."},
                format="json",
            ).status_code

        self.assertEqual(reply(hidden), 404)
        self.assertEqual(reply(seen), 409)  # readable; the viewer has no mailbox
