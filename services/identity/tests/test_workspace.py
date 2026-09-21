"""Self-serve workspaces, and why an employee starting one ahead of their
company is harmless.

The rules under test: a self-made workspace holds only its founder; its domain
is a claim that routes nobody; the next person from that domain is pointed at
the existing workspace rather than handed a second one; and proof of DNS
control takes the domain from every claim that came before it.
"""

from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import AuditEvent
from services.accounts.capabilities import ALL_CAPABILITIES
from services.accounts.models import Organisation, User
from services.identity import domains, login, onboarding
from services.identity.context import capabilities_for
from services.identity.models import (
    AccessRequest,
    Identity,
    OrganizationDomain,
    OrganizationMembership,
)
from services.identity.providers import VerifiedIdentity

from .test_login import StubProvider


def verified(email, *, subject="sub-1", name="Priya Founder"):
    return VerifiedIdentity(
        provider="google", subject=subject, email=email, email_verified=True, name=name
    )


class RoutingTests(TestCase):
    """`route_for_email` is the one rule every newcomer path shares."""

    def test_a_personal_address_routes_nowhere(self):
        self.assertEqual(domains.route_for_email("me@gmail.com").kind, "personal")

    def test_an_unclaimed_domain(self):
        self.assertEqual(domains.route_for_email("me@nobody.io").kind, "unclaimed")

    def test_a_single_claim_routes_to_its_holder(self):
        org = Organisation.objects.create(name="Acme")
        OrganizationDomain.objects.create(organisation=org, domain="acme.io")
        route = domains.route_for_email("me@acme.io")
        self.assertEqual((route.kind, route.organisation), ("claimed", org))

    def test_proof_beats_claims(self):
        claimant = Organisation.objects.create(name="Shadow")
        owner = Organisation.objects.create(name="Acme")
        OrganizationDomain.objects.create(organisation=claimant, domain="acme.io")
        OrganizationDomain.objects.create(
            organisation=owner, domain="acme.io", verification_status="verified"
        )
        route = domains.route_for_email("me@acme.io")
        self.assertEqual((route.kind, route.organisation), ("verified", owner))

    def test_two_claims_and_no_proof_is_ambiguous(self):
        for name in ("One", "Two"):
            OrganizationDomain.objects.create(
                organisation=Organisation.objects.create(name=name), domain="acme.io"
            )
        self.assertEqual(domains.route_for_email("me@acme.io").kind, "ambiguous")

    def test_a_revoked_claim_no_longer_counts(self):
        org = Organisation.objects.create(name="Acme")
        OrganizationDomain.objects.create(
            organisation=org, domain="acme.io", verification_status="revoked"
        )
        self.assertEqual(domains.route_for_email("me@acme.io").kind, "unclaimed")


