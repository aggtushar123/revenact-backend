"""Unit tier: find_mentioned_company/retrieve_recent_communications, no HTTP."""

from django.test import TestCase

from services.accounts.models import Organisation
from services.copilot.retrieval import find_mentioned_company, retrieve_recent_communications
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
