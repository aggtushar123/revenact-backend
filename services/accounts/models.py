from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils.text import slugify

#: Settings > Data's global configuration: each headline concept and the
#: customer fields that may stand for it. The defaults are what the
#: dashboards have always used.
GLOBAL_ATTRIBUTE_CHOICES = {
    "arr": ("arr_billed_at_account", "arr_billed_at_hq", "total_contract_value"),
    "mrr": ("arr_billed_at_account", "arr_billed_at_hq", "total_contract_value"),
    "renewal_date": ("renewal_date", "contract_end_date"),
    "joined_date": ("joined_date", "contract_start_date", "created_at"),
}
GLOBAL_ATTRIBUTE_DEFAULTS = {
    "arr": "arr_billed_at_account",
    "mrr": "arr_billed_at_account",
    "renewal_date": "renewal_date",
    "joined_date": "joined_date",
}


class Organisation(models.Model):
    """A tenant. Every non-superuser User belongs to exactly one of these —
    the org that signed up and everything it does is scoped underneath it.

    `currency`/`default_lifecycle_stage` back the frontend's Settings >
    Currency / Global Presets tabs (react-ts-app's src/pages/settings/
    CurrencyPage.tsx / GlobalPresetsPage.tsx) — see OrganisationSettingsView
    for the endpoint. Both are tenant-wide, admin-only-to-change settings;
    everything else about this model stays as it was.

    `currency` is this tenant's own reporting currency — every money
    value the frontend renders is formatted with it (see
    react-ts-app's src/features/customers/formatters.ts). Individual
    `customers.Customer` rows can carry their own, different contract
    currency (see that model's own docstring); cross-currency rollups
    convert into *this* field's value via the admin-maintained
    `services.fx_rates` table. Changing this clears every existing
    `FxRate` for this org (see OrganisationSettingsView.perform_update)
    — a stored rate's meaning doesn't carry over to a new base currency.

    `default_lifecycle_stage` isn't a hard FK/enum tie to
    `customers.Customer.LifecycleStage` — duplicating that small, stable
    set of string values here avoids making this lower-level tenant model
    depend on a business-domain model layered on top of it (Customer
    already depends on Organisation, not the other way around). Blank
    means "no override" — the standalone Add Organization flow (see
    OrganizationFormModal.tsx) falls back to its own hardcoded "onboarding"
    when this is unset, same as before this field existed.

    `ai_agent_enabled`/`ai_agent_tone` back Settings > AI Agent
    (AIAgentPage.tsx) — same real-but-not-yet-consumed pattern as
    `currency`: genuinely stored and shown back, but Copilot
    (react-ts-app's src/pages/copilot/) has no backend of its own at all
    yet (see docs/API_CONTRACTS.md's Status table), so nothing reads
    these two back out. Wiring them into actual Copilot behavior is
    real work of its own, same deliberate boundary as currency's own."""

    class Currency(models.TextChoices):
        USD = "USD", "US Dollar ($)"
        EUR = "EUR", "Euro (€)"
        GBP = "GBP", "British Pound (£)"
        INR = "INR", "Indian Rupee (₹)"
        CAD = "CAD", "Canadian Dollar (C$)"
        AUD = "AUD", "Australian Dollar (A$)"
        JPY = "JPY", "Japanese Yen (¥)"

    class Status(models.TextChoices):
        """Whether the tenant may be used.

        Checked at authorization time rather than by editing every membership:
        suspending a company for non-payment must not require touching a
        thousand rows, and must not be undone by a stale membership somewhere.
        """

        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        ARCHIVED = "archived", "Archived"

    class AgentTone(models.TextChoices):
        PROFESSIONAL = "professional", "Professional"
        FRIENDLY = "friendly", "Friendly"
        CONCISE = "concise", "Concise"

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.USD)
    default_lifecycle_stage = models.CharField(
        max_length=16,
        blank=True,
        default="",
        help_text="One of Customer.LifecycleStage's own values, or blank "
        "for no tenant-wide default (see this model's own docstring).",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        help_text="Existing tenants are active; nothing reads this yet (phase 1).",
    )
    ai_agent_enabled = models.BooleanField(default=True)
    #: The global configuration card (Settings > Data): which customer
    #: attribute stands for each headline concept across the organisation —
    #: "ARR" means arr_billed_at_account here, "Renewal date" means
    #: renewal_date. Keys are fixed (GLOBAL_ATTRIBUTE_KEYS); values are
    #: customer field names from GLOBAL_ATTRIBUTE_CHOICES. Stored so every
    #: screen can read one answer instead of hard-coding its own.
    global_attributes = models.JSONField(default=dict, blank=True)
    ai_agent_tone = models.CharField(
        max_length=16, choices=AgentTone.choices, default=AgentTone.PROFESSIONAL
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._generate_unique_slug()
        super().save(*args, **kwargs)

    def _generate_unique_slug(self):
        base = slugify(self.name)
        slug = base
        n = 1
        while Organisation.objects.filter(slug=slug).exists():
            n += 1
            slug = f"{base}-{n}"
        return slug

    def ensure_system_roles(self) -> dict:
        """This organisation's two built-in Roles, keyed by slug —
        created if they don't exist yet, so this is safe to call any
        number of times.

        The single place those two rows are defined: signup calls it
        for a brand-new org, UserManager._resolve_role calls it when
        resolving a role slug, and the migration that introduced Role
        called it to backfill every organisation that already existed.
        Admin holds every capability and CSM holds none, which is
        exactly what the two hardcoded roles meant before this."""

        from .capabilities import ALL_CAPABILITIES

        admin, _ = self.roles.get_or_create(
            slug=User.Role.ADMIN,
            defaults={"name": "Admin", "permissions": ALL_CAPABILITIES, "is_system": True},
        )
        csm, _ = self.roles.get_or_create(
            slug=User.Role.CSM,
            defaults={"name": "CSM", "permissions": [], "is_system": True},
        )
        return {admin.slug: admin, csm.slug: csm}

    def effective_global_attributes(self):
        """The stored mapping over the defaults, so a screen always gets every key."""
        return {**GLOBAL_ATTRIBUTE_DEFAULTS, **(self.global_attributes or {})}

    def __str__(self):
        return self.name


class Role(models.Model):
    """One organisation's own named bundle of capabilities — what an org
    admin actually creates and maintains on the Users page.

    Every organisation gets two `is_system` roles at signup (see
    Organisation.ensure_system_roles): **Admin**, holding every
    capability, and **CSM**, holding none — exactly reproducing the
    two hardcoded roles this model replaced, so behaviour didn't change
    for anyone when it landed. System roles can't be renamed, have
    their capabilities changed, or be deleted: Admin is the only thing
    standing between an org and locking itself out, and CSM is the
    default every new member falls back to.

    `permissions` is a plain list of `capabilities.Capability` values
    rather than a M2M to a Permission table: the set is closed, small,
    and defined in code (an unenforced capability row would be
    meaningless), so there's nothing to normalise and nothing to query
    across. Same reasoning as CustomFieldDefinition.picklist_options.
    """

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "slug"], name="unique_role_slug_per_organisation"
            )
        ]
        ordering = ["name"]

    organisation = models.ForeignKey(Organisation, related_name="roles", on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100)
    permissions = models.JSONField(default=list, blank=True)
    is_system = models.BooleanField(
        default=False,
        help_text="The built-in Admin/CSM roles — not renamable, editable, or deletable.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.organisation_id})"

    def has_capability(self, capability) -> bool:
        return capability in (self.permissions or [])


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address")
        email = self.normalize_email(email)
        extra_fields["role"] = self._resolve_role(
            extra_fields.get("role"), extra_fields.get("organisation")
        )
        user = self.model(email=email, **extra_fields)
        # callers validate first (serializers._check_password_strength); seeds use fixtures
        # nosemgrep: python.django.security.audit.unvalidated-password.unvalidated-password
        user.set_password(password)
        user.save(using=self._db)
        return user

    def _resolve_role(self, role, organisation):
        """Accept a real Role, a role *slug* string, or nothing.

        The slug-string form is what keeps every existing
        `create_user(..., role=User.Role.ADMIN)` call site working
        unchanged now that `role` is a ForeignKey — `User.Role.ADMIN`
        is the string "admin", which resolves here to that
        organisation's own real Admin row.

        Omitting `role` entirely falls back to that organisation's CSM
        role, preserving the `default=Role.CSM` the old CharField had.
        Superusers pass no organisation and so keep `role=None`;
        they're platform staff, not members of any tenant (see
        User.has_capability, which grants them everything anyway)."""

        if isinstance(role, Role):
            return role
        if organisation is None:
            return None
        system_roles = organisation.ensure_system_roles()
        if role is None:
            return system_roles[User.Role.CSM]
        return system_roles.get(str(role))

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        # Platform-staff account, not scoped to any customer organisation.
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self._create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """A person who logs in — a member of exactly one Organisation,
    holding exactly one of that organisation's own Roles."""

    class Role:
        """The two `is_system` role slugs every organisation has.

        Not a TextChoices on a CharField any more — `User.role` is a
        real ForeignKey to the Role model above. These constants stay
        because they're the slugs those built-in rows carry, and
        because passing one to `create_user(role=...)` still resolves
        to the right row (see UserManager._resolve_role)."""

        ADMIN = "admin"
        CSM = "csm"

    class Function(models.TextChoices):
        """Which part of the company someone works in. Not a permission —
        roles carry those — but what stamps their contributions and what
        "the responsible person" is looked up by (see services.knowledge)."""

        CS = "cs", "Customer Success"
        ENGINEERING = "engineering", "Engineering"
        SALES = "sales", "Sales"
        ANALYTICS = "analytics", "Analytics"
        LEADERSHIP = "leadership", "Leadership"
        OTHER = "other", "Other"

    email = models.EmailField(unique=True)
    name = models.CharField(max_length=255)
    function = models.CharField(max_length=16, choices=Function.choices, default=Function.CS)
    reports_to = models.ForeignKey(
        "self",
        related_name="reports",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Their manager — the org chart. What a person may see of what others "
        "say is read from it (services.accounts.hierarchy).",
    )
    organisation = models.ForeignKey(
        Organisation,
        related_name="members",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Null only for platform-staff superusers; every org member has one.",
    )
    # A string reference, not the `Role` model object: the inner `Role`
    # constants class above shadows that name inside this class body.
    role = models.ForeignKey(
        "accounts.Role",
        related_name="users",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        help_text="Null only for platform-staff superusers; every org member has one.",
    )
    is_active = models.BooleanField(default=True)
    # When they finished (or skipped) the first-run tour. Server-side so it
    # follows the person across browsers, and so nothing in localStorage
    # decides what a new device shows.
    tour_completed_at = models.DateTimeField(null=True, blank=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    def __str__(self):
        return self.email

    def has_capability(self, capability) -> bool:
        """The single authorization question this codebase asks — see
        permissions.py, which turns it into DRF permission classes.

        Platform superusers get everything: they're Django-admin staff
        with no organisation and so no Role to read, and Django's own
        convention already grants them every permission.

        Everyone else resolves through `services.identity.context`, which
        reads the role from their membership in the tenant they're acting
        in, and returns nothing at all when that membership or that tenant
        is not active. Against today's data that is the same answer this
        used to give from `self.role` directly — see
        `manage.py check_membership_consistency`, which proves it — but it
        makes "this person is suspended" and "this customer is suspended"
        answerable without rewriting either of their rows."""

        if self.is_superuser:
            return True

        # Imported here, not at module scope: services.identity.models
        # imports this module, so a top-level import would be circular.
        from services.identity.context import capabilities_for

        return capability in capabilities_for(self)


class TOTPDevice(models.Model):
    """One authenticator app per person, for the second factor.

    `secret_encrypted` is Fernet at rest (see services.mail.crypto); nothing
    here is ever serialised. `confirmed_at` is null until the person proves
    the app works, and an unconfirmed device grants nothing. `last_counter`
    is the replay guard: a code is accepted once. `recovery_codes` holds
    SHA-256 digests only; the plain codes are shown exactly once, at
    enrolment. See services.accounts.mfa for every operation.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, related_name="totp_device", on_delete=models.CASCADE
    )
    secret_encrypted = models.TextField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    last_counter = models.BigIntegerField(default=-1)
    recovery_codes = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        state = "confirmed" if self.confirmed_at else "pending"
        return f"TOTP for {self.user_id} ({state})"
