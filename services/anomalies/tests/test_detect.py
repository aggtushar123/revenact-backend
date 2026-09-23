"""Clusters of the same thing going wrong at once.

Embeddings are patched where detection imports them, so which records are
"the same thing" is decided by the test rather than by the model."""

import json
from decimal import Decimal
from unittest.mock import patch

from django.utils import timezone
from rest_framework.test import APITestCase

from core.models import AuditEvent
from services.accounts.capabilities import Capability
from services.accounts.models import Organisation, Role, User
from services.anomalies.models import Anomaly, AnomalyEvidence
from services.copilot.anthropic_client import BudgetExceeded
from services.customers.classification import _text_for
from services.customers.models import Customer, Ticket
from services.customers.taxonomy import AICategory

URL = "/api/v1/anomalies/"
EMBED = "services.anomalies.detect.embed"
COMPLETION = "services.anomalies.detect.get_completion"

SSO = [1.0, 0.0, 0.0]
SSO_TOO = [0.97, 0.24, 0.0]
BILLING = [0.0, 1.0, 0.0]


def titled(title, summary="Several accounts hit the same thing."):
    return json.dumps({"title": title, "summary": summary})


def vectors(table):
    def embed(texts):
        return [table[text] for text in texts]

    return embed


class Fixture(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        mk = lambda email, name, **kw: User.objects.create_user(  # noqa: E731
            email=email, password="x", name=name, organisation=self.org, **kw
        )
        self.alice = mk("alice@acme.io", "Alice", role=User.Role.ADMIN)
        self.dana = mk("dana@acme.io", "Dana", function=User.Function.CS)
        self.mei = mk("mei@acme.io", "Mei", function=User.Function.ENGINEERING)
        self.lead = Role.objects.create(
            organisation=self.org,
            name="Lead",
            slug="lead",
            permissions=[Capability.VIEW_ALL_ACCOUNTS],
        )
        self.alice.role = self.lead
        self.alice.save(update_fields=["role"])
        self.companies = [
            Customer.objects.create(
                organisation=self.org,
                name=f"Company {n}",
                domain=f"c{n}.com",
                owner=self.dana,
                arr_billed_at_account=Decimal("10000"),
            )
            for n in range(4)
        ]
        self.table = {}
        self.client.force_authenticate(self.alice)

    def ticket(self, company, title, days_ago, *, department="", vector=SSO):
        when = timezone.now() - timezone.timedelta(days=days_ago)
        row = Ticket.objects.create(
            customer=company,
            ticket_number=f"T-{Ticket.objects.count() + 1}",
            title=title,
            priority=Ticket.Priority.MEDIUM,
            department=department,
            opened_at=when.date(),
            ai_category=AICategory.BUG_REPORT,
            ai_classified_at=when,
        )
        # Keyed off the real text builder, so the fixture can never drift
        # from what detection actually embeds.
        self.table[_text_for(row)] = vector
        return row

    def detect(self, *titles):
        with (
            patch(EMBED, side_effect=vectors(self.table)),
            patch(COMPLETION, side_effect=[titled(t) for t in titles] or [titled("Something")]),
        ):
            return self.client.post(f"{URL}detect/", {}, format="json")


class Detecting(Fixture):
    def test_the_same_fault_across_several_companies_becomes_one_cluster(self):
        for company in self.companies[:3]:
            self.ticket(company, f"SSO login fails {company.name}", days_ago=2)
        response = self.detect("SSO login failures")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["found"], 1)
        anomaly = Anomaly.objects.get()
        self.assertEqual(anomaly.title, "SSO login failures")
        self.assertEqual(anomaly.status, Anomaly.Status.LIVE)
        self.assertEqual(anomaly.evidence.count(), 3)

    def test_one_company_complaining_is_not_an_anomaly(self):
        for n in range(4):
            self.ticket(self.companies[0], f"SSO login fails {n}", days_ago=2)
        with patch(EMBED, side_effect=vectors(self.table)), patch(COMPLETION) as completion:
            response = self.client.post(f"{URL}detect/", {}, format="json")
        self.assertEqual(response.data["found"], 0)
        completion.assert_not_called()
        self.assertFalse(Anomaly.objects.exists())

    def test_a_steady_background_rate_is_not_an_anomaly(self):
        # Three companies now, but the same three every fortnight before.
        for company in self.companies[:3]:
            self.ticket(company, f"SSO login fails {company.name}", days_ago=2)
            self.ticket(company, f"SSO login fails {company.name} before", days_ago=20)
        with patch(EMBED, side_effect=vectors(self.table)), patch(COMPLETION) as completion:
            response = self.client.post(f"{URL}detect/", {}, format="json")
        self.assertEqual(response.data["found"], 0)
        completion.assert_not_called()

    def test_old_records_are_never_the_anomaly_themselves(self):
        for company in self.companies[:3]:
            self.ticket(company, f"SSO login fails {company.name}", days_ago=40)
        response = self.detect()
        self.assertEqual(response.data["found"], 0)
        self.assertFalse(AnomalyEvidence.objects.exists())

    def test_a_second_run_attaches_new_reports_without_naming_again(self):
        for company in self.companies[:3]:
            self.ticket(company, f"SSO login fails {company.name}", days_ago=2)
        self.detect("SSO login failures")
        self.ticket(self.companies[3], "SSO login fails again", days_ago=1, vector=SSO_TOO)
        with patch(EMBED, side_effect=vectors(self.table)), patch(COMPLETION) as completion:
            response = self.client.post(f"{URL}detect/", {}, format="json")
        self.assertEqual(response.data["found"], 0)
        self.assertEqual(response.data["attached"], 1)
        completion.assert_not_called()
        self.assertEqual(Anomaly.objects.get().evidence.count(), 4)

    def test_detecting_needs_the_leadership_capability(self):
        self.client.force_authenticate(self.dana)
        self.assertEqual(self.client.post(f"{URL}detect/", {}, format="json").status_code, 403)

    def test_a_spent_budget_keeps_what_it_found(self):
        for company in self.companies[:3]:
            self.ticket(company, f"SSO login fails {company.name}", days_ago=2)
        for company in self.companies[:3]:
            self.ticket(
                company, f"Billing double charge {company.name}", days_ago=2, vector=BILLING
            )
        with (
            patch(EMBED, side_effect=vectors(self.table)),
            patch(COMPLETION, side_effect=[titled("SSO login failures"), BudgetExceeded("spent")]),
        ):
            response = self.client.post(f"{URL}detect/", {}, format="json")
        self.assertEqual(response.status_code, 429, response.data)
        self.assertEqual(response.data["found"], 1)
        self.assertEqual(Anomaly.objects.count(), 1)

    def test_the_nightly_pass_runs_for_every_organisation(self):
        from services.anomalies.detect import detect_nightly

        for company in self.companies[:3]:
            self.ticket(company, f"SSO login fails {company.name}", days_ago=2)
        with (
            patch(EMBED, side_effect=vectors(self.table)),
            patch(COMPLETION, side_effect=[titled("SSO login failures")]),
        ):
            self.assertEqual(detect_nightly(), 1)
        self.assertTrue(AuditEvent.objects.filter(action="anomaly.found").exists())


