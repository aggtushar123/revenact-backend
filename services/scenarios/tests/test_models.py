"""Unit tier: model defaults, no HTTP."""

from django.test import TestCase

from services.accounts.models import Organisation
from services.scenarios.models import Scenario


class ScenarioDefaultsTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")

    def test_defaults(self):
        scenario = Scenario.objects.create(organisation=self.org)
        self.assertEqual(scenario.name, "Untitled Scenario")
        self.assertEqual(scenario.apply_to, Scenario.ApplyTo.ORGANIZATIONS)
        self.assertEqual(scenario.nodes, [])
        self.assertEqual(scenario.edges, [])
        # Defaults off — see the model's own docstring on why an On
        # Event trigger shouldn't start firing the moment it's saved.
        self.assertFalse(scenario.is_active)

    def test_ordered_most_recently_updated_first(self):
        older = Scenario.objects.create(organisation=self.org, name="Older")
        newer = Scenario.objects.create(organisation=self.org, name="Newer")
        self.assertEqual(list(Scenario.objects.all()), [newer, older])
