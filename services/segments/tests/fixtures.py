"""The segments tests' shared world, built once per test class
(`setUpTestData`), so a class of thirty tests creates it once: the CI budget
is tight."""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from services.accounts.models import Organisation, User
from services.customers.models import Account, Customer
from services.segments.models import Segment


def rule(field, op, value=None, *, match="all"):
    """One condition as a whole rule: `{"match", "conditions": [...]}`."""
    condition = {"field": field, "op": op}
    if value is not None:
        condition["value"] = value
    return {"match": match, "conditions": [condition]}


def person(email, name, organisation, **extra):
    values = {"role": User.Role.CSM, "function": User.Function.CS, **extra}
    return User.objects.create_user(
        email=email, password="supersecret1", name=name, organisation=organisation, **values
    )


class SegmentFixture(TestCase):
    """Acme has Alice (admin, Leadership) and two CSMs, Carl and Dana. Globex
    is another tenant, with Gus. Carl owns Pizza Hut and its account Pizza
    EMEA; Dana owns Taco Bell and its account Taco West. Carl cannot open
    Taco Bell or Taco West, and Dana cannot open Pizza Hut or Pizza EMEA."""

    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.org = Organisation.objects.create(name="Acme Inc", currency="USD")
        cls.admin = person(
            "alice@acme.io",
            "Alice Admin",
            cls.org,
            role=User.Role.ADMIN,
            function=User.Function.LEADERSHIP,
        )
        cls.csm = person("carl@acme.io", "Carl CSM", cls.org)
        cls.other = person("dana@acme.io", "Dana CSM", cls.org)
        cls.other_org = Organisation.objects.create(name="Globex", currency="USD")
        cls.stranger = person("gus@globex.io", "Gus Globex", cls.other_org)
        cls.pizza = Customer.objects.create(
            organisation=cls.org, name="Pizza Hut", owner=cls.csm, health_score=Decimal("8.0")
        )
        cls.taco = Customer.objects.create(
            organisation=cls.org, name="Taco Bell", owner=cls.other, health_score=Decimal("3.0")
        )
        cls.globex = Customer.objects.create(
            organisation=cls.other_org, name="Globex Corp", owner=cls.stranger
        )
        cls.emea = cls.make_account("Pizza EMEA", cls.pizza, cls.csm)
        cls.west = cls.make_account("Taco West", cls.taco, cls.other)

    @staticmethod
    def make_account(name, customer, owner, **fields):
        account = Account.objects.create(name=name, owner=owner, **fields)
        account.customers.add(customer)
        return account

    def segment(self, *, owner=None, kind="customer", rules=None, **fields):
        return Segment.objects.create(
            organisation=self.org,
            owner=owner or self.csm,
            name=fields.pop("name", "Renewal risk"),
            kind=kind,
            rules=rules or {"match": "all", "conditions": []},
            **fields,
        )
