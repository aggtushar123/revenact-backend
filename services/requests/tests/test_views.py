"""Integration tier: feature requests through the real URLconf and test DB.

Embeddings and the model are patched where gather imports them: vectors
are chosen by hand so the grouping is deterministic."""

import json
from datetime import datetime
from datetime import timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.utils import timezone
from rest_framework.test import APITestCase

from core.models import AuditEvent
from services.accounts.capabilities import Capability
from services.accounts.models import Organisation, Role, User
from services.copilot.anthropic_client import BudgetExceeded
from services.customers.models import Account, Customer, Email, Ticket
from services.customers.taxonomy import AICategory
from services.requests.models import FeatureRequest, RequestEvidence

NOW = datetime(2026, 9, 15, 9, 0, tzinfo=dt_timezone.utc)
URL = "/api/v1/requests/"
EMBED = "services.requests.gather.embed"
COMPLETION = "services.requests.gather.get_completion"

SLACK = [1.0, 0.0, 0.0]
SLACK_TOO = [0.98, 0.2, 0.0]
EXPORT = [0.0, 1.0, 0.0]


def titled(title, summary="Customers want it."):
    return json.dumps({"title": title, "summary": summary})


def fake_embed(vectors_by_text):
    def embed(texts):
        return [vectors_by_text[t] for t in texts]

    return embed


