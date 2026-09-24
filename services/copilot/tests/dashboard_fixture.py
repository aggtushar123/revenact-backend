"""Shared set-up for the dashboard Ask Revenact tests: one organisation, a CSM
(Carl, Customer Success), a second CSM (Dana) whose book Carl must never see,
and a leadership admin (Boss) who sees everything."""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from services.accounts.models import Organisation, User
from services.anomalies.models import Anomaly, AnomalyEvidence
from services.customers.models import Customer, Ticket

GOOD, AVERAGE, POOR = Decimal("8.0"), Decimal("5.0"), Decimal("2.0")


class DashboardFixture(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")
        self.csm = self.person("carl@acme.io", "Carl")
        self.other = self.person("dana@acme.io", "Dana")
        self.admin = self.person(
            "boss@acme.io", "Boss", role=User.Role.ADMIN, function=User.Function.LEADERSHIP
        )
        self.api = APIClient()
        self.api.force_authenticate(self.csm)

    def person(self, email, name, *, role=User.Role.CSM, function=User.Function.CS):
        return User.objects.create_user(
            email=email,
            password="supersecret1",
            name=name,
            organisation=self.org,
            role=role,
            function=function,
        )

    def customer(self, name, *, owner=None, **fields):
        return Customer.objects.create(
            organisation=self.org,
            name=name,
            owner=owner or self.csm,
            health_score=fields.pop("health_score", GOOD),
            arr_billed_at_account=fields.pop("arr", 100_000),
            currency=fields.pop("currency", "USD"),
            **fields,
        )

    def ticket(self, n, customer, **overrides):
        return Ticket.objects.create(
            **{
                "customer": customer,
                "ticket_number": f"TKT-{n}",
                "title": f"Ticket {n}",
                "status": Ticket.Status.OPEN,
                "priority": Ticket.Priority.HIGH,
                "opened_at": self.today,
                **overrides,
            }
        )

    def anomaly(self, title, summary=""):
        return Anomaly.objects.create(
            organisation=self.org,
            title=title,
            summary=summary,
            status=Anomaly.Status.LIVE,
            first_seen_at=timezone.now(),
            last_seen_at=timezone.now(),
        )

    def evidence(self, anomaly, n, *, customer, snippet="Login fails after SSO redirect"):
        return AnomalyEvidence.objects.create(
            anomaly=anomaly,
            organisation=self.org,
            kind=AnomalyEvidence.Kind.CALL,
            record_id=n,
            customer=customer,
            snippet=snippet,
            occurred_at=timezone.now(),
        )

    def context(self, area="overview", view=None, focus=None, **filters):
        return {
            "surface": "dashboard",
            "area": area,
            "view": view,
            "filters": {"owner": "", "lifecycle": "", "customer": "", **filters},
            "focus": focus,
        }
