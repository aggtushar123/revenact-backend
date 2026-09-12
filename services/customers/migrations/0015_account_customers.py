# Step 1 of 3 for turning Account.customer (a single FK) into
# Account.customers (a many-to-many) — see the Account model's own
# docstring for why. Adds the new field alongside the old one so
# 0016's data migration can copy every existing customer_id across
# before 0017 removes the old field.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0014_risk"),
    ]

    operations = [
        migrations.AddField(
            model_name="account",
            name="customers",
            field=models.ManyToManyField(
                blank=True,
                help_text=(
                    "Every Customer this Account belongs to — see this model's own "
                    "docstring for why this is a many-to-many, not a single FK."
                ),
                related_name="accounts_m2m",
                to="customers.customer",
            ),
        ),
    ]
