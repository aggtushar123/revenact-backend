# Step 3 of 3 — drops the old single-FK column now that 0016 copied
# every value across, and renames the M2M's own related_name from the
# temporary "accounts_m2m" (needed in 0015 to avoid colliding with the
# old FK's own "accounts" while both existed side by side) to its real
# "accounts", matching the Account model's own current definition.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0016_populate_account_customers"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="account",
            name="customer",
        ),
        migrations.AlterField(
            model_name="account",
            name="customers",
            field=models.ManyToManyField(
                blank=True,
                help_text=(
                    "Every Customer this Account belongs to — see this model's own "
                    "docstring for why this is a many-to-many, not a single FK."
                ),
                related_name="accounts",
                to="customers.customer",
            ),
        ),
    ]
