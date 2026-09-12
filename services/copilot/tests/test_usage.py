"""The audit trail and the budget: every model call logged, none over budget."""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.copilot import usage
from services.copilot.anthropic_client import BudgetExceeded, CopilotNotConfigured, get_completion
from services.copilot.models import ModelBudget, ModelCall


def _fake_client(text="hello", input_tokens=120, output_tokens=30):
    response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: response))
    return client


class AuditTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")
        self.user = User.objects.create_user(
            email="alice@acme.io",
            password="x",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )

    def test_a_successful_call_is_logged_with_its_tokens(self):
        with patch(
            "services.copilot.anthropic_client._build_client_and_model",
            return_value=(_fake_client(), "claude-test"),
        ):
            text = get_completion(
                "sys",
                [{"role": "user", "content": "hi"}],
                purpose="brief",
                organisation=self.org,
                user=self.user,
            )

        self.assertEqual(text, "hello")
        call = ModelCall.objects.get()
        self.assertEqual((call.purpose, call.outcome, call.model), ("brief", "ok", "claude-test"))
        self.assertEqual((call.input_tokens, call.output_tokens), (120, 30))
        self.assertEqual(call.user, self.user)
        self.assertEqual(call.max_tokens, 1024)

    def test_a_failed_call_and_a_missing_provider_are_logged_too(self):
        failing = SimpleNamespace(
            messages=SimpleNamespace(
                create=lambda **kw: (_ for _ in ()).throw(RuntimeError("rate limited"))
            )
        )
        with patch(
            "services.copilot.anthropic_client._build_client_and_model", return_value=(failing, "m")
        ):
            with self.assertRaises(Exception):
                get_completion("sys", [], purpose="copilot", organisation=self.org)
        with patch(
            "services.copilot.anthropic_client._build_client_and_model",
            side_effect=CopilotNotConfigured("no key"),
        ):
            with self.assertRaises(CopilotNotConfigured):
                get_completion("sys", [], purpose="copilot", organisation=self.org)

        outcomes = sorted(ModelCall.objects.values_list("outcome", flat=True))
        self.assertEqual(outcomes, ["failed", "unconfigured"])
        self.assertIn("rate limited", ModelCall.objects.get(outcome="failed").error)

    @override_settings(MODEL_BUDGET_DEFAULT_TOKENS=100)
    def test_a_call_over_budget_is_refused_before_it_is_made(self):
        ModelCall.objects.create(
            organisation=self.org, purpose="brief", input_tokens=90, output_tokens=20, outcome="ok"
        )
        make = _fake_client()
        with patch(
            "services.copilot.anthropic_client._build_client_and_model", return_value=(make, "m")
        ) as build:
            with self.assertRaises(BudgetExceeded):
                get_completion("sys", [], purpose="brief", organisation=self.org)
            build.assert_not_called()

        self.assertEqual(ModelCall.objects.filter(outcome="over_budget").count(), 1)
        # Another purpose has its own budget.
        with patch(
            "services.copilot.anthropic_client._build_client_and_model", return_value=(make, "m")
        ):
            get_completion("sys", [], purpose="copilot", organisation=self.org)
        self.assertEqual(ModelCall.objects.filter(outcome="ok", purpose="copilot").count(), 1)

    @override_settings(MODEL_BUDGET_DEFAULT_TOKENS=100)
    def test_a_custom_budget_overrides_the_default(self):
        ModelBudget.objects.create(organisation=self.org, purpose="brief", monthly_tokens=1_000)
        ModelCall.objects.create(
            organisation=self.org, purpose="brief", input_tokens=500, output_tokens=0, outcome="ok"
        )

        self.assertEqual(usage.remaining_tokens(self.org, "brief"), 500)
        with patch(
            "services.copilot.anthropic_client._build_client_and_model",
            return_value=(_fake_client(), "m"),
        ):
            get_completion("sys", [], purpose="brief", organisation=self.org)

    def test_a_call_with_no_organisation_is_logged_but_never_budgeted(self):
        with patch(
            "services.copilot.anthropic_client._build_client_and_model",
            return_value=(_fake_client(), "m"),
        ):
            get_completion("sys", [])

        call = ModelCall.objects.get()
        self.assertIsNone(call.organisation)
        self.assertEqual(call.purpose, "copilot")

    def test_summary_reports_every_purpose_with_spend_against_budget(self):
        ModelCall.objects.create(
            organisation=self.org,
            purpose="proposals",
            input_tokens=1000,
            output_tokens=200,
            outcome="ok",
        )
        ModelCall.objects.create(
            organisation=self.org, purpose="proposals", outcome="failed", error="x"
        )

        rows = {row["purpose"]: row for row in usage.summary(self.org)["purposes"]}

        self.assertEqual(rows["proposals"]["calls"], 2)
        self.assertEqual(rows["proposals"]["failed"], 1)
        self.assertEqual(rows["proposals"]["spent"], 1200)
        self.assertEqual(rows["proposals"]["remaining"], rows["proposals"]["budget"] - 1200)
        self.assertEqual(rows["copilot"]["calls"], 0)
        self.assertEqual(set(rows), set(usage.PURPOSES))


