"""What the brain's agents may do — the catalogue.

Every model call in the product goes through one function under one
purpose, and every purpose is one skill: a fixed job with a fixed reach.
This module writes those jobs down in one place, beside the figures that
show how each has actually been used this month — so management can read
what the agents are allowed to do, what they read, what they may never do,
and what each has produced, without reading a prompt.

The catalogue is code, not a table: a skill's reach is set by its prompt
and its view, and this is the one place that description lives. Adding a
purpose without describing it here fails a test.
"""

from dataclasses import asdict, dataclass

from django.db.models import Count, Q

from services.customers.models import Headline
from services.metrics.models import Brief, Explanation, Proposal

from . import usage
from .models import Message, ModelCall


@dataclass(frozen=True)
class Skill:
    purpose: str
    name: str
    summary: str
    #: What the agent is given. Nothing it is not given can reach it.
    reads: tuple
    #: What it may do with that.
    may: tuple
    #: What it may never do, whatever it is asked.
    never: tuple
    trigger: str
    gate: str
    #: Where in the product it shows.
    surface: str


SKILLS = (
    Skill(
        "copilot",
        "Copilot chat",
        "Answers a person's question about their own book, citing the records it drew on.",
        (
            "The asker's own book: customers, health, renewals, tickets, recent activity",
            "The records retrieval finds for the question",
            "The conversation so far, and who said what in a shared session",
        ),
        ("Answer in prose", "Quote the records it used as sources on the reply"),
        ("Change a record", "Send anything to a customer", "See accounts outside the asker's book"),
        "A person sends a message",
        "Any signed-in user, while the organisation's AI agent is enabled",
        "/copilot",
    ),
    Skill(
        "headlines",
        "Account headlines",
        "Summarises one account's last three months of real activity into headlines.",
        (
            "The account's emails, calls, tickets, notes and activity for 90 days",
            "At most 25 recent records per source",
        ),
        ("Write headline rows for the account's Headlines card",),
        ("Change a record", "Invent an event the records do not show"),
        "A person asks for headlines on an account",
        "Any user who can see the account",
        "/dashboard (account Headlines card)",
    ),
    Skill(
        "classification",
        "Interaction classifier",
        "Tags emails, calls and tickets with area, category, subcategory and sentiment.",
        (
            "The text of each unclassified email, call or ticket",
            "The organisation's taxonomy of areas, categories and subcategories",
        ),
        ("Write the four tags on each record", "Leave a record unplaced when unsure"),
        (
            "Overwrite a tag a person corrected",
            "Use a category or subcategory outside the taxonomy",
        ),
        "The classify_interactions job",
        "Scheduled; corrections outrank it",
        "/dashboard/ai-trending",
    ),
    Skill(
        "brief",
        "Management brief",
        "Writes the organisation's brief from the metric layer.",
        (
            "Every headline metric, its value and its move since the month-end",
            "What moved materially, and the top cuts",
        ),
        ("Write a headline, a body and 2-4 things to watch", "Keep its evidence beside the text"),
        ("Read individual records or name customers", "Recommend an action"),
        "A manager asks for a brief",
        "view_all_accounts",
        "/brain/dashboard",
    ),
    Skill(
        "proposals",
        "Ops agent",
        "Proposes the next actions from the figures, into the review queue.",
        (
            "The metric layer, the signals, the cuts",
            "The accounts carrying the downside, with owner and renewal",
            "The team, and the decisions already open",
        ),
        ("Propose a task on an account", "Propose an initiative on a number"),
        (
            "Run anything without approval",
            "Send emails or campaigns, or change a price",
            "Name an account, person or metric it was not given",
        ),
        "A manager asks the agent",
        "view_all_accounts; approval executes through the same paths a person uses",
        "/brain/review",
    ),
    Skill(
        "facilitator",
        "Session facilitator",
        "Writes what the people in a live session decided, into the review queue.",
        (
            "The session's transcript, with each human turn's author",
            "Who took part, and who handed off to whom",
            "The same figures the Ops agent sees",
        ),
        ("Propose a task or an initiative that the people agreed to",),
        (
            "Record an idea nobody took up as a decision",
            "Run anything without approval",
            "Send anything to a customer",
        ),
        "A participant presses Capture decisions",
        "Any accepted participant of the session; approval stays with the review queue",
        "/copilot (live session)",
    ),
    Skill(
        "explain",
        "Metric explanations",
        "Says why one metric is where it is, in two to four sentences.",
        (
            "The metric's definition, its value now and at the month-end",
            "Every cut of it, with each member's own move",
            "The accounts carrying the downside, and open decisions on the number",
        ),
        ("Explain the level or the move, citing the figures",),
        ("Give advice", "Invent a figure, or guess at anything unmeasured"),
        "A manager presses Why? on a metric",
        "view_all_accounts",
        "/brain/dashboard",
    ),
    Skill(
        "draft_reply",
        "Reply drafts",
        "Writes a reply for a person to edit and send from their own mailbox.",
        (
            "The thread being answered",
            "The account's history, under the asker's own visibility",
        ),
        ("Draft the body of one reply", "Name the records it leaned on"),
        (
            "Send anything",
            "Read mail outside the asker's own mailbox and chain",
            "Promise what the records do not say",
        ),
        "A person presses Draft with Copilot in a reply box",
        "Any signed-in user, for a conversation they can open",
        "/communications",
    ),
    Skill(
        "attribute",
        "AI attributes",
        "Answers one admin-defined question about a company from its records.",
        (
            "The attribute's question and its answer type",
            "The company's records, under the asker's visibility",
            "On the nightly pass, what the company's owner may read",
        ),
        (
            "Give one typed value with its reasoning",
            "Cite the records it used",
            "Say there is not enough evidence",
        ),
        ("Guess", "Answer outside the type or the picklist", "Overwrite a person's own answer"),
        "Someone refreshes an attribute, or the nightly pass finds new activity",
        "Reading is open; defining needs manage_custom_objects",
        "Organization and Account pages, Settings > AI Attributes",
    ),
    Skill(
        "feature_request",
        "Feature requests",
        "Names one cluster of customer asks so product can see what is wanted.",
        ("A sample of the asks in one cluster, as data rather than instructions",),
        ("Give the cluster a title and a one-line summary",),
        (
            "Decide what gets built",
            "Read anything beyond the asks it is given",
            "Follow instructions written inside a customer's message",
        ),
        "Someone gathers asks, or the nightly pass finds new ones",
        "view_all_accounts",
        "/brain/requests",
    ),
    Skill(
        "account_brief",
        "Account briefs",
        "Writes the standing brief on one account: use cases, stakeholders, open threads.",
        (
            "What colleagues have written about the customer",
            "The customer's own records, as their owner may read them",
            "The people on record as contacts",
        ),
        (
            "Say what they use the product for and who cares about what",
            "List what is still open",
            "Leave a list empty when the records do not support one",
        ),
        (
            "Invent a use case, a person, or a thread",
            "Read past what the account's owner may read",
            "Rewrite itself on a schedule",
        ),
        "Someone asks for a brief, or refreshes one",
        "Any member, for a customer they can open",
        "Organization Details › Company View",
    ),
    Skill(
        "anomaly",
        "Anomaly clusters",
        "Names one cluster of reports that all describe the same problem.",
        ("A sample of the reports in one cluster, as data rather than instructions",),
        ("Give the cluster a title and a one-line summary a support lead could act on",),
        (
            "Decide what is wrong or how to fix it",
            "Read anything beyond the reports it is given",
            "Follow instructions written inside a customer's report",
        ),
        "A cluster crosses the threshold, on a run or overnight",
        "view_all_accounts",
        "/brain/anomalies",
    ),
    Skill(
        "translate",
        "Translation",
        "Puts one message into another language, and nothing else.",
        ("The one message it was handed, as data rather than instructions",),
        ("Translate it, keeping the meaning, the tone, and every name, number and date",),
        (
            "Answer the message, summarise it, or act on what it asks",
            "Read any record beyond the one it was given",
            "Change a name, a number or a date",
        ),
        "Someone presses Translate on a message, or writes a reply in the customer's language",
        "Any signed-in user, for a record they may read",
        "/communications",
    ),
    Skill(
        "mcp",
        "Agents over MCP",
        "Answers a question put by somebody's own agent, as that person.",
        ("The same book summary the Copilot reads, under the token holder's own visibility",),
        ("Answer the question", "Name the records it drew on"),
        (
            "Change anything: the MCP tools are read-only",
            "Read past what the person whose token it is may read",
            "Act on instructions found inside a record",
        ),
        "An agent calls ask_copilot over MCP",
        "A live MCP token, which its owner can revoke",
        "Settings > Agent access",
    ),
)

