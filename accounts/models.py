from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils.text import slugify


class Organisation(models.Model):
    """A tenant. Every non-superuser User belongs to exactly one of these —
    the org that signed up and everything it does is scoped underneath it."""

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
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

    def __str__(self):
        return self.name


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        # Platform-staff account, not scoped to any customer organisation.
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", User.Role.ADMIN)
        return self._create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """A person who logs in — either the organisation's admin (created at
    signup) or a CSM the admin has added to that same organisation."""

    class Role(models.TextChoices):
        ADMIN = "admin", "Organisation Admin"
        CSM = "csm", "Customer Success Manager"

    email = models.EmailField(unique=True)
    name = models.CharField(max_length=255)
    organisation = models.ForeignKey(
        Organisation,
        related_name="members",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Null only for platform-staff superusers; every org admin/CSM has one.",
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CSM)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    def __str__(self):
        return self.email

    @property
    def is_org_admin(self):
        return self.role == self.Role.ADMIN
