from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from .models import User
from .permissions import IsOrgAdmin
from .serializers import (
    ChangePasswordSerializer,
    CreateCSMSerializer,
    EditCSMSerializer,
    LoginSerializer,
    LogoutSerializer,
    MeSerializer,
    SignupSerializer,
    UserSerializer,
)


class SignupView(generics.CreateAPIView):
    """POST /api/v1/auth/signup/ — creates an Organisation + its first user
    (role=admin), and logs them in immediately (same response shape as
    login: user + access + refresh)."""

    serializer_class = SignupSerializer
    permission_classes = [AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "user": UserSerializer(user).data,
                "access": str(refresh.access_token),
                "refresh": str(refresh),
            },
            status=status.HTTP_201_CREATED,
        )


class LoginView(TokenObtainPairView):
    """POST /api/v1/auth/login/ — email+password for any user (org admin or
    CSM). Response: {access, refresh, user}."""

    serializer_class = LoginSerializer
    permission_classes = [AllowAny]


class LogoutView(generics.GenericAPIView):
    """POST /api/v1/auth/logout/ — blacklists the given refresh token so it
    can no longer be used at /token/refresh/. Requires a valid access token
    (i.e. you must be logged in to log out) but always succeeds from the
    client's point of view: an already-expired/invalid/blacklisted refresh
    token is not an error here — the caller is logged out either way.

    Note: this only revokes the *refresh* token. The current access token
    (60 min lifetime) keeps working until it naturally expires — simplejwt
    doesn't track individual access tokens for revocation."""

    serializer_class = LogoutSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            RefreshToken(serializer.validated_data["refresh"]).blacklist()
        except TokenError:
            pass
        return Response(status=status.HTTP_205_RESET_CONTENT)


class MeView(generics.RetrieveUpdateAPIView):
    """GET/PATCH /api/v1/auth/me/ — your own profile. Any authenticated
    user (admin or CSM). Only `name` is writable here — see
    ChangePasswordView for passwords."""

    serializer_class = MeSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user


class ChangePasswordView(generics.GenericAPIView):
    """POST /api/v1/auth/me/change-password/ — self-service password
    change. Requires the current password. Does not invalidate existing
    sessions/tokens (unlike an admin deactivating you, which does) — see
    the auth-flow doc."""

    serializer_class = ChangePasswordSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(status=status.HTTP_200_OK)


class CSMListCreateView(generics.ListCreateAPIView):
    """GET /api/v1/auth/csms/ — list the CSMs in the caller's own
    organisation (org-admin-only; the admin manages members, they don't
    appear in their own list here).
    POST /api/v1/auth/csms/ — adds a CSM to the caller's own organisation."""

    permission_classes = [IsOrgAdmin]

    def get_queryset(self):
        return User.objects.filter(
            organisation=self.request.user.organisation, role=User.Role.CSM
        ).order_by("name")

    def get_serializer_class(self):
        return UserSerializer if self.request.method == "GET" else CreateCSMSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


class CSMDetailView(generics.RetrieveUpdateAPIView):
    """GET/PATCH /api/v1/auth/csms/<id>/ — org-admin-only. Scoped to CSMs in
    the caller's own organisation — a 404, not a 403, for any other id (out
    of this org, or not a CSM), so admins can't probe for other orgs' user
    ids. Edits name/is_active/password — see EditCSMSerializer.

    Deactivating (is_active: false) also blacklists every outstanding
    refresh token for that user — the JWTAuthentication is_active check
    already blocks their current access token on its very next request, so
    this is defense-in-depth for the refresh token specifically, not the
    only thing making deactivation effective."""

    serializer_class = EditCSMSerializer
    permission_classes = [IsOrgAdmin]

    def get_queryset(self):
        return User.objects.filter(organisation=self.request.user.organisation, role=User.Role.CSM)

    def get_serializer_class(self):
        return UserSerializer if self.request.method == "GET" else EditCSMSerializer

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        was_active = instance.is_active
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        if was_active and not user.is_active:
            for token in OutstandingToken.objects.filter(user=user):
                BlacklistedToken.objects.get_or_create(token=token)

        return Response(UserSerializer(user).data)
