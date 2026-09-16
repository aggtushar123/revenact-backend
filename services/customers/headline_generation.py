"""Turns a Customer's or Account's real activity records into Headline
rows — the thing the Headlines card's own "Data sources" footer has
always claimed was happening, and until now wasn't (the tab shipped
with a hardcoded two-item array).

Pulled into its own module for the same reason
services/copilot/anthropic_client.py is: one home for "how do we build
this prompt and read the answer back", trivially mockable in tests.
Every test in this repo patches `generate_headlines` or
`get_completion` rather than making a real, paid call.

Deliberately reuses services.copilot's client rather than adding a
second one — the provider choice (Anthropic direct vs Bedrock),
credential handling, and error taxonomy are already settled there, and
a Headline is no more special than a Copilot reply as far as "call the
model once, synchronously" goes.
"""

import json
from datetime import timedelta

from django.utils import timezone

from services.copilot.anthropic_client import get_completion

from .models import Headline

# How far back to read. Matches the mock's own "Last 3 months" TL;DR —
# far enough to see a renewal cycle form, short enough that the prompt
# stays well inside one request.
DEFAULT_WINDOW_DAYS = 90

# Per source, the most recent N records to include. A busy account can
# have hundreds of activities; the model gets the recent, representative
# slice rather than a truncated dump, and the prompt stays predictable
# in size.
MAX_RECORDS_PER_SOURCE = 25


class NothingToSummarise(Exception):
    """Raised when the parent has no records at all in the window —
    the view turns this into a clear 422 rather than paying for a call
    that can only invent things."""


SYSTEM_PROMPT = """You are a customer-success analyst writing the \
"Headlines" summary cards for one account in a CS platform.

You will be given that account's real records from a recent window: \
notes, emails, tickets and logged activities.

Write a JSON object with exactly two keys:

"summary": an object with "title" and "content". This is the pinned \
TL;DR card. "title" must be of the form "TL;DR (<window>)". "content" \
is 3-6 sentences covering overall health, what dominated the period, \
and any risks. If there are no risks, say so explicitly.

"headlines": an array of 1-4 objects, each a distinct storyline that \
runs across several records — a renewal, an escalation, an expansion, \
an onboarding. Each has:
  - "title": a specific noun phrase naming the account and the theme.
  - "content": 3-5 sentences. Name the real people and companies that \
appear in the records. Do not invent participants.
  - "status": exactly one of "open", "in_progress", "closed".
  - "period_start" and "period_end": "YYYY-MM-DD", the first and last \
dated record that storyline draws on.

Rules:
- Ground every claim in the records given. Never invent a metric, a \
name, a date, or a dollar figure that is not present.
- If the records are thin, write fewer headlines. One well-supported \
headline beats four speculative ones.
- Write plainly, in the past tense, no marketing language.
- Return only the JSON object. No prose before or after, no code fence.
"""


def _fmt(records, fields):
    """One line per record, only the fields that carry meaning — the
    model doesn't need ids or foreign keys, and leaving them out keeps
    the prompt readable when debugging a bad generation."""
    lines = []
    for record in records:
        parts = []
        for label, value in fields(record):
            if value:
                parts.append(f"{label}: {value}")
        if parts:
            lines.append("  - " + " | ".join(parts))
    return lines


def collect_records(parent, *, window_days=DEFAULT_WINDOW_DAYS):
    """Every record within the window under one Customer or Account,
    grouped by the DataSource it maps to. Sources with nothing in them
    are left out entirely, so `data_sources` on the resulting Headline
    names only what was genuinely read.

    Both parents expose the same `notes`/`emails`/`tickets`/`activities`
    related managers, so this needs no branch on parent type."""

    # Two cutoffs for one window: Note/Ticket/Activity date their
    # records with a plain DateField, but Email.sent_at is a
    # DateTimeField — filtering that one with a `date` hands Django a
    # naive datetime and it warns, so it gets the aware value instead.
    since_dt = timezone.now() - timedelta(days=window_days)
    since = since_dt.date()
    collected = {}

    # Headlines are read by everyone who opens the record, so a personal
    # note (services.customers.personal) must not surface through one.
    notes = list(
        parent.notes.filter(logged_at__gte=since, author__isnull=True)[:MAX_RECORDS_PER_SOURCE]
    )
    if notes:
        collected[Headline.DataSource.NOTES] = _fmt(
            notes,
            lambda n: [
                ("date", n.logged_at),
                ("title", n.title),
                ("author", n.author_name),
                ("body", n.body),
            ],
        )

    emails = list(parent.emails.filter(sent_at__gte=since_dt)[:MAX_RECORDS_PER_SOURCE])
    if emails:
        collected[Headline.DataSource.EMAILS] = _fmt(
            emails,
            lambda e: [
                ("date", e.sent_at),
                ("subject", e.subject),
                ("from", e.sender_name),
                ("to", e.recipient_name),
                ("body", e.body),
            ],
        )

    tickets = list(parent.tickets.filter(opened_at__gte=since)[:MAX_RECORDS_PER_SOURCE])
    if tickets:
        collected[Headline.DataSource.TICKETS] = _fmt(
            tickets,
            lambda t: [
                ("date", t.opened_at),
                ("ticket", t.ticket_number),
                ("title", t.title),
                ("status", t.get_status_display()),
                ("priority", t.get_priority_display()),
                ("assignee", t.assignee_name),
            ],
        )

    # Activity has no free-text body at all — `type` *is* the card's
    # title (see that model's own docstring), so the display label plus
    # the date is genuinely everything it carries.
    activities = list(parent.activities.filter(occurred_at__gte=since)[:MAX_RECORDS_PER_SOURCE])
    if activities:
        collected[Headline.DataSource.ACTIVITIES] = _fmt(
            activities,
            lambda a: [("date", a.occurred_at), ("event", a.get_type_display())],
        )

    return collected


