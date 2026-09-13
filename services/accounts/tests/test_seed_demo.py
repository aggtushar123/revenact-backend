from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from services.accounts.models import Organisation, User
from services.customers.models import Customer
from services.knowledge.models import Contribution


class SeedDemoTests(TestCase):
    def test_seeds_the_whole_book_twice_without_duplicating(self):
        out, err = StringIO(), StringIO()
        call_command("seed_demo", stdout=out, stderr=err)

        org = Organisation.objects.get(name="Acme Inc")
        alice = User.objects.get(email="alice@acme.io")
        self.assertEqual(alice.organisation, org)
        self.assertTrue(alice.check_password("supersecret1"))
        self.assertEqual(User.objects.get(email="dana@acme.io").reports_to.email, "carl@acme.io")
        self.assertGreater(Customer.objects.filter(organisation=org).count(), 5)
        self.assertGreater(Contribution.objects.filter(organisation=org).count(), 5)
        self.assertNotIn("failed", err.getvalue(), err.getvalue())

        before = (Customer.objects.count(), Contribution.objects.count(), User.objects.count())
        call_command("seed_demo", stdout=StringIO(), stderr=StringIO())
        after = (Customer.objects.count(), Contribution.objects.count(), User.objects.count())
        self.assertEqual(before, after)