class Fixture(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        mk = lambda email, name, **kw: User.objects.create_user(  # noqa: E731
            email=email, password="x", name=name, organisation=self.org, **kw
        )
        self.alice = mk("alice@acme.io", "Alice", role=User.Role.ADMIN)
        self.dana = mk("dana@acme.io", "Dana", role=User.Role.CSM)
        self.eve = mk("eve@acme.io", "Eve", role=User.Role.CSM)
        self.pizza = Customer.objects.create(
            organisation=self.org,
            name="Pizza Hut",
            domain="pizzahut.com",
            owner=self.dana,
            arr_billed_at_account=Decimal("120000"),
        )
        self.burger = Customer.objects.create(
            organisation=self.org,
            name="Burger King",
            domain="bk.com",
            owner=self.eve,
            arr_billed_at_account=Decimal("80000"),
        )
        self.apac = Account.objects.create(name="APAC", domain="apac.pizzahut.com", owner=self.dana)
        self.apac.customers.add(self.pizza)
        self.e1 = Email.objects.create(
            customer=self.pizza,
            subject="Slack alerts",
            body="Can we get alerts in Slack?",
            sent_at=NOW,
            ai_category=AICategory.FEATURE_REQUEST,
            ai_classified_at=NOW,
        )
        self.t1 = Ticket.objects.create(
            customer=self.burger,
            ticket_number="T-1",
            title="Slack notifications please",
            priority=Ticket.Priority.MEDIUM,
            opened_at=NOW.date(),
            ai_category=AICategory.FEATURE_REQUEST,
            ai_classified_at=NOW,
        )
        self.e2 = Email.objects.create(
            account=self.apac,
            subject="CSV export",
            body="Need to export the dashboard to CSV.",
            sent_at=NOW,
            ai_category=AICategory.FEATURE_REQUEST,
            ai_classified_at=NOW,
        )
        Email.objects.create(
            customer=self.pizza,
            subject="Invoice",
            body="Where is my invoice?",
            sent_at=NOW,
            ai_category=AICategory.ACCOUNT_MANAGEMENT,
            ai_classified_at=NOW,
        )
        self.vectors = {
            "Slack alerts. Can we get alerts in Slack?": SLACK,
            "Slack notifications please (priority: medium, status: open)": SLACK_TOO,
            "CSV export. Need to export the dashboard to CSV.": EXPORT,
        }

    def gather(self, user=None):
        self.client.force_authenticate(user or self.alice)
        with (
            patch(EMBED, side_effect=fake_embed(self.vectors)),
            patch(COMPLETION, side_effect=[titled("Slack alerts"), titled("CSV export")]),
        ):
            return self.client.post(f"{URL}gather/", {}, format="json")


class Gather(Fixture):
    def test_groups_classified_asks_into_titled_requests_with_revenue(self):
        response = self.gather()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, {"created": 2, "linked": 3, "remaining": 0})
        rows = self.client.get(URL).data
        self.assertEqual([r["title"] for r in rows], ["Slack alerts", "CSV export"])
        slack, export = rows
        self.assertEqual(slack["companies"], 2)
        self.assertEqual(slack["interactions"], 2)
        self.assertEqual(Decimal(slack["arr"]), Decimal("200000"))
        # The account's ask counts its parent organisation's revenue.
        self.assertEqual(Decimal(export["arr"]), Decimal("120000"))
        self.assertEqual(export["companies"], 1)
        self.assertTrue(AuditEvent.objects.filter(action="request.create").count() == 2)

    def test_a_second_pass_links_new_asks_to_the_existing_request(self):
        self.gather()
        Email.objects.create(
            customer=self.burger,
            subject="Slack again",
            body="Any news on Slack alerts?",
            sent_at=timezone.now(),
            ai_category=AICategory.FEATURE_REQUEST,
            ai_classified_at=timezone.now(),
        )
        self.vectors["Slack again. Any news on Slack alerts?"] = [0.97, 0.24, 0.0]
        self.client.force_authenticate(self.alice)
        with patch(EMBED, side_effect=fake_embed(self.vectors)), patch(COMPLETION) as completion:
            response = self.client.post(f"{URL}gather/", {}, format="json")
        self.assertEqual(response.data, {"created": 0, "linked": 1, "remaining": 0})
        completion.assert_not_called()
        self.assertEqual(FeatureRequest.objects.get(title="Slack alerts").evidence.count(), 3)

    def test_needs_the_leadership_capability(self):
        self.client.force_authenticate(self.dana)
        response = self.client.post(f"{URL}gather/", {}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_a_spent_budget_keeps_what_was_done_and_says_so(self):
        self.client.force_authenticate(self.alice)
        with (
            patch(EMBED, side_effect=fake_embed(self.vectors)),
            patch(COMPLETION, side_effect=[titled("Slack alerts"), BudgetExceeded("spent")]),
        ):
            response = self.client.post(f"{URL}gather/", {}, format="json")
        self.assertEqual(response.status_code, 429, response.data)
        self.assertEqual(response.data["created"], 1)
        self.assertEqual(FeatureRequest.objects.count(), 1)

    def test_nightly_pass_gathers_for_every_organisation(self):
        from services.requests.gather import gather_nightly

        with (
            patch(EMBED, side_effect=fake_embed(self.vectors)),
            patch(COMPLETION, side_effect=[titled("Slack alerts"), titled("CSV export")]),
        ):
            self.assertEqual(gather_nightly(), 2)
        self.assertEqual(FeatureRequest.objects.count(), 2)


class Reading(Fixture):
    def setUp(self):
        super().setUp()
        self.gather()
        self.slack = FeatureRequest.objects.get(title="Slack alerts")

    def test_detail_lists_evidence_and_companies_the_reader_may_open(self):
        self.client.force_authenticate(self.alice)
        detail = self.client.get(f"{URL}{self.slack.id}/").data
        self.assertEqual(
            [(e["kind"], e["company"]["name"]) for e in detail["evidence"]],
            [("email", "Pizza Hut"), ("ticket", "Burger King")],
        )
        self.assertEqual([c["name"] for c in detail["companies"]], ["Pizza Hut", "Burger King"])
        self.assertIn("Can we get alerts", detail["evidence"][0]["snippet"])
        # Dana owns Pizza Hut only: the list and the detail shrink to her book.
        self.client.force_authenticate(self.dana)
        rows = self.client.get(URL).data
        slack = next(r for r in rows if r["id"] == self.slack.id)
        self.assertEqual(slack["companies"], 1)
        self.assertEqual(Decimal(slack["arr"]), Decimal("120000"))
        detail = self.client.get(f"{URL}{self.slack.id}/").data
        self.assertEqual([e["company"]["name"] for e in detail["evidence"]], ["Pizza Hut"])

    def test_status_filter_and_trend(self):
        self.client.force_authenticate(self.alice)
        self.client.patch(f"{URL}{self.slack.id}/", {"status": "planned"}, format="json")
        self.assertEqual(
            [r["title"] for r in self.client.get(f"{URL}?status=open").data], ["CSV export"]
        )
        row = self.client.get(f"{URL}?status=planned").data[0]
        # Both Slack asks are dated a week back, so they sit in the live window.
        self.assertEqual(row["last_90_days"], 2)
        self.assertEqual(row["previous_90_days"], 0)

    def test_another_tenant_sees_nothing(self):
        other = Organisation.objects.create(name="Other")
        outsider = User.objects.create_user(
            email="o@other.io", password="x", name="O", organisation=other, role=User.Role.ADMIN
        )
        self.client.force_authenticate(outsider)
        self.assertEqual(self.client.get(URL).data, [])
        self.assertEqual(self.client.get(f"{URL}{self.slack.id}/").status_code, 404)


class Curation(Fixture):
    def setUp(self):
        super().setUp()
        self.gather()
        self.slack = FeatureRequest.objects.get(title="Slack alerts")
        self.export = FeatureRequest.objects.get(title="CSV export")
        self.client.force_authenticate(self.alice)

    def test_status_owner_and_title_changes_are_audited(self):
        response = self.client.patch(
            f"{URL}{self.slack.id}/",
            {"status": "shipped", "owner": self.dana.id, "title": "Slack alerting"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "shipped")
        self.assertEqual(response.data["owner"]["name"], "Dana")
        event = AuditEvent.objects.get(action="request.update")
        self.assertEqual(event.metadata["status"], "shipped")

    def test_a_csm_cannot_curate(self):
        self.client.force_authenticate(self.dana)
        self.assertEqual(
            self.client.patch(
                f"{URL}{self.slack.id}/", {"status": "shipped"}, format="json"
            ).status_code,
            403,
        )

    def test_merge_moves_evidence_and_removes_the_source(self):
        response = self.client.post(
            f"{URL}{self.export.id}/merge/", {"into": self.slack.id}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(FeatureRequest.objects.filter(id=self.export.id).exists())
        self.assertEqual(self.slack.evidence.count(), 3)
        self.assertTrue(AuditEvent.objects.filter(action="request.merge").exists())
        self.assertEqual(
            self.client.post(
                f"{URL}{self.slack.id}/merge/", {"into": self.slack.id}, format="json"
            ).status_code,
            400,
        )

    def test_evidence_can_move_or_be_dismissed_and_stays_out_of_the_next_gather(self):
        evidence = RequestEvidence.objects.get(kind="ticket", record_id=self.t1.id)
        response = self.client.post(
            f"{URL}evidence/{evidence.id}/move/", {"request": self.export.id}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.export.evidence.count(), 2)
        response = self.client.post(f"{URL}evidence/{evidence.id}/dismiss/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.export.evidence.count(), 1)
        with patch(EMBED, side_effect=fake_embed(self.vectors)), patch(COMPLETION) as completion:
            response = self.client.post(f"{URL}gather/", {}, format="json")
        self.assertEqual(response.data["linked"], 0)
        completion.assert_not_called()

    def test_leadership_role_can_read_and_curate(self):
        lead = Role.objects.create(
            organisation=self.org,
            name="Lead",
            slug="lead",
            permissions=[Capability.VIEW_ALL_ACCOUNTS],
        )
        self.eve.role = lead
        self.eve.save()
        self.client.force_authenticate(self.eve)
        self.assertEqual(len(self.client.get(URL).data), 2)
        self.assertEqual(
            self.client.patch(
                f"{URL}{self.slack.id}/", {"status": "planned"}, format="json"
            ).status_code,
            200,
        )
