"""The story: one page and its counts, merged across every source.

Each record source contributes at most `limit + 1` rows after the cursor, in
one query, newest first; the merge keeps the newest `limit`, and there is a
next page exactly when more than `limit` rows came back in total. Counts are
one aggregate per source over the whole searched set, never the page, grouped
by account; every figure is summed from those. Health changes come from one
snapshot query (`health.health_entries`) and page in Python.

The base querysets are built once per request and shared by the page, the
counts and the attention block: each personal rule reads the org chart when
it is built, so building them again would add queries.
"""

from collections import Counter

from django.db.models import Count

from .cursor import Cut, after_q, decode_cursor, encode_cursor, fingerprint, is_after
from .health import health_entries
from .items import render
from .params import GROUP_KINDS, KINDS, NO_ACCOUNT
from .sources import SOURCES, horizon_for

HEALTH = "health"


def _account_id(item):
    return item["account"]["id"] if item["account"] else None


def matches_account(account_id, account) -> bool:
    """Whether a row filed on `account_id` (None: on the organisation itself)
    passes the `account` parameter."""
    if account is None:
        return True
    if account == NO_ACCOUNT:
        return account_id is None
    return account_id == account


def build_page(bases, health, params, scope):
    fp = fingerprint(params)
    cut = decode_cursor(params.cursor, fp)
    candidates = []
    for kind in params.page_kinds:
        if kind == HEALTH:
            if not params.q:
                candidates += [
                    (key, item)
                    for key, item in health
                    if is_after(key, cut) and matches_account(_account_id(item), params.account)
                ]
            continue
        source = SOURCES[kind]
        rows = bases[kind].filter(
            scope.parent_q(params.account), source.search_q(params.q), after_q(kind, cut)
        )
        if params.thread:
            rows = rows.filter(thread_id=params.thread)
        rows = rows.select_related(*source.related).order_by("-_at", "-id")[: params.limit + 1]
        candidates += [((row._at, kind, row.pk), render(kind, row, scope)) for row in rows]

    candidates.sort(key=lambda pair: pair[0], reverse=True)
    page = candidates[: params.limit]
    next_cursor = None
    if len(candidates) > params.limit:
        at, kind, ident = page[-1][0]
        next_cursor = encode_cursor(Cut(at=at, kind=kind, id=ident), fp)
    return [item for _key, item in page], next_cursor


def tally(bases, health, q):
    counts = {}
    for kind, source in SOURCES.items():
        rows = (
            bases[kind]
            .filter(source.search_q(q))
            .order_by()
            .values("account_id")
            .annotate(n=Count("id"))
        )
        counts[kind] = {row["account_id"]: row["n"] for row in rows}
    # Health items have no text to search, so a search leaves them all out.
    counts[HEALTH] = {} if q else dict(Counter(_account_id(item) for _key, item in health))
    return counts


def build_counts(counts, params, scope):
    """`by_kind` and `by_group` follow `account` and `q` but not `group` or
    `source`, so every filter chip keeps its number while one is selected;
    `by_account` follows `group`, `source` and `q` but not `account`, for the
    same reason. The accounts listed are exactly the ones in scope."""

    def total(kinds, account):
        return sum(
            n
            for kind in kinds
            for account_id, n in counts[kind].items()
            if matches_account(account_id, account)
        )

    by_kind = {kind: total((kind,), params.account) for kind in KINDS}
    by_group = {"all": sum(by_kind.values())}
    by_group.update(
        {group: sum(by_kind[kind] for kind in kinds) for group, kinds in GROUP_KINDS.items()}
    )
    selected = params.selected_kinds
    by_account = {"all": total(selected, None), NO_ACCOUNT: total(selected, NO_ACCOUNT)}
    by_account.update(
        {str(account_id): total(selected, account_id) for account_id in scope.accounts}
    )
    return {"by_group": by_group, "by_kind": by_kind, "by_account": by_account}


def build_story(user, scope, params, *, today):
    horizon = horizon_for(today)
    bases = {kind: source.base(user, scope, horizon=horizon) for kind, source in SOURCES.items()}
    health = health_entries(scope, horizon=horizon)
    items, next_cursor = build_page(bases, health, params, scope)
    return {
        "items": items,
        "next_cursor": next_cursor,
        "counts": build_counts(tally(bases, health, params.q), params, scope),
    }
