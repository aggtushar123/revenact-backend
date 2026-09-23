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

from django.db.models import Count, Q
from django.utils import timezone

from services.accounts.models import User
from services.copilot.embeddings import embed
from services.customers.models import Customer, Task
from services.email import send_scenario_email
from services.notifications.models import Notification
from services.notifications.realtime import notify

from .models import ScenarioRun

# Fixed, safe set of real Customer facts a Condition/Filter node can read
# — deliberately not "any model field": arbitrary field names out of a
# JSON blob would let a saved scenario reach into columns no UI ever meant
# to expose. An AI attribute is read by name instead, as `attr:<api_name>`.
CONDITION_ATTRIBUTES = {
    "lifecycle_stage": lambda customer: customer.lifecycle_stage,
    "health_score": lambda customer: customer.health_score,
    "nps_score": lambda customer: customer.nps_score,
    "name": lambda customer: customer.name,
    "owner": lambda customer: customer.owner.email if customer.owner_id else None,
    "arr": lambda customer: _arr(customer),
    "renewal_days": lambda customer: _renewal_days(customer),
    "open_tickets": lambda customer: _open_tickets(customer),
}

#: Which of those compare as text rather than as numbers.
TEXT_ATTRIBUTES = {"lifecycle_stage", "name", "owner"}

#: How close a record must be to a semantic condition's phrase to count.
#: Cosine similarity on the local model: below about a half is a different
#: subject entirely, and a scenario that fires on everything is worse than
#: one that never fires.
SEMANTIC_THRESHOLD = 0.6
#: How many of a company's most recent interactions a semantic condition
#: reads. It embeds them on every run, so this is a real cost ceiling.
SEMANTIC_RECORDS = 15


def _arr(customer):
    """Revenue the way this organisation has said it counts revenue."""
    field = customer.organisation.effective_global_attributes()["arr"]
    return getattr(customer, field, None)


def _renewal_days(customer):
    if customer.renewal_date is None:
        return None
    return (customer.renewal_date - timezone.localdate()).days


def _open_tickets(customer):
    from services.customers.models import Ticket

    return (
        Ticket.objects.filter(customer=customer)
        .exclude(status__in=[Ticket.Status.RESOLVED, Ticket.Status.CLOSED])
        .count()
    )


def _ai_attribute(customer, api_name):
    """The current answer to one AI attribute — the newest row, which is a
    person's correction when there has been one (services.attributes)."""
    from services.attributes.models import AIAttributeValue

    row = (
        AIAttributeValue.objects.filter(
            attribute__organisation=customer.organisation,
            attribute__api_name=api_name,
            customer=customer,
        )
        .order_by("-computed_at", "-id")
        .first()
    )
    return None if row is None else row.value


def _read(customer, attribute):
    """One condition attribute's current value, and whether it is text."""
    if attribute and attribute.startswith("attr:"):
        value = _ai_attribute(customer, attribute[5:])
        return value, not isinstance(value, (int, float, Decimal))
    getter = CONDITION_ATTRIBUTES.get(attribute)
    if getter is None:
        return None, True
    return getter(customer), attribute in TEXT_ATTRIBUTES


def _coerce(as_text, raw_value):
    """The saved string -> the type the attribute actually holds, so e.g.
    health_score compares as a number rather than lexicographically."""
    if as_text:
        return None if raw_value is None else str(raw_value)
    try:
        return Decimal(str(raw_value))
    except (InvalidOperation, TypeError):
        return None


def _recent_texts(customer):
    """What the company has recently said, for a semantic condition: the
    same interactions the Copilot would read, newest first."""
    from services.customers.models import Call, Email, Ticket

    rows = []
    for email in Email.objects.filter(customer=customer).order_by("-sent_at")[:SEMANTIC_RECORDS]:
        rows.append((f"{email.subject}. {email.body}", f"Email: {email.subject}"))
    for ticket in Ticket.objects.filter(customer=customer).order_by("-opened_at")[
        :SEMANTIC_RECORDS
    ]:
        rows.append((ticket.title, f"Ticket {ticket.ticket_number}: {ticket.title}"))
    for call in Call.objects.filter(customer=customer).order_by("-occurred_at")[:SEMANTIC_RECORDS]:
        rows.append(
            (f"{call.title}. {call.summary}" if call.summary else call.title, f"Call: {call.title}")
        )
    return rows[: SEMANTIC_RECORDS * 3]


def _semantic_match(customer, data):
    """(passed, detail) for a phrase condition.

    The phrase and the company's recent interactions go through the same
    local embedding model the Copilot uses, so this costs no credits and
    gives the same answer every run. The detail names the record it matched
    and how closely — never the record's own words, which are personal to
    whoever they belong to."""
    phrase = (data.get("conditionPhrase") or "").strip()
    if not phrase:
        return False, "No phrase to match."
    rows = _recent_texts(customer)
    if not rows:
        return False, "Nothing on record to match against."
    try:
        threshold = float(data.get("conditionThreshold") or SEMANTIC_THRESHOLD)
    except (TypeError, ValueError):
        threshold = SEMANTIC_THRESHOLD
    vectors = embed([phrase] + [text for text, _ in rows])
    phrase_vector, record_vectors = vectors[0], vectors[1:]
    scored = [
        (sum(a * b for a, b in zip(phrase_vector, vector)), label)
        for vector, (_, label) in zip(record_vectors, rows)
    ]
    score, label = max(scored)
    if score >= threshold:
        return True, f'Matched "{phrase}" to {label} ({score:.2f})'
    return False, f'Nothing matched "{phrase}"; closest was {label} ({score:.2f})'


