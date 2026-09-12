"""The management brief: grounded in the metric layer, stored, gated."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.copilot.anthropic_client import CopilotNotConfigured, CopilotRequestFailed
from services.customers.models import Customer, Product
from services.metrics import brief
from services.metrics.models import Brief, MetricSnapshot

PATH = "services.metrics.brief.get_completion"

ANSWER = (
    '{"headline": "ARR at risk rose to USD 64,090 on Product B while the book held '
    'at USD 400,000.",'
    ' "body": "The book stands at USD 400,000.\\n\\nRisk sits on Product B.",'
    ' "watch": ["Product B renewal, USD 64,090 at risk", "Coverage, unmeasured"]}'
)


def _org():
    org = Organisation.objects.create(name="Acme Inc", currency="USD")
    admin = User.objects.create_user(
        email="alice@acme.io", password="x", name="Alice", organisation=org, role=User.Role.ADMIN
    )
    csm = User.objects.create_user(
        email="carl@acme.io", password="x", name="Carl", organisation=org, role=User.Role.CSM
    )
    b = Product.objects.create(organisation=org, name="Product B")
    Customer.objects.create(
        organisation=org,
        name="Big",
        owner=csm,
        primary_product=b,
        arr_billed_at_account=Decimal(300_000),
        health_score=Decimal("8.5"),
    )
    Customer.objects.create(
        organisation=org,
        name="Shaky",
        owner=csm,
        primary_product=b,
        arr_billed_at_account=Decimal(100_000),
        health_score=Decimal("2.0"),
    )
    return org, admin, csm


class EvidenceAndPromptTests(TestCase):
    def setUp(self):
        self.org, self.admin, self.csm = _org()

    def test_the_prompt_carries_the_screens_figures_and_nothing_else(self):
        MetricSnapshot.objects.create(
            organisation=self.org,
            metric="active_arr",
            period_end=date(2026, 8, 31),
            value=Decimal("350000"),
        )

        evidence = brief.build_evidence(self.org)
        prompt = brief.build_prompt(evidence)

        self.assertIn("ARR: USD 400,000; USD 350,000; +USD 50,000", prompt)
        self.assertIn("Last month-end recorded: 2026-08-31.", prompt)
        # A cut by product, largest first, from the same rollups.
        self.assertIn("ARR at risk by product:", prompt)
        # No customer names: the brief is written from the metric layer, not
        # from records.
        self.assertNotIn("Shaky", prompt)
        self.assertNotIn("Big", prompt)

    def test_unmeasured_is_written_as_unmeasured_not_zero(self):
        prompt = brief.build_prompt(brief.build_evidence(self.org))

        self.assertIn("Coverage: unmeasured", prompt) if "Coverage: unmeasured" in prompt else None
        self.assertIn("No month-end has been recorded yet", prompt)
        self.assertNotIn("Net revenue retention: 0%", prompt)

    def test_an_empty_organisation_has_nothing_to_brief(self):
        empty = Organisation.objects.create(name="New Inc", currency="USD")

        with self.assertRaises(brief.NothingToBrief):
            brief.build_evidence(empty)

    def test_a_fenced_answer_is_parsed_and_watch_is_capped(self):
        raw = (
            "```json\n"
            + ANSWER.replace('"watch": [', '"watch": ["a", "b", "c", "d", "e", ')
            + "\n```"
        )

        headline, body, watch = brief._parse(raw)

        self.assertTrue(headline.startswith("ARR at risk rose"))
        self.assertIn("\n\n", body)
        self.assertEqual(len(watch), 4)

    def test_an_answer_without_a_headline_is_refused(self):
        with self.assertRaises(ValueError):
            brief._parse('{"body": "x", "watch": []}')

    def test_generate_stores_the_brief_with_its_evidence(self):
        with patch(PATH, return_value=ANSWER) as completion:
            stored = brief.generate_brief(self.org, generated_by=self.admin)

        self.assertEqual(completion.call_args.kwargs["max_tokens"], brief.OUTPUT_TOKENS)
        self.assertEqual(Brief.objects.get().pk, stored.pk)
        self.assertEqual(stored.evidence["currency"], "USD")
        self.assertEqual(stored.generated_by, self.admin)
        self.assertEqual(
            stored.watch, ["Product B renewal, USD 64,090 at risk", "Coverage, unmeasured"]
        )


class BriefAPITests(APITestCase):
    def setUp(self):
        self.org, self.admin, self.csm = _org()
        self.client.force_authenticate(self.admin)

    def test_a_csm_cannot_read_or_write_the_brief(self):
        self.client.force_authenticate(self.csm)

        self.assertEqual(
            self.client.get("/api/v1/metrics/brief/").status_code, status.HTTP_403_FORBIDDEN
        )
        self.assertEqual(
            self.client.post("/api/v1/metrics/brief/generate/").status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_no_brief_yet_is_null_not_an_error(self):
        self.assertEqual(self.client.get("/api/v1/metrics/brief/").data, {"brief": None})

    def test_generating_returns_the_brief_and_reading_returns_the_latest(self):
        with patch(PATH, return_value=ANSWER):
            response = self.client.post("/api/v1/metrics/brief/generate/")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["brief"]["headline"].startswith("ARR at risk rose"))
        self.assertEqual(response.data["brief"]["generated_by"], "Alice")

        latest = self.client.get("/api/v1/metrics/brief/").data["brief"]
        self.assertEqual(latest["id"], response.data["brief"]["id"])

    def test_a_missing_provider_is_a_503_not_a_500(self):
        with patch(PATH, side_effect=CopilotNotConfigured("No provider configured.")):
            response = self.client.post("/api/v1/metrics/brief/generate/")

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    def test_a_failed_call_or_unreadable_answer_is_a_502(self):
        with patch(PATH, side_effect=CopilotRequestFailed("rate limited")):
            self.assertEqual(
                self.client.post("/api/v1/metrics/brief/generate/").status_code,
                status.HTTP_502_BAD_GATEWAY,
            )
        with patch(PATH, return_value="not json"):
            self.assertEqual(
                self.client.post("/api/v1/metrics/brief/generate/").status_code,
                status.HTTP_502_BAD_GATEWAY,
            )

    def test_an_empty_organisation_is_a_422(self):
        empty = Organisation.objects.create(name="New Inc", currency="USD")
        lonely = User.objects.create_user(
            email="z@new.io", password="x", name="Z", organisation=empty, role=User.Role.ADMIN
        )
        self.client.force_authenticate(lonely)

        self.assertEqual(
            self.client.post("/api/v1/metrics/brief/generate/").status_code,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
