from django.contrib.auth.password_validation import validate_password as _django_validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.utils.text import slugify
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from services.email import send_password_reset_email

from .capabilities import ALL_CAPABILITIES, Capability
from .models import Organisation, Role, User


class OrganisationSerializer(serializers.ModelSerializer):
    """Read: nested as-is in UserSerializer/login/signup responses, and
    standalone from OrganisationSettingsView. Write: only via that same
    view (PATCH `currency`/`default_lifecycle_stage`/`ai_agent_enabled`/
    `ai_agent_tone`) — `name`/`slug` have no edit UI anywhere and stay
    read-only here so a settings PATCH can never accidentally rename the
    tenant."""

    currency_display = serializers.CharField(source="get_currency_display", read_only=True)
    ai_agent_tone_display = serializers.CharField(
        source="get_ai_agent_tone_display", read_only=True
    )
    global_attributes = serializers.SerializerMethodField()
    global_attribute_choices = serializers.SerializerMethodField()

    class Meta:
        model = Organisation
        fields = [
            "id",
            "name",
            "slug",
            "currency",
            "currency_display",
            "default_lifecycle_stage",
            "ai_agent_enabled",
            "ai_agent_tone",
            "ai_agent_tone_display",
            "global_attributes",
            "global_attribute_choices",
        ]
        # `slug` is read-only: it is the tenant's identity. `name` is what the
        # global configuration card edits, gated on manage_org_settings by the
        # view; the settings PATCH cannot touch anything else here.
        read_only_fields = ["id", "slug"]

    def get_global_attributes(self, obj):
        return obj.effective_global_attributes()

    def get_global_attribute_choices(self, obj):
        from .models import GLOBAL_ATTRIBUTE_CHOICES

        return {key: list(values) for key, values in GLOBAL_ATTRIBUTE_CHOICES.items()}

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("The organisation needs a name.")
        return value

    def to_internal_value(self, data):
        # `global_attributes` is a method field on read; on write it is a
        # partial mapping validated against the fixed keys and choices.
        attrs = super().to_internal_value(data)
        if "global_attributes" in data:
            from .models import GLOBAL_ATTRIBUTE_CHOICES

            mapping = data["global_attributes"]
            if not isinstance(mapping, dict):
                raise serializers.ValidationError({"global_attributes": "Must be an object."})
            for key, value in mapping.items():
                if key not in GLOBAL_ATTRIBUTE_CHOICES:
                    raise serializers.ValidationError(
                        {"global_attributes": f"Unknown attribute {key!r}."}
                    )
                if value not in GLOBAL_ATTRIBUTE_CHOICES[key]:
                    raise serializers.ValidationError(
                        {"global_attributes": f"{value!r} cannot stand for {key}."}
                    )
            attrs["global_attributes"] = {
                **(self.instance.global_attributes or {}),
                **mapping,
            }
        return attrs


class UserSerializer(serializers.ModelSerializer):
    """Read-only representation embedded in signup/login responses — shaped
    to match the frontend's `User` type in authSlice.ts (email, name,
    avatar), plus organisation + role for tenant-scoping the UI.
    `is_active` matters for the User Management list (a deactivated
    member still shows up there, just greyed out/toggleable — it isn't a
    delete).

    `role` stays a plain **slug string** even though it's a ForeignKey
    now, so the two built-in slugs read exactly as they did when this
    was a CharField. `permissions` is the flat capability list the
    frontend actually gates on (see useCapability in the frontend's
    hooks.ts) — a role's name is for display, its capabilities are what
    mean something."""

    avatar = serializers.SerializerMethodField()
    organisation = OrganisationSerializer(read_only=True)
    role = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    role_id = serializers.PrimaryKeyRelatedField(source="role", read_only=True)
    role_name = serializers.CharField(source="role.name", read_only=True, default="")
    permissions = serializers.SerializerMethodField()
    function_display = serializers.CharField(source="get_function_display", read_only=True)
    reports_to = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "name",
            "avatar",
            "role",
            "role_id",
            "role_name",
            "permissions",
            "function",
            "function_display",
            "reports_to",
            "organisation",
            "is_active",
        ]

    def get_avatar(self, obj):
        return f"https://i.pravatar.cc/150?u={obj.email}"

    def get_reports_to(self, obj):
        # The org chart, one level up — see services.accounts.hierarchy.
        if obj.reports_to_id is None:
            return None
        return {"id": obj.reports_to.id, "name": obj.reports_to.name}

    def get_permissions(self, obj) -> list:
        if obj.is_superuser:
            return list(ALL_CAPABILITIES)
        return list(obj.role.permissions or []) if obj.role_id else []


def _check_password_strength(password, user=None, field=None):
    """SOC2:AUTH-04 every path that sets a password runs Django's
    AUTH_PASSWORD_VALIDATORS (12+ chars, not common, not similar to the
    user's own name/email) — signup, admin-set, self-service change and
    reset alike. `user` may be unsaved; only its attributes are read.
    From a serializer-level validate(), pass `field` so the errors are
    reported against that field rather than as non_field_errors."""
    try:
        _django_validate_password(password, user=user)
    except DjangoValidationError as exc:
        messages = list(exc.messages)
        raise serializers.ValidationError({field: messages} if field else messages)


