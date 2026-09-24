"""POST /api/v1/copilot/messages/ with a dashboard `context`. The model call is
always stubbed; the tests read the prompt it was given."""

from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings

from services.copilot.anthropic_client import BudgetExceeded
from services.copilot.models import Conversation, Message, ModelCall

from .dashboard_fixture import POOR, DashboardFixture

URL = "/api/v1/copilot/messages/"


@patch("services.copilot.views.get_completion", return_value="Because Shaky renews soon.")
class DashboardSendTests(DashboardFixture):
    def setUp(self):
        super().setUp()
        self.shaky = self.customer(
            "Shaky", health_score=POOR, renewal_date=self.today + timedelta(days=10), arr=50_000
        )
        self.theirs = self.customer(
            "Theirs",
            owner=self.other,
            health_score=POOR,
            renewal_date=self.today + timedelta(days=5),
        )

    def send(self, context, content="Why is at-risk ARR up?", **extra):
        return self.api.post(URL, {"content": content, "context": context, **extra}, format="json")

    def test_the_answer_is_grounded_in_the_screen_and_metered_as_dashboard(self, completion):
        response = self.send(self.context("revenue", "forecast", owner=str(self.csm.pk)))

        self.assertEqual(response.status_code, 200, response.data)
        kwargs = completion.call_args.kwargs
        self.assertEqual(kwargs["purpose"], "dashboard")
        self.assertIn("Screen: Revenue › Forecast", kwargs["system"])
        self.assertIn("Opening ARR: 50,000.00 USD", kwargs["system"])
        self.assertIn("Answer only from", kwargs["system"])
        self.assertNotIn("Theirs", kwargs["system"])
        self.assertNotIn("Real-data summary", kwargs["system"])

    def test_the_context_is_echoed_on_the_turn_and_the_origin_on_the_conversation(self, completion):
        context = self.context("health", "triage", lifecycle="renewal")

        data = self.send(context).data

        self.assertEqual(data["messages"][0]["context"], context)
        self.assertIsNone(data["messages"][1]["context"])
        origin = {key: value for key, value in context.items() if key != "focus"}
        self.assertEqual(data["origin"], origin)
        self.assertEqual(Conversation.objects.get().origin, origin)

    def test_origin_is_set_once(self, completion):
        first = self.send(self.context("overview")).data
        second = self.send(self.context("support", "tickets"), conversation_id=first["id"]).data

        self.assertEqual(second["origin"]["area"], "overview")
        self.assertEqual(
            [m["context"]["area"] for m in second["messages"] if m["role"] == "user"],
            ["overview", "support"],
        )

    def test_origin_comes_from_the_first_dashboard_message(self, completion):
        started = self.api.post(URL, {"content": "[About: Shaky] Hi"}, format="json").data
        self.assertIsNone(started["origin"])

        data = self.send(self.context("revenue", "forecast"), conversation_id=started["id"]).data

        self.assertEqual(data["origin"]["area"], "revenue")

    def test_focus_ids_of_another_csm_are_dropped_silently(self, completion):
        focus = {"kind": "companies", "ids": [self.theirs.pk, self.shaky.pk]}

        response = self.send(self.context("overview", focus=focus))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Message.objects.get(role="user").context["focus"],
            {"kind": "companies", "ids": [self.shaky.pk]},
        )
        self.assertNotIn("Theirs", completion.call_args.kwargs["system"])

    def test_why_on_an_attention_row(self, completion):
        focus = {"kind": "attention", "key": f"renewal:{self.shaky.pk}"}

        response = self.send(self.context("overview", focus=focus), "Why is this on my list?")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "why this is on their attention list: Shaky", completion.call_args.kwargs["system"]
        )

    def test_a_key_not_on_the_list_is_the_generic_400(self, completion):
        bodies = [
            self.send(self.context(focus={"kind": "attention", "key": key})).data
            for key in (f"renewal:{self.theirs.pk}", "renewal:nope")
        ]

        self.assertEqual(bodies[0], {"context": {"focus": {"key": ["Not an item on your list."]}}})
        self.assertEqual(bodies[0], bodies[1])
        completion.assert_not_called()
        self.assertEqual(Conversation.objects.count(), 0)

    def test_validation_errors_are_400_and_leave_no_trace(self, completion):
        for context in (
            {**self.context(), "surface": "brain"},
            {**self.context(), "area": "brain"},
            self.context("revenue", None),
            self.context(focus={"kind": "segment"}),
            self.context(focus={"kind": "companies", "ids": list(range(1, 202))}),
            "revenue",
        ):
            with self.subTest(context=context):
                response = self.send(context)
                self.assertEqual(response.status_code, 400)
                self.assertIn("context", response.data)
        completion.assert_not_called()
        self.assertEqual(Message.objects.count(), 0)

    def test_a_null_context_is_a_plain_send(self, completion):
        response = self.api.post(URL, {"content": "Hi", "context": None}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(completion.call_args.kwargs["purpose"], "copilot")

    def test_an_exhausted_budget_is_a_429(self, completion):
        completion.side_effect = BudgetExceeded("Monthly budget reached")

        response = self.send(self.context("overview"))

        self.assertEqual(response.status_code, 429)
        self.assertEqual(Conversation.objects.count(), 0)


class DashboardBudgetTests(DashboardFixture):
    """Not stubbed: the real budget check runs before any call is made."""

    @override_settings(MODEL_BUDGET_DEFAULT_TOKENS=1)
    def test_the_dashboard_purpose_has_its_own_budget(self):
        ModelCall.objects.create(
            organisation=self.org, purpose="dashboard", input_tokens=5, outcome="ok"
        )

        response = self.api.post(
            URL, {"content": "Why?", "context": self.context("overview")}, format="json"
        )

        self.assertEqual(response.status_code, 429)
        self.assertIn("budget", response.data["detail"])
        self.assertTrue(
            ModelCall.objects.filter(purpose="dashboard", outcome="over_budget").exists()
        )
