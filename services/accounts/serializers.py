from django.contrib.auth.tokens import default_token_generator
from django.db import transaction
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from services.email import send_password_reset_email

from .models import Organisation, User


class OrganisationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organisation
        fields = ["id", "name", "slug"]


class UserSerializer(serializers.ModelSerializer):
    """Read-only representation embedded in signup/login responses — shaped
    to match the frontend's `User` type in authSlice.ts (email, name,
    avatar), plus organisation + role for tenant-scoping the UI.
    `is_active` matters for the User Management list (a deactivated CSM
    still shows up there, just greyed out/toggleable — it isn't a delete)."""

    avatar = serializers.SerializerMethodField()
    organisation = OrganisationSerializer(read_only=True)

    class Meta:
        model = User
        fields = ["id", "email", "name", "avatar", "role", "organisation", "is_active"]

    def get_avatar(self, obj):
        return f"https://i.pravatar.cc/150?u={obj.email}"


class SignupSerializer(serializers.Serializer):
    """Creates a new Organisation plus its first user (role=admin) in one
    call. This is the only way an Organisation gets created."""

    organisation_name = serializers.CharField(max_length=255)
    name = serializers.CharField(max_length=255)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)

    def validate_email(self, value):
        value = value.lower()
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    @transaction.atomic
    def create(self, validated_data):
        organisation = Organisation.objects.create(name=validated_data["organisation_name"])
        return User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
            name=validated_data["name"],
            organisation=organisation,
            role=User.Role.ADMIN,
        )


class LoginSerializer(TokenObtainPairSerializer):
    """simplejwt's default serializer already authenticates against
    USERNAME_FIELD (email); this just adds the `user` object to the
    response so the frontend gets it in the same call as the tokens."""

    def validate(self, attrs):
        data = super().validate(attrs)
        data["user"] = UserSerializer(self.user).data
        return data


class LogoutSerializer(serializers.Serializer):
    """Just the refresh token to blacklist — see accounts/views.py:LogoutView."""

    refresh = serializers.CharField()


class CreateCSMSerializer(serializers.Serializer):
    """Org-admin-only: adds a Customer Success Manager to the admin's own
    organisation. The admin sets the CSM's initial password directly (no
    email invite flow yet)."""

    name = serializers.CharField(max_length=255)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)

    def validate_email(self, value):
        value = value.lower()
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def create(self, validated_data):
        organisation = self.context["request"].user.organisation
        return User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
            name=validated_data["name"],
            organisation=organisation,
            role=User.Role.CSM,
        )


class MeSerializer(serializers.ModelSerializer):
    """Your own profile. Read: full profile. Write: `name` only — email and
    role aren't self-editable. See ChangePasswordSerializer for passwords."""

    avatar = serializers.SerializerMethodField()
    organisation = OrganisationSerializer(read_only=True)

    class Meta:
        model = User
        fields = ["id", "email", "name", "avatar", "role", "organisation"]
        read_only_fields = ["id", "email", "role"]

    def get_avatar(self, obj):
        return f"https://i.pravatar.cc/150?u={obj.email}"


class ChangePasswordSerializer(serializers.Serializer):
    """Self-service password change — requires the current password (unlike
    an admin resetting a CSM's password via EditCSMSerializer, which is an
    override and doesn't)."""

    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)

    def validate_current_password(self, value):
        if not self.context["request"].user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def save(self):
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.save(update_fields=["password"])
        return user


class ForgotPasswordSerializer(serializers.Serializer):
    """Requests a reset-link email. Always succeeds from the caller's point
    of view (see ForgotPasswordView) regardless of whether the address
    matches a user — this is deliberately silent on a miss, rather than
    telling the caller no such account exists, so the endpoint can't be
    used to enumerate registered emails.

    The email itself (building the uid/token pair and sending it) lives in
    services/email.py, not here — this serializer's job stops at "does this
    email belong to a user, and if so hand it off."""

    email = serializers.EmailField()

    def save(self):
        try:
            user = User.objects.get(email=self.validated_data["email"].lower())
        except User.DoesNotExist:
            return  # deliberately silent — see class docstring
        send_password_reset_email(user)


class ResetPasswordSerializer(serializers.Serializer):
    """Consumes the uid+token from the emailed link (see
    ForgotPasswordSerializer) and sets a new password. Every failure mode —
    malformed uid, unknown user, wrong or expired token — collapses to the
    same generic error, again to avoid leaking which emails are registered
    or how a bad request differs from an expired one."""

    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True, min_length=8)

    GENERIC_ERROR = "This reset link is invalid or has expired."

    def validate(self, attrs):
        try:
            pk = force_str(urlsafe_base64_decode(attrs["uid"]))
            user = User.objects.get(pk=pk)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            raise serializers.ValidationError(self.GENERIC_ERROR)

        if not default_token_generator.check_token(user, attrs["token"]):
            raise serializers.ValidationError(self.GENERIC_ERROR)

        attrs["user"] = user
        return attrs

    def save(self):
        user = self.validated_data["user"]
        user.set_password(self.validated_data["new_password"])
        user.save(update_fields=["password"])
        return user


class EditCSMSerializer(serializers.ModelSerializer):
    """Org-admin-only: edits a CSM in the admin's own organisation. `password`
    is an admin override (no current-password check, unlike self-service
    ChangePasswordSerializer) — a manual reset an admin can still reach for
    even though CSMs now also have the self-serve ForgotPasswordSerializer
    flow (e.g. if their email account itself is inaccessible)."""

    password = serializers.CharField(write_only=True, required=False, min_length=8)

    class Meta:
        model = User
        fields = ["name", "is_active", "password"]

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        instance = super().update(instance, validated_data)
        if password:
            instance.set_password(password)
            instance.save(update_fields=["password"])
        return instance
