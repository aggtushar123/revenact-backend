"""Ordering, grouping, paging and totals for the portfolio — pure functions
over the entries `book.load_portfolio` returns. No queries.

Everything here runs over the whole filtered set: a section header or a tile
that counted only the page would count nothing useful.
"""

import base64
import binascii
import json
import math
from datetime import date
from functools import total_ordering

from services.customers.models import Customer
from services.fx_rates.conversion import convert_to_org_currency

from .params import NUMERIC_SORT_KEYS
from .rows import row_payload

#: Money fields sort in the organisation's currency — a list mixing EUR and
#: USD contracts cannot be ranked on their raw numbers.
MONEY_FIELDS = frozenset(
    {
        "arr_billed_at_hq",
        "implementation_fee",
        "total_contract_value",
        "total_forecasted_renewal_revenue",
    }
)

HEALTH_GROUP_ORDER = ("poor", "average", "good")

RENEWAL_WINDOWS = (
    ("overdue", "Overdue"),
    ("30", "Within 30 days"),
    ("90", "31–90 days"),
    ("180", "91–180 days"),
    ("later", "Later"),
    ("none", "No renewal date"),
)

#: The bucket for "nobody" / "nothing", which always closes the list.
EMPTY_KEYS = frozenset({"unassigned", "none"})


def _numeric(field):
    def get(entry, portfolio):
        raw = getattr(entry.customer, field)
        if raw is None:
            return None
        if field in MONEY_FIELDS:
            converted = convert_to_org_currency(
                raw, entry.customer.currency, portfolio.organisation, rates=portfolio.rates
            )
            return None if converted is None else float(converted)
        return float(raw)

    return get


SORT_GETTERS = {
    "arr": lambda entry, portfolio: entry.arr,
    "health": lambda entry, portfolio: float(entry.customer.health_score),
    "renewal": lambda entry, portfolio: entry.customer.renewal_date,
    # Never contacted is the longest silence there is, as on Activity Tracking.
    "touch": lambda entry, portfolio: (
        math.inf if entry.last_touch_days is None else entry.last_touch_days
    ),
    "risk": lambda entry, portfolio: entry.triage.score,
    "name": lambda entry, portfolio: entry.customer.name.casefold(),
    **{field: _numeric(field) for field in NUMERIC_SORT_KEYS},
}


def _tiebreak(entry):
    return (entry.customer.name.casefold(), entry.customer.pk)


@total_ordering
class _Desc:
    """Wraps an orderable value so ascending comparison sees it in reverse —
    lets one tuple comparison serve both sort directions, for any orderable
    type (numbers, dates, names), without negating anything."""

    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value

    def __eq__(self, other):
        return self.value == other.value

    def __lt__(self, other):
        return other.value < self.value


def _wrap(value, descending):
    return _Desc(value) if descending else value


def _rank(entry, sort_key, descending, portfolio, group=""):
    """The exact tuple `select`, `order_entries` and the cursor all sort by:
    the section's position first when the list is grouped (`()` when it is
    not), then missing values last regardless of direction, then the sort
    value (reversed for descending), then the name/id tiebreak — always
    ascending, so ties keep one order in either direction. One function for
    all three means the list order and the paging order can never drift
    apart."""
    section = _group_rank(*group_key(entry, group), group) if group else ()
    value = SORT_GETTERS[sort_key](entry, portfolio)
    if value is None:
        return (section, 1, None, _tiebreak(entry))
    return (section, 0, _wrap(value, descending), _tiebreak(entry))


def order_entries(portfolio, sort_key, descending, group=""):
    """Sections in their fixed order, then missing values last in either
    direction; ties by name, then id."""
    return sorted(
        portfolio.entries,
        key=lambda entry: _rank(entry, sort_key, descending, portfolio, group),
    )


def renewal_window(days):
    if days is None:
        return "none"
    if days < 0:
        return "overdue"
    if days <= 30:
        return "30"
    if days <= 90:
        return "90"
    if days <= 180:
        return "180"
    return "later"


def group_key(entry, group):
    customer = entry.customer
    if group == "health":
        category = customer.health_category
        return category, Customer.HealthCategory(category).label
    if group == "owner":
        if customer.owner_id is None:
            return "unassigned", "Unassigned"
        return str(customer.owner_id), customer.owner.name
    if group == "lifecycle":
        return customer.lifecycle_stage, customer.get_lifecycle_stage_display()
    if group == "product":
        if customer.primary_product_id is None:
            return "none", "No product"
        return str(customer.primary_product_id), customer.primary_product.name
    key = renewal_window(entry.renewal_days)
    return key, dict(RENEWAL_WINDOWS)[key]


