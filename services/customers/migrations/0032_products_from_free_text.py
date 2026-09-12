"""Step two: turn what people typed into each tenant's product list.

One Product row per distinct product name per organisation, folded on case and
surrounding whitespace — the same folding the Product Usage dashboard was
doing at read time, done once here so no dashboard has to do it again.

Where spellings disagree, the **most common** spelling wins the row, because
that is the one the team recognises; ties fall back to the first alphabetically
so the same database always migrates the same way. Nothing is dropped: a
customer whose product was blank simply gets a null FK, which is what "nobody
recorded one" has always meant.
"""

from collections import Counter

from django.db import migrations


def canonical_name(spellings):
    """The spelling to keep out of several for the same product."""
    ranked = Counter(spellings).most_common()
    best = max(count for _, count in ranked)
    return sorted(name for name, count in ranked if count == best)[0]


def forwards(apps, schema_editor):
    Customer = apps.get_model("customers", "Customer")
    Product = apps.get_model("customers", "Product")

    spellings = {}
    for organisation_id, raw in Customer.objects.values_list("organisation_id", "primary_product"):
        name = (raw or "").strip()
        if name:
            spellings.setdefault((organisation_id, name.casefold()), []).append(name)

    products = {}
    for (organisation_id, folded), seen in spellings.items():
        product = Product.objects.create(organisation_id=organisation_id, name=canonical_name(seen))
        products[(organisation_id, folded)] = product.pk

    for customer in Customer.objects.exclude(primary_product="").iterator():
        key = (customer.organisation_id, customer.primary_product.strip().casefold())
        product_id = products.get(key)
        if product_id is not None:
            customer.primary_product_ref_id = product_id
            customer.save(update_fields=["primary_product_ref"])


def backwards(apps, schema_editor):
    """Write the product names back into the text column.

    A true reverse of the data, though not of the folding: two spellings that
    were merged come back as the one that won.
    """
    Customer = apps.get_model("customers", "Customer")

    for customer in Customer.objects.exclude(primary_product_ref__isnull=True).select_related(
        "primary_product_ref"
    ):
        customer.primary_product = customer.primary_product_ref.name
        customer.save(update_fields=["primary_product"])


class Migration(migrations.Migration):
    dependencies = [("customers", "0031_customer_primary_product_ref")]

    operations = [migrations.RunPython(forwards, backwards)]
