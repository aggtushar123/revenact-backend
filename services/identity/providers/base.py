"""What a login provider looks like from the inside.

Deliberately narrower than `services.mail.providers`. A mailbox provider needs
long-lived access to somebody's mail, so it stores refresh tokens and knows how
to renew them. A *login* provider needs one thing and then forgets: proof of who
just signed in.

So nothing here stores a token. The access token is used once, if at all, and
the ID token is verified and discarded. There is no credential at rest to leak,
and `services/mail`'s Fernet machinery is deliberately not reused because there
is nothing to encrypt.

The security work is in `verify_id_token`. An ID token is only evidence if every
one of these holds:

- the signature checks out against the provider's own published keys (JWKS)
- the issuer is the one we expect, not merely a well-formed URL
- the audience is *our* client id, not some other application's
- it has not expired
- the nonce matches the one this login attempt generated

Skipping any of them turns a login into an impersonation. The nonce in
particular is what stops a token captured from another session being replayed
here.
"""

from collections.abc import Callable
from dataclasses import dataclass

import jwt
from jwt import PyJWKClient

from services.mail.providers.base import ProviderError, http_json

__all__ = ["LoginProvider", "ProviderError", "VerifiedIdentity", "http_json", "verify_id_token"]

#: How long a fetched JWKS stays usable before it is fetched again. Providers
#: rotate signing keys, and `PyJWKClient` handles the refresh; this only bounds
#: how stale the cached set may get.
JWKS_CACHE_SECONDS = 600


@dataclass(frozen=True)
class VerifiedIdentity:
    """Who the provider says just signed in, after the token was verified.

    `subject` is the provider's stable identifier for the person and is what
    `identity.Identity` keys on. `email` is a snapshot: useful for matching and
    display, never a join key, because an address can be reassigned inside a
    company while a subject cannot.
    """

    provider: str
    subject: str
    email: str
    email_verified: bool
    name: str = ""
    given_name: str = ""
    family_name: str = ""
    picture: str = ""

    def __post_init__(self):
        object.__setattr__(self, "email", (self.email or "").strip().lower())


class LoginProvider:
    """One external identity provider, from the login flow's point of view."""

    key: str = ""
    label: str = ""

    def is_configured(self) -> bool:
        """Whether this provider has credentials to use. An unconfigured
        provider is never offered, rather than offered and then failing."""
        raise NotImplementedError

    def authorize_url(self, *, state: str, nonce: str, redirect_uri: str) -> str:
        """Where to send the browser to start the flow."""
        raise NotImplementedError

    def verify_callback(self, *, code: str, redirect_uri: str, nonce: str) -> VerifiedIdentity:
        """Exchange the code and return the identity, or raise `ProviderError`.

        Implementations must verify the ID token rather than trusting the
        userinfo endpoint's body, and must compare the nonce.
        """
        raise NotImplementedError


_jwk_clients: dict[str, PyJWKClient] = {}


def _jwk_client(jwks_uri: str) -> PyJWKClient:
    client = _jwk_clients.get(jwks_uri)
    if client is None:
        client = PyJWKClient(jwks_uri, cache_keys=True, lifespan=JWKS_CACHE_SECONDS)
        _jwk_clients[jwks_uri] = client
    return client


def verify_id_token(
    id_token: str,
    *,
    jwks_uri: str,
    issuers: list[str] | Callable[[dict], list[str]],
    audience: str,
    nonce: str,
) -> dict:
    """Verify an OIDC ID token and return its claims.

    Every check that makes the token evidence is required here, and `options`
    spells them out rather than relying on library defaults that could change
    under us. `issuers` is a list, or a function of the *verified* claims that
    returns one: Microsoft's issuer carries the tenant id, so a multi-tenant
    app only knows which issuer to expect once it can trust the `tid` claim,
    and it can trust it only after the signature check. Nothing here ever
    reads a claim before that check.
    """
    if not id_token:
        raise ProviderError("the provider returned no id_token")

    try:
        signing_key = _jwk_client(jwks_uri).get_signing_key_from_jwt(id_token)
    except Exception as exc:  # PyJWKClient raises several distinct types
        raise ProviderError("could not fetch the provider's signing keys") from exc

    try:
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience,
            options={
                "require": ["exp", "iat", "aud", "iss", "sub"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_aud": True,
                "verify_iss": False,  # checked below, against a list
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise ProviderError("the sign-in took too long; please try again") from exc
    except jwt.InvalidAudienceError as exc:
        raise ProviderError("that token was issued for a different application") from exc
    except jwt.InvalidTokenError as exc:
        # Never echo the token or the library's detail back to the caller.
        raise ProviderError("the provider's response could not be verified") from exc

    accepted = issuers(claims) if callable(issuers) else issuers
    if not accepted or claims.get("iss") not in accepted:
        raise ProviderError("the provider's response could not be verified")

    # Replay protection: a token minted for a different login attempt has a
    # different nonce, so it cannot be presented here.
    if not nonce or claims.get("nonce") != nonce:
        raise ProviderError("this sign-in could not be matched to its request")

    return claims
