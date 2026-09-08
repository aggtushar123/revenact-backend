from django.db import migrations

# Hardcoded rather than imported from services/accounts/capabilities.py,
# for the same reason 0005_backfill_system_roles duplicates its own list:
# a data migration has to keep meaning what it meant the day it ran.
VIEW_ALL_ACCOUNTS = "view_all_accounts"


def grant_to_system_admin_roles(apps, schema_editor):
    """Give every organisation's built-in Admin role the new capability.

    Without this, adding the key to the Capability enum would silently
    *remove* org-wide visibility from every existing admin: the
    querysets in services/customers/scoping.py narrow to owned records
    unless the caller holds this, and
    `Organisation.ensure_system_roles()` only sets `permissions` on
    creation (it's a get_or_create with `defaults=`), so an Admin row
    that already exists never picks up new keys on its own.

    Deliberately limited to `is_system` Admin roles. A custom role an
    org built by hand is that admin's own explicit set of choices, and
    quietly widening it here would be the migration deciding a
    permissions question on their behalf. Those roles get the new
    checkbox in the Roles tab, unticked, which is the honest default.
    """

    Role = apps.get_model("accounts", "Role")

    for role in Role.objects.filter(is_system=True, slug="admin"):
        permissions = list(role.permissions or [])
        if VIEW_ALL_ACCOUNTS not in permissions:
            permissions.append(VIEW_ALL_ACCOUNTS)
            role.permissions = permissions
            role.save(update_fields=["permissions"])


def revoke_from_system_admin_roles(apps, schema_editor):
    """Reverse: strip the key back out, from custom roles too. On the
    way back down the capability no longer exists in the enum, so
    leaving it in any role's list would be a value nothing can read —
    and the Roles tab would render a checkbox with no label."""

    Role = apps.get_model("accounts", "Role")

    # Filtered in Python rather than with `permissions__contains`: that
    # lookup is a Postgres JSON containment operator wanting a list, not
    # a bare string, and Role is a handful of rows per organisation.
    for role in Role.objects.all():
        permissions = list(role.permissions or [])
        if VIEW_ALL_ACCOUNTS in permissions:
            role.permissions = [p for p in permissions if p != VIEW_ALL_ACCOUNTS]
            role.save(update_fields=["permissions"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0006_swap_user_role_to_fk"),
    ]

    operations = [
        migrations.RunPython(grant_to_system_admin_roles, revoke_from_system_admin_roles),
    ]
