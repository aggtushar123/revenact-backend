"""Identity and tenancy: who someone is, and which organisations they belong to.

Today a person *is* their membership: `accounts.User` carries `organisation`
and `role` directly, so identity, tenancy and authorization sit on one row. That
works while everyone belongs to exactly one company forever, and stops working
the moment a consultant works for two, or someone's access is revoked and later
restored, or the same human signs in through two providers.

These four models pull those concerns apart, following the separation the
architecture note (`docs/product/07-multi-tenant-identity-and-billing.md`) sets
out:

    Identity  ──▶ User ──▶ OrganizationMembership ──▶ Organisation
    (how they         (the        (their standing         (the tenant)
     signed in)        person)     in one tenant)              │
                                        │                      │
                                     Department        OrganizationDomain
                                     Role (existing)

**Nothing reads these yet.** Phase 1 adds the tables and backfills them from the
columns in use today, so the two representations agree before anything switches
over. `user.organisation` remains the live path until phase 3.

What each one is for:

- `Identity` — one external account (a Google or Microsoft login) belonging to
  one `User`. Separate from `User` so a person can sign in through either and
  still be one person, and so a future SAML or OIDC provider is a new row rather
  than a new column.
- `Department` — a part of one organisation. Not a permission: `Engineering`
  must not imply `production.deploy`. Permissions come from `Role`, which
  already exists and is unchanged.
- `OrganizationMembership` — a person's standing *in one tenant*: their status,
  their role there, their department there. The row that makes belonging to two
  companies expressible.
- `OrganizationDomain` — a domain an organisation has proven it controls.
  Unverified domains map nobody: typing "accenture.com" is not evidence of
  owning Accenture.
"""

from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from services.accounts.models import Organisation, Role


class Identity(models.Model):
    """One external login belonging to one person.

    Keyed on `(provider, provider_user_id)` rather than on the email address,
    because an email can be reassigned inside a company while the provider's
    subject identifier cannot. Someone who changes their surname keeps the same
    identity; someone who inherits a departed colleague's address does not
    inherit their account.

    `email` is stored alongside as a *snapshot for matching and display*, always
    lowercased. It is never the join key.
    """

    class Provider(models.TextChoices):
        GOOGLE = "google", "Google"
        MICROSOFT = "microsoft", "Microsoft"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="identities",
        on_delete=models.CASCADE,
    )
    provider = models.CharField(max_length=32, choices=Provider.choices)
    provider_user_id = models.CharField(
        max_length=255,
        help_text="The provider's stable subject identifier, not the email address.",
    )
    email = models.EmailField(
        help_text="Lowercased snapshot of the address the provider asserted.",
    )
    email_verified = models.BooleanField(
        default=False,
        help_text="Whether the provider asserted the address as verified. An "
        "unverified address must never be used to map someone to an organisation.",
    )
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "identities"
        ordering = ["provider", "email"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_user_id"],
                name="identity_unique_per_provider_subject",
            )
        ]
        indexes = [models.Index(fields=["email"])]

    def __str__(self):
        return f"{self.get_provider_display()}: {self.email}"

    def save(self, *args, **kwargs):
        # Normalised on the way in, so every lookup can compare directly rather
        # than remembering to lower() at each call site.
        self.email = (self.email or "").strip().lower()
        super().save(*args, **kwargs)


