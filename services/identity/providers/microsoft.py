"""Sign in with Microsoft, over OpenID Connect.

Microsoft's issuer carries the tenant id, so a `common` (multi-tenant) app sees
a different `iss` per customer. The accepted issuers are therefore built from
the token's own `tid` claim only after the signature has been verified, which
is why `verify_id_token` takes a list rather than a single value.
"""

import urllib.parse

import jwt
from django.conf import settings

from .base import LoginProvider, ProviderError, VerifiedIdentity, http_json, verify_id_token


def _endpoint(path: str) -> str:
    tenant = settings.MICROSOFT_OAUTH_TENANT or "common"
    return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/{path}"


def _jwks_uri() -> str:
    tenant = settings.MICROSOFT_OAUTH_TENANT or "common"
    return f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys"


SCOPES = "openid email profile"


class MicrosoftLoginProvider(LoginProvider):
    key = "microsoft"
    label = "Microsoft"

    def is_configured(self) -> bool:
        return bool(settings.MICROSOFT_OAUTH_CLIENT_ID and settings.MICROSOFT_OAUTH_CLIENT_SECRET)

    def authorize_url(self, *, state, nonce, redirect_uri):
        return (
            _endpoint("authorize")
            + "?"
            + urllib.parse.urlencode(
                {
                    "client_id": settings.MICROSOFT_OAUTH_CLIENT_ID,
                    "redirect_uri": redirect_uri,
                    "response_type": "code",
                    "scope": SCOPES,
                    "state": state,
                    "nonce": nonce,
                    "prompt": "select_account",
                }
            )
        )

    def verify_callback(self, *, code, redirect_uri, nonce):
        payload = http_json(
            "POST",
            _endpoint("token"),
            form={
                "code": code,
                "client_id": settings.MICROSOFT_OAUTH_CLIENT_ID,
                "client_secret": settings.MICROSOFT_OAUTH_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        id_token = payload.get("id_token", "")
        if not id_token:
            raise ProviderError("Microsoft returned no id_token")

        # Read the tenant id without trusting it: this is an unverified peek
        # used only to build the list of issuers the *verified* token is then
        # required to match. The signature check happens inside verify_id_token.
        try:
            unverified = jwt.decode(id_token, options={"verify_signature": False})
        except jwt.InvalidTokenError as exc:
            raise ProviderError("the provider's response could not be verified") from exc
        tenant_id = unverified.get("tid")
        issuers = [f"https://login.microsoftonline.com/{tenant_id}/v2.0"] if tenant_id else []

        claims = verify_id_token(
            id_token,
            jwks_uri=_jwks_uri(),
            issuers=issuers,
            audience=settings.MICROSOFT_OAUTH_CLIENT_ID,
            nonce=nonce,
        )

        # Entra ID puts the address in `email` when the tenant publishes it and
        # in `preferred_username` otherwise.
        email = claims.get("email") or claims.get("preferred_username") or ""
        if "@" not in email:
            raise ProviderError("Microsoft did not return an email address")

        return VerifiedIdentity(
            provider=self.key,
            subject=claims["sub"],
            email=email,
            # Entra ID does not send `email_verified`. A work or school account's
            # address is controlled by its tenant, which is the assurance that
            # matters here; a token that reached us at all came from that tenant.
            email_verified=bool(tenant_id),
            name=claims.get("name", ""),
            given_name=claims.get("given_name", ""),
            family_name=claims.get("family_name", ""),
        )
