from django.test import TestCase
from django.utils import timezone

from services.accounts.models import Organisation, User
from services.customers.drill import LIMIT, companies_payload, parse_segment, record_counts
from services.customers.models import Account, Customer, Ticket

KINDS = {"all": False, "priority": True}


class ParseSegmentTests(TestCase):
    def test_absent_unknown_and_malformed_are_ignored(self):
        self.assertIsNone(parse_segment({}, KINDS))
        self.assertIsNone(parse_segment({"drill": "nope:1"}, KINDS))
        self.assertIsNone(parse_segment({"drill": "priority:"}, KINDS))
        self.assertIsNone(parse_segment({"drill": "all:extra"}, KINDS))

    def test_kinds_with_and_without_a_value(self):
        self.assertEqual(parse_segment({"drill": "all"}, KINDS), ("all", ""))
        self.assertEqual(parse_segment({"drill": "priority:high"}, KINDS), ("priority", "high"))

    def test_the_value_may_contain_colons(self):
        self.assertEqual(parse_segment({"drill": "priority:a:b"}, KINDS), ("priority", "a:b"))


class Fixture(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="supersecret1",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.other = User.objects.create_user(
            email="dana@acme.io",
            password="supersecret1",
            name="Dana",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.mine = Customer.objects.create(
            organisation=self.org,
            name="Mine",
            owner=self.csm,
            arr_billed_at_account=100_000,
            currency="USD",
        )
        self.also_mine = Customer.objects.create(
            organisation=self.org,
            name="Also mine",
            owner=self.csm,
            arr_billed_at_account=50_000,
            currency="USD",
        )
        self.theirs = Customer.objects.create(
            organisation=self.org, name="Theirs", owner=self.other
        )

    def _ticket(self, n, **overrides):
        return Ticket.objects.create(
            **{
                "customer": self.mine,
                "ticket_number": f"TKT-{n}",
                "title": "x",
                "status": Ticket.Status.OPEN,
                "priority": Ticket.Priority.HIGH,
                "opened_at": timezone.now(),
                **overrides,
            }
        )


class RecordCountsTests(Fixture):
    def test_counts_every_record_not_distinct_companies(self):
        self._ticket(1)
        self._ticket(2)
        self._ticket(3, customer=self.also_mine)
        self.assertEqual(
            record_counts([Ticket.objects.all()]), {self.mine.pk: 2, self.also_mine.pk: 1}
        )

    def test_an_account_record_counts_for_each_of_its_customers(self):
        account = Account.objects.create(name="Shared", owner=self.csm)
        account.customers.add(self.mine, self.also_mine)
        self._ticket(1, customer=None, account=account)
        self.assertEqual(
            record_counts([Ticket.objects.all()]), {self.mine.pk: 1, self.also_mine.pk: 1}
        )


class CompaniesPayloadTests(Fixture):
    def test_shape_order_and_visibility(self):
        body = companies_payload(
            self.csm,
            "priority:high",
            {self.mine.pk: 2, self.also_mine.pk: 5, self.theirs.pk: 9},
            value_label="tickets",
        )
        drill = body["drill"]
        self.assertEqual(body["currency"], "USD")
        self.assertEqual(drill["segment"], "priority:high")
        self.assertEqual(drill["value_label"], "tickets")
        # Theirs is outside Carl's book, so it is dropped even though a value came in.
        self.assertEqual([c["name"] for c in drill["companies"]], ["Also mine", "Mine"])
        self.assertEqual(drill["count"], 2)
        self.assertFalse(drill["truncated"])
        self.assertEqual(
            drill["companies"][1],
            {"id": self.mine.pk, "name": "Mine", "owner": "Carl", "arr": 100000.0, "value": 2},
        )

    def test_none_values_sort_last_or_first(self):
        values = {self.mine.pk: None, self.also_mine.pk: 3}
        last = companies_payload(self.csm, "x", values, value_label="days")
        first = companies_payload(self.csm, "x", values, value_label="days", none_first=True)
        self.assertEqual([c["name"] for c in last["drill"]["companies"]], ["Also mine", "Mine"])
        self.assertEqual([c["name"] for c in first["drill"]["companies"]], ["Mine", "Also mine"])

    def test_truncates_to_the_limit_but_counts_everything(self):
        extra = [
            Customer(organisation=self.org, name=f"C{i}", owner=self.csm) for i in range(LIMIT + 1)
        ]
        Customer.objects.bulk_create(extra)
        values = {c.pk: 1 for c in Customer.objects.filter(owner=self.csm)}
        drill = companies_payload(self.csm, "all", values, value_label="tickets")["drill"]
        self.assertEqual(drill["count"], len(values))
        self.assertTrue(drill["truncated"])
        self.assertEqual(len(drill["companies"]), LIMIT)
