from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from .permissions import IsOrgAdmin
from .serializers import (
    CreateCSMSerializer,
    LoginSerializer,
    LogoutSerializer,
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


class CreateCSMView(generics.CreateAPIView):
    """POST /api/v1/auth/csms/ — org-admin-only, adds a CSM to the caller's
    own organisation."""

    serializer_class = CreateCSMSerializer
    permission_classes = [IsOrgAdmin]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)
