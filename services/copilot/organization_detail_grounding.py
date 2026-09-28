"""Grounds an answer asked on one organisation's page (`view: "detail"`).

The client says which organisation and which account chip
(`organization_detail_context`); this module recomputes the page for the
asker with the code that serves it:

- the organisation's portfolio row (`load_portfolio` with `ids` and
  `include_churned`, as the page's header asks for it);
- the story's Needs attention block and its counts (`build_story`);
- the story items of the last 30 days, newest first, at most 25 — the
  story's own first page, narrowed by the account chip;
- the story item the question is about ("Ask about this"), read again under
  its own rule;
- the records retrieval finds for the question, on the organisation or on
  the chip's account, each under its own rule.

First filter, always: `resolve_scope`, which 404s an organisation outside
`visible_customers(asker)` and keeps only the accounts in
`visible_accounts(asker)`; the chip must be one of them. Every story source
then applies its own record rule. Story text is record text — an inbound
email's subject is written by whoever sent it — and is fenced like every Ask
digest (`dashboard_system_prompt`). Needs attention states only the renewal
and the urgent tickets: its overdue tasks, Knowledge questions and live anomaly
depend on the asker's own rules, and the digest is shown to shared readers.

What a shared reader is checked against (`views._reply_readable_by`): the
organisation (`customer_ids`), the tickets the digest counted (`tickets`), and
every account it covered and every story item it quoted (`records`).
"""

from datetime import UTC, datetime, timedelta

from django.http import Http404
from django.utils import timezone

from services.attention.rules import support_tickets
from services.customers.models import Account
from services.customers.personal import ticket_snapshot
from services.customers.triage import ACTION_THRESHOLD
from services.organizations.book import load_portfolio
from services.organizations.params import parse_params
from services.organizations.story.build import build_story
from services.organizations.story.params import parse_story_params
from services.organizations.story.scope import resolve_scope
from services.organizations.story.sources import SOURCES, horizon_for

from .context import Grounding
from .dashboard_grounding import _days, _money, owner_name
from .grounded_records import account_ref, record_ref, union_records
from .organization_detail_context import detail_label, find_item
from .retrieval import retrieve_with_sources

#: The story window and cap the digest quotes (spec §3: "last 30 days, capped").
STORY_DAYS = 30
STORY_ITEMS = 25
#: Records retrieval returns for the question, as for one company elsewhere.
RECORD_LIMIT = 6

KIND_LABELS = {
    "activity": "Activity",
    "calendar_event": "Meeting",
    "call": "Call",
    "email": "Email",
    "health": "Health change",
    "note": "Note",
    "survey": "Survey",
    "task": "Task",
    "ticket": "Ticket",
}
#: Counted in the digest's all-time line: kinds open to the whole
#: organisation (no personal author/mailbox rule), plus tickets, whose
#: department-wise count is covered separately by the ticket snapshot a
#: shared reader is checked against. Email, note and task counts follow the
#: asker's own personal record rule and are never counted here — the digest
#: is shown to shared readers too, and counts don't carry a per-record
#: reference the way quoted items do.
COUNTED_GROUP_KINDS = {
    "conversations": ("activity", "call", "calendar_event"),
    "tickets": ("ticket",),
    "feedback": ("survey",),
    "health": ("health",),
}
GROUP_LABELS = {
    "conversations": "Conversations",
    "tickets": "Tickets",
    "feedback": "Feedback",
    "health": "Health & usage",
}


def _plural(n, noun):
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _header(scope, account, currency):
    names = ", ".join(scope.accounts.values()) or "none"
    if account is None:
        chip = (
            "all — the story below covers the organisation and every account of it the "
            f"asker can open ({names})"
        )
    else:
        chip = f"{scope.accounts[account]} — the story below is narrowed to it"
    return [
        f"Screen: Organizations › {detail_label(scope, account)} (one organisation's page)",
        f"Account: {chip}",
        f"Currency: {currency}",
    ]


