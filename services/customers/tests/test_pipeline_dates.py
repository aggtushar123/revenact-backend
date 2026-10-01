"""Opportunity and risk dates, the Closed Lost stage, and the stage clock
(`stage_changed_at`) the Pipelines "…this quarter" tiles read."""

import importlib
from datetime import timedelta

from django.apps import apps
from django.test import TestCase
from django.utils import timezone

from services.accounts.models import Organisation
from services.customers.models import Customer, Opportunity, Risk


class Fixture(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.pizza = Customer.objects.create(organisation=self.org, name="Pizza Hut")
        self.long_ago = timezone.now() - timedelta(days=400)

    def backdate(self, item):
        """Moves the clock into the past and reloads, as a request would."""
        type(item).objects.filter(pk=item.pk).update(stage_changed_at=self.long_ago)
        return type(item).objects.get(pk=item.pk)


class StagesAndDatesTests(Fixture):
    def test_closed_lost_is_an_opportunity_stage_after_closed_won(self):
        self.assertEqual(Opportunity.Stage.values[-2:], ["closed_won", "closed_lost"])
        self.assertEqual(Opportunity.Stage.CLOSED_LOST.label, "Closed Lost")

    def test_open_and_closed_stages_partition_each_kind(self):
        self.assertEqual(Opportunity.CLOSED_STAGES, ("closed_won", "closed_lost"))
        self.assertEqual(
            set(Opportunity.OPEN_STAGES) | set(Opportunity.CLOSED_STAGES),
            set(Opportunity.Stage.values),
        )
        self.assertFalse(set(Opportunity.OPEN_STAGES) & set(Opportunity.CLOSED_STAGES))
        self.assertEqual(Risk.OPEN_STAGES, ("open",))

    def test_dates_are_optional(self):
        opportunity = Opportunity.objects.create(customer=self.pizza, title="Upsell")
        risk = Risk.objects.create(customer=self.pizza, title="Budget")
        self.assertIsNone(opportunity.expected_close)
        self.assertIsNone(risk.due_by)
        opportunity.expected_close = timezone.localdate()
        opportunity.save()
        opportunity.refresh_from_db()
        self.assertEqual(opportunity.expected_close, timezone.localdate())


class StageClockTests(Fixture):
    def test_the_clock_starts_at_creation(self):
        before = timezone.now()
        opportunity = Opportunity.objects.create(customer=self.pizza, title="Upsell")
        risk = Risk.objects.create(customer=self.pizza, title="Budget")
        self.assertGreaterEqual(opportunity.stage_changed_at, before)
        self.assertGreaterEqual(risk.stage_changed_at, before)

    def test_a_stage_change_moves_the_clock(self):
        opportunity = self.backdate(Opportunity.objects.create(customer=self.pizza, title="Upsell"))
        opportunity.stage = Opportunity.Stage.CLOSED_WON
        opportunity.save()
        opportunity.refresh_from_db()
        self.assertGreater(opportunity.stage_changed_at, self.long_ago)

    def test_saving_without_a_stage_change_leaves_the_clock(self):
        opportunity = self.backdate(Opportunity.objects.create(customer=self.pizza, title="Upsell"))
        opportunity.title = "Bigger upsell"
        opportunity.mrr = 500
        opportunity.save()
        opportunity.refresh_from_db()
        self.assertEqual(opportunity.stage_changed_at, self.long_ago)

    def test_setting_the_same_stage_again_is_not_a_change(self):
        opportunity = self.backdate(Opportunity.objects.create(customer=self.pizza, title="Upsell"))
        opportunity.stage = Opportunity.Stage.DISCOVERY
        opportunity.save()
        opportunity.refresh_from_db()
        self.assertEqual(opportunity.stage_changed_at, self.long_ago)

    def test_update_fields_with_the_stage_also_writes_the_clock(self):
        risk = self.backdate(Risk.objects.create(customer=self.pizza, title="Budget"))
        risk.stage = Risk.Stage.MITIGATED
        risk.save(update_fields=["stage"])
        risk.refresh_from_db()
        self.assertEqual(risk.stage, "mitigated")
        self.assertGreater(risk.stage_changed_at, self.long_ago)

    def test_update_fields_without_the_stage_leaves_the_clock(self):
        risk = self.backdate(Risk.objects.create(customer=self.pizza, title="Budget"))
        risk.stage = Risk.Stage.MITIGATED
        risk.title = "Budget freeze"
        risk.save(update_fields=["title"])
        risk.refresh_from_db()
        self.assertEqual((risk.stage, risk.stage_changed_at), ("open", self.long_ago))

    def test_a_refreshed_row_judges_the_stage_it_has_now(self):
        opportunity = Opportunity.objects.create(customer=self.pizza, title="Upsell")
        Opportunity.objects.filter(pk=opportunity.pk).update(
            stage=Opportunity.Stage.NEGOTIATION, stage_changed_at=self.long_ago
        )
        opportunity.refresh_from_db()
        opportunity.title = "Renamed"
        opportunity.save()
        opportunity.refresh_from_db()
        self.assertEqual(opportunity.stage_changed_at, self.long_ago)


class StageClockBackfillTests(Fixture):
    """The migration on existing rows: their clock starts at creation."""

    def test_existing_rows_start_their_clock_at_creation(self):
        migration = importlib.import_module("services.customers.migrations.0049_pipeline_dates")
        opportunity = Opportunity.objects.create(customer=self.pizza, title="Old deal")
        risk = Risk.objects.create(customer=self.pizza, title="Old risk")
        created = timezone.now() - timedelta(days=90)
        Opportunity.objects.filter(pk=opportunity.pk).update(created_at=created)
        Risk.objects.filter(pk=risk.pk).update(created_at=created)

        migration.backfill_stage_changed_at(apps, None)

        self.assertEqual(Opportunity.objects.get(pk=opportunity.pk).stage_changed_at, created)
        self.assertEqual(Risk.objects.get(pk=risk.pk).stage_changed_at, created)
        self.assertIsNone(Opportunity.objects.get(pk=opportunity.pk).expected_close)
