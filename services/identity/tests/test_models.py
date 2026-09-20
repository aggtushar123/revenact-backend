"""The invariants the identity models exist to enforce."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from services.accounts.models import Organisation, Role, User
from services.identity.models import (
    Department,
    Identity,
    OrganizationDomain,
    OrganizationMembership,
)


class IdentityTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io", password="x", name="Alice", organisation=self.org
        )

    def test_the_same_provider_subject_cannot_belong_to_two_people(self):
        Identity.objects.create(
            user=self.user, provider="google", provider_user_id="sub-1", email="alice@acme.io"
        )
        other = User.objects.create_user(
            email="mallory@acme.io", password="x", name="Mallory", organisation=self.org
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Identity.objects.create(
                    user=other,
                    provider="google",
                    provider_user_id="sub-1",
                    email="mallory@acme.io",
                )

    def test_one_person_may_hold_both_providers(self):
        Identity.objects.create(
            user=self.user, provider="google", provider_user_id="g-1", email="alice@acme.io"
        )
        Identity.objects.create(
            user=self.user, provider="microsoft", provider_user_id="m-1", email="alice@acme.io"
        )
        self.assertEqual(self.user.identities.count(), 2)

    def test_the_same_subject_may_repeat_across_providers(self):
        """Google's and Microsoft's identifier spaces are unrelated."""
        Identity.objects.create(
            user=self.user, provider="google", provider_user_id="same", email="alice@acme.io"
        )
        Identity.objects.create(
            user=self.user, provider="microsoft", provider_user_id="same", email="alice@acme.io"
        )
        self.assertEqual(Identity.objects.count(), 2)

    def test_email_is_normalised_on_the_way_in(self):
        identity = Identity.objects.create(
            user=self.user, provider="google", provider_user_id="g-2", email="  Alice@ACME.io "
        )
        self.assertEqual(identity.email, "alice@acme.io")


class MembershipTests(TestCase):
    def setUp(self):
        self.acme = Organisation.objects.create(name="Acme Inc")
        self.rival = Organisation.objects.create(name="Rival Ltd")
        self.user = User.objects.create_user(
            email="consultant@example.com", password="x", name="Consultant"
        )
        self.acme_role = Role.objects.create(
            organisation=self.acme, name="Manager", slug="manager", permissions=[]
        )
        self.rival_role = Role.objects.create(
            organisation=self.rival, name="Viewer", slug="viewer", permissions=[]
        )

    def test_one_person_can_belong_to_two_organisations(self):
        """The whole reason this model exists."""
        OrganizationMembership.objects.create(
            organisation=self.acme, user=self.user, status="active", role=self.acme_role
        )
        OrganizationMembership.objects.create(
            organisation=self.rival, user=self.user, status="active", role=self.rival_role
        )
        self.assertEqual(self.user.memberships.count(), 2)

    def test_only_one_live_membership_per_person_per_organisation(self):
        OrganizationMembership.objects.create(
            organisation=self.acme, user=self.user, status="active"
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                OrganizationMembership.objects.create(
                    organisation=self.acme, user=self.user, status="pending"
                )

    def test_leaving_and_rejoining_keeps_the_history(self):
        """A revoked row is history, so it must not block a new membership."""
        OrganizationMembership.objects.create(
            organisation=self.acme, user=self.user, status="revoked"
        )
        OrganizationMembership.objects.create(
            organisation=self.acme, user=self.user, status="active"
        )
        self.assertEqual(
            sorted(
                OrganizationMembership.objects.filter(
                    organisation=self.acme, user=self.user
                ).values_list("status", flat=True)
            ),
            ["active", "revoked"],
        )

    def test_a_role_from_another_tenant_is_refused(self):
        """Accepting it would be a cross-tenant privilege leak."""
        membership = OrganizationMembership(
            organisation=self.acme, user=self.user, status="active", role=self.rival_role
        )
        with self.assertRaises(ValidationError):
            membership.save()

    def test_a_department_from_another_tenant_is_refused(self):
        elsewhere = Department.objects.create(organisation=self.rival, name="Engineering")
        membership = OrganizationMembership(
            organisation=self.acme, user=self.user, status="active", department=elsewhere
        )
        with self.assertRaises(ValidationError):
            membership.save()

    def test_is_live_tracks_the_slot_holding_statuses(self):
        membership = OrganizationMembership(organisation=self.acme, user=self.user)
        for status, expected in [
            ("pending", True),
            ("active", True),
            ("suspended", True),
            ("rejected", False),
            ("revoked", False),
        ]:
            membership.status = status
            self.assertIs(membership.is_live, expected, status)


class DepartmentTests(TestCase):
    def setUp(self):
        self.acme = Organisation.objects.create(name="Acme Inc")
        self.rival = Organisation.objects.create(name="Rival Ltd")

    def test_names_are_unique_per_organisation_case_insensitively(self):
        Department.objects.create(organisation=self.acme, name="Engineering")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Department.objects.create(organisation=self.acme, name="engineering")

    def test_two_organisations_may_each_have_the_same_department_name(self):
        Department.objects.create(organisation=self.acme, name="Engineering")
        Department.objects.create(organisation=self.rival, name="Engineering")
        self.assertEqual(Department.objects.filter(name="Engineering").count(), 2)


class OrganizationDomainTests(TestCase):
    def setUp(self):
        self.acme = Organisation.objects.create(name="Acme Inc")
        self.rival = Organisation.objects.create(name="Rival Ltd")

    def test_a_domain_cannot_be_claimed_by_two_organisations(self):
        """Otherwise a corporate sign-in would be ambiguous about the tenant."""
        OrganizationDomain.objects.create(organisation=self.acme, domain="acme.io")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                OrganizationDomain.objects.create(organisation=self.rival, domain="acme.io")

    def test_a_new_domain_is_not_verified(self):
        """Typing a domain is a claim, not evidence of owning it."""
        domain = OrganizationDomain.objects.create(organisation=self.acme, domain="acme.io")
        self.assertEqual(domain.verification_status, "pending")
        self.assertFalse(domain.is_verified)

    def test_domains_are_normalised(self):
        domain = OrganizationDomain.objects.create(organisation=self.acme, domain=" ACME.IO. ")
        self.assertEqual(domain.domain, "acme.io")

    def test_only_one_primary_domain_per_organisation(self):
        OrganizationDomain.objects.create(organisation=self.acme, domain="acme.io", is_primary=True)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                OrganizationDomain.objects.create(
                    organisation=self.acme, domain="acme.co.uk", is_primary=True
                )