class SignupSerializer(serializers.Serializer):
    """Creates a new Organisation plus its first user (role=admin) in one
    call. This is the only way an Organisation gets created."""

    organisation_name = serializers.CharField(max_length=255)
    name = serializers.CharField(max_length=255)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate_email(self, value):
        value = value.lower()
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def validate(self, attrs):
        _check_password_strength(
            attrs["password"], User(email=attrs["email"], name=attrs["name"]), field="password"
        )
        return attrs

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


def _validate_grantable(permissions, actor):
    """No privilege escalation: you can only put capabilities into a role
    that you already hold yourself.

    Without this, anyone with `manage_users` — a capability an admin
    might reasonably delegate to an office manager — could mint a role
    holding every other capability and assign it to themselves, making
    `manage_users` silently equivalent to full admin."""

    missing = [c for c in permissions if not actor.has_capability(c)]
    if missing:
        raise serializers.ValidationError(
            {"permissions": f"You can't grant capabilities you don't have yourself: {missing}."}
        )


class RoleSerializer(serializers.ModelSerializer):
    """Read/write for the Roles tab of the Users page. `slug`,
    `is_system` and `organisation` are all server-derived — a client
    names a role and picks its capabilities, nothing else."""

    users_count = serializers.SerializerMethodField()

    class Meta:
        model = Role
        fields = ["id", "name", "slug", "permissions", "is_system", "users_count", "created_at"]
        read_only_fields = ["slug", "is_system", "created_at"]

    def get_users_count(self, obj) -> int:
        return obj.users.count()

    def validate_permissions(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("Expected a list of capability keys.")
        unknown = [c for c in value if c not in Capability.values]
        if unknown:
            raise serializers.ValidationError(f"Unknown capabilities: {unknown}.")
        _validate_grantable(value, self.context["request"].user)
        return list(dict.fromkeys(value))

    def validate(self, attrs):
        # A system role's whole point is being the fixed floor (CSM) and
        # ceiling (Admin) of an org's permissions — editing either would
        # let an org quietly redefine what "Admin" means and lock itself
        # out. New roles are the supported way to express anything else.
        if self.instance and self.instance.is_system:
            raise serializers.ValidationError("The built-in Admin and CSM roles can't be changed.")
        return attrs

    def create(self, validated_data):
        organisation = self.context["request"].user.organisation
        validated_data["organisation"] = organisation
        validated_data["slug"] = _unique_role_slug(validated_data["name"], organisation)
        return super().create(validated_data)


def _unique_role_slug(name, organisation):
    """A real, collision-free slug within one organisation — same
    derive-then-suffix shape as Organisation._generate_unique_slug and
    custom_objects' own `_unique_slug`."""

    base = slugify(name) or "role"
    slug = base
    n = 1
    while organisation.roles.filter(slug=slug).exists():
        n += 1
        slug = f"{base}-{n}"
    return slug


class CreateOrgUserSerializer(serializers.Serializer):
    """Adds a member to the caller's own organisation, in a role of the
    caller's choosing (defaulting to CSM when `role_id` is omitted). The
    admin sets the initial password directly (no email invite flow yet)."""

    name = serializers.CharField(max_length=255)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    role_id = serializers.IntegerField(required=False)
    function = serializers.ChoiceField(choices=User.Function.choices, required=False)
    reports_to_id = serializers.IntegerField(required=False, allow_null=True)

    def validate(self, attrs):
        _check_password_strength(
            attrs["password"],
            User(email=attrs.get("email", ""), name=attrs.get("name", "")),
            field="password",
        )
        return attrs

    def validate_reports_to_id(self, value):
        if value is None:
            return None
        organisation = self.context["request"].user.organisation
        if not User.objects.filter(pk=value, organisation=organisation).exists():
            raise serializers.ValidationError("That person isn't in your own organisation.")
        return value

    def validate_email(self, value):
        value = value.lower()
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def validate_role_id(self, value):
        organisation = self.context["request"].user.organisation
        role = organisation.roles.filter(pk=value).first()
        if role is None:
            raise serializers.ValidationError("That role isn't in your own organisation.")
        _validate_grantable(role.permissions or [], self.context["request"].user)
        return value

    def create(self, validated_data):
        organisation = self.context["request"].user.organisation
        role_id = validated_data.get("role_id")
        role = (
            organisation.roles.get(pk=role_id)
            if role_id
            else organisation.ensure_system_roles()[User.Role.CSM]
        )
        return User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
            name=validated_data["name"],
            organisation=organisation,
            role=role,
            function=validated_data.get("function", User.Function.CS),
            reports_to_id=validated_data.get("reports_to_id"),
        )


