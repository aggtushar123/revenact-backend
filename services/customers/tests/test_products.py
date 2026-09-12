"""The product catalogue: /api/v1/products/.

Products replaced a free-text field, so the tests are mostly about the two
things a list can do that a text box could not — refuse a duplicate, and
refuse to lose the record of what a customer bought.

Deliberately **not** scoped by ownership, unlike every other list in this app:
a product list is the shape of the business, and a CSM who cannot see
"Product C" cannot record a customer on it.
"""

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers.models import Customer, Product


class ProductModelTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")

    def test_one_name_per_organisation_ignoring_case(self):
        """The whole point. "Product A" and "product a" were two rows on the
        Product Usage dashboard, folded at read time, and no folding could
        ever have merged "Integrations Module" with "Integrations module"."""
        Product.objects.create(organisation=self.org, name="Product A")

        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.create(organisation=self.org, name="product a")

    def test_two_tenants_can_sell_the_same_product_name(self):
        # The reason this is a table and not a TextChoices enum: every tenant
        # sells something different, and "Product A" is not reserved.
        other = Organisation.objects.create(name="Other Inc", currency="USD")
        Product.objects.create(organisation=self.org, name="Product A")

        Product.objects.create(organisation=other, name="Product A")

        self.assertEqual(Product.objects.filter(name="Product A").count(), 2)

    def test_a_product_in_use_cannot_be_deleted_at_the_database_either(self):
        product = Product.objects.create(organisation=self.org, name="Product A")
        Customer.objects.create(organisation=self.org, name="Globex", primary_product=product)

        # PROTECT, not SET_NULL: losing the record of what they bought is
        # worse than being made to retire the product instead.
        from django.db.models import ProtectedError

        with self.assertRaises(ProtectedError):
            product.delete()


