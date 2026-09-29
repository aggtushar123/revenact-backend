"""What a send from Contacts may say about where it was asked. The client
sends ids and filter values; the server checks them and names them."""

from django.test import TestCase

from services.accounts.models import Organisation
from services.copilot.contacts_context import (
    NOT_A_PERSON,
    NOT_OPEN_ACCOUNT,
    NOT_OPEN_ORGANISATION,
    ContactsContextSerializer,
    filters_of,
)
from services.customers.contact_list import ContactFilters
from services.customers.models import Contact, Customer
from services.customers.tests.test_views import blind_to_one_account


class ContextFixture(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme")
        self.pizza = Customer.objects.create(organisation=self.org, name="Pizza Hut")
        self.viewer, self.seen, self.hidden = blind_to_one_account(self.pizza)
        self.sam = Contact.objects.create(customer=self.pizza, name="Sam Pizza")
        self.sid = Contact.objects.create(account=self.seen, name="Sid Seen")
        self.hal = Contact.objects.create(account=self.hidden, name="Hal Hidden")

    def validate(self, data, user=None):
        serializer = ContactsContextSerializer(data=data, context={"user": user or self.viewer})
        valid = serializer.is_valid()
        return valid, (serializer.validated_data if valid else serializer.errors)


class ListViewTests(ContextFixture):
    def test_filters_are_kept_in_the_pages_url_form_and_named_by_the_server(self):
        valid, data = self.validate(
            {
                "surface": "contacts",
                "view": "list",
                "filters": {
                    "q": " sam ",
                    "customer": str(self.pizza.pk),
                    "account": self.seen.pk,
                    "sentiment": "negative",
                    "role": "decision_maker",
                    "junk": "x",
                },
                "label": "Spoofed",
            }
        )
        self.assertTrue(valid, data)
        self.assertEqual(
            data,
            {
                "surface": "contacts",
                "view": "list",
                "filters": {
                    "q": "sam",
                    "customer": str(self.pizza.pk),
                    "account": str(self.seen.pk),
                    "sentiment": "negative",
                    "role": "decision_maker",
                },
                "label": 'Contacts · Pizza Hut › Seen · Negative · Decision Maker · "sam"',
            },
        )

    def test_no_filters_reads_contacts_alone_and_bad_values_are_dropped(self):
        valid, data = self.validate(
            {
                "surface": "contacts",
                "view": "list",
                "filters": {"sentiment": "angry", "role": ["x"], "q": "y" * 101},
            }
        )
        self.assertTrue(valid, data)
        self.assertEqual((data["filters"], data["label"]), ({}, "Contacts"))

    def test_a_company_the_asker_cannot_open_reads_the_same_as_a_missing_one(self):
        for field, value, message in (
            ("customer", 999999, NOT_OPEN_ORGANISATION),
            ("account", self.hidden.pk, NOT_OPEN_ACCOUNT),
            ("account", 999999, NOT_OPEN_ACCOUNT),
        ):
            with self.subTest(field=field, value=value):
                valid, errors = self.validate(
                    {"surface": "contacts", "view": "list", "filters": {field: str(value)}}
                )
                self.assertFalse(valid)
                self.assertEqual(errors, {"filters": {field: [message]}})

    def test_filters_of_reads_stored_filters_back(self):
        self.assertEqual(
            filters_of({"q": "sam", "customer": "4", "sentiment": "negative"}),
            ContactFilters(search="sam", customer=4, sentiment="negative"),
        )


class PersonViewTests(ContextFixture):
    def test_the_person_is_named_by_the_server_with_where_they_sit(self):
        for contact, label in (
            (self.sam, "Sam Pizza · Pizza Hut"),
            (self.sid, "Sid Seen · Pizza Hut › Seen"),
        ):
            with self.subTest(label=label):
                valid, data = self.validate(
                    {
                        "surface": "contacts",
                        "view": "person",
                        "contact": contact.pk,
                        "focus": "sentiment",
                        "label": "Spoofed",
                    }
                )
                self.assertTrue(valid, data)
                self.assertEqual(
                    data,
                    {
                        "surface": "contacts",
                        "view": "person",
                        "contact": contact.pk,
                        "label": label,
                        "focus": "sentiment",
                    },
                )

    def test_a_person_the_asker_cannot_open_reads_the_same_as_a_missing_one(self):
        for pk in (self.hal.pk, 999999):
            with self.subTest(pk=pk):
                valid, errors = self.validate(
                    {"surface": "contacts", "view": "person", "contact": pk}
                )
                self.assertFalse(valid)
                self.assertEqual(errors, {"contact": [NOT_A_PERSON]})

    def test_focus_is_sentiment_or_nothing(self):
        valid, data = self.validate(
            {"surface": "contacts", "view": "person", "contact": self.sam.pk}
        )
        self.assertTrue(valid, data)
        self.assertIsNone(data["focus"])
        valid, errors = self.validate(
            {"surface": "contacts", "view": "person", "contact": self.sam.pk, "focus": "calls"}
        )
        self.assertFalse(valid)
        self.assertIn("focus", errors)
