"""Turning a verified external identity into a session in this application.

Three things happen here, and the order matters:

1. **Start.** Mint a signed, expiring `state` carrying a fresh nonce. The state
   is what proves the callback belongs to a request we made (CSRF); the nonce
   is what proves the ID token was minted for *this* attempt (replay).
2. **Complete.** Hand the code to the provider, which verifies the token and
   returns who signed in. Then resolve that to a local `User`.
3. **Hand off.** Put the session behind a single-use code and redirect the
   browser to the frontend with only that code.

**Why the hand-off code exists.** The obvious thing is to redirect with the JWT
in the URL. That puts a bearer token in browser history, in the Referer header
of the next request, and in any proxy log along the way. Instead the redirect
carries an opaque one-time value, good for sixty seconds, which the frontend
exchanges for tokens over POST. Nothing long-lived ever appears in a URL.

**How someone is matched to an account, in this phase.** Either the identity is
already linked, or their verified address matches exactly one active user and
the identity is linked on the spot. An address nobody recognises is refused with
`NO_ACCOUNT`; phase 4 turns that case into an access request. Linking without a
provider-verified address is never allowed, because that is account takeover by
whoever can claim an address they do not own.
"""

import secrets

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from core import audit
from services.accounts.models import User

from . import providers
from .models import Identity

#: Namespaces the signature, so a state token cannot be replayed at the mail
#: OAuth callback (which uses its own salt) or anywhere else.
STATE_SALT = "identity.login.oauth"

#: How long someone has to complete the provider's screens.
STATE_MAX_AGE = 15 * 60

#: How long the browser has to trade the hand-off code for tokens. Short: the
#: frontend redeems it immediately on landing.
HANDOFF_TTL = 60

HANDOFF_CACHE_PREFIX = "identity.login.handoff:"


class LoginError(Exception):
    """A login that cannot proceed, carrying a code the frontend can act on."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def callback_url(provider_key: str) -> str:
    """Where the provider sends the browser back. Must match the redirect URI
    registered with the provider exactly, including scheme and trailing slash."""
    base = (settings.MAIL_OAUTH_REDIRECT_BASE or "").rstrip("/")
    return f"{base}/api/v1/auth/oauth/{provider_key}/callback/"


def start(provider_key: str) -> str:
    """Begin a sign-in. Returns the URL to send the browser to."""
    provider = providers.get(provider_key)
    if provider is None:
        raise LoginError("PROVIDER_NOT_AVAILABLE", "That sign-in method is not available.")

    nonce = secrets.token_urlsafe(24)
    state = signing.dumps({"p": provider.key, "n": nonce}, salt=STATE_SALT)
    return provider.authorize_url(state=state, nonce=nonce, redirect_uri=callback_url(provider.key))


def _read_state(provider_key: str, state: str) -> str:
    """Return the nonce carried by a valid state, or raise."""
    try:
        payload = signing.loads(state or "", salt=STATE_SALT, max_age=STATE_MAX_AGE)
    except signing.SignatureExpired as exc:
        raise LoginError("STATE_EXPIRED", "That sign-in took too long. Please try again.") from exc
    except signing.BadSignature as exc:
        raise LoginError("INVALID_STATE", "That sign-in request could not be verified.") from exc

    # The state names the provider it was minted for, so a state issued for
    # Google cannot be presented at Microsoft's callback.
    if payload.get("p") != provider_key:
        raise LoginError("INVALID_STATE", "That sign-in request could not be verified.")
    return payload.get("n", "")


def resolve_user(identity_info, *, request=None) -> User:
    """Find the local person this external identity belongs to.

    Linking happens here, once, the first time someone signs in with a provider
    whose verified address already matches an account.
    """
    existing = (
        Identity.objects.select_related("user")
        .filter(provider=identity_info.provider, provider_user_id=identity_info.subject)
        .first()
    )
    if existing:
        user = existing.user
        if not user.is_active:
            raise LoginError("ACCOUNT_DISABLED", "This account has been deactivated.")
        Identity.objects.filter(pk=existing.pk).update(
            email=identity_info.email,
            email_verified=identity_info.email_verified,
            last_used_at=timezone.now(),
        )
        return user

    # First time with this provider. Only a provider-verified address may be
    # used to claim an existing account.
    if not identity_info.email_verified:
        raise LoginError(
            "EMAIL_NOT_VERIFIED",
            "Your provider has not verified that address, so it cannot be used to sign in.",
        )

    user = User.objects.filter(email__iexact=identity_info.email).first()
    if user is None:
        raise LoginError(
            "NO_ACCOUNT",
            "There is no account for that address yet.",
        )
    if not user.is_active:
        raise LoginError("ACCOUNT_DISABLED", "This account has been deactivated.")

    Identity.objects.create(
        user=user,
        provider=identity_info.provider,
        provider_user_id=identity_info.subject,
        email=identity_info.email,
        email_verified=True,
        last_used_at=timezone.now(),
    )
    audit.record(  # SOC2:LOG-01
        "identity.linked",
        request=request,
        actor=user,
        organisation=user.organisation,
        target=user,
        metadata={"provider": identity_info.provider},
    )
    return user


def complete(provider_key: str, *, code: str, state: str, request=None) -> User:
    """Verify the callback and return the person it belongs to."""
    provider = providers.get(provider_key)
    if provider is None:
        raise LoginError("PROVIDER_NOT_AVAILABLE", "That sign-in method is not available.")
    if not code:
        raise LoginError("INVALID_STATE", "That sign-in request could not be verified.")

    nonce = _read_state(provider_key, state)

    try:
        identity_info = provider.verify_callback(
            code=code, redirect_uri=callback_url(provider_key), nonce=nonce
        )
    except providers.ProviderError as exc:
        # The provider's own message is safe to surface: they are written for
        # people, and never contain the token.
        raise LoginError("PROVIDER_REJECTED", str(exc)) from exc

    with transaction.atomic():
        return resolve_user(identity_info, request=request)


def issue_handoff(user: User) -> str:
    """Stash a session behind a single-use code and return it."""
    from django.core.cache import cache

    handoff = secrets.token_urlsafe(32)
    cache.set(HANDOFF_CACHE_PREFIX + handoff, user.pk, HANDOFF_TTL)
    return handoff


def redeem_handoff(handoff: str) -> dict:
    """Trade the code for tokens. Single use: the code is dropped on read."""
    from django.core.cache import cache

    key = HANDOFF_CACHE_PREFIX + (handoff or "")
    user_id = cache.get(key)
    # Deleted before anything else, so a replay within the TTL finds nothing
    # even if issuing the tokens below fails.
    cache.delete(key)

    if not user_id:
        raise LoginError("INVALID_HANDOFF", "That sign-in link has already been used.")

    user = User.objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        raise LoginError("ACCOUNT_DISABLED", "This account has been deactivated.")

    refresh = RefreshToken.for_user(user)
    return {"user": user, "refresh": str(refresh), "access": str(refresh.access_token)}
