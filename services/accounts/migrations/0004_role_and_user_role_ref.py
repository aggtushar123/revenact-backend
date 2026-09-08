import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Step 1 of 3 turning User.role from a hardcoded CharField into a
    real ForeignKey: create the Role table and a nullable `role_ref`
    alongside the existing `role` string, so no data moves yet.

    Split across three migrations (schema / data / swap) so the data
    migration in 0005 can read the old string column and write the new
    FK with both present at once."""

    dependencies = [
        ("accounts", "0003_organisation_ai_agent_enabled_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="Role",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("name", models.CharField(max_length=100)),
                ("slug", models.SlugField(max_length=100)),
                ("permissions", models.JSONField(blank=True, default=list)),
                (
                    "is_system",
                    models.BooleanField(
                        default=False,
                        help_text=(
                            "The built-in Admin/CSM roles — not renamable, editable, or deletable."
                        ),
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "organisation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="roles",
                        to="accounts.organisation",
                    ),
                ),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.AddConstraint(
            model_name="role",
            constraint=models.UniqueConstraint(
                fields=("organisation", "slug"), name="unique_role_slug_per_organisation"
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="role_ref",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="users",
                to="accounts.role",
            ),
        ),
    ]
