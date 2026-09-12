"""Unit tier: model logic in isolation, no HTTP."""

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from services.accounts.models import Organisation
from services.connectors.models import Connector
from services.customers.models import (
    Account,
    Activity,
    CalendarEvent,
    Canvas,
    Contact,
    Customer,
    Email,
    Headline,
    HealthSnapshot,
    Note,
    Opportunity,
    Risk,
    Survey,
    Task,
    Ticket,
    capture_health_snapshot,
    csat_band,
)


def create_account(customer, **kwargs):
    """Account.customers is a many-to-many now (see that model's own
    docstring), so `Account.objects.create(customer=...)` no longer
    works — this is the test-suite's own equivalent, linking `customer`
    onto the new Account right after creating it."""
    account = Account.objects.create(**kwargs)
    account.customers.add(customer)
    return account


class HealthCategoryTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")

    def _customer(self, score):
        return Customer.objects.create(organisation=self.org, name="Some Co", health_score=score)

    def test_score_at_or_above_7_is_good(self):
        self.assertEqual(self._customer(7.0).health_category, Customer.HealthCategory.GOOD)
        self.assertEqual(self._customer(9.9).health_category, Customer.HealthCategory.GOOD)

    def test_score_4_to_6point9_is_average(self):
        self.assertEqual(self._customer(4.0).health_category, Customer.HealthCategory.AVERAGE)
        self.assertEqual(self._customer(6.9).health_category, Customer.HealthCategory.AVERAGE)

    def test_score_below_4_is_poor(self):
        self.assertEqual(self._customer(0.0).health_category, Customer.HealthCategory.POOR)
        self.assertEqual(self._customer(3.9).health_category, Customer.HealthCategory.POOR)


class CustomerCurrencyTests(TestCase):
    """The model-level default (USD) — see CustomerSerializer.create()'s
    own test coverage in test_views.py for the "defaults to the org's
    own currency" behavior, which only applies going through the API,
    not a direct ORM .objects.create() like these."""

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")

    def test_defaults_to_usd(self):
        customer = Customer.objects.create(organisation=self.org, name="Some Co")
        self.assertEqual(customer.currency, "USD")

    def test_can_be_set_independently_of_the_orgs_own_currency(self):
        customer = Customer.objects.create(organisation=self.org, name="Some Co", currency="EUR")
        self.assertEqual(customer.currency, "EUR")
        self.assertEqual(self.org.currency, "USD")


class SeatUtilizationTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")

    def _customer(self, contracted, active):
        return Customer.objects.create(
            organisation=self.org,
            name="Some Co",
            total_contracted_seats=contracted,
            total_active_seats=active,
        )

    def test_computed_from_active_and_contracted_seats(self):
        customer = self._customer(contracted=560, active=471)
        self.assertEqual(customer.seat_utilization_percentage, 84.11)

    def test_none_when_no_contracted_seats_recorded(self):
        customer = Customer.objects.create(organisation=self.org, name="Some Co")
        self.assertIsNone(customer.seat_utilization_percentage)

    def test_none_when_contracted_seats_is_zero(self):
        customer = self._customer(contracted=0, active=0)
        self.assertIsNone(customer.seat_utilization_percentage)


class AccountHealthCategoryTests(TestCase):
    """Account.health_category reuses Customer.HEALTH_THRESHOLDS directly
    (see Account model's docstring) — same thresholds, same behavior,
    just pinned down again here in case that ever drifts."""

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=org, name="Some Co")

    def _account(self, score):
        return create_account(self.customer, name="Some Region", health_score=score)

    def test_score_at_or_above_7_is_good(self):
        self.assertEqual(self._account(7.0).health_category, Customer.HealthCategory.GOOD)
        self.assertEqual(self._account(9.9).health_category, Customer.HealthCategory.GOOD)

    def test_score_4_to_6point9_is_average(self):
        self.assertEqual(self._account(4.0).health_category, Customer.HealthCategory.AVERAGE)
        self.assertEqual(self._account(6.9).health_category, Customer.HealthCategory.AVERAGE)

    def test_score_below_4_is_poor(self):
        self.assertEqual(self._account(0.0).health_category, Customer.HealthCategory.POOR)
        self.assertEqual(self._account(3.9).health_category, Customer.HealthCategory.POOR)


class ContactInfoDefaultsTests(TestCase):
    """email/phone (Customer) and address/email/phone (Account) back
    ActivityFeed's Overview tab. All blank=True, defaulting to "" —
    the frontend's own mapAccountToAccountRow.ts is what falls an
    Account's blank value back to its parent Customer's, not this
    model, so there's nothing to pin down here beyond "blank by
    default", same as domain already was."""

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")

    def test_customer_email_and_phone_default_to_blank(self):
        customer = Customer.objects.create(organisation=self.org, name="Some Co")
        self.assertEqual(customer.email, "")
        self.assertEqual(customer.phone, "")

    def test_account_address_email_and_phone_default_to_blank(self):
        customer = Customer.objects.create(organisation=self.org, name="Some Co")
        account = create_account(customer, name="Some Region")
        self.assertEqual(account.address, "")
        self.assertEqual(account.email, "")
        self.assertEqual(account.phone, "")


