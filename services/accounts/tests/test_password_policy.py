"""Password policy applies on every path that sets one (SOC2:AUTH-04)."""

from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User

STRONG = "correct-horse-battery"


class PasswordPolicyTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password=STRONG,
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )

    def _signup(self, password):
        return self.client.post(
            "/api/v1/auth/signup/",
            {
                "organisation_name": "Globex",
                "name": "Hank Scorpio",
                "email": "hank@globex.io",
                "password": password,
            },
            format="json",
        )

    def test_signup_rejects_short_common_and_self_similar_passwords(self):
        for weak, fragment in [
            ("short1", "at least 12"),
            ("password1234", "too common"),
            ("hank@globex.io", "too similar"),
        ]:
            with self.subTest(weak=weak):
                response = self._signup(weak)
                self.assertEqual(response.status_code, 400)
                self.assertIn(fragment, str(response.data))
        self.assertFalse(User.objects.filter(email="hank@globex.io").exists())

    def test_signup_accepts_a_long_passphrase(self):
        self.assertEqual(self._signup("three random words here").status_code, 201)

    def test_admin_set_password_is_validated(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            "/api/v1/auth/users/",
            {"name": "Carol", "email": "carol@acme.io", "password": "short1"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.data)

        csm = User.objects.create_user(
            email="dave@acme.io", password=STRONG, name="Dave", organisation=self.org
        )
        response = self.client.patch(
            f"/api/v1/auth/users/{csm.pk}/", {"password": "dave@acme.io"}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("too similar", str(response.data))

    def test_self_service_change_is_validated(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            "/api/v1/auth/me/change-password/",
            {"current_password": STRONG, "new_password": "alice"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("new_password", response.data)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password(STRONG))

    def test_new_hashes_use_argon2(self):
        user = User.objects.create_user(
            email="new@acme.io", password=STRONG, name="New", organisation=self.org
        )
        self.assertTrue(user.password.startswith("argon2"))
