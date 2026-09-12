"""Step three: drop the text column and give the FK its name.

After this, `Customer.primary_product` is a Product. Everything that read a
string off it reads `primary_product.name`, and everything that grouped by
folding case groups by id.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("customers", "0032_products_from_free_text")]

    operations = [
        migrations.RemoveField(model_name="customer", name="primary_product"),
        migrations.RenameField(
            model_name="customer",
            old_name="primary_product_ref",
            new_name="primary_product",
        ),
    ]
