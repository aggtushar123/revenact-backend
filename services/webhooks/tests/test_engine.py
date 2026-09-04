"""Unit tier: engine.py's own URL safety checks and delivery recording.
`socket.getaddrinfo` is mocked throughout — a real DNS lookup has no
place in a test suite, and pinning exactly what a hostname "resolves
to" is the whole point of these tests anyway."""

import hashlib
import hmac
import json
import socket
import urllib.error
from unittest.mock import Mock, patch

from django.test import TestCase

from services.accounts.models import Organisation
from services.webhooks.engine import UnsafeWebhookURLError, send_webhook, validate_webhook_url
from services.webhooks.models import WebhookDelivery, WebhookSubscription


def addrinfo_for(ip):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


class ValidateWebhookUrlTests(TestCase):
    def test_rejects_non_http_scheme(self):
        with self.assertRaises(UnsafeWebhookURLError):
            validate_webhook_url("ftp://example.com/hook")

    def test_rejects_unresolvable_host(self):
        with patch("services.webhooks.engine.socket.getaddrinfo", side_effect=socket.gaierror):
            with self.assertRaises(UnsafeWebhookURLError):
                validate_webhook_url("https://no-such-host.invalid/hook")

    def test_rejects_loopback(self):
        with patch(
            "services.webhooks.engine.socket.getaddrinfo", return_value=addrinfo_for("127.0.0.1")
        ):
            with self.assertRaises(UnsafeWebhookURLError):
                validate_webhook_url("http://localhost/hook")

    def test_rejects_private_range(self):
        with patch(
            "services.webhooks.engine.socket.getaddrinfo", return_value=addrinfo_for("10.0.0.5")
        ):
            with self.assertRaises(UnsafeWebhookURLError):
                validate_webhook_url("http://internal.corp/hook")

    def test_rejects_link_local_metadata_address(self):
        # 169.254.169.254 — the cloud-metadata address SSRF payloads
        # almost always target.
        with patch(
            "services.webhooks.engine.socket.getaddrinfo",
            return_value=addrinfo_for("169.254.169.254"),
        ):
            with self.assertRaises(UnsafeWebhookURLError):
                validate_webhook_url("http://169.254.169.254/latest/meta-data")

    def test_accepts_a_public_address(self):
        with patch(
            "services.webhooks.engine.socket.getaddrinfo",
            return_value=addrinfo_for("93.184.216.34"),
        ):
            validate_webhook_url("https://example.com/hook")  # doesn't raise


class SendWebhookTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.webhook = WebhookSubscription.objects.create(
            organisation=self.org,
            url="https://example.com/hook",
            event=WebhookSubscription.Event.CUSTOMER_CREATED,
        )

    def test_unsafe_url_records_a_failed_delivery_without_opening_a_connection(self):
        with patch(
            "services.webhooks.engine.socket.getaddrinfo", return_value=addrinfo_for("127.0.0.1")
        ):
            with patch("services.webhooks.engine._opener.open") as mock_open:
                send_webhook(self.webhook, "customer.created", {"id": 1})
                mock_open.assert_not_called()

        delivery = WebhookDelivery.objects.get(webhook=self.webhook)
        self.assertFalse(delivery.success)
        self.assertIn("private or internal", delivery.error)

    def test_successful_delivery_is_recorded_and_signed(self):
        mock_response = Mock()
        mock_response.status = 200
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)

        with patch(
            "services.webhooks.engine.socket.getaddrinfo",
            return_value=addrinfo_for("93.184.216.34"),
        ):
            with patch(
                "services.webhooks.engine._opener.open", return_value=mock_response
            ) as mock_open:
                send_webhook(self.webhook, "customer.created", {"id": 1, "name": "Globex"})

        sent_request = mock_open.call_args[0][0]
        expected_body = json.dumps(
            {"event": "customer.created", "data": {"id": 1, "name": "Globex"}}
        ).encode("utf-8")
        expected_signature = hmac.new(
            self.webhook.secret.encode("utf-8"), expected_body, hashlib.sha256
        ).hexdigest()
        self.assertEqual(sent_request.data, expected_body)
        self.assertEqual(sent_request.get_header("X-revenact-signature"), expected_signature)

        delivery = WebhookDelivery.objects.get(webhook=self.webhook)
        self.assertTrue(delivery.success)
        self.assertEqual(delivery.status_code, 200)

    def test_http_error_is_recorded_as_a_failed_delivery(self):
        with patch(
            "services.webhooks.engine.socket.getaddrinfo",
            return_value=addrinfo_for("93.184.216.34"),
        ):
            with patch(
                "services.webhooks.engine._opener.open",
                side_effect=urllib.error.HTTPError(
                    "https://example.com/hook", 500, "Server Error", {}, None
                ),
            ):
                send_webhook(self.webhook, "customer.created", {"id": 1})

        delivery = WebhookDelivery.objects.get(webhook=self.webhook)
        self.assertFalse(delivery.success)
        self.assertEqual(delivery.status_code, 500)

    def test_connection_failure_is_recorded_without_raising(self):
        with patch(
            "services.webhooks.engine.socket.getaddrinfo",
            return_value=addrinfo_for("93.184.216.34"),
        ):
            with patch(
                "services.webhooks.engine._opener.open", side_effect=OSError("Connection refused")
            ):
                send_webhook(self.webhook, "customer.created", {"id": 1})  # doesn't raise

        delivery = WebhookDelivery.objects.get(webhook=self.webhook)
        self.assertFalse(delivery.success)
        self.assertIn("Connection refused", delivery.error)

    def test_redirects_are_not_followed(self):
        # _NoRedirectHandler.redirect_request returning None means
        # urllib treats the 3xx itself as terminal — verified here via
        # the handler directly rather than a full network round trip.
        from services.webhooks.engine import _NoRedirectHandler

        handler = _NoRedirectHandler()
        self.assertIsNone(
            handler.redirect_request(Mock(), Mock(), 302, "Found", {}, "https://internal/")
        )