class ActivityParentConstraintTests(TestCase):
    """An Activity belongs to exactly one of customer/account — enforced
    by a DB CheckConstraint (see the model's own docstring for why
    that's at the DB level rather than serializer validation: there's
    no create/update endpoint yet to run the latter through)."""

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=org, name="Some Co")
        self.account = create_account(self.customer, name="Some Region")

    def test_customer_only_is_valid(self):
        activity = Activity.objects.create(
            customer=self.customer,
            type=Activity.ActivityType.HEALTH_CHECK_REVIEW,
            occurred_at="2026-03-05",
        )
        self.assertIsNone(activity.account)

    def test_account_only_is_valid(self):
        activity = Activity.objects.create(
            account=self.account,
            type=Activity.ActivityType.HEALTH_CHECK_REVIEW,
            occurred_at="2026-03-05",
        )
        self.assertIsNone(activity.customer)

    def test_neither_parent_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Activity.objects.create(type=Activity.ActivityType.OTHER, occurred_at="2026-03-05")

    def test_both_parents_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Activity.objects.create(
                customer=self.customer,
                account=self.account,
                type=Activity.ActivityType.OTHER,
                occurred_at="2026-03-05",
            )


class EmailParentConstraintTests(TestCase):
    """Same "exactly one parent" DB constraint as Activity — see that
    model's own docstring, and Email's, for why."""

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=org, name="Some Co")
        self.account = create_account(self.customer, name="Some Region")

    def _email_kwargs(self):
        return {
            "subject": "Welcome aboard",
            "sender_name": "Edgar Holmes",
            "recipient_name": "Natalie Reyes",
            "body": "Hi Natalie, excited to get started.",
            "sent_at": "2026-03-05T18:20:00Z",
        }

    def test_customer_only_is_valid(self):
        email = Email.objects.create(customer=self.customer, **self._email_kwargs())
        self.assertIsNone(email.account)

    def test_account_only_is_valid(self):
        email = Email.objects.create(account=self.account, **self._email_kwargs())
        self.assertIsNone(email.customer)

    def test_neither_parent_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Email.objects.create(**self._email_kwargs())

    def test_both_parents_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Email.objects.create(
                customer=self.customer, account=self.account, **self._email_kwargs()
            )


class TaskParentConstraintTests(TestCase):
    """Same "exactly one parent" DB constraint as Activity/Email."""

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=org, name="Some Co")
        self.account = create_account(self.customer, name="Some Region")

    def _task_kwargs(self):
        return {
            "title": "Prepare QBR deck",
            "assignee_name": "Edgar Holmes",
            "due_date": "2026-03-15",
            "priority": Task.Priority.HIGH,
        }

    def test_customer_only_is_valid(self):
        task = Task.objects.create(customer=self.customer, **self._task_kwargs())
        self.assertIsNone(task.account)

    def test_account_only_is_valid(self):
        task = Task.objects.create(account=self.account, **self._task_kwargs())
        self.assertIsNone(task.customer)

    def test_neither_parent_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Task.objects.create(**self._task_kwargs())

    def test_both_parents_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Task.objects.create(customer=self.customer, account=self.account, **self._task_kwargs())

    def test_status_defaults_to_pending(self):
        task = Task.objects.create(customer=self.customer, **self._task_kwargs())
        self.assertEqual(task.status, Task.Status.PENDING)


class NoteParentConstraintTests(TestCase):
    """Same "exactly one parent" DB constraint as Activity/Email/Task."""

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=org, name="Some Co")
        self.account = create_account(self.customer, name="Some Region")

    def _note_kwargs(self):
        return {
            "title": "Call Notes: Product Feedback Session",
            "author_name": "Edgar Holmes",
            "body": "Customer expressed interest in AI-powered analytics.",
            "logged_at": "2026-03-04",
        }

    def test_customer_only_is_valid(self):
        note = Note.objects.create(customer=self.customer, **self._note_kwargs())
        self.assertIsNone(note.account)

    def test_account_only_is_valid(self):
        note = Note.objects.create(account=self.account, **self._note_kwargs())
        self.assertIsNone(note.customer)

    def test_neither_parent_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Note.objects.create(**self._note_kwargs())

    def test_both_parents_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Note.objects.create(customer=self.customer, account=self.account, **self._note_kwargs())

    def test_links_defaults_to_zero(self):
        note = Note.objects.create(customer=self.customer, **self._note_kwargs())
        self.assertEqual(note.links, 0)


