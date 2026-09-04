"""Walks a Scenario's saved node graph and actually does the real-world
things a handful of its node types claim to do — the rest are still the
illustrative mockups they've always been (see EditNodePane.tsx's own
docstring on the frontend side); a run that reaches one of those just
logs "skipped" and moves on rather than pretending or crashing.

v1 only supports `apply_to == "organizations"` scenarios run against a
Customer — every real action below (email, task, lifecycle stage) hangs
naturally off Customer; Account/Contact targets don't have an engine
path yet (see Scenario's own model docstring).

No task queue exists in this codebase (see requirements.txt), so this
all runs synchronously, in-request, in the order the graph visits nodes
— including "Send Email", which really does call send_mail before this
function returns. Wait/Conditional Wait execute as an immediate no-op
(there's nothing to actually pause a synchronous call on); Condition
picks its Yes/No outgoing edge by the real clause below; Filter ends
the walk early when its clause is false; End stops cleanly.

A per-node try/except means one failing node is logged and the walk
continues — it does NOT roll back the side effects earlier nodes in the
same run already committed (an email already sent or a Task already
created is real; it doesn't get undone because a later node broke).
"""

from datetime import date
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from services.customers.models import Customer, Task
from services.email import send_scenario_email

from .models import ScenarioRun

# Fixed, safe set of real Customer fields a Condition/Filter/Set
# Attribute node can read or write — deliberately not "any model
# field": arbitrary field names out of a JSON blob would let a saved
# scenario reach into columns no UI ever meant to expose.
CONDITION_ATTRIBUTES = {
    "lifecycle_stage": lambda customer: customer.lifecycle_stage,
    "health_score": lambda customer: customer.health_score,
    "nps_score": lambda customer: customer.nps_score,
}


def _coerce(attribute, raw_value):
    """String from the saved node data -> the type that attribute's
    real field actually holds, so e.g. health_score compares as a
    number, not lexicographically."""
    if attribute == "lifecycle_stage":
        return raw_value
    try:
        return Decimal(str(raw_value))
    except (InvalidOperation, TypeError):
        return None


def _evaluate_condition(customer, data):
    """True/False for a Condition/Filter node's saved single clause.
    Missing/unrecognized attribute, operator, or an uncoercible value
    all evaluate to False rather than raising — a half-configured
    condition node should skip its Yes branch (or stop a Filter),
    never crash the whole run."""
    attribute = data.get("conditionAttribute")
    operator = data.get("conditionOperator")
    getter = CONDITION_ATTRIBUTES.get(attribute)
    if getter is None or operator is None:
        return False

    actual = getter(customer)
    expected = _coerce(attribute, data.get("conditionValue"))
    if actual is None or expected is None:
        return False
    if attribute != "lifecycle_stage":
        actual = Decimal(str(actual))

    if operator == "equals":
        return actual == expected
    if operator == "not_equals":
        return actual != expected
    if operator == "greater_than":
        return actual > expected
    if operator == "less_than":
        return actual < expected
    return False


def _handle_send_email(customer, data):
    subject = data.get("emailSubject") or f"A message from {customer.organisation.name}"
    body = data.get("emailBody") or ""
    send_scenario_email(customer, subject, body)
    return f'Emailed {customer.email}: "{subject}"'


def _handle_create_task(customer, data):
    title = data.get("taskTitle") or "Follow up"
    Task.objects.create(
        customer=customer,
        title=title,
        assignee_name="Scenario Automation",
        due_date=date.today(),
        priority=Task.Priority.MEDIUM,
    )
    return f'Created task "{title}"'


def _handle_set_attribute(customer, data):
    value = data.get("attributeValue")
    if value not in Customer.LifecycleStage.values:
        raise ValueError(f"'{value}' isn't a valid lifecycle stage.")
    customer.lifecycle_stage = value
    customer.save(update_fields=["lifecycle_stage"])
    return f"Set lifecycle_stage = {value}"


def _handle_churn_entity(customer, _data):
    customer.lifecycle_stage = Customer.LifecycleStage.CHURN
    customer.churn_date = date.today()
    customer.save(update_fields=["lifecycle_stage", "churn_date"])
    return "Marked as churned"


