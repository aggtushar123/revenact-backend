# Step 2 of 3 — copies every existing Account.customer_id into the new
# Account.customers many-to-many before 0017 drops the old FK. Uses the
# historical model (via apps.get_model), same convention as any other
# data migration, so this keeps working regardless of what the "real"
# Account model looks like by the time this runs later.

from django.db import migrations


def copy_customer_to_customers(apps, schema_editor):
    Account = apps.get_model("customers", "Account")
    for account in Account.objects.exclude(customer__isnull=True):
        account.customers.add(account.customer_id)


def copy_customers_to_customer(apps, schema_editor):
    # Reverse: picks one (arbitrary, if there's more than one) linked
    # Customer back into the old single FK — lossy by nature (that's
    # exactly what the FK -> M2M move was for), but keeps `migrate
    # customers 0015` from leaving `customer` stranded at NULL.
    Account = apps.get_model("customers", "Account")
    for account in Account.objects.all():
        first = account.customers.first()
        if first is not None:
            account.customer_id = first.id
            account.save(update_fields=["customer"])


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0015_account_customers"),
    ]

    operations = [
        migrations.RunPython(copy_customer_to_customers, copy_customers_to_customer),
    ]
