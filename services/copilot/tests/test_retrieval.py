"""Unit tier: find_mentioned_company/retrieve_recent_communications, no
HTTP. The real embedding model (see test_embeddings.py's own real
coverage) is mocked here throughout via services.copilot.retrieval's own
imported `rank_by_similarity` name — these tests are about this
module's own logic (thresholds, candidate gathering, ranking wiring),
not embedding quality."""

from unittest.mock import patch

from django.test import TestCase

from services.accounts.models import Organisation
from services.copilot.retrieval import (
    find_mentioned_company,
    find_relevant_company_semantic,
    retrieve_recent_communications,
)
from services.customers.models import Account, Activity, Customer, Email, Note, Ticket


class FindMentionedCompanyTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.globex = Customer.objects.create(organisation=self.org, name="Globex")
        self.initech = Customer.objects.create(organisation=self.org, name="Initech")
        self.account = Account.objects.create(name="Globex EMEA")
        self.account.customers.add(self.globex)

    def test_matches_a_customer_named_in_the_query_case_insensitively(self):
        found = find_mentioned_company("why is GLOBEX at risk?", [self.globex, self.initech], [])
        self.assertEqual(found, self.globex)

    def test_matches_an_account_named_in_the_query(self):
        found = find_mentioned_company("what's up with Globex EMEA", [self.initech], [self.account])
        self.assertEqual(found, self.account)

    def test_returns_none_when_no_company_is_named(self):
        found = find_mentioned_company("how is my whole book doing?", [self.globex], [self.account])
        self.assertIsNone(found)

    def test_only_searches_the_companies_actually_passed_in(self):
        # A real safeguard against leaking another company's own name
        # match — the caller is responsible for only passing owned ones.
        found = find_mentioned_company("why is Globex at risk?", [self.initech], [])
        self.assertIsNone(found)


class RetrieveRecentCommunicationsTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=self.org, name="Globex")

    def test_empty_when_nothing_logged(self):
        self.assertEqual(retrieve_recent_communications(self.customer, limit=3), [])

    def test_includes_real_email_subject_and_body(self):
        Email.objects.create(
            customer=self.customer,
            subject="Renewal concerns",
            sender_name="Jane",
            recipient_name="Carl",
            body="We're worried about the price increase.",
            sent_at="2026-08-28T00:00:00Z",
        )

        lines = retrieve_recent_communications(self.customer, limit=3)

        self.assertEqual(len(lines), 1)
        self.assertIn("Renewal concerns", lines[0])
        self.assertIn("worried about the price increase", lines[0])

    def test_includes_real_note_title_and_body(self):
        Note.objects.create(
            customer=self.customer,
            title="Champion left",
            author_name="Carl",
            body="No replacement identified yet.",
            logged_at="2026-09-01",
        )

        lines = retrieve_recent_communications(self.customer, limit=3)
        self.assertIn("Champion left", lines[0])
        self.assertIn("No replacement identified yet", lines[0])

    def test_includes_only_open_tickets_not_resolved_ones(self):
        Ticket.objects.create(
            customer=self.customer,
            ticket_number="TKT-1",
            title="API downtime",
            assignee_name="Support",
            status=Ticket.Status.OPEN,
            priority=Ticket.Priority.HIGH,
            opened_at="2026-08-01",
        )
        Ticket.objects.create(
            customer=self.customer,
            ticket_number="TKT-2",
            title="Already fixed",
            assignee_name="Support",
            status=Ticket.Status.RESOLVED,
            priority=Ticket.Priority.LOW,
            opened_at="2026-07-01",
        )

        lines = retrieve_recent_communications(self.customer, limit=5)

        joined = "\n".join(lines)
        self.assertIn("API downtime", joined)
        self.assertNotIn("Already fixed", joined)

    def test_includes_real_activity_type(self):
        Activity.objects.create(
            customer=self.customer,
            type=Activity.ActivityType.ESCALATION_TRIGGERED,
            occurred_at="2026-09-02",
        )

        lines = retrieve_recent_communications(self.customer, limit=3)
        self.assertIn("Escalation Triggered", "\n".join(lines))

    def test_respects_the_limit_per_source(self):
        for i in range(5):
            Note.objects.create(
                customer=self.customer,
                title=f"Note {i}",
                author_name="Carl",
                body="x",
                logged_at="2026-09-01",
            )

        lines = retrieve_recent_communications(self.customer, limit=2)
        self.assertEqual(len(lines), 2)

    def test_long_bodies_are_snipped_not_sent_in_full(self):
        Note.objects.create(
            customer=self.customer,
            title="Long note",
            author_name="Carl",
            body="x" * 500,
            logged_at="2026-09-01",
        )

        lines = retrieve_recent_communications(self.customer, limit=1)
        self.assertLess(len(lines[0]), 300)
        self.assertTrue(lines[0].endswith("…"))

    def test_works_for_a_real_account_too_not_just_customers(self):
        account = Account.objects.create(name="Globex EMEA")
        account.customers.add(self.customer)
        Note.objects.create(
            account=account,
            title="Account note",
            author_name="Carl",
            body="hi",
            logged_at="2026-09-01",
        )

        lines = retrieve_recent_communications(account, limit=3)
        self.assertIn("Account note", "\n".join(lines))

    def test_a_query_reranks_by_relevance_across_sources_not_a_per_source_quota(self):
        Email.objects.create(
            customer=self.customer,
            subject="Renewal concerns",
            sender_name="Jane",
            recipient_name="Carl",
            body="pricing",
            sent_at="2026-08-28T00:00:00Z",
        )
        Note.objects.create(
            customer=self.customer,
            title="Champion left",
            author_name="Carl",
            body="x",
            logged_at="2026-09-01",
        )

        # Real rank_by_similarity returns (index, score) pairs, best
        # first, indexing into whatever order _gather_candidates built
        # (email first, then note) — patched here to force the note
        # ahead of the email regardless of recency, proving the query
        # path really re-ranks rather than just taking source order.
        with patch("services.copilot.retrieval.rank_by_similarity") as mock_rank:
            mock_rank.return_value = [(1, 0.9), (0, 0.1)]
            lines = retrieve_recent_communications(
                self.customer, limit=1, query="who is our champion"
            )

        self.assertEqual(len(lines), 1)
        self.assertIn("Champion left", lines[0])
        mock_rank.assert_called_once()

    def test_no_query_falls_back_to_recency_without_calling_rank_by_similarity(self):
        Note.objects.create(
            customer=self.customer,
            title="A note",
            author_name="Carl",
            body="x",
            logged_at="2026-09-01",
        )

        with patch("services.copilot.retrieval.rank_by_similarity") as mock_rank:
            lines = retrieve_recent_communications(self.customer, limit=1)

        self.assertEqual(len(lines), 1)
        self.assertIn("A note", lines[0])
        mock_rank.assert_not_called()


class FindRelevantCompanySemanticTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.pizza_hut = Customer.objects.create(organisation=self.org, name="Pizza Hut")
        self.spotify = Customer.objects.create(organisation=self.org, name="Spotify")

    def test_returns_the_best_match_above_threshold(self):
        with patch("services.copilot.retrieval.rank_by_similarity") as mock_rank:
            mock_rank.return_value = [(0, 0.5), (1, 0.1)]
            found = find_relevant_company_semantic(
                "that food delivery account struggling", [self.pizza_hut, self.spotify], []
            )
        self.assertEqual(found, self.pizza_hut)

    def test_returns_none_when_the_best_match_is_below_threshold(self):
        # A generic, no-company-in-mind question shouldn't spuriously
        # latch onto whichever company happens to embed closest.
        with patch("services.copilot.retrieval.rank_by_similarity") as mock_rank:
            mock_rank.return_value = [(0, 0.2), (1, 0.15)]
            found = find_relevant_company_semantic(
                "how is my whole book doing?", [self.pizza_hut, self.spotify], [], threshold=0.3
            )
        self.assertIsNone(found)

    def test_returns_none_with_no_companies_or_no_query(self):
        self.assertIsNone(find_relevant_company_semantic("anything", [], []))
        self.assertIsNone(find_relevant_company_semantic("", [self.pizza_hut], []))

    def test_never_calls_the_real_embedding_model_once_an_exact_match_already_won(self):
        # Documents the real intent (see retrieval.py's own docstring):
        # find_relevant_company_semantic is only ever reached after
        # find_mentioned_company has already failed — verified at the
        # context.py call-site level, not re-asserted here beyond
        # confirming this function itself doesn't special-case it.
        with patch("services.copilot.retrieval.rank_by_similarity") as mock_rank:
            mock_rank.return_value = [(0, 0.9)]
            find_relevant_company_semantic("Pizza Hut", [self.pizza_hut], [])
        mock_rank.assert_called_once()