class MeSerializer(UserSerializer):
    """Your own profile. Read: full profile. Write: `name` only — email
    and role aren't self-editable. See ChangePasswordSerializer for
    passwords.

    Subclasses UserSerializer rather than redeclaring its fields so the
    two can't drift: the frontend hydrates the logged-in user from
    whichever of them answered last (login/signup use UserSerializer,
    the Profile page's own refetch uses this one), and a field missing
    here would silently wipe it from that cached user — `permissions`
    especially, which every capability gate reads."""

    class Meta(UserSerializer.Meta):
        read_only_fields = [
            "id",
            "email",
            "role",
            "role_id",
            "role_name",
            "permissions",
            "is_active",
        ]


class ChangePasswordSerializer(serializers.Serializer):
    """Self-service password change — requires the current password (unlike
    an admin resetting a CSM's password via EditCSMSerializer, which is an
    override and doesn't)."""

    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)

    def validate_current_password(self, value):
        if not self.context["request"].user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def validate_new_password(self, value):
        _check_password_strength(value, self.context["request"].user)
        return value

    def save(self):
        user = self.context["request"].user
        # validated in validate_new_password / validate above
        # nosemgrep: python.django.security.audit.unvalidated-password.unvalidated-password
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
    new_password = serializers.CharField(write_only=True)

    GENERIC_ERROR = "This reset link is invalid or has expired."

    def validate(self, attrs):
        try:
            pk = force_str(urlsafe_base64_decode(attrs["uid"]))
            user = User.objects.get(pk=pk)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            raise serializers.ValidationError(self.GENERIC_ERROR)

        if not default_token_generator.check_token(user, attrs["token"]):
            raise serializers.ValidationError(self.GENERIC_ERROR)

        _check_password_strength(attrs["new_password"], user)
        attrs["user"] = user
        return attrs

    def save(self):
        user = self.validated_data["user"]
        # validated in validate_new_password / validate above
        # nosemgrep: python.django.security.audit.unvalidated-password.unvalidated-password
        user.set_password(self.validated_data["new_password"])
        user.save(update_fields=["password"])
        return user


class EditOrgUserSerializer(serializers.ModelSerializer):
    """Edits a member of the caller's own organisation — name, active
    state, role, or password. `password` is an admin override (no
    current-password check, unlike self-service ChangePasswordSerializer)
    — a manual reset an admin can still reach for even though members
    also have the self-serve ForgotPasswordSerializer flow (e.g. if
    their email account itself is inaccessible).

    `role_id` is what makes role *assignment* real; it's validated
    against the caller's own organisation so an admin can't move
    somebody onto another tenant's role."""

    password = serializers.CharField(write_only=True, required=False)

    def validate_password(self, value):
        _check_password_strength(value, self.instance)
        return value

    role_id = serializers.PrimaryKeyRelatedField(
        source="role", queryset=Role.objects.all(), required=False
    )

    reports_to_id = serializers.PrimaryKeyRelatedField(
        source="reports_to", queryset=User.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = User
        fields = ["name", "is_active", "role_id", "password", "function", "reports_to_id"]

    def validate_reports_to_id(self, manager):
        from .hierarchy import would_cycle

        actor = self.context["request"].user
        if manager is not None and manager.organisation_id != actor.organisation_id:
            raise serializers.ValidationError("That person isn't in your own organisation.")
        if would_cycle(self.instance, manager):
            raise serializers.ValidationError(
                "That would make someone their own manager, directly or through the chain."
            )
        return manager

    def validate_role_id(self, role):
        actor = self.context["request"].user
        if role.organisation_id != actor.organisation_id:
            raise serializers.ValidationError("That role isn't in your own organisation.")
        _validate_grantable(role.permissions or [], actor)
        return role

    def validate(self, attrs):
        """The no-lockout rule: an organisation must always keep at least
        one *active* person who can manage users.

        Both a role change and a deactivation can violate it, so it's
        checked here rather than in either field's own validator —
        strip that last person's access and nobody could ever add a
        member, mint a role, or restore anyone again."""

        new_role = attrs.get("role", self.instance.role)
        will_be_active = attrs.get("is_active", self.instance.is_active)
        still_manages_users = will_be_active and Capability.MANAGE_USERS in (
            new_role.permissions or [] if new_role else []
        )
        if not still_manages_users and self._is_last_user_manager():
            raise serializers.ValidationError(
                "This is the only person who can manage users — give someone else "
                "that permission first."
            )
        return attrs

    def _is_last_user_manager(self):
        others = User.objects.filter(
            organisation_id=self.instance.organisation_id, is_active=True
        ).exclude(pk=self.instance.pk)
        return not any(u.has_capability(Capability.MANAGE_USERS) for u in others)

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        instance = super().update(instance, validated_data)
        if password:
            # validated in validate_password above
            # nosemgrep: python.django.security.audit.unvalidated-password.unvalidated-password
            instance.set_password(password)
            instance.save(update_fields=["password"])
        return instance
