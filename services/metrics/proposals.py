"""The Ops agent: proposals into the review queue.

Reads what the brain knows — the metric layer, what moved, the cuts, the
accounts carrying the downside, who owns what, which decisions are already
open — and proposes concrete next actions. Two kinds, both things a person
could do by hand through the app: a task on an account, or an initiative on
a number. Nothing else. No emails, no campaigns, nothing outward-facing —
an agent's reach here is exactly a person's, and a person still has to say
yes.

**What comes back is validated before it is stored.** The prompt gives the
agent account ids, member names, metric keys and cuts, and an answer that
names anything outside that set is dropped at generation — with a log line
— rather than becoming a proposal that fails at the click. The reviewer
sees only actions that will work.

**Costs a real model call.** Generation is an explicit POST.
"""

import json
import logging
import uuid
from datetime import timedelta

from django.utils import timezone

from services.accounts.models import User
from services.copilot.anthropic_client import get_completion
from services.customers import forecast
from services.customers.models import Customer, Task
from services.customers.scoping import SystemActor, live_customers

from . import feedback as feedback_log
from . import initiatives as initiative_rules
from .models import Initiative, Proposal
from .registry import BY_KEY, compute_all, compute_slices
from .signals import number, signals_for

logger = logging.getLogger(__name__)

OUTPUT_TOKENS = 2000
MAX_PROPOSALS = 5
EXPOSURE_LIMIT = 8
CUT_METRICS = ("at_risk_arr", "nrr", "poor_health_count")


class NothingToProposeFrom(Exception):
    """No customers — there is nothing to act on."""


SYSTEM_PROMPT = """You are the operations agent inside a customer-success \
platform, proposing next actions for a manager to approve.

You will be given the organisation's figures: headline metrics, what moved \
since the last month-end, a few cuts by owner and product, the accounts \
carrying the most revenue at risk (with their owner, renewal and the reasons \
behind the risk), the team members, and the decisions already open.

Propose 2 to {max_proposals} actions as a JSON array and nothing else. Each is \
an object with:
  "kind": "task" or "initiative".
  "title": a specific imperative sentence, at most 80 characters.
  "rationale": 2-4 sentences saying why, quoting the figures you rely on.
  "evidence": an array of 1-4 short strings, each one figure from the input, \
quoted as given (e.g. "Pizza Hut: USD 42,000 at risk, renews in 34 days").
  "initiative_id": the id of an open decision this serves, or null.
  "action": for a task — {{"customer_id": <id from the accounts list>, \
"title": <task title>, "assignee": <a team member's exact name — the person on \
the account's team responsible for the function the task needs (an engineer \
for a fix, sales for a commercial step), else the account owner>, \
"due_in_days": <1-60>, "priority": "high"|"medium"|"low"}}; \
for an initiative — {{"metric": <metric key>, "dimension": <a cut the metric \
has, or "">, "member": <member id from that cut, or "">, "target_value": \
<number>, "target_in_days": <14-180>}}.

Rules:
- Use only the account ids, member names, metric keys, cuts and member ids \
given. Anything else will be discarded.
- Do not propose an initiative that duplicates one already open; link to it \
instead and propose a task.
- Prefer the account with the most at risk and the nearest renewal.
- Never propose emails, campaigns, price changes or anything sent to a \
customer. Tasks and decisions only.
- Ground every claim in the figures given. Never invent a figure or a name.
"""


def _money(value, currency):
    return f"{currency} {value:,.0f}" if value is not None else "unmeasured"