class CreateWorkspaceTests(TestCase):
    def test_the_founder_gets_a_whole_workspace_and_nothing_else_exists(self):
        user = onboarding.create_workspace(
            verified("priya@newco.io"), organisation_name="Newco", name="Priya"
        )

        organisation = user.organisation
        self.assertEqual(organisation.name, "Newco")
        self.assertEqual(organisation.status, Organisation.Status.ACTIVE)
        self.assertEqual(user.role.slug, "admin")
        self.assertEqual(set(capabilities_for(user)), set(ALL_CAPABILITIES))

        membership = OrganizationMembership.objects.get(user=user)
        self.assertEqual(membership.organisation, organisation)
        self.assertEqual(membership.status, "active")
        self.assertEqual(
            OrganizationMembership.objects.filter(organisation=organisation).count(), 1
        )

        domain = OrganizationDomain.objects.get(organisation=organisation)
        self.assertEqual(domain.domain, "newco.io")
        self.assertTrue(domain.is_primary)
        self.assertEqual(domain.verification_status, "pending")
        self.assertTrue(domain.verification_token)

        identity = Identity.objects.get(user=user)
        self.assertEqual(identity.provider_user_id, "sub-1")
        self.assertFalse(user.has_usable_password())

    def test_the_name_falls_back_to_what_the_provider_said(self):
        user = onboarding.create_workspace(verified("priya@newco.io"), organisation_name="Newco")
        self.assertEqual(user.name, "Priya Founder")

    def test_creation_is_audited_as_a_signup_through_the_provider(self):
        onboarding.create_workspace(verified("priya@newco.io"), organisation_name="Newco")
        event = AuditEvent.objects.get(action="auth.signup")
        self.assertEqual(event.metadata["via"], "oauth")
        self.assertEqual(event.metadata["organisation"], "Newco")
        self.assertTrue(AuditEvent.objects.filter(action="domain.added").exists())

    def test_a_workspace_needs_a_name(self):
        with self.assertRaises(onboarding.OnboardingError) as caught:
            onboarding.create_workspace(verified("priya@newco.io"), organisation_name="   ")
        self.assertEqual(caught.exception.code, "INVALID_ORGANISATION_NAME")
        self.assertEqual(Organisation.objects.count(), 0)

    def test_a_personal_address_cannot_found_a_workspace(self):
        """Or gmail.com would belong to the first Gmail user to try."""
        with self.assertRaises(onboarding.OnboardingError) as caught:
            onboarding.create_workspace(verified("priya@gmail.com"), organisation_name="Mine")
        self.assertEqual(caught.exception.code, "PERSONAL_EMAIL_NOT_SUPPORTED")
        self.assertEqual(Organisation.objects.count(), 0)

    def test_the_second_founder_from_one_domain_is_refused(self):
        """Two colleagues both handed a setup code before either submitted:
        the second is sent back to sign in, where they are routed to the
        first's workspace instead of given their own."""
        onboarding.create_workspace(verified("priya@newco.io"), organisation_name="Newco")
        with self.assertRaises(onboarding.OnboardingError) as caught:
            onboarding.create_workspace(
                verified("raj@newco.io", subject="sub-2"), organisation_name="Newco Again"
            )
        self.assertEqual(caught.exception.code, "WORKSPACE_CLAIMED")
        self.assertEqual(Organisation.objects.count(), 1)
        self.assertEqual(User.objects.count(), 1)

    def test_an_address_that_already_has_an_account_cannot_found_a_workspace(self):
        org = Organisation.objects.create(name="Old")
        User.objects.create_user(email="priya@newco.io", password="x", name="P", organisation=org)
        with self.assertRaises(onboarding.OnboardingError) as caught:
            onboarding.create_workspace(verified("priya@newco.io"), organisation_name="Newco")
        self.assertEqual(caught.exception.code, "WORKSPACE_CLAIMED")


