from django.db import IntegrityError, transaction
from django.test import TestCase

from services.accounts.models import Organisation
from services.customers.models import Customer

from ..models import CustomFieldDefinition, CustomObjectDefinition, CustomObjectRecord


class CustomObjectDefinitionModelTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")

    def test_requires_at_least_one_parent_type(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            CustomObjectDefinition.objects.create(
                organisation=self.org,
                name="Nothing",
                api_name="nothing",
                applies_to_customer=False,
                applies_to_account=False,
            )

    def test_api_name_unique_per_organisation(self):
        CustomObjectDefinition.objects.create(
            organisation=self.org, name="Line Item", api_name="line_item"
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            CustomObjectDefinition.objects.create(
                organisation=self.org, name="Line Item Again", api_name="line_item"
            )

    def test_same_api_name_allowed_across_different_organisations(self):
        other_org = Organisation.objects.create(name="Globex")
        CustomObjectDefinition.objects.create(
            organisation=self.org, name="Line Item", api_name="line_item"
        )
        # Doesn't raise — the constraint is scoped per-organisation.
        CustomObjectDefinition.objects.create(
            organisation=other_org, name="Line Item", api_name="line_item"
        )


class CustomFieldDefinitionModelTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.definition = CustomObjectDefinition.objects.create(
            organisation=self.org, name="Line Item", api_name="line_item"
        )

    def test_api_name_unique_per_object_definition(self):
        CustomFieldDefinition.objects.create(
            object_definition=self.definition,
            name="Product",
            api_name="product",
            field_type=CustomFieldDefinition.FieldType.TEXT,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            CustomFieldDefinition.objects.create(
                object_definition=self.definition,
                name="Product Again",
                api_name="product",
                field_type=CustomFieldDefinition.FieldType.TEXT,
            )


class CustomObjectRecordModelTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=self.org, name="Globex Corp")
        self.definition = CustomObjectDefinition.objects.create(
            organisation=self.org, name="Line Item", api_name="line_item"
        )

    def test_requires_exactly_one_parent(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            CustomObjectRecord.objects.create(object_definition=self.definition)

    def test_cannot_set_both_customer_and_account(self):
        from services.customers.models import Account

        account = Account.objects.create(name="North America")
        account.customers.add(self.customer)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CustomObjectRecord.objects.create(
                object_definition=self.definition, customer=self.customer, account=account
            )

    def test_a_valid_record_saves_real_data(self):
        record = CustomObjectRecord.objects.create(
            object_definition=self.definition,
            customer=self.customer,
            data={"product": "Seat License", "qty": 50},
        )
        record.refresh_from_db()
        self.assertEqual(record.data, {"product": "Seat License", "qty": 50})
