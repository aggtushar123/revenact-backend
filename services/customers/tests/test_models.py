"""Unit tier: model logic in isolation, no HTTP."""

from django.db import IntegrityError, transaction
from django.test import TestCase

from services.accounts.models import Organisation
from services.customers.models import (
    Account,
    Activity,
    CalendarEvent,
    Contact,
    Customer,
    Email,
    Note,
    Opportunity,
    Risk,
    Task,
    Ticket,
)


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
        return Account.objects.create(
            customer=self.customer, name="Some Region", health_score=score
        )

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
        account = Account.objects.create(customer=customer, name="Some Region")
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
        self.account = Account.objects.create(customer=self.customer, name="Some Region")

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
        self.account = Account.objects.create(customer=self.customer, name="Some Region")

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
        self.account = Account.objects.create(customer=self.customer, name="Some Region")

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
            Task.objects.create(
                customer=self.customer, account=self.account, **self._task_kwargs()
            )

    def test_status_defaults_to_pending(self):
        task = Task.objects.create(customer=self.customer, **self._task_kwargs())
        self.assertEqual(task.status, Task.Status.PENDING)


class NoteParentConstraintTests(TestCase):
    """Same "exactly one parent" DB constraint as Activity/Email/Task."""

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=org, name="Some Co")
        self.account = Account.objects.create(customer=self.customer, name="Some Region")

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
            Note.objects.create(
                customer=self.customer, account=self.account, **self._note_kwargs()
            )

    def test_links_defaults_to_zero(self):
        note = Note.objects.create(customer=self.customer, **self._note_kwargs())
        self.assertEqual(note.links, 0)


class TicketParentConstraintTests(TestCase):
    """Same "exactly one parent" DB constraint as Activity/Email/Task/Note."""

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=org, name="Some Co")
        self.account = Account.objects.create(customer=self.customer, name="Some Region")

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
        self.account = Account.objects.create(customer=self.customer, name="Some Region")

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
        self.account = Account.objects.create(customer=self.customer, name="Some Region")

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
    """Contact.company resolves to the ultimate parent Customer whether
    the contact is org-level or account-level — see ContactSerializer's
    own docstring for why the standalone /contacts/list page needs it."""

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=org, name="Some Co")
        self.account = Account.objects.create(customer=self.customer, name="Some Region")

    def test_org_level_contact_company_is_its_own_customer(self):
        contact = Contact.objects.create(
            customer=self.customer, name="Jamie Lee", role=Contact.Role.CHAMPION,
            email="jamie.lee@someco.com",
        )
        self.assertEqual(contact.company, self.customer)

    def test_account_level_contact_company_is_the_accounts_customer(self):
        contact = Contact.objects.create(
            account=self.account, name="Jamie Lee", role=Contact.Role.CHAMPION,
            email="jamie.lee@someco.com",
        )
        self.assertEqual(contact.company, self.customer)


class OpportunityParentConstraintTests(TestCase):
    """Same "exactly one parent" DB constraint as Contact/Activity/
    Email/Task/Note/Ticket/CalendarEvent."""

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=org, name="Some Co")
        self.account = Account.objects.create(customer=self.customer, name="Some Region")

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
    """Opportunity.company resolves to the ultimate parent Customer
    whether the opportunity is org-level or account-level — same
    reasoning as ContactCompanyPropertyTests above."""

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=org, name="Some Co")
        self.account = Account.objects.create(customer=self.customer, name="Some Region")

    def test_org_level_opportunity_company_is_its_own_customer(self):
        opportunity = Opportunity.objects.create(
            customer=self.customer, title="Upsell", mrr="1000.00",
        )
        self.assertEqual(opportunity.company, self.customer)

    def test_account_level_opportunity_company_is_the_accounts_customer(self):
        opportunity = Opportunity.objects.create(
            account=self.account, title="Upsell", mrr="1000.00",
        )
        self.assertEqual(opportunity.company, self.customer)


class RiskParentConstraintTests(TestCase):
    """Same "exactly one parent" DB constraint as Opportunity/Contact/
    Activity/Email/Task/Note/Ticket/CalendarEvent."""

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=org, name="Some Co")
        self.account = Account.objects.create(customer=self.customer, name="Some Region")

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
    """Risk.company resolves to the ultimate parent Customer whether
    the risk is org-level or account-level — same reasoning as
    OpportunityCompanyPropertyTests above."""

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=org, name="Some Co")
        self.account = Account.objects.create(customer=self.customer, name="Some Region")

    def test_org_level_risk_company_is_its_own_customer(self):
        risk = Risk.objects.create(customer=self.customer, title="Downgrade Risk", mrr="1000.00")
        self.assertEqual(risk.company, self.customer)

    def test_account_level_risk_company_is_the_accounts_customer(self):
        risk = Risk.objects.create(account=self.account, title="Downgrade Risk", mrr="1000.00")
        self.assertEqual(risk.company, self.customer)
