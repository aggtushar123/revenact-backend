"""Unit tier: model defaults, no HTTP, no network."""

from django.test import TestCase

from services.accounts.models import Organisation
from services.webhooks.models import WebhookDelivery, WebhookSubscription


class WebhookSubscriptionDefaultsTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")

    def test_defaults(self):
        webhook = WebhookSubscription.objects.create(
            organisation=self.org,
            url="https://example.com/hook",
            event=WebhookSubscription.Event.CUSTOMER_CREATED,
        )
        self.assertTrue(webhook.is_active)
        self.assertTrue(webhook.secret)
        self.assertGreaterEqual(len(webhook.secret), 32)

    def test_secret_is_unique_per_webhook(self):
        one = WebhookSubscription.objects.create(
            organisation=self.org,
            url="https://a.example.com",
            event=WebhookSubscription.Event.CUSTOMER_CREATED,
        )
        two = WebhookSubscription.objects.create(
            organisation=self.org,
            url="https://b.example.com",
            event=WebhookSubscription.Event.CUSTOMER_CREATED,
        )
        self.assertNotEqual(one.secret, two.secret)


class WebhookDeliveryOrderingTests(TestCase):
    def test_ordered_most_recent_first(self):
        org = Organisation.objects.create(name="Acme Inc")
        webhook = WebhookSubscription.objects.create(
            organisation=org,
            url="https://example.com/hook",
            event=WebhookSubscription.Event.CUSTOMER_CREATED,
        )
        older = WebhookDelivery.objects.create(webhook=webhook, success=True, status_code=200)
        newer = WebhookDelivery.objects.create(webhook=webhook, success=False, error="timed out")
        self.assertEqual(list(webhook.deliveries.all()), [newer, older])
