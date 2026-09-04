"""Unit tier: model/manager logic in isolation, no HTTP."""

from django.test import TestCase

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
        self.assertEqual(user.role, User.Role.CSM)  # default

    def test_create_superuser_has_no_organisation_and_is_admin_role(self):
        superuser = User.objects.create_superuser(email="staff@revenact.io", password="x1234567")
        self.assertIsNone(superuser.organisation)
        self.assertTrue(superuser.is_staff)
        self.assertTrue(superuser.is_superuser)
        self.assertEqual(superuser.role, User.Role.ADMIN)

    def test_is_org_admin_property(self):
        org = Organisation.objects.create(name="Acme Inc")
        admin = User.objects.create_user(
            email="a@acme.io", password="x1234567", name="A", organisation=org, role=User.Role.ADMIN
        )
        csm = User.objects.create_user(
            email="c@acme.io", password="x1234567", name="C", organisation=org, role=User.Role.CSM
        )
        self.assertTrue(admin.is_org_admin)
        self.assertFalse(csm.is_org_admin)
