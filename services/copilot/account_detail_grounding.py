"""Grounds an answer asked on one account's page (`view: "detail"`).

The client says which account and, from "Ask about this", which story item
(`accounts_context`); this module recomputes the page for the asker with the
code that serves it:

- the account's portfolio row (`accounts_portfolio.book.load_portfolio` with
  `ids`), as the page's name row and tiles read it;
- the story's Needs attention block and its counts (`build_story` over
  `AccountScope` with `build_account_attention`, as `GET
  /accounts/<id>/story/` runs it);
- the story items of the last 30 days, newest first, at most 25 — the
  story's own first page;
- the story item the question is about, read again under its own rule;
- the records retrieval finds for the question on the account, each under
  its own rule.

First filter, always: `resolve_account_scope`, which 404s an account outside
`visible_accounts(asker)`. Every story source then applies its own record
rule (mail by mailbox owner and chain, tickets by department, notes and tasks
by their personal chains). Story text is record text and is fenced like every
Ask digest. Needs attention states only the renewal and the urgent tickets,
and the count line leaves out emails, notes and tasks: both follow the
asker's own rules, and the digest is shown to shared readers. The row, the
attention block, the count line, the story and the focus are written by the
organisation page's own helpers (`organization_detail_grounding`), so the
two pages read alike. The AI pulse reason is never quoted: it is
model-written from records under nobody's rule.

What a shared reader is checked against (`views._reply_readable_by`): the
organisations named on the "Part of" line (`customer_ids`), the tickets the
row and the story counted (`tickets`), and the account itself and every story
item quoted (`records`).
"""

from django.http import Http404
from django.utils import timezone

from services.account_story.attention import build_account_attention
from services.account_story.scope import resolve_account_scope
from services.accounts_portfolio.book import account_urgent_tickets, load_portfolio
from services.accounts_portfolio.params import parse_params
from services.customers.personal import ticket_snapshot
from services.organizations.story.build import build_story
from services.organizations.story.params import parse_story_params
from services.organizations.story.sources import SOURCES, horizon_for

from .context import Grounding
from .grounded_records import account_ref, record_ref, union_records
from .organization_detail_grounding import (
    RECORD_LIMIT,
    STORY_ITEMS,
    _attention_lines,
    _count_line,
    _focus_lines,
    _story_lines,
    nps_text,
    recent_items,
    renewal_phrase,
    row_lines,
)
from .retrieval import retrieve_with_sources

#: Every account page digest's first line ends with this; the Accounts system
#: prompt reads it (first line only — never record text) to title the fence
#: "Account page data".
DETAIL_SCREEN_MARKER = "(one account's page)"
#: A pipeline or ticket snapshot that counted nothing.
NO_SNAPSHOT = {"account_ids": [], "departments": []}


def renewal_text(entry):
    """The account's renewal as its row reads it. Accounts have no churn, so
    only the date decides."""
    return renewal_phrase(entry.account.renewal_date, entry.renewal_days)


def _header(account, organisations, currency):
    part_of = ", ".join(name for _pk, name in organisations)
    return [
        f"Screen: Accounts › {account.name} {DETAIL_SCREEN_MARKER}",
        f"Part of: {part_of or 'no organisation the asker can open'}",
        f"Currency: {currency}",
    ]


def _account_lines(entry, organisation):
    account = entry.account
    csat = "not set" if account.csat_score is None else f"{float(account.csat_score):.1f}"
    return row_lines(
        account,
        entry,
        organisation,
        heading=f"Account: {account.name}",
        scores=f"{nps_text(account)}; CSAT {csat}",
    )


def _item_ref(item, account_id):
    return record_ref(item["kind"], item["id"], customer_id=None, account_id=account_id)


def build_account_detail_grounding(user, context, question, *, today=None):
    today = today or timezone.localdate()
    organisation = user.organisation
    # SOC2:AUTH-02 the account is re-read for the asker: outside
    # `visible_accounts` is a 404 (the send's serializer 400s the common case)
    scope = resolve_account_scope(user, context["account"])
    account = scope.account

    portfolio = load_portfolio(user, parse_params({"ids": str(account.pk)}), today=today)
    if not portfolio.entries:
        raise Http404
    entry = portfolio.entries[0]
    story = build_story(
        user,
        scope,
        parse_story_params({"limit": str(STORY_ITEMS)}),
        today=today,
        attention=build_account_attention,
    )
    items = recent_items(story["items"], today)

    lines = _header(account, entry.organisations, organisation.currency)
    lines.extend(_account_lines(entry, organisation))
    lines.extend(_attention_lines(story["attention"]))
    lines.append(_count_line(story["counts"]["by_kind"]))
    lines.extend(_story_lines(items))
    focus_lines, focused = _focus_lines(user, scope, context.get("focus"), None, today)
    lines.extend(focus_lines)

    retrieved = retrieve_with_sources(account, limit=RECORD_LIMIT, query=question, viewer=user)
    if retrieved:
        lines.append(f"Records for {account.name}:")
        lines.extend(f"  - {item.line}" for item in retrieved)

    quoted = [*items, *([focused] if focused else [])]
    records = union_records(
        [account_ref(account.pk)], [_item_ref(item, account.pk) for item in quoted]
    )
    # The row's signal counts the account's urgent tickets; the story's counts
    # and attention count its readable tickets up to today.
    ticket_base = SOURCES["ticket"].base(user, scope, horizon=horizon_for(today))
    tickets = ticket_snapshot(account_urgent_tickets(user, [account.pk]), ticket_base)
    return Grounding(
        "\n".join(lines),
        [item.source for item in retrieved],
        account,
        customer_ids=sorted(pk for pk, _name in entry.organisations),
        pipeline=dict(NO_SNAPSHOT),
        tickets=tickets,
        records=records,
    )
