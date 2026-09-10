"""Unit tier: the scope rule in isolation, no HTTP.

`Connector.covers()` is the whole point of this model — everything
else is CRUD. These tests define what "this connector applies here"
means.
"""

from django.db import IntegrityError, transaction
from django.test import TestCase

from services.accounts.models import Organisation
from services.connectors.models import Connector
from services.customers.models import Account, Customer


def create_account(customer, **kwargs):
    """Account.customers is a many-to-many, so
    `Account.objects.create(customer=...)` doesn't work — same helper
    every other test module here carries."""
    account = Account.objects.create(**kwargs)
    account.customers.add(customer)
    return account


class ConnectorScopeTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.apple = Customer.objects.create(organisation=self.org, name="Apple Inc")
        self.kraft = Customer.objects.create(organisation=self.org, name="Kraft Heinz")
        self.apple_emea = create_account(self.apple, name="Apple EMEA")
        self.kraft_apac = create_account(self.kraft, name="APAC Division")

    def _connector(self, **kwargs):
        defaults = {
            "organisation": self.org,
            "provider": Connector.Provider.ZENDESK,
            "name": "Zendesk",
        }
        return Connector.objects.create(**{**defaults, **kwargs})

    # ── empty means everywhere ───────────────────────────────────────

    def test_a_connector_with_nothing_linked_covers_the_whole_organisation(self):
        connector = self._connector()

        self.assertTrue(connector.is_organisation_wide)
        self.assertTrue(connector.covers(customer=self.apple))
        self.assertTrue(connector.covers(customer=self.kraft))
        self.assertTrue(connector.covers(account=self.apple_emea))

    def test_linking_one_customer_narrows_it_to_that_customer(self):
        connector = self._connector()
        connector.customers.add(self.apple)

        self.assertFalse(connector.is_organisation_wide)
        self.assertTrue(connector.covers(customer=self.apple))
        self.assertFalse(connector.covers(customer=self.kraft))

    def test_two_companies_can_be_on_different_providers(self):
        """The case the whole feature exists for."""
        zendesk = self._connector(name="Zendesk")
        zendesk.customers.add(self.apple)
        jira = self._connector(provider=Connector.Provider.JIRA, name="Jira")
        jira.customers.add(self.kraft)

        self.assertTrue(zendesk.covers(customer=self.apple))
        self.assertFalse(zendesk.covers(customer=self.kraft))
        self.assertTrue(jira.covers(customer=self.kraft))
        self.assertFalse(jira.covers(customer=self.apple))

    # ── reaching down from a customer to its accounts ────────────────

    def test_linking_a_customer_covers_its_accounts_too(self):
        """Connecting Zendesk to "Apple Inc" shouldn't require naming
        every Apple region separately."""
        connector = self._connector()
        connector.customers.add(self.apple)

        self.assertTrue(connector.covers(account=self.apple_emea))
        self.assertFalse(connector.covers(account=self.kraft_apac))

    def test_linking_an_account_does_not_cover_its_parent(self):
        """Deliberately one-directional: one division being on Zendesk
        says nothing about the others, so it can't put the whole
        company on Zendesk."""
        connector = self._connector()
        connector.accounts.add(self.apple_emea)

        self.assertTrue(connector.covers(account=self.apple_emea))
        self.assertFalse(connector.covers(customer=self.apple))

    def test_an_account_link_alone_still_counts_as_narrowed(self):
        connector = self._connector()
        connector.accounts.add(self.apple_emea)

        self.assertFalse(connector.is_organisation_wide)
        self.assertFalse(connector.covers(account=self.kraft_apac))

    # ── uniqueness ───────────────────────────────────────────────────

    def test_the_same_provider_can_be_connected_twice_under_different_names(self):
        self._connector(name="Zendesk (EU)")
        self._connector(name="Zendesk (US)")

        self.assertEqual(Connector.objects.filter(provider="zendesk").count(), 2)

    def test_the_same_name_for_the_same_provider_is_rejected(self):
        self._connector(name="Zendesk")

        with self.assertRaises(IntegrityError), transaction.atomic():
            self._connector(name="Zendesk")

    def test_another_organisation_can_use_the_same_name(self):
        other_org = Organisation.objects.create(name="Other Org")
        self._connector(name="Zendesk")

        other = Connector.objects.create(
            organisation=other_org, provider=Connector.Provider.ZENDESK, name="Zendesk"
        )

        self.assertEqual(other.name, "Zendesk")
