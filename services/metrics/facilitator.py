"""The facilitator: a session's decisions into the review queue.

A multiplayer Copilot session is where people decide things — two CSMs and
a manager working an account, handing off, agreeing what happens next. Until
now that agreement lived only in the transcript. The facilitator reads the
session (who said what, who joined, who handed off to whom) alongside the
same figures the Ops agent sees, and writes what the people decided as
proposals — the same two kinds, validated the same way, landing in the same
review queue. Nothing the transcript says runs on its own: a person still
approves each item, and approval executes through the paths a person uses
by hand.

**Decisions, not suggestions.** The prompt is explicit that only what a
person decided or agreed to counts; an idea the assistant floated and no
one took up is not a decision. An empty answer is the right answer for a
session where nothing was settled.

**Costs a real model call**, logged under the `facilitator` purpose.
"""

import logging

from django.utils import timezone

from services.copilot.anthropic_client import get_completion
from services.copilot.models import Message, SessionEvent
from services.customers import forecast
from services.customers.models import Customer

from . import proposals

logger = logging.getLogger(__name__)

OUTPUT_TOKENS = 2000
MAX_DECISIONS = 5
TRANSCRIPT_TURNS = 60
TURN_CHARS = 1200


class NothingToDecideFrom(Exception):
    """A session with no human turns has no decisions in it."""


SYSTEM_PROMPT = """You are the facilitator of a working session inside a \
customer-success platform. Several people worked through a question with an \
assistant; your job is to write down what they DECIDED, as actions a manager \
can approve.

You will be given the organisation's figures (headline metrics, what moved, \
cuts, the accounts carrying the most revenue at risk, team members, open \
decisions) and then the session itself: which account it was about, who took \
part, who handed off to whom and why, and the transcript with each turn's \
author.

Return a JSON array of 0 to {max_decisions} decisions and nothing else — your \
reply starts with "[" and ends with "]", no preamble. Each is an object with:
  "kind": "task" or "initiative".
  "title": a specific imperative sentence, at most 80 characters.
  "rationale": 1-3 sentences saying who decided it and why, quoting the \
transcript or the figures.
  "evidence": an array of 1-4 short strings — a line from the transcript \
(prefixed with the author's name) or a figure from the input, quoted as given.
  "initiative_id": the id of an open decision this serves, or null.
  "action": for a task — {{"customer_id": <id from the accounts list>, \
"title": <task title>, "assignee": <a team member's exact name — the person \
who took it on in the session, else the account's owner>, "due_in_days": \
<1-60>, "priority": "high"|"medium"|"low"}}; for an initiative — \
{{"metric": <metric key>, "dimension": <a cut the metric has, or "">, \
"member": <member id from that cut, or "">, "target_value": <number>, \
"target_in_days": <14-180>}}.

Rules:
- Only what a person decided or explicitly agreed to. An idea the assistant \
offered that no person took up is not a decision. If nothing was decided, \
return [].
- Use only the account ids, member names, metric keys, cuts and member ids \
given. Anything else will be discarded.
- The session's own account is in the accounts list; prefer it for tasks.
- Do not propose an initiative that duplicates one already open; link to it \
instead and propose a task.
- Never propose emails, campaigns, price changes or anything sent to a \
customer. Tasks and decisions only.
- Never invent a figure, a name or a commitment nobody made.
"""


def _account_row(customer, organisation):
    """The session's account in the same shape as the exposure list, so
    it is a valid task target even when it carries no measurable downside."""
    row = forecast._row_payload(
        forecast.build_rows([customer], organisation, horizon=forecast.horizon_days({}))[0]
    )
    return {
        "id": row["id"],
        "name": row["name"],
        "owner": row["owner"],
        "owner_id": customer.owner_id,
        "arr": row["arr"],
        "downside": row["downside"],
        "days_to_renewal": row["days_to_renewal"],
        "health": row["health_category"],
        "risk": row["risk"],
        "factors": [f["label"] for f in row["factors"]] if row["factors"] else [],
        "product": customer.primary_product.name if customer.primary_product_id else None,
    }


