"""Integration tier: through the real URLconf + real test DB, one endpoint
at a time (rest_framework.test.APITestCase)."""

from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import Organisation, User


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


class CreateCSMTests(APITestCase):
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