def build_prompt(parent, collected, *, period_label):
    """The single user message. Named the parent explicitly — the model
    is asked to use real names, so it needs to know whose account this
    is rather than inferring it from whoever appears most often.

    `period_label` is the caller's own wording, not the raw day count.
    The system prompt asks for a title of the form "TL;DR (<window>)",
    so whatever wording lands here is what ends up on the card — pass
    the day count and a caller asking for "Last 13 months" gets a card
    titled "Last 400 days" over a footer reading "Last 13 months"."""

    blocks = [f"Account: {parent}", f"Window: {period_label}.", ""]
    for source, lines in collected.items():
        blocks.append(f"{Headline.DataSource(source).label}:")
        blocks.extend(lines)
        blocks.append("")
    return "\n".join(blocks)


def _parse(raw):
    """The prompt asks for bare JSON, but models sometimes wrap it in a
    fence anyway — strip one if present rather than failing a whole
    generation over punctuation."""

    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        if text.startswith("json"):
            text = text[4:].strip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"The model didn't return valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("The model returned JSON, but not an object.")
    return payload


def _clean_status(value):
    """An unrecognised status becomes OPEN rather than failing the whole
    generation — the card renders a status either way, and losing four
    good headlines over one bad enum value would be the worse trade."""
    valid = set(Headline.Status.values)
    return value if value in valid else Headline.Status.OPEN


def _organisation_of(parent):
    organisation = getattr(parent, "organisation", None)
    if organisation is not None:
        return organisation
    customer = parent.customers.first()
    return customer.organisation if customer is not None else None


def generate_headlines(parent, *, window_days=DEFAULT_WINDOW_DAYS, time_period_label=None):
    """Reads `parent`'s records, asks the model for cards, and returns
    unsaved Headline instances — saving is the view's job, so the
    replace-old-with-new step stays in one transaction there.

    Raises NothingToSummarise when the window is empty, ValueError when
    the model's answer can't be read, and lets
    CopilotNotConfigured/CopilotRequestFailed through untouched so the
    view can map them to the same 503/502 SendMessageView already uses.
    """

    collected = collect_records(parent, window_days=window_days)
    if not collected:
        raise NothingToSummarise(
            f"{parent} has no notes, emails, tickets or activities in the "
            f"last {window_days} days — nothing to summarise yet."
        )

    label = time_period_label or f"Last {window_days} days"
    prompt = build_prompt(parent, collected, period_label=label)
    raw = get_completion(
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
        purpose="headlines",
        organisation=_organisation_of(parent),
    )
    payload = _parse(raw)

    sources = list(collected)
    generated_at = timezone.now()
    parent_field = "customer" if parent.__class__.__name__ == "Customer" else "account"
    built = []

    summary = payload.get("summary")
    if isinstance(summary, dict) and summary.get("content"):
        built.append(
            Headline(
                kind=Headline.Kind.SUMMARY,
                title=summary.get("title") or f"TL;DR ({label})",
                content=summary["content"],
                time_period_label=label,
                data_sources=sources,
                generated_at=generated_at,
                **{parent_field: parent},
            )
        )

    for item in payload.get("headlines") or []:
        if not isinstance(item, dict) or not item.get("content"):
            continue
        built.append(
            Headline(
                kind=Headline.Kind.HEADLINE,
                title=item.get("title") or "Untitled headline",
                content=item["content"],
                status=_clean_status(item.get("status")),
                period_start=item.get("period_start") or None,
                period_end=item.get("period_end") or None,
                data_sources=sources,
                generated_at=generated_at,
                **{parent_field: parent},
            )
        )

    if not built:
        raise ValueError("The model returned no usable cards.")
    return built
