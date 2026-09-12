"""Integration tier: the list carries what each connector brought in, and
writes stay gated on manage_integrations."""

from datetime import date

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.connectors.models import Connector
from services.customers.models import Call, Customer, Ticket


class ConnectorViewTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="x",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="x",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.apple = Customer.objects.create(organisation=self.org, name="Apple Inc")
        self.zendesk = Connector.objects.create(
            organisation=self.org, provider=Connector.Provider.ZENDESK, name="Zendesk"
        )
        self.zoom = Connector.objects.create(
            organisation=self.org, provider=Connector.Provider.ZOOM, name="Zoom"
        )
        for day in (10, 12):
            Ticket.objects.create(
                customer=self.apple,
                connector=self.zendesk,
                title=f"t{day}",
                opened_at=date(2026, 9, day),
                priority=Ticket.Priority.MEDIUM,
            )
        Call.objects.create(
            customer=self.apple,
            connector=self.zoom,
            title="Kick-off",
            occurred_at=timezone.make_aware(timezone.datetime(2026, 9, 11, 10, 0)),
            duration_minutes=30,
        )

    def test_the_list_says_what_each_connector_brought_in(self):
        self.client.force_authenticate(self.csm)
        response = self.client.get("/api/v1/connectors/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        by = {row["name"]: row for row in response.data}
        self.assertEqual(by["Zendesk"]["ticket_count"], 2)
        self.assertEqual(by["Zendesk"]["call_count"], 0)
        self.assertEqual(by["Zendesk"]["last_record_at"], "2026-09-12")
        self.assertEqual(by["Zoom"]["call_count"], 1)
        self.assertEqual(by["Zoom"]["last_record_at"], "2026-09-11")

    def test_a_connector_with_nothing_yet_says_so(self):
        slack = Connector.objects.create(
            organisation=self.org, provider=Connector.Provider.SLACK, name="Slack"
        )
        self.client.force_authenticate(self.csm)
        row = self.client.get(f"/api/v1/connectors/{slack.id}/").data
        self.assertEqual(
            (row["ticket_count"], row["call_count"], row["last_record_at"]), (0, 0, None)
        )

    def test_writes_need_manage_integrations(self):
        self.client.force_authenticate(self.csm)
        self.assertEqual(
            self.client.post(
                "/api/v1/connectors/", {"provider": "slack", "name": "Slack"}
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.client.force_authenticate(self.admin)
        created = self.client.post("/api/v1/connectors/", {"provider": "slack", "name": "Slack"})
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        self.assertEqual(created.data["ticket_count"], 0)
        toggled = self.client.patch(
            f"/api/v1/connectors/{created.data['id']}/", {"is_enabled": False}, format="json"
        )
        self.assertFalse(toggled.data["is_enabled"])
