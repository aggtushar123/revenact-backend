"""Corporate sign-in, access requests, and domain verification.

The rules being defended here are the ones whose opposite is a real incident:
an unverified domain must map nobody, a pending person must hold nothing, a
pending request must take no seat, and one tenant's administrator must never
reach another's.
"""

from unittest.mock import patch

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import AuditEvent
from services.accounts.capabilities import ALL_CAPABILITIES, Capability
from services.accounts.models import Organisation, Role, User
from services.identity import domains, onboarding
from services.identity.context import capabilities_for
from services.identity.models import (
    AccessRequest,
    Department,
    OrganizationDomain,
    OrganizationMembership,
)
from services.identity.providers import VerifiedIdentity
from services.mail.providers.base import ProviderError


def verified(email, *, subject="sub-1"):
    return VerifiedIdentity(
        provider="google", subject=subject, email=email, email_verified=True, name="New Person"
    )


class DomainMappingTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")

    def test_an_unverified_domain_maps_nobody(self):
        """Typing a domain is a claim. Only proof maps people to a tenant."""
        OrganizationDomain.objects.create(organisation=self.org, domain="acme.io")
        self.assertIsNone(domains.organisation_for_email("someone@acme.io"))

    def test_a_verified_domain_maps(self):
        OrganizationDomain.objects.create(
            organisation=self.org, domain="acme.io", verification_status="verified"
        )
        self.assertEqual(domains.organisation_for_email("someone@acme.io"), self.org)

    def test_a_personal_domain_never_maps(self):
        """Otherwise every Gmail user lands in whoever claimed gmail.com."""
        OrganizationDomain.objects.create(
            organisation=self.org, domain="gmail.com", verification_status="verified"
        )
        self.assertIsNone(domains.organisation_for_email("someone@gmail.com"))
        self.assertTrue(domains.is_personal("someone@gmail.com"))

    def test_addresses_are_matched_case_insensitively(self):
        OrganizationDomain.objects.create(
            organisation=self.org, domain="acme.io", verification_status="verified"
        )
        self.assertEqual(domains.organisation_for_email("Someone@ACME.IO"), self.org)


class DnsVerificationTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.record = OrganizationDomain.objects.create(
            organisation=self.org, domain="acme.io", verification_token="tok-123"
        )

    def answer(self, *values):
        return {"Answer": [{"data": f'"{v}"'} for v in values]}

    def test_the_published_record_promotes_the_domain(self):
        expected = domains.expected_record(self.record)
        with patch.object(domains, "http_json", return_value=self.answer(expected)):
            self.assertTrue(domains.verify(self.record))

        self.record.refresh_from_db()
        self.assertEqual(self.record.verification_status, "verified")
        self.assertIsNotNone(self.record.verified_at)

    def test_a_wrong_record_does_not_promote(self):
        with patch.object(
            domains,
            "http_json",
            return_value=self.answer("revenact-site-verification=someone-else"),
        ):
            self.assertFalse(domains.verify(self.record))
        self.record.refresh_from_db()
        self.assertEqual(self.record.verification_status, "pending")

    def test_no_record_at_all_does_not_promote(self):
        with patch.object(domains, "http_json", return_value={}):
            self.assertFalse(domains.verify(self.record))

    def test_an_unreachable_resolver_raises_rather_than_reporting_a_mismatch(self):
        """An outage must not look like a failed verification."""
        with patch.object(domains, "http_json", side_effect=ProviderError("no route")):
            with self.assertRaises(ProviderError):
                domains.verify(self.record)

    def test_only_allow_listed_resolvers_are_called(self):
        calls = {}

        def fake(method, url, **kwargs):
            calls["allowed"] = kwargs.get("allowed_hosts")
            return {}

        with patch.object(domains, "http_json", side_effect=fake):
            domains.lookup_txt("acme.io")
        self.assertEqual(calls["allowed"], domains.RESOLVER_HOSTS)


class RequestAccessTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        OrganizationDomain.objects.create(
            organisation=self.org, domain="acme.io", verification_status="verified"
        )

    def test_a_first_corporate_sign_in_creates_a_person_who_holds_nothing(self):
        user, access_request = onboarding.request_access(verified("new@acme.io"))

        self.assertIsNotNone(access_request)
        self.assertEqual(access_request.status, "pending")
        # The whole point: authenticated, and holding nothing at all.
        self.assertIsNone(user.organisation)
        self.assertFalse(OrganizationMembership.objects.filter(user=user).exists())
        self.assertEqual(capabilities_for(user), [])

    def test_a_pending_person_cannot_use_a_password(self):
        user, _ = onboarding.request_access(verified("new@acme.io"))
        self.assertFalse(user.has_usable_password())

    def test_a_pending_request_consumes_no_seat(self):
        """Load-bearing for billing: twenty people may wait on one free seat."""
        for i in range(20):
            onboarding.request_access(verified(f"person{i}@acme.io", subject=f"sub-{i}"))

        self.assertEqual(AccessRequest.objects.filter(status="pending").count(), 20)
        self.assertEqual(OrganizationMembership.objects.count(), 0)

    def test_signing_in_again_while_waiting_does_not_queue_a_second_request(self):
        onboarding.request_access(verified("new@acme.io"))
        onboarding.request_access(verified("new@acme.io"))
        self.assertEqual(AccessRequest.objects.count(), 1)

    def test_an_unverified_domain_is_refused(self):
        OrganizationDomain.objects.all().update(verification_status="pending")
        with self.assertRaises(onboarding.OnboardingError) as caught:
            onboarding.request_access(verified("new@acme.io"))
        self.assertEqual(caught.exception.code, "DOMAIN_NOT_VERIFIED")
        self.assertEqual(User.objects.filter(email="new@acme.io").count(), 0)

    def test_a_personal_address_is_refused(self):
        with self.assertRaises(onboarding.OnboardingError) as caught:
            onboarding.request_access(verified("someone@gmail.com"))
        self.assertEqual(caught.exception.code, "PERSONAL_EMAIL_NOT_SUPPORTED")

    def test_a_suspended_tenant_accepts_nobody(self):
        Organisation.objects.filter(pk=self.org.pk).update(status="suspended")
        with self.assertRaises(onboarding.OnboardingError) as caught:
            onboarding.request_access(verified("new@acme.io"))
        self.assertEqual(caught.exception.code, "ORGANIZATION_SUSPENDED")

    def test_an_existing_member_needs_no_request(self):
        member = User.objects.create_user(
            email="carl@acme.io", password="x", name="Carl", organisation=self.org
        )
        user, access_request = onboarding.request_access(verified("carl@acme.io"))
        self.assertEqual(user, member)
        self.assertIsNone(access_request)

    def test_the_request_is_audited(self):
        onboarding.request_access(verified("new@acme.io"))
        self.assertTrue(AuditEvent.objects.filter(action="access_request.created").exists())


class ApprovalTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        OrganizationDomain.objects.create(
            organisation=self.org, domain="acme.io", verification_status="verified"
        )
        self.admin_role = Role.objects.create(
            organisation=self.org, name="Admin", slug="admin-2", permissions=list(ALL_CAPABILITIES)
        )
        self.member_role = Role.objects.create(
            organisation=self.org, name="Member", slug="member", permissions=[]
        )
        self.admin = User.objects.create_user(
            email="admin@acme.io",
            password="x",
            name="Admin",
            organisation=self.org,
            role=self.admin_role,
        )
        self.department = Department.objects.create(organisation=self.org, name="Engineering")
        self.user, self.request_row = onboarding.request_access(verified("new@acme.io"))

    def test_approval_grants_a_membership_and_both_columns(self):
        """Phase 3's guard requires the column and the membership to agree."""
        onboarding.approve(
            self.request_row,
            reviewer=self.admin,
            role=self.member_role,
            department=self.department,
        )

        membership = OrganizationMembership.objects.get(user=self.user)
        self.assertEqual(membership.status, "active")
        self.assertEqual(membership.role_id, self.member_role.id)
        self.assertEqual(membership.department_id, self.department.id)

        self.user.refresh_from_db()
        self.assertEqual(self.user.organisation_id, self.org.id)
        self.assertEqual(self.user.role_id, self.member_role.id)

        self.request_row.refresh_from_db()
        self.assertEqual(self.request_row.status, "approved")

    def test_an_approved_person_can_then_act(self):
        onboarding.approve(self.request_row, reviewer=self.admin, role=self.admin_role)
        self.assertTrue(User.objects.get(pk=self.user.pk).has_capability(Capability.MANAGE_USERS))

    def test_a_role_from_another_tenant_is_refused(self):
        rival = Organisation.objects.create(name="Rival Ltd")
        rival_role = Role.objects.create(
            organisation=rival, name="Admin", slug="admin-2", permissions=[]
        )
        with self.assertRaises(onboarding.OnboardingError) as caught:
            onboarding.approve(self.request_row, reviewer=self.admin, role=rival_role)
        self.assertEqual(caught.exception.code, "INVALID_ROLE")

    def test_an_admin_cannot_grant_more_than_they_hold(self):
        """Otherwise manage_users is quietly a route to full administration."""
        limited_role = Role.objects.create(
            organisation=self.org,
            name="User manager",
            slug="user-manager",
            permissions=[Capability.MANAGE_USERS],
        )
        limited_admin = User.objects.create_user(
            email="limited@acme.io",
            password="x",
            name="Limited",
            organisation=self.org,
            role=limited_role,
        )
        with self.assertRaises(onboarding.OnboardingError) as caught:
            onboarding.approve(self.request_row, reviewer=limited_admin, role=self.admin_role)
        self.assertEqual(caught.exception.code, "INSUFFICIENT_PERMISSION")

    def test_a_decided_request_cannot_be_decided_again(self):
        onboarding.approve(self.request_row, reviewer=self.admin, role=self.member_role)
        self.request_row.refresh_from_db()
        with self.assertRaises(onboarding.OnboardingError) as caught:
            onboarding.approve(self.request_row, reviewer=self.admin, role=self.member_role)
        self.assertEqual(caught.exception.code, "ACCESS_REQUEST_DECIDED")

    def test_rejection_leaves_them_with_an_account_and_no_membership(self):
        onboarding.reject(self.request_row, reviewer=self.admin, reason="Not a colleague")
        self.request_row.refresh_from_db()

        self.assertEqual(self.request_row.status, "rejected")
        self.assertEqual(self.request_row.rejection_reason, "Not a colleague")
        self.assertFalse(OrganizationMembership.objects.filter(user=self.user).exists())
        self.assertEqual(capabilities_for(User.objects.get(pk=self.user.pk)), [])

    def test_both_decisions_are_audited(self):
        onboarding.approve(self.request_row, reviewer=self.admin, role=self.member_role)
        self.assertTrue(AuditEvent.objects.filter(action="access_request.approved").exists())