def _renewal(entry):
    date, days = entry.customer.renewal_date, entry.renewal_days
    if date is None:
        return "no renewal date"
    if days < 0:
        return f"renewal was due {date.isoformat()} ({_days(-days)} overdue)"
    if days == 0:
        return f"renews today ({date.isoformat()})"
    return f"renews {date.isoformat()} (in {_days(days)})"


def _organisation_lines(entry, organisation):
    if entry is None:
        return ["Organisation: not in the asker's portfolio (archived rows included)."]
    customer = entry.customer
    arr = (
        "ARR unknown (no exchange rate)"
        if entry.arr is None
        else f"ARR {_money(entry.arr, organisation.currency)}"
    )
    trend = " → ".join(f"{score:.1f}" for score in entry.trend)
    factors = "; ".join(factor["label"] for factor in entry.triage.factors) or "no factors"
    touch = (
        "never touched"
        if entry.last_touch_days is None
        else f"last touched {_days(entry.last_touch_days)} ago"
    )
    pulse = (
        f"AI pulse {customer.ai_pulse_value if customer.ai_pulse_value is not None else '—'}, "
        f"CSM pulse {customer.csm_pulse_score if customer.csm_pulse_score is not None else '—'}"
    )
    lines = [
        f"Organisation: {customer.name}"
        + (" (churned)" if entry.churned else "")
        + f"; lifecycle {customer.get_lifecycle_stage_display()}; "
        f"owner {owner_name(customer, organisation)}",
        f"  Health {customer.health_category} ({customer.health_score}/10); "
        f"trend over the last months {trend}",
        f"  {arr}; {_renewal(entry)}; NPS "
        + ("not set" if customer.nps_score is None else str(customer.nps_score)),
        f"  Triage risk {entry.triage.score} ({factors}; {ACTION_THRESHOLD} or more needs "
        f"action now); {pulse}; {touch}",
    ]
    if entry.signal:
        lines.append(f"  Signal: {entry.signal['label']}")
    return lines


def _attention_lines(attention):
    lines = []
    renewal = attention["renewal"]
    if renewal is not None:
        days = renewal["days"]
        if renewal["overdue"]:
            lines.append(f"Renewal overdue: it was due {renewal['date']} ({_days(-days)} ago)")
        elif days == 0:
            lines.append(f"Renewal due today ({renewal['date']})")
        else:
            lines.append(f"Renewal due {renewal['date']} (in {_days(days)})")
    if attention["tickets"] is not None:
        tickets = attention["tickets"]
        lines.append(
            f"{_plural(tickets['count'], 'open High or Critical ticket')}, oldest opened "
            f"{_days(tickets['oldest_days'])} ago"
        )
    # Counts follow the viewer, and the digest is shown to shared readers
    # whose rules may differ from the asker's, so it never states an entry
    # that depends on the asker's own rules: overdue tasks (the personal
    # task-visibility chain), unanswered Knowledge questions
    # (`visible_questions`), or a live anomaly (shown only when the asker may
    # read its evidence).
    if not lines:
        return ["Needs attention: nothing."]
    return ["Needs attention:", *(f"  - {line}" for line in lines)]


def _count_line(by_kind):
    by_group = {
        group: sum(by_kind[kind] for kind in kinds) for group, kinds in COUNTED_GROUP_KINDS.items()
    }
    parts = "; ".join(f"{label} {by_group[group]}" for group, label in GROUP_LABELS.items())
    return f"Story records up to today (all time): {sum(by_group.values())} — {parts}"


def item_line(item):
    """One story item as the digest quotes it: the day, the kind, where it
    was filed, its title and one-line summary, and who."""
    where = item["account"]["name"] if item["account"] else "Organisation"
    line = f"{item['occurred_at'][:10]} · {KIND_LABELS[item['kind']]} · {where} · {item['title']}"
    if item["summary"]:
        line += f": {item['summary']}"
    if item["actor"]:
        line += f" ({item['actor']['name']})"
    return line