def _handle_wait(_customer, _data):
    return "No task queue to wait on yet — continued immediately"


ACTION_HANDLERS = {
    "Send Email": _handle_send_email,
    "Create Task": _handle_create_task,
    "Set Attribute": _handle_set_attribute,
    "Churn Entity": _handle_churn_entity,
    "Wait": _handle_wait,
    "Conditional Wait": _handle_wait,
}


def _build_graph(scenario):
    nodes_by_id = {node["id"]: node for node in scenario.nodes}
    outgoing = {}
    for edge in scenario.edges:
        outgoing.setdefault(edge["source"], []).append(edge)
    entry = next((n for n in scenario.nodes if n.get("type") == "entry"), None)
    return nodes_by_id, outgoing, entry


def run_scenario(scenario, customer, triggered_by):
    """Runs `scenario` against `customer` right now and returns the
    ScenarioRun it created. Never raises for a broken/incomplete graph
    or a failing node — every failure mode becomes a `log` entry, and
    `status` reflects whether anything failed."""

    run = ScenarioRun.objects.create(
        scenario=scenario, customer=customer, triggered_by=triggered_by
    )
    log = []
    failed = False

    nodes_by_id, outgoing, entry = _build_graph(scenario)
    if entry is None:
        log.append(
            {"node_id": None, "action": None, "status": "failed", "detail": "No entry node."}
        )
        run.log = log
        run.status = ScenarioRun.Status.FAILED
        run.finished_at = timezone.now()
        run.save()
        return run

    current = entry
    visited_guard = 0
    while current is not None and visited_guard < len(nodes_by_id) + 1:
        visited_guard += 1
        node_type = current.get("type")
        data = current.get("data", {})
        action = data.get("action")

        next_edges = outgoing.get(current["id"], [])

        if node_type == "entry":
            # The trigger itself did nothing to execute — it's what got
            # us here. Just move on to whatever it connects to.
            current = nodes_by_id.get(next_edges[0]["target"]) if next_edges else None
            continue

        if action == "End":
            log.append(
                {
                    "node_id": current["id"],
                    "action": action,
                    "status": "ok",
                    "detail": "End of flow.",
                }
            )
            break

        if action == "Condition":
            result = _evaluate_condition(customer, data)
            log.append(
                {
                    "node_id": current["id"],
                    "action": action,
                    "status": "ok",
                    "detail": f"Condition evaluated to {result}.",
                }
            )
            wanted_label = "Yes" if result else "No"
            match = next((e for e in next_edges if e.get("label") == wanted_label), None)
            current = nodes_by_id.get(match["target"]) if match else None
            continue

        if action == "Filter":
            result = _evaluate_condition(customer, data)
            if not result:
                log.append(
                    {
                        "node_id": current["id"],
                        "action": action,
                        "status": "ok",
                        "detail": "Filter condition false — run stopped here.",
                    }
                )
                break
            log.append(
                {
                    "node_id": current["id"],
                    "action": action,
                    "status": "ok",
                    "detail": "Filter condition true — continuing.",
                }
            )
            current = nodes_by_id.get(next_edges[0]["target"]) if next_edges else None
            continue

        handler = ACTION_HANDLERS.get(action)
        if handler is None:
            log.append(
                {
                    "node_id": current["id"],
                    "action": action,
                    "status": "skipped",
                    "detail": f'"{action}" isn\'t wired to a real system yet.',
                }
            )
        else:
            try:
                detail = handler(customer, data)
                log.append(
                    {"node_id": current["id"], "action": action, "status": "ok", "detail": detail}
                )
            except Exception as exc:  # noqa: BLE001 — deliberately broad: any
                # single node's failure becomes a log line, never a 500.
                failed = True
                log.append(
                    {
                        "node_id": current["id"],
                        "action": action,
                        "status": "failed",
                        "detail": str(exc),
                    }
                )

        current = nodes_by_id.get(next_edges[0]["target"]) if next_edges else None

    run.log = log
    run.status = ScenarioRun.Status.FAILED if failed else ScenarioRun.Status.SUCCESS
    run.finished_at = timezone.now()
    run.save()
    return run
