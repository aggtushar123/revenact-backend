"""Integration tier: through the real URLconf + real test DB."""

from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers.models import Account, Customer

from ..models import CustomFieldDefinition, CustomObjectDefinition, CustomObjectRecord


def _admin(org, email="admin@acme.io"):
    return User.objects.create_user(
        email=email, password="supersecret1", name="Admin", organisation=org, role=User.Role.ADMIN
    )


def _csm(org, email="csm@acme.io"):
    return User.objects.create_user(
        email=email, password="supersecret1", name="CSM", organisation=org, role=User.Role.CSM
    )


class CustomObjectDefinitionListCreateViewTests(APITestCase):
    url = "/api/v1/custom-objects/definitions/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.admin = _admin(self.org)
        self.csm = _csm(self.org)

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_any_org_member_can_list(self):
        CustomObjectDefinition.objects.create(
            organisation=self.org, name="Line Item", api_name="line_item"
        )
        self.client.force_authenticate(self.csm)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["name"], "Line Item")
        self.assertEqual(response.data[0]["fields"], [])
        self.assertEqual(response.data[0]["records_count"], 0)

    def test_only_returns_the_callers_own_organisation(self):
        other_org = Organisation.objects.create(name="Globex")
        CustomObjectDefinition.objects.create(
            organisation=other_org, name="Not Mine", api_name="not_mine"
        )
        self.client.force_authenticate(self.csm)

        response = self.client.get(self.url)

        self.assertEqual(response.data, [])

    def test_a_csm_cannot_create_a_definition(self):
        self.client.force_authenticate(self.csm)
        response = self.client.post(self.url, {"name": "Line Item", "applies_to_account": True})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_admin_can_create_a_definition_with_a_real_derived_api_name(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            self.url, {"name": "Opportunity Line Item", "applies_to_account": True}
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["api_name"], "opportunity_line_item")
        definition = CustomObjectDefinition.objects.get(pk=response.data["id"])
        self.assertEqual(definition.organisation, self.org)
        self.assertEqual(definition.created_by, self.admin)

    def test_colliding_names_get_a_real_unique_api_name(self):
        self.client.force_authenticate(self.admin)
        self.client.post(self.url, {"name": "Line Item", "applies_to_account": True})

        response = self.client.post(self.url, {"name": "Line Item", "applies_to_customer": True})

        self.assertEqual(response.data["api_name"], "line_item_2")

    def test_rejects_a_definition_that_applies_to_neither_parent_type(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self.url,
            {"name": "Nothing", "applies_to_customer": False, "applies_to_account": False},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class CustomObjectDefinitionDetailViewTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.admin = _admin(self.org)
        self.csm = _csm(self.org)
        self.definition = CustomObjectDefinition.objects.create(
            organisation=self.org, name="Line Item", api_name="line_item", applies_to_account=True
        )

    def _url(self, definition=None):
        return f"/api/v1/custom-objects/definitions/{(definition or self.definition).id}/"

    def test_any_org_member_can_read(self):
        self.client.force_authenticate(self.csm)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_a_csm_cannot_update_or_delete(self):
        self.client.force_authenticate(self.csm)
        self.assertEqual(
            self.client.patch(self._url(), {"name": "Renamed"}).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(self.client.delete(self._url()).status_code, status.HTTP_403_FORBIDDEN)

    def test_an_admin_can_update_and_delete(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(self._url(), {"name": "Renamed"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "Renamed")

        response = self.client.delete(self._url())
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(CustomObjectDefinition.objects.filter(pk=self.definition.id).exists())

    def test_404s_for_a_definition_in_another_organisation(self):
        other_org = Organisation.objects.create(name="Globex")
        other_admin = _admin(other_org, email="other@globex.io")
        self.client.force_authenticate(other_admin)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class CustomFieldDefinitionViewTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.admin = _admin(self.org)
        self.csm = _csm(self.org)
        self.definition = CustomObjectDefinition.objects.create(
            organisation=self.org, name="Line Item", api_name="line_item", applies_to_account=True
        )

    def _list_url(self):
        return f"/api/v1/custom-objects/definitions/{self.definition.id}/fields/"

    def _detail_url(self, field):
        return f"/api/v1/custom-objects/definitions/{self.definition.id}/fields/{field.id}/"

    def test_a_csm_cannot_add_a_field(self):
        self.client.force_authenticate(self.csm)
        response = self.client.post(self._list_url(), {"name": "Product", "field_type": "text"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_admin_can_add_a_field_with_auto_order(self):
        self.client.force_authenticate(self.admin)
        first = self.client.post(self._list_url(), {"name": "Product", "field_type": "text"})
        second = self.client.post(self._list_url(), {"name": "Quantity", "field_type": "number"})

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(first.data["api_name"], "product")
        self.assertEqual(first.data["order"], 1)
        self.assertEqual(second.data["order"], 2)

    def test_a_picklist_field_requires_real_options(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self._list_url(), {"name": "Tier", "field_type": "picklist", "picklist_options": []}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_picklist_field_with_real_options_succeeds(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            self._list_url(),
            {"name": "Tier", "field_type": "picklist", "picklist_options": ["Gold", "Silver"]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["picklist_options"], ["Gold", "Silver"])

    def test_an_admin_can_delete_a_field(self):
        field = CustomFieldDefinition.objects.create(
            object_definition=self.definition,
            name="Product",
            api_name="product",
            field_type=CustomFieldDefinition.FieldType.TEXT,
        )
        self.client.force_authenticate(self.admin)
        response = self.client.delete(self._detail_url(field))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)


class CustomObjectRecordListCreateViewTests(APITestCase):
    url = "/api/v1/custom-objects/records/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.admin = _admin(self.org)
        self.csm = _csm(self.org)
        self.customer = Customer.objects.create(organisation=self.org, name="Globex Corp")
        self.account = Account.objects.create(name="North America")
        self.account.customers.add(self.customer)

        self.customer_object = CustomObjectDefinition.objects.create(
            organisation=self.org,
            name="Renewal Note",
            api_name="renewal_note",
            applies_to_customer=True,
        )
        self.account_object = CustomObjectDefinition.objects.create(
            organisation=self.org,
            name="Line Item",
            api_name="line_item",
            applies_to_customer=False,
            applies_to_account=True,
        )
        self.product_field = CustomFieldDefinition.objects.create(
            object_definition=self.account_object,
            name="Product",
            api_name="product",
            field_type=CustomFieldDefinition.FieldType.TEXT,
            is_required=True,
        )
        self.qty_field = CustomFieldDefinition.objects.create(
            object_definition=self.account_object,
            name="Quantity",
            api_name="qty",
            field_type=CustomFieldDefinition.FieldType.NUMBER,
        )
        self.tier_field = CustomFieldDefinition.objects.create(
            object_definition=self.customer_object,
            name="Tier",
            api_name="tier",
            field_type=CustomFieldDefinition.FieldType.PICKLIST,
            picklist_options=["Gold", "Silver"],
        )

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_requires_a_definition_query_param(self):
        self.client.force_authenticate(self.csm)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_definition_alone_lists_every_record_across_the_org_paginated_with_parent_info(self):
        CustomObjectRecord.objects.create(
            object_definition=self.account_object, account=self.account, data={"product": "A"}
        )
        other_account = Account.objects.create(name="EMEA")
        other_account.customers.add(self.customer)
        CustomObjectRecord.objects.create(
            object_definition=self.account_object, account=other_account, data={"product": "B"}
        )
        self.client.force_authenticate(self.csm)

        response = self.client.get(self.url, {"definition": self.account_object.id})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)
        names = {r["parent_name"] for r in response.data["results"]}
        self.assertEqual(names, {"North America", "EMEA"})
        self.assertTrue(all(r["parent_type"] == "account" for r in response.data["results"]))

    def test_definition_alone_never_leaks_another_organisations_records(self):
        CustomObjectRecord.objects.create(
            object_definition=self.account_object, account=self.account, data={"product": "Mine"}
        )
        other_org = Organisation.objects.create(name="Globex")
        other_customer = Customer.objects.create(organisation=other_org, name="Other Co")
        other_account = Account.objects.create(name="Other Account")
        other_account.customers.add(other_customer)
        other_definition = CustomObjectDefinition.objects.create(
            organisation=other_org, name="Line Item", api_name="line_item", applies_to_account=True
        )
        CustomObjectRecord.objects.create(
            object_definition=other_definition, account=other_account, data={}
        )
        self.client.force_authenticate(self.csm)

        response = self.client.get(self.url, {"definition": self.account_object.id})

        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["data"], {"product": "Mine"})

    def test_a_csm_can_create_a_record_not_just_an_admin(self):
        self.client.force_authenticate(self.csm)

        response = self.client.post(
            self.url,
            {
                "object_definition_id": self.account_object.id,
                "account_id": self.account.id,
                "data": {"product": "Seat License", "qty": 50},
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["data"], {"product": "Seat License", "qty": 50.0})
        record = CustomObjectRecord.objects.get(pk=response.data["id"])
        self.assertEqual(record.created_by, self.csm)

    def test_lists_only_records_for_the_given_definition_and_parent(self):
        CustomObjectRecord.objects.create(
            object_definition=self.account_object, account=self.account, data={"product": "A"}
        )
        other_account = Account.objects.create(name="EMEA")
        other_account.customers.add(self.customer)
        CustomObjectRecord.objects.create(
            object_definition=self.account_object, account=other_account, data={"product": "B"}
        )
        self.client.force_authenticate(self.csm)

        response = self.client.get(
            self.url, {"definition": self.account_object.id, "account": self.account.id}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["data"]["product"], "A")

    def test_rejects_a_missing_required_field(self):
        self.client.force_authenticate(self.csm)
        response = self.client.post(
            self.url,
            {
                "object_definition_id": self.account_object.id,
                "account_id": self.account.id,
                "data": {},
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_a_non_numeric_value_for_a_number_field(self):
        self.client.force_authenticate(self.csm)
        response = self.client.post(
            self.url,
            {
                "object_definition_id": self.account_object.id,
                "account_id": self.account.id,
                "data": {"product": "Seat License", "qty": "many"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_an_unknown_field_key(self):
        self.client.force_authenticate(self.csm)
        response = self.client.post(
            self.url,
            {
                "object_definition_id": self.account_object.id,
                "account_id": self.account.id,
                "data": {"product": "Seat License", "made_up_field": "x"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_a_picklist_value_outside_the_real_options(self):
        self.client.force_authenticate(self.csm)
        response = self.client.post(
            self.url,
            {
                "object_definition_id": self.customer_object.id,
                "customer_id": self.customer.id,
                "data": {"tier": "Platinum"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_a_customer_record_for_an_account_only_object(self):
        self.client.force_authenticate(self.csm)
        response = self.client.post(
            self.url,
            {
                "object_definition_id": self.account_object.id,
                "customer_id": self.customer.id,
                "data": {"product": "x"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_both_customer_and_account_set(self):
        self.client.force_authenticate(self.csm)
        response = self.client.post(
            self.url,
            {
                "object_definition_id": self.account_object.id,
                "account_id": self.account.id,
                "customer_id": self.customer.id,
                "data": {"product": "x"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_a_customer_from_another_organisation(self):
        other_org = Organisation.objects.create(name="Globex")
        other_customer = Customer.objects.create(organisation=other_org, name="Other Co")
        self.client.force_authenticate(self.csm)

        response = self.client.post(
            self.url,
            {
                "object_definition_id": self.customer_object.id,
                "customer_id": other_customer.id,
                "data": {"tier": "Gold"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class CustomObjectRecordDetailViewTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.csm = _csm(self.org)
        self.customer = Customer.objects.create(organisation=self.org, name="Globex Corp")
        self.definition = CustomObjectDefinition.objects.create(
            organisation=self.org,
            name="Renewal Note",
            api_name="renewal_note",
            applies_to_customer=True,
        )
        CustomFieldDefinition.objects.create(
            object_definition=self.definition,
            name="Note",
            api_name="note",
            field_type=CustomFieldDefinition.FieldType.TEXT,
        )
        self.record = CustomObjectRecord.objects.create(
            object_definition=self.definition,
            customer=self.customer,
            data={"note": "Renewing early"},
        )

    def _url(self):
        return f"/api/v1/custom-objects/records/{self.record.id}/"

    def test_can_update_real_data(self):
        self.client.force_authenticate(self.csm)
        response = self.client.patch(self._url(), {"data": {"note": "Updated"}}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.record.refresh_from_db()
        self.assertEqual(self.record.data, {"note": "Updated"})

    def test_can_delete(self):
        self.client.force_authenticate(self.csm)
        response = self.client.delete(self._url())
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(CustomObjectRecord.objects.filter(pk=self.record.id).exists())

    def test_404s_for_a_record_in_another_organisation(self):
        other_org = Organisation.objects.create(name="Globex")
        other_csm = _csm(other_org, email="other@globex.io")
        self.client.force_authenticate(other_csm)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class CustomObjectRecordOwnershipScopingTests(APITestCase):
    """Records follow their parent Customer/Account's visibility, same
    as Notes and Tasks do — see services/customers/scoping.py. Defining
    object *types* stays capability-gated separately; this is about
    which rows of an existing type you can read and edit."""

    url = "/api/v1/custom-objects/records/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.csm = _csm(self.org, email="carl@acme.io")
        self.other = _csm(self.org, email="dana@acme.io")
        self.admin = _admin(self.org, email="alice@acme.io")

        self.definition = CustomObjectDefinition.objects.create(
            organisation=self.org, name="Opportunity Line", api_name="opportunity_line"
        )
        # A real field, so a PATCH through the serializer has something
        # valid to write — records made straight through the ORM below
        # skip that validation, PATCH doesn't.
        CustomFieldDefinition.objects.create(
            object_definition=self.definition,
            name="Amount",
            api_name="amount",
            field_type=CustomFieldDefinition.FieldType.NUMBER,
        )
        self.mine = Customer.objects.create(organisation=self.org, name="Mine", owner=self.csm)
        self.theirs = Customer.objects.create(
            organisation=self.org, name="Theirs", owner=self.other
        )
        self.my_record = CustomObjectRecord.objects.create(
            object_definition=self.definition, customer=self.mine, data={"amount": 100}
        )
        self.their_record = CustomObjectRecord.objects.create(
            object_definition=self.definition, customer=self.theirs, data={"amount": 900}
        )
        self.client.force_authenticate(self.csm)

    def test_the_org_wide_list_shows_only_records_you_can_see(self):
        response = self.client.get(f"{self.url}?definition={self.definition.id}")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([r["id"] for r in response.data["results"]], [self.my_record.id])

    def test_a_capability_holder_sees_every_record(self):
        self.client.force_authenticate(self.admin)

        response = self.client.get(f"{self.url}?definition={self.definition.id}")

        self.assertEqual(response.data["count"], 2)

    def test_a_guessed_customer_id_returns_nothing(self):
        """The branch that made this worth changing: `?customer=` used
        to be trusted after only an organisation check, so naming
        someone else's id read their records straight out."""
        response = self.client.get(
            f"{self.url}?definition={self.definition.id}&customer={self.theirs.id}"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_your_own_parent_filter_still_works(self):
        response = self.client.get(
            f"{self.url}?definition={self.definition.id}&customer={self.mine.id}"
        )

        self.assertEqual([r["id"] for r in response.data], [self.my_record.id])

    def test_cannot_read_someone_elses_record_by_id(self):
        response = self.client.get(f"{self.url}{self.their_record.id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_cannot_edit_someone_elses_record(self):
        response = self.client.patch(
            f"{self.url}{self.their_record.id}/", {"data": {"amount": 1}}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.their_record.refresh_from_db()
        self.assertEqual(self.their_record.data, {"amount": 900})

    def test_cannot_delete_someone_elses_record(self):
        response = self.client.delete(f"{self.url}{self.their_record.id}/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(CustomObjectRecord.objects.filter(pk=self.their_record.pk).exists())

    def test_your_own_record_is_still_fully_editable(self):
        response = self.client.patch(
            f"{self.url}{self.my_record.id}/", {"data": {"amount": 250}}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.my_record.refresh_from_db()
        self.assertEqual(self.my_record.data, {"amount": 250})

    def test_an_unowned_parents_records_stay_visible(self):
        unowned = Customer.objects.create(organisation=self.org, name="Unassigned")
        record = CustomObjectRecord.objects.create(
            object_definition=self.definition, customer=unowned, data={"amount": 5}
        )

        response = self.client.get(f"{self.url}?definition={self.definition.id}")

        self.assertIn(record.id, [r["id"] for r in response.data["results"]])

    def test_records_on_an_account_you_own_are_visible_under_someone_elses_customer(self):
        account = Account.objects.create(name="Mine EMEA", owner=self.csm)
        account.customers.add(self.theirs)
        record = CustomObjectRecord.objects.create(
            object_definition=self.definition, account=account, data={"amount": 7}
        )

        response = self.client.get(f"{self.url}?definition={self.definition.id}")

        self.assertIn(record.id, [r["id"] for r in response.data["results"]])
