"""Sign-in with an external provider.

The provider itself is stubbed: these tests are about what this application
does with a verified identity, not about whether Google's servers work. Token
verification has its own tests in `test_token_verification.py`.
"""

from unittest.mock import patch

from django.core import signing
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import AuditEvent
from services.accounts.models import Organisation, User
from services.identity import login
from services.identity.models import Identity
from services.identity.providers import ProviderError, VerifiedIdentity


def verified(email="alice@acme.io", *, subject="g-1", provider="google", email_verified=True):
    return VerifiedIdentity(
        provider=provider,
        subject=subject,
        email=email,
        email_verified=email_verified,
        name="Alice Example",
    )


class StubProvider:
    key = "google"
    label = "Google"

    def __init__(self, identity=None, error=None):
        self.identity = identity or verified()
        self.error = error
        self.seen = {}

    def is_configured(self):
        return True

    def authorize_url(self, *, state, nonce, redirect_uri):
        self.seen = {"state": state, "nonce": nonce, "redirect_uri": redirect_uri}
        return f"https://provider.example/auth?state={state}&nonce={nonce}"

    def verify_callback(self, *, code, redirect_uri, nonce):
        self.seen["callback_nonce"] = nonce
        if self.error:
            raise self.error
        return self.identity


@override_settings(AUTH_V2_ENABLED=True, FRONTEND_URL="http://localhost:5173")
class ResolveUserTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io", password="x", name="Alice", organisation=self.org
        )

    def test_a_known_identity_signs_its_owner_in(self):
        Identity.objects.create(
            user=self.user, provider="google", provider_user_id="g-1", email="alice@acme.io"
        )
        self.assertEqual(login.resolve_user(verified()), self.user)

    def test_a_first_sign_in_links_the_identity_to_the_matching_account(self):
        resolved = login.resolve_user(verified())

        self.assertEqual(resolved, self.user)
        identity = Identity.objects.get(user=self.user)
        self.assertEqual(identity.provider_user_id, "g-1")
        self.assertTrue(identity.email_verified)

    def test_linking_is_refused_when_the_provider_has_not_verified_the_address(self):
        """Otherwise anyone who can assert an address takes over that account."""
        with self.assertRaises(login.LoginError) as caught:
            login.resolve_user(verified(email_verified=False))
        self.assertEqual(caught.exception.code, "EMAIL_NOT_VERIFIED")
        self.assertFalse(Identity.objects.exists())

    def test_an_address_from_an_unclaimed_domain_creates_nothing(self):
        """Phase 2 answered `NO_ACCOUNT` here. Phase 4 says something more
        useful: nobody has proved they own that domain, so there is no tenant
        to ask to join. Either way nothing is created — see
        `test_onboarding.py` for the case where the domain *is* verified and a
        request is raised."""
        with self.assertRaises(login.LoginError) as caught:
            login.resolve_user(verified(email="stranger@elsewhere.com", subject="g-9"))
        self.assertEqual(caught.exception.code, "DOMAIN_NOT_VERIFIED")
        self.assertEqual(User.objects.filter(email="stranger@elsewhere.com").count(), 0)

    def test_a_deactivated_account_cannot_sign_in(self):
        Identity.objects.create(
            user=self.user, provider="google", provider_user_id="g-1", email="alice@acme.io"
        )
        User.objects.filter(pk=self.user.pk).update(is_active=False)

        with self.assertRaises(login.LoginError) as caught:
            login.resolve_user(verified())
        self.assertEqual(caught.exception.code, "ACCOUNT_DISABLED")

    def test_the_subject_wins_over_the_address(self):
        """Someone who inherits a departed colleague's address must not inherit
        their account: the provider's subject is the identity, not the email."""
        departed = User.objects.create_user(
            email="old@acme.io", password="x", name="Departed", organisation=self.org
        )
        Identity.objects.create(
            user=departed, provider="google", provider_user_id="g-old", email="old@acme.io"
        )
        # The address has since been reassigned to Alice by the IT department.
        resolved = login.resolve_user(verified(email="old@acme.io", subject="g-old"))
        self.assertEqual(resolved, departed, "matched on subject, not on the address")

    def test_the_stored_address_follows_a_rename(self):
        Identity.objects.create(
            user=self.user, provider="google", provider_user_id="g-1", email="alice@acme.io"
        )
        login.resolve_user(verified(email="alice.example@acme.io"))
        self.assertEqual(Identity.objects.get().email, "alice.example@acme.io")


