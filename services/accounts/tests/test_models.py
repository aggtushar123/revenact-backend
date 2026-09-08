"""Unit tier: model/manager logic in isolation, no HTTP."""

from django.test import TestCase

from services.accounts.capabilities import Capability
from services.accounts.models import Organisation, User


class OrganisationSlugTests(TestCase):
    def test_slug_is_generated_from_name(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.assertEqual(org.slug, "acme-inc")

    def test_slug_is_unique_when_names_collide(self):
        Organisation.objects.create(name="Acme Inc")
        second = Organisation.objects.create(name="Acme Inc")
        self.assertEqual(second.slug, "acme-inc-2")

    def test_currency_defaults_to_usd_and_no_default_lifecycle_stage(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.assertEqual(org.currency, Organisation.Currency.USD)
        self.assertEqual(org.default_lifecycle_stage, "")

    def test_ai_agent_defaults(self):
        org = Organisation.objects.create(name="Acme Inc")
        self.assertTrue(org.ai_agent_enabled)
        self.assertEqual(org.ai_agent_tone, Organisation.AgentTone.PROFESSIONAL)


class UserManagerTests(TestCase):
    def test_create_user_hashes_password_and_normalizes_email(self):
        org = Organisation.objects.create(name="Acme Inc")
        user = User.objects.create_user(
            email="Alice@Acme.io", password="supersecret1", name="Alice", organisation=org
        )
        self.assertEqual(user.email, "Alice@acme.io")
        self.assertNotEqual(user.password, "supersecret1")
        self.assertTrue(user.check_password("supersecret1"))
        self.assertEqual(user.role.slug, User.Role.CSM)  # default

    def test_create_superuser_has_no_organisation_and_no_role(self):
        """Platform staff belong to no tenant, so there's no org-scoped
        Role to hold — but has_capability still grants them everything,
        the same way Django's own permission checks do."""

        superuser = User.objects.create_superuser(email="staff@revenact.io", password="x1234567")
        self.assertIsNone(superuser.organisation)
        self.assertTrue(superuser.is_staff)
        self.assertTrue(superuser.is_superuser)
        self.assertIsNone(superuser.role)
        self.assertTrue(superuser.has_capability(Capability.MANAGE_USERS))

    def test_a_role_slug_string_resolves_to_that_organisations_real_role(self):
        """`create_user(role="admin")` still works now that role is a
        ForeignKey — the slug resolves to the org's own Admin row."""

        org = Organisation.objects.create(name="Acme Inc")
        admin = User.objects.create_user(
            email="a@acme.io", password="x1234567", name="A", organisation=org, role=User.Role.ADMIN
        )
        self.assertEqual(admin.role.organisation, org)
        self.assertEqual(admin.role.slug, User.Role.ADMIN)
        self.assertTrue(admin.role.is_system)

    def test_has_capability_reflects_the_users_own_role(self):
        org = Organisation.objects.create(name="Acme Inc")
        admin = User.objects.create_user(
            email="a@acme.io", password="x1234567", name="A", organisation=org, role=User.Role.ADMIN
        )
        csm = User.objects.create_user(
            email="c@acme.io", password="x1234567", name="C", organisation=org, role=User.Role.CSM
        )
        for capability in Capability.values:
            self.assertTrue(admin.has_capability(capability))
            self.assertFalse(csm.has_capability(capability))

    def test_ensure_system_roles_is_idempotent(self):
        org = Organisation.objects.create(name="Acme Inc")
        first = org.ensure_system_roles()
        second = org.ensure_system_roles()
        self.assertEqual(org.roles.count(), 2)
        self.assertEqual(first[User.Role.ADMIN].pk, second[User.Role.ADMIN].pk)
