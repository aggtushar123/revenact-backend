"""Step one of three: a nullable FK beside the free-text column.

Split across three migrations so the names survive. Letting makemigrations
swap the field in one step would drop the column and add an empty FK, which
loses every customer's product silently.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("customers", "0030_product_model")]

    operations = [
        migrations.AddField(
            model_name="customer",
            name="primary_product_ref",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="primary_customers",
                to="customers.product",
                help_text="The product this customer is led by, from the tenant's own "
                "Product table — free text until migration 0030. Null means nobody "
                "recorded one. PROTECT rather than SET_NULL: losing the record of what "
                "a customer bought is worse than being made to retire the product "
                "instead (Product.is_active). Only one product per customer is "
                "recorded, which is the limit the Product Usage dashboard states on "
                "screen; additional_products_count below counts the rest without "
                "naming them.",
            ),
        )
    ]
