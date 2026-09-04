from django.db import models


class Scenario(models.Model):
    """A saved automation flow built in the frontend's canvas builder
    (react-ts-app's src/pages/scenarios/CreateScenario.tsx) — a trigger
    node, some operators, some actions, connected by edges. `nodes`/
    `edges` are stored verbatim as React Flow gives them (an array of
    `{id, type, position, data}` / `{id, source, target, label}`
    objects) rather than decomposed into relational rows: the graph
    shape is entirely the frontend's concern (positions, node types,
    per-node config), and the backend only needs to read `data` back
    out of it when actually running one (see engine.py). This mirrors
    how `Customer.pulse` stores AI-analysis JSON verbatim rather than
    modeling every possible shape as columns.

    `apply_to` is deliberately narrower than a real multi-tenant
    "audience" concept — it just says which entity type the scenario is
    conceptually about. `run_scenario` (engine.py) only actually
    supports "organizations" today (see that module's own docstring);
    Accounts/Contacts scenarios save and list normally but can't be run
    yet.

    `is_active` gates On Event execution only (see signals.py) — Run Now
    works regardless, since a human explicitly asking to run a specific
    scenario right now isn't the "silently fires on every future
    Customer" surprise an active On Event trigger would be. Defaults to
    False for exactly that reason: a freshly-saved scenario with an On
    Event trigger shouldn't start acting on live data until someone
    deliberately flips it on.
    """

    class ApplyTo(models.TextChoices):
        ORGANIZATIONS = "organizations", "Organizations"
        ACCOUNTS = "accounts", "Accounts"
        CONTACTS = "contacts", "Contacts"

    organisation = models.ForeignKey(
        "accounts.Organisation", related_name="scenarios", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=255, default="Untitled Scenario")
    apply_to = models.CharField(
        max_length=16, choices=ApplyTo.choices, default=ApplyTo.ORGANIZATIONS
    )
    nodes = models.JSONField(default=list, blank=True)
    edges = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(
        default=False,
        help_text="Gates On Event auto-execution (see scenarios.signals). "
        "Run Now ignores this — it's an explicit, one-off ask.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name


class ScenarioRun(models.Model):
    """One execution of a Scenario against one target Customer — the
    audit trail behind the builder's "Run Now" button and behind
    on-event auto-runs alike (`triggered_by` tells them apart). `log` is
    a list of `{node_id, action, status, detail}` objects, one per node
    the engine actually visited, in visit order — see engine.py's own
    docstring for the full shape and why a failing node doesn't roll
    back the ones before it.

    `customer` is nullable only because Django requires it to be
    (`on_delete=SET_NULL` keeps the run's own history around after the
    Customer it ran against is deleted) — every real row has it set;
    v1 has no Account/Contact target to put here instead (see Scenario's
    own docstring on why)."""

    class TriggeredBy(models.TextChoices):
        MANUAL = "manual", "Manual"
        EVENT = "event", "Event"

    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    scenario = models.ForeignKey(Scenario, related_name="runs", on_delete=models.CASCADE)
    customer = models.ForeignKey(
        "customers.Customer",
        related_name="scenario_runs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    triggered_by = models.CharField(max_length=8, choices=TriggeredBy.choices)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.SUCCESS)
    log = models.JSONField(default=list, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.scenario.name} → {self.customer} ({self.status})"
