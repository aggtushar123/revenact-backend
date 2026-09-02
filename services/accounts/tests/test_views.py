"""Integration tier: through the real URLconf + real test DB, one endpoint
at a time (rest_framework.test.APITestCase)."""

from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User


class SignupTests(APITestCase):
    url = "/api/v1/auth/signup/"

    def test_signup_creates_org_and_admin_user(self):
        response = self.client.post(
            self.url,
            {
                "organisation_name": "Acme Inc",
                "name": "Alice Admin",
                "email": "alice@acme.io",
                "password": "supersecret1",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(response.data["user"]["role"], "admin")
        self.assertEqual(response.data["user"]["organisation"]["name"], "Acme Inc")

        self.assertEqual(Organisation.objects.count(), 1)
        user = User.objects.get(email="alice@acme.io")
        self.assertEqual(user.role, User.Role.ADMIN)
        self.assertTrue(user.check_password("supersecret1"))

    def test_signup_rejects_duplicate_email(self):
        self._signup("alice@acme.io")
        response = self._signup("alice@acme.io", org_name="Other Org")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def _signup(self, email, org_name="Acme Inc"):
        return self.client.post(
            self.url,
            {
                "organisation_name": org_name,
                "name": "Someone",
                "email": email,
                "password": "supersecret1",
            },
            format="json",
        )


class LoginTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )

    def test_login_with_valid_credentials(self):
        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": "alice@acme.io", "password": "supersecret1"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(response.data["user"]["email"], "alice@acme.io")

    def test_login_with_wrong_password_is_rejected(self):
        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": "alice@acme.io", "password": "wrong"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_token_refresh(self):
        login = self.client.post(
            "/api/v1/auth/login/",
            {"email": "alice@acme.io", "password": "supersecret1"},
            format="json",
        )
        response = self.client.post(
            "/api/v1/auth/token/refresh/",
            {"refresh": login.data["refresh"]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)


class LogoutTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        login = self.client.post(
            "/api/v1/auth/login/",
            {"email": "alice@acme.io", "password": "supersecret1"},
            format="json",
        )
        self.access = login.data["access"]
        self.refresh = login.data["refresh"]

    def test_unauthenticated_cannot_logout(self):
        response = self.client.post(
            "/api/v1/auth/logout/", {"refresh": self.refresh}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_logout_blacklists_the_refresh_token(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")
        response = self.client.post(
            "/api/v1/auth/logout/", {"refresh": self.refresh}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_205_RESET_CONTENT)

        # The blacklisted refresh token can no longer mint a new access token.
        refresh_attempt = self.client.post(
            "/api/v1/auth/token/refresh/", {"refresh": self.refresh}, format="json"
        )
        self.assertEqual(refresh_attempt.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_logout_is_idempotent(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")
        first = self.client.post("/api/v1/auth/logout/", {"refresh": self.refresh}, format="json")
        second = self.client.post("/api/v1/auth/logout/", {"refresh": self.refresh}, format="json")
        self.assertEqual(first.status_code, status.HTTP_205_RESET_CONTENT)
        self.assertEqual(second.status_code, status.HTTP_205_RESET_CONTENT)

    def test_logout_rejects_garbage_token_gracefully(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")
        response = self.client.post(
            "/api/v1/auth/logout/", {"refresh": "not-a-real-token"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_205_RESET_CONTENT)


class CSMListCreateTests(APITestCase):
    url = "/api/v1/auth/csms/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
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

    def test_admin_can_add_csm(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self.url,
            {"name": "New CSM", "email": "new@acme.io", "password": "supersecret1"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["role"], "csm")
        new_user = User.objects.get(email="new@acme.io")
        self.assertEqual(new_user.organisation_id, self.org.id)

    def test_csm_cannot_add_csm(self):
        self.client.force_authenticate(self.csm)
        response = self.client.post(
            self.url,
            {"name": "New CSM", "email": "new@acme.io", "password": "supersecret1"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_cannot_add_csm(self):
        response = self.client.post(
            self.url,
            {"name": "New CSM", "email": "new@acme.io", "password": "supersecret1"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_admin_can_list_own_org_csms(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        emails = [row["email"] for row in response.data["results"]]
        self.assertEqual(emails, ["carl@acme.io"])
        # is_active must be visible here — the User Management UI needs it
        # to render each CSM's active/deactivated status.
        self.assertTrue(response.data["results"][0]["is_active"])

    def test_admin_does_not_see_csms_from_another_org(self):
        other_org = Organisation.objects.create(name="Other Org")
        other_admin = User.objects.create_user(
            email="other@other.io",
            password="supersecret1",
            name="Other",
            organisation=other_org,
            role=User.Role.ADMIN,
        )
        self.client.force_authenticate(other_admin)
        response = self.client.get(self.url)
        self.assertEqual(response.data["count"], 0)

    def test_csm_cannot_list_csms(self):
        self.client.force_authenticate(self.csm)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class MeViewTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.client.force_authenticate(self.user)

    def test_get_own_profile(self):
        response = self.client.get("/api/v1/auth/me/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["email"], "alice@acme.io")

    def test_unauthenticated_cannot_get_profile(self):
        self.client.force_authenticate(None)
        response = self.client.get("/api/v1/auth/me/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_can_update_own_name(self):
        response = self.client.patch("/api/v1/auth/me/", {"name": "Alice Renamed"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "Alice Renamed")
        self.user.refresh_from_db()
        self.assertEqual(self.user.name, "Alice Renamed")

    def test_cannot_change_own_email_or_role(self):
        response = self.client.patch(
            "/api/v1/auth/me/",
            {"email": "hacked@evil.io", "role": "csm"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "alice@acme.io")
        self.assertEqual(self.user.role, User.Role.ADMIN)


class ChangePasswordTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.client.force_authenticate(self.user)
        self.url = "/api/v1/auth/me/change-password/"

    def test_change_password_with_correct_current_password(self):
        response = self.client.post(
            self.url,
            {"current_password": "supersecret1", "new_password": "newpassword1"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("newpassword1"))
        self.assertFalse(self.user.check_password("supersecret1"))

    def test_change_password_rejects_wrong_current_password(self):
        response = self.client.post(
            self.url,
            {"current_password": "wrongpassword", "new_password": "newpassword1"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("supersecret1"))

    def test_unauthenticated_cannot_change_password(self):
        self.client.force_authenticate(None)
        response = self.client.post(
            self.url,
            {"current_password": "supersecret1", "new_password": "newpassword1"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class CSMDetailTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="csmpassword1",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.url = f"/api/v1/auth/csms/{self.csm.id}/"

    def test_admin_can_edit_csm_name(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(self.url, {"name": "Carl Renamed"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.csm.refresh_from_db()
        self.assertEqual(self.csm.name, "Carl Renamed")

    def test_admin_can_reset_csm_password_without_knowing_the_old_one(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(self.url, {"password": "brandnewpass1"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.csm.refresh_from_db()
        self.assertTrue(self.csm.check_password("brandnewpass1"))

    def test_admin_deactivating_csm_blacklists_their_outstanding_tokens(self):
        login = self.client.post(
            "/api/v1/auth/login/",
            {"email": "carl@acme.io", "password": "csmpassword1"},
            format="json",
        )
        csm_access = login.data["access"]

        self.client.force_authenticate(self.admin)
        response = self.client.patch(self.url, {"is_active": False}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # The CSM's still-unexpired access token is rejected immediately —
        # not just future logins.
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {csm_access}")
        me_response = self.client.get("/api/v1/auth/me/")
        self.assertEqual(me_response.status_code, status.HTTP_401_UNAUTHORIZED)

        login_attempt = self.client.post(
            "/api/v1/auth/login/",
            {"email": "carl@acme.io", "password": "csmpassword1"},
            format="json",
        )
        self.assertEqual(login_attempt.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_csm_cannot_edit_another_csm(self):
        other_csm = User.objects.create_user(
            email="dana@acme.io",
            password="supersecret1",
            name="Dana",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.client.force_authenticate(self.csm)
        response = self.client.patch(
            f"/api/v1/auth/csms/{other_csm.id}/", {"name": "Hacked"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_cannot_edit_csm_from_another_org(self):
        other_org = Organisation.objects.create(name="Other Org")
        other_admin = User.objects.create_user(
            email="other@other.io",
            password="supersecret1",
            name="Other",
            organisation=other_org,
            role=User.Role.ADMIN,
        )
        self.client.force_authenticate(other_admin)
        response = self.client.patch(self.url, {"name": "Pwned"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.csm.refresh_from_db()
        self.assertEqual(self.csm.name, "Carl")


class MembersListTests(APITestCase):
    """Unlike /csms/, /members/ is not admin-gated — any org member can use
    it to populate an owner-picker (e.g. for customers)."""

    url = "/api/v1/auth/members/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
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

    def test_csm_can_list_all_org_members_not_just_csms(self):
        self.client.force_authenticate(self.csm)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        emails = sorted(row["email"] for row in response.data)
        self.assertEqual(emails, ["alice@acme.io", "carl@acme.io"])

    def test_response_is_a_plain_list_not_paginated(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(self.url)
        self.assertIsInstance(response.data, list)

    def test_does_not_leak_another_organisations_members(self):
        other_org = Organisation.objects.create(name="Other Org")
        User.objects.create_user(
            email="other@other.io",
            password="supersecret1",
            name="Other",
            organisation=other_org,
            role=User.Role.ADMIN,
        )
        self.client.force_authenticate(self.admin)
        response = self.client.get(self.url)
        emails = [row["email"] for row in response.data]
        self.assertNotIn("other@other.io", emails)


class ForgotPasswordTests(APITestCase):
    url = "/api/v1/auth/password-reset/"

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io", password="oldpassword1", name="Alice Admin",
            organisation=org, role=User.Role.ADMIN,
        )

    def test_known_email_gets_a_reset_email(self):
        response = self.client.post(self.url, {"email": "alice@acme.io"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["alice@acme.io"])
        self.assertIn("reset-password?uid=", mail.outbox[0].body)

    def test_unknown_email_still_returns_200_but_sends_nothing(self):
        response = self.client.post(self.url, {"email": "nobody@nowhere.io"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 0)

    def test_lookup_is_case_insensitive(self):
        response = self.client.post(self.url, {"email": "ALICE@acme.io"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)

    def test_rejects_missing_email(self):
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class ResetPasswordTests(APITestCase):
    url = "/api/v1/auth/password-reset/confirm/"

    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io", password="oldpassword1", name="Alice Admin",
            organisation=org, role=User.Role.ADMIN,
        )
        self.uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        self.token = default_token_generator.make_token(self.user)

    def test_valid_token_resets_the_password(self):
        response = self.client.post(
            self.url,
            {"uid": self.uid, "token": self.token, "new_password": "newpassword1"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("newpassword1"))
        self.assertFalse(self.user.check_password("oldpassword1"))

    def test_token_cannot_be_reused_once_the_password_has_changed(self):
        # default_token_generator's hash includes the password field, so a
        # successful reset invalidates the same token for a second use.
        self.client.post(
            self.url,
            {"uid": self.uid, "token": self.token, "new_password": "newpassword1"},
            format="json",
        )
        response = self.client.post(
            self.url,
            {"uid": self.uid, "token": self.token, "new_password": "anotherpassword1"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_garbage_token_is_rejected(self):
        response = self.client.post(
            self.url,
            {"uid": self.uid, "token": "not-a-real-token", "new_password": "newpassword1"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_garbage_uid_is_rejected(self):
        response = self.client.post(
            self.url,
            {"uid": "not-a-real-uid", "token": self.token, "new_password": "newpassword1"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_uid_for_a_nonexistent_user_is_rejected(self):
        bogus_uid = urlsafe_base64_encode(force_bytes(999999))
        response = self.client.post(
            self.url,
            {"uid": bogus_uid, "token": self.token, "new_password": "newpassword1"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_new_password_too_short_is_rejected_and_old_one_still_works(self):
        response = self.client.post(
            self.url,
            {"uid": self.uid, "token": self.token, "new_password": "short"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("oldpassword1"))
