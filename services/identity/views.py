"""The three endpoints behind "Continue with Google / Microsoft".

    POST /api/v1/auth/oauth/<provider>/start/   -> { authorize_url }
    GET  /api/v1/auth/oauth/<provider>/callback/ -> 302 to the frontend
    POST /api/v1/auth/oauth/exchange/           -> { access, refresh, user }

The callback is the only endpoint the provider talks to, and it is a browser
redirect rather than an API call, so it answers with a `302` carrying either a
one-time hand-off code or an error code. It never renders a token into the URL
(see `login.py` for why) and never renders provider text into HTML.

All three are `AllowAny` by necessity: nobody is signed in yet. They are
throttled instead, reusing the same scopes the password endpoints use, so a
second front door does not become an unrated one.
"""

import urllib.parse

from django.conf import settings
from django.shortcuts import redirect
from rest_framework import status, views
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from core import audit
from core.throttling import LoginIPThrottle, TokenRefreshThrottle
from services.accounts.serializers import UserSerializer

from . import login, providers


def _flag_enabled() -> bool:
    return bool(getattr(settings, "AUTH_V2_ENABLED", False))


def _error(code: str, message: str, http_status=status.HTTP_400_BAD_REQUEST) -> Response:
    """The structured error shape the architecture note specifies."""
    return Response({"success": False, "error": {"code": code, "message": message}}, http_status)


class ProviderListView(views.APIView):
    """GET /api/v1/auth/oauth/providers/ — which buttons the sign-in page shows.

    Returns an empty list rather than an error when the feature is off or
    nothing is configured, so the sign-in page renders its password form and
    says nothing about a half-built feature.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, request):
        return Response({"providers": providers.available() if _flag_enabled() else []})


class StartView(views.APIView):
    """POST /api/v1/auth/oauth/<provider>/start/ — begin a sign-in."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [LoginIPThrottle]  # SOC2:AUTH-06

    def post(self, request, provider_key):
        if not _flag_enabled():
            return _error("PROVIDER_NOT_AVAILABLE", "Sign-in with a provider is not enabled.")
        try:
            return Response({"authorize_url": login.start(provider_key)})
        except login.LoginError as exc:
            return _error(exc.code, exc.message)


class CallbackView(views.APIView):
    """GET /api/v1/auth/oauth/<provider>/callback/ — where the provider returns.

    Always redirects. The frontend owns every screen a person sees; this only
    decides whether it lands on success with a hand-off code, or on failure with
    a code it can explain.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [LoginIPThrottle]  # SOC2:AUTH-06

    def _frontend(self, **params) -> str:
        base = (settings.FRONTEND_URL or "").rstrip("/")
        return f"{base}/auth/callback?{urllib.parse.urlencode(params)}"

    def get(self, request, provider_key):
        if not _flag_enabled():
            return redirect(self._frontend(error="PROVIDER_NOT_AVAILABLE"))

        # The provider reports its own refusals here (a cancelled consent
        # screen, for instance) rather than by not calling back.
        if request.query_params.get("error"):
            return redirect(self._frontend(error="PROVIDER_REJECTED"))

        try:
            user = login.complete(
                provider_key,
                code=request.query_params.get("code", ""),
                state=request.query_params.get("state", ""),
                request=request,
            )
        except login.LoginError as exc:
            audit.record(  # SOC2:LOG-01
                "auth.login",
                request=request,
                outcome="failure",
                metadata={"via": "oauth", "provider": provider_key, "reason": exc.code},
            )
            return redirect(self._frontend(error=exc.code))

        audit.record(  # SOC2:LOG-01
            "auth.login",
            request=request,
            actor=user,
            organisation=user.organisation,
            metadata={"via": "oauth", "provider": provider_key},
        )
        return redirect(self._frontend(handoff=login.issue_handoff(user)))


class ExchangeView(views.APIView):
    """POST /api/v1/auth/oauth/exchange/ — trade the hand-off code for tokens.

    Same response shape as the password login, so the frontend stores the
    session exactly as it already does.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [TokenRefreshThrottle]  # SOC2:AUTH-06

    def post(self, request):
        if not _flag_enabled():
            return _error("PROVIDER_NOT_AVAILABLE", "Sign-in with a provider is not enabled.")
        try:
            session = login.redeem_handoff(request.data.get("handoff", ""))
        except login.LoginError as exc:
            return _error(exc.code, exc.message, status.HTTP_401_UNAUTHORIZED)

        return Response(
            {
                "access": session["access"],
                "refresh": session["refresh"],
                "user": UserSerializer(session["user"]).data,
            }
        )
