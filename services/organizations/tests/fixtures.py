from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from services.accounts.models import Organisation, User
from services.customers.models import Customer

_CARL = object()


class PortfolioFixture(TestCase):
    """Carl and Dana are CSMs in Acme, Alice is its admin, and Globex is
    another tenant. `customer()` makes a Good, USD, 12,000-ARR customer owned by
    Carl unless a test says otherwise, so each test's differences are its own."""

    def setUp(self):
        self.today = timezone.localdate()
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice Admin",
            organisation=self.org,
            role=User.Role.ADMIN,
            function=User.Function.LEADERSHIP,
        )
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="supersecret1",
            name="Carl CSM",
            organisation=self.org,
            role=User.Role.CSM,
            function=User.Function.CS,
        )
        self.other = User.objects.create_user(
            email="dana@acme.io",
            password="supersecret1",
            name="Dana CSM",
            organisation=self.org,
            role=User.Role.CSM,
            function=User.Function.CS,
        )
        self.other_org = Organisation.objects.create(name="Globex", currency="USD")

    def customer(self, name, *, owner=_CARL, organisation=None, **fields):
        values = {
            "health_score": Decimal("8.0"),
            "arr_billed_at_account": Decimal("12000"),
            "currency": "USD",
            **fields,
        }
        return Customer.objects.create(
            organisation=organisation or self.org,
            name=name,
            owner=self.csm if owner is _CARL else owner,
            **values,
        )