def _evaluate_condition(customer, data):
    """True/False for a Condition/Filter node's saved clause. A missing or
    unrecognised attribute, operator or value is False rather than an
    exception: a half-configured node should skip its Yes branch, never
    stop the run."""
    passed, _ = evaluate_condition(customer, data)
    return passed


def evaluate_condition(customer, data) -> tuple[bool, str]:
    """The clause, and a line saying why it went the way it did."""
    if data.get("conditionKind") == "semantic":
        return _semantic_match(customer, data)

    attribute = data.get("conditionAttribute")
    operator = data.get("conditionOperator")
    if attribute is None or operator is None:
        return False, "Condition is not configured."

    actual, as_text = _read(customer, attribute)
    if operator == "is_empty":
        return actual in (None, ""), f"{attribute} is {'empty' if actual in (None, '') else 'set'}."
    if operator == "is_not_empty":
        return actual not in (
            None,
            "",
        ), f"{attribute} is {'set' if actual not in (None, '') else 'empty'}."
    if actual is None:
        return False, f"{attribute} has no value."

    raw = data.get("conditionValue")
    if operator == "is_one_of":
        wanted = {part.strip().lower() for part in str(raw or "").split(",") if part.strip()}
        return str(actual).lower() in wanted, f"{attribute} = {actual}."
    if operator == "contains":
        return str(raw or "").lower() in str(actual).lower(), f"{attribute} = {actual}."

    expected = _coerce(as_text, raw)
    if expected is None:
        return False, f"{raw!r} is not comparable with {attribute}."
    if not as_text:
        actual = Decimal(str(actual))

    result = {
        "equals": lambda: actual == expected,
        "not_equals": lambda: actual != expected,
        "greater_than": lambda: actual > expected,
        "less_than": lambda: actual < expected,
    }.get(operator)
    if result is None:
        return False, f"Unknown operator {operator!r}."
    return result(), f"{attribute} = {actual}."


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


def _member(organisation, user_id):
    """A person in this organisation, or nothing. A scenario is saved JSON:
    an id in it is a claim, not a permission, so the tenant is checked here
    rather than trusted."""
    if not user_id:
        return None
    return User.objects.filter(pk=user_id, organisation=organisation).first()


def _least_loaded(organisation, function):
    """Whoever in that function carries the fewest live customers. Ties go
    to the lowest id, so the same graph routes the same way twice."""
    people = User.objects.filter(organisation=organisation, is_active=True)
    if function:
        people = people.filter(function=function)
    people = people.annotate(
        load=Count("owned_customers", filter=Q(owned_customers__is_archived=False))
    ).order_by("load", "id")
    return people.first()


def _handle_assign_owner(customer, data):
    organisation = customer.organisation
    if data.get("assignRule") == "least_loaded":
        person = _least_loaded(organisation, data.get("assignFunction"))
        if person is None:
            raise ValueError("Nobody in that function to assign to.")
    else:
        person = _member(organisation, data.get("assignTo"))
        if person is None:
            raise ValueError("This scenario names nobody in your organisation to assign to.")
    if customer.owner_id == person.id:
        return f"{person.name} already owns this"
    customer.owner = person
    customer.save(update_fields=["owner"])
    notify(
        recipient=person,
        actor=None,
        kind=Notification.Kind.CUSTOMER_ASSIGNED,
        message=f"{customer.name} was assigned to you by a scenario.",
        link=f"/organizations/{customer.id}",
    )
    return f"Assigned to {person.name}"


def _recipients(customer, data):
    who = data.get("notifyWho") or "owner"
    if who == "owner":
        return [customer.owner] if customer.owner_id else []
    if who == "manager":
        manager = customer.owner.reports_to if customer.owner_id else None
        return [manager] if manager else []
    return [_member(customer.organisation, data.get("notifyUser"))]


def _handle_notify(customer, data):
    people = [person for person in _recipients(customer, data) if person is not None]
    if not people:
        raise ValueError("Nobody to notify.")
    message = (data.get("notifyMessage") or "").strip() or f"A scenario flagged {customer.name}."
    for person in people:
        notify(
            recipient=person,
            actor=None,
            kind=Notification.Kind.CUSTOMER_ASSIGNED,
            message=f"{customer.name}: {message}",
            link=f"/organizations/{customer.id}",
        )
    return f"Notified {', '.join(person.name for person in people)}"


ACTION_HANDLERS = {
    "Send Email": _handle_send_email,
    "Create Task": _handle_create_task,
    "Set Attribute": _handle_set_attribute,
    "Churn Entity": _handle_churn_entity,
    "Assign Owner": _handle_assign_owner,
    "Notify": _handle_notify,
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
            result, why = evaluate_condition(customer, data)
            log.append(
                {
                    "node_id": current["id"],
                    "action": action,
                    "status": "ok",
                    "detail": f"Condition evaluated to {result}. {why}",
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
