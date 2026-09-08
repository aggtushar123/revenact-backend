from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils.text import slugify


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
    ai_agent_enabled = models.BooleanField(default=True)
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

    email = models.EmailField(unique=True)
    name = models.CharField(max_length=255)
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
        convention already grants them every permission."""

        if self.is_superuser:
            return True
        if self.role_id is None:
            return False
        return self.role.has_capability(capability)
