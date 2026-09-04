"""On Event execution: the one On Event trigger option that's real (see
Scenario's own docstring and CreateScenario.tsx) is "Creation of new
entity" — react-ts-app's EditNodePane already saves this as
`ScenarioNodeData.eventTrigger == "new_entity"` on the entry node, no
new frontend work needed to make it mean something real here.

First use of Django signals in this codebase (see apps.py's `ready()`,
which is what actually connects this). `transaction.on_commit` rather
than running inline in the receiver — a receiver fires mid-transaction,
before the new Customer row is guaranteed visible to the query the
engine itself would run if it, say, re-fetched the row; committing
first avoids that race for no real cost (this already isn't a
synchronous "block the request" concern the caller is waiting on)."""

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from services.customers.models import Customer

from .engine import run_scenario
from .models import Scenario


@receiver(post_save, sender=Customer)
def run_on_event_scenarios(sender, instance, created, **kwargs):
    if not created:
        return

    scenarios = Scenario.objects.filter(
        organisation=instance.organisation,
        apply_to=Scenario.ApplyTo.ORGANIZATIONS,
        is_active=True,
    )
    matching = [scenario for scenario in scenarios if _entry_event(scenario) == "new_entity"]
    if not matching:
        return

    def _run_all():
        for scenario in matching:
            run_scenario(scenario, instance, triggered_by="event")

    transaction.on_commit(_run_all)


def _entry_event(scenario):
    entry = next((n for n in scenario.nodes if n.get("type") == "entry"), None)
    if entry is None:
        return None
    return entry.get("data", {}).get("eventTrigger")