class TicketParentConstraintTests(TestCase):
    """Same "exactly one parent" DB constraint as Activity/Email/Task/Note."""

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=org, name="Some Co")
        self.account = create_account(self.customer, name="Some Region")

    def _ticket_kwargs(self):
        return {
            "ticket_number": "TKT-1042",
            "title": "Dashboard loading slow on large datasets",
            "assignee_name": "Support Team",
            "priority": Ticket.Priority.HIGH,
            "opened_at": "2026-03-03",
        }

    def test_customer_only_is_valid(self):
        ticket = Ticket.objects.create(customer=self.customer, **self._ticket_kwargs())
        self.assertIsNone(ticket.account)

    def test_account_only_is_valid(self):
        ticket = Ticket.objects.create(account=self.account, **self._ticket_kwargs())
        self.assertIsNone(ticket.customer)

    def test_neither_parent_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Ticket.objects.create(**self._ticket_kwargs())

    def test_both_parents_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Ticket.objects.create(
                customer=self.customer, account=self.account, **self._ticket_kwargs()
            )

    def test_status_defaults_to_open(self):
        ticket = Ticket.objects.create(customer=self.customer, **self._ticket_kwargs())
        self.assertEqual(ticket.status, Ticket.Status.OPEN)


class CalendarEventParentConstraintTests(TestCase):
    """Same "exactly one parent" DB constraint as Activity/Email/Task/
    Note/Ticket."""

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=org, name="Some Co")
        self.account = create_account(self.customer, name="Some Region")

    def _event_kwargs(self):
        return {
            "title": "Quarterly Business Review",
            "description": "Q1 2026 QBR with stakeholders",
            "type": CalendarEvent.EventType.REVIEW,
            "event_date": "2026-03-15",
            "start_time": "10:00",
            "end_time": "11:30",
            "attendee_count": 3,
        }

    def test_customer_only_is_valid(self):
        event = CalendarEvent.objects.create(customer=self.customer, **self._event_kwargs())
        self.assertIsNone(event.account)

    def test_account_only_is_valid(self):
        event = CalendarEvent.objects.create(account=self.account, **self._event_kwargs())
        self.assertIsNone(event.customer)

    def test_neither_parent_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            CalendarEvent.objects.create(**self._event_kwargs())

    def test_both_parents_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            CalendarEvent.objects.create(
                customer=self.customer, account=self.account, **self._event_kwargs()
            )


class ContactParentConstraintTests(TestCase):
    """Same "exactly one parent" DB constraint as Activity/Email/Task/
    Note/Ticket/CalendarEvent."""

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=org, name="Some Co")
        self.account = create_account(self.customer, name="Some Region")

    def _contact_kwargs(self):
        return {
            "name": "Jamie Lee",
            "role": Contact.Role.CHAMPION,
            "email": "jamie.lee@someco.com",
        }

    def test_customer_only_is_valid(self):
        contact = Contact.objects.create(customer=self.customer, **self._contact_kwargs())
        self.assertIsNone(contact.account)

    def test_account_only_is_valid(self):
        contact = Contact.objects.create(account=self.account, **self._contact_kwargs())
        self.assertIsNone(contact.customer)

    def test_neither_parent_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Contact.objects.create(**self._contact_kwargs())

    def test_both_parents_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Contact.objects.create(
                customer=self.customer, account=self.account, **self._contact_kwargs()
            )


class ContactCompanyPropertyTests(TestCase):
    """Contact.companies resolves to every ultimate parent Customer
    whether the contact is org-level or account-level — see
    ContactSerializer's own docstring for why the standalone
    /contacts/list page needs it."""

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=org, name="Some Co")
        self.account = create_account(self.customer, name="Some Region")

    def test_org_level_contact_company_is_its_own_customer(self):
        contact = Contact.objects.create(
            customer=self.customer,
            name="Jamie Lee",
            role=Contact.Role.CHAMPION,
            email="jamie.lee@someco.com",
        )
        self.assertEqual(contact.companies, [self.customer])

    def test_account_level_contact_company_is_the_accounts_customer(self):
        contact = Contact.objects.create(
            account=self.account,
            name="Jamie Lee",
            role=Contact.Role.CHAMPION,
            email="jamie.lee@someco.com",
        )
        self.assertEqual(contact.companies, [self.customer])


