from rest_framework import generics, status, views
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from core import audit
from core.throttling import (
    LoginAccountThrottle,
    LoginIPThrottle,
    PasswordResetThrottle,
    SignupThrottle,
    TokenRefreshThrottle,
)

from .capabilities import Capability
from .models import Role, User
from .permissions import CanManageOrgSettings, CanManageUsers
from .serializers import (
    ChangePasswordSerializer,
    CreateOrgUserSerializer,
    EditOrgUserSerializer,
    ForgotPasswordSerializer,
    LoginSerializer,
    LogoutSerializer,
    MeSerializer,
    OrganisationSerializer,
    ResetPasswordSerializer,
    RoleSerializer,
    SignupSerializer,
    UserSerializer,
)


class SignupView(generics.CreateAPIView):
    """POST /api/v1/auth/signup/ — creates an Organisation + its first user
    (role=admin), and logs them in immediately (same response shape as
    login: user + access + refresh)."""

    serializer_class = SignupSerializer
    permission_classes = [AllowAny]
    throttle_classes = [SignupThrottle]  # SOC2:AUTH-06

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        # SOC2:LOG-01 new tenant + first admin
        audit.record(
            "auth.signup",
            request=request,
            actor=user,
            target=user.organisation,
            metadata={"organisation": user.organisation.name},
        )

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
    throttle_classes = [LoginIPThrottle, LoginAccountThrottle]  # SOC2:AUTH-06

    def post(self, request, *args, **kwargs):
        # Failures are recorded by core.signals (Django's user_login_failed
        # fires from authenticate()); success is recorded here because
        # SimpleJWT never calls django.contrib.auth.login().
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        audit.record("auth.login", request=request, actor=serializer.user)  # SOC2:LOG-01
        return Response(serializer.validated_data, status=status.HTTP_200_OK)


class RefreshView(TokenRefreshView):
    """POST /api/v1/auth/token/refresh/ — { refresh } → { access, refresh }.
    Rotation is on (SIMPLE_JWT.ROTATE_REFRESH_TOKENS): the token sent in is
    blacklisted and a new one comes back, so the client must store it."""

    throttle_classes = [TokenRefreshThrottle]  # SOC2:AUTH-06


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
        audit.record("auth.logout", request=request)  # SOC2:LOG-01
        return Response(status=status.HTTP_205_RESET_CONTENT)


class MeView(generics.RetrieveUpdateAPIView):
    """GET/PATCH /api/v1/auth/me/ — your own profile. Any authenticated
    user (admin or CSM). Only `name` is writable here — see
    ChangePasswordView for passwords."""

    serializer_class = MeSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user


class OrganisationSettingsView(generics.RetrieveUpdateAPIView):
    """GET/PATCH /api/v1/auth/organisation/ — the caller's own tenant.
    Backs Settings > Currency and Settings > Global Presets
    (react-ts-app's src/pages/settings/CurrencyPage.tsx/
    GlobalPresetsPage.tsx). Any authenticated user can view it (both
    pages show a read-only view to someone without the capability);
    changing it requires `manage_org_settings`, method-gated here since
    GET stays open to everyone."""

    serializer_class = OrganisationSerializer

    def get_permissions(self):
        if self.request.method in ("PATCH", "PUT"):
            return [IsAuthenticated(), CanManageOrgSettings()]
        return [IsAuthenticated()]

    def get_object(self):
        return self.request.user.organisation

    def perform_update(self, serializer):
        # A stored FxRate row means "X -> the org's *old* currency" —
        # silently reinterpreting it as "X -> the *new* currency" the
        # moment this changes would produce a wrong number with no
        # visible sign anything was wrong. Clearing them forces the
        # admin to re-enter rates for the new base currency instead
        # (see services.fx_rates.models.FxRate's own docstring). No
        # import of FxRate needed — the reverse `fx_rates` accessor
        # works once services.fx_rates is installed.
        new_currency = serializer.validated_data.get("currency")
        if new_currency and new_currency != serializer.instance.currency:
            serializer.instance.fx_rates.all().delete()
        organisation = serializer.save()
        # SOC2:LOG-01 tenant-wide config change
        audit.record(
            "organisation.update",
            request=self.request,
            target=organisation,
            metadata={"fields": sorted(serializer.validated_data.keys())},
        )


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
        audit.record("auth.password_change", request=request, target=request.user)  # SOC2:LOG-01
        return Response(status=status.HTTP_200_OK)


class ForgotPasswordView(generics.GenericAPIView):
    """POST /api/v1/auth/password-reset/ — { email }. Emails a reset link
    when that address matches a user, but always responds 200 with the same
    generic message either way (see ForgotPasswordSerializer) so the
    endpoint can't be used to probe which emails are registered."""

    serializer_class = ForgotPasswordSerializer
    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetThrottle]  # SOC2:AUTH-06

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        # SOC2:LOG-01 recorded whether or not the address matched, so the
        # audit row itself doesn't reveal which emails exist.
        audit.record(
            "auth.password_reset_request",
            request=request,
            metadata={"email": serializer.validated_data["email"].lower()},
        )
        return Response(
            {"detail": "If an account exists for that email, we've sent a password reset link."},
            status=status.HTTP_200_OK,
        )


class ResetPasswordView(generics.GenericAPIView):
    """POST /api/v1/auth/password-reset/confirm/ — { uid, token, new_password },
    the uid+token pair from the emailed link. Sets the new password if
    they're valid and unexpired; `400` with a generic error otherwise. Does
    not log the caller in — they sign in with the new password same as any
    other login, same as ChangePasswordView doesn't either."""

    serializer_class = ResetPasswordSerializer
    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetThrottle]  # SOC2:AUTH-06

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        audit.record("auth.password_reset", request=request, actor=user, target=user)  # SOC2:LOG-01
        return Response({"detail": "Your password has been reset."}, status=status.HTTP_200_OK)


