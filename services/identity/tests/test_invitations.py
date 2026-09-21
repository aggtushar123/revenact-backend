"""Invitations: the company asking someone in, before they ever sign in.

Defended here: an invitation grants nothing until the invited address is
verified by a provider; only that exact address can accept it; acceptance is
one transaction that leaves the membership and the columns agreeing; an
expired or cancelled invitation opens no door; and one tenant's administrator
can neither see nor cancel another's.
"""

from datetime import timedelta

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import AuditEvent
from services.accounts.capabilities import ALL_CAPABILITIES, Capability
from services.accounts.models import Organisation, Role, User
from services.identity import login, onboarding
from services.identity.context import capabilities_for
from services.identity.models import (
    AccessRequest,
    Department,
    Invitation,
    OrganizationDomain,
    OrganizationMembership,
)
from services.identity.providers import VerifiedIdentity


def verified(email, *, subject="sub-1", name="Invited Person"):
    return VerifiedIdentity(
        provider="google", subject=subject, email=email, email_verified=True, name=name
    )


def admin_of(org, email):
    role = Role.objects.create(
        organisation=org, name="Admin", slug="admin-2", permissions=list(ALL_CAPABILITIES)
    )
    return User.objects.create_user(
        email=email, password="x", name="Admin", organisation=org, role=role
    )


class InviteTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.admin = admin_of(self.org, "admin@acme.io")
        self.csm_role = Role.objects.create(
            organisation=self.org, name="CSM", slug="csm-2", permissions=[]
        )

    def test_an_invitation_is_recorded_and_emailed_and_grants_nothing_yet(self):
        invitation, created = onboarding.invite(
            self.org, email="New@Acme.io", role=self.csm_role, inviter=self.admin
        )

        self.assertTrue(created)
        self.assertEqual(invitation.email, "new@acme.io", "normalised")
        self.assertTrue(invitation.is_open)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["new@acme.io"])
        self.assertIn("Acme Inc", mail.outbox[0].body)
        self.assertNotIn("token", mail.outbox[0].body.lower())

        # Nobody exists yet, and nothing has been granted.
        self.assertFalse(User.objects.filter(email="new@acme.io").exists())
        self.assertEqual(OrganizationMembership.objects.filter(organisation=self.org).count(), 1)
        self.assertTrue(AuditEvent.objects.filter(action="invitation.created").exists())

    def test_inviting_again_resends_rather_than_duplicating(self):
        onboarding.invite(self.org, email="new@acme.io", role=self.csm_role, inviter=self.admin)
        _, created = onboarding.invite(
            self.org, email="new@acme.io", role=self.csm_role, inviter=self.admin
        )
        self.assertFalse(created)
        self.assertEqual(Invitation.objects.filter(email="new@acme.io").count(), 1)
        self.assertEqual(len(mail.outbox), 2)

    def test_an_inviter_cannot_grant_more_than_they_hold(self):
        """Or `manage_users` would be a route to full administration."""
        limited_role = Role.objects.create(
            organisation=self.org,
            name="User manager",
            slug="um",
            permissions=[Capability.MANAGE_USERS],
        )
        limited = User.objects.create_user(
            email="um@acme.io", password="x", name="UM", organisation=self.org, role=limited_role
        )
        full = Role.objects.create(
            organisation=self.org, name="Everything", slug="all", permissions=list(ALL_CAPABILITIES)
        )
        with self.assertRaises(onboarding.OnboardingError) as caught:
            onboarding.invite(self.org, email="new@acme.io", role=full, inviter=limited)
        self.assertEqual(caught.exception.code, "INSUFFICIENT_PERMISSION")
        self.assertFalse(Invitation.objects.exists())

    def test_a_role_from_another_tenant_is_refused(self):
        rival = Organisation.objects.create(name="Rival")
        foreign = Role.objects.create(organisation=rival, name="R", slug="r", permissions=[])
        with self.assertRaises(onboarding.OnboardingError) as caught:
            onboarding.invite(self.org, email="new@acme.io", role=foreign, inviter=self.admin)
        self.assertEqual(caught.exception.code, "INVALID_ROLE")

    def test_an_existing_member_cannot_be_invited(self):
        with self.assertRaises(onboarding.OnboardingError) as caught:
            onboarding.invite(
                self.org, email="admin@acme.io", role=self.csm_role, inviter=self.admin
            )
        self.assertEqual(caught.exception.code, "ALREADY_A_MEMBER")

    def test_cancelling_closes_the_door(self):
        invitation, _ = onboarding.invite(
            self.org, email="new@acme.io", role=self.csm_role, inviter=self.admin
        )
        onboarding.cancel_invitation(invitation, actor=self.admin)
        self.assertIsNone(onboarding.open_invitation_for("new@acme.io"))
        self.assertTrue(AuditEvent.objects.filter(action="invitation.cancelled").exists())