def _authors(session):
    """Who wrote each user turn: the owner sent the opening query, and every
    later human turn is tagged by its `redirected` event."""
    owner = session.conversation.user.name
    by_message = {
        e.message_id: (e.actor.name if e.actor else owner)
        for e in session.events.filter(kind=SessionEvent.Kind.REDIRECTED).select_related("actor")
        if e.message_id
    }
    return owner, by_message


def build_evidence(session):
    """The Ops agent's evidence plus the session itself."""
    conversation = session.conversation
    organisation = conversation.organisation
    evidence = proposals.build_evidence(organisation)

    messages = list(conversation.messages.order_by("created_at", "id"))
    if not any(m.role == Message.Role.USER for m in messages):
        raise NothingToDecideFrom("Nobody has said anything in this session yet.")
    owner, authors = _authors(session)
    if len(messages) > TRANSCRIPT_TURNS:
        messages = messages[-TRANSCRIPT_TURNS:]
    transcript = [
        {
            "author": "Copilot" if m.role == Message.Role.ASSISTANT else authors.get(m.id, owner),
            "text": m.content[:TURN_CHARS],
        }
        for m in messages
    ]

    if session.customer_id and not any(
        a["id"] == session.customer_id for a in evidence["accounts"]
    ):
        customer = Customer.objects.select_related("owner", "primary_product").get(
            pk=session.customer_id
        )
        evidence["accounts"].append(_account_row(customer, organisation))

    participants = sorted(
        {owner, *(p.user.name for p in session.participants.select_related("user"))}
    )
    handoffs = [
        {
            "from": e.actor.name if e.actor else owner,
            "to": e.payload.get("to_user_name", ""),
            "note": e.payload.get("note", ""),
        }
        for e in session.events.filter(kind=SessionEvent.Kind.HANDED_OFF).select_related("actor")
    ]
    evidence["session"] = {
        "id": session.id,
        "conversation_id": conversation.id,
        "title": conversation.title,
        "customer_id": session.customer_id,
        "subject": (
            session.customer.name
            if session.customer_id
            else session.account.name
            if session.account_id
            else None
        ),
        "status": session.status,
        "participants": participants,
        "handoffs": handoffs,
        "transcript": transcript,
    }
    return evidence


def build_prompt(evidence):
    lines = [proposals.build_prompt(evidence)]
    s = evidence["session"]
    lines.append(f'\nTHE SESSION: "{s["title"]}" ({s["status"]})')
    lines.append(
        f"About: {s['subject']} (account id {s['customer_id']})"
        if s["customer_id"]
        else f"About: {s['subject'] or 'no particular account'}"
    )
    lines.append("Took part: " + ", ".join(s["participants"]))
    if s["handoffs"]:
        lines.append("Hand-offs:")
        for h in s["handoffs"]:
            note = f' — "{h["note"]}"' if h["note"] else ""
            lines.append(f"- {h['from']} handed off to {h['to']}{note}")
    lines.append("\nTRANSCRIPT:")
    for turn in s["transcript"]:
        lines.append(f"{turn['author']}: {turn['text']}")
    return "\n".join(lines)


def capture_decisions(session, *, requested_by=None):
    """One model call → the session's decisions, stored as proposals.

    Lets CopilotNotConfigured / CopilotRequestFailed / BudgetExceeded through
    for the view to map; raises NothingToProposeFrom on an empty book,
    NothingToDecideFrom on a session with no human turn, ValueError on an
    unreadable answer.
    """
    evidence = build_evidence(session)
    raw = get_completion(
        system=SYSTEM_PROMPT.format(max_decisions=MAX_DECISIONS),
        messages=[{"role": "user", "content": build_prompt(evidence)}],
        max_tokens=OUTPUT_TOKENS,
        purpose="facilitator",
        organisation=session.conversation.organisation,
        user=requested_by,
    )
    stored = proposals.store_answer(
        session.conversation.organisation,
        evidence,
        raw,
        limit=MAX_DECISIONS,
        generated_by=requested_by,
        session=session,
    )
    logger.info(
        "facilitator: session %s → %d decision(s) at %s",
        session.id,
        len(stored),
        timezone.now().isoformat(),
    )
    return stored
