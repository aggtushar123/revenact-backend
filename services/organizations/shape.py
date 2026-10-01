"""Ordering, grouping, paging and totals for the portfolio — pure functions
over the entries `book.load_portfolio` returns. No queries. The order,
section totals and cursor are `portfolio_core`'s; the sort values, sections
and fingerprint here are the organisation's.

Everything here runs over the whole filtered set: a section header or a tile
that counted only the page would count nothing useful.
"""

import math

from services.customers.models import Customer
from services.fx_rates.conversion import convert_to_org_currency
from services.portfolio_core.shape import PortfolioOrder, fingerprint, name_rank

# The cursor lives in `portfolio_core`; re-exported for Organizations' tests.
from services.portfolio_core.shape import decode_cursor as decode_cursor
from services.portfolio_core.shape import encode_cursor as encode_cursor
from services.portfolio_core.shape import keyset_page as keyset_page
from services.portfolio_core.shape import wrap_desc as wrap_desc

from .book import NPS_Q, RENEWING_WINDOWS
from .params import NUMERIC_SORT_KEYS, RENEWS_WITHIN_DAYS
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


def _window_labels():
    """The renewal sections, cut at the `renews_within` filter's own days, so
    a section and the filter of the same number can never disagree."""
    labels, previous = [("overdue", "Overdue")], None
    for days in RENEWS_WITHIN_DAYS:
        label = f"Within {days} days" if previous is None else f"{previous + 1}–{days} days"
        labels.append((str(days), label))
        previous = days
    return (*labels, ("later", "Later"), ("none", "No renewal date"))


RENEWAL_WINDOWS = _window_labels()


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


def _ordering(portfolio):
    """This portfolio's parts of the shared order (`portfolio_core`): money
    sorts need the portfolio's rates, so the order is built per portfolio."""
    return PortfolioOrder(
        sort_value=lambda entry, sort_key: SORT_GETTERS[sort_key](entry, portfolio),
        tiebreak=_tiebreak,
        group_key=group_key,
        section_rank=group_rank,
        money=lambda entry: entry.arr,
        money_field="arr",
    )


def order_entries(portfolio, sort_key, descending, group=""):
    """Sections in their fixed order, then missing values last in either
    direction; ties by name, then id."""
    return _ordering(portfolio).order(portfolio.entries, sort_key, descending, group)


def renewal_window(days):
    if days is None:
        return "none"
    if days < 0:
        return "overdue"
    return next((str(limit) for limit in RENEWS_WITHIN_DAYS if days <= limit), "later")


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


def group_rank(key, label, group):
    if group == "health":
        return (HEALTH_GROUP_ORDER.index(key), "", "")
    if group == "lifecycle":
        return (Customer.LifecycleStage.values.index(key), "", "")
    if group == "renewal":
        return ([window for window, _label in RENEWAL_WINDOWS].index(key), "", "")
    # Owners and products by name, the empty bucket last.
    return name_rank(key, label)


def select(portfolio, params):
    """The rows in list order — sections first, the chosen sort inside each —
    and the section totals over every row, before `group_value` narrows."""
    return _ordering(portfolio).select(portfolio.entries, params)


def filter_fingerprint(params):
    """A short hash of everything that decides which rows a list holds and in
    what order — the filters, search, `ids`, sort, group and board column —
    but not `cursor` or `limit`. A cursor carries the fingerprint of the list
    it was cut from, so changing any filter while keeping the cursor reads
    the new list from its first page instead of from the old list's cut.
    Multi-value filters are compared as sets: `health=poor,good` is the same
    list as `health=good,poor`."""
    state = [
        params.search,
        params.owner,
        sorted(set(params.lifecycles)),
        sorted(set(params.health)),
        sorted(set(params.products)),
        params.renews_within,
        params.nps,
        None if params.ids is None else sorted(set(params.ids)),
        params.include_churned,
        params.sort,
        params.group,
        params.group_value,
    ]
    return fingerprint(state)


def paginate(entries, *, params, portfolio):
    """The shared keyset cursor over `select`'s order, cut against this
    list's `filter_fingerprint`."""
    return _ordering(portfolio).paginate(entries, params, fingerprint=filter_fingerprint(params))


def build_summary(entries):
    """The five tiles, over every filtered row. Health, NPS and lifecycle are
    `CustomerStatsView`'s arithmetic (ARR converted, MRR = ARR / 12 per
    customer, unconvertible money counted but not summed, NPS by sign);
    renewals are `book.renewing_q`, the `renews_within` filter's own rule
    (overdue in, churned out — a churn date or the Churn stage), so a clicked
    tile lists exactly its N."""
    categories = Customer.HealthCategory.values
    health = {category: 0 for category in categories}
    health_arr = {category: 0.0 for category in categories}
    health_mrr = {category: 0.0 for category in categories}
    stages = {stage: {"count": 0, "arr": 0.0} for stage in Customer.LifecycleStage.values}
    bands = dict.fromkeys(NPS_Q, 0)
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
        if customer.nps_band is not None:
            bands[customer.nps_band] += 1
        for days in entry.renewing:
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
        "unconverted_count": unconverted,
        "renewing": renewing,
    }


def build_listing(portfolio, params, *, filters):
    """The endpoint's body. `count` is the rows this query pages through
    (after `group_value`); `groups` and `summary` are over the whole filtered
    set, so a board column's header and the tiles never shrink to a page."""
    entries, groups = select(portfolio, params)
    page, next_cursor = paginate(entries, params=params, portfolio=portfolio)
    return {
        "results": [row_payload(entry) for entry in page],
        "next_cursor": next_cursor,
        "count": len(entries),
        "groups": groups,
        "summary": build_summary(portfolio.entries),
        "filters": filters,
        "currency": portfolio.organisation.currency,
    }
