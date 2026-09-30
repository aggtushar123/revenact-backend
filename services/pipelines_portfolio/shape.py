"""Ordering, grouping, paging and totals for the Pipelines book — pure
functions over `book.load_book`'s entries. No queries.

The order, section totals, `group_value` and the cursor are
`portfolio_core`'s, shared with Organizations and Accounts. The sort values,
the sections (stage, close month, parent, owner, department, priority), their
order and the fingerprint are the pipeline's. Everything runs over the whole
filtered set, so a section header or a tile never counts only the page.
"""

import calendar

from services.portfolio_core.shape import PortfolioOrder, fingerprint, name_rank

from .book import OUTSIDE_OWNER
from .params import NO_DEPARTMENT, OUTSIDE, UNASSIGNED
from .rows import department_label

PRIORITY_ORDER = ("high", "medium", "low")
#: So `-priority` puts High first.
PRIORITY_RANK = {"high": 3, "medium": 2, "low": 1}
#: Close-month sections around the months: Overdue first, No date last.
OVERDUE, NO_DATE = "overdue", "none"
#: Owner sections after the named people: outside the tenant, then nobody.
OWNER_BUCKET_ORDER = (OUTSIDE, UNASSIGNED)


def sort_value(entry, sort_key, kind):
    item = entry.item
    if sort_key == "mrr":
        return entry.mrr
    if sort_key == "date":
        return entry.when
    if sort_key == "priority":
        return PRIORITY_RANK[item.priority]
    if sort_key == "stage":
        return kind.stages.index(item.stage)
    return item.title.casefold()


def _tiebreak(entry):
    return (entry.item.title.casefold(), entry.item.pk)


def month_bucket(entry):
    """Overdue (open and past its date), else the date's month, else No
    date. A closed item past its date sits in its month."""
    if entry.overdue:
        return OVERDUE, "Overdue"
    if entry.when is None:
        return NO_DATE, "No date"
    return (
        entry.when.strftime("%Y-%m"),
        f"{calendar.month_name[entry.when.month]} {entry.when.year}",
    )


def _owner_bucket(owner):
    if owner is None:
        return UNASSIGNED, "Unassigned"
    if owner is OUTSIDE_OWNER:
        # Never merged with Unassigned: an owner exists, just not one of ours.
        return OUTSIDE, OUTSIDE_OWNER.name
    return str(owner.pk), owner.name


def group_key(entry, group, kind):
    item = entry.item
    if group == "stage":
        return item.stage, item.get_stage_display()
    if group == "month":
        return month_bucket(entry)
    if group == "parent":
        parent = entry.parent
        return f"{parent['type']}:{parent['id']}", parent["name"]
    if group == "owner":
        return _owner_bucket(entry.owner)
    if group == "department":
        if not item.department:
            return NO_DEPARTMENT, "No department"
        return item.department, department_label(item.department) or item.department
    return item.priority, item.get_priority_display()


def section_rank(key, label, group, kind):
    """A section's position, as the `(int, str, str)` triple the cursor
    carries: stage and priority in their fixed order; close month Overdue,
    then the months ascending, then No date; owners by name, then Not in
    your book, then Unassigned; parent and department by name, the empty
    bucket last."""
    if group == "stage":
        return (kind.stages.index(key), "", "")
    if group == "priority":
        return (PRIORITY_ORDER.index(key), "", "")
    if group == "month":
        if key == OVERDUE:
            return (0, "", "")
        if key == NO_DATE:
            return (2, "", "")
        return (1, key, key)
    if group == "owner" and key in OWNER_BUCKET_ORDER:
        return (1 + OWNER_BUCKET_ORDER.index(key), "", key)
    return name_rank(key, label)


def ordering(kind):
    """The pipeline's parts of the shared order, for one kind."""
    return PortfolioOrder(
        sort_value=lambda entry, sort_key: sort_value(entry, sort_key, kind),
        tiebreak=_tiebreak,
        group_key=lambda entry, group: group_key(entry, group, kind),
        section_rank=lambda key, label, group: section_rank(key, label, group, kind),
        money=lambda entry: entry.mrr,
        money_field="mrr",
    )


def order_entries(entries, sort_key, descending, kind, group=""):
    return ordering(kind).order(entries, sort_key, descending, group)


def build_groups(entries, group, kind):
    return ordering(kind).groups(entries, group)


def select(book, params):
    """The rows in list order — sections first, the chosen sort inside each —
    and the section totals over every row of the chosen stages, before
    `group_value` narrows to one Board column."""
    return ordering(book.kind).select(book.rows, params)


def filter_fingerprint(params, kind):
    """A short hash of everything that decides which rows a list holds and in
    what order, but not `cursor` or `limit`, so a cursor from a list with
    other filters (or the other kind) reads the new list from its first
    page. Multi-value filters compare as sets."""
    return fingerprint(
        [
            kind.key,
            params.search,
            sorted(set(params.organisations)),
            sorted(set(params.accounts)),
            params.owner,
            sorted(set(params.stages)),
            sorted(set(params.priorities)),
            sorted(set(params.departments)),
            params.date,
            params.changed,
            None if params.ids is None else sorted(set(params.ids)),
            params.sort,
            params.group,
            params.group_value,
        ]
    )


def paginate(entries, *, params, kind):
    """The shared keyset cursor over `select`'s order."""
    return ordering(kind).paginate(entries, params, fingerprint=filter_fingerprint(params, kind))