class UsageAPITests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="x",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="x",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.client.force_authenticate(self.admin)

    def test_a_csm_cannot_read_the_spend_or_set_a_budget(self):
        self.client.force_authenticate(self.csm)
        self.assertEqual(
            self.client.get("/api/v1/copilot/usage/").status_code, status.HTTP_403_FORBIDDEN
        )
        self.assertEqual(
            self.client.patch(
                "/api/v1/copilot/usage/budgets/",
                {"purpose": "brief", "monthly_tokens": 1},
                format="json",
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_the_usage_page_lists_the_month_and_the_recent_calls(self):
        ModelCall.objects.create(
            organisation=self.org,
            user=self.admin,
            purpose="brief",
            input_tokens=10,
            output_tokens=5,
            outcome="ok",
            model="m",
        )
        other = Organisation.objects.create(name="Other Inc", currency="USD")
        ModelCall.objects.create(
            organisation=other, purpose="brief", input_tokens=999, outcome="ok"
        )

        data = self.client.get("/api/v1/copilot/usage/").data

        self.assertEqual(len(data["recent"]), 1)
        self.assertEqual(data["recent"][0]["user"], "Alice")
        self.assertEqual(data["recent"][0]["purpose_label"], "Management brief")
        brief = next(p for p in data["purposes"] if p["purpose"] == "brief")
        self.assertEqual(brief["spent"], 15)
        self.assertIn("default_budget", data)

    def test_setting_and_clearing_a_budget(self):
        response = self.client.patch(
            "/api/v1/copilot/usage/budgets/",
            {"purpose": "proposals", "monthly_tokens": 50_000},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        row = next(p for p in response.data["purposes"] if p["purpose"] == "proposals")
        self.assertEqual((row["budget"], row["custom_budget"]), (50_000, True))

        response = self.client.patch(
            "/api/v1/copilot/usage/budgets/",
            {"purpose": "proposals", "monthly_tokens": None},
            format="json",
        )
        row = next(p for p in response.data["purposes"] if p["purpose"] == "proposals")
        self.assertFalse(row["custom_budget"])
        self.assertEqual(
            self.client.patch(
                "/api/v1/copilot/usage/budgets/",
                {"purpose": "nope", "monthly_tokens": 1},
                format="json",
            ).status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    @override_settings(MODEL_BUDGET_DEFAULT_TOKENS=1)
    def test_a_generation_over_budget_is_a_429(self):
        from services.customers.models import Customer

        Customer.objects.create(organisation=self.org, name="Globex", arr_billed_at_account=1000)
        ModelCall.objects.create(
            organisation=self.org, purpose="brief", input_tokens=5, outcome="ok"
        )

        response = self.client.post("/api/v1/metrics/brief/generate/")

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertIn("budget", response.data["detail"])
