"""The internal portal.

Defended: only a superuser with the `mfa` claim gets in (a tenant admin never
does, an unenrolled superuser is told to enrol); the detail payload carries
metadata and nothing from inside a tenant; suspension closes every door and
reactivation opens them; staff can move ownership when the owner cannot; every
action lands in the tenant's own audit trail.
"""

from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from core.models import AuditEvent
from services.accounts import mfa
from services.accounts.models import Organisation, User
from services.identity import ownership
from services.identity.models import AccessRequest, OrganizationDomain, OrganizationMembership

#: Everything the detail endpoint may return. A new key here is a review
#: question: is it metadata, or is it something a tenant considers theirs?
DETAIL_KEYS = {
    "id",
    "name",
    "slug",
    "status",
    "created_at",
    "owner",
    "members_active",
    "pending_requests",
    "domains",
    "plan",
    "open_invitations",
    "memberships",
    "recent_events",
}


def mfa_token_for(user):
    token = AccessToken.for_user(user)
    token["mfa"] = True
    return str(token)


class PlatformTestCase(APITestCase):
    def setUp(self):
        self.acme = Organisation.objects.create(name="Acme Inc")
        self.rival = Organisation.objects.create(name="Rival Ltd")
        self.owner = User.objects.create_user(
            email="owner@acme.io",
            password="x",
            name="Owner",
            organisation=self.acme,
            role=User.Role.ADMIN,
        )
        ownership.claim(self.owner, self.acme)
        self.csm = User.objects.create_user(
            email="csm@acme.io", password="supersecret-pw-1", name="CSM", organisation=self.acme
        )
        OrganizationDomain.objects.create(
            organisation=self.acme, domain="acme.io", verification_status="verified"
        )
        self.staff = User.objects.create_superuser(
            email="staff@revenact.io", password="supersecret-pw-1", name="Staff"
        )

    def as_staff(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {mfa_token_for(self.staff)}")


class AccessTests(PlatformTestCase):
    def test_a_tenant_admin_is_refused_even_with_a_second_factor(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {mfa_token_for(self.owner)}")
        response = self.client.get("/api/v1/platform/organisations/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_superuser_without_the_second_factor_is_told_to_enrol(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.staff)}")
        response = self.client.get("/api/v1/platform/organisations/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["detail"], "MFA_REQUIRED")

    def test_the_real_login_flow_reaches_the_portal(self):
        """End to end: password, then code, then the portal answers."""
        _, secret = mfa.begin_enrolment(self.staff)
        mfa.confirm_enrolment(self.staff, mfa._code_at(secret, mfa.current_counter()))
        first = self.client.post(
            "/api/v1/auth/login/", {"email": "staff@revenact.io", "password": "supersecret-pw-1"}
        )
        import time
        from unittest.mock import patch

        later = time.time() + 60
        with patch("services.accounts.mfa.time.time", return_value=later):
            second = self.client.post(
                "/api/v1/auth/login/mfa/",
                {
                    "mfa_token": first.data["mfa_token"],
                    "code": mfa._code_at(secret, mfa.current_counter(later)),
                },
            )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {second.data['access']}")
        self.assertEqual(
            self.client.get("/api/v1/platform/overview/").status_code, status.HTTP_200_OK
        )

    def test_anonymous_is_refused(self):
        self.assertEqual(
            self.client.get("/api/v1/platform/overview/").status_code, status.HTTP_401_UNAUTHORIZED
        )


class ReadTests(PlatformTestCase):
    def test_overview_counts(self):
        self.as_staff()
        response = self.client.get("/api/v1/platform/overview/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["organisations"]["total"], 2)
        self.assertEqual(response.data["organisations"]["active"], 2)
        self.assertEqual(response.data["members_active"], 2)
        self.assertEqual(response.data["verified_domains"], 1)
        self.assertEqual(response.data["staff"], 1)

    def test_list_filters_and_searches_by_name_or_domain(self):
        self.as_staff()
        everything = self.client.get("/api/v1/platform/organisations/").data
        self.assertEqual([o["name"] for o in everything], ["Acme Inc", "Rival Ltd"])
        acme = next(o for o in everything if o["name"] == "Acme Inc")
        self.assertEqual(acme["owner"]["email"], "owner@acme.io")
        self.assertEqual(acme["members_active"], 2)
        self.assertEqual(acme["domains"][0]["domain"], "acme.io")

        by_domain = self.client.get("/api/v1/platform/organisations/", {"q": "acme.io"}).data
        self.assertEqual([o["name"] for o in by_domain], ["Acme Inc"])

        Organisation.objects.filter(pk=self.rival.pk).update(status="suspended")
        suspended = self.client.get("/api/v1/platform/organisations/", {"status": "suspended"}).data
        self.assertEqual([o["name"] for o in suspended], ["Rival Ltd"])

    def test_detail_is_metadata_only(self):
        """The property that makes a 'manage everyone' surface safe to have."""
        self.as_staff()
        response = self.client.get(f"/api/v1/platform/organisations/{self.acme.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(set(response.data), DETAIL_KEYS)
        for forbidden in ("customers", "emails", "notes", "tickets", "calls", "attachments"):
            self.assertNotIn(forbidden, str(response.data).lower())

        members = response.data["memberships"]
        self.assertEqual(members[0]["email"], "owner@acme.io", "owner first")
        self.assertTrue(members[0]["is_owner"])
        self.assertEqual({m["email"] for m in members}, {"owner@acme.io", "csm@acme.io"})

    def test_an_unknown_organisation_is_404(self):
        self.as_staff()
        self.assertEqual(
            self.client.get("/api/v1/platform/organisations/999999/").status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_staff_list_shows_second_factor_state(self):
        self.as_staff()
        rows = self.client.get("/api/v1/platform/staff/").data
        self.assertEqual([r["email"] for r in rows], ["staff@revenact.io"])
        self.assertFalse(rows[0]["mfa_enrolled"])


class SuspendTests(PlatformTestCase):
    def test_suspension_closes_every_door_and_reactivation_opens_them(self):
        self.as_staff()
        response = self.client.post(
            f"/api/v1/platform/organisations/{self.acme.id}/status/",
            {"status": "suspended", "reason": "Non-payment"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Organisation.objects.get(pk=self.acme.pk).status, "suspended")

        # Password sign-in is refused.
        self.client.credentials()
        login = self.client.post(
            "/api/v1/auth/login/", {"email": "csm@acme.io", "password": "supersecret-pw-1"}
        )
        self.assertEqual(login.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn("not active", str(login.data))

        # An existing session holds nothing.
        from services.identity.context import capabilities_for

        self.assertEqual(capabilities_for(User.objects.get(pk=self.owner.pk)), [])

        # It is on the tenant's own record.
        event = AuditEvent.objects.get(action="platform.organisation.status")
        self.assertEqual(event.organisation, self.acme)
        self.assertEqual(event.actor, self.staff)
        self.assertEqual(event.metadata["reason"], "Non-payment")

        self.as_staff()
        self.client.post(
            f"/api/v1/platform/organisations/{self.acme.id}/status/",
            {"status": "active", "reason": "Paid"},
            format="json",
        )
        self.client.credentials()
        login = self.client.post(
            "/api/v1/auth/login/", {"email": "csm@acme.io", "password": "supersecret-pw-1"}
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK)

    def test_a_reason_is_required_and_status_is_bounded(self):
        self.as_staff()
        no_reason = self.client.post(
            f"/api/v1/platform/organisations/{self.acme.id}/status/",
            {"status": "suspended"},
            format="json",
        )
        self.assertEqual(no_reason.data["error"]["code"], "REASON_REQUIRED")
        bad = self.client.post(
            f"/api/v1/platform/organisations/{self.acme.id}/status/",
            {"status": "archived", "reason": "x"},
            format="json",
        )
        self.assertEqual(bad.data["error"]["code"], "INVALID_STATUS")
        self.assertEqual(Organisation.objects.get(pk=self.acme.pk).status, "active")


class OwnerTests(PlatformTestCase):
    def test_staff_can_move_ownership_when_the_owner_cannot(self):
        User.objects.filter(pk=self.owner.pk).update(is_active=False)
        self.as_staff()
        response = self.client.post(
            f"/api/v1/platform/organisations/{self.acme.id}/owner/",
            {"user_id": self.csm.id},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["owner"]["email"], "csm@acme.io")
        self.assertTrue(ownership.is_owner(User.objects.get(pk=self.csm.pk)))
        self.assertEqual(User.objects.get(pk=self.csm.pk).role.slug, "admin")
        event = AuditEvent.objects.get(action="organisation.owner_transferred")
        self.assertTrue(event.metadata["by_platform"])
        self.assertEqual(event.actor, self.staff)

    def test_ownership_cannot_be_given_to_someone_outside(self):
        self.as_staff()
        outsider = User.objects.create_user(
            email="x@rival.io", password="x", name="X", organisation=self.rival
        )
        response = self.client.post(
            f"/api/v1/platform/organisations/{self.acme.id}/owner/",
            {"user_id": outsider.id},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["code"], "NOT_A_MEMBER")
        self.assertTrue(ownership.is_owner(self.owner))

    def test_pending_people_are_counted_not_listed_as_members(self):
        waiting = User.objects.create_user(email="w@acme.io", password="x", name="W")
        AccessRequest.objects.create(organisation=self.acme, user=waiting, email="w@acme.io")
        self.as_staff()
        detail = self.client.get(f"/api/v1/platform/organisations/{self.acme.id}/").data
        self.assertEqual(detail["pending_requests"], 1)
        self.assertNotIn("w@acme.io", {m["email"] for m in detail["memberships"]})
        self.assertEqual(OrganizationMembership.objects.filter(user=waiting).count(), 0)