@override_settings(AUTH_V2_ENABLED=True, FRONTEND_URL="http://localhost:5173")
class StateAndNonceTests(TestCase):
    def test_state_is_bound_to_the_provider_that_minted_it(self):
        """A state minted for Google must not be redeemable at Microsoft."""
        state = signing.dumps({"p": "google", "n": "abc"}, salt=login.STATE_SALT)
        with self.assertRaises(login.LoginError) as caught:
            login._read_state("microsoft", state)
        self.assertEqual(caught.exception.code, "INVALID_STATE")

    def test_a_tampered_state_is_refused(self):
        with self.assertRaises(login.LoginError) as caught:
            login._read_state("google", "not-a-real-state")
        self.assertEqual(caught.exception.code, "INVALID_STATE")

    def test_an_expired_state_is_refused(self):
        state = signing.dumps({"p": "google", "n": "abc"}, salt=login.STATE_SALT)
        with patch.object(login.signing, "loads", side_effect=signing.SignatureExpired("old")):
            with self.assertRaises(login.LoginError) as caught:
                login._read_state("google", state)
        self.assertEqual(caught.exception.code, "STATE_EXPIRED")

    def test_the_nonce_from_start_is_the_one_handed_to_the_provider(self):
        """The nonce must survive the round trip, or replay protection is a
        no-op that still appears to work.

        Whether the person then resolves to an account is a separate concern,
        so this asserts only the nonce and tolerates the refusal after it.
        """
        stub = StubProvider()
        with patch.object(login.providers, "get", return_value=stub):
            login.start("google")
            issued_nonce = stub.seen["nonce"]
            try:
                login.complete("google", code="c", state=stub.seen["state"])
            except login.LoginError:
                pass

        self.assertTrue(issued_nonce)
        self.assertEqual(stub.seen["callback_nonce"], issued_nonce)


@override_settings(AUTH_V2_ENABLED=True, FRONTEND_URL="http://localhost:5173")
class HandoffTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io", password="x", name="Alice", organisation=self.org
        )

    def test_a_handoff_yields_one_session(self):
        session = login.redeem_handoff(login.issue_handoff(self.user))
        self.assertEqual(session["user"], self.user)
        self.assertTrue(session["access"])
        self.assertTrue(session["refresh"])

    def test_a_handoff_cannot_be_used_twice(self):
        """It rides in a URL, so it must be worthless the moment it is spent."""
        handoff = login.issue_handoff(self.user)
        login.redeem_handoff(handoff)

        with self.assertRaises(login.LoginError) as caught:
            login.redeem_handoff(handoff)
        self.assertEqual(caught.exception.code, "INVALID_HANDOFF")

    def test_an_invented_handoff_is_refused(self):
        with self.assertRaises(login.LoginError) as caught:
            login.redeem_handoff("made-up")
        self.assertEqual(caught.exception.code, "INVALID_HANDOFF")

    def test_a_handoff_stops_working_if_the_account_is_disabled_meanwhile(self):
        handoff = login.issue_handoff(self.user)
        User.objects.filter(pk=self.user.pk).update(is_active=False)

        with self.assertRaises(login.LoginError) as caught:
            login.redeem_handoff(handoff)
        self.assertEqual(caught.exception.code, "ACCOUNT_DISABLED")


