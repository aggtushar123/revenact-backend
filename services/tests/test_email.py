"""Unit tier: exercises send_password_reset_email directly, no HTTP layer.
accounts/tests/test_views.py's ForgotPasswordTests already cover the full
request/response flow through the API — these just pin down the function's
own contract in isolation."""

from django.core import mail
from django.test import TestCase

from accounts.models import Organisation, User
from services.email import send_password_reset_email


class SendPasswordResetEmailTests(TestCase):
    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io",
            password="oldpassword1",
            name="Alice Admin",
            organisation=org,
            role=User.Role.ADMIN,
        )

    def test_sends_one_email_to_the_user_with_a_working_reset_link(self):
        send_password_reset_email(self.user)

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ["alice@acme.io"])
        self.assertIn("reset-password?uid=", sent.body)
        self.assertIn("&token=", sent.body)
        self.assertIn(self.user.name, sent.body)