class ProductAPITests(APITestCase):
    url = "/api/v1/products/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="supersecret1",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.other_org = Organisation.objects.create(name="Other Inc", currency="USD")
        self.client.force_authenticate(self.csm)

    def _product(self, name, **overrides):
        return Product.objects.create(organisation=self.org, name=name, **overrides)

    def test_unauthenticated_is_refused(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lists_the_whole_organisations_catalogue_ordered_by_name(self):
        self._product("Product B")
        self._product("Integrations Module")

        response = self.client.get(self.url)

        self.assertEqual(
            [row["name"] for row in response.data], ["Integrations Module", "Product B"]
        )

    def test_another_tenants_products_are_invisible(self):
        Product.objects.create(organisation=self.other_org, name="Their Product")
        self._product("Mine")

        self.assertEqual([row["name"] for row in self.client.get(self.url).data], ["Mine"])

    def test_a_csm_sees_products_their_own_book_does_not_use(self):
        """Not scoped by ownership, unlike the customer lists: a product
        nobody in your book is on is still one you can put a customer on."""
        self._product("Product nobody here sells")

        self.assertEqual(len(self.client.get(self.url).data), 1)

    def test_retired_products_are_listed_so_they_can_be_brought_back(self):
        self._product("Legacy", is_active=False)

        row = self.client.get(self.url).data[0]

        self.assertEqual(row["name"], "Legacy")
        self.assertFalse(row["is_active"])

    def test_a_row_says_how_many_customers_are_on_it(self):
        product = self._product("Product A")
        Customer.objects.create(organisation=self.org, name="Globex", primary_product=product)
        Customer.objects.create(organisation=self.org, name="Initech", primary_product=product)

        self.assertEqual(self.client.get(self.url).data[0]["customers"], 2)

    # ── creating ─────────────────────────────────────────────────────

    def test_creating_a_product_puts_it_in_the_callers_own_organisation(self):
        response = self.client.post(self.url, {"name": "Product A"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Product.objects.get(name="Product A").organisation, self.org)

    def test_a_name_is_trimmed_on_the_way_in(self):
        # "  Product A " and "Product A" are the same product, and the second
        # is the one anybody wants to read.
        self.client.post(self.url, {"name": "  Product A "}, format="json")

        self.assertEqual(Product.objects.get().name, "Product A")

    def test_a_duplicate_name_is_refused_with_the_name_it_clashes_with(self):
        """A 500 from the database constraint is not an answer. This is the
        message that tells somebody the product they are adding is already on
        the list under a different capitalisation."""
        self._product("Product A")

        response = self.client.post(self.url, {"name": "product a"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Product A", str(response.data["name"]))

    def test_a_blank_name_is_refused(self):
        response = self.client.post(self.url, {"name": "   "}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # ── renaming and retiring ────────────────────────────────────────

    def test_renaming_fixes_the_name_everywhere_at_once(self):
        """The difference from the text field this replaced, where a typo had
        to be fixed on every customer individually and usually wasn't."""
        product = self._product("Prodcut A")
        customer = Customer.objects.create(
            organisation=self.org, name="Globex", primary_product=product
        )

        response = self.client.patch(
            f"{self.url}{product.id}/", {"name": "Product A"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        customer.refresh_from_db()
        self.assertEqual(customer.primary_product.name, "Product A")

    def test_a_rename_that_collides_with_another_product_is_refused(self):
        self._product("Product A")
        other = self._product("Product B")

        response = self.client.patch(f"{self.url}{other.id}/", {"name": "PRODUCT A"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_product_can_keep_its_own_name_on_an_unrelated_edit(self):
        # The duplicate check has to exclude the row being edited, or nothing
        # could ever be retired.
        product = self._product("Product A")

        response = self.client.patch(
            f"{self.url}{product.id}/", {"name": "Product A", "is_active": False}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        product.refresh_from_db()
        self.assertFalse(product.is_active)

    # ── deleting ─────────────────────────────────────────────────────

    def test_deleting_a_product_nobody_is_on_works(self):
        product = self._product("Added by mistake")

        response = self.client.delete(f"{self.url}{product.id}/")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Product.objects.filter(pk=product.pk).exists())

    def test_deleting_a_product_customers_are_on_is_refused_with_the_count(self):
        product = self._product("Product A")
        Customer.objects.create(
            organisation=self.org,
            name="Globex",
            primary_product=product,
            arr_billed_at_account=Decimal(100_000),
        )

        response = self.client.delete(f"{self.url}{product.id}/")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("1 customer is recorded against", str(response.data))
        # And the alternative, which is what they actually wanted.
        self.assertIn("Retire it instead", str(response.data))
        self.assertTrue(Product.objects.filter(pk=product.pk).exists())

    def test_a_churned_customer_still_protects_their_product(self):
        """Especially a churned one: the Product Usage dashboard's churn
        figures are the reason that row exists."""
        product = self._product("Dead Product")
        Customer.objects.create(
            organisation=self.org, name="Gone", primary_product=product, churn_date="2026-01-01"
        )

        self.assertEqual(
            self.client.delete(f"{self.url}{product.id}/").status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_another_tenants_product_cannot_be_read_renamed_or_deleted(self):
        theirs = Product.objects.create(organisation=self.other_org, name="Their Product")

        self.assertEqual(
            self.client.get(f"{self.url}{theirs.id}/").status_code, status.HTTP_404_NOT_FOUND
        )
        self.assertEqual(
            self.client.patch(f"{self.url}{theirs.id}/", {"name": "Mine now"}).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.client.delete(f"{self.url}{theirs.id}/").status_code, status.HTTP_404_NOT_FOUND
        )


class CustomerProductWriteTests(APITestCase):
    """Putting a customer on a product, through the customer endpoints."""

    url = "/api/v1/customers/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="supersecret1",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.product = Product.objects.create(organisation=self.org, name="Product A")
        self.client.force_authenticate(self.csm)

    def test_a_customer_is_created_on_a_product_by_id(self):
        response = self.client.post(
            self.url, {"name": "Globex", "primary_product": self.product.id}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["primary_product"], self.product.id)
        # The name rides along so a screen that only prints what they bought
        # doesn't have to fetch the catalogue to find out.
        self.assertEqual(response.data["primary_product_name"], "Product A")

    def test_a_customer_can_have_no_product_recorded(self):
        response = self.client.post(
            self.url, {"name": "Globex", "primary_product": None}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(response.data["primary_product"])
        self.assertEqual(response.data["primary_product_name"], "")

    def test_another_tenants_product_is_refused(self):
        other_org = Organisation.objects.create(name="Other Inc", currency="USD")
        theirs = Product.objects.create(organisation=other_org, name="Their Product")

        response = self.client.post(
            self.url, {"name": "Globex", "primary_product": theirs.id}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("your own organisation", str(response.data["primary_product"]))
