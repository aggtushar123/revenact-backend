"""Sign in with Google, over OpenID Connect."""

import urllib.parse

from django.conf import settings

from .base import LoginProvider, ProviderError, VerifiedIdentity, http_json, verify_id_token

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ["https://accounts.google.com", "accounts.google.com"]

#: Identity only. No Gmail scope: connecting a mailbox is a separate, explicit
#: act on the Integrations page (`services.mail`), and signing in must not
#: quietly ask for someone's inbox.
SCOPES = "openid email profile"


class GoogleLoginProvider(LoginProvider):
    key = "google"
    label = "Google"

    def is_configured(self) -> bool:
        return bool(settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET)

    def authorize_url(self, *, state, nonce, redirect_uri):
        return (
            AUTH_URL
            + "?"
            + urllib.parse.urlencode(
                {
                    "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                    "redirect_uri": redirect_uri,
                    "response_type": "code",
                    "scope": SCOPES,
                    "state": state,
                    "nonce": nonce,
                    # Ask every time rather than silently reusing a session, so
                    # switching accounts works and consent is never assumed.
                    "prompt": "select_account",
                }
            )
        )

    def verify_callback(self, *, code, redirect_uri, nonce):
        payload = http_json(
            "POST",
            TOKEN_URL,
            form={
                "code": code,
                "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        claims = verify_id_token(
            payload.get("id_token", ""),
            jwks_uri=JWKS_URI,
            issuers=ISSUERS,
            audience=settings.GOOGLE_OAUTH_CLIENT_ID,
            nonce=nonce,
        )
        if not claims.get("email"):
            raise ProviderError("Google did not return an email address")

        return VerifiedIdentity(
            provider=self.key,
            subject=claims["sub"],
            email=claims["email"],
            # Google sends this as a real boolean or the string "true".
            email_verified=str(claims.get("email_verified", "")).lower() == "true"
            or claims.get("email_verified") is True,
            name=claims.get("name", ""),
            given_name=claims.get("given_name", ""),
            family_name=claims.get("family_name", ""),
            picture=claims.get("picture", ""),
        )
