"""core.audit + the accounts views that emit through it (SOC2:LOG-01, LOG-02)."""

from rest_framework.test import APITestCase

from core.models import AuditEvent
from services.accounts.models import Organisation, User

PASSWORD = "correct-horse-battery"


class AuditTrailTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password=PASSWORD,
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )

    def _login(self, email="alice@acme.io", password=PASSWORD, **extra):
        return self.client.post(
            "/api/v1/auth/login/", {"email": email, "password": password}, format="json", **extra
        )

    def test_successful_login_is_recorded_with_actor_ip_and_request_id(self):
        response = self._login(
            REMOTE_ADDR="203.0.113.9", HTTP_X_REQUEST_ID="req-abc-123", HTTP_USER_AGENT="pytest"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Request-ID"], "req-abc-123")

        event = AuditEvent.objects.get(action="auth.login")
        self.assertEqual(event.outcome, AuditEvent.Outcome.SUCCESS)
        self.assertEqual(event.actor, self.admin)
        self.assertEqual(event.actor_email, "alice@acme.io")
        self.assertEqual(event.organisation, self.org)
        self.assertEqual(event.ip, "203.0.113.9")
        self.assertEqual(event.request_id, "req-abc-123")
        self.assertEqual(event.user_agent, "pytest")

    def test_client_ip_comes_from_x_forwarded_for_behind_the_proxy(self):
        # NUM_PROXIES=1: the last hop is what Caddy appended — the real client.
        self._login(REMOTE_ADDR="10.0.0.2", HTTP_X_FORWARDED_FOR="198.51.100.7, 10.0.0.2")
        # DRF's get_ident with one proxy takes addrs[-1]; audit.client_ip mirrors it.
        event = AuditEvent.objects.get(action="auth.login")
        self.assertEqual(event.ip, "10.0.0.2")

    def test_failed_login_is_recorded_without_the_password(self):
        response = self._login(password="wrong-password-xyz", REMOTE_ADDR="203.0.113.9")
        self.assertEqual(response.status_code, 401)

        event = AuditEvent.objects.get(action="auth.login")
        self.assertEqual(event.outcome, AuditEvent.Outcome.FAILURE)
        self.assertIsNone(event.actor)
        self.assertEqual(event.metadata, {"email": "alice@acme.io"})
        self.assertNotIn("password", str(event.metadata))

    def test_unknown_email_login_failure_is_recorded(self):
        self._login(email="nobody@acme.io", password="whatever-whatever")
        event = AuditEvent.objects.get(action="auth.login", outcome="failure")
        self.assertEqual(event.metadata["email"], "nobody@acme.io")

    def test_signup_records_new_tenant(self):
        response = self.client.post(
            "/api/v1/auth/signup/",
            {
                "organisation_name": "Globex",
                "name": "Hank",
                "email": "hank@globex.io",
                "password": "another-long-passphrase",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        event = AuditEvent.objects.get(action="auth.signup")
        self.assertEqual(event.actor_email, "hank@globex.io")
        self.assertEqual(event.target_type, "accounts.organisation")
        self.assertEqual(event.metadata, {"organisation": "Globex"})

    def test_deactivating_a_user_is_recorded_with_field_names_only(self):
        csm = User.objects.create_user(
            email="carol@acme.io",
            password=PASSWORD,
            name="Carol",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"/api/v1/auth/users/{csm.pk}/",
            {"is_active": False, "password": "brand-new-passphrase-1"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        actions = list(AuditEvent.objects.order_by("id").values_list("action", flat=True))
        self.assertEqual(actions, ["user.deactivate", "user.update"])
        update = AuditEvent.objects.get(action="user.update")
        self.assertEqual(update.actor, self.admin)
        self.assertEqual(update.target_id, str(csm.pk))
        self.assertEqual(update.metadata, {"fields": ["is_active", "password"]})
        self.assertNotIn("brand-new", str(update.metadata))

    def test_password_change_and_logout_are_recorded(self):
        self.client.force_authenticate(self.admin)
        self.client.post(
            "/api/v1/auth/me/change-password/",
            {"current_password": PASSWORD, "new_password": "yet-another-long-one"},
            format="json",
        )
        self.client.post("/api/v1/auth/logout/", {"refresh": "not-a-token"}, format="json")
        self.assertEqual(
            set(AuditEvent.objects.values_list("action", flat=True)),
            {"auth.password_change", "auth.logout"},
        )


class AuditEventModelTests(APITestCase):
    def test_rows_are_append_only(self):
        from core import audit

        event = audit.record("test.event", metadata={"password": "x", "fields": ["a"]})
        self.assertEqual(event.metadata, {"fields": ["a"]})  # credential keys dropped

        event.action = "tampered"
        with self.assertRaises(TypeError):
            event.save()
        with self.assertRaises(TypeError):
            event.delete()
        self.assertEqual(AuditEvent.objects.get(pk=event.pk).action, "test.event")
