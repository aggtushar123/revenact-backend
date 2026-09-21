"""One credit per model call, taken before the call and returned if it fails.
Enforced at the single chokepoint every model call goes through."""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings

from services.accounts.models import Organisation, User
from services.billing import accounts, ledger
from services.copilot import anthropic_client
from services.copilot.models import ModelCall


class FakeMessages:
    def __init__(self, fail=False):
        self.fail = fail

    def create(self, **kwargs):
        if self.fail:
            raise RuntimeError("boom")
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="hello")],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )


class CreditTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme")
        self.user = User.objects.create_user(
            email="a@acme.io", password="x", name="A", organisation=self.org
        )
        self.account = accounts.ensure(self.org)

    def _complete(self, fail=False):
        client = SimpleNamespace(messages=FakeMessages(fail=fail))
        with patch.object(
            anthropic_client, "_build_client_and_model", return_value=(client, "model-x")
        ):
            return anthropic_client.get_completion(
                "sys", [{"role": "user", "content": "hi"}], organisation=self.org, user=self.user
            )

    def test_a_successful_call_costs_one_credit(self):
        self._complete()
        self.assertEqual(ledger.balance(self.account), 199)
        row = self.account.ledger.first()
        self.assertEqual(row.kind, "consume")
        self.assertEqual(row.actor, self.user)

    def test_a_failed_call_costs_nothing(self):
        with self.assertRaises(anthropic_client.CopilotRequestFailed):
            self._complete(fail=True)
        self.assertEqual(ledger.balance(self.account), 200)
        kinds = list(self.account.ledger.order_by("id").values_list("kind", flat=True))
        self.assertEqual(
            kinds, ["grant", "consume", "refund"], "charged, then given back; nothing hidden"
        )

    def test_no_credits_means_no_call(self):
        ledger.expire_all(self.account, reason="trial over")
        with self.assertRaises(anthropic_client.BudgetExceeded) as caught:
            self._complete()
        self.assertIn("no AI credits", str(caught.exception))
        self.assertEqual(
            ModelCall.objects.filter(organisation=self.org, outcome="over_budget").count(), 1
        )

    @override_settings(BILLING_ENFORCED=False)
    def test_enforcement_off_still_records_when_affordable_and_never_refuses(self):
        ledger.expire_all(self.account, reason="trial over")
        self.assertEqual(self._complete(), "hello")
        self.assertEqual(ledger.balance(self.account), 0)

    def test_a_call_with_no_organisation_is_not_charged(self):
        client = SimpleNamespace(messages=FakeMessages())
        with patch.object(
            anthropic_client, "_build_client_and_model", return_value=(client, "model-x")
        ):
            anthropic_client.get_completion("sys", [{"role": "user", "content": "hi"}])
        self.assertEqual(ledger.balance(self.account), 200)
