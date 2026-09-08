from django.db import migrations

# Duplicated from services/accounts/capabilities.py on purpose: a data
# migration has to keep meaning what it meant the day it ran, so it must
# not import a live module whose contents can change later. If a
# capability is added to that file tomorrow, this list stays as it is —
# and existing Admin roles keep exactly the permissions they were
# granted here, which is the correct historical behaviour.
CAPABILITIES_AT_TIME_OF_MIGRATION = [
    "manage_users",
    "manage_org_settings",
    "manage_custom_objects",
    "manage_integrations",
    "manage_fx_rates",
]


def create_system_roles_and_link_users(apps, schema_editor):
    """Give every existing organisation its two system roles and point
    every existing user at the one matching their old `role` string.

    Admin gets every capability and CSM gets none — which reproduces
    exactly what the old two-value field meant, so nobody's access
    changes the moment this runs."""

    Organisation = apps.get_model("accounts", "Organisation")
    Role = apps.get_model("accounts", "Role")
    User = apps.get_model("accounts", "User")

    for organisation in Organisation.objects.all():
        admin_role, _ = Role.objects.get_or_create(
            organisation=organisation,
            slug="admin",
            defaults={
                "name": "Admin",
                "permissions": CAPABILITIES_AT_TIME_OF_MIGRATION,
                "is_system": True,
            },
        )
        csm_role, _ = Role.objects.get_or_create(
            organisation=organisation,
            slug="csm",
            defaults={"name": "CSM", "permissions": [], "is_system": True},
        )
        by_slug = {"admin": admin_role, "csm": csm_role}

        for user in User.objects.filter(organisation=organisation):
            # Anything unrecognised falls back to the least-privileged
            # role rather than silently becoming an admin.
            user.role_ref = by_slug.get(user.role, csm_role)
            user.save(update_fields=["role_ref"])


def unlink_users(apps, schema_editor):
    """Reverse: drop the links. The old `role` string column is still
    present at this point in history (0006 removes it), so reversing
    this loses nothing."""

    User = apps.get_model("accounts", "User")
    User.objects.update(role_ref=None)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_role_and_user_role_ref"),
    ]

    operations = [
        migrations.RunPython(create_system_roles_and_link_users, unlink_users),
    ]