class CapabilityListView(views.APIView):
    """GET /api/v1/auth/capabilities/ — the closed set of capabilities a
    Role can hold, as `[{key, label}]`.

    Served rather than hardcoded in the frontend so the role editor's
    checkboxes can't drift out of sync with what the backend actually
    enforces (see capabilities.py's own docstring on why that set stays
    small). Open to any authenticated member — it's a static vocabulary,
    not org data."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response([{"key": choice.value, "label": choice.label} for choice in Capability])


class RoleListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/v1/auth/roles/ — the caller's own organisation's
    roles.

    GET is open to any member: the Users page renders role names, and
    anyone may legitimately need to see what roles exist. POST requires
    `manage_users` — same capability that gates assigning them."""

    serializer_class = RoleSerializer
    pagination_class = None  # one org's own roles — a handful.

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), CanManageUsers()]
        return [IsAuthenticated()]

    def get_queryset(self):
        return Role.objects.filter(organisation=self.request.user.organisation)

    def perform_create(self, serializer):
        role = serializer.save()
        # SOC2:LOG-01 permission change
        audit.record(
            "role.create",
            request=self.request,
            target=role,
            metadata={"permissions": role.permissions},
        )


class RoleDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/v1/auth/roles/<id>/ — same read-open,
    write-gated split as the list view above.

    The system roles (Admin/CSM) reject PATCH and DELETE, and a role
    still assigned to somebody rejects DELETE — see RoleSerializer and
    `destroy` below for the real checks and why each exists."""

    serializer_class = RoleSerializer

    def get_permissions(self):
        if self.request.method == "GET":
            return [IsAuthenticated()]
        return [IsAuthenticated(), CanManageUsers()]

    def get_queryset(self):
        return Role.objects.filter(organisation=self.request.user.organisation)

    def perform_update(self, serializer):
        role = serializer.save()
        # SOC2:LOG-01 permission change
        audit.record(
            "role.update",
            request=self.request,
            target=role,
            metadata={
                "fields": sorted(serializer.validated_data.keys()),
                "permissions": role.permissions,
            },
        )

    def destroy(self, request, *args, **kwargs):
        role = self.get_object()
        if role.is_system:
            raise ValidationError("The built-in Admin and CSM roles can't be deleted.")
        if role.users.exists():
            raise ValidationError(
                "Move the people holding this role onto another one before deleting it."
            )
        audit.record("role.delete", request=request, target=role)  # SOC2:LOG-01
        return super().destroy(request, *args, **kwargs)


class OrgUserListCreateView(generics.ListCreateAPIView):
    """GET /api/v1/auth/users/ — every member of the caller's own
    organisation, admins included (unlike the CSM-only list this
    replaced, which meant an admin couldn't see themselves or their
    fellow admins on the Users page at all).
    POST /api/v1/auth/users/ — adds a member, in a role of the
    admin's choosing (defaulting to CSM). Both require `manage_users`."""

    permission_classes = [CanManageUsers]

    def get_queryset(self):
        return (
            User.objects.filter(organisation=self.request.user.organisation)
            .select_related("role")
            .order_by("name")
        )

    def get_serializer_class(self):
        return UserSerializer if self.request.method == "GET" else CreateOrgUserSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        # SOC2:LOG-01 / AUTH-07 provisioning
        audit.record(
            "user.create",
            request=request,
            target=user,
            metadata={"role": user.role.slug if user.role else None},
        )
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


class OrgUserDetailView(generics.RetrieveUpdateAPIView):
    """GET/PATCH /api/v1/auth/users/<id>/ — requires `manage_users`.
    Scoped to the caller's own organisation — a 404, not a 403, for any
    other id, so nobody can probe for other orgs' user ids. Edits
    name/is_active/role/password — see EditOrgUserSerializer.

    Deactivating (is_active: false) also blacklists every outstanding
    refresh token for that user — the JWTAuthentication is_active check
    already blocks their current access token on its very next request, so
    this is defense-in-depth for the refresh token specifically, not the
    only thing making deactivation effective."""

    serializer_class = EditOrgUserSerializer
    permission_classes = [CanManageUsers]

    def get_queryset(self):
        return User.objects.filter(organisation=self.request.user.organisation)

    def get_serializer_class(self):
        return UserSerializer if self.request.method == "GET" else EditOrgUserSerializer

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        was_active = instance.is_active
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        changed = sorted(serializer.validated_data.keys())
        if was_active and not user.is_active:
            for token in OutstandingToken.objects.filter(user=user):
                BlacklistedToken.objects.get_or_create(token=token)
            # SOC2:AUTH-07 / LOG-01 deprovisioning is auditable
            audit.record("user.deactivate", request=request, target=user)
        elif not was_active and user.is_active:
            audit.record("user.reactivate", request=request, target=user)

        # Field *names* only — never the password value (audit drops it anyway).
        audit.record("user.update", request=request, target=user, metadata={"fields": changed})

        return Response(UserSerializer(user).data)


class MembersListView(generics.ListAPIView):
    """GET /api/v1/auth/members/ — every member of the caller's own
    organisation. Unlike /users/, this is not capability-gated — it
    exists so any authenticated user can populate an owner-picker
    (e.g. assigning a customer to a CSM) without needing User Management
    access. Read-only; no pagination envelope, this list is expected to
    stay small."""

    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return User.objects.filter(organisation=self.request.user.organisation).order_by("name")