class ColleagueAfterAClaimTests(TestCase):
    """What the user asked: an employee starts 'Acme' before Acme does."""

    def setUp(self):
        self.founder = onboarding.create_workspace(
            verified("employee@acme.io"), organisation_name="Acme (started early)"
        )
        self.shadow = self.founder.organisation

    def test_a_colleague_is_pointed_at_the_existing_workspace_not_given_a_second(self):
        with self.assertRaises(login.LoginError) as caught:
            login.resolve_user(verified("colleague@acme.io", subject="sub-2"))

        self.assertEqual(caught.exception.code, "ACCESS_REQUEST_PENDING")
        self.assertEqual(Organisation.objects.count(), 1)
        request = AccessRequest.objects.get(email="colleague@acme.io")
        self.assertEqual(request.organisation, self.shadow)
        self.assertEqual(request.status, "pending")

    def test_a_colleague_holds_nothing_until_the_founder_decides(self):
        """An unverified claim never auto-joins anyone: the founder must say
        yes explicitly, which is an administrator's decision, not domain
        trust."""
        with self.assertRaises(login.LoginError):
            login.resolve_user(verified("colleague@acme.io", subject="sub-2"))
        colleague = User.objects.get(email="colleague@acme.io")
        self.assertIsNone(colleague.organisation)
        self.assertEqual(capabilities_for(colleague), [])
        self.assertFalse(OrganizationMembership.objects.filter(user=colleague).exists())

    def test_signing_in_again_while_waiting_does_not_open_an_empty_application(self):
        """The first attempt linked their identity. The second must not turn
        that into a session: they still hold nothing, so they still wait."""
        with self.assertRaises(login.LoginError):
            login.resolve_user(verified("colleague@acme.io", subject="sub-2"))
        with self.assertRaises(login.LoginError) as caught:
            login.resolve_user(verified("colleague@acme.io", subject="sub-2"))
        self.assertEqual(caught.exception.code, "ACCESS_REQUEST_PENDING")
        self.assertEqual(AccessRequest.objects.filter(email="colleague@acme.io").count(), 1)

    def test_the_company_takes_the_domain_by_proving_it(self):
        """Acme's IT publishes the TXT record under Acme's own workspace. The
        employee keeps an empty room; the domain, and every future sign-in
        from it, goes to Acme."""
        acme = Organisation.objects.create(name="Acme Inc")
        record = OrganizationDomain.objects.create(
            organisation=acme, domain="acme.io", verification_token="acme-token"
        )
        with patch.object(
            domains, "lookup_txt", return_value=["revenact-site-verification=acme-token"]
        ):
            self.assertTrue(domains.verify(record))

        record.refresh_from_db()
        self.assertEqual(record.verification_status, "verified")
        shadow_claim = OrganizationDomain.objects.get(organisation=self.shadow)
        self.assertEqual(shadow_claim.verification_status, "revoked")

        route = domains.route_for_email("newhire@acme.io")
        self.assertEqual((route.kind, route.organisation), ("verified", acme))

        # Somebody lost something, so it is on the record.
        event = AuditEvent.objects.get(action="domain.superseded")
        self.assertEqual(event.organisation, self.shadow)
        self.assertEqual(event.metadata["verified_by"], acme.id)

        # The founder still has their workspace; it just cannot grow by domain.
        self.assertEqual(Organisation.objects.filter(pk=self.shadow.pk).count(), 1)
        self.assertEqual(set(capabilities_for(self.founder)), set(ALL_CAPABILITIES))

    def test_a_pending_colleague_moves_to_the_company_once_it_verifies(self):
        """Their earlier request sits with the shadow workspace; a fresh
        sign-in after verification raises one with the real tenant."""
        with self.assertRaises(login.LoginError):
            login.resolve_user(verified("colleague@acme.io", subject="sub-2"))

        acme = Organisation.objects.create(name="Acme Inc")
        record = OrganizationDomain.objects.create(
            organisation=acme, domain="acme.io", verification_token="acme-token"
        )
        with patch.object(
            domains, "lookup_txt", return_value=["revenact-site-verification=acme-token"]
        ):
            domains.verify(record)

        with self.assertRaises(login.LoginError) as caught:
            login.resolve_user(verified("colleague@acme.io", subject="sub-2"))
        self.assertEqual(caught.exception.code, "ACCESS_REQUEST_PENDING")
        self.assertTrue(
            AccessRequest.objects.filter(
                email="colleague@acme.io", organisation=acme, status="pending"
            ).exists()
        )


class SetupCodeTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_a_code_can_be_read_without_being_spent_and_then_spent_once(self):
        code = login.issue_setup(verified("priya@newco.io"))

        self.assertEqual(login.peek_setup(code).email, "priya@newco.io")
        self.assertEqual(login.peek_setup(code).email, "priya@newco.io")
        self.assertEqual(login.redeem_setup(code).email, "priya@newco.io")
        with self.assertRaises(login.LoginError) as caught:
            login.redeem_setup(code)
        self.assertEqual(caught.exception.code, "INVALID_SETUP")

    def test_an_unknown_code_is_refused(self):
        with self.assertRaises(login.LoginError):
            login.peek_setup("nope")


