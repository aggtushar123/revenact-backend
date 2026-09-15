"""Brute-force protection on the public auth endpoints (SOC2:AUTH-06).

Rates are None under `manage.py test` (settings.TESTING), so each test pins
a rate on the throttle class it exercises and clears the cache."""

from unittest import mock

from django.core.cache import cache
from rest_framework.test import APITestCase

from core.throttling import LoginAccountThrottle, LoginIPThrottle, PasswordResetThrottle
from services.accounts.models import Organisation, User


class LoginThrottleTests(APITestCase):
    url = "/api/v1/auth/login/"

    def setUp(self):
        cache.clear()
        org = Organisation.objects.create(name="Acme Inc")
        User.objects.create_user(
            email="alice@acme.io",
            password="correct-horse-battery",
            name="Alice",
            organisation=org,
            role=User.Role.ADMIN,
        )

    def _attempt(self, email="alice@acme.io", ip="203.0.113.9"):
        return self.client.post(
            self.url,
            {"email": email, "password": "wrong-wrong-wrong"},
            format="json",
            REMOTE_ADDR=ip,
        )

    @mock.patch.object(LoginIPThrottle, "rate", "2/min")
    def test_per_ip_limit_returns_429_with_retry_after(self):
        self.assertEqual(self._attempt(email="a@acme.io").status_code, 401)
        self.assertEqual(self._attempt(email="b@acme.io").status_code, 401)
        blocked = self._attempt(email="c@acme.io")
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("Retry-After", blocked)
        # A different source IP is not affected.
        self.assertEqual(self._attempt(email="d@acme.io", ip="198.51.100.1").status_code, 401)

    @mock.patch.object(LoginAccountThrottle, "rate", "2/min")
    def test_per_account_limit_spans_source_ips(self):
        self.assertEqual(self._attempt(ip="203.0.113.1").status_code, 401)
        self.assertEqual(self._attempt(ip="203.0.113.2").status_code, 401)
        self.assertEqual(self._attempt(ip="203.0.113.3").status_code, 429)
        # Another account from those IPs is still fine.
        self.assertEqual(self._attempt(email="bob@acme.io", ip="203.0.113.3").status_code, 401)

    @mock.patch.object(LoginIPThrottle, "rate", "2/min")
    def test_successful_logins_count_too(self):
        good = {"email": "alice@acme.io", "password": "correct-horse-battery"}
        self.assertEqual(self.client.post(self.url, good, format="json").status_code, 200)
        self.assertEqual(self.client.post(self.url, good, format="json").status_code, 200)
        self.assertEqual(self.client.post(self.url, good, format="json").status_code, 429)


class PasswordResetThrottleTests(APITestCase):
    def setUp(self):
        cache.clear()

    @mock.patch.object(PasswordResetThrottle, "rate", "1/hour")
    def test_reset_request_is_rate_limited(self):
        url = "/api/v1/auth/password-reset/"
        self.assertEqual(
            self.client.post(url, {"email": "x@acme.io"}, format="json").status_code, 200
        )
        self.assertEqual(
            self.client.post(url, {"email": "x@acme.io"}, format="json").status_code, 429
        )
