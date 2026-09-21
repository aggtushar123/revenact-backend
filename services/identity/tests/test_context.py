"""Authorization resolved through membership.

Two things are being asserted here, and they pull in opposite directions:

1. **Nothing changed.** Against the data that exists today, every capability
   answer is identical to the one the column gave. If this is wrong, access
   silently shifts for real people.
2. **Two new rules are now expressible.** A pending or suspended membership
   grants nothing, and a suspended tenant grants nothing to anybody. Both are
   inert today, which is exactly why they can be added safely now rather than
   during an incident.
"""

from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase

from services.accounts.capabilities import Capability
from services.accounts.models import Organisation, Role, User
from services.identity.context import active_membership, capabilities_for, organisation_for
from services.identity.models import OrganizationMembership


class MembershipIsKeptInStepTests(TestCase):
    """New users must get a membership, or the guard starts failing for
    reasons nobody can act on."""

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")

    def test_creating_a_user_creates_their_membership(self):
        user = User.objects.create_user(
            email="new@acme.io", password="x", name="New", organisation=self.org
        )
        membership = OrganizationMembership.objects.get(user=user)
        self.assertEqual(membership.organisation_id, self.org.id)
        self.assertEqual(membership.status, "active")
        self.assertEqual(membership.role_id, user.role_id)

    def test_a_platform_superuser_gets_no_membership(self):
        root = User.objects.create_superuser(email="root@revenact.io", password="x")
        self.assertFalse(OrganizationMembership.objects.filter(user=root).exists())

    def test_saving_an_existing_user_does_not_add_a_second_membership(self):
        user = User.objects.create_user(
            email="new@acme.io", password="x", name="New", organisation=self.org
        )
        user.name = "Renamed"
        user.save()
        self.assertEqual(OrganizationMembership.objects.filter(user=user).count(), 1)


class EquivalenceTests(TestCase):
    """The answer must not change for anyone who exists today."""

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.admin_role = Role.objects.create(
            organisation=self.org,
            name="Admin",
            slug="admin-2",
            permissions=[Capability.MANAGE_USERS, Capability.VIEW_ALL_ACCOUNTS],
        )
        self.alice = User.objects.create_user(
            email="alice@acme.io",
            password="x",
            name="Alice",
            organisation=self.org,
            role=self.admin_role,
        )
        self.carl = User.objects.create_user(
            email="carl@acme.io", password="x", name="Carl", organisation=self.org
        )

    def test_capabilities_match_what_the_column_would_have_given(self):
        for user in (self.alice, self.carl):
            expected = sorted((user.role.permissions if user.role_id else None) or [])
            self.assertEqual(sorted(capabilities_for(user)), expected, user.email)

    def test_the_holder_still_holds(self):
        self.assertTrue(self.alice.has_capability(Capability.MANAGE_USERS))

    def test_the_non_holder_still_does_not(self):
        self.assertFalse(self.carl.has_capability(Capability.MANAGE_USERS))

    def test_a_superuser_still_bypasses_everything(self):
        root = User.objects.create_superuser(email="root@revenact.io", password="x")
        self.assertTrue(root.has_capability(Capability.MANAGE_USERS))
        self.assertIsNone(active_membership(root), "and belongs to no tenant")

    def test_the_organisation_resolves_the_same_either_way(self):
        self.assertEqual(organisation_for(self.alice), self.org)


