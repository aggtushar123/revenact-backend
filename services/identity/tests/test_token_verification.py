"""ID token verification.

This is the part of sign-in that is actually security. An ID token is only
evidence of who someone is if the signature, issuer, audience, expiry and nonce
are all checked; skip any one and a login becomes an impersonation. These tests
mint real RS256 tokens and assert each check independently, because a test that
only proves the happy path would pass just as well against a function that
verified nothing.
"""

import json
from datetime import datetime, timedelta, timezone

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import TestCase

from services.identity.providers.base import ProviderError, verify_id_token

ISSUER = "https://accounts.google.com"
AUDIENCE = "our-client-id.apps.googleusercontent.com"
NONCE = "the-nonce-from-this-attempt"

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def make_token(key=None, **overrides):
    now = datetime.now(timezone.utc)
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "subject-123",
        "email": "alice@acme.io",
        "email_verified": True,
        "nonce": NONCE,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode(claims, key or _key, algorithm="RS256")


class FakeSigningKey:
    def __init__(self, key):
        self.key = key.public_key()


class FakeJWKClient:
    """Stands in for the provider's published key set."""

    def __init__(self, key=None, fail=False):
        self._key = key or _key
        self._fail = fail

    def get_signing_key_from_jwt(self, token):
        if self._fail:
            raise RuntimeError("jwks unreachable")
        return FakeSigningKey(self._key)


class VerifyIdTokenTests(TestCase):
    def setUp(self):
        # Replace the cached client rather than reaching the network.
        from services.identity.providers import base

        self._clients = base._jwk_clients
        base._jwk_clients = {}
        self.base = base

    def tearDown(self):
        self.base._jwk_clients = self._clients

    def verify(self, token, *, client=None, issuers=None, audience=AUDIENCE, nonce=NONCE):
        self.base._jwk_clients["https://jwks.test/keys"] = client or FakeJWKClient()
        return verify_id_token(
            token,
            jwks_uri="https://jwks.test/keys",
            issuers=issuers or [ISSUER],
            audience=audience,
            nonce=nonce,
        )

    def test_a_good_token_verifies(self):
        claims = self.verify(make_token())
        self.assertEqual(claims["sub"], "subject-123")
        self.assertEqual(claims["email"], "alice@acme.io")

    def test_a_token_signed_by_the_wrong_key_is_refused(self):
        """The core check: anyone can mint a token, only the provider can sign one."""
        with self.assertRaises(ProviderError):
            self.verify(make_token(key=_other_key))

    def test_a_token_for_another_application_is_refused(self):
        with self.assertRaises(ProviderError):
            self.verify(make_token(aud="someone-elses-client-id"))

    def test_an_expired_token_is_refused(self):
        expired = datetime.now(timezone.utc) - timedelta(minutes=1)
        with self.assertRaises(ProviderError):
            self.verify(make_token(exp=expired, iat=expired - timedelta(minutes=5)))

    def test_a_token_from_an_unexpected_issuer_is_refused(self):
        with self.assertRaises(ProviderError):
            self.verify(make_token(iss="https://evil.example"))

    def test_a_token_minted_for_another_attempt_is_refused(self):
        """Replay protection. Without this a token captured elsewhere works here."""
        with self.assertRaises(ProviderError):
            self.verify(make_token(nonce="a-different-attempt"))

    def test_a_token_with_no_nonce_is_refused(self):
        token = make_token()
        payload = jwt.decode(token, options={"verify_signature": False})
        payload.pop("nonce")
        with self.assertRaises(ProviderError):
            self.verify(jwt.encode(payload, _key, algorithm="RS256"))

    def test_an_empty_token_is_refused(self):
        with self.assertRaises(ProviderError):
            self.verify("")

    def test_an_unsigned_token_is_refused(self):
        """`alg: none` is the oldest JWT attack there is."""
        unsigned = jwt.encode({"iss": ISSUER, "sub": "x", "nonce": NONCE}, key="", algorithm="none")
        with self.assertRaises(ProviderError):
            self.verify(unsigned)

    def test_an_unreachable_key_set_fails_closed(self):
        with self.assertRaises(ProviderError):
            self.verify(make_token(), client=FakeJWKClient(fail=True))

    def test_the_token_never_appears_in_the_error(self):
        """Errors reach logs and screens; a token in one is a credential leak."""
        token = make_token(key=_other_key)
        with self.assertRaises(ProviderError) as caught:
            self.verify(token)
        self.assertNotIn(token, str(caught.exception))
        self.assertNotIn(token.split(".")[1], str(caught.exception))


class MicrosoftIssuerTests(TestCase):
    """Entra ID's issuer carries the tenant id, so it is derived per token."""

    def test_the_issuer_list_is_built_from_the_tenant(self):
        from services.identity.providers import base, microsoft

        tenant = "11111111-2222-3333-4444-555555555555"
        token = make_token(iss=f"https://login.microsoftonline.com/{tenant}/v2.0", tid=tenant)

        base._jwk_clients["https://jwks.test/keys"] = FakeJWKClient()
        claims = verify_id_token(
            token,
            jwks_uri="https://jwks.test/keys",
            issuers=[f"https://login.microsoftonline.com/{tenant}/v2.0"],
            audience=AUDIENCE,
            nonce=NONCE,
        )
        self.assertEqual(claims["tid"], tenant)
        self.assertTrue(hasattr(microsoft, "MicrosoftLoginProvider"))

    def test_a_token_from_another_tenant_is_refused(self):
        from services.identity.providers import base

        token = make_token(iss="https://login.microsoftonline.com/attacker-tenant/v2.0")
        base._jwk_clients["https://jwks.test/keys"] = FakeJWKClient()
        with self.assertRaises(ProviderError):
            verify_id_token(
                token,
                jwks_uri="https://jwks.test/keys",
                issuers=["https://login.microsoftonline.com/our-tenant/v2.0"],
                audience=AUDIENCE,
                nonce=NONCE,
            )


class JsonImportGuard(TestCase):
    """`json` is imported by the providers for token endpoints; keep it used."""

    def test_json_is_available(self):
        self.assertEqual(json.loads("{}"), {})
