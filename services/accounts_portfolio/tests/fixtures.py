from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from services.accounts.models import Organisation, User
from services.customers.models import Account, Customer

_CARL = object()


class AccountPortfolioFixture(TestCase):
    """Carl and Dana are CSMs in Acme, Alice is its admin (Leadership), and
    Globex is another tenant. Carl owns the organisation Pizza Hut and Dana
    owns Taco Bell, so Carl sees every account under Pizza Hut and only his
    own or unowned ones under Taco Bell. `account()` makes a Good,
    12,000-ARR account owned by Carl under Pizza Hut unless a test says
    otherwise, so each test's differences are its own."""

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
        self.pizza = Customer.objects.create(
            organisation=self.org, name="Pizza Hut", owner=self.csm
        )
        self.taco = Customer.objects.create(
            organisation=self.org, name="Taco Bell", owner=self.other
        )
        self.globex = Customer.objects.create(organisation=self.other_org, name="Globex Corp")

    def account(self, name, *, customers=None, owner=_CARL, **fields):
        values = {"health_score": Decimal("8.0"), "arr": Decimal("12000"), **fields}
        account = Account.objects.create(
            name=name, owner=self.csm if owner is _CARL else owner, **values
        )
        account.customers.add(*(customers if customers is not None else [self.pizza]))
        return account
