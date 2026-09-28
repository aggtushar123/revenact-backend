"""Unit tier: the account fields the organisation page's account chips and
tags read. No database: the models are built in memory."""

from django.test import SimpleTestCase

from services.customers.models import Account, Attachment, Call, Customer
from services.customers.serializers import AttachmentSerializer, CallSerializer


class AttachmentAccountFieldsTests(SimpleTestCase):
    def test_an_account_level_file_names_its_account(self):
        row = Attachment(account=Account(id=5, name="EMEA"))
        self.assertEqual(AttachmentSerializer().get_account_name(row), "EMEA")

    def test_an_organisation_level_file_has_no_account(self):
        row = Attachment(customer=Customer(id=1, name="Pizza Hut"))
        self.assertIsNone(AttachmentSerializer().get_account_name(row))

    def test_the_account_id_is_part_of_the_row(self):
        self.assertIn("account_id", AttachmentSerializer().fields)


class CallAccountFieldsTests(SimpleTestCase):
    def test_an_account_level_call_names_its_account(self):
        row = Call(account=Account(id=5, name="EMEA"))
        self.assertEqual(CallSerializer().get_account_name(row), "EMEA")

    def test_an_organisation_level_call_has_no_account(self):
        row = Call(customer=Customer(id=1, name="Pizza Hut"))
        self.assertIsNone(CallSerializer().get_account_name(row))

    def test_the_account_id_cannot_be_written(self):
        # Logging a call takes its parent from the URL, never the body.
        self.assertTrue(CallSerializer().fields["account_id"].read_only)
