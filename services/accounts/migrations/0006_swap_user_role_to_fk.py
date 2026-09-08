import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Step 3 of 3: now that 0005 has copied every user's role into
    `role_ref`, drop the old string column and rename the FK into its
    place, so the field is simply `User.role` again — just a real
    ForeignKey this time."""

    dependencies = [
        ("accounts", "0005_backfill_system_roles"),
    ]

    operations = [
        migrations.RemoveField(model_name="user", name="role"),
        migrations.RenameField(model_name="user", old_name="role_ref", new_name="role"),
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.ForeignKey(
                blank=True,
                help_text=("Null only for platform-staff superusers; every org member has one."),
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="users",
                to="accounts.role",
            ),
        ),
        # Wording only — "every org admin/CSM" no longer describes the
        # membership now that roles are org-defined.
        migrations.AlterField(
            model_name="user",
            name="organisation",
            field=models.ForeignKey(
                blank=True,
                help_text=("Null only for platform-staff superusers; every org member has one."),
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="members",
                to="accounts.organisation",
            ),
        ),
    ]
