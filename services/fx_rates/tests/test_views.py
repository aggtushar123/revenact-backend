"""Integration tier: through the real URLconf + real test DB, same
convention as services/webhooks/tests/test_views.py."""

from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.fx_rates.models import FxRate


class FxRateListCreateTests(APITestCase):
    url = "/api/v1/fx-rates/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")
        self.other_org = Organisation.objects.create(name="Other Inc", currency="USD")
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

    def test_csm_cannot_list_or_create(self):
        self.client.force_authenticate(self.csm)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_403_FORBIDDEN)
        response = self.client.post(
            self.url, {"currency": "EUR", "rate_to_org_currency": "1.08"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_create_and_list_only_own_organisation(self):
        FxRate.objects.create(
            organisation=self.other_org, currency="EUR", rate_to_org_currency="1.08"
        )
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self.url, {"currency": "EUR", "rate_to_org_currency": "1.080000"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["currency_display"], "Euro (€)")

        list_response = self.client.get(self.url)
        self.assertEqual(len(list_response.data), 1)
        self.assertEqual(list_response.data[0]["currency"], "EUR")

    def test_cannot_create_a_rate_for_the_orgs_own_currency(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self.url, {"currency": "USD", "rate_to_org_currency": "1.0"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(FxRate.objects.count(), 0)

    def test_cannot_create_a_duplicate_rate_for_the_same_currency(self):
        FxRate.objects.create(organisation=self.org, currency="EUR", rate_to_org_currency="1.08")
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self.url, {"currency": "EUR", "rate_to_org_currency": "1.10"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(FxRate.objects.count(), 1)


class FxRateDetailTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")
        self.other_org = Organisation.objects.create(name="Other Inc", currency="USD")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.rate = FxRate.objects.create(
            organisation=self.org, currency="EUR", rate_to_org_currency="1.08"
        )
        self.foreign_rate = FxRate.objects.create(
            organisation=self.other_org, currency="EUR", rate_to_org_currency="1.08"
        )

    def test_admin_can_update_the_rate(self):
        self.client.force_authenticate(self.admin)
        url = f"/api/v1/fx-rates/{self.rate.id}/"
        response = self.client.patch(url, {"rate_to_org_currency": "1.12"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.rate.refresh_from_db()
        self.assertEqual(str(self.rate.rate_to_org_currency), "1.120000")

    def test_404_for_a_rate_outside_own_organisation(self):
        self.client.force_authenticate(self.admin)
        url = f"/api/v1/fx-rates/{self.foreign_rate.id}/"
        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)

    def test_admin_can_delete(self):
        self.client.force_authenticate(self.admin)
        url = f"/api/v1/fx-rates/{self.rate.id}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(FxRate.objects.filter(pk=self.rate.id).exists())