class AcceptAtSignInTests(TestCase):
    """The invited person just signs in. That is the whole acceptance."""

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.admin = admin_of(self.org, "admin@acme.io")
        self.dept = Department.objects.create(organisation=self.org, name="Success")
        self.role = Role.objects.create(
            organisation=self.org,
            name="CSM",
            slug="csm-2",
            permissions=[Capability.MANAGE_FX_RATES],
        )
        self.invitation, _ = onboarding.invite(
            self.org,
            email="new@acme.io",
            role=self.role,
            department=self.dept,
            inviter=self.admin,
        )

    def test_the_invited_address_is_signed_straight_in_with_the_promised_role(self):
        user = login.resolve_user(verified("new@acme.io"))

        self.assertEqual(user.organisation, self.org)
        self.assertEqual(user.role, self.role)
        self.assertEqual(capabilities_for(user), [Capability.MANAGE_FX_RATES])
        membership = OrganizationMembership.objects.get(user=user)
        self.assertEqual(membership.status, "active")
        self.assertEqual(membership.department, self.dept)
        self.assertEqual(membership.approved_by, self.admin)

        self.invitation.refresh_from_db()
        self.assertEqual(self.invitation.status, "accepted")
        self.assertEqual(self.invitation.accepted_by, user)
        self.assertTrue(AuditEvent.objects.filter(action="invitation.accepted").exists())

    def test_the_invitation_beats_the_domain_rules(self):
        """A personal address has no company to map to, and an unclaimed
        domain would be offered a workspace. An invitation overrides both:
        the administrator already decided."""
        personal, _ = onboarding.invite(
            self.org, email="someone@gmail.com", role=self.role, inviter=self.admin
        )
        user = login.resolve_user(verified("someone@gmail.com", subject="sub-9"))
        self.assertEqual(user.organisation, self.org)
        self.assertEqual(Organisation.objects.count(), 1, "no workspace was created")

    def test_a_different_verified_address_is_not_invited(self):
        """`INVITATION_EMAIL_MISMATCH` by construction: the lookup is keyed on
        the address the provider verified, so a colleague signing in with
        their own address finds no invitation and takes the ordinary route."""
        OrganizationDomain.objects.create(
            organisation=self.org, domain="acme.io", verification_status="verified"
        )
        with self.assertRaises(login.LoginError) as caught:
            login.resolve_user(verified("other@acme.io", subject="sub-2"))
        self.assertEqual(caught.exception.code, "ACCESS_REQUEST_PENDING")
        self.invitation.refresh_from_db()
        self.assertEqual(self.invitation.status, "pending")

        with self.assertRaises(onboarding.OnboardingError) as direct:
            onboarding.accept_invitation(self.invitation, verified("other@acme.io"))
        self.assertEqual(direct.exception.code, "INVITATION_EMAIL_MISMATCH")

    def test_an_expired_invitation_opens_no_door(self):
        Invitation.objects.filter(pk=self.invitation.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        with self.assertRaises(login.LoginError) as caught:
            login.resolve_user(verified("new@acme.io"))
        # No verified domain, so they are offered a workspace like any stranger.
        self.assertEqual(caught.exception.code, "WORKSPACE_SETUP_REQUIRED")
        self.assertFalse(OrganizationMembership.objects.filter(user__email="new@acme.io").exists())

    def test_someone_already_waiting_is_let_in_by_a_later_invitation(self):
        """They signed in first and were parked on an access request; then an
        administrator invited them. The next sign-in accepts, and the stale
        request is closed rather than left for someone to approve twice."""
        OrganizationDomain.objects.create(
            organisation=self.org, domain="acme.io", verification_status="verified"
        )
        with self.assertRaises(login.LoginError):
            login.resolve_user(verified("waiting@acme.io", subject="sub-3"))
        self.assertTrue(
            AccessRequest.objects.filter(email="waiting@acme.io", status="pending").exists()
        )

        onboarding.invite(self.org, email="waiting@acme.io", role=self.role, inviter=self.admin)
        user = login.resolve_user(verified("waiting@acme.io", subject="sub-3"))

        self.assertEqual(user.organisation, self.org)
        self.assertFalse(
            AccessRequest.objects.filter(email="waiting@acme.io", status="pending").exists()
        )

    def test_a_suspended_organisation_invites_nobody_in(self):
        Organisation.objects.filter(pk=self.org.pk).update(status="suspended")
        self.assertIsNone(onboarding.open_invitation_for("new@acme.io"))


@override_settings(AUTH_V2_ENABLED=True)
class InvitationEndpointTests(APITestCase):
    def setUp(self):
        self.acme = Organisation.objects.create(name="Acme Inc")
        self.rival = Organisation.objects.create(name="Rival Ltd")
        self.acme_admin = admin_of(self.acme, "admin@acme.io")
        self.rival_admin = admin_of(self.rival, "admin@rival.io")
        self.acme_role = Role.objects.create(
            organisation=self.acme, name="CSM", slug="csm-2", permissions=[]
        )
        self.client.force_authenticate(self.acme_admin)

    def test_create_list_and_cancel(self):
        response = self.client.post(
            "/api/v1/identity/invitations/",
            {"email": "New@Acme.io", "role_id": self.acme_role.id},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["email"], "new@acme.io")
        self.assertEqual(response.data["role_name"], "CSM")
        invitation_id = response.data["id"]

        again = self.client.post(
            "/api/v1/identity/invitations/",
            {"email": "new@acme.io", "role_id": self.acme_role.id},
            format="json",
        )
        self.assertEqual(again.status_code, status.HTTP_200_OK, "re-sent, not duplicated")

        listed = self.client.get("/api/v1/identity/invitations/")
        self.assertEqual([row["id"] for row in listed.data], [invitation_id])

        cancelled = self.client.post(f"/api/v1/identity/invitations/{invitation_id}/cancel/")
        self.assertEqual(cancelled.status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.get("/api/v1/identity/invitations/").data, [])

    def test_a_missing_role_is_a_bad_request(self):
        response = self.client.post(
            "/api/v1/identity/invitations/", {"email": "new@acme.io"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["code"], "INVALID_ROLE")

    def test_one_tenants_admin_cannot_see_or_cancel_anothers(self):
        """The mandatory cross-tenant test, for invitations."""
        theirs, _ = onboarding.invite(
            self.rival,
            email="new@rival.io",
            role=Role.objects.create(organisation=self.rival, name="R", slug="r", permissions=[]),
            inviter=self.rival_admin,
        )
        self.assertEqual(self.client.get("/api/v1/identity/invitations/").data, [])
        response = self.client.post(f"/api/v1/identity/invitations/{theirs.pk}/cancel/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, "pending")

    def test_a_member_without_the_capability_is_refused(self):
        plain = User.objects.create_user(
            email="plain@acme.io", password="x", name="Plain", organisation=self.acme
        )
        self.client.force_authenticate(plain)
        response = self.client.post(
            "/api/v1/identity/invitations/",
            {"email": "new@acme.io", "role_id": self.acme_role.id},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
