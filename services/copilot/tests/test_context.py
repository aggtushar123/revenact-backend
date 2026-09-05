"""Unit tier: build_org_context_summary, no HTTP. The real embedding
model that backs the semantic company-match fallback (see
test_embeddings.py's own real coverage, test_retrieval.py's own mocked
logic coverage) is mocked here too — these tests are about this
module's own aggregation/formatting logic, not embedding quality."""

from unittest.mock import patch

from django.test import TestCase

from services.accounts.models import Organisation, User
from services.copilot.context import build_org_context_summary
from services.customers.models import Account, Customer, Note, Opportunity, Risk, Ticket


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

    def test_a_company_named_in_the_query_gets_its_own_real_retrieval(self):
        Customer.objects.create(organisation=self.org, name="Globex", owner=self.user)
        Note.objects.create(
            customer=Customer.objects.get(name="Globex"),
            title="Champion left",
            author_name="Carl",
            body="No replacement identified yet.",
            logged_at="2026-09-01",
        )

        summary = build_org_context_summary(self.org, self.user, query="Why is Globex at risk?")

        self.assertIn("Recent real communications for Globex (named in the question)", summary)
        self.assertIn("Champion left", summary)

    def test_no_company_named_or_semantically_matched_falls_back_to_a_slice_per_at_risk_customer(
        self,
    ):
        risky = Customer.objects.create(
            organisation=self.org, name="Globex", owner=self.user, health_score="1.0"
        )
        Note.objects.create(
            customer=risky,
            title="Champion left",
            author_name="Carl",
            body="x",
            logged_at="2026-09-01",
        )

        with patch("services.copilot.context.find_relevant_company_semantic", return_value=None):
            summary = build_org_context_summary(
                self.org, self.user, query="How is my book doing overall?"
            )

        self.assertIn("Recent real communications for Globex:", summary)
        self.assertNotIn("named in the question", summary)

    def test_a_semantic_match_gets_its_own_retrieval_with_a_distinct_label(self):
        # e.g. "that food delivery account struggling" finding Pizza Hut
        # without naming it — see retrieval.py's own real, verified
        # example. The semantic call itself is mocked here (see
        # test_retrieval.py's own coverage of the real threshold logic).
        globex = Customer.objects.create(organisation=self.org, name="Globex", owner=self.user)
        Note.objects.create(
            customer=globex,
            title="Champion left",
            author_name="Carl",
            body="x",
            logged_at="2026-09-01",
        )

        with patch("services.copilot.context.find_relevant_company_semantic", return_value=globex):
            summary = build_org_context_summary(
                self.org, self.user, query="that account without a champion"
            )

        self.assertIn(
            "Recent real communications for Globex (the account your question seems to be about):",
            summary,
        )
        self.assertIn("Champion left", summary)

    def test_no_query_at_all_still_falls_back_to_the_per_at_risk_slice(self):
        # `query` defaults to "" (no other real caller omits it — see
        # SendMessageView, which always has a validated non-empty
        # content) — an empty query behaves exactly like one that
        # doesn't name a company, not like a special "skip retrieval"
        # mode. No query also means find_relevant_company_semantic is
        # never even called (see its own early-return on empty query),
        # so nothing needs mocking here.
        risky = Customer.objects.create(
            organisation=self.org, name="Globex", owner=self.user, health_score="1.0"
        )
        Note.objects.create(
            customer=risky,
            title="Champion left",
            author_name="Carl",
            body="x",
            logged_at="2026-09-01",
        )

        summary = build_org_context_summary(self.org, self.user)

        self.assertIn("Recent real communications for Globex:", summary)
