"""The one helper every path that creates a call runs (`calls.classify_call`):
a call is read at once, or marked not analysable, and a failure never blocks
the call. The model is stubbed throughout."""

import ast
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase
from rest_framework import status

from services.copilot.anthropic_client import CopilotNotConfigured
from services.customers import calls
from services.customers.classification import _text_for, classify_records
from services.customers.models import Account, Call, Contact, Email
from services.customers.tests.test_call_state import WHEN, CallFixture

BATCH = "services.customers.classification.classify_batch"


def placed(call, sentiment="negative"):
    return {
        f"call:{call.pk}": {
            "ai_area": "customer_success",
            "ai_category": "onboarding",
            "ai_subcategory": "",
            "sentiment": sentiment,
        }
    }


class DeclinedTests(CallFixture):
    def test_a_call_the_model_declines_is_not_analysable(self):
        call = self.call(summary="Hard to say.")
        with patch(BATCH, return_value={}):
            classify_records([call], organisation=self.org)
        call.refresh_from_db()
        self.assertEqual(call.analysis, "not_analysable")

    def test_an_email_the_model_declines_stays_pending(self):
        email = Email.objects.create(
            customer=self.pizza,
            subject="Hi",
            sender_name="Sam",
            recipient_name="Carl",
            body="hi",
            sent_at=WHEN,
        )
        with patch(BATCH, return_value={}):
            classify_records([email], organisation=self.org)
        email.refresh_from_db()
        self.assertEqual(email.analysis, "pending")


class ClassifyCallTests(CallFixture):
    def test_a_generic_call_with_nothing_else_is_marked_without_a_model_call(self):
        call = self.call(title="Weekly sync")
        with patch(BATCH) as batch:
            calls.classify_call(call)
        batch.assert_not_called()
        call.refresh_from_db()
        self.assertEqual(call.analysis, "not_analysable")

    def test_a_generic_title_with_a_pasted_transcript_is_read(self):
        call = self.call(title="Weekly sync")
        with patch(BATCH, side_effect=lambda batch, **_: placed(call)) as batch:
            calls.classify_call(call, transcript_text="We are unhappy with support.")
        self.assertIn("We are unhappy", _text_for(batch.call_args.args[0][0]))
        call.refresh_from_db()
        self.assertEqual((call.analysis, call.sentiment), ("analysed", "negative"))

    def test_an_account_call_is_metered_to_its_organisation_and_logger(self):
        call = self.call(customer=None, account=self.emea, logged_by=self.carl)
        with patch(BATCH, return_value=placed(call)) as batch:
            calls.classify_call(call)
        self.assertEqual(batch.call_args.kwargs, {"organisation": self.org, "user": self.carl})

    def test_classifying_updates_the_people_on_the_call(self):
        sam = Contact.objects.create(customer=self.pizza, name="Sam Pizza", email="sam@pizza.io")
        call = self.call()
        call.participants.set([sam])
        with patch(BATCH, return_value=placed(call)):
            calls.classify_call(call)
        sam.refresh_from_db()
        self.assertEqual((sam.sentiment, sam.sentiment_source), ("negative", "computed"))

    def test_no_model_leaves_it_pending_for_the_nightly_pass(self):
        call = self.call()
        with patch(
            "services.customers.classification.get_completion",
            side_effect=CopilotNotConfigured("no model"),
        ):
            calls.classify_call(call)
        call.refresh_from_db()
        self.assertEqual(call.analysis, "pending")

    def test_any_failure_is_swallowed(self):
        call = self.call()
        with patch(BATCH, side_effect=RuntimeError("boom")):
            calls.classify_call(call)
        call.refresh_from_db()
        self.assertEqual(call.analysis, "pending")

    def test_an_account_with_no_organisation_is_swallowed_too(self):
        orphan = Account.objects.create(name="Orphan")
        call = self.call(customer=None, account=orphan)
        calls.classify_call(call)
        call.refresh_from_db()
        self.assertEqual(call.analysis, "pending")


class CreatePathTests(CallFixture):
    """Logging a call through the API ("+ Add" on an organisation or an
    account) runs the helper."""

    def post(self, path, body):
        self.client.force_authenticate(self.carl)
        return self.client.post(f"/api/v1/customers/{self.pizza.id}{path}", body, format="json")

    def test_both_create_paths_classify_the_new_call(self):
        def answer(batch, **_):
            return placed(batch[0], "positive")

        for path in ["/calls/", f"/accounts/{self.emea.id}/calls/"]:
            with self.subTest(path=path), patch(BATCH, side_effect=answer):
                response = self.post(
                    path,
                    {"title": "QBR", "occurred_at": WHEN.isoformat(), "summary": "Going well."},
                )
                self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
                self.assertEqual(
                    (response.data["analysis"], response.data["sentiment"]),
                    ("analysed", "positive"),
                )

    def test_a_pasted_transcript_is_what_the_classifier_reads(self):
        with (
            patch("services.customers.calls.get_completion", return_value="A summary."),
            patch(BATCH, side_effect=lambda batch, **_: placed(batch[0])) as batch,
        ):
            self.post(
                "/calls/",
                {
                    "title": "QBR",
                    "occurred_at": WHEN.isoformat(),
                    "transcript_text": "We are unhappy with support.",
                },
            )
        self.assertEqual(_text_for(batch.call_args.args[0][0]), "QBR. We are unhappy with support.")

    def test_a_call_with_nothing_to_read_says_so(self):
        with patch(BATCH) as batch:
            response = self.post("/calls/", {"title": "Call", "occurred_at": WHEN.isoformat()})
        batch.assert_not_called()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["analysis"], "not_analysable")

    def test_a_classifier_failure_never_blocks_the_call(self):
        with patch(BATCH, side_effect=RuntimeError("boom")):
            response = self.post(
                "/calls/", {"title": "QBR", "occurred_at": WHEN.isoformat(), "summary": "Fine."}
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["analysis"], "pending")
        self.assertTrue(Call.objects.filter(pk=response.data["id"]).exists())


class OneCreatePathTests(SimpleTestCase):
    """A Call is created in one place outside tests and seed commands: the
    CallSense list view, which runs `calls.classify_call`. A new path — a
    recorder connector, say — must call it too, and then join ALLOWED."""

    CREATORS = {"create", "bulk_create", "get_or_create", "update_or_create"}
    ALLOWED = {"services/customers/views.py"}

    def test_every_module_that_creates_a_call_runs_the_helper(self):
        root = Path(settings.BASE_DIR)
        creators = set()
        for path in (root / "services").rglob("*.py"):
            relative = path.relative_to(root).as_posix()
            if "/tests/" in relative or "/migrations/" in relative or "/seed_demo_" in relative:
                continue
            source = path.read_text()
            if "Call" not in source:
                continue
            if "serializer_class = CallSerializer" in source:
                creators.add(relative)
            for node in ast.walk(ast.parse(source)):
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr in self.CREATORS
                    and isinstance(node.value, ast.Attribute)
                    and node.value.attr == "objects"
                    and isinstance(node.value.value, ast.Name)
                    and node.value.value.id == "Call"
                ):
                    creators.add(relative)
        self.assertEqual(creators, self.ALLOWED)
        views = (root / "services/customers/views.py").read_text()
        self.assertIn("classify_call(call, transcript_text=transcript_text", views)