@override_settings(AUTH_V2_ENABLED=True, FRONTEND_URL="http://localhost:5173")
class WorkspaceEndpointTests(APITestCase):
    def setUp(self):
        cache.clear()

    def _setup_code(self, email="priya@newco.io"):
        stub = StubProvider(identity=verified(email))
        with patch.object(login.providers, "get", return_value=stub):
            self.client.post("/api/v1/auth/oauth/google/start/")
            response = self.client.get(
                "/api/v1/auth/oauth/google/callback/",
                {"code": "abc", "state": stub.seen["state"]},
            )
        return response["Location"].split("setup=")[1]

    def test_preview_shows_who_is_setting_up_without_spending_the_code(self):
        code = self._setup_code()
        response = self.client.post("/api/v1/auth/oauth/workspace/preview/", {"setup": code})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["email"], "priya@newco.io")
        self.assertEqual(response.data["domain"], "newco.io")
        self.assertEqual(response.data["suggested_organisation_name"], "Newco")

        again = self.client.post("/api/v1/auth/oauth/workspace/preview/", {"setup": code})
        self.assertEqual(again.status_code, status.HTTP_200_OK)

    def test_create_returns_a_session_and_spends_the_code(self):
        code = self._setup_code()
        response = self.client.post(
            "/api/v1/auth/oauth/workspace/",
            {"setup": code, "organisation_name": "Newco", "name": "Priya"},
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(response.data["user"]["organisation"]["name"], "Newco")
        self.assertEqual(response.data["user"]["role"], "admin")
        self.assertFalse(response.data["user"]["has_password"])

        replay = self.client.post(
            "/api/v1/auth/oauth/workspace/", {"setup": code, "organisation_name": "Newco"}
        )
        self.assertEqual(replay.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(replay.data["error"]["code"], "INVALID_SETUP")

    def test_a_claimed_domain_is_a_conflict(self):
        first = self._setup_code("priya@newco.io")
        second = self._setup_code("raj@newco.io")
        self.client.post(
            "/api/v1/auth/oauth/workspace/", {"setup": first, "organisation_name": "Newco"}
        )
        response = self.client.post(
            "/api/v1/auth/oauth/workspace/", {"setup": second, "organisation_name": "Newco 2"}
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"]["code"], "WORKSPACE_CLAIMED")
        self.assertEqual(Organisation.objects.count(), 1)

    def test_a_blank_name_is_a_bad_request(self):
        code = self._setup_code()
        response = self.client.post(
            "/api/v1/auth/oauth/workspace/", {"setup": code, "organisation_name": ""}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["code"], "INVALID_ORGANISATION_NAME")

    @override_settings(AUTH_V2_ENABLED=False)
    def test_nothing_works_with_the_flag_off(self):
        response = self.client.post(
            "/api/v1/auth/oauth/workspace/", {"setup": "x", "organisation_name": "Newco"}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["code"], "PROVIDER_NOT_AVAILABLE")


class ProfileFlagsTests(APITestCase):
    """The two small things the frontend's first run and Account settings need."""

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme")
        self.user = User.objects.create_user(
            email="alice@acme.io", password="supersecret-pw-1", name="Alice", organisation=self.org
        )
        self.client.force_authenticate(self.user)

    def test_the_tour_is_stamped_by_the_server_and_can_be_cleared(self):
        response = self.client.patch("/api/v1/auth/me/", {"tour_completed": True}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(response.data["tour_completed_at"])

        response = self.client.patch("/api/v1/auth/me/", {"tour_completed": False}, format="json")
        self.assertIsNone(response.data["tour_completed_at"])

    def test_password_presence_and_providers_are_reported(self):
        Identity.objects.create(
            user=self.user, provider="google", provider_user_id="g-1", email="alice@acme.io"
        )
        response = self.client.get("/api/v1/auth/me/")
        self.assertTrue(response.data["has_password"])
        self.assertEqual(response.data["sign_in_providers"], ["google"])

    def test_a_provider_only_person_has_no_password(self):
        founder = onboarding.create_workspace(verified("priya@newco.io"), organisation_name="Newco")
        self.client.force_authenticate(founder)
        response = self.client.get("/api/v1/auth/me/")
        self.assertFalse(response.data["has_password"])
