"""Every organisation has exactly one owner, and only they (or platform staff)
can move it."""

from rest_framework import status
from rest_framework.test import APITestCase

from core.models import AuditEvent
from services.accounts.models import Organisation, User
from services.identity import onboarding, ownership
from services.identity.models import OrganizationMembership
from services.identity.providers import VerifiedIdentity


class FounderTests(APITestCase):
    def test_password_signup_makes_the_founder_the_owner(self):
        response = self.client.post(
            "/api/v1/auth/signup/",
            {
                "organisation_name": "Newco",
                "name": "Priya",
                "email": "priya@newco.io",
                "password": "a-long-random-password-1",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(email="priya@newco.io")
        self.assertTrue(ownership.is_owner(user))
        self.assertEqual(ownership.owner_membership(user.organisation).user, user)

    def test_a_self_serve_workspace_founder_is_the_owner(self):
        identity = VerifiedIdentity(
            provider="google", subject="s", email="priya@newco.io", email_verified=True, name="P"
        )
        user = onboarding.create_workspace(identity, organisation_name="Newco")
        self.assertTrue(ownership.is_owner(user))

    def test_one_owner_per_organisation(self):
        from django.db import IntegrityError, transaction

        org = Organisation.objects.create(name="Acme")
        a = User.objects.create_user(email="a@acme.io", password="x", name="A", organisation=org)
        b = User.objects.create_user(email="b@acme.io", password="x", name="B", organisation=org)
        ownership.claim(a, org)
        ownership.claim(b, org)  # no-op: never steals
        self.assertTrue(ownership.is_owner(a))
        self.assertFalse(ownership.is_owner(b))
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                OrganizationMembership.objects.filter(user=b).update(is_owner=True)


class TransferTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme")
        self.owner = User.objects.create_user(
            email="owner@acme.io",
            password="x",
            name="Owner",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        ownership.claim(self.owner, self.org)
        self.admin = User.objects.create_user(
            email="admin@acme.io",
            password="x",
            name="Admin",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.csm = User.objects.create_user(
            email="csm@acme.io", password="x", name="CSM", organisation=self.org
        )

    def test_the_owner_hands_over_and_the_new_owner_becomes_an_admin(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            "/api/v1/auth/organisation/owner/", {"user_id": self.csm.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["owner"]["id"], self.csm.id)
        self.assertFalse(ownership.is_owner(User.objects.get(pk=self.owner.pk)))
        self.assertTrue(ownership.is_owner(User.objects.get(pk=self.csm.pk)))
        self.assertEqual(User.objects.get(pk=self.csm.pk).role.slug, "admin")
        self.assertTrue(AuditEvent.objects.filter(action="organisation.owner_transferred").exists())

    def test_another_admin_cannot_take_ownership(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            "/api/v1/auth/organisation/owner/", {"user_id": self.admin.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["error"]["code"], "NOT_OWNER")
        self.assertTrue(ownership.is_owner(self.owner))

    def test_ownership_cannot_leave_the_active_membership(self):
        outsider = User.objects.create_user(
            email="x@rival.io",
            password="x",
            name="X",
            organisation=Organisation.objects.create(name="R"),
        )
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            "/api/v1/auth/organisation/owner/", {"user_id": outsider.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["code"], "NOT_A_MEMBER")

    def test_another_admin_cannot_deactivate_or_demote_the_owner(self):
        self.client.force_authenticate(self.admin)
        deactivate = self.client.patch(
            f"/api/v1/auth/users/{self.owner.id}/", {"is_active": False}, format="json"
        )
        self.assertEqual(deactivate.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("owns the organisation", str(deactivate.data))

        csm_role = self.csm.role
        demote = self.client.patch(
            f"/api/v1/auth/users/{self.owner.id}/", {"role_id": csm_role.id}, format="json"
        )
        self.assertEqual(demote.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(User.objects.get(pk=self.owner.pk).is_active)

        # Renaming them is fine: that is not their standing.
        rename = self.client.patch(
            f"/api/v1/auth/users/{self.owner.id}/", {"name": "The Owner"}, format="json"
        )
        self.assertEqual(rename.status_code, status.HTTP_200_OK)

    def test_the_organisation_endpoint_names_its_owner(self):
        self.client.force_authenticate(self.csm)
        response = self.client.get("/api/v1/auth/organisation/")
        self.assertEqual(response.data["owner"]["email"], "owner@acme.io")
        me = self.client.get("/api/v1/auth/me/")
        self.assertFalse(me.data["is_owner"])
        self.client.force_authenticate(self.owner)
        self.assertTrue(self.client.get("/api/v1/auth/me/").data["is_owner"])
