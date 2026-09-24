"""The Ticket Overview's filter, shared by /tickets/stats/ and the dashboard's
Ask Revenact digest."""

from django.test import TestCase
from django.utils import timezone

from services.accounts.models import Organisation, User
from services.customers.models import Customer, Ticket
from services.customers.ticket_filters import filtered_tickets


class FilteredTicketsTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")
        self.carl = User.objects.create_user(
            email="carl@acme.io",
            password="supersecret1",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
            function=User.Function.CS,
        )
        self.dana = User.objects.create_user(
            email="dana@acme.io",
            password="supersecret1",
            name="Dana",
            organisation=self.org,
            role=User.Role.CSM,
            function=User.Function.CS,
        )
        self.mine = Customer.objects.create(organisation=self.org, name="Mine", owner=self.carl)
        self.also = Customer.objects.create(organisation=self.org, name="Also", owner=self.carl)
        self.theirs = Customer.objects.create(organisation=self.org, name="Theirs", owner=self.dana)
        self.ticket("TKT-1", self.mine)
        self.ticket("TKT-2", self.also)
        self.ticket("TKT-3", self.mine, department=User.Function.ENGINEERING)
        self.ticket("TKT-4", self.theirs)

    def ticket(self, number, customer, **fields):
        return Ticket.objects.create(
            customer=customer,
            ticket_number=number,
            title="t",
            priority=Ticket.Priority.HIGH,
            opened_at=timezone.localdate(),
            **fields,
        )

    def numbers(self, params):
        return set(filtered_tickets(self.carl, params).values_list("ticket_number", flat=True))

    def test_visibility_first(self):
        # Dana's customer and another department's ticket are never in the set.
        self.assertEqual(self.numbers({}), {"TKT-1", "TKT-2"})

    def test_customer_narrows(self):
        self.assertEqual(self.numbers({"customer": str(self.mine.pk)}), {"TKT-1"})

    def test_owner_narrows_and_cannot_widen(self):
        self.assertEqual(self.numbers({"owner": str(self.carl.pk)}), {"TKT-1", "TKT-2"})
        self.assertEqual(self.numbers({"owner": str(self.dana.pk)}), set())

    def test_a_bad_value_is_ignored(self):
        self.assertEqual(self.numbers({"owner": "abc", "priority": "urgent"}), {"TKT-1", "TKT-2"})
