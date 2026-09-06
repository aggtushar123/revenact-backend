from django.conf import settings
from django.db import models

from services.accounts.models import Organisation
from services.customers.models import Account, Customer


class CustomObjectDefinition(models.Model):
    """An org admin's own declared object type — the SFDC "custom
    object" pattern: "Opportunity Line Item", "Contract Clause",
    anything an admin invents, each with its own admin-defined fields
    (see CustomFieldDefinition) and its own records (see
    CustomObjectRecord), with no code change or migration needed to add
    another one later.

    Scoped to exactly one Organisation (tenant) — one org's own custom
    objects are invisible to every other org, same isolation as every
    other tenant-scoped model in this codebase.

    `applies_to_customer`/`applies_to_account` say which parent
    type(s) this object's records may attach to (see
    CustomObjectRecord's own "exactly one of customer/account"
    constraint) — at least one must be true (see this model's own
    CheckConstraint). An object can apply to both, e.g. an "Opportunity
    Line Item" style object useful whether the deal lives at the
    organization level or one specific account's."""

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "api_name"], name="unique_custom_object_api_name_per_org"
            ),
            models.CheckConstraint(
                check=models.Q(applies_to_customer=True) | models.Q(applies_to_account=True),
                name="custom_object_applies_to_at_least_one_parent_type",
            ),
        ]
        ordering = ["name"]

    organisation = models.ForeignKey(
        Organisation, related_name="custom_object_definitions", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=255)
    api_name = models.SlugField(max_length=255)
    applies_to_customer = models.BooleanField(default=True)
    applies_to_account = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="+", on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.organisation_id})"


class CustomFieldDefinition(models.Model):
    """One admin-defined field on a CustomObjectDefinition — the "no
    code change needed" part of this feature: the six `field_type`
    choices are the closed set this codebase's own record-validation
    (CustomObjectRecordSerializer) and the frontend's dynamic form
    renderer both know how to handle, but which *fields* an object has
    is entirely admin-declared, not hardcoded per object the way every
    other model in this codebase is.

    `picklist_options` only means something when `field_type ==
    PICKLIST` — validated in the serializer (a plain CharField list
    stored as JSON, not a separate child table; there's no per-org
    reuse of option sets asked for, so this stays as simple as
    Opportunity.Stage's own fixed choices, just admin-defined instead
    of hardcoded).

    `order` fixes display/column order across the record table and the
    add/edit form — assigned as this object's current max + 1 at
    creation (see the serializer), same "append order, no manual
    drag-reorder for v1" scope as everywhere else fields are listed in
    this codebase."""

    class FieldType(models.TextChoices):
        TEXT = "text", "Text"
        NUMBER = "number", "Number"
        CURRENCY = "currency", "Currency"
        DATE = "date", "Date"
        BOOLEAN = "boolean", "Yes / No"
        PICKLIST = "picklist", "Picklist"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["object_definition", "api_name"],
                name="unique_custom_field_api_name_per_object",
            ),
        ]
        ordering = ["order", "id"]

    object_definition = models.ForeignKey(
        CustomObjectDefinition, related_name="fields", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=255)
    api_name = models.SlugField(max_length=255)
    field_type = models.CharField(max_length=16, choices=FieldType.choices)
    is_required = models.BooleanField(default=False)
    picklist_options = models.JSONField(default=list, blank=True)
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.object_definition.name} · {self.name}"


class CustomObjectRecord(models.Model):
    """One real instance of a CustomObjectDefinition, hanging off
    exactly one Customer or Account — same "belongs to exactly one of
    customer/account" shape (two nullable FKs + a CheckConstraint) as
    Contact/Activity/Email/Task/Opportunity/Risk/... elsewhere in this
    codebase, not a new pattern invented for this feature.

    `data` holds every CustomFieldDefinition's own value for this
    record, keyed by that field's `api_name` — e.g. `{"product":
    "Seat License", "qty": 50, "price": "12000.00"}`. A JSON blob
    rather than one child row per field-value: an object's own fields
    are admin-defined and can change over time, so a fixed set of
    typed columns isn't possible without a migration per field (the
    exact problem this feature exists to avoid), and a real per-value
    child table would mean N extra rows per record for no query this
    app actually needs to run. Validated against the definition's own
    `fields` in CustomObjectRecordSerializer, not at the database
    layer — Postgres JSONField can't express "this key is required,
    this one must be numeric" itself."""

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(customer__isnull=False, account__isnull=True)
                    | models.Q(customer__isnull=True, account__isnull=False)
                ),
                name="custom_object_record_belongs_to_exactly_one_parent",
            )
        ]
        ordering = ["-created_at"]

    object_definition = models.ForeignKey(
        CustomObjectDefinition, related_name="records", on_delete=models.CASCADE
    )
    customer = models.ForeignKey(
        Customer,
        related_name="custom_object_records",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    account = models.ForeignKey(
        Account,
        related_name="custom_object_records",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    data = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="+", on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        parent = self.customer or self.account
        return f"{self.object_definition.name} — {parent}"
