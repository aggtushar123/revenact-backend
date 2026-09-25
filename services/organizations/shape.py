"""Ordering, grouping, paging and totals for the portfolio — pure functions
over the entries `book.load_portfolio` returns. No queries.

Everything here runs over the whole filtered set: a section header or a tile
that counted only the page would count nothing useful.
"""

import base64
import binascii
import json
import math

from services.customers.models import Customer
from services.fx_rates.conversion import convert_to_org_currency

from .params import NUMERIC_SORT_KEYS

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


def order_entries(portfolio, sort_key, descending):
    """Missing values last in either direction; ties by name, then id. Python's
    sort is stable with `reverse=True` too, so the tiebreak survives it."""
    getter = SORT_GETTERS[sort_key]
    ranked = sorted(portfolio.entries, key=_tiebreak)
    present = [entry for entry in ranked if getter(entry, portfolio) is not None]
    missing = [entry for entry in ranked if getter(entry, portfolio) is None]
    present.sort(key=lambda entry: getter(entry, portfolio), reverse=descending)
    return present + missing


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
    entries = order_entries(portfolio, params.sort_key, params.descending)
    if not params.group:
        return entries, []
    entries = sorted(
        entries, key=lambda entry: _group_rank(*group_key(entry, params.group), params.group)
    )
    groups = build_groups(entries, params.group)
    if params.group_value is not None:
        entries = [e for e in entries if group_key(e, params.group)[0] == params.group_value]
    return entries, groups


def encode_cursor(entry_id, offset):
    raw = json.dumps({"id": entry_id, "offset": offset}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor):
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (binascii.Error, ValueError, UnicodeError):
        return None
    if not isinstance(data, dict):
        return None
    entry_id, offset = data.get("id"), data.get("offset")
    if not isinstance(entry_id, int) or not isinstance(offset, int) or offset < 0:
        return None
    return entry_id, offset


def paginate(entries, *, cursor, limit):
    """The cursor names the last row served and how many rows that was. The
    next page starts after that row; if the row has since left the set (just
    archived, say), the rows after it moved up by one, so start one earlier."""
    start = 0
    decoded = decode_cursor(cursor)
    if decoded is not None:
        entry_id, offset = decoded
        position = next(
            (i for i, entry in enumerate(entries) if entry.customer.pk == entry_id), None
        )
        start = position + 1 if position is not None else min(max(offset - 1, 0), len(entries))
    page = entries[start : start + limit]
    end = start + len(page)
    next_cursor = encode_cursor(page[-1].customer.pk, end) if page and end < len(entries) else None
    return page, next_cursor
