from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from services.accounts.models import Organisation, User
from services.customers.models import Account, Customer, Opportunity, Risk

_CARL = object()


class PipelineFixture(TestCase):
    """Carl and Dana are CSMs (Customer Success) in Acme, Sid is in Sales and
    Alice is its admin (Leadership); Globex is another tenant. Carl owns Pizza
    Hut and Dana owns Taco Bell, so Carl opens Pizza Hut and not Taco Bell,
    unless he owns an account under it. `opportunity()`/`risk()` make an
    organisation-level item on Pizza Hut in Customer Success, 1,000 MRR,
    unless a test says otherwise; `account()` makes an account owned by Carl
    under Pizza Hut."""

    def setUp(self):
        self.today = timezone.localdate()
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")
        self.admin = self.user("alice@acme.io", "Alice Admin", User.Role.ADMIN, "leadership")
        self.csm = self.user("carl@acme.io", "Carl CSM", User.Role.CSM, "cs")
        self.other = self.user("dana@acme.io", "Dana CSM", User.Role.CSM, "cs")
        self.sales = self.user("sid@acme.io", "Sid Sales", User.Role.CSM, "sales")
        self.other_org = Organisation.objects.create(name="Globex", currency="USD")
        self.pizza = Customer.objects.create(
            organisation=self.org, name="Pizza Hut", owner=self.csm
        )
        self.taco = Customer.objects.create(
            organisation=self.org, name="Taco Bell", owner=self.other
        )
        self.globex = Customer.objects.create(organisation=self.other_org, name="Globex Corp")

    def user(self, email, name, role, function):
        return User.objects.create_user(
            email=email,
            password="supersecret1",
            name=name,
            organisation=self.org,
            role=role,
            function=function,
        )

    def account(self, name, *, customers=None, owner=_CARL):
        account = Account.objects.create(name=name, owner=self.csm if owner is _CARL else owner)
        account.customers.add(*(customers if customers is not None else [self.pizza]))
        return account

    def _item(self, model, title, *, customer, account, fields):
        values = {"mrr": Decimal("1000"), "department": User.Function.CS, **fields}
        if account is not None:
            return model.objects.create(account=account, title=title, **values)
        return model.objects.create(customer=customer or self.pizza, title=title, **values)

    def opportunity(self, title, *, customer=None, account=None, **fields):
        return self._item(Opportunity, title, customer=customer, account=account, fields=fields)

    def risk(self, title, *, customer=None, account=None, **fields):
        return self._item(Risk, title, customer=customer, account=account, fields=fields)

    def days(self, n):
        return self.today + timedelta(days=n)
