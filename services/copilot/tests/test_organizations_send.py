"""POST /api/v1/copilot/messages/ with an Organizations `context`. The model
call is stubbed; the tests read the prompt it was given."""

from unittest.mock import patch

from django.test import override_settings

from services.copilot import usage
from services.copilot.anthropic_client import BudgetExceeded
from services.copilot.models import Conversation, Message, ModelCall

from .organizations_fixture import OrganizationsAskFixture

URL = "/api/v1/copilot/messages/"


def _in_order(query, candidates):
    return [(index, 1.0) for index in range(len(candidates))]


@patch("services.copilot.views.get_completion", return_value="Hooli renews first.")
class OrganizationsSendTests(OrganizationsAskFixture):
    def setUp(self):
        super().setUp()
        patcher = patch("services.copilot.retrieval.rank_by_similarity", side_effect=_in_order)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.book()

    def send(self, context, content="Which accounts need me first?", **extra):
        return self.api.post(URL, {"content": content, "context": context, **extra}, format="json")

    def test_the_answer_is_grounded_in_the_list_and_metered_as_organizations(self, completion):
        response = self.send(self.context(owner=str(self.csm.pk)))

        self.assertEqual(response.status_code, 200, response.data)
        kwargs = completion.call_args.kwargs
        self.assertEqual(kwargs["purpose"], "organizations")
        self.assertIn("Screen: Organizations › List", kwargs["system"])
        self.assertIn("Filters: Owner: Carl CSM", kwargs["system"])
        self.assertIn("Organizations data:\n<dashboard_data>", kwargs["system"])
        self.assertIn("Hooli", kwargs["system"])
        self.assertNotIn("Dana's Co", kwargs["system"])
        self.assertNotIn("Real-data summary", kwargs["system"])

    def test_the_context_is_stored_canonical_with_its_labels_and_becomes_the_origin(
        self, completion
    ):
        context = self.context(
            "board",
            owner=self.csm.pk,
            lifecycle=["live", "renewal"],
            cursor="abc",
            limit="5",
            group_value="live",
            horizon_days="90",
        )

        data = self.send(context).data

        expected = {
            "surface": "organizations",
            "view": "board",
            "filters": {"owner": str(self.csm.pk), "lifecycle": "live,renewal"},
            "labels": ["Owner: Carl CSM", "Lifecycle: Live, Renewal"],
            "focus": None,
        }
        self.assertEqual(data["messages"][0]["context"], expected)
        self.assertIsNone(data["messages"][1]["context"])
        origin = {key: value for key, value in expected.items() if key != "focus"}
        self.assertEqual(data["origin"], origin)
        self.assertEqual(Conversation.objects.get().origin, origin)

    def test_unknown_filter_values_are_dropped_not_rejected(self, completion):
        response = self.send(
            self.context(
                lifecycle="bogus,live",
                health="meh",
                renews_within="45",
                nps="fan",
                sort="wat",
                group="colour",
                include_churned="yes",
                search="x" * 101,
            )
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Message.objects.get(role="user").context["filters"], {"lifecycle": "live"})

    def test_history_lists_the_conversation_with_what_restores_the_page(self, completion):
        started = self.send(self.context(owner=str(self.csm.pk))).data

        rows = self.api.get("/api/v1/copilot/conversations/").data
        detail = self.api.get(f"/api/v1/copilot/conversations/{started['id']}/").data

        origin = {
            "surface": "organizations",
            "view": "list",
            "filters": {"owner": str(self.csm.pk)},
            "labels": ["Owner: Carl CSM"],
        }
        self.assertEqual(next(r for r in rows if r["id"] == started["id"])["origin"], origin)
        self.assertEqual(detail["origin"], origin)

    def test_origin_is_set_once_across_surfaces(self, completion):
        first = self.send(self.context(health="poor")).data
        dashboard = {
            "surface": "dashboard",
            "area": "overview",
            "view": None,
            "filters": {"owner": "", "lifecycle": "", "customer": ""},
            "focus": None,
        }

        second = self.send(dashboard, conversation_id=first["id"]).data

        self.assertEqual(second["origin"]["surface"], "organizations")
        self.assertEqual(
            [m["context"]["surface"] for m in second["messages"] if m["role"] == "user"],
            ["organizations", "dashboard"],
        )
        self.assertEqual(completion.call_args.kwargs["purpose"], "dashboard")

    def test_the_reply_records_the_turn_it_answers(self, completion):
        self.send(self.context())

        self.assertEqual(
            Message.objects.get(role="assistant").reply_to, Message.objects.get(role="user")
        )

    def test_focus_ids_of_another_csm_are_dropped_silently(self, completion):
        focus = {"kind": "companies", "ids": [self.danas.pk, self.pizza.pk]}

        response = self.send(self.context(focus=focus))

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            Message.objects.get(role="user").context["focus"],
            {"kind": "companies", "ids": [self.pizza.pk]},
        )
        self.assertNotIn("Dana's Co", completion.call_args.kwargs["system"])

    def test_ids_naming_another_csms_customer_read_nothing_of_it(self, completion):
        response = self.send(
            self.context(ids=f"{self.danas.pk},{self.pizza.pk}"), "What about Dana's Co?"
        )

        self.assertEqual(response.status_code, 200, response.data)
        system = completion.call_args.kwargs["system"]
        self.assertNotIn("Dana's Co", system)
        self.assertIn("Filters: Opened from the dashboard (2)", system)
        self.assertIn("Accounts in view: 1;", system)

    def test_an_opened_row_question_raises_no_knowledge_gap(self, completion):
        from services.knowledge.models import KnowledgeGap

        focus = {"kind": "companies", "ids": [self.pizza.pk]}

        response = self.send(self.context(focus=focus), "Will they renew?")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(KnowledgeGap.objects.count(), 0)

    def test_validation_errors_are_400_and_leave_no_trace(self, completion):
        base = self.context()
        cases = (
            {**base, "surface": "brain"},
            {**base, "view": "grid"},
            {key: value for key, value in base.items() if key != "view"},
            {**base, "focus": {"kind": "attention", "key": f"renewal:{self.pizza.pk}"}},
            {**base, "focus": {"kind": "companies", "ids": "1,2"}},
            {**base, "focus": {"kind": "companies"}},
            {**base, "focus": {"kind": "companies", "ids": None}},
            {**base, "focus": {"kind": "companies", "ids": list(range(1, 202))}},
            "organizations",
        )
        for context in cases:
            with self.subTest(context=context):
                response = self.send(context)
                self.assertEqual(response.status_code, 400)
                self.assertIn("context", response.data)
        completion.assert_not_called()
        self.assertEqual(Message.objects.count(), 0)
        self.assertEqual(Conversation.objects.count(), 0)

    def test_the_error_names_the_field(self, completion):
        cases = (
            (
                {**self.context(), "surface": "brain"},
                {"surface": ['"brain" is not a valid choice.']},
            ),
            ({**self.context(), "view": "grid"}, {"view": ['"grid" is not a valid choice.']}),
            (
                {**self.context(), "focus": {"kind": "attention", "key": "risk:1"}},
                {"focus": {"kind": ["Must be companies."]}},
            ),
        )
        for context, errors in cases:
            with self.subTest(context=context):
                self.assertEqual(self.send(context).data, {"context": errors})

    def test_an_exhausted_budget_is_a_429(self, completion):
        completion.side_effect = BudgetExceeded("Monthly budget reached")

        response = self.send(self.context())

        self.assertEqual(response.status_code, 429)
        self.assertEqual(Conversation.objects.count(), 0)


class OrganizationsBudgetTests(OrganizationsAskFixture):
    """Not stubbed: the real budget check runs before any call is made."""

    @override_settings(MODEL_BUDGET_DEFAULT_TOKENS=1)
    def test_the_organizations_purpose_has_its_own_budget(self):
        ModelCall.objects.create(
            organisation=self.org, purpose="organizations", input_tokens=5, outcome="ok"
        )

        response = self.api.post(URL, {"content": "Why?", "context": self.context()}, format="json")

        self.assertEqual(response.status_code, 429)
        self.assertIn("budget", response.data["detail"])
        self.assertTrue(
            ModelCall.objects.filter(purpose="organizations", outcome="over_budget").exists()
        )

    def test_usage_reports_the_purpose_by_name(self):
        ModelCall.objects.create(
            organisation=self.org, purpose="organizations", input_tokens=5, outcome="ok"
        )

        rows = {row["purpose"]: row for row in usage.summary(self.org)["purposes"]}

        self.assertEqual(rows["organizations"]["label"], "Ask Revenact on Organizations")
        self.assertEqual(rows["organizations"]["spent"], 5)