class OpportunityParentConstraintTests(TestCase):
    """Same "exactly one parent" DB constraint as Contact/Activity/
    Email/Task/Note/Ticket/CalendarEvent."""

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=org, name="Some Co")
        self.account = create_account(self.customer, name="Some Region")

    def _opportunity_kwargs(self):
        return {
            "title": "Renewal Expansion Opportunity",
            "mrr": "2500.00",
            "stage": Opportunity.Stage.DISCOVERY,
            "priority": Opportunity.Priority.HIGH,
        }

    def test_customer_only_is_valid(self):
        opportunity = Opportunity.objects.create(
            customer=self.customer, **self._opportunity_kwargs()
        )
        self.assertIsNone(opportunity.account)

    def test_account_only_is_valid(self):
        opportunity = Opportunity.objects.create(account=self.account, **self._opportunity_kwargs())
        self.assertIsNone(opportunity.customer)

    def test_neither_parent_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Opportunity.objects.create(**self._opportunity_kwargs())

    def test_both_parents_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Opportunity.objects.create(
                customer=self.customer, account=self.account, **self._opportunity_kwargs()
            )


class OpportunityCompanyPropertyTests(TestCase):
    """Opportunity.companies resolves to every ultimate parent Customer
    whether the opportunity is org-level or account-level — same
    reasoning as ContactCompanyPropertyTests above."""

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=org, name="Some Co")
        self.account = create_account(self.customer, name="Some Region")

    def test_org_level_opportunity_company_is_its_own_customer(self):
        opportunity = Opportunity.objects.create(
            customer=self.customer,
            title="Upsell",
            mrr="1000.00",
        )
        self.assertEqual(opportunity.companies, [self.customer])

    def test_account_level_opportunity_company_is_the_accounts_customer(self):
        opportunity = Opportunity.objects.create(
            account=self.account,
            title="Upsell",
            mrr="1000.00",
        )
        self.assertEqual(opportunity.companies, [self.customer])


class RiskParentConstraintTests(TestCase):
    """Same "exactly one parent" DB constraint as Opportunity/Contact/
    Activity/Email/Task/Note/Ticket/CalendarEvent."""

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=org, name="Some Co")
        self.account = create_account(self.customer, name="Some Region")

    def _risk_kwargs(self):
        return {
            "title": "Renewal Risk — Contract Expiry",
            "mrr": "2500.00",
            "stage": Risk.Stage.OPEN,
            "priority": Risk.Priority.HIGH,
        }

    def test_customer_only_is_valid(self):
        risk = Risk.objects.create(customer=self.customer, **self._risk_kwargs())
        self.assertIsNone(risk.account)

    def test_account_only_is_valid(self):
        risk = Risk.objects.create(account=self.account, **self._risk_kwargs())
        self.assertIsNone(risk.customer)

    def test_neither_parent_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Risk.objects.create(**self._risk_kwargs())

    def test_both_parents_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Risk.objects.create(customer=self.customer, account=self.account, **self._risk_kwargs())


class RiskCompanyPropertyTests(TestCase):
    """Risk.companies resolves to every ultimate parent Customer whether
    the risk is org-level or account-level — same reasoning as
    OpportunityCompanyPropertyTests above."""

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=org, name="Some Co")
        self.account = create_account(self.customer, name="Some Region")

    def test_org_level_risk_company_is_its_own_customer(self):
        risk = Risk.objects.create(customer=self.customer, title="Downgrade Risk", mrr="1000.00")
        self.assertEqual(risk.companies, [self.customer])

    def test_account_level_risk_company_is_the_accounts_customer(self):
        risk = Risk.objects.create(account=self.account, title="Downgrade Risk", mrr="1000.00")
        self.assertEqual(risk.companies, [self.customer])


class SurveyParentConstraintTests(TestCase):
    """Same "exactly one parent" DB constraint as Opportunity/Risk/
    Contact/Activity/Email/Task/Note/Ticket/CalendarEvent."""

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=org, name="Some Co")
        self.account = create_account(self.customer, name="Some Region")

    def _survey_kwargs(self):
        return {"survey_type": Survey.SurveyType.NPS, "sent_at": "2026-09-01"}

    def test_customer_only_is_valid(self):
        survey = Survey.objects.create(customer=self.customer, **self._survey_kwargs())
        self.assertIsNone(survey.account)

    def test_account_only_is_valid(self):
        survey = Survey.objects.create(account=self.account, **self._survey_kwargs())
        self.assertIsNone(survey.customer)

    def test_neither_parent_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Survey.objects.create(**self._survey_kwargs())

    def test_both_parents_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Survey.objects.create(
                customer=self.customer, account=self.account, **self._survey_kwargs()
            )