def build_evidence(organisation):
    """Everything the agent is allowed to know."""
    actor = SystemActor(organisation)
    if not live_customers(actor).exists():
        raise NothingToProposeFrom("There are no live customers to act on.")

    currency = organisation.currency
    values = compute_all(organisation)
    signals = signals_for(organisation, values)
    slices = compute_slices(organisation)

    customers = list(forecast.filtered_customers(actor, {}))
    rows = forecast.build_rows(customers, organisation, horizon=forecast.horizon_days({}))
    exposure = forecast.exposure_list(rows, limit=EXPOSURE_LIMIT)
    owners = {c.owner_id: c.owner for c in customers if c.owner_id}
    # The account team: who answers for each account in each function
    # (services.knowledge), so a task lands with the person whose job it is.
    from services.knowledge.models import FunctionOwner

    team = {}
    for fo in FunctionOwner.objects.filter(
        customer_id__in=[r["id"] for r in exposure]
    ).select_related("user"):
        team.setdefault(fo.customer_id, {})[fo.get_function_display()] = fo.user.name
    accounts = []
    for row in exposure:
        customer = next(c for c in customers if c.id == row["id"])
        accounts.append(
            {
                "id": row["id"],
                "name": row["name"],
                "owner": row["owner"],
                "owner_id": customer.owner_id,
                "team": team.get(row["id"], {}),
                "arr": row["arr"],
                "downside": row["downside"],
                "days_to_renewal": row["days_to_renewal"],
                "health": row["health_category"],
                "risk": row["risk"],
                "factors": [f["label"] for f in row["factors"]] if row["factors"] else [],
                "product": customer.primary_product.name if customer.primary_product_id else None,
            }
        )

    members = [
        {"id": u.id, "name": u.name}
        for u in User.objects.filter(organisation=organisation, is_active=True).order_by("name")
    ]
    open_initiatives = [
        {
            "id": i.id,
            "title": i.title,
            "metric": i.metric,
            "member_label": i.member_label,
            "target_value": number(i.target_value),
            "target_by": i.target_by.isoformat(),
        }
        for i in Initiative.objects.filter(
            organisation=organisation,
            status__in=[Initiative.Status.PLANNED, Initiative.Status.ACTIVE],
        )
    ]
    cuts = []
    for key in CUT_METRICS:
        for dimension, members_ in slices.get(key, {}).items():
            cuts.append(
                {
                    "metric": key,
                    "dimension": dimension,
                    "members": [
                        {"member": m, "label": label, "value": number(v)}
                        for m, label, v in members_
                    ],
                }
            )
    return {
        "as_of": signals["as_of"],
        "baseline": signals["baseline"],
        "currency": currency,
        "metrics": {k: number(v) for k, v in values.items()},
        "signals": signals["signals"],
        "cuts": cuts,
        "accounts": accounts,
        "members": members,
        "owners": {str(uid): u.name for uid, u in owners.items()},
        "initiatives": open_initiatives,
    }


def build_prompt(evidence):
    currency = evidence["currency"]
    lines = [f"Figures as of {evidence['as_of']}. Currency: {currency}."]
    lines.append("\nHEADLINE METRICS:")
    for metric in BY_KEY.values():
        value = evidence["metrics"].get(metric.key)
        if metric.unit == "money":
            shown = _money(value, currency)
        elif metric.unit == "percent":
            shown = f"{value}%" if value is not None else "unmeasured"
        else:
            shown = str(int(value)) if value is not None else "unmeasured"
        lines.append(f"- {metric.label} [{metric.key}]: {shown}")

    lines.append("\nMATERIAL MOVES SINCE THE MONTH-END:")
    if not evidence["signals"]:
        lines.append("- none recorded yet")
    for s in evidence["signals"]:
        lines.append(
            f"- {s['label']}: {s['value']} from {s['previous']['value']} ({s['change']:+})"
        )

    lines.append("\nCUTS (metric key, cut, member id: label = value):")
    for cut in evidence["cuts"]:
        members = "; ".join(f"{m['member']}: {m['label']} = {m['value']}" for m in cut["members"])
        lines.append(f"- {cut['metric']} by {cut['dimension']}: {members}")

    lines.append("\nACCOUNTS CARRYING THE DOWNSIDE (id: name):")
    if not evidence["accounts"]:
        lines.append("- none: no account carries measurable downside right now")
    for a in evidence["accounts"]:
        if a["days_to_renewal"] is None:
            renewal = "no renewal date"
        elif a["days_to_renewal"] < 0:
            renewal = f"renewal overdue by {-a['days_to_renewal']} days"
        else:
            renewal = f"renews in {a['days_to_renewal']} days"
        factors = ", ".join(a["factors"]) or "no named factors"
        team = "; ".join(f"{fn} {name}" for fn, name in a.get("team", {}).items())
        lines.append(
            f"- {a['id']}: {a['name']} — account owner {a['owner']}"
            + (f" (team: {team})" if team else "")
            + ", "
            f"product {a['product'] or 'unrecorded'}, "
            f"ARR {_money(a['arr'], currency)}, downside {_money(a['downside'], currency)}, "
            f"{renewal}, health {a['health']}, risk {a['risk']:.0%} ({factors})"
        )

    lines.append("\nTEAM MEMBERS (exact names):")
    lines.extend(f"- {m['name']}" for m in evidence["members"])

    lines.append("\nOPEN DECISIONS (id: title):")
    if not evidence["initiatives"]:
        lines.append("- none")
    for i in evidence["initiatives"]:
        lines.append(
            f"- {i['id']}: {i['title']} — {i['metric']} {i['member_label'] or '(whole org)'} "
            f"to {i['target_value']} by {i['target_by']}"
        )
    return "\n".join(lines)


