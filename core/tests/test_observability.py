"""Request ids and log redaction (SOC2:API-05, LOG-03)."""

import json
import logging

from django.test import SimpleTestCase, TestCase

from core.logging import JSONFormatter, RedactFilter, redact
from core.middleware import get_request_id


class RedactionTests(SimpleTestCase):
    def test_credentials_are_scrubbed(self):
        cases = {
            "Authorization: Bearer abc.def.ghi": "Authorization: Bearer [REDACTED]",
            "password=hunter22 user=bob": "password=[REDACTED] user=bob",
            '{"refresh": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghijklmnop"}': (
                '{"refresh": "[REDACTED]"}'
            ),
            "api_key: sk-live-123": "api_key: [REDACTED]",
            "plain message with nothing secret": "plain message with nothing secret",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(redact(raw), expected)

    def test_filter_rewrites_the_record_before_formatting(self):
        record = logging.LogRecord(
            "x", logging.INFO, __file__, 1, "token=%s for %s", ("secret-value", "bob"), None
        )
        self.assertTrue(RedactFilter().filter(record))
        payload = json.loads(JSONFormatter().format(record))
        self.assertEqual(payload["message"], "token=[REDACTED] for bob")
        self.assertEqual(payload["level"], "INFO")
        self.assertIn("ts", payload)


class RequestIDTests(TestCase):
    def test_generated_when_missing_and_echoed_in_the_header(self):
        response = self.client.get("/api/v1/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response["X-Request-ID"]), 32)

    def test_client_supplied_id_is_kept_only_when_safe(self):
        kept = self.client.get("/api/v1/health/", HTTP_X_REQUEST_ID="trace-42")
        self.assertEqual(kept["X-Request-ID"], "trace-42")
        replaced = self.client.get("/api/v1/health/", HTTP_X_REQUEST_ID="bad id\nX-Injected: 1")
        self.assertNotEqual(replaced["X-Request-ID"], "bad id\nX-Injected: 1")
        self.assertEqual(len(replaced["X-Request-ID"]), 32)

    def test_context_is_cleared_after_the_request(self):
        self.client.get("/api/v1/health/", HTTP_X_REQUEST_ID="trace-42")
        self.assertEqual(get_request_id(), "")