class SurveyCompanyPropertyTests(TestCase):
    """Survey.companies resolves to every ultimate parent Customer
    whether the survey is org-level or account-level — same reasoning
    as OpportunityCompanyPropertyTests above."""

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=org, name="Some Co")
        self.account = create_account(self.customer, name="Some Region")

    def test_org_level_survey_company_is_its_own_customer(self):
        survey = Survey.objects.create(
            customer=self.customer, survey_type=Survey.SurveyType.NPS, sent_at="2026-09-01"
        )
        self.assertEqual(survey.companies, [self.customer])

    def test_account_level_survey_company_is_the_accounts_customer(self):
        survey = Survey.objects.create(
            account=self.account, survey_type=Survey.SurveyType.NPS, sent_at="2026-09-01"
        )
        self.assertEqual(survey.companies, [self.customer])


class CanvasParentConstraintTests(TestCase):
    """Same "exactly one parent" DB constraint as Opportunity/Risk/
    Survey/Contact/Activity/Email/Task/Note/Ticket/CalendarEvent."""

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=org, name="Some Co")
        self.account = create_account(self.customer, name="Some Region")

    def test_customer_only_is_valid(self):
        canvas = Canvas.objects.create(customer=self.customer)
        self.assertIsNone(canvas.account)

    def test_account_only_is_valid(self):
        canvas = Canvas.objects.create(account=self.account)
        self.assertIsNone(canvas.customer)

    def test_neither_parent_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Canvas.objects.create()

    def test_both_parents_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Canvas.objects.create(customer=self.customer, account=self.account)


class CanvasCompanyPropertyTests(TestCase):
    """Canvas.companies resolves to every ultimate parent Customer
    whether the canvas is org-level or account-level — same reasoning
    as OpportunityCompanyPropertyTests above."""

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=org, name="Some Co")
        self.account = create_account(self.customer, name="Some Region")

    def test_org_level_canvas_company_is_its_own_customer(self):
        canvas = Canvas.objects.create(customer=self.customer)
        self.assertEqual(canvas.companies, [self.customer])

    def test_account_level_canvas_company_is_the_accounts_customer(self):
        canvas = Canvas.objects.create(account=self.account)
        self.assertEqual(canvas.companies, [self.customer])


class CanvasNodesEdgesRoundTripTests(TestCase):
    """nodes/edges are stored verbatim — same "the graph shape is the
    frontend's concern" contract as scenarios.Scenario's own."""

    def test_nodes_and_edges_round_trip_verbatim(self):
        org = Organisation.objects.create(name="Acme Inc")
        customer = Customer.objects.create(organisation=org, name="Some Co")
        nodes = [
            {
                "id": "n1",
                "type": "contact",
                "position": {"x": 10, "y": 20},
                "data": {"contact_id": 5},
            }
        ]
        edges = [{"id": "n1-n2", "source": "n1", "target": "n2", "label": "Reports to"}]

        canvas = Canvas.objects.create(customer=customer, nodes=nodes, edges=edges)
        canvas.refresh_from_db()

        self.assertEqual(canvas.nodes, nodes)
        self.assertEqual(canvas.edges, edges)


