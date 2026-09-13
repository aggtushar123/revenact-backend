"""Integration tier: through the real URLconf + real test DB, one endpoint
at a time (rest_framework.test.APITestCase)."""

from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.capabilities import Capability
from services.accounts.models import Organisation, Role, User


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
        self.assertEqual(user.role.slug, User.Role.ADMIN)
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


class OrgUserListCreateTests(APITestCase):
    url = "/api/v1/auth/users/"

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

    def test_admin_can_list_every_member_including_themselves(self):
        """This list used to be CSMs only, which meant an admin couldn't
        see or manage themselves or any fellow admin on the Users page."""

        self.client.force_authenticate(self.admin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        emails = sorted(row["email"] for row in response.data["results"])
        self.assertEqual(emails, ["alice@acme.io", "carl@acme.io"])
        # is_active must be visible here — the User Management UI needs it
        # to render each member's active/deactivated status.
        self.assertTrue(all(row["is_active"] for row in response.data["results"]))

    def test_the_list_carries_each_members_real_role_and_capabilities(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(self.url)
        by_email = {row["email"]: row for row in response.data["results"]}

        self.assertEqual(by_email["alice@acme.io"]["role"], User.Role.ADMIN)
        self.assertEqual(by_email["alice@acme.io"]["role_name"], "Admin")
        self.assertIn(Capability.MANAGE_USERS, by_email["alice@acme.io"]["permissions"])
        self.assertEqual(by_email["carl@acme.io"]["role"], User.Role.CSM)
        self.assertEqual(by_email["carl@acme.io"]["permissions"], [])

    def test_admin_does_not_see_members_from_another_org(self):
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
        emails = [row["email"] for row in response.data["results"]]
        self.assertEqual(emails, ["other@other.io"])

    def test_csm_cannot_list_members(self):
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

    def test_me_returns_the_same_user_shape_as_login(self):
        """The frontend caches whichever of these answered last, so a
        field here that login has (or vice versa) would silently wipe it
        from the cached user — `permissions` especially, which every
        capability gate reads. This caught a real regression once."""

        me = self.client.get("/api/v1/auth/me/")
        login = self.client.post(
            "/api/v1/auth/login/",
            {"email": "alice@acme.io", "password": "supersecret1"},
            format="json",
        )

        self.assertEqual(set(me.data), set(login.data["user"]))
        self.assertEqual(me.data["role"], User.Role.ADMIN)
        self.assertEqual(me.data["role_name"], "Admin")
        self.assertIn(Capability.MANAGE_USERS, me.data["permissions"])

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
        self.assertEqual(self.user.role.slug, User.Role.ADMIN)


class OrganisationSettingsTests(APITestCase):
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
        self.other_org = Organisation.objects.create(name="Other Inc", currency="EUR")

    def test_defaults(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/auth/organisation/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["currency"], "USD")
        self.assertEqual(response.data["currency_display"], "US Dollar ($)")
        self.assertEqual(response.data["default_lifecycle_stage"], "")
        self.assertTrue(response.data["ai_agent_enabled"])
        self.assertEqual(response.data["ai_agent_tone"], "professional")
        self.assertEqual(response.data["ai_agent_tone_display"], "Professional")

    def test_admin_can_change_ai_agent_settings(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            "/api/v1/auth/organisation/",
            {"ai_agent_enabled": False, "ai_agent_tone": "friendly"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.org.refresh_from_db()
        self.assertFalse(self.org.ai_agent_enabled)
        self.assertEqual(self.org.ai_agent_tone, "friendly")

    def test_unauthenticated_cannot_view(self):
        response = self.client.get("/api/v1/auth/organisation/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_csm_can_view_but_not_change(self):
        self.client.force_authenticate(self.csm)
        get_response = self.client.get("/api/v1/auth/organisation/")
        self.assertEqual(get_response.status_code, status.HTTP_200_OK)

        patch_response = self.client.patch(
            "/api/v1/auth/organisation/", {"currency": "GBP"}, format="json"
        )
        self.assertEqual(patch_response.status_code, status.HTTP_403_FORBIDDEN)
        self.org.refresh_from_db()
        self.assertEqual(self.org.currency, "USD")

    def test_admin_can_change_currency_and_default_lifecycle_stage(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            "/api/v1/auth/organisation/",
            {"currency": "EUR", "default_lifecycle_stage": "adoption"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["currency"], "EUR")
        self.org.refresh_from_db()
        self.assertEqual(self.org.currency, "EUR")
        self.assertEqual(self.org.default_lifecycle_stage, "adoption")

    def test_changing_currency_clears_existing_fx_rates(self):
        from services.fx_rates.models import FxRate

        FxRate.objects.create(organisation=self.org, currency="EUR", rate_to_org_currency="1.08")
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            "/api/v1/auth/organisation/", {"currency": "GBP"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # A rate stored as "EUR -> USD" has no valid meaning once the org's
        # own currency is GBP -- silently reinterpreting it would be a real,
        # invisible bug, so it's cleared rather than carried over.
        self.assertEqual(self.org.fx_rates.count(), 0)

    def test_changing_an_unrelated_field_does_not_clear_fx_rates(self):
        from services.fx_rates.models import FxRate

        FxRate.objects.create(organisation=self.org, currency="EUR", rate_to_org_currency="1.08")
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            "/api/v1/auth/organisation/", {"ai_agent_tone": "friendly"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.org.fx_rates.count(), 1)

    def test_patching_currency_to_its_own_current_value_does_not_clear_fx_rates(self):
        from services.fx_rates.models import FxRate

        FxRate.objects.create(organisation=self.org, currency="EUR", rate_to_org_currency="1.08")
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            "/api/v1/auth/organisation/", {"currency": "USD"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.org.fx_rates.count(), 1)

    def test_a_settings_manager_can_rename_the_organisation_but_never_its_slug(self):
        # The name is what Settings > Data's global configuration card edits
        # (a deliberate change from the earlier read-only rule); the slug is
        # the tenant's identity and stays read-only.
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            "/api/v1/auth/organisation/", {"name": "Acme Corp", "slug": "hacked"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.org.refresh_from_db()
        self.assertEqual(self.org.name, "Acme Corp")
        self.assertNotEqual(self.org.slug, "hacked")

    def test_scoped_to_callers_own_organisation(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/auth/organisation/")
        self.assertEqual(response.data["id"], self.org.id)
        self.assertNotEqual(response.data["currency"], "EUR")


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


class OrgUserDetailTests(APITestCase):
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
        self.url = f"/api/v1/auth/users/{self.csm.id}/"

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
            f"/api/v1/auth/users/{other_csm.id}/", {"name": "Hacked"}, format="json"
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
            email="alice@acme.io",
            password="oldpassword1",
            name="Alice Admin",
            organisation=org,
            role=User.Role.ADMIN,
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
            email="alice@acme.io",
            password="oldpassword1",
            name="Alice Admin",
            organisation=org,
            role=User.Role.ADMIN,
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


class CapabilityListTests(APITestCase):
    url = "/api/v1/auth/capabilities/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="supersecret1",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
        )

    def test_unauthenticated_cannot_list(self):
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_any_member_sees_the_real_capability_vocabulary(self):
        """Served rather than hardcoded in the frontend so the role
        editor's checkboxes can't drift from what's enforced."""

        self.client.force_authenticate(self.csm)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(sorted(row["key"] for row in response.data), sorted(Capability.values))
        self.assertTrue(all(row["label"] for row in response.data))


class RoleTests(APITestCase):
    url = "/api/v1/auth/roles/"

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

    def _detail(self, role):
        return f"/api/v1/auth/roles/{role.id}/"

    def test_every_org_starts_with_its_two_system_roles(self):
        self.client.force_authenticate(self.csm)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        by_slug = {row["slug"]: row for row in response.data}
        self.assertEqual(sorted(by_slug), ["admin", "csm"])
        self.assertTrue(all(row["is_system"] for row in response.data))
        self.assertEqual(sorted(by_slug["admin"]["permissions"]), sorted(Capability.values))
        self.assertEqual(by_slug["csm"]["permissions"], [])

    def test_roles_are_scoped_to_the_callers_own_organisation(self):
        other_org = Organisation.objects.create(name="Other Org")
        other_org.ensure_system_roles()
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(len(response.data), 2)
        self.assertTrue(
            all(Role.objects.get(pk=r["id"]).organisation_id == self.org.id for r in response.data)
        )

    def test_a_csm_cannot_create_a_role(self):
        self.client.force_authenticate(self.csm)
        response = self.client.post(
            self.url, {"name": "Support Lead", "permissions": []}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_someone_with_manage_users_can_create_a_role_with_a_derived_slug(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self.url,
            {"name": "Support Lead", "permissions": [Capability.MANAGE_INTEGRATIONS]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["slug"], "support-lead")
        self.assertFalse(response.data["is_system"])
        self.assertEqual(response.data["permissions"], [Capability.MANAGE_INTEGRATIONS])
        self.assertEqual(response.data["users_count"], 0)

    def test_colliding_role_names_get_a_real_unique_slug(self):
        self.client.force_authenticate(self.admin)
        self.client.post(self.url, {"name": "Support Lead", "permissions": []}, format="json")
        second = self.client.post(
            self.url, {"name": "Support Lead", "permissions": []}, format="json"
        )
        self.assertEqual(second.data["slug"], "support-lead-2")

    def test_unknown_capabilities_are_rejected(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self.url, {"name": "Bogus", "permissions": ["make_coffee"]}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_you_cannot_grant_a_capability_you_do_not_hold_yourself(self):
        """Otherwise `manage_users` would silently be full admin: mint a
        role holding everything, assign it to yourself, done."""

        delegated = Role.objects.create(
            organisation=self.org,
            name="User Manager",
            slug="user-manager",
            permissions=[Capability.MANAGE_USERS],
        )
        self.csm.role = delegated
        self.csm.save(update_fields=["role"])
        self.client.force_authenticate(self.csm)

        response = self.client.post(
            self.url,
            {"name": "Sneaky", "permissions": [Capability.MANAGE_FX_RATES]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Role.objects.filter(name="Sneaky").exists())

    def test_system_roles_cannot_be_edited_or_deleted(self):
        admin_role = self.org.roles.get(slug=User.Role.ADMIN)
        self.client.force_authenticate(self.admin)

        patched = self.client.patch(self._detail(admin_role), {"permissions": []}, format="json")
        deleted = self.client.delete(self._detail(admin_role))

        self.assertEqual(patched.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(deleted.status_code, status.HTTP_400_BAD_REQUEST)
        admin_role.refresh_from_db()
        self.assertEqual(sorted(admin_role.permissions), sorted(Capability.values))

    def test_a_custom_role_can_be_edited_and_deleted(self):
        role = Role.objects.create(
            organisation=self.org, name="Support Lead", slug="support-lead", permissions=[]
        )
        self.client.force_authenticate(self.admin)

        patched = self.client.patch(
            self._detail(role),
            {"permissions": [Capability.MANAGE_INTEGRATIONS]},
            format="json",
        )
        self.assertEqual(patched.status_code, status.HTTP_200_OK)
        self.assertEqual(patched.data["permissions"], [Capability.MANAGE_INTEGRATIONS])

        self.assertEqual(
            self.client.delete(self._detail(role)).status_code, status.HTTP_204_NO_CONTENT
        )

    def test_a_role_still_assigned_to_someone_cannot_be_deleted(self):
        role = Role.objects.create(
            organisation=self.org, name="Support Lead", slug="support-lead", permissions=[]
        )
        self.csm.role = role
        self.csm.save(update_fields=["role"])
        self.client.force_authenticate(self.admin)

        response = self.client.delete(self._detail(role))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(Role.objects.filter(pk=role.pk).exists())

    def test_404_for_a_role_in_another_organisation(self):
        other_org = Organisation.objects.create(name="Other Org")
        other_role = other_org.ensure_system_roles()[User.Role.ADMIN]
        self.client.force_authenticate(self.admin)

        self.assertEqual(
            self.client.get(self._detail(other_role)).status_code, status.HTTP_404_NOT_FOUND
        )


class CapabilityEnforcementTests(APITestCase):
    """One test per capability, proving a role holding *only* that
    capability reaches exactly its own endpoints and nothing else.

    This is what makes the roles real rather than decorative — without
    it, a checkbox in the UI could easily grant nothing at all."""

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.org.ensure_system_roles()

    def _user_with(self, *capabilities, email="scoped@acme.io"):
        role = Role.objects.create(
            organisation=self.org,
            name="Scoped",
            slug=f"scoped-{email}",
            permissions=list(capabilities),
        )
        user = User.objects.create_user(
            email=email,
            password="supersecret1",
            name="Scoped",
            organisation=self.org,
            role=role,
        )
        self.client.force_authenticate(user)
        return user

    def test_manage_users_reaches_only_user_and_role_management(self):
        self._user_with(Capability.MANAGE_USERS)

        self.assertEqual(self.client.get("/api/v1/auth/users/").status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.client.post(
                "/api/v1/auth/roles/", {"name": "X", "permissions": []}, format="json"
            ).status_code,
            status.HTTP_201_CREATED,
        )
        self.assertEqual(
            self.client.get("/api/v1/webhooks/").status_code, status.HTTP_403_FORBIDDEN
        )
        self.assertEqual(
            self.client.get("/api/v1/fx-rates/").status_code, status.HTTP_403_FORBIDDEN
        )

    def test_manage_integrations_reaches_only_webhooks(self):
        self._user_with(Capability.MANAGE_INTEGRATIONS)

        self.assertEqual(self.client.get("/api/v1/webhooks/").status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.client.get("/api/v1/auth/users/").status_code, status.HTTP_403_FORBIDDEN
        )
        self.assertEqual(
            self.client.get("/api/v1/fx-rates/").status_code, status.HTTP_403_FORBIDDEN
        )

    def test_manage_fx_rates_reaches_only_fx_rates(self):
        self._user_with(Capability.MANAGE_FX_RATES)

        self.assertEqual(self.client.get("/api/v1/fx-rates/").status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.client.get("/api/v1/webhooks/").status_code, status.HTTP_403_FORBIDDEN
        )

    def test_manage_org_settings_reaches_only_the_org_settings_patch(self):
        self._user_with(Capability.MANAGE_ORG_SETTINGS)

        self.assertEqual(
            self.client.patch(
                "/api/v1/auth/organisation/", {"currency": "EUR"}, format="json"
            ).status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            self.client.get("/api/v1/webhooks/").status_code, status.HTTP_403_FORBIDDEN
        )

    def test_manage_custom_objects_reaches_only_custom_object_config(self):
        self._user_with(Capability.MANAGE_CUSTOM_OBJECTS)

        created = self.client.post(
            "/api/v1/custom-objects/definitions/",
            {"name": "Line Item", "applies_to_customer": True},
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            self.client.get("/api/v1/auth/users/").status_code, status.HTTP_403_FORBIDDEN
        )

    def test_a_role_with_no_capabilities_reaches_none_of_them(self):
        self._user_with()

        for path in ("/api/v1/auth/users/", "/api/v1/webhooks/", "/api/v1/fx-rates/"):
            self.assertEqual(self.client.get(path).status_code, status.HTTP_403_FORBIDDEN, path)
        self.assertEqual(
            self.client.patch(
                "/api/v1/auth/organisation/", {"currency": "EUR"}, format="json"
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        # …but everyday work is still open to them, exactly as before.
        self.assertEqual(self.client.get("/api/v1/customers/").status_code, status.HTTP_200_OK)


class LastUserManagerGuardrailTests(APITestCase):
    """An organisation must always keep at least one active person who
    can manage users — otherwise nobody could ever add a member, mint a
    role, or restore anyone again."""

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
        self.client.force_authenticate(self.admin)

    def _url(self, user):
        return f"/api/v1/auth/users/{user.id}/"

    def test_the_only_user_manager_cannot_be_demoted(self):
        csm_role = self.org.roles.get(slug=User.Role.CSM)
        response = self.client.patch(self._url(self.admin), {"role_id": csm_role.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.has_capability(Capability.MANAGE_USERS))

    def test_the_only_user_manager_cannot_be_deactivated(self):
        response = self.client.patch(self._url(self.admin), {"is_active": False}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_demotion_is_allowed_once_somebody_else_can_manage_users(self):
        self.csm.role = self.org.roles.get(slug=User.Role.ADMIN)
        self.csm.save(update_fields=["role"])
        csm_role = self.org.roles.get(slug=User.Role.CSM)

        response = self.client.patch(self._url(self.admin), {"role_id": csm_role.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.has_capability(Capability.MANAGE_USERS))

    def test_an_admin_can_change_someone_elses_role(self):
        support_lead = Role.objects.create(
            organisation=self.org,
            name="Support Lead",
            slug="support-lead",
            permissions=[Capability.MANAGE_INTEGRATIONS],
        )

        response = self.client.patch(
            self._url(self.csm), {"role_id": support_lead.id}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["role"], "support-lead")
        self.csm.refresh_from_db()
        self.assertTrue(self.csm.has_capability(Capability.MANAGE_INTEGRATIONS))
        self.assertFalse(self.csm.has_capability(Capability.MANAGE_USERS))

    def test_cannot_move_someone_onto_another_organisations_role(self):
        other_org = Organisation.objects.create(name="Other Org")
        other_role = other_org.ensure_system_roles()[User.Role.CSM]

        response = self.client.patch(self._url(self.csm), {"role_id": other_role.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
