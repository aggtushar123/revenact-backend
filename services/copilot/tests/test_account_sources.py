"""A Copilot reply citing an account-level record is shown to a viewer who
sees only a slice only when that *account* is one they may open — seeing
the account's organisation is not enough (the twice-filter)."""

from django.test import TestCase

from services.accounts.models import Organisation, User
from services.copilot.models import Message as Turn
from services.copilot.views import _reply_readable_by
from services.customers.models import Customer
from services.customers.tests.test_views import blind_to_one_account


def cites(account):
    return Turn(
        sources=[
            {
                "type": "activity",
                "id": 1,
                "company_type": "account",
                "company_id": account.id,
            }
        ]
    )


class AccountSourceReadabilityTests(TestCase):
    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        customer = Customer.objects.create(organisation=org, name="Globex")
        self.viewer, self.seen, self.hidden = blind_to_one_account(customer)
        self.admin = User.objects.create_user(
            email="admin@acme.io",
            password="supersecret1",
            name="Admin",
            organisation=org,
            role=User.Role.ADMIN,
        )

    def test_a_reply_citing_a_hidden_account_is_not_shown(self):
        self.assertFalse(_reply_readable_by(cites(self.hidden), self.viewer))

    def test_a_reply_citing_a_seen_account_is_shown(self):
        self.assertTrue(_reply_readable_by(cites(self.seen), self.viewer))

    def test_a_viewer_who_sees_everything_reads_both(self):
        self.assertTrue(_reply_readable_by(cites(self.hidden), self.admin))
        self.assertTrue(_reply_readable_by(cites(self.seen), self.admin))