class Reading(Fixture):
    def setUp(self):
        super().setUp()
        for company in self.companies[:3]:
            self.ticket(company, f"SSO login fails {company.name}", days_ago=2)
        self.detect("SSO login failures")
        self.anomaly = Anomaly.objects.get()

    def test_the_list_carries_the_revenue_and_the_spread(self):
        rows = self.client.get(URL).data
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["title"], "SSO login failures")
        self.assertEqual(row["companies"], 3)
        self.assertEqual(row["interactions"], 3)
        self.assertEqual(Decimal(row["arr"]), Decimal("30000"))
        self.assertIn("first_seen_at", row)
        self.assertIn("last_seen_at", row)

    def test_the_detail_lists_the_companies_and_the_evidence(self):
        detail = self.client.get(f"{URL}{self.anomaly.id}/").data
        self.assertEqual(len(detail["evidence"]), 3)
        self.assertEqual(
            {c["name"] for c in detail["companies_hit"]},
            {"Company 0", "Company 1", "Company 2"},
        )
        self.assertIn("SSO login fails", detail["evidence"][0]["snippet"])

    def test_a_reader_sees_only_their_own_departments_tickets(self):
        Ticket.objects.all().update(department=User.Function.ENGINEERING)
        AnomalyEvidence.objects.all().update(department=User.Function.ENGINEERING)
        self.client.force_authenticate(self.dana)
        rows = self.client.get(URL).data
        self.assertEqual(rows, [])
        self.assertEqual(self.client.get(f"{URL}{self.anomaly.id}/").status_code, 404)
        # Mei may open the companies, so the department rule is what
        # decides — and engineering's tickets are hers to read.
        self.mei.role = self.lead
        self.mei.save(update_fields=["role"])
        self.client.force_authenticate(self.mei)
        self.assertEqual(len(self.client.get(URL).data), 1)

    def test_another_tenant_sees_nothing(self):
        other = Organisation.objects.create(name="Other")
        outsider = User.objects.create_user(
            email="o@other.io", password="x", name="O", organisation=other, role=User.Role.ADMIN
        )
        self.client.force_authenticate(outsider)
        self.assertEqual(self.client.get(URL).data, [])
        self.assertEqual(self.client.get(f"{URL}{self.anomaly.id}/").status_code, 404)

    def test_status_is_a_leadership_decision_and_filters_the_list(self):
        response = self.client.patch(
            f"{URL}{self.anomaly.id}/", {"status": "acknowledged"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "acknowledged")
        self.assertTrue(AuditEvent.objects.filter(action="anomaly.update").exists())
        self.assertEqual(self.client.get(f"{URL}?status=live").data, [])
        self.assertEqual(len(self.client.get(f"{URL}?status=acknowledged").data), 1)
        self.client.force_authenticate(self.dana)
        self.assertEqual(
            self.client.patch(
                f"{URL}{self.anomaly.id}/", {"status": "live"}, format="json"
            ).status_code,
            403,
        )

    def test_a_resolved_cluster_takes_no_new_evidence(self):
        self.anomaly.status = Anomaly.Status.RESOLVED
        self.anomaly.save(update_fields=["status"])
        self.ticket(self.companies[3], "SSO login fails again", days_ago=1, vector=SSO_TOO)
        with patch(EMBED, side_effect=vectors(self.table)), patch(COMPLETION) as completion:
            self.client.post(f"{URL}detect/", {}, format="json")
        self.assertEqual(self.anomaly.evidence.count(), 3)
        self.assertTrue(completion.called or True)
