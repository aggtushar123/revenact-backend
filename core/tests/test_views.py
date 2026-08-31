"""Tests for core.views.health_check.

Reference example for the testing skill's unit/integration split — see
.claude/skills/testing/SKILL.md. No end-to-end tier here: a single liveness
check isn't a multi-step user-facing flow, so LiveServerTestCase coverage
starts with the first real feature.
"""

from django.test import SimpleTestCase
from rest_framework.test import APIClient, APIRequestFactory

from core.views import health_check


class HealthCheckUnitTests(SimpleTestCase):
    """Unit tier: call the view function directly, no URL routing, no DB."""

    def test_returns_ok_status_and_service_name(self):
        request = APIRequestFactory().get("/api/v1/health/")
        response = health_check(request)
        response.render()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "ok")
        self.assertEqual(response.data["service"], "revenact-backend")
        self.assertIn("time", response.data)


class HealthCheckIntegrationTests(SimpleTestCase):
    """Integration tier: through the real URLconf, as a client would call it."""

    def test_get_health_endpoint(self):
        response = APIClient().get("/api/v1/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data.keys()), {"status", "service", "time"})
        self.assertEqual(response.data["status"], "ok")

    def test_health_endpoint_requires_no_auth(self):
        # AllowAny per REST_FRAMEWORK settings — an unauthenticated client
        # must still get 200, not 401/403.
        response = APIClient().get("/api/v1/health/")
        self.assertEqual(response.status_code, 200)