class CrossTenantTests(APITestCase):
    """The mandatory test: one tenant's administrator must never reach another's.

    There is no organisation id in any of these paths, so this asserts the
    stronger property that there is nothing to tamper with in the first place.
    """

    def setUp(self):
        self.acme = Organisation.objects.create(name="Acme Inc")
        self.rival = Organisation.objects.create(name="Rival Ltd")

        def admin_of(org, email):
            role = Role.objects.create(
                organisation=org, name="Admin", slug="admin-2", permissions=list(ALL_CAPABILITIES)
            )
            return User.objects.create_user(
                email=email, password="x", name="Admin", organisation=org, role=role
            )

        self.acme_admin = admin_of(self.acme, "admin@acme.io")
        self.rival_admin = admin_of(self.rival, "admin@rival.io")

        OrganizationDomain.objects.create(
            organisation=self.rival, domain="rival.io", verification_status="verified"
        )
        self.rival_user, self.rival_request = onboarding.request_access(
            verified("newcomer@rival.io")
        )
        self.rival_domain = OrganizationDomain.objects.get(domain="rival.io")

    def test_an_admin_sees_only_their_own_access_requests(self):
        self.client.force_authenticate(self.acme_admin)
        response = self.client.get("/api/v1/identity/access-requests/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

        self.client.force_authenticate(self.rival_admin)
        response = self.client.get("/api/v1/identity/access-requests/")
        self.assertEqual(len(response.data), 1)

    def test_an_admin_cannot_approve_another_tenants_request(self):
        """Even knowing the id. This is the §70 test."""
        self.client.force_authenticate(self.acme_admin)
        response = self.client.post(
            f"/api/v1/identity/access-requests/{self.rival_request.pk}/approve/",
            {"role_id": Role.objects.filter(organisation=self.acme).first().pk},
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        self.rival_request.refresh_from_db()
        self.assertEqual(self.rival_request.status, "pending", "untouched")
        self.assertFalse(OrganizationMembership.objects.filter(user=self.rival_user).exists())

    def test_an_admin_cannot_verify_another_tenants_domain(self):
        self.client.force_authenticate(self.acme_admin)
        response = self.client.post(f"/api/v1/identity/domains/{self.rival_domain.pk}/verify/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_an_admin_sees_only_their_own_domains(self):
        self.client.force_authenticate(self.acme_admin)
        response = self.client.get("/api/v1/identity/domains/")
        self.assertEqual(response.data, [])

    def test_a_domain_cannot_be_claimed_twice(self):
        self.client.force_authenticate(self.acme_admin)
        response = self.client.post("/api/v1/identity/domains/", {"domain": "rival.io"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["code"], "DOMAIN_ALREADY_CLAIMED")

    def test_a_member_without_the_capability_is_refused(self):
        plain = User.objects.create_user(
            email="plain@acme.io", password="x", name="Plain", organisation=self.acme
        )
        self.client.force_authenticate(plain)
        self.assertEqual(
            self.client.get("/api/v1/identity/access-requests/").status_code,
            status.HTTP_403_FORBIDDEN,
        )


class DomainEndpointTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        role = Role.objects.create(
            organisation=self.org, name="Admin", slug="admin-2", permissions=list(ALL_CAPABILITIES)
        )
        self.admin = User.objects.create_user(
            email="admin@acme.io", password="x", name="Admin", organisation=self.org, role=role
        )
        self.client.force_authenticate(self.admin)

    def test_adding_a_domain_returns_the_record_to_publish(self):
        response = self.client.post("/api/v1/identity/domains/", {"domain": "Acme.IO"})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["domain"], "acme.io", "normalised")
        self.assertEqual(response.data["verification_status"], "pending")

        record = response.data["dns_record"]
        self.assertEqual(record["type"], "TXT")
        self.assertEqual(record["name"], "acme.io")
        self.assertTrue(record["value"].startswith(domains.TXT_PREFIX))

    def test_a_personal_domain_cannot_be_claimed(self):
        response = self.client.post("/api/v1/identity/domains/", {"domain": "gmail.com"})
        self.assertEqual(response.data["error"]["code"], "PERSONAL_DOMAIN_NOT_ALLOWED")

    def test_verification_reports_a_lookup_failure_separately_from_a_mismatch(self):
        self.client.post("/api/v1/identity/domains/", {"domain": "acme.io"})
        record = OrganizationDomain.objects.get(domain="acme.io")

        with patch.object(domains, "http_json", side_effect=ProviderError("no route")):
            response = self.client.post(f"/api/v1/identity/domains/{record.pk}/verify/")
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["error"]["code"], "DNS_LOOKUP_FAILED")

    def test_publishing_the_record_verifies_the_domain(self):
        self.client.post("/api/v1/identity/domains/", {"domain": "acme.io"})
        record = OrganizationDomain.objects.get(domain="acme.io")
        answer = {"Answer": [{"data": f'"{domains.expected_record(record)}"'}]}

        with patch.object(domains, "http_json", return_value=answer):
            response = self.client.post(f"/api/v1/identity/domains/{record.pk}/verify/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["verification_status"], "verified")
        self.assertTrue(AuditEvent.objects.filter(action="domain.verified").exists())
