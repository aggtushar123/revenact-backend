"""Ordering, grouping, paging and totals for the Accounts portfolio — pure
functions over the entries `book.load_portfolio` returns. No queries.

The cursor (`keyset_page`), the section order (`group_rank`) and the renewal
windows are Organizations' own. The sort values, group keys and fingerprint
are the account's: they read `entry.account` and the account's filters.
Everything runs over the whole filtered set, so a section header or a tile
never counts only the page.
"""

import hashlib
import json

from services.customers.models import Customer
from services.organizations.shape import (
    RENEWAL_WINDOWS,
    group_rank,
    keyset_page,
    renewal_window,
    wrap_desc,
)

SORT_GETTERS = {
    "risk": lambda entry: entry.triage.score,
    "arr": lambda entry: entry.arr,
    "renewal": lambda entry: entry.account.renewal_date,
    "health": lambda entry: float(entry.account.health_score),
    "name": lambda entry: entry.account.name.casefold(),
}


def _tiebreak(entry):
    return (entry.account.name.casefold(), entry.account.pk)


def group_key(entry, group):
    account = entry.account
    if group == "health":
        category = account.health_category
        return category, Customer.HealthCategory(category).label
    if group == "owner":
        if account.owner_id is None:
            return "unassigned", "Unassigned"
        return str(account.owner_id), account.owner.name
    if group == "lifecycle":
        return account.lifecycle_stage, account.get_lifecycle_stage_display()
    key = renewal_window(entry.renewal_days)
    return key, dict(RENEWAL_WINDOWS)[key]


def _rank(entry, sort_key, descending, group=""):
    """The tuple `order_entries` and the cursor both sort by — Organizations'
    `_rank` shape: section, then missing values last in either direction,
    then the (direction-wrapped) value, then name and id ascending."""
    section = group_rank(*group_key(entry, group), group) if group else ()
    value = SORT_GETTERS[sort_key](entry)
    if value is None:
        return (section, 1, None, _tiebreak(entry))
    return (section, 0, wrap_desc(value, descending), _tiebreak(entry))


def order_entries(entries, sort_key, descending, group=""):
    return sorted(entries, key=lambda entry: _rank(entry, sort_key, descending, group))


def build_groups(entries, group):
    groups = {}
    for entry in entries:
        key, label = group_key(entry, group)
        bucket = groups.setdefault(key, {"key": key, "label": label, "count": 0, "arr": 0.0})
        bucket["count"] += 1
        bucket["arr"] += entry.arr
    ordered = sorted(groups.values(), key=lambda g: group_rank(g["key"], g["label"], group))
    for bucket in ordered:
        bucket["arr"] = round(bucket["arr"], 2)
    return ordered


def select(portfolio, params):
    """The rows in list order — sections first, the chosen sort inside each —
    and the section totals over every row, before `group_value` narrows."""
    entries = order_entries(portfolio.entries, params.sort_key, params.descending, params.group)
    if not params.group:
        return entries, []
    groups = build_groups(entries, params.group)
    if params.group_value is not None:
        entries = [e for e in entries if group_key(e, params.group)[0] == params.group_value]
    return entries, groups


def filter_fingerprint(params):
    """A short hash of everything that decides which rows a list holds and in
    what order, but not `cursor` or `limit`, so a cursor from a list with
    other filters reads the new list from its first page. Multi-value filters
    compare as sets."""
    state = [
        params.search,
        sorted(set(params.organisations)),
        params.owner,
        sorted(set(params.lifecycles)),
        sorted(set(params.health)),
        params.renews_within,
        params.nps,
        None if params.ids is None else sorted(set(params.ids)),
        params.sort,
        params.group,
        params.group_value,
    ]
    digest = hashlib.sha256(json.dumps(state, separators=(",", ":")).encode())
    return digest.hexdigest()[:16]


def paginate(entries, *, params):
    """Organizations' keyset cursor over `select`'s order."""
    sort_key, descending, group = params.sort_key, params.descending, params.group
    return keyset_page(
        entries,
        rank=lambda entry: _rank(entry, sort_key, descending, group),
        cursor=params.cursor,
        limit=params.limit,
        descending=descending,
        fingerprint=filter_fingerprint(params),
        grouped=bool(group),
    )
