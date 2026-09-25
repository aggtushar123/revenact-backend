"""Unit tier: build_org_context_summary, no HTTP. The real embedding
model that backs the semantic company-match fallback (see
test_embeddings.py's own real coverage, test_retrieval.py's own mocked
logic coverage) is mocked here too — these tests are about this
module's own aggregation/formatting logic, not embedding quality."""

from unittest.mock import patch

from django.test import TestCase

from services.accounts.models import Organisation, User
from services.copilot.context import build_grounding, build_org_context_summary
from services.customers.models import Account, Customer, Note, Opportunity, Risk, Ticket
from services.knowledge.models import FunctionOwner


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
            summary,
            "You own no customers or accounts yourself; answering from what the company "
            "knows about its customers.",
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

    def test_a_churned_but_unarchived_owned_customer_is_excluded_from_the_digest(self):
        # Churn and archive are separate actions (see
        # services.customers.scoping.live_customers's own docstring): a
        # churned customer can still be unarchived and still owned, but it
        # is no longer "my book" and must not inflate ARR/health/at-risk.
        Customer.objects.create(
            organisation=self.org,
            name="Globex",
            owner=self.user,
            health_score="9.0",
        )
        churned = Customer.objects.create(
            organisation=self.org,
            name="Churned Co",
            owner=self.user,
            health_score="1.0",
            churn_date="2026-01-01",
        )

        summary = build_org_context_summary(self.org, self.user)

        self.assertIn("Your customers: 1 total", summary)
        self.assertNotIn(churned.name, summary)

    def test_a_customer_no_longer_owned_or_function_owned_drops_out_of_the_book(self):
        # Reassigning the customer away removes it from Carl's own book on
        # this same request, not just from some cached snapshot — distinct
        # from company *matching*, which stays organisation-wide by design
        # (see context.py's own docstring and
        # test_the_copilot_grounds_only_in_what_the_asker_may_see in
        # services.knowledge — the company can still be named and asked
        # about; it just no longer counts as "my book").
        reassigned = Customer.objects.create(
            organisation=self.org, name="Reassigned Co", owner=self.user, health_score="4.0"
        )
        reassigned.owner = self.other_user
        reassigned.save()

        summary = build_org_context_summary(self.org, self.user)

        self.assertIn("You own no customers or accounts yourself", summary)
        self.assertNotIn(reassigned.name, summary)

    def test_an_owned_live_customer_still_counts(self):
        Customer.objects.create(
            organisation=self.org, name="Globex", owner=self.user, health_score="8.0"
        )

        summary = build_org_context_summary(self.org, self.user)

        self.assertIn("Your customers: 1 total", summary)
        self.assertIn("Globex", summary)

    def test_a_function_owned_customer_still_counts(self):
        # Owned by someone else, but Carl is the function owner for CS on
        # it — "your customers" means owned *or* responsible in your
        # function (docs/API_CONTRACTS.md), not only Customer.owner.
        customer = Customer.objects.create(
            organisation=self.org, name="Initech", owner=self.other_user, health_score="7.0"
        )
        FunctionOwner.objects.create(customer=customer, function=User.Function.CS, user=self.user)

        summary = build_org_context_summary(self.org, self.user)

        self.assertIn("Your customers: 1 total", summary)
        self.assertIn("Initech", summary)

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


class GroundingSourcesTests(TestCase):
    """`build_grounding` returns the digest *and* the records it quoted.

    Until it did, retrieval formatted records into a prompt string and
    threw away which records they were, so "where did that come from?"
    had no answer.
    """

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="carl@acme.io", password="supersecret1", name="Carl", organisation=self.org
        )
        self.customer = Customer.objects.create(
            organisation=self.org, name="Globex", owner=self.user, health_score="3.0"
        )

    def test_a_book_with_no_content_cites_nothing(self):
        grounding = build_grounding(self.org, self.user)

        self.assertEqual(grounding.sources, [])
        self.assertIn("Globex", grounding.summary)

    def test_a_quoted_note_comes_back_as_a_citation(self):
        Note.objects.create(
            customer=self.customer,
            title="Commercial Negotiation Summary",
            author_name="Edgar Holmes",
            body="Customer requested 15% discount for a 3-year commitment.",
            logged_at="2026-03-15",
        )

        grounding = build_grounding(self.org, self.user)

        self.assertEqual(len(grounding.sources), 1)
        source = grounding.sources[0]
        self.assertEqual(source["type"], "note")
        self.assertEqual(source["label"], "Commercial Negotiation Summary")
        self.assertEqual(source["company"], "Globex")
        self.assertEqual(source["company_type"], "customer")
        self.assertEqual(source["date"], "2026-03-15")

    def test_citations_carry_an_id_so_the_ui_can_link_to_the_record(self):
        note = Note.objects.create(
            customer=self.customer,
            title="Renewal Strategy",
            author_name="Carl",
            body="Multi-year preferred.",
            logged_at="2026-03-15",
        )

        grounding = build_grounding(self.org, self.user)

        self.assertEqual(grounding.sources[0]["id"], note.id)
        self.assertEqual(grounding.sources[0]["company_id"], self.customer.id)

    def test_every_cited_record_also_appears_in_the_digest(self):
        """The invariant that makes a citation trustworthy: sources are
        collected from exactly the items appended to the prompt, so an
        answer can't cite something the model never saw.

        Three notes exist but only two are cited — with no company named
        in the question, each at-risk company contributes at most two
        items. The point is that cited and quoted stay in lockstep, not
        that everything gets cited."""
        for n in range(3):
            Note.objects.create(
                customer=self.customer,
                title=f"Note {n}",
                author_name="Carl",
                body="Body.",
                logged_at="2026-03-15",
            )

        grounding = build_grounding(self.org, self.user)

        self.assertEqual(len(grounding.sources), 2)
        for source in grounding.sources:
            self.assertIn(source["label"], grounding.summary)

    def test_an_account_source_is_labelled_as_an_account(self):
        # Named distinctly so the exact-match pass picks the account
        # rather than the customer — with no company named in the
        # question, retrieval only walks the top at-risk *customers*,
        # so an account's records are reached by naming it.
        account = Account.objects.create(name="Northwind Division", owner=self.user)
        account.customers.add(self.customer)
        Note.objects.create(
            customer=None,
            account=account,
            title="EMEA rollout",
            author_name="Carl",
            body="Three offices live.",
            logged_at="2026-04-01",
        )

        sources = build_grounding(
            self.org, self.user, query="How is Northwind Division doing?"
        ).sources

        emea = next(s for s in sources if s["company"] == "Northwind Division")
        self.assertEqual(emea["company_type"], "account")
        self.assertEqual(emea["company_id"], account.id)

    def test_the_old_summary_only_helper_still_returns_a_string(self):
        """~20 existing tests call it — it's a one-line wrapper now, not
        a second implementation."""
        summary = build_org_context_summary(self.org, self.user)

        self.assertIsInstance(summary, str)
        self.assertEqual(summary, build_grounding(self.org, self.user).summary)
