"""The feedback log and the classification correction it records."""

from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers.models import Customer, Ticket
from services.metrics import feedback
from services.metrics.models import Feedback, Proposal
from services.metrics.proposals import approve, reject


def _org():
    org = Organisation.objects.create(name="Acme Inc", currency="USD")
    admin = User.objects.create_user(
        email="alice@acme.io", password="x", name="Alice", organisation=org, role=User.Role.ADMIN
    )
    carl = User.objects.create_user(
        email="carl@acme.io", password="x", name="Carl", organisation=org, role=User.Role.CSM
    )
    dana = User.objects.create_user(
        email="dana@acme.io", password="x", name="Dana", organisation=org, role=User.Role.CSM
    )
    customer = Customer.objects.create(
        organisation=org, name="Pizza Hut", owner=carl, arr_billed_at_account=Decimal(50_000)
    )
    ticket = Ticket.objects.create(
        customer=customer,
        ticket_number="TKT-1",
        title="Slow page load for large accounts",
        assignee_name="Support",
        priority=Ticket.Priority.HIGH,
        opened_at=timezone.localdate(),
        ai_area="support_operations",
        ai_category="integration_support",
        ai_subcategory="webhook_failure",
        sentiment="neutral",
        ai_classified_at=timezone.now(),
    )
    return org, admin, carl, dana, customer, ticket


class CorrectionTests(TestCase):
    def setUp(self):
        self.org, self.admin, self.carl, self.dana, self.customer, self.ticket = _org()

    def test_a_correction_applies_the_tags_and_logs_before_and_after(self):
        record_, entry = feedback.correct_classification(
            self.org,
            self.ticket,
            {
                "area": "Product & Growth",
                "category": "bug_report",
                "subcategory": "performance_issue",
            },
            made_by=self.carl,
            note="It's a perf bug, not a webhook.",
        )

        self.assertEqual(record_.ai_area, "product_growth")
        self.assertEqual(record_.ai_subcategory, "performance_issue")
        self.assertIsNotNone(record_.classification_corrected_at)
        self.assertEqual(entry.kind, "classification")
        self.assertEqual(entry.subject_label, "Slow page load for large accounts")
        self.assertEqual(entry.before["subcategory"], "webhook_failure")
        self.assertEqual(entry.after["subcategory"], "performance_issue")
        self.assertEqual(entry.made_by, self.carl)

    def test_a_subcategory_under_the_wrong_category_is_refused(self):
        with self.assertRaises(feedback.InvalidCorrection):
            feedback.correct_classification(
                self.org,
                self.ticket,
                {"category": "onboarding", "subcategory": "webhook_failure"},
                made_by=self.carl,
            )

    def test_a_value_the_taxonomy_lacks_is_refused(self):
        with self.assertRaises(feedback.InvalidCorrection):
            feedback.correct_classification(
                self.org, self.ticket, {"area": "marketing"}, made_by=self.carl
            )

    def test_a_no_op_correction_logs_nothing(self):
        _record, entry = feedback.correct_classification(
            self.org, self.ticket, {"area": "support_operations"}, made_by=self.carl
        )

        self.assertIsNone(entry)
        self.assertFalse(Feedback.objects.exists())

    def test_a_reclassify_pass_leaves_a_corrected_row_alone(self):
        """A person's correction outranks the model. A reclassify is for a
        changed taxonomy or a better prompt, not a reason to put the model's
        answer back."""
        feedback.correct_classification(
            self.org,
            self.ticket,
            {"category": "bug_report", "subcategory": "ui_bug"},
            made_by=self.carl,
        )
        untouched = Ticket.objects.create(
            customer=self.customer,
            ticket_number="TKT-2",
            title="Other",
            assignee_name="S",
            priority=Ticket.Priority.LOW,
            opened_at=timezone.localdate(),
            ai_classified_at=timezone.now(),
        )

        with patch(
            "services.customers.management.commands.classify_interactions.classify_batch",
            side_effect=lambda batch, **_kw: {},
        ) as classify:
            call_command(
                "classify_interactions", reclassify=True, stdout=StringIO(), stderr=StringIO()
            )

        sent = {r.pk for call in classify.call_args_list for r in call.args[0]}
        self.assertIn(untouched.pk, sent)
        self.assertNotIn(self.ticket.pk, sent)

        with patch(
            "services.customers.management.commands.classify_interactions.classify_batch",
            side_effect=lambda batch, **_kw: {},
        ) as classify:
            call_command(
                "classify_interactions",
                reclassify=True,
                include_corrected=True,
                stdout=StringIO(),
                stderr=StringIO(),
            )
        sent = {r.pk for call in classify.call_args_list for r in call.args[0]}
        self.assertIn(self.ticket.pk, sent)