def _parse(raw):
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    try:
        payload = json.loads(text, strict=False)
    except json.JSONDecodeError as exc:
        # The model was told to answer with the array and nothing else, and
        # mostly does; when it wraps the array in a sentence anyway, the
        # array is still the answer. Seen live from the facilitator.
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end <= start:
            raise ValueError(f"The model's answer wasn't JSON: {exc}") from exc
        try:
            payload = json.loads(text[start : end + 1], strict=False)
        except json.JSONDecodeError as inner:
            raise ValueError(f"The model's answer wasn't JSON: {inner}") from inner
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list):
                payload = value
                break
    if not isinstance(payload, list):
        raise ValueError("The model returned JSON, but not an array.")
    return [item for item in payload if isinstance(item, dict)]


def _validate(organisation, evidence, item):
    """One answer → a stored `action`, or None (logged) if it names anything
    outside what the agent was given."""
    kind = str(item.get("kind") or "").strip().lower()
    action = item.get("action") or {}
    title = str(item.get("title") or "").strip()[:255]
    if not title or kind not in Proposal.Kind.values or not isinstance(action, dict):
        logger.warning(
            "proposal dropped: kind=%r title=%r action_is_dict=%s",
            kind,
            title,
            isinstance(action, dict),
        )
        return None
    item["kind"] = kind
    today = timezone.localdate()

    if kind == Proposal.Kind.TASK:
        ids = {a["id"] for a in evidence["accounts"]}
        customer_id = action.get("customer_id")
        if customer_id not in ids:
            logger.warning("proposal dropped: customer %r not in the accounts given", customer_id)
            return None
        names = {m["name"] for m in evidence["members"]}
        account = next(a for a in evidence["accounts"] if a["id"] == customer_id)
        assignee = action.get("assignee")
        if assignee not in names:
            assignee = account["owner"] if account["owner"] in names else next(iter(names), "")
        try:
            due_in = max(1, min(60, int(action.get("due_in_days", 14))))
        except (TypeError, ValueError):
            due_in = 14
        priority = (
            action.get("priority") if action.get("priority") in Task.Priority.values else "medium"
        )
        return {
            "customer_id": customer_id,
            "customer_name": account["name"],
            "title": str(action.get("title") or title)[:255],
            "assignee_name": assignee,
            "due_date": (today + timedelta(days=due_in)).isoformat(),
            "priority": priority,
        }

    metric = BY_KEY.get(action.get("metric"))
    if metric is None:
        logger.warning("proposal dropped: unknown metric %r", action.get("metric"))
        return None
    dimension = action.get("dimension") or ""
    member = str(action.get("member") or "")
    member_label = ""
    if dimension:
        cut = next(
            (
                c
                for c in evidence["cuts"]
                if c["metric"] == metric.key and c["dimension"] == dimension
            ),
            None,
        )
        if cut is None or dimension not in metric.slices:
            logger.warning("proposal dropped: %s has no cut %r", metric.key, dimension)
            return None
        found = next((m for m in cut["members"] if m["member"] == member), None)
        if found is None:
            logger.warning(
                "proposal dropped: member %r not in %s by %s", member, metric.key, dimension
            )
            return None
        member_label = found["label"]
    try:
        target_value = float(action.get("target_value"))
        target_in = max(14, min(180, int(action.get("target_in_days", 90))))
    except (TypeError, ValueError):
        logger.warning("proposal dropped: unreadable target on %s", metric.key)
        return None
    return {
        "metric": metric.key,
        "metric_label": metric.label,
        "dimension": dimension,
        "member": member if dimension else "",
        "member_label": member_label,
        "target_value": target_value,
        "target_by": (today + timedelta(days=target_in)).isoformat(),
    }


