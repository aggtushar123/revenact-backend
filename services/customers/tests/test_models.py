"""Unit tier: model logic in isolation, no HTTP."""

from django.db import IntegrityError, transaction
from django.test import TestCase

from services.accounts.models import Organisation
from services.customers.models import Account, Activity, Customer, Email, Task


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