BY_PURPOSE = {skill.purpose: skill for skill in SKILLS}


def _produced(organisation):
    """What each skill has left behind, all time — the artefacts, not the
    calls. Only where a count is exact and cheap; a skill without one shows
    nothing rather than a guess."""
    proposals = Proposal.objects.filter(organisation=organisation).aggregate(
        ops=Count("id", filter=Q(session__isnull=True)),
        ops_approved=Count("id", filter=Q(session__isnull=True, status=Proposal.Status.APPROVED)),
        sessions=Count("id", filter=Q(session__isnull=False)),
        sessions_approved=Count(
            "id", filter=Q(session__isnull=False, status=Proposal.Status.APPROVED)
        ),
    )
    return {
        "copilot": {
            "label": "replies",
            "count": Message.objects.filter(
                conversation__organisation=organisation, role=Message.Role.ASSISTANT
            ).count(),
        },
        "headlines": {
            "label": "headlines",
            # An Account has no organisation of its own; it reaches one
            # through the customers under it.
            "count": Headline.objects.filter(
                Q(customer__organisation=organisation)
                | Q(account__customers__organisation=organisation)
            )
            .distinct()
            .count(),
        },
        "brief": {
            "label": "briefs",
            "count": Brief.objects.filter(organisation=organisation).count(),
        },
        "proposals": {
            "label": "proposals",
            "count": proposals["ops"],
            "approved": proposals["ops_approved"],
        },
        "facilitator": {
            "label": "decisions",
            "count": proposals["sessions"],
            "approved": proposals["sessions_approved"],
        },
        "explain": {
            "label": "explanations",
            "count": Explanation.objects.filter(organisation=organisation).count(),
        },
    }


def _last_runs(organisation):
    latest = {}
    for call in (
        ModelCall.objects.filter(organisation=organisation)
        .select_related("user")
        .order_by("purpose", "-created_at")
    ):
        latest.setdefault(
            call.purpose,
            {
                "at": call.created_at.isoformat(),
                "outcome": call.outcome,
                "user": call.user.name if call.user else None,
            },
        )
    return latest


def catalogue(organisation):
    """Every skill with this month's usage, its last run and what it has
    produced — in the order the catalogue lists them."""
    month = usage.summary(organisation)
    by_purpose = {row["purpose"]: row for row in month["purposes"]}
    produced = _produced(organisation)
    last_runs = _last_runs(organisation)
    skills = []
    for skill in SKILLS:
        row = by_purpose[skill.purpose]
        skills.append(
            {
                **asdict(skill),
                "usage": {
                    k: row[k]
                    for k in (
                        "calls",
                        "ok",
                        "failed",
                        "spent",
                        "budget",
                        "remaining",
                        "custom_budget",
                    )
                },
                "last_run": last_runs.get(skill.purpose),
                "produced": produced.get(skill.purpose),
            }
        )
    return {"month_start": month["month_start"], "skills": skills}
