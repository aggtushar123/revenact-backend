"""The contacts list's filtering and summary as functions, shared by
GET /contacts/ and Ask on Contacts (copilot.contacts_grounding)."""

from django.test import TestCase

from services.accounts.models import Organisation
from services.customers.contact_list import (
    ContactFilters,
    contacts_summary,
    filtered_contacts,
    parse_contact_filters,
)
from services.customers.models import Contact, Customer
from services.customers.tests.test_views import blind_to_one_account


class ParseTests(TestCase):
    def test_reads_the_list_endpoints_keys_and_drops_unusable_values(self):
        self.assertEqual(
            parse_contact_filters(
                {
                    "search": "  sam ",
                    "customer": "4",
                    "account": "x",
                    "sentiment": "negative",
                    "role": "decision_maker",
                }
            ),
            ContactFilters(search="sam", customer=4, sentiment="negative", role="decision_maker"),
        )
        self.assertEqual(parse_contact_filters({"company": "7"}), ContactFilters(customer=7))
        self.assertEqual(
            parse_contact_filters({"sentiment": "angry", "role": "king", "customer": "-1"}),
            ContactFilters(),
        )


class FilterTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme")
        self.pizza = Customer.objects.create(organisation=self.org, name="Pizza Hut")
        self.viewer, self.seen, self.hidden = blind_to_one_account(self.pizza)
        # Not role="decision_maker": that string itself contains "si"
        # (deci*si*on_maker), which would make it match the search="si"
        # case below through the search filter's own role__icontains —
        # a fixture collision, not the behaviour under test. Any other
        # DECISION_ROLES member exercises the decision_makers summary
        # the same way.
        self.sam = Contact.objects.create(
            customer=self.pizza, name="Sam", sentiment="negative", role="economic_buyer"
        )
        self.sid = Contact.objects.create(account=self.seen, name="Sid", sentiment="positive")
        self.hal = Contact.objects.create(account=self.hidden, name="Hal", sentiment="negative")

    def names(self, **filters):
        return [c.name for c in filtered_contacts(self.viewer, ContactFilters(**filters))]

    def test_only_people_the_viewer_may_open_in_name_order(self):
        self.assertEqual(self.names(), ["Sam", "Sid"])

    def test_filters_narrow_as_the_list_does(self):
        self.assertEqual(self.names(sentiment="negative"), ["Sam"])
        self.assertEqual(self.names(account=self.seen.pk), ["Sid"])
        self.assertEqual(self.names(customer=self.pizza.pk), ["Sam", "Sid"])
        self.assertEqual(self.names(search="si"), ["Sid"])

    def test_a_company_the_viewer_cannot_open_matches_nobody(self):
        self.assertEqual(self.names(account=self.hidden.pk), [])
        self.assertEqual(self.names(customer=999999), [])

    def test_the_summary_counts_the_whole_filtered_set(self):
        summary = contacts_summary(filtered_contacts(self.viewer, ContactFilters()))
        self.assertEqual(
            {k: summary[k] for k in ("total", "positive", "negative", "decision_makers", "active")},
            {"total": 2, "positive": 1, "negative": 1, "decision_makers": 1, "active": 2},
        )