class Department(models.Model):
    """A part of one organisation.

    Deliberately **not** a source of permissions. Being in Engineering says
    where someone sits, not what they may do; `accounts.Role` carries that and
    is unchanged by this work.

    Distinct from `User.function`, which is a fixed six-value enum shared across
    every tenant and drives knowledge routing and ticket visibility. Function
    stays exactly as it is. A department is per organisation and free-form, so
    one company can have "Platform" and "Revenue Ops" without every other
    company inheriting them.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    organisation = models.ForeignKey(
        Organisation, related_name="departments", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            # Case-insensitive, matching how `customers.Product` already scopes
            # its own name: "Engineering" and "engineering" are one department.
            models.UniqueConstraint(
                Lower("name"),
                "organisation",
                name="department_name_unique_per_organisation_ci",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.organisation.name})"


#: Membership statuses that occupy a person's one live slot in a tenant.
#: Module level so `Meta.constraints` can reference it during class creation.
LIVE_MEMBERSHIP_STATUSES = ("pending", "active", "suspended")


class OrganizationMembership(models.Model):
    """A person's standing inside one organisation.

    This is the row that makes the same `User` able to belong to two companies
    with a different role in each, and the row that carries a lifecycle:
    requested, approved, suspended, revoked. `accounts.User.organisation` can
    express none of that.

    The role and department must belong to the same organisation as the
    membership. There is no database constraint that can span three tables to
    say so, so `clean()` enforces it and `save()` calls `clean()` — the same
    belt-and-braces `knowledge.FunctionOwner` already uses for its own
    cross-model rule.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        REJECTED = "rejected", "Rejected"
        REVOKED = "revoked", "Revoked"

    #: Statuses that occupy the one live membership slot per person per tenant.
    #: Rejected and revoked rows are history and may accumulate. Defined at
    #: module level too (LIVE_MEMBERSHIP_STATUSES) because a nested `Meta`
    #: cannot see its enclosing class's attributes while the class is built.
    LIVE_STATUSES = LIVE_MEMBERSHIP_STATUSES

    organisation = models.ForeignKey(
        Organisation, related_name="memberships", on_delete=models.CASCADE
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="memberships", on_delete=models.CASCADE
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    # The one person the organisation ultimately belongs to: billing contact,
    # un-demotable by other administrators, transferable only by themselves
    # or by platform staff. Exactly one per organisation (partial unique).
    is_owner = models.BooleanField(default=False)
    role = models.ForeignKey(
        "accounts.Role",
        related_name="memberships",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        help_text="What they may do here. Null until approved.",
    )
    department = models.ForeignKey(
        Department,
        related_name="memberships",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="+", on_delete=models.SET_NULL, null=True, blank=True
    )
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="+", on_delete=models.SET_NULL, null=True, blank=True
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="+", on_delete=models.SET_NULL, null=True, blank=True
    )

    class Meta:
        ordering = ["organisation__name", "user__name"]
        constraints = [
            # One *live* membership per person per tenant. History rows
            # (rejected, revoked) are allowed to pile up, so someone who leaves
            # and rejoins keeps an auditable trail rather than overwriting it.
            models.UniqueConstraint(
                fields=["organisation", "user"],
                condition=models.Q(status__in=LIVE_MEMBERSHIP_STATUSES),
                name="one_live_membership_per_user_per_organisation",
            ),
            # One owner per organisation.
            models.UniqueConstraint(
                fields=["organisation"],
                condition=models.Q(is_owner=True),
                name="one_owner_per_organisation",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["organisation", "status"]),
        ]

    def __str__(self):
        return f"{self.user.email} @ {self.organisation.name} ({self.status})"

    def clean(self):
        # A role or department from another tenant would be a cross-tenant
        # privilege leak, so it is refused rather than quietly accepted.
        if self.role_id and self.role.organisation_id != self.organisation_id:
            raise ValidationError({"role": "That role belongs to a different organisation."})
        if self.department_id and self.department.organisation_id != self.organisation_id:
            raise ValidationError(
                {"department": "That department belongs to a different organisation."}
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    @property
    def is_live(self) -> bool:
        """Whether this membership occupies the person's slot in this tenant."""
        return self.status in self.LIVE_STATUSES


class OrganizationDomain(models.Model):
    """A domain an organisation claims, and may have proven it controls.

    **Verification is the point.** A domain maps nobody to an organisation until
    `verification_status` is `verified`. Typing a domain into a form is a claim,
    not evidence; proving control of DNS is evidence. The token below is what a
    company publishes as a TXT record for that proof, and it is generated
    server-side.

    **Claims may overlap; ownership may not.** Several organisations can hold a
    `pending` claim on `accenture.com` (an employee who started a workspace, and
    later the company itself), but only one can hold it `verified`, because a
    verified domain is what routes sign-ins and routing must never be ambiguous.
    Verifying supersedes every other claim on that domain (see
    `domains.verify`), so whoever controls DNS always wins, and an earlier
    unverified claim can never stand in their way.
    """

    class VerificationStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        VERIFIED = "verified", "Verified"
        REVOKED = "revoked", "Revoked"

    organisation = models.ForeignKey(Organisation, related_name="domains", on_delete=models.CASCADE)
    domain = models.CharField(max_length=253, db_index=True)
    is_primary = models.BooleanField(default=False)
    verification_status = models.CharField(
        max_length=16, choices=VerificationStatus.choices, default=VerificationStatus.PENDING
    )
    verification_token = models.CharField(
        max_length=64,
        blank=True,
        help_text="Published by the company as a DNS TXT record to prove control.",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["domain"]
        constraints = [
            # At most one primary domain per organisation.
            models.UniqueConstraint(
                fields=["organisation"],
                condition=models.Q(is_primary=True),
                name="one_primary_domain_per_organisation",
            ),
            # One claim per organisation per domain: re-adding is an update.
            models.UniqueConstraint(
                fields=["organisation", "domain"],
                name="one_claim_per_organisation_per_domain",
            ),
            # Only one organisation can have *proved* a domain, because a
            # verified domain routes sign-ins and routing cannot be ambiguous.
            models.UniqueConstraint(
                fields=["domain"],
                condition=models.Q(verification_status="verified"),
                name="one_verified_holder_per_domain",
            ),
        ]

    def __str__(self):
        return f"{self.domain} ({self.verification_status})"

    def save(self, *args, **kwargs):
        self.domain = (self.domain or "").strip().lower().rstrip(".")
        super().save(*args, **kwargs)

    @property
    def is_verified(self) -> bool:
        return self.verification_status == self.VerificationStatus.VERIFIED


class AccessRequest(models.Model):
    """Somebody who signed in with a corporate address and has no membership.

    The point is that authenticating is not joining. Proving you control
    `john@accenture.com` proves you work at Accenture; it does not prove
    Accenture wants you in their Revenact tenant, and it certainly does not say
    which role or department. An administrator there decides that.

    So a request holds the person in a waiting state: they have a `User` and a
    verified `Identity`, and no membership at all, which means
    `capabilities_for` returns nothing and every scoped queryset finds nothing.
    There is no partial access to leak through.

    **A pending request consumes no seat.** Twenty people can be waiting while
    the company has one seat left; the seat is taken at approval, which is where
    the check belongs. That rule is load-bearing for billing and is asserted in
    the tests.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    #: Statuses still awaiting a decision, and so holding the one open slot.
    OPEN_STATUSES = (Status.PENDING,)

    organisation = models.ForeignKey(
        Organisation, related_name="access_requests", on_delete=models.CASCADE
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="access_requests", on_delete=models.CASCADE
    )
    email = models.EmailField(
        help_text="The verified address that mapped to this organisation, as it was at the time."
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)

    requested_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="+", on_delete=models.SET_NULL, null=True, blank=True
    )
    rejection_reason = models.CharField(max_length=500, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-requested_at", "-id"]
        constraints = [
            # One open request per person per tenant: signing in again while
            # waiting must not queue a second row for an admin to wade through.
            models.UniqueConstraint(
                fields=["organisation", "user"],
                condition=models.Q(status="pending"),
                name="one_open_access_request_per_user_per_organisation",
            )
        ]
        indexes = [models.Index(fields=["organisation", "status"])]

    def __str__(self):
        return f"{self.email} -> {self.organisation.name} ({self.status})"


class Invitation(models.Model):
    """An administrator asked someone in, by address, before they ever signed in.

    The other half of joining: access requests are the person asking, an
    invitation is the company asking. It names the role and department up
    front, so acceptance needs no second decision.

    **Acceptance is keyed on the verified address, not on a link.** The person
    signs in with a provider that vouches for `email`; if a pending invitation
    names that exact address, it is accepted then and there and they hold a
    membership. There is no token to forward to somebody else, so the address
    in the invitation is the only thing that can redeem it — which is what
    `INVITATION_EMAIL_MISMATCH` means in the architecture note: a different
    verified address simply is not invited.

    **An invitation consumes no seat.** As with requests, the seat is taken at
    acceptance, which is where the check belongs.

    Invitations expire, because an address that has not been used in a week
    may have been typed wrongly, and a standing open door is a liability.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    #: How long an invitation stays open.
    TTL_DAYS = 7

    organisation = models.ForeignKey(
        Organisation, related_name="invitations", on_delete=models.CASCADE
    )
    email = models.EmailField(help_text="Lowercased. The one address that can accept this.")
    role = models.ForeignKey(Role, related_name="invitations", on_delete=models.PROTECT)
    department = models.ForeignKey(
        Department, related_name="invitations", on_delete=models.SET_NULL, null=True, blank=True
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)

    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="+", on_delete=models.SET_NULL, null=True, blank=True
    )
    invited_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="+", on_delete=models.SET_NULL, null=True, blank=True
    )

    class Meta:
        ordering = ["-invited_at", "-id"]
        constraints = [
            # One open invitation per address per tenant: inviting twice is a
            # reminder, not a second row.
            models.UniqueConstraint(
                fields=["organisation", "email"],
                condition=models.Q(status="pending"),
                name="one_open_invitation_per_email_per_organisation",
            )
        ]
        indexes = [models.Index(fields=["email", "status"])]

    def __str__(self):
        return f"{self.email} <- {self.organisation.name} ({self.status})"

    def clean(self):
        if self.role_id and self.role.organisation_id != self.organisation_id:
            raise ValidationError({"role": "The role must belong to the same organisation."})
        if self.department_id and self.department.organisation_id != self.organisation_id:
            raise ValidationError(
                {"department": "The department must belong to the same organisation."}
            )

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(days=self.TTL_DAYS)
        self.full_clean(exclude=["accepted_by", "invited_by"])
        super().save(*args, **kwargs)

    @property
    def is_open(self) -> bool:
        return self.status == self.Status.PENDING and self.expires_at > timezone.now()
