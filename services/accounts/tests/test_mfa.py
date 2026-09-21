"""The second factor for the accounts that administer everyone.

Defended: a code works once; a wrong or replayed code is refused; a password
login for an enrolled person yields a challenge, not a session; only the
second-factor login mints the `mfa` claim; platform access needs both a
superuser and that claim; recovery codes are single use; disabling needs a
current code.
"""

import time
from unittest.mock import patch

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from core.models import AuditEvent
from services.accounts import mfa
from services.accounts.models import Organisation, TOTPDevice, User
from services.accounts.permissions import IsPlatformStaff


def code_for(secret, at=None):
    return mfa._code_at(secret, mfa.current_counter(at))


class AlgorithmTests(TestCase):
    def test_rfc_6238_reference_vector(self):
        """The RFC's SHA-1 test secret at T=59 s yields ...287082 (8 digits);
        our six-digit code is the last six of that."""
        secret = mfa.base64.b32encode(b"12345678901234567890").decode()
        self.assertEqual(mfa._code_at(secret, 59 // 30), "287082")

    def test_a_code_matches_within_the_drift_window_only(self):
        secret = mfa.generate_secret()
        now = 1_800_000_000
        self.assertIsNotNone(mfa.match_counter(secret, code_for(secret, now), at=now))
        self.assertIsNotNone(mfa.match_counter(secret, code_for(secret, now - 30), at=now))
        self.assertIsNone(mfa.match_counter(secret, code_for(secret, now - 90), at=now))
        self.assertIsNone(mfa.match_counter(secret, "000000", at=now))
        self.assertIsNone(mfa.match_counter(secret, "12345", at=now))


class EnrolmentTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme")
        self.user = User.objects.create_user(
            email="alice@acme.io", password="supersecret-pw-1", name="Alice", organisation=self.org
        )

    def test_enrolment_confirms_with_a_correct_code_and_hands_out_recovery_codes_once(self):
        device, secret = mfa.begin_enrolment(self.user)
        self.assertFalse(mfa.is_enrolled(self.user), "pending grants nothing")
        self.assertNotIn(secret, device.secret_encrypted, "never in clear at rest")

        codes = mfa.confirm_enrolment(self.user, code_for(secret))
        self.assertEqual(len(codes), mfa.RECOVERY_CODE_COUNT)
        self.assertTrue(mfa.is_enrolled(self.user))
        device.refresh_from_db()
        self.assertNotIn(codes[0], device.recovery_codes, "hashed, not stored")

    def test_a_wrong_code_does_not_confirm(self):
        mfa.begin_enrolment(self.user)
        with self.assertRaises(mfa.MFAError):
            mfa.confirm_enrolment(self.user, "000000")
        self.assertFalse(mfa.is_enrolled(self.user))

    def test_restarting_enrolment_replaces_the_abandoned_secret(self):
        _, first = mfa.begin_enrolment(self.user)
        _, second = mfa.begin_enrolment(self.user)
        self.assertNotEqual(first, second)
        with self.assertRaises(mfa.MFAError):
            mfa.confirm_enrolment(self.user, code_for(first))

    def test_a_code_is_accepted_once(self):
        _, secret = mfa.begin_enrolment(self.user)
        mfa.confirm_enrolment(self.user, code_for(secret))
        later = time.time() + 60  # the confirming code's step is spent; move on
        with patch("services.accounts.mfa.time.time", return_value=later):
            code = code_for(secret, later)
            self.assertEqual(mfa.verify(self.user, code), "totp")
            with self.assertRaises(mfa.MFAError) as caught:
                mfa.verify(self.user, code)
        self.assertEqual(caught.exception.code, "MFA_CODE_REUSED")

    def test_a_recovery_code_works_once(self):
        _, secret = mfa.begin_enrolment(self.user)
        codes = mfa.confirm_enrolment(self.user, code_for(secret))
        self.assertEqual(mfa.verify(self.user, codes[0].upper()), "recovery")
        with self.assertRaises(mfa.MFAError):
            mfa.verify(self.user, codes[0])
        self.assertEqual(mfa.verify(self.user, codes[1]), "recovery")

    def test_disabling_needs_a_current_code(self):
        _, secret = mfa.begin_enrolment(self.user)
        mfa.confirm_enrolment(self.user, code_for(secret))
        with self.assertRaises(mfa.MFAError):
            mfa.disable(self.user, "000000")
        self.assertTrue(mfa.is_enrolled(self.user))
        later = time.time() + 60
        with patch("services.accounts.mfa.time.time", return_value=later):
            mfa.disable(self.user, code_for(secret, later))
        self.assertFalse(TOTPDevice.objects.filter(user=self.user).exists())


class LoginFlowTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme")
        self.staff = User.objects.create_superuser(
            email="staff@revenact.io", password="supersecret-pw-1", name="Staff"
        )
        self.member = User.objects.create_user(
            email="alice@acme.io", password="supersecret-pw-1", name="Alice", organisation=self.org
        )

    def _enrol(self, user):
        _, secret = mfa.begin_enrolment(user)
        mfa.confirm_enrolment(user, code_for(secret))
        return secret

    def test_someone_without_a_second_factor_logs_in_as_before(self):
        response = self.client.post(
            "/api/v1/auth/login/", {"email": "alice@acme.io", "password": "supersecret-pw-1"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertFalse(AccessToken(response.data["access"]).get("mfa", False))

    def test_an_enrolled_person_gets_a_challenge_then_tokens(self):
        secret = self._enrol(self.staff)

        first = self.client.post(
            "/api/v1/auth/login/", {"email": "staff@revenact.io", "password": "supersecret-pw-1"}
        )
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertTrue(first.data["mfa_required"])
        self.assertNotIn("access", first.data, "no session until the second factor")

        later = time.time() + 60
        with patch("services.accounts.mfa.time.time", return_value=later):
            second = self.client.post(
                "/api/v1/auth/login/mfa/",
                {"mfa_token": first.data["mfa_token"], "code": code_for(secret, later)},
            )
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertTrue(AccessToken(second.data["access"]).get("mfa"))
        self.assertEqual(second.data["user"]["email"], "staff@revenact.io")
        self.assertTrue(
            AuditEvent.objects.filter(action="auth.login", metadata__mfa="totp").exists()
        )

    def test_a_wrong_second_factor_is_refused_and_audited(self):
        self._enrol(self.staff)
        first = self.client.post(
            "/api/v1/auth/login/", {"email": "staff@revenact.io", "password": "supersecret-pw-1"}
        )
        second = self.client.post(
            "/api/v1/auth/login/mfa/", {"mfa_token": first.data["mfa_token"], "code": "000000"}
        )
        self.assertEqual(second.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(second.data["error"]["code"], "MFA_CODE_INVALID")
        self.assertTrue(AuditEvent.objects.filter(action="auth.login", outcome="failure").exists())

    def test_a_forged_or_stale_challenge_is_refused(self):
        response = self.client.post("/api/v1/auth/login/mfa/", {"mfa_token": "nope", "code": "1"})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data["error"]["code"], "MFA_CHALLENGE_INVALID")

    def test_the_mfa_claim_survives_a_refresh(self):
        secret = self._enrol(self.staff)
        first = self.client.post(
            "/api/v1/auth/login/", {"email": "staff@revenact.io", "password": "supersecret-pw-1"}
        )
        later = time.time() + 60
        with patch("services.accounts.mfa.time.time", return_value=later):
            second = self.client.post(
                "/api/v1/auth/login/mfa/",
                {"mfa_token": first.data["mfa_token"], "code": code_for(secret, later)},
            )
        refreshed = self.client.post(
            "/api/v1/auth/token/refresh/", {"refresh": second.data["refresh"]}
        )
        self.assertEqual(refreshed.status_code, status.HTTP_200_OK)
        self.assertTrue(AccessToken(refreshed.data["access"]).get("mfa"))

    def test_enrolment_endpoints(self):
        self.client.force_authenticate(self.member)
        setup = self.client.post("/api/v1/auth/me/mfa/setup/")
        self.assertEqual(setup.status_code, status.HTTP_200_OK)
        self.assertTrue(setup.data["otpauth_uri"].startswith("otpauth://totp/Revenact"))

        confirm = self.client.post(
            "/api/v1/auth/me/mfa/confirm/", {"code": code_for(setup.data["secret"])}
        )
        self.assertEqual(confirm.status_code, status.HTTP_200_OK)
        self.assertEqual(len(confirm.data["recovery_codes"]), mfa.RECOVERY_CODE_COUNT)
        self.assertTrue(self.client.get("/api/v1/auth/me/").data["mfa_enrolled"])
        self.assertTrue(AuditEvent.objects.filter(action="mfa.enrolled").exists())

        again = self.client.post("/api/v1/auth/me/mfa/setup/")
        self.assertEqual(again.data["error"]["code"], "MFA_ALREADY_ENROLLED")


class PlatformPermissionTests(APITestCase):
    """The permission itself, exercised directly: the platform views land in
    the next phase and inherit this."""

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme")
        self.staff = User.objects.create_superuser(
            email="staff@revenact.io", password="supersecret-pw-1", name="Staff"
        )
        self.admin = User.objects.create_user(
            email="alice@acme.io", password="supersecret-pw-1", name="Alice", organisation=self.org
        )

    def _check(self, user, token):
        from types import SimpleNamespace

        request = SimpleNamespace(user=user, auth=token)
        return IsPlatformStaff().has_permission(request, None)

    def test_a_tenant_admin_is_never_platform_staff(self):
        token = AccessToken.for_user(self.admin)
        token["mfa"] = True
        self.assertFalse(self._check(self.admin, token))

    def test_a_superuser_without_the_second_factor_is_told_to_enrol(self):
        permission = IsPlatformStaff()
        from types import SimpleNamespace

        request = SimpleNamespace(user=self.staff, auth=AccessToken.for_user(self.staff))
        self.assertFalse(permission.has_permission(request, None))
        self.assertEqual(permission.message, "MFA_REQUIRED")

    def test_a_superuser_with_the_claim_passes(self):
        token = AccessToken.for_user(self.staff)
        token["mfa"] = True
        self.assertTrue(self._check(self.staff, token))
