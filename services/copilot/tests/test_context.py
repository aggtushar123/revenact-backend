"""Unit tier: build_org_context_summary, no HTTP."""

from django.test import TestCase

from services.accounts.models import Organisation, User
from services.copilot.context import build_org_context_summary
from services.customers.models import Account, Customer, Opportunity, Risk, Ticket


class BuildOrgContextSummaryTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="carl@acme.io", password="supersecret1", name="Carl", organisation=self.org
        )
        self.other_user = User.objects.create_user(
            email="dana@acme.io", password="supersecret1", name="Dana", organisation=self.org
        )

    def test_nothing_owned_yet(self):
        summary = build_org_context_summary(self.org, self.user)
        self.assertEqual(
            summary, "You don't own any customers or accounts yet — nothing to summarize."
        )

    def test_includes_real_health_and_lifecycle_data_for_owned_customers_only(self):
        Customer.objects.create(
            organisation=self.org,
            name="Globex",
            owner=self.user,
            health_score="2.0",
            lifecycle_stage=Customer.LifecycleStage.RENEWAL,
            nps_score=-10,
        )
        Customer.objects.create(
            organisation=self.org,
            name="Initech",
            owner=self.user,
            health_score="9.0",
            lifecycle_stage=Customer.LifecycleStage.LIVE,
        )
        # Owned by someone else — shouldn't appear in Carl's own summary.
        Customer.objects.create(
            organisation=self.org, name="Not Carl's", owner=self.other_user, health_score="9.0"
        )
        archived = Customer.objects.create(
            organisation=self.org,
            name="Ghost Co",
            owner=self.user,
            is_archived=True,
            health_score="1.0",
        )

        summary = build_org_context_summary(self.org, self.user)

        self.assertIn("Your customers: 2 total", summary)
        self.assertIn("Globex", summary)
        self.assertIn("renewal", summary)
        self.assertIn("live", summary)
        self.assertNotIn("Ghost Co", summary)
        self.assertNotIn(archived.name, summary)
        self.assertNotIn("Not Carl's", summary)

    def test_includes_owned_accounts_even_with_no_owned_customers(self):
        someone_elses_customer = Customer.objects.create(
            organisation=self.org, name="Parent Co", owner=self.other_user
        )
        account = Account.objects.create(name="My Account", owner=self.user, health_score="8.0")
        account.customers.add(someone_elses_customer)

        summary = build_org_context_summary(self.org, self.user)

        self.assertIn("don't own any customers directly", summary)
        self.assertIn("Your accounts: 1 total", summary)

    def test_includes_open_pipeline_counts_for_owned_companies_only(self):
        customer = Customer.objects.create(organisation=self.org, name="Globex", owner=self.user)
        other_customer = Customer.objects.create(
            organisation=self.org, name="Not Carl's", owner=self.other_user
        )
        Opportunity.objects.create(
            customer=customer, title="Upsell", stage=Opportunity.Stage.DISCOVERY
        )
        Opportunity.objects.create(
            customer=customer, title="Won deal", stage=Opportunity.Stage.CLOSED_WON
        )
        Opportunity.objects.create(
            customer=other_customer, title="Not mine", stage=Opportunity.Stage.DISCOVERY
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

        summary = build_org_context_summary(self.org, self.user)

        self.assertIn("Your pipeline: 1 open opportunities", summary)
        self.assertIn("1 open risks", summary)
        self.assertIn("1 open tickets", summary)
