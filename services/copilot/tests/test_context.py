"""Unit tier: build_org_context_summary, no HTTP."""

from django.test import TestCase

from services.accounts.models import Organisation
from services.copilot.context import build_org_context_summary
from services.customers.models import Customer, Opportunity, Risk, Ticket


class BuildOrgContextSummaryTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")

    def test_no_customers_yet(self):
        summary = build_org_context_summary(self.org)
        self.assertEqual(summary, "This organisation has no customers on record yet.")

    def test_includes_real_health_and_lifecycle_data(self):
        Customer.objects.create(
            organisation=self.org,
            name="Globex",
            health_score="2.0",
            lifecycle_stage=Customer.LifecycleStage.RENEWAL,
            nps_score=-10,
        )
        Customer.objects.create(
            organisation=self.org,
            name="Initech",
            health_score="9.0",
            lifecycle_stage=Customer.LifecycleStage.LIVE,
        )
        archived = Customer.objects.create(
            organisation=self.org, name="Ghost Co", is_archived=True, health_score="1.0"
        )

        summary = build_org_context_summary(self.org)

        self.assertIn("Customers: 2 total", summary)
        self.assertIn("Globex", summary)
        self.assertIn("renewal", summary)
        self.assertIn("live", summary)
        self.assertNotIn("Ghost Co", summary)
        self.assertNotIn(archived.name, summary)

    def test_includes_open_pipeline_counts(self):
        customer = Customer.objects.create(organisation=self.org, name="Globex")
        Opportunity.objects.create(
            customer=customer, title="Upsell", stage=Opportunity.Stage.DISCOVERY
        )
        Opportunity.objects.create(
            customer=customer, title="Won deal", stage=Opportunity.Stage.CLOSED_WON
        )
        Risk.objects.create(customer=customer, title="Churn risk", stage=Risk.Stage.OPEN)
        Ticket.objects.create(
            customer=customer,
            ticket_number="TKT-1",
            title="Bug",
            assignee_name="Support",
            status=Ticket.Status.OPEN,
            priority=Ticket.Priority.HIGH,
            opened_at="2026-01-01",
        )

        summary = build_org_context_summary(self.org)

        self.assertIn("1 open opportunities", summary)
        self.assertIn("1 open risks", summary)
        self.assertIn("1 open tickets", summary)
