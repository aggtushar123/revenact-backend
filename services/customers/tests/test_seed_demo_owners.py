from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from services.accounts.models import Organisation, User
from services.customers.models import Customer


class SeedDemoOwnersTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.alice = User.objects.create_user(
            email="alice@acme.io", password="x", name="Alice", organisation=self.org
        )
        self.carl = User.objects.create_user(
            email="carl@acme.io", password="x", name="Carl", organisation=self.org
        )
        for name in ("Apple Inc", "Spotify", "Zoom"):
            Customer.objects.create(organisation=self.org, name=name, owner=self.carl)

    def test_creates_dana_and_moves_her_slice_and_only_her_slice(self):
        err = StringIO()
        call_command("seed_demo_owners", org_email="alice@acme.io", stdout=StringIO(), stderr=err)

        dana = User.objects.get(email="dana@acme.io")
        self.assertEqual(dana.organisation, self.org)
        self.assertEqual(dana.role.slug, User.Role.CSM)
        self.assertTrue(dana.check_password("supersecret1"))
        owners = dict(Customer.objects.values_list("name", "owner__email"))
        self.assertEqual(owners["Apple Inc"], "carl@acme.io")
        self.assertEqual(owners["Spotify"], "dana@acme.io")
        self.assertEqual(owners["Zoom"], "dana@acme.io")
        self.assertIn("no customer 'Stripe'", err.getvalue())

    def test_running_twice_changes_nothing(self):
        call_command(
            "seed_demo_owners", org_email="alice@acme.io", stdout=StringIO(), stderr=StringIO()
        )
        out = StringIO()
        call_command("seed_demo_owners", org_email="alice@acme.io", stdout=out, stderr=StringIO())

        self.assertEqual(User.objects.filter(email="dana@acme.io").count(), 1)
        self.assertIn("found Dana CSM; moved 0 customer(s)", out.getvalue())