class HeadlineParentConstraintTests(TestCase):
    """Same "exactly one parent" DB constraint as Activity/Email/Task/Note."""

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=org, name="Some Co")
        self.account = create_account(self.customer, name="Some Region")

    def _headline_kwargs(self, **overrides):
        return {
            "title": "Renewal and Expansion",
            "content": "Renewal positioned for success with growth opportunities.",
            "status": Headline.Status.OPEN,
            "period_start": "2025-11-20",
            "period_end": "2026-01-21",
        } | overrides

    def test_customer_only_is_valid(self):
        headline = Headline.objects.create(customer=self.customer, **self._headline_kwargs())
        self.assertIsNone(headline.account)

    def test_account_only_is_valid(self):
        headline = Headline.objects.create(account=self.account, **self._headline_kwargs())
        self.assertIsNone(headline.customer)

    def test_neither_parent_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Headline.objects.create(**self._headline_kwargs())

    def test_both_parents_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Headline.objects.create(
                customer=self.customer, account=self.account, **self._headline_kwargs()
            )

    def test_a_summary_may_not_carry_a_status(self):
        """There is nothing open or closed about a rolling TL;DR — the
        model refuses one at the DB level, not just in the serializer."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            Headline.objects.create(
                customer=self.customer,
                **self._headline_kwargs(kind=Headline.Kind.SUMMARY),
            )

    def test_a_summary_without_a_status_is_valid(self):
        headline = Headline.objects.create(
            customer=self.customer,
            **self._headline_kwargs(kind=Headline.Kind.SUMMARY, status=""),
        )
        self.assertEqual(headline.kind, Headline.Kind.SUMMARY)

    def test_defaults_to_a_headline_with_no_sources_and_no_generated_at(self):
        headline = Headline.objects.create(
            customer=self.customer, title="T", content="C", status=Headline.Status.OPEN
        )
        self.assertEqual(headline.kind, Headline.Kind.HEADLINE)
        self.assertEqual(headline.data_sources, [])
        self.assertIsNone(headline.generated_at)


class TicketConnectorTests(TestCase):
    """`Ticket.clean()` is where the connector-scope invariant lives —
    see that method's own docstring on why not the serializer."""

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.apple = Customer.objects.create(organisation=self.org, name="Apple Inc")
        self.kraft = Customer.objects.create(organisation=self.org, name="Kraft Heinz")
        self.apple_emea = create_account(self.apple, name="Apple EMEA")
        self.zendesk = Connector.objects.create(
            organisation=self.org, provider=Connector.Provider.ZENDESK, name="Zendesk"
        )

    def _ticket(self, **overrides):
        return Ticket(
            **{
                "customer": self.apple,
                "ticket_number": "TKT-1",
                "title": "Login fails",
                "assignee_name": "Support Team",
                "priority": Ticket.Priority.HIGH,
                "opened_at": "2026-03-01",
                **overrides,
            }
        )

    def test_a_ticket_with_no_connector_is_valid(self):
        """Null means raised in Revenact itself — a real case, not
        missing data."""
        ticket = self._ticket()
        ticket.full_clean()
        self.assertIsNone(ticket.connector)

    def test_an_organisation_wide_connector_covers_any_ticket(self):
        self._ticket(connector=self.zendesk).full_clean()
        self._ticket(customer=self.kraft, connector=self.zendesk).full_clean()

    def test_a_scoped_connector_rejects_a_company_it_does_not_cover(self):
        self.zendesk.customers.add(self.apple)

        self._ticket(connector=self.zendesk).full_clean()  # Apple is fine

        with self.assertRaises(DjangoValidationError) as caught:
            self._ticket(customer=self.kraft, connector=self.zendesk).full_clean()
        self.assertIn("isn't connected for", str(caught.exception))

    def test_a_customer_scoped_connector_covers_that_customers_accounts(self):
        self.zendesk.customers.add(self.apple)

        self._ticket(customer=None, account=self.apple_emea, connector=self.zendesk).full_clean()

    def test_another_organisations_connector_is_rejected(self):
        other_org = Organisation.objects.create(name="Other Inc")
        theirs = Connector.objects.create(
            organisation=other_org, provider=Connector.Provider.JIRA, name="Jira"
        )

        with self.assertRaises(DjangoValidationError) as caught:
            self._ticket(connector=theirs).full_clean()
        self.assertIn("different organisation", str(caught.exception))

    def test_the_new_fields_default_sensibly(self):
        ticket = Ticket.objects.create(customer=self.apple, **self._ticket_kwargs())

        self.assertEqual(ticket.sentiment, Ticket.Sentiment.NEUTRAL)
        self.assertIsNone(ticket.resolved_at)
        self.assertIsNone(ticket.connector)

    def _ticket_kwargs(self):
        return {
            "ticket_number": "TKT-2",
            "title": "Sync failure",
            "assignee_name": "Engineering",
            "priority": Ticket.Priority.LOW,
            "opened_at": "2026-03-02",
        }

    def test_on_hold_is_a_real_status(self):
        ticket = Ticket.objects.create(
            customer=self.apple, status=Ticket.Status.ON_HOLD, **self._ticket_kwargs()
        )
        self.assertEqual(ticket.get_status_display(), "On Hold")


class AIPulseCategoryTests(TestCase):
    """`ai_pulse_score` was a stored column and is derived from
    `ai_pulse_value` now, the same way `health_category` is derived from
    `health_score` — so the two can never disagree."""

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")

    def _customer(self, value):
        return Customer.objects.create(organisation=self.org, name="Some Co", ai_pulse_value=value)

    def test_maps_each_value_to_its_category(self):
        self.assertEqual(self._customer(5).ai_pulse_score, Customer.AIPulseScore.VERY_SATISFIED)
        self.assertEqual(self._customer(4).ai_pulse_score, Customer.AIPulseScore.SATISFIED)
        self.assertEqual(self._customer(3).ai_pulse_score, Customer.AIPulseScore.MODERATE)

    def test_the_bottom_two_values_are_both_high_risk(self):
        self.assertEqual(self._customer(2).ai_pulse_score, Customer.AIPulseScore.HIGH_RISK)
        self.assertEqual(self._customer(1).ai_pulse_score, Customer.AIPulseScore.HIGH_RISK)

    def test_unscored_reads_blank_not_high_risk(self):
        # Blank, not null, because that is what the column used to hold and
        # what the frontend's AI_PULSE_LABELS still expects. "Not scored yet"
        # is emphatically not the same as "scored badly".
        self.assertEqual(self._customer(None).ai_pulse_score, "")

    def test_accounts_derive_it_the_same_way(self):
        customer = Customer.objects.create(organisation=self.org, name="Parent Co")
        account = create_account(customer, name="EMEA", ai_pulse_value=1)
        self.assertEqual(account.ai_pulse_score, Customer.AIPulseScore.HIGH_RISK)
        self.assertEqual(create_account(customer, name="APAC").ai_pulse_score, "")

    def test_rejects_a_value_outside_the_scale(self):
        for value in (0, 6):
            customer = Customer(organisation=self.org, name="Some Co", ai_pulse_value=value)
            with self.assertRaises(DjangoValidationError):
                customer.full_clean()


class CSMPulseTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")

    def test_defaults_to_unrated(self):
        # Null rather than a middling 3: "the CSM hasn't looked" is a
        # different fact from "the CSM thinks it's average", and the
        # Divergence view must not read the first as the second.
        customer = Customer.objects.create(organisation=self.org, name="Some Co")
        self.assertIsNone(customer.csm_pulse_score)
        self.assertIsNone(customer.csm_pulse_modified_at)

    def test_shares_the_ai_pulse_scale(self):
        customer = Customer.objects.create(
            organisation=self.org, name="Some Co", csm_pulse_score=4, ai_pulse_value=2
        )
        # The whole point: both are 1-5, so the gap is meaningful.
        self.assertEqual(customer.csm_pulse_score - customer.ai_pulse_value, 2)

    def test_rejects_a_value_outside_the_scale(self):
        for value in (0, 6):
            customer = Customer(organisation=self.org, name="Some Co", csm_pulse_score=value)
            with self.assertRaises(DjangoValidationError):
                customer.full_clean()


class HealthSnapshotTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(
            organisation=self.org,
            name="Some Co",
            health_score="8.0",
            csm_pulse_score=4,
            ai_pulse_value=5,
        )
        self.account = create_account(self.customer, name="EMEA", health_score="3.0")

    def test_derives_its_categories_from_its_own_stored_numbers(self):
        snapshot = HealthSnapshot.objects.create(
            customer=self.customer,
            captured_on=date(2026, 1, 31),
            health_score="2.5",
            ai_pulse_value=1,
        )
        # A snapshot reports the past, not the parent's current 8.0/5.
        self.assertEqual(snapshot.health_category, Customer.HealthCategory.POOR)
        self.assertEqual(snapshot.ai_pulse_score, Customer.AIPulseScore.HIGH_RISK)
        self.assertEqual(self.customer.health_category, Customer.HealthCategory.GOOD)

    def test_requires_exactly_one_parent(self):
        for kwargs in ({}, {"customer": self.customer, "account": self.account}):
            with self.assertRaises(IntegrityError), transaction.atomic():
                HealthSnapshot.objects.create(
                    captured_on=date(2026, 1, 31), health_score="5.0", **kwargs
                )

    def test_one_snapshot_per_parent_per_date(self):
        HealthSnapshot.objects.create(
            customer=self.customer, captured_on=date(2026, 1, 31), health_score="5.0"
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            HealthSnapshot.objects.create(
                customer=self.customer, captured_on=date(2026, 1, 31), health_score="6.0"
            )

    def test_a_customer_and_an_account_can_share_a_date(self):
        HealthSnapshot.objects.create(
            customer=self.customer, captured_on=date(2026, 1, 31), health_score="5.0"
        )
        HealthSnapshot.objects.create(
            account=self.account, captured_on=date(2026, 1, 31), health_score="5.0"
        )
        self.assertEqual(HealthSnapshot.objects.count(), 2)

    def test_orders_oldest_first_so_a_series_reads_left_to_right(self):
        for day in (31, 28, 15):
            HealthSnapshot.objects.create(
                customer=self.customer,
                captured_on=date(2026, 1, day) if day != 28 else date(2026, 2, 28),
                health_score="5.0",
            )
        captured = list(HealthSnapshot.objects.values_list("captured_on", flat=True))
        self.assertEqual(captured, sorted(captured))


class CaptureHealthSnapshotTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(
            organisation=self.org,
            name="Some Co",
            health_score="7.5",
            csm_pulse_score=3,
            ai_pulse_value=4,
        )

    def test_records_the_parents_current_readings(self):
        snapshot = capture_health_snapshot(self.customer, date(2026, 3, 31))
        # Reloaded, not the returned in-memory copy: a DecimalField holds
        # whatever was assigned until the row round-trips through the DB.
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.customer, self.customer)
        self.assertEqual(snapshot.health_score, Decimal("7.5"))
        self.assertEqual(snapshot.csm_pulse_score, 3)
        self.assertEqual(snapshot.ai_pulse_value, 4)

    def test_rerunning_for_the_same_date_overwrites_rather_than_raising(self):
        # A scheduled job that fires twice in a day must be harmless.
        capture_health_snapshot(self.customer, date(2026, 3, 31))
        self.customer.health_score = Decimal("2.0")
        self.customer.save()
        snapshot = capture_health_snapshot(self.customer, date(2026, 3, 31))
        snapshot.refresh_from_db()

        self.assertEqual(HealthSnapshot.objects.count(), 1)
        self.assertEqual(snapshot.health_score, Decimal("2.0"))

    def test_sets_the_account_fk_for_an_account(self):
        account = create_account(self.customer, name="EMEA", health_score="4.0")
        snapshot = capture_health_snapshot(account, date(2026, 3, 31))
        self.assertEqual(snapshot.account, account)
        self.assertIsNone(snapshot.customer)


