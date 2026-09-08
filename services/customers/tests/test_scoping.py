"""Unit tier: the visibility rule in isolation, no HTTP.

These are the tests that define the policy. The view-level tests in
test_views.py check that endpoints actually route through it; this file
checks the rule itself is right, so a failure here means the policy is
wrong and a failure there means an endpoint forgot to apply it.
"""

from django.test import TestCase

from services.accounts.models import Organisation, User
from services.customers.models import Account, Customer, Note
from services.customers.scoping import (
    visible_accounts,
    visible_children_q,
    visible_customers,
)


def create_account(customer, **kwargs):
    """Same helper as the other test modules — Account.customers is a
    many-to-many, so `Account.objects.create(customer=...)` doesn't
    work."""
    account = Account.objects.create(**kwargs)
    account.customers.add(customer)
    return account


class VisibilityRuleTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="supersecret1",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.other = User.objects.create_user(
            email="dana@acme.io",
            password="supersecret1",
            name="Dana",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )

    # ── the plain cases ──────────────────────────────────────────────

    def test_you_see_a_customer_you_own(self):
        mine = Customer.objects.create(organisation=self.org, name="Mine", owner=self.csm)
        self.assertIn(mine, visible_customers(self.csm))

    def test_you_do_not_see_a_customer_someone_else_owns(self):
        theirs = Customer.objects.create(organisation=self.org, name="Theirs", owner=self.other)
        self.assertNotIn(theirs, visible_customers(self.csm))

    def test_you_see_an_account_you_own(self):
        parent = Customer.objects.create(organisation=self.org, name="Parent", owner=self.other)
        mine = create_account(parent, name="Mine", owner=self.csm)
        self.assertIn(mine, visible_accounts(self.csm))

    def test_you_do_not_see_an_account_someone_else_owns(self):
        parent = Customer.objects.create(organisation=self.org, name="Parent", owner=self.other)
        theirs = create_account(parent, name="Theirs", owner=self.other)
        self.assertNotIn(theirs, visible_accounts(self.csm))

    # ── reaching in both directions ──────────────────────────────────

    def test_owning_a_customer_shows_you_its_accounts(self):
        mine = Customer.objects.create(organisation=self.org, name="Mine", owner=self.csm)
        division = create_account(mine, name="Mine EMEA")
        self.assertIn(division, visible_accounts(self.csm))

    def test_owning_an_account_shows_you_the_company_it_belongs_to(self):
        """Without this the account page 404s on its own header — it
        names the parent org and its Organizations tab lists it."""
        parent = Customer.objects.create(organisation=self.org, name="Parent", owner=self.other)
        create_account(parent, name="Mine EMEA", owner=self.csm)
        self.assertIn(parent, visible_customers(self.csm))

    def test_owning_an_account_does_not_show_you_its_siblings(self):
        """The asymmetry that makes reaching upward safe: you get the
        company, not the rest of its book."""
        parent = Customer.objects.create(organisation=self.org, name="Parent", owner=self.other)
        create_account(parent, name="Mine EMEA", owner=self.csm)
        sibling = create_account(parent, name="Their APAC", owner=self.other)
        self.assertNotIn(sibling, visible_accounts(self.csm))

    # ── unowned ──────────────────────────────────────────────────────

    def test_an_unowned_customer_is_visible_to_everyone(self):
        unowned = Customer.objects.create(organisation=self.org, name="Unassigned")
        self.assertIn(unowned, visible_customers(self.csm))
        self.assertIn(unowned, visible_customers(self.other))

    def test_an_unowned_account_is_visible_to_everyone(self):
        parent = Customer.objects.create(organisation=self.org, name="Parent", owner=self.other)
        unowned = create_account(parent, name="Unassigned")
        self.assertIn(unowned, visible_accounts(self.csm))

    # ── the capability ───────────────────────────────────────────────

    def test_the_capability_shows_everything(self):
        theirs = Customer.objects.create(organisation=self.org, name="Theirs", owner=self.other)
        their_account = create_account(theirs, name="Theirs EMEA", owner=self.other)

        self.assertIn(theirs, visible_customers(self.admin))
        self.assertIn(their_account, visible_accounts(self.admin))

    def test_the_capability_stops_at_the_tenant_boundary(self):
        """Widest it ever gets is the caller's own organisation — this
        replaces an ownership filter, never the org one."""
        other_org = Organisation.objects.create(name="Other Org")
        outsider = Customer.objects.create(organisation=other_org, name="Outsider")

        self.assertNotIn(outsider, visible_customers(self.admin))

    # ── the shared-account consequence ───────────────────────────────

    def test_an_account_shared_by_two_customers_is_visible_to_both_owners(self):
        """Documented consequence, not a hole: the M2M says the two
        companies share the account, so both owners can reach it."""
        mine = Customer.objects.create(organisation=self.org, name="Mine", owner=self.csm)
        theirs = Customer.objects.create(organisation=self.org, name="Theirs", owner=self.other)
        shared = Account.objects.create(name="Shared")
        shared.customers.add(mine, theirs)

        self.assertIn(shared, visible_accounts(self.csm))
        self.assertIn(shared, visible_accounts(self.other))

    # ── no duplicate rows ────────────────────────────────────────────

    def test_a_customer_with_several_owned_accounts_appears_once(self):
        """The M2M join fans out; without .distinct() this customer
        would come back once per matching account and every count in
        the app would be wrong."""
        mine = Customer.objects.create(organisation=self.org, name="Mine", owner=self.csm)
        create_account(mine, name="One", owner=self.csm)
        create_account(mine, name="Two", owner=self.csm)

        self.assertEqual(visible_customers(self.csm).filter(pk=mine.pk).count(), 1)

    def test_an_account_under_several_owned_customers_appears_once(self):
        first = Customer.objects.create(organisation=self.org, name="First", owner=self.csm)
        second = Customer.objects.create(organisation=self.org, name="Second", owner=self.csm)
        shared = Account.objects.create(name="Shared")
        shared.customers.add(first, second)

        self.assertEqual(visible_accounts(self.csm).filter(pk=shared.pk).count(), 1)

    # ── children ─────────────────────────────────────────────────────

    def _note(self, **parent):
        return Note.objects.create(
            title="T", author_name="A", body="B", logged_at="2026-03-04", **parent
        )

    def test_children_follow_their_parents_visibility(self):
        mine = Customer.objects.create(organisation=self.org, name="Mine", owner=self.csm)
        theirs = Customer.objects.create(organisation=self.org, name="Theirs", owner=self.other)
        my_note = self._note(customer=mine)
        their_note = self._note(customer=theirs)

        visible = Note.objects.filter(visible_children_q(self.csm))

        self.assertIn(my_note, visible)
        self.assertNotIn(their_note, visible)

    def test_children_of_an_owned_account_are_visible_under_someone_elses_customer(self):
        parent = Customer.objects.create(organisation=self.org, name="Parent", owner=self.other)
        mine = create_account(parent, name="Mine EMEA", owner=self.csm)
        note = self._note(account=mine)

        self.assertIn(note, Note.objects.filter(visible_children_q(self.csm)))

    def test_children_are_visible_to_a_capability_holder(self):
        theirs = Customer.objects.create(organisation=self.org, name="Theirs", owner=self.other)
        note = self._note(customer=theirs)

        self.assertIn(note, Note.objects.filter(visible_children_q(self.admin)))

    def test_children_stop_at_the_tenant_boundary(self):
        other_org = Organisation.objects.create(name="Other Org")
        outsider = Customer.objects.create(organisation=other_org, name="Outsider")
        note = self._note(customer=outsider)

        self.assertNotIn(note, Note.objects.filter(visible_children_q(self.admin)))