def _group_rank(key, label, group):
    if group == "health":
        return (HEALTH_GROUP_ORDER.index(key), "", "")
    if group == "lifecycle":
        return (Customer.LifecycleStage.values.index(key), "", "")
    if group == "renewal":
        return ([window for window, _label in RENEWAL_WINDOWS].index(key), "", "")
    # Owners and products by name; the key splits two people who share one.
    return (1 if key in EMPTY_KEYS else 0, label.casefold(), key)


def build_groups(entries, group):
    groups = {}
    for entry in entries:
        key, label = group_key(entry, group)
        bucket = groups.setdefault(key, {"key": key, "label": label, "count": 0, "arr": 0.0})
        bucket["count"] += 1
        if entry.arr is not None:
            bucket["arr"] += entry.arr
    ordered = sorted(groups.values(), key=lambda g: _group_rank(g["key"], g["label"], group))
    for bucket in ordered:
        bucket["arr"] = round(bucket["arr"], 2)
    return ordered


def select(portfolio, params):
    """The rows in list order — sections first, the chosen sort inside each —
    and the section totals over every row, before `group_value` narrows."""
    entries = order_entries(portfolio, params.sort_key, params.descending, params.group)
    if not params.group:
        return entries, []
    groups = build_groups(entries, params.group)
    if params.group_value is not None:
        entries = [e for e in entries if group_key(e, params.group)[0] == params.group_value]
    return entries, groups


def _dump_value(value):
    """A rank value, JSON-safe: dates as ISO strings, everything else as the
    JSON types it already is (a float, including `inf` for "never touched",
    or a casefolded name string)."""
    if value is None:
        return None, "none"
    if isinstance(value, date):
        return value.isoformat(), "date"
    if isinstance(value, str):
        return value, "str"
    return float(value), "num"


def _load_value(raw, kind):
    if kind == "none":
        return None
    if kind == "date":
        return date.fromisoformat(raw)
    if kind == "str":
        return raw
    if kind == "num":
        return float(raw)
    raise ValueError(kind)


def _sort_token(sort_key, descending):
    return f"-{sort_key}" if descending else sort_key