class HealthCategoryCoercionTests(TestCase):
    """`health_category` reads a DecimalField, which holds whatever was
    assigned to it until the row is reloaded — so the category has to cope
    with a str, an int and a Decimal alike rather than raising TypeError."""

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")

    def test_reads_the_same_category_whatever_the_score_was_assigned_as(self):
        for score in ("8.0", 8.0, 8, Decimal("8.0")):
            customer = Customer(organisation=self.org, name="Some Co", health_score=score)
            self.assertEqual(customer.health_category, Customer.HealthCategory.GOOD)

    def test_applies_to_snapshots_too(self):
        customer = Customer.objects.create(organisation=self.org, name="Some Co")
        snapshot = HealthSnapshot(
            customer=customer, captured_on=date(2026, 1, 31), health_score="3.9"
        )
        self.assertEqual(snapshot.health_category, Customer.HealthCategory.POOR)


class CsatBreakdownTests(TestCase):
    """The Organisations table's CSAT popover invented its whole distribution.
    It reads this now: real answered surveys, bucketed into five bands."""

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=self.org, name="Some Co")

    def _respond(self, score, survey_type=None, status=None):
        return Survey.objects.create(
            customer=self.customer,
            survey_type=survey_type or Survey.SurveyType.CSAT,
            status=status or Survey.Status.RESPONDED,
            score=score,
            sent_at=date(2026, 1, 1),
            responded_at=date(2026, 1, 5),
        )

    def _bands(self):
        return {b["key"]: b for b in self.customer.csat_breakdown["bands"]}

    def test_buckets_scores_into_equal_fifths(self):
        for score, expected in (
            (0, "very_dissatisfied"),
            (20, "very_dissatisfied"),
            (21, "dissatisfied"),
            (40, "dissatisfied"),
            (41, "neutral"),
            (60, "neutral"),
            (61, "satisfied"),
            (80, "satisfied"),
            (81, "very_satisfied"),
            (100, "very_satisfied"),
        ):
            self.assertEqual(csat_band(score), expected, msg=f"score {score}")

    def test_counts_and_shares_the_answered_surveys(self):
        for score in (100, 90, 50):
            self._respond(score)

        breakdown = self.customer.csat_breakdown
        self.assertEqual(breakdown["responses"], 3)
        bands = self._bands()
        self.assertEqual(bands["very_satisfied"]["count"], 2)
        self.assertEqual(bands["neutral"]["count"], 1)
        self.assertAlmostEqual(bands["very_satisfied"]["share"], 66.67, places=1)

    def test_shares_total_one_hundred(self):
        for score in (95, 85, 70, 45, 10):
            self._respond(score)
        shares = sum(b["share"] for b in self.customer.csat_breakdown["bands"])
        self.assertAlmostEqual(shares, 100.0, places=1)

    def test_always_returns_all_five_bands(self):
        # A stable five-row shape, so the popover doesn't change length
        # per customer.
        self._respond(100)
        self.assertEqual(len(self.customer.csat_breakdown["bands"]), 5)
        self.assertEqual(self._bands()["very_dissatisfied"]["count"], 0)

    def test_ignores_surveys_that_are_not_answered_csat(self):
        self._respond(100)
        self._respond(90, survey_type=Survey.SurveyType.NPS)
        self._respond(90, status=Survey.Status.SENT)
        self.assertEqual(self.customer.csat_breakdown["responses"], 1)

    def test_reports_zero_rather_than_a_made_up_spread(self):
        breakdown = self.customer.csat_breakdown
        self.assertEqual(breakdown["responses"], 0)
        self.assertTrue(all(b["count"] == 0 and b["share"] == 0.0 for b in breakdown["bands"]))

    def test_orders_bands_best_to_worst(self):
        self._respond(100)
        keys = [b["key"] for b in self.customer.csat_breakdown["bands"]]
        self.assertEqual(
            keys,
            ["very_satisfied", "satisfied", "neutral", "dissatisfied", "very_dissatisfied"],
        )
