"""What the model is told on the dashboard: the screen's figures, labelled
with where they were computed, and the records behind them — never anything
outside the asker's filtered, visible book."""

from datetime import timedelta
from unittest.mock import patch

from services.accounts.models import User
from services.copilot.dashboard_grounding import (
    build_dashboard_grounding,
    dashboard_system_prompt,
)
from services.customers.models import Contact, Note

from .dashboard_fixture import POOR, DashboardFixture


def _in_order(query, candidates):
    return [(index, 1.0) for index in range(len(candidates))]


class GroundingTests(DashboardFixture):
    def setUp(self):
        super().setUp()
        # Retrieval ranks with local embeddings; the order is not under test.
        patcher = patch("services.copilot.retrieval.rank_by_similarity", side_effect=_in_order)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.shaky = self.customer(
            "Shaky",
            health_score=POOR,
            renewal_date=self.today + timedelta(days=30),
            arr=50_000,
        )
        self.steady = self.customer("Steady", arr=80_000)
        self.theirs = self.customer("Theirs", owner=self.other, health_score=POOR)

    def ground(self, context, question="Why?", user=None):
        return build_dashboard_grounding(user or self.csm, context, question, today=self.today)

    def test_the_digest_is_labelled_with_the_screen(self):
        summary = self.ground(self.context("revenue", "forecast", owner=str(self.csm.pk))).summary

        self.assertIn("Screen: Revenue › Forecast", summary)
        self.assertIn("Filters: Owner: Carl", summary)
        self.assertIn("Currency: USD", summary)

    def test_each_area_has_its_digest(self):
        cases = {
            ("overview", None): "ARR today: 130,000.00 USD",
            ("revenue", "forecast"): "Opening ARR: 130,000.00 USD",
            ("health", "triage"): "Health triage over 2 accounts",
            ("support", "tickets"): "Open tickets: 0",
        }
        for (area, view), expected in cases.items():
            with self.subTest(area=area):
                self.assertIn(expected, self.ground(self.context(area, view)).summary)

    def test_revenue_says_there_is_no_new_business_figure(self):
        summary = self.ground(self.context("revenue", "forecast")).summary
        self.assertIn("no new-business figure", summary)

    def test_another_csms_customer_never_appears_even_as_focus(self):
        # Straight to the builder, past the serializer: the builder narrows too.
        focus = {"kind": "companies", "ids": [self.theirs.pk, self.shaky.pk]}
        for area, view in (("overview", None), ("revenue", "forecast"), ("health", "triage")):
            with self.subTest(area=area):
                grounding = self.ground(self.context(area, view, focus=focus))
                self.assertNotIn("Theirs", grounding.summary)
                self.assertIn("Shaky: health poor", grounding.summary)

    def test_a_named_company_inside_the_book_gets_its_facts_and_records(self):
        Note.objects.create(
            customer=self.shaky,
            title="Budget freeze",
            author_name="Edgar",
            body="Procurement froze all renewals.",
            logged_at=str(self.today),
        )
        Contact.objects.create(customer=self.shaky, name="Priya Rao", email="p@shaky.io")

        grounding = self.ground(self.context("revenue", "forecast"), "Why is Shaky at risk?")

        self.assertIn("The question names Shaky.", grounding.summary)
        self.assertIn("renews", grounding.summary)
        self.assertIn("Priya Rao", grounding.summary)
        self.assertNotIn("p@shaky.io", grounding.summary)
        self.assertIn("Budget freeze", grounding.summary)
        self.assertIn("Budget freeze", [s["label"] for s in grounding.sources])
        self.assertEqual(grounding.company, self.shaky)

    def test_a_named_company_outside_the_book_is_not_read(self):
        grounding = self.ground(self.context("overview"), "What about Theirs?")
        self.assertNotIn("Theirs", grounding.summary)
        self.assertIsNone(grounding.company)

    def test_a_ticket_outside_the_viewers_department_is_never_cited(self):
        self.ticket(1, self.shaky, title="Checkout broken")
        self.ticket(2, self.shaky, title="Kernel panic", department=User.Function.ENGINEERING)
        focus = {"kind": "companies", "ids": [self.shaky.pk]}

        grounding = self.ground(self.context("support", "tickets", focus=focus))

        self.assertIn("Checkout broken", grounding.summary)
        self.assertNotIn("Kernel panic", grounding.summary)
        self.assertEqual(
            {s["label"] for s in grounding.sources if s["type"] == "ticket"},
            {"TKT-1 Checkout broken"},
        )

    def test_an_attention_item_is_explained_with_its_company(self):
        key = f"renewal:{self.shaky.pk}"
        focus = {"kind": "attention", "key": key}

        grounding = self.ground(self.context(focus=focus))

        self.assertIn("why this is on their attention list: Shaky", grounding.summary)
        self.assertIn("Shaky: health poor", grounding.summary)
        self.assertEqual(grounding.company, self.shaky)

    def test_an_attention_item_outside_the_filters_is_not_read(self):
        focus = {"kind": "attention", "key": f"renewal:{self.shaky.pk}"}
        grounding = self.ground(self.context(focus=focus, customer=str(self.steady.pk)))
        self.assertIn("outside the current filters", grounding.summary)
        self.assertNotIn("Shaky: health", grounding.summary)


class AnomalyTitleTests(DashboardFixture):
    def setUp(self):
        super().setUp()
        self.mine = self.customer("Mine")
        self.customer("Theirs", owner=self.other)
        self.live = self.anomaly(
            "Theirs and Mine both report SSO failures", summary="Theirs is down too"
        )
        self.row = self.evidence(self.live, 41, customer=self.mine)
        self.focus = {"kind": "attention", "key": f"anomaly:{self.live.pk}"}

    def test_a_viewer_who_does_not_see_everything_gets_the_built_title(self):
        grounding = build_dashboard_grounding(
            self.csm, self.context(focus=self.focus), "Why?", today=self.today
        )

        self.assertIn("Similar reports across 1 of your companies", grounding.summary)
        self.assertNotIn("Theirs", grounding.summary)
        self.assertIn("Login fails after SSO redirect", grounding.summary)
        self.assertEqual(
            [(s["type"], s["id"]) for s in grounding.sources], [("call", self.row.record_id)]
        )

    def test_a_viewer_who_sees_everything_gets_the_stored_title(self):
        grounding = build_dashboard_grounding(
            self.admin, self.context(focus=self.focus), "Why?", today=self.today
        )

        self.assertIn("Theirs and Mine both report SSO failures", grounding.summary)
        self.assertIn("Theirs is down too", grounding.summary)


class PromptTests(DashboardFixture):
    def test_the_prompt_confines_the_answer_to_the_digest(self):
        prompt = dashboard_system_prompt("Be concise.", "Screen: Overview")

        self.assertIn("Answer only from", prompt)
        self.assertIn("say so plainly", prompt)
        self.assertIn("Cite", prompt)
        self.assertIn("Be concise.", prompt)
        self.assertTrue(prompt.endswith("Dashboard data:\nScreen: Overview"))
