"""Integration tier: through the real URLconf + real test DB."""

from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.connectors.models import Connector
from services.customers.models import Account, Customer


def create_account(customer, **kwargs):
    account = Account.objects.create(**kwargs)
    account.customers.add(customer)
    return account


class ConnectorListCreateTests(APITestCase):
    url = "/api/v1/connectors/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.other_org = Organisation.objects.create(name="Other Inc")
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
        self.apple = Customer.objects.create(organisation=self.org, name="Apple Inc")

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_a_csm_can_read_connectors(self):
        """Unlike webhooks, reads are open: the Ticket Overview
        dashboard labels its origin chart with these names, so a CSM
        has to be able to fetch them."""
        Connector.objects.create(
            organisation=self.org, provider=Connector.Provider.ZENDESK, name="Zendesk"
        )
        self.client.force_authenticate(self.csm)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([c["name"] for c in response.data], ["Zendesk"])

    def test_a_csm_cannot_create_a_connector(self):
        self.client.force_authenticate(self.csm)

        response = self.client.post(
            self.url, {"provider": "zendesk", "name": "Zendesk"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Connector.objects.exists())

    def test_an_admin_can_create_and_lists_only_its_own_organisation(self):
        Connector.objects.create(
            organisation=self.other_org, provider=Connector.Provider.JIRA, name="Theirs"
        )
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url, {"provider": "zendesk", "name": "Zendesk"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        response = self.client.get(self.url)
        self.assertEqual([c["name"] for c in response.data], ["Zendesk"])

    def test_a_connector_created_with_no_links_is_organisation_wide(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url, {"provider": "zendesk", "name": "Zendesk"}, format="json"
        )

        self.assertTrue(response.data["is_organisation_wide"])

    def test_a_connector_can_be_scoped_to_specific_customers(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url,
            {"provider": "zendesk", "name": "Zendesk", "customer_ids": [self.apple.id]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertFalse(response.data["is_organisation_wide"])
        self.assertEqual(response.data["customers"], [{"id": self.apple.id, "name": "Apple Inc"}])

    def test_cannot_scope_a_connector_to_another_organisations_customer(self):
        outsider = Customer.objects.create(organisation=self.other_org, name="Initech")
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url,
            {"provider": "zendesk", "name": "Zendesk", "customer_ids": [outsider.id]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_scope_a_connector_to_another_organisations_account(self):
        outsider = Customer.objects.create(organisation=self.other_org, name="Initech")
        their_account = create_account(outsider, name="Initech EMEA")
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url,
            {"provider": "zendesk", "name": "Zendesk", "account_ids": [their_account.id]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_the_same_provider_twice_needs_different_names(self):
        self.client.force_authenticate(self.admin)
        payload = {"provider": "zendesk", "name": "Zendesk"}
        self.client.post(self.url, payload, format="json")

        response = self.client.post(self.url, payload, format="json")

        # A clean 400, not a raw IntegrityError 500 — DRF can't build
        # the uniqueness validator itself because `organisation` isn't
        # a serializer field.
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already have a connector", str(response.data["name"]))

    def test_two_instances_of_one_provider_are_allowed(self):
        self.client.force_authenticate(self.admin)
        self.client.post(self.url, {"provider": "zendesk", "name": "Zendesk (EU)"}, format="json")

        response = self.client.post(
            self.url, {"provider": "zendesk", "name": "Zendesk (US)"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class ConnectorDetailTests(APITestCase):
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
        self.connector = Connector.objects.create(
            organisation=self.org, provider=Connector.Provider.ZENDESK, name="Zendesk"
        )
        self.url = f"/api/v1/connectors/{self.connector.id}/"

    def test_a_csm_can_read_but_not_edit(self):
        self.client.force_authenticate(self.csm)

        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_200_OK)
        response = self.client.patch(self.url, {"is_enabled": False}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_admin_can_disable_it(self):
        self.client.force_authenticate(self.admin)

        response = self.client.patch(self.url, {"is_enabled": False}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.connector.refresh_from_db()
        self.assertFalse(self.connector.is_enabled)

    def test_an_admin_can_rescope_it(self):
        apple = Customer.objects.create(organisation=self.org, name="Apple Inc")
        self.client.force_authenticate(self.admin)

        response = self.client.patch(self.url, {"customer_ids": [apple.id]}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["is_organisation_wide"])

    def test_renaming_to_an_existing_name_is_rejected_but_keeping_its_own_is_fine(self):
        Connector.objects.create(
            organisation=self.org, provider=Connector.Provider.ZENDESK, name="Zendesk (US)"
        )
        self.client.force_authenticate(self.admin)

        clash = self.client.patch(self.url, {"name": "Zendesk (US)"}, format="json")
        self.assertEqual(clash.status_code, status.HTTP_400_BAD_REQUEST)

        # Editing something else without touching the name must not
        # trip the uniqueness check against the connector's own row.
        same = self.client.patch(self.url, {"is_enabled": False}, format="json")
        self.assertEqual(same.status_code, status.HTTP_200_OK)

    def test_another_organisation_cannot_reach_it(self):
        other_org = Organisation.objects.create(name="Other Inc")
        outsider = User.objects.create_user(
            email="other@other.io",
            password="supersecret1",
            name="Other",
            organisation=other_org,
            role=User.Role.ADMIN,
        )
        self.client.force_authenticate(outsider)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