def recent_items(items, today):
    """The items of the last STORY_DAYS days, in the story's order.

    Compared as UTC instants, not local calendar dates: extracting `.date()`
    from an aware timestamp reads its own offset's calendar day, which can
    land on a different day than the timestamp's true UTC instant near
    midnight. `since` is always UTC midnight of the cutoff day, whatever
    offset an item's own timestamp carries.
    """
    cutoff = today - timedelta(days=STORY_DAYS)
    since = datetime(cutoff.year, cutoff.month, cutoff.day, tzinfo=UTC)
    return [item for item in items if datetime.fromisoformat(item["occurred_at"]) >= since]


def _item_ref(item, customer):
    account_id = item["account"]["id"] if item["account"] else None
    return record_ref(item["kind"], item["id"], customer_id=customer.pk, account_id=account_id)


def _story_lines(items):
    if not items:
        return [f"Recent story (last {STORY_DAYS} days): nothing."]
    return [
        f"Recent story (last {STORY_DAYS} days, newest first, at most {STORY_ITEMS}):",
        *(f"  - {item_line(item)}" for item in items),
    ]


def _focus_lines(user, scope, focus, account, today):
    if focus is None:
        return [], None
    item = find_item(user, scope, focus["kind"], focus["id"], account=account, today=today)
    if item is None:
        return ["The story item asked about is not one the asker can read here."], None
    return ["The asker is asking about this story item:", f"  - {item_line(item)}"], item


def build_detail_grounding(user, context, question, *, today=None):
    today = today or timezone.localdate()
    organisation = user.organisation
    # SOC2:AUTH-02 the organisation and its accounts are re-read for the asker:
    # outside `visible_customers` is a 404, and only `visible_accounts` are in scope
    scope = resolve_scope(user, context["organization"])
    customer = scope.customer
    account = context.get("account")
    if account is not None and account not in scope.accounts:
        raise Http404

    portfolio = load_portfolio(
        user,
        parse_params({"ids": str(customer.pk), "include_churned": "1"}),
        today=today,
    )
    entry = portfolio.entries[0] if portfolio.entries else None
    story_query = {"limit": str(STORY_ITEMS)}
    if account is not None:
        story_query["account"] = str(account)
    story = build_story(user, scope, parse_story_params(story_query), today=today)
    items = recent_items(story["items"], today)

    lines = _header(scope, account, organisation.currency)
    lines.extend(_organisation_lines(entry, organisation))
    lines.extend(_attention_lines(story["attention"]))
    lines.append(_count_line(story["counts"]["by_kind"]))
    lines.extend(_story_lines(items))
    focus_lines, focused = _focus_lines(user, scope, context.get("focus"), account, today)
    lines.extend(focus_lines)

    company = customer if account is None else Account.objects.get(pk=account)
    retrieved = retrieve_with_sources(company, limit=RECORD_LIMIT, query=question, viewer=user)
    if retrieved:
        lines.append(f"Records for {company.name}:")
        lines.extend(f"  - {item.line}" for item in retrieved)

    covered = list(scope.accounts) if account is None else [account]
    quoted = [*items, *([focused] if focused else [])]
    records = union_records(
        [account_ref(pk) for pk in covered], [_item_ref(item, customer) for item in quoted]
    )
    # The row's urgent-ticket signal counts every account's; the story's
    # counts and attention count the chip's.
    ticket_base = SOURCES["ticket"].base(user, scope, horizon=horizon_for(today))
    tickets = ticket_snapshot(
        support_tickets(user, [customer.pk]), ticket_base.filter(scope.parent_q(account))
    )
    return Grounding(
        "\n".join(lines),
        [item.source for item in retrieved],
        customer,
        customer_ids=[customer.pk],
        pipeline={"account_ids": [], "departments": []},
        tickets=tickets,
        records=records,
    )