def encode_cursor(section, bucket, value, name, entry_id, sort, group, group_value):
    """The last served row's rank, unwrapped: its section's position (`[]`
    when the list is not grouped), bucket (0 present, 1 missing), its raw sort
    value, then the name/id tiebreak — exactly what `_rank` computes, minus
    the direction wrapping, which the next request's own `descending`
    re-applies. `sort` (e.g. "-arr"), `group` and `group_value` scope the
    cursor to the list it was cut from: a different sort or a different board
    column must not resume from it, even when the value types happen to
    compare (two numeric sorts, say)."""
    dumped, kind = _dump_value(value)
    raw = json.dumps(
        {
            "sec": list(section),
            "b": bucket,
            "v": dumped,
            "t": kind,
            "n": name,
            "id": entry_id,
            "s": sort,
            "g": group,
            "gv": group_value,
        },
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _load_section(raw):
    """`_group_rank`'s (position, name, key) triple, or `()` ungrouped."""
    if raw == []:
        return ()
    if (
        isinstance(raw, list)
        and len(raw) == 3
        and isinstance(raw[0], int)
        and isinstance(raw[1], str)
        and isinstance(raw[2], str)
    ):
        return tuple(raw)
    raise ValueError(raw)


def decode_cursor(cursor):
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        if not isinstance(data, dict):
            return None
        section = _load_section(data["sec"])
        bucket, kind = data["b"], data["t"]
        if bucket not in (0, 1):
            return None
        value = _load_value(data["v"], kind)
        name, entry_id = data["n"], data["id"]
        sort, group, group_value = data["s"], data["g"], data["gv"]
        if not isinstance(name, str) or not isinstance(entry_id, int):
            return None
        if not isinstance(sort, str) or not isinstance(group, str):
            return None
        if group_value is not None and not isinstance(group_value, str):
            return None
        if bool(section) != bool(group):
            return None
    except (binascii.Error, ValueError, UnicodeError, KeyError, TypeError):
        return None
    return section, bucket, value, name, entry_id, sort, group, group_value


def paginate(
    entries, *, cursor, limit, sort_key, descending, portfolio, group="", group_value=None
):
    """Keyset pagination over `select`'s order. The cursor names the last
    served row's full rank — section, bucket, sort value, name, id, exactly
    what `_rank` sorts by — plus the sort and board column it was cut from.
    The next page is every row that ranks strictly after it, found with a
    scan over the already-ordered `entries`. Rows added or removed anywhere
    else in the set, in any number, never cause a skip or a repeat: the cut is
    by value, not by a row count. A malformed, tampered, stale-typed, or
    wrong-sort/wrong-column cursor is treated as absent — the first page."""
    start = 0
    decoded = decode_cursor(cursor)
    current_sort = _sort_token(sort_key, descending)
    if decoded is not None and decoded[5] == current_sort and decoded[6:] == (group, group_value):
        section, bucket, value, name, entry_id, *_scope = decoded
        # Missing values are never wrapped in `_rank` either — only a present
        # value's direction is reversed.
        wrapped = value if bucket == 1 else _wrap(value, descending)
        cursor_rank = (section, bucket, wrapped, (name, entry_id))
        try:
            start = next(
                (
                    index
                    for index, entry in enumerate(entries)
                    if _rank(entry, sort_key, descending, portfolio, group) > cursor_rank
                ),
                len(entries),
            )
        except TypeError:
            # A cursor built for a different sort's value type compares
            # against nothing usefully — the safest read is the first page.
            start = 0
    page = entries[start : start + limit]
    next_cursor = None
    if page and start + len(page) < len(entries):
        section, last_bucket, last_value, (last_name, last_id) = _rank(
            page[-1], sort_key, descending, portfolio, group
        )
        raw_value = last_value.value if isinstance(last_value, _Desc) else last_value
        next_cursor = encode_cursor(
            section, last_bucket, raw_value, last_name, last_id, current_sort, group, group_value
        )
    return page, next_cursor


#: The Renewing tile's two windows — `/customers/?renewal_within=` counts.
RENEWING_WINDOWS = (30, 90)


def build_summary(entries):
    """The five tiles, over every filtered row. Health, NPS and lifecycle are
    `CustomerStatsView`'s arithmetic (ARR converted, MRR = ARR / 12 per
    customer, unconvertible money counted but not summed, NPS by sign);
    renewals are the list's `renewal_within` rule (overdue in, Churn stage
    out)."""
    categories = Customer.HealthCategory.values
    health = {category: 0 for category in categories}
    health_arr = {category: 0.0 for category in categories}
    health_mrr = {category: 0.0 for category in categories}
    stages = {stage: {"count": 0, "arr": 0.0} for stage in Customer.LifecycleStage.values}
    promoters = passives = detractors = 0
    renewing = {str(days): 0 for days in RENEWING_WINDOWS}
    total = 0.0
    unconverted = 0

    for entry in entries:
        customer = entry.customer
        category = customer.health_category
        health[category] += 1
        stages[customer.lifecycle_stage]["count"] += 1
        if entry.arr is None:
            unconverted += 1
        else:
            health_arr[category] += entry.arr
            health_mrr[category] += entry.arr / 12
            stages[customer.lifecycle_stage]["arr"] += entry.arr
            total += entry.arr
        if customer.nps_score is not None:
            if customer.nps_score > 0:
                promoters += 1
            elif customer.nps_score == 0:
                passives += 1
            else:
                detractors += 1
        if (
            customer.lifecycle_stage != Customer.LifecycleStage.CHURN
            and entry.renewal_days is not None
        ):
            for days in RENEWING_WINDOWS:
                if entry.renewal_days <= days:
                    renewing[str(days)] += 1

    scored = promoters + passives + detractors
    return {
        "health": {
            **health,
            "arr": {category: round(value, 2) for category, value in health_arr.items()},
            "mrr": {category: round(value, 2) for category, value in health_mrr.items()},
        },
        "nps": {
            "promoters": promoters,
            "passives": passives,
            "detractors": detractors,
            "score": round((promoters - detractors) / scored * 100) if scored else 0,
        },
        "lifecycle": [
            {
                "value": value,
                "label": label,
                "count": stages[value]["count"],
                "arr": round(stages[value]["arr"], 2),
            }
            for value, label in Customer.LifecycleStage.choices
        ],
        "accounts": len(entries),
        "arr": round(total, 2),
        "unconverted_count": unconverted,
        "renewing": renewing,
    }


def build_listing(portfolio, params, *, filters):
    """The endpoint's body. `count` is the rows this query pages through
    (after `group_value`); `groups` and `summary` are over the whole filtered
    set, so a board column's header and the tiles never shrink to a page."""
    entries, groups = select(portfolio, params)
    page, next_cursor = paginate(
        entries,
        cursor=params.cursor,
        limit=params.limit,
        sort_key=params.sort_key,
        descending=params.descending,
        portfolio=portfolio,
        group=params.group,
        group_value=params.group_value,
    )
    return {
        "results": [row_payload(entry) for entry in page],
        "next_cursor": next_cursor,
        "count": len(entries),
        "groups": groups,
        "summary": build_summary(portfolio.entries),
        "filters": filters,
        "currency": portfolio.organisation.currency,
    }