def generate_proposals(organisation, *, generated_by=None):
    """One model call → the proposals it produced that survived validation.

    Lets CopilotNotConfigured / CopilotRequestFailed through untouched so the
    view can map them; raises NothingToProposeFrom on an empty book and
    ValueError on an unreadable answer.
    """
    evidence = build_evidence(organisation)
    raw = get_completion(
        system=SYSTEM_PROMPT.format(max_proposals=MAX_PROPOSALS),
        messages=[{"role": "user", "content": build_prompt(evidence)}],
        max_tokens=OUTPUT_TOKENS,
        purpose="proposals",
        organisation=organisation,
        user=generated_by,
    )
    return store_answer(organisation, evidence, raw, limit=MAX_PROPOSALS, generated_by=generated_by)


def store_answer(organisation, evidence, raw, *, limit, generated_by=None, session=None):
    """Parse one answer, validate each item against what the prompt offered,
    and store the survivors as one batch. Shared with the facilitator, which
    asks a different question of the same evidence and stores the same
    kind of answer — tagged with the session it came from."""
    batch = uuid.uuid4().hex
    open_ids = {i["id"] for i in evidence["initiatives"]}
    stored = []
    for item in _parse(raw)[:limit]:
        action = _validate(organisation, evidence, item)
        if action is None:
            continue
        linked = item.get("initiative_id")
        stored.append(
            Proposal.objects.create(
                organisation=organisation,
                batch=batch,
                kind=item["kind"],
                title=str(item["title"]).strip()[:255],
                rationale=str(item.get("rationale") or "").strip(),
                evidence=[str(e) for e in (item.get("evidence") or []) if isinstance(e, str)][:4],
                action=action,
                initiative_id=linked if linked in open_ids else None,
                generated_by=generated_by,
                session=session,
            )
        )
    return stored


class AlreadyDecided(Exception):
    """A proposal is decided once."""


def approve(proposal, user, note=""):
    """Execute the action through the same path a person would use, and
    record what it created."""
    if proposal.status != Proposal.Status.PROPOSED:
        raise AlreadyDecided(f"This proposal was already {proposal.get_status_display().lower()}.")
    action = proposal.action
    if proposal.kind == Proposal.Kind.TASK:
        customer = Customer.objects.get(
            organisation=proposal.organisation, pk=action["customer_id"]
        )
        task = Task.objects.create(
            customer=customer,
            title=action["title"],
            assignee_name=action["assignee_name"],
            due_date=action["due_date"],
            priority=action["priority"],
            # The decision this work serves, so the initiative shows it.
            initiative=proposal.initiative,
        )
        result = {"task_id": task.id, "customer_id": customer.id}
    else:
        initiative = initiative_rules.create_initiative(
            proposal.organisation,
            user,
            title=proposal.title,
            hypothesis=proposal.rationale,
            metric=action["metric"],
            dimension=action["dimension"],
            member=action["member"],
            member_label=action["member_label"],
            target_value=initiative_rules.as_decimal(action["target_value"]),
            target_by=action["target_by"],
        )
        result = {"initiative_id": initiative.id}
    proposal.status = Proposal.Status.APPROVED
    proposal.decided_by = user
    proposal.decided_at = timezone.now()
    proposal.decision_note = note
    proposal.result = result
    proposal.save(update_fields=["status", "decided_by", "decided_at", "decision_note", "result"])
    feedback_log.record_proposal_decision(proposal, "approved", note, user)
    return proposal


def reject(proposal, user, note=""):
    if proposal.status != Proposal.Status.PROPOSED:
        raise AlreadyDecided(f"This proposal was already {proposal.get_status_display().lower()}.")
    proposal.status = Proposal.Status.REJECTED
    proposal.decided_by = user
    proposal.decided_at = timezone.now()
    proposal.decision_note = note
    proposal.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
    feedback_log.record_proposal_decision(proposal, "rejected", note, user)
    return proposal
