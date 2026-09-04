"""Integration tier: through the real URLconf + real test DB. Every
outbound DNS/HTTP call is mocked — see test_engine.py's own docstring
on why a real lookup has no place here."""

import socket
from unittest.mock import patch

from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.webhooks.models import WebhookSubscription


def addrinfo_for(ip):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


PUBLIC_IP_PATCH = patch(
    "services.webhooks.engine.socket.getaddrinfo", return_value=addrinfo_for("93.184.216.34")
)


class WebhookListCreateTests(APITestCase):
    url = "/api/v1/webhooks/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.other_org = Organisation.objects.create(name="Other Inc")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="supersecret1",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
        )

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_csm_cannot_list_or_create(self):
        self.client.force_authenticate(self.csm)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_403_FORBIDDEN)
        response = self.client.post(
            self.url,
            {"url": "https://example.com/hook", "event": "customer.created"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_create_and_list_only_own_organisation(self):
        WebhookSubscription.objects.create(
            organisation=self.other_org,
            url="https://other.example.com/hook",
            event="customer.created",
        )
        self.client.force_authenticate(self.admin)
        with PUBLIC_IP_PATCH:
            response = self.client.post(
                self.url,
                {"url": "https://example.com/hook", "event": "customer.created"},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["secret"])
        self.assertEqual(response.data["event_display"], "Organization Created")

        list_response = self.client.get(self.url)
        self.assertEqual(len(list_response.data), 1)
        self.assertEqual(list_response.data[0]["url"], "https://example.com/hook")

    def test_cannot_set_a_custom_secret(self):
        self.client.force_authenticate(self.admin)
        with PUBLIC_IP_PATCH:
            response = self.client.post(
                self.url,
                {
                    "url": "https://example.com/hook",
                    "event": "customer.created",
                    "secret": "hacked",
                },
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotEqual(response.data["secret"], "hacked")

    def test_rejects_a_url_resolving_to_a_private_address(self):
        self.client.force_authenticate(self.admin)
        with patch(
            "services.webhooks.engine.socket.getaddrinfo", return_value=addrinfo_for("10.0.0.5")
        ):
            response = self.client.post(
                self.url,
                {"url": "http://internal.corp/hook", "event": "customer.created"},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(WebhookSubscription.objects.count(), 0)


class WebhookDetailTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.other_org = Organisation.objects.create(name="Other Inc")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.webhook = WebhookSubscription.objects.create(
            organisation=self.org, url="https://example.com/hook", event="customer.created"
        )
        self.foreign_webhook = WebhookSubscription.objects.create(
            organisation=self.other_org,
            url="https://other.example.com/hook",
            event="customer.created",
        )

    def test_admin_can_toggle_is_active(self):
        self.client.force_authenticate(self.admin)
        url = f"/api/v1/webhooks/{self.webhook.id}/"
        response = self.client.patch(url, {"is_active": False}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.webhook.refresh_from_db()
        self.assertFalse(self.webhook.is_active)

    def test_404_for_a_webhook_outside_own_organisation(self):
        self.client.force_authenticate(self.admin)
        url = f"/api/v1/webhooks/{self.foreign_webhook.id}/"
        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)

    def test_admin_can_delete(self):
        self.client.force_authenticate(self.admin)
        url = f"/api/v1/webhooks/{self.webhook.id}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(WebhookSubscription.objects.filter(pk=self.webhook.id).exists())

    def test_includes_recent_deliveries(self):
        from services.webhooks.models import WebhookDelivery

        WebhookDelivery.objects.create(webhook=self.webhook, success=True, status_code=200)
        self.client.force_authenticate(self.admin)
        response = self.client.get(f"/api/v1/webhooks/{self.webhook.id}/")
        self.assertEqual(len(response.data["recent_deliveries"]), 1)
        self.assertTrue(response.data["recent_deliveries"][0]["success"])
