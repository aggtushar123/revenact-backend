"""Settings > Data's global configuration: name and attribute mapping."""

from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User


class GlobalAttributesTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="x",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="x",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
        )

    def test_defaults_are_the_dashboards_own_fields_and_choices_travel_with_them(self):
        self.client.force_authenticate(self.csm)
        data = self.client.get("/api/v1/auth/organisation/").data
        self.assertEqual(data["global_attributes"]["arr"], "arr_billed_at_account")
        self.assertEqual(data["global_attributes"]["renewal_date"], "renewal_date")
        self.assertIn("total_contract_value", data["global_attribute_choices"]["arr"])

    def test_a_settings_manager_renames_and_remaps_a_csm_cannot(self):
        self.client.force_authenticate(self.csm)
        self.assertEqual(
            self.client.patch(
                "/api/v1/auth/organisation/", {"name": "Nope"}, format="json"
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            "/api/v1/auth/organisation/",
            {"name": "Acme Corp", "global_attributes": {"arr": "total_contract_value"}},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "Acme Corp")
        self.assertEqual(response.data["global_attributes"]["arr"], "total_contract_value")
        # A partial mapping keeps the rest.
        self.assertEqual(response.data["global_attributes"]["renewal_date"], "renewal_date")
        self.assertEqual(
            Organisation.objects.get(pk=self.org.pk).global_attributes,
            {"arr": "total_contract_value"},
        )

    def test_unknown_keys_and_impossible_choices_are_refused(self):
        self.client.force_authenticate(self.admin)
        for body in (
            {"global_attributes": {"nps": "x"}},
            {"global_attributes": {"arr": "joined_date"}},
            {"name": "  "},
        ):
            self.assertEqual(
                self.client.patch("/api/v1/auth/organisation/", body, format="json").status_code,
                status.HTTP_400_BAD_REQUEST,
                body,
            )
