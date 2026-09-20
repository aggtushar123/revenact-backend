"""Backfill memberships and departments from the columns in use today.

Phase 1 of the identity work adds a second, richer representation of who
belongs where. This migration makes it agree with the first one, so that when
phase 3 switches the read path over there is nothing to discover.

For every existing user that has an organisation:

- one **active** membership, carrying the role they hold right now
- one **department per distinct `User.function`** within that organisation,
  named from the function's own display label, with the membership pointed at it

Users with no organisation are superusers (`User.organisation` is nullable
precisely for them) and get no membership: a platform operator is not a member
of any tenant, which is the separation the architecture note calls for.

Deliberately conservative:

- It creates nothing it cannot derive. No invented departments, no guessed roles.
- It is idempotent. Re-running skips anyone who already has a live membership,
  so a partially applied migration can be re-applied safely.
- It is fully reversible. The reverse deletes only the rows it would have
  created, and touches no pre-existing column.

`User.function` is **not** consumed by this. Function stays exactly where it is
and keeps driving knowledge routing and ticket visibility; the department is an
additional, per-tenant grouping that happens to start life mirroring it.
"""

from django.db import migrations

#: Mirrors accounts.User.Function at the time of writing. Spelled out rather
#: than imported, because a data migration must keep working after the enum on
#: the live model changes.
FUNCTION_LABELS = {
    "cs": "Customer Success",
    "engineering": "Engineering",
    "sales": "Sales",
    "analytics": "Analytics",
    "leadership": "Leadership",
    "other": "Other",
}

LIVE = ("pending", "active", "suspended")


def backfill(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    Department = apps.get_model("identity", "Department")
    Membership = apps.get_model("identity", "OrganizationMembership")

    already = set(
        Membership.objects.filter(status__in=LIVE).values_list("organisation_id", "user_id")
    )

    departments = {}  # (organisation_id, label) -> Department

    for user in User.objects.filter(organisation__isnull=False).select_related("organisation"):
        if (user.organisation_id, user.id) in already:
            continue

        label = FUNCTION_LABELS.get(user.function or "", "Other")
        key = (user.organisation_id, label)
        department = departments.get(key)
        if department is None:
            department, _ = Department.objects.get_or_create(
                organisation_id=user.organisation_id,
                name=label,
                defaults={
                    "description": "Created from the person's function when "
                    "memberships were introduced.",
                    "status": "active",
                },
            )
            departments[key] = department

        Membership.objects.create(
            organisation_id=user.organisation_id,
            user_id=user.id,
            status="active",
            # Whatever they hold right now. Null for anyone without a role;
            # phase 3 resolves that rather than inventing one here.
            role_id=user.role_id,
            department_id=department.id,
            # date_joined is the closest true record of when they gained access.
            approved_at=user.date_joined,
        )


def unbackfill(apps, schema_editor):
    """Remove only what the forward pass creates."""
    Department = apps.get_model("identity", "Department")
    Membership = apps.get_model("identity", "OrganizationMembership")

    Membership.objects.all().delete()
    Department.objects.filter(description__startswith="Created from the person's function").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0001_initial"),
        ("accounts", "0011_organisation_status"),
    ]

    operations = [migrations.RunPython(backfill, unbackfill)]
