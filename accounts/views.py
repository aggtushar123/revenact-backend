from rest_framework import generics, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from .permissions import IsOrgAdmin
from .serializers import CreateCSMSerializer, LoginSerializer, SignupSerializer, UserSerializer


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
