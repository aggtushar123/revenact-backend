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

**How someone is matched to an account.** Either the identity is already
linked, or their verified address matches exactly one active user and the
identity is linked on the spot. An address nobody recognises is routed by its
domain (`_route_newcomer`): to an access request when a tenant holds the domain,
or to the workspace form when nobody does. Linking without a provider-verified
address is never allowed, because that is account takeover by whoever can claim
an address they do not own.
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

#: How long someone has to fill in the workspace form after a provider has
#: vouched for them. Longer than the hand-off: a form is being typed into.
SETUP_TTL = 15 * 60

SETUP_CACHE_PREFIX = "identity.login.setup:"


class LoginError(Exception):
    """A login that cannot proceed, carrying a code the frontend can act on.

    `setup` is present on exactly one code, `WORKSPACE_SETUP_REQUIRED`: the
    person is verified, nobody has claimed their domain, and the frontend
    should show them the workspace form. The value is the short-lived code that
    form sends back (see `issue_setup`).
    """

    def __init__(self, code: str, message: str, *, setup: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.setup = setup


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
        return _require_standing(user, identity_info, request=request)

    # First time with this provider. Only a provider-verified address may be
    # used to claim an existing account.
    if not identity_info.email_verified:
        raise LoginError(
            "EMAIL_NOT_VERIFIED",
            "Your provider has not verified that address, so it cannot be used to sign in.",
        )

    user = User.objects.filter(email__iexact=identity_info.email).first()
    if user is None:
        return _route_newcomer(identity_info, request=request)
    if not user.is_active:
        raise LoginError("ACCOUNT_DISABLED", "This account has been deactivated.")

    _link_identity(user, identity_info, request=request)
    return _require_standing(user, identity_info, request=request)


def _require_standing(user, identity_info, *, request=None) -> User:
    """A person is signed in only if they hold an active membership somewhere.

    Returns the user to sign in: the same instance, or a fresh one when an
    invitation was accepted along the way and the columns changed.

    Authenticating is not joining. Someone who signed in once, was routed to
    an access request, and comes back the next morning has a linked identity
    and still no membership; without this check they would receive a session
    that opens onto an empty application. Instead they are routed again, which
    also carries them to the right tenant if the domain changed hands while
    they waited (a company verifying a domain an employee had claimed).
    """
    if user.is_superuser:
        return user

    from . import domains, onboarding
    from .models import OrganizationMembership

    statuses = dict(
        OrganizationMembership.objects.filter(user=user).values_list("organisation_id", "status")
    )
    if OrganizationMembership.Status.ACTIVE in statuses.values():
        return user
    if OrganizationMembership.Status.SUSPENDED in statuses.values():
        raise LoginError("ACCOUNT_DISABLED", "Your access has been suspended.")

    # Waiting on a request, and meanwhile an administrator invited them:
    # the invitation is the decision, so it is accepted here.
    invited = _accept_if_invited(identity_info, request=request)
    if invited is not None:
        return invited

    route = domains.route_for_email(identity_info.email)
    if route.organisation is not None:
        try:
            onboarding.request_access(
                identity_info, organisation=route.organisation, request=request
            )
        except onboarding.OnboardingError as exc:
            raise LoginError(exc.code, exc.message) from exc
        raise LoginError(
            "ACCESS_REQUEST_PENDING",
            "Your request is with an administrator at your organisation.",
        )
    if route.kind == "personal":
        raise LoginError(
            "PERSONAL_EMAIL_NOT_SUPPORTED",
            "Ask a colleague to invite you with your work address.",
        )
    raise LoginError(
        "DOMAIN_NOT_VERIFIED",
        "No organisation here has verified that email domain yet.",
    )


def _route_newcomer(identity_info, *, request=None) -> User:
    """Nobody here by that address. Decide where a verified stranger goes.

    Returns a signed-in user in exactly one case: an administrator already
    invited this address, so the provider's word that they hold it is the
    whole acceptance. Otherwise raises: they are asked to wait for an
    administrator, told why they cannot proceed, or invited to set up a
    workspace of their own.

        an open invitation  -> accepted; membership granted; signed in
        verified domain     -> access request to that tenant, then wait
        one unverified claim -> access request to that tenant, then wait
        several claims      -> refused; nobody can be chosen without proof
        personal address    -> refused; there is no company to map to
        nobody has claimed it -> WORKSPACE_SETUP_REQUIRED with a setup code

    The second line is the one that stops a company fragmenting into a
    workspace per employee: once anyone from `acme.io` has started one, the
    next person is pointed at it and must be let in deliberately.
    """
    from . import domains, onboarding

    invited = _accept_if_invited(identity_info, request=request)
    if invited is not None:
        return invited

    route = domains.route_for_email(identity_info.email)

    if route.kind == "personal":
        raise LoginError(
            "PERSONAL_EMAIL_NOT_SUPPORTED",
            "Sign in with your work address, or ask a colleague to invite you.",
        )
    if route.kind == "ambiguous":
        raise LoginError(
            "DOMAIN_NOT_VERIFIED",
            "More than one workspace has claimed that domain and none has verified it.",
        )
    if route.kind == "unclaimed":
        raise LoginError(
            "WORKSPACE_SETUP_REQUIRED",
            "Nobody has set up a workspace for that domain yet.",
            setup=issue_setup(identity_info),
        )

    try:
        user, _ = onboarding.request_access(
            identity_info, organisation=route.organisation, request=request
        )
    except onboarding.OnboardingError as exc:
        raise LoginError(exc.code, exc.message) from exc
    _link_identity(user, identity_info, request=request)
    raise LoginError(
        "ACCESS_REQUEST_PENDING",
        "Your request is with an administrator at your organisation.",
    )


def _accept_if_invited(identity_info, *, request=None):
    """An open invitation to exactly this verified address is accepted on the
    spot, whatever the domain says: an invitation is an administrator's
    decision already made, and it is the only door for a personal address."""
    from . import onboarding

    invitation = onboarding.open_invitation_for(identity_info.email)
    if invitation is None:
        return None
    try:
        user = onboarding.accept_invitation(invitation, identity_info, request=request)
    except onboarding.OnboardingError as exc:
        raise LoginError(exc.code, exc.message) from exc
    _link_identity(user, identity_info, request=request)
    return user


def _link_identity(user, identity_info, *, request=None) -> None:
    """Record that this external account belongs to this person.

    Idempotent, because the pending-access path links before refusing the
    sign-in: the same person signing in again while they wait must not collide
    with the identity they already have.
    """
    _, created = Identity.objects.get_or_create(
        provider=identity_info.provider,
        provider_user_id=identity_info.subject,
        defaults={
            "user": user,
            "email": identity_info.email,
            "email_verified": True,
            "last_used_at": timezone.now(),
        },
    )
    if created:
        audit.record(  # SOC2:LOG-01
            "identity.linked",
            request=request,
            actor=user,
            organisation=user.organisation,
            target=user,
            metadata={"provider": identity_info.provider},
        )


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


def issue_setup(identity_info) -> str:
    """Park a verified identity behind a code while its owner fills in the
    workspace form. Nothing is written to the database until they submit:
    a person who closes the tab leaves no user and no organisation behind."""
    from django.core.cache import cache

    code = secrets.token_urlsafe(32)
    cache.set(
        SETUP_CACHE_PREFIX + code,
        {
            "provider": identity_info.provider,
            "subject": identity_info.subject,
            "email": identity_info.email,
            "name": identity_info.name or "",
        },
        SETUP_TTL,
    )
    return code


def peek_setup(code: str):
    """The identity behind a setup code, without spending it. For the form's
    own prefill; the code is spent by `redeem_setup` on submit."""
    from django.core.cache import cache

    from .providers import VerifiedIdentity

    payload = cache.get(SETUP_CACHE_PREFIX + (code or ""))
    if not payload:
        raise LoginError("INVALID_SETUP", "That sign-in has expired. Start again.")
    return VerifiedIdentity(
        provider=payload["provider"],
        subject=payload["subject"],
        email=payload["email"],
        email_verified=True,
        name=payload["name"],
    )


def redeem_setup(code: str):
    """Spend a setup code. Single use, like the hand-off."""
    from django.core.cache import cache

    identity_info = peek_setup(code)
    cache.delete(SETUP_CACHE_PREFIX + code)
    return identity_info


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

    return session_for(user)


def session_for(user: User) -> dict:
    """Mint the same pair the password login issues."""
    refresh = RefreshToken.for_user(user)
    return {"user": user, "refresh": str(refresh), "access": str(refresh.access_token)}