class ProposalAndOverrideFeedbackTests(APITestCase):
    def setUp(self):
        self.org, self.admin, self.carl, self.dana, self.customer, self.ticket = _org()

    def test_deciding_a_proposal_is_feedback_on_the_agent(self):
        proposal = Proposal.objects.create(
            organisation=self.org,
            batch="b",
            kind="task",
            title="Do a thing",
            rationale="because",
            action={
                "customer_id": self.customer.id,
                "customer_name": "Pizza Hut",
                "title": "t",
                "assignee_name": "Carl",
                "due_date": (timezone.localdate() + timedelta(days=3)).isoformat(),
                "priority": "high",
            },
        )
        reject(proposal, self.admin, "Already in hand")

        entry = Feedback.objects.get()
        self.assertEqual(entry.kind, "proposal")
        self.assertEqual(entry.after, {"decision": "rejected"})
        self.assertEqual(entry.note, "Already in hand")
        self.assertEqual(entry.before["kind"], "task")

        other = Proposal.objects.create(
            organisation=self.org,
            batch="b",
            kind="task",
            title="Another",
            rationale="r",
            action=proposal.action,
        )
        approve(other, self.admin)
        self.assertEqual(Feedback.objects.filter(kind="proposal").count(), 2)

    def test_overriding_a_health_score_is_feedback_on_the_rubric(self):
        self.client.force_authenticate(self.carl)

        response = self.client.patch(
            f"/api/v1/customers/{self.customer.id}/", {"health_score": "9.5"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        entry = Feedback.objects.get(kind="health_override")
        self.assertEqual(entry.subject_label, "Pizza Hut")
        self.assertEqual(entry.after, {"health_score_override": "9.5"})
        self.assertEqual(entry.made_by, self.carl)

        # Clearing it is a correction too, and an unrelated edit is not.
        self.client.patch(
            f"/api/v1/customers/{self.customer.id}/", {"health_score": None}, format="json"
        )
        self.client.patch(
            f"/api/v1/customers/{self.customer.id}/", {"domain": "pizzahut.com"}, format="json"
        )
        self.assertEqual(Feedback.objects.filter(kind="health_override").count(), 2)


class CorrectionAPITests(APITestCase):
    def setUp(self):
        self.org, self.admin, self.carl, self.dana, self.customer, self.ticket = _org()
        self.url = f"/api/v1/interactions/ticket/{self.ticket.id}/classification/"

    def test_the_owner_corrects_their_own_record(self):
        self.client.force_authenticate(self.carl)

        response = self.client.patch(
            self.url,
            {"category": "bug_report", "subcategory": "performance_issue", "note": "perf"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["keys"]["subcategory"], "performance_issue")
        self.assertEqual(response.data["labels"]["category"], "Bug Report")
        self.assertTrue(response.data["corrected"])
        self.assertEqual(response.data["feedback"]["note"], "perf")

    def test_someone_who_cannot_see_the_record_cannot_correct_it(self):
        self.client.force_authenticate(self.dana)

        self.assertEqual(
            self.client.patch(self.url, {"category": "bug_report"}, format="json").status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_bad_values_are_a_400_and_nothing_is_written(self):
        self.client.force_authenticate(self.carl)

        response = self.client.patch(self.url, {"subcategory": "made_up"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Feedback.objects.exists())
        self.assertEqual(
            self.client.patch(self.url, {}, format="json").status_code, status.HTTP_400_BAD_REQUEST
        )
        self.assertEqual(
            self.client.patch(
                f"/api/v1/interactions/memo/{self.ticket.id}/classification/", {"area": "x"}
            ).status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_the_log_is_management_facing_and_newest_first(self):
        self.client.force_authenticate(self.carl)
        self.client.patch(
            self.url, {"category": "bug_report", "subcategory": "ui_bug"}, format="json"
        )
        self.client.patch(self.url, {"sentiment": "negative"}, format="json")

        self.assertEqual(
            self.client.get("/api/v1/metrics/feedback/").status_code, status.HTTP_403_FORBIDDEN
        )

        self.client.force_authenticate(self.admin)
        data = self.client.get("/api/v1/metrics/feedback/").data
        self.assertEqual(data["counts"]["classification"], 2)
        self.assertEqual(data["feedback"][0]["after"]["sentiment"], "negative")
        self.assertEqual(data["feedback"][0]["made_by"], "Carl")
        self.assertEqual(
            len(self.client.get("/api/v1/metrics/feedback/?kind=proposal").data["feedback"]), 0
        )
