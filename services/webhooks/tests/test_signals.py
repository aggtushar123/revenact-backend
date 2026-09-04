"""Integration tier: the real post_save signal, real transaction commit
behavior. `send_webhook` itself is mocked — whether it delivers
correctly is test_engine.py's own job; this only checks *which*
webhooks get called for a given Customer creation."""

from unittest.mock import patch

from django.test import TestCase

from services.accounts.models import Organisation
from services.customers.models import Customer
from services.webhooks.models import WebhookSubscription


class CustomerCreatedSignalTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")

    def test_active_webhook_fires_on_customer_creation(self):
        WebhookSubscription.objects.create(
            organisation=self.org,
            url="https://example.com/hook",
            event="customer.created",
            is_active=True,
        )
        with patch("services.webhooks.signals.send_webhook") as mock_send:
            with self.captureOnCommitCallbacks(execute=True):
                customer = Customer.objects.create(organisation=self.org, name="Globex")

        mock_send.assert_called_once()
        webhook_arg, event_arg, payload_arg = mock_send.call_args[0]
        self.assertEqual(event_arg, "customer.created")
        self.assertEqual(payload_arg["name"], "Globex")
        self.assertEqual(payload_arg["id"], customer.id)

    def test_inactive_webhook_does_not_fire(self):
        WebhookSubscription.objects.create(
            organisation=self.org,
            url="https://example.com/hook",
            event="customer.created",
            is_active=False,
        )
        with patch("services.webhooks.signals.send_webhook") as mock_send:
            with self.captureOnCommitCallbacks(execute=True):
                Customer.objects.create(organisation=self.org, name="Globex")

        mock_send.assert_not_called()

    def test_does_not_fire_for_a_different_organisation(self):
        other_org = Organisation.objects.create(name="Other Inc")
        WebhookSubscription.objects.create(
            organisation=other_org,
            url="https://example.com/hook",
            event="customer.created",
            is_active=True,
        )
        with patch("services.webhooks.signals.send_webhook") as mock_send:
            with self.captureOnCommitCallbacks(execute=True):
                Customer.objects.create(organisation=self.org, name="Globex")

        mock_send.assert_not_called()

    def test_does_not_fire_on_update_only_on_create(self):
        customer = Customer.objects.create(organisation=self.org, name="Globex")
        WebhookSubscription.objects.create(
            organisation=self.org,
            url="https://example.com/hook",
            event="customer.created",
            is_active=True,
        )
        with patch("services.webhooks.signals.send_webhook") as mock_send:
            with self.captureOnCommitCallbacks(execute=True):
                customer.name = "Globex Renamed"
                customer.save()

        mock_send.assert_not_called()