@override_settings(AUTH_V2_ENABLED=True, FRONTEND_URL="http://localhost:5173")
class EndpointTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io", password="x", name="Alice", organisation=self.org
        )

    def test_the_provider_list_is_empty_when_nothing_is_configured(self):
        response = self.client.get("/api/v1/auth/oauth/providers/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["providers"], [])

    def test_starting_a_sign_in_returns_the_provider_url(self):
        stub = StubProvider()
        with patch.object(login.providers, "get", return_value=stub):
            response = self.client.post("/api/v1/auth/oauth/google/start/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("https://provider.example/auth", response.data["authorize_url"])

    def test_a_full_round_trip_signs_someone_in(self):
        stub = StubProvider()
        with patch.object(login.providers, "get", return_value=stub):
            self.client.post("/api/v1/auth/oauth/google/start/")
            callback = self.client.get(
                "/api/v1/auth/oauth/google/callback/",
                {"code": "abc", "state": stub.seen["state"]},
            )

            self.assertEqual(callback.status_code, status.HTTP_302_FOUND)
            self.assertIn("handoff=", callback["Location"])
            # The token must never ride in the redirect.
            self.assertNotIn("access", callback["Location"])

            handoff = callback["Location"].split("handoff=")[1].split("&")[0]
            exchange = self.client.post("/api/v1/auth/oauth/exchange/", {"handoff": handoff})

        self.assertEqual(exchange.status_code, status.HTTP_200_OK)
        self.assertEqual(exchange.data["user"]["email"], "alice@acme.io")
        self.assertTrue(exchange.data["access"])

    def test_a_refusal_redirects_with_a_code_the_frontend_can_explain(self):
        stub = StubProvider(identity=verified(email="stranger@elsewhere.com", subject="g-9"))
        with patch.object(login.providers, "get", return_value=stub):
            self.client.post("/api/v1/auth/oauth/google/start/")
            response = self.client.get(
                "/api/v1/auth/oauth/google/callback/",
                {"code": "abc", "state": stub.seen["state"]},
            )

        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertIn("error=DOMAIN_NOT_VERIFIED", response["Location"])

    def test_a_provider_refusal_is_reported_without_a_round_trip(self):
        with patch.object(login.providers, "get", return_value=StubProvider()):
            response = self.client.get(
                "/api/v1/auth/oauth/google/callback/", {"error": "access_denied"}
            )
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertIn("error=PROVIDER_REJECTED", response["Location"])

    def test_a_failed_sign_in_is_audited(self):
        stub = StubProvider(error=ProviderError("bad token"))
        with patch.object(login.providers, "get", return_value=stub):
            self.client.post("/api/v1/auth/oauth/google/start/")
            self.client.get(
                "/api/v1/auth/oauth/google/callback/",
                {"code": "abc", "state": stub.seen["state"]},
            )

        event = AuditEvent.objects.filter(action="auth.login", outcome="failure").first()
        self.assertIsNotNone(event)
        self.assertEqual(event.metadata["reason"], "PROVIDER_REJECTED")

    def test_a_successful_sign_in_is_audited_with_how_it_happened(self):
        stub = StubProvider()
        with patch.object(login.providers, "get", return_value=stub):
            self.client.post("/api/v1/auth/oauth/google/start/")
            self.client.get(
                "/api/v1/auth/oauth/google/callback/",
                {"code": "abc", "state": stub.seen["state"]},
            )

        event = AuditEvent.objects.filter(action="auth.login", outcome="success").first()
        self.assertIsNotNone(event)
        self.assertEqual(event.metadata["via"], "oauth")
        self.assertEqual(event.actor_id, self.user.id)

    def test_linking_an_identity_is_audited(self):
        login.resolve_user(verified())
        self.assertTrue(AuditEvent.objects.filter(action="identity.linked").exists())


class FeatureFlagTests(APITestCase):
    """With the flag off the feature is invisible, not half-present."""

    @override_settings(AUTH_V2_ENABLED=False)
    def test_no_providers_are_offered(self):
        response = self.client.get("/api/v1/auth/oauth/providers/")
        self.assertEqual(response.data["providers"], [])

    @override_settings(AUTH_V2_ENABLED=False)
    def test_starting_a_sign_in_is_refused(self):
        response = self.client.post("/api/v1/auth/oauth/google/start/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["code"], "PROVIDER_NOT_AVAILABLE")

    @override_settings(AUTH_V2_ENABLED=False)
    def test_password_sign_in_still_works(self):
        """The flag must not take the existing front door with it."""
        org = Organisation.objects.create(name="Acme Inc")
        User.objects.create_user(
            email="carl@acme.io", password="supersecret1", name="Carl", organisation=org
        )
        response = self.client.post(
            "/api/v1/auth/login/", {"email": "carl@acme.io", "password": "supersecret1"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
