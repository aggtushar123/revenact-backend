"""Ordering, grouping, paging and totals for the Accounts portfolio — pure
functions over the entries `book.load_portfolio` returns. No queries.

The order, sections, totals and cursor are `portfolio_core`'s; the section
order (`group_rank`) and the renewal windows are Organizations' own. The sort
values, group keys and fingerprint are the account's: they read
`entry.account` and the account's filters.
Everything runs over the whole filtered set, so a section header or a tile
never counts only the page.
"""

from services.customers.models import Customer
from services.organizations.book import RENEWING_WINDOWS
from services.organizations.params import NPS_BANDS
from services.organizations.shape import RENEWAL_WINDOWS, group_rank, renewal_window
from services.portfolio_core.shape import PortfolioOrder, fingerprint

from .rows import row_payload

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


ORDER = PortfolioOrder(
    sort_value=lambda entry, sort_key: SORT_GETTERS[sort_key](entry),
    tiebreak=_tiebreak,
    group_key=group_key,
    section_rank=group_rank,
    money=lambda entry: entry.arr,
    money_field="arr",
)


def order_entries(entries, sort_key, descending, group=""):
    return ORDER.order(entries, sort_key, descending, group)


def select(portfolio, params):
    """The rows in list order — sections first, the chosen sort inside each —
    and the section totals over every row, before `group_value` narrows."""
    return ORDER.select(portfolio.entries, params)


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
    return fingerprint(state)


def paginate(entries, *, params):
    """The shared keyset cursor over `select`'s order."""
    return ORDER.paginate(entries, params, fingerprint=filter_fingerprint(params))


def nps_band(score):
    """`NPS_Q`'s rule in Python, by sign (`/accounts/stats/`' buckets), so the
    NPS tile and the `nps` filter can never disagree."""
    if score is None:
        return None
    if score > 0:
        return "promoter"
    if score == 0:
        return "passive"
    return "detractor"


def build_summary(entries):
    """The five tiles, over every filtered row, in the Organizations summary's
    shape. Health, NPS and lifecycle are `AccountStatsView`'s arithmetic (ARR
    as stored, MRR = ARR / 12, NPS by sign); renewals are
    `book.account_renewing_q`'s rule over `renewal_days` (overdue in), so a
    clicked tile lists exactly its N. Account ARR is always in the
    workspace's currency, so `unconverted_count` is always 0 — kept so both
    lists' tiles read one shape."""
    categories = Customer.HealthCategory.values
    health = dict.fromkeys(categories, 0)
    health_arr = dict.fromkeys(categories, 0.0)
    health_mrr = dict.fromkeys(categories, 0.0)
    stages = {stage: {"count": 0, "arr": 0.0} for stage in Customer.LifecycleStage.values}
    bands = dict.fromkeys(NPS_BANDS, 0)
    renewing = {str(days): 0 for days in RENEWING_WINDOWS}
    total = 0.0

    for entry in entries:
        account = entry.account
        category = account.health_category
        health[category] += 1
        health_arr[category] += entry.arr
        health_mrr[category] += entry.arr / 12
        stages[account.lifecycle_stage]["count"] += 1
        stages[account.lifecycle_stage]["arr"] += entry.arr
        total += entry.arr
        band = nps_band(account.nps_score)
        if band is not None:
            bands[band] += 1
        for days in RENEWING_WINDOWS:
            if entry.renewal_days is not None and entry.renewal_days <= days:
                renewing[str(days)] += 1

    promoters, detractors = bands["promoter"], bands["detractor"]
    scored = sum(bands.values())
    return {
        "health": {
            **health,
            "arr": {category: round(value, 2) for category, value in health_arr.items()},
            "mrr": {category: round(value, 2) for category, value in health_mrr.items()},
        },
        "nps": {
            "promoters": promoters,
            "passives": bands["passive"],
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
        "unconverted_count": 0,
        "renewing": renewing,
    }


def build_listing(portfolio, params, *, filters):
    """The endpoint's body. `count` is the rows this query pages through
    (after `group_value`); `groups` and `summary` are over the whole filtered
    set, so a Board column's header and the tiles never shrink to a page."""
    entries, groups = select(portfolio, params)
    page, next_cursor = paginate(entries, params=params)
    return {
        "results": [row_payload(entry) for entry in page],
        "next_cursor": next_cursor,
        "count": len(entries),
        "groups": groups,
        "summary": build_summary(portfolio.entries),
        "filters": filters,
        "currency": portfolio.organisation.currency,
    }