class NewlyEnforceableRulesTests(TestCase):
    """Inert against today's data, which is why they are safe to add now."""

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.role = Role.objects.create(
            organisation=self.org,
            name="Admin",
            slug="admin-2",
            permissions=[Capability.MANAGE_USERS],
        )
        self.user = User.objects.create_user(
            email="alice@acme.io", password="x", name="Alice", organisation=self.org, role=self.role
        )
        self.membership = OrganizationMembership.objects.get(user=self.user)

    def test_a_suspended_membership_grants_nothing(self):
        """Even though the role is still on their user row."""
        self.membership.status = "suspended"
        self.membership.save()

        user = self.reloaded()
        self.assertEqual(capabilities_for(user), [])
        self.assertFalse(user.has_capability(Capability.MANAGE_USERS))

    def test_a_pending_membership_grants_nothing(self):
        self.membership.status = "pending"
        self.membership.save()
        self.assertFalse(self.reloaded().has_capability(Capability.MANAGE_USERS))

    def reloaded(self):
        """A fresh user instance, which is what the next request gets.

        `active_membership` memoises on the instance, so the resolved tenant is
        stable for the life of one request. Re-reading the row is therefore how
        a suspension takes effect: on the following request, not mid-flight.
        """
        return User.objects.get(pk=self.user.pk)

    def test_a_suspended_tenant_grants_nothing_to_anybody(self):
        """One field, rather than rewriting every membership the customer has."""
        self.assertTrue(self.reloaded().has_capability(Capability.MANAGE_USERS))

        Organisation.objects.filter(pk=self.org.pk).update(status="suspended")

        self.assertFalse(self.reloaded().has_capability(Capability.MANAGE_USERS))

    def test_reactivating_the_tenant_restores_access(self):
        Organisation.objects.filter(pk=self.org.pk).update(status="suspended")
        self.assertFalse(self.reloaded().has_capability(Capability.MANAGE_USERS))

        Organisation.objects.filter(pk=self.org.pk).update(status="active")
        self.assertTrue(self.reloaded().has_capability(Capability.MANAGE_USERS))

    def test_the_resolved_tenant_is_stable_within_one_request(self):
        """Suspending mid-request must not change the answer half way through a
        single request's checks; it takes effect on the next one."""
        user = self.reloaded()
        self.assertTrue(user.has_capability(Capability.MANAGE_USERS))

        Organisation.objects.filter(pk=self.org.pk).update(status="suspended")

        self.assertTrue(user.has_capability(Capability.MANAGE_USERS), "same instance, same answer")
        self.assertFalse(self.reloaded().has_capability(Capability.MANAGE_USERS), "next request")

    def test_a_suspended_tenant_does_not_affect_another_tenant(self):
        rival = Organisation.objects.create(name="Rival Ltd")
        rival_role = Role.objects.create(
            organisation=rival, name="Admin", slug="admin-2", permissions=[Capability.MANAGE_USERS]
        )
        zed = User.objects.create_user(
            email="zed@rival.io", password="x", name="Zed", organisation=rival, role=rival_role
        )

        Organisation.objects.filter(pk=self.org.pk).update(status="suspended")

        self.assertFalse(User.objects.get(pk=self.user.pk).has_capability(Capability.MANAGE_USERS))
        self.assertTrue(User.objects.get(pk=zed.pk).has_capability(Capability.MANAGE_USERS))


class AmbiguousMembershipTests(TestCase):
    """Someone with two live memberships must resolve the same way every time."""

    def setUp(self):
        self.acme = Organisation.objects.create(name="Acme Inc")
        self.rival = Organisation.objects.create(name="Rival Ltd")
        self.user = User.objects.create_user(
            email="consultant@example.com", password="x", name="Consultant", organisation=self.acme
        )
        OrganizationMembership.objects.create(
            organisation=self.rival, user=self.user, status="active"
        )

    def test_the_column_disambiguates_while_it_still_exists(self):
        membership = active_membership(self.user)
        self.assertEqual(membership.organisation_id, self.acme.id)

    def test_resolution_is_stable_across_calls(self):
        first = active_membership(self.user)
        second = active_membership(self.user)
        self.assertEqual(first.pk, second.pk)


class ConsistencyCommandTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io", password="x", name="Alice", organisation=self.org
        )

    def test_it_passes_when_the_two_representations_agree(self):
        out = StringIO()
        call_command("check_membership_consistency", stdout=out)
        self.assertIn("consistent", out.getvalue())

    def test_it_fails_when_a_membership_is_missing(self):
        OrganizationMembership.objects.filter(user=self.user).delete()
        with self.assertRaises(CommandError):
            call_command("check_membership_consistency", stdout=StringIO(), stderr=StringIO())

    def test_it_fails_when_the_membership_names_another_tenant(self):
        rival = Organisation.objects.create(name="Rival Ltd")
        OrganizationMembership.objects.filter(user=self.user).update(organisation=rival)
        with self.assertRaises(CommandError):
            call_command("check_membership_consistency", stdout=StringIO(), stderr=StringIO())

    def test_it_fails_when_a_platform_operator_holds_a_membership(self):
        root = User.objects.create_superuser(email="root@revenact.io", password="x")
        OrganizationMembership.objects.create(organisation=self.org, user=root, status="active")
        with self.assertRaises(CommandError):
            call_command("check_membership_consistency", stdout=StringIO(), stderr=StringIO())
