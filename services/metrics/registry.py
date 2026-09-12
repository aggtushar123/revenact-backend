"""The metric layer: every headline number, defined once.

Nine dashboards each compute their own rollup, and they agree because the
rules they share (`churn.py`, `contact.py`, `segments.py`) have one home. A
management question cuts across them — "NRR, coverage and shelfware, this
quarter against last" — and nothing could answer it, because there was no
list of what the numbers *are*.

This is that list. Each entry names a metric, its unit, which way is good,
and where it is read from. The values come from the same rollups the
dashboards draw, run for the whole organisation through `SystemActor`, so the
number here is the number on the screen — never a second computation that
can drift. A metric that a rollup reports as `None` stays `None`: unmeasured
is not zero, here as everywhere else in this codebase.

`MetricSnapshot` records these at each month end (see recording.py), which is
what turns a mirror into a memory.
"""

from dataclasses import dataclass, field
from typing import Callable

from django.utils import timezone

from services.customers import activity_tracking, forecast, portfolio, product_usage, usage
from services.customers.models import Customer, Ticket
from services.customers.scoping import SystemActor, live_customers, visible_children_q

#: Units the API and the screens agree on.
MONEY, PERCENT, COUNT = "money", "percent", "count"

#: Which direction is good news. "none" for a figure that is context rather
#: than a target — a count of customers is neither good nor bad on its own.
UP, DOWN, NONE = "up", "down", "none"


#: The ways a metric can be cut. A member is `(id, label, value)`; ids are
#: stable strings (an owner or product pk, a segment key, a lifecycle value)
#: so a month-end row for a member can be found again next month.
OWNER, PRODUCT, SEGMENT, LIFECYCLE = "owner", "product", "segment", "lifecycle"
DIMENSION_LABELS = {
    OWNER: "Owner",
    PRODUCT: "Product",
    SEGMENT: "Size band",
    LIFECYCLE: "Lifecycle stage",
}


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    unit: str
    better: str
    source: str
    read: Callable[[dict], object]
    note: str
    #: dimension -> a function of the source returning [(member, label, value)].
    #: Only where the rollup already computes that cut, or the cut is a plain
    #: regrouping of rows the rollup scored — never a new rule.
    slices: dict = field(default_factory=dict)


def _owner_of(customer):
    return (
        (str(customer.owner_id), customer.owner.name)
        if customer.owner_id
        else ("unassigned", "Unassigned")
    )


def _product_of(customer):
    if customer.primary_product_id:
        return (str(customer.primary_product_id), customer.primary_product.name)
    return ("none", "No product recorded")


def _forecast(actor):
    """The ARR bridge over the default horizon, whole-org — plus the same
    bridge per owner and per product, through `forecast.bridge_by`, so a
    slice can never disagree with the whole."""
    organisation = actor.organisation
    customers = list(forecast.filtered_customers(actor, {}))
    rows = forecast.build_rows(customers, organisation, horizon=forecast.horizon_days({}))
    bridge = forecast.build_bridge(rows)
    bridge["by"] = {
        OWNER: forecast.bridge_by(rows, lambda row: _owner_of(row.customer)),
        PRODUCT: forecast.bridge_by(rows, lambda row: _product_of(row.customer)),
    }
    return bridge


def _bridge_slice(dimension, read):
    """A forecast metric cut by a dimension: the per-group bridge, read the
    same way the whole-org one is."""
    return lambda s: [
        (member, label, read(bridge)) for (member, label), bridge in s["by"][dimension].items()
    ]


def _health(actor):
    """Health mix of the live book. A count over `health_category`, which is
    the rubric's own property — nothing is re-derived here."""
    customers = list(live_customers(actor).select_related("owner", "primary_product"))

    def mix(group):
        good = sum(1 for c in group if c.health_category == Customer.HealthCategory.GOOD)
        poor = sum(1 for c in group if c.health_category == Customer.HealthCategory.POOR)
        return {
            "healthy_share": round(good / len(group) * 100, 1) if group else None,
            "poor_count": poor,
        }

    by = {}
    for dimension, key in ((OWNER, _owner_of), (PRODUCT, _product_of)):
        groups = {}
        for customer in customers:
            groups.setdefault(key(customer), []).append(customer)
        by[dimension] = {member: mix(group) for member, group in groups.items()}
    return {**mix(customers), "by": by}


def _health_slice(dimension, field_name):
    return lambda s: [
        (member, label, m[field_name]) for (member, label), m in s["by"][dimension].items()
    ]


def _portfolio_rows(kind, value_key):
    """A portfolio cut the rollup already draws: size bands or lifecycle
    stages, each row carrying `customers` and `arr`."""

    def read(s):
        rows = s["segments"]["rows"] if kind == SEGMENT else s["lifecycle"]
        return [(row["key"], row["name"], row[value_key]) for row in rows]

    return read


def _product_rows(value_key):
    """A product cut, from the Product Usage rollup's own rows."""

    def read(s):
        return [
            (str(row["id"]) if row["id"] is not None else "none", row["product"], row[value_key])
            for row in s["rows"]
        ]

    return read


def _support(actor):
    open_tickets = (
        Ticket.objects.filter(visible_children_q(actor))
        .exclude(status__in=Ticket.RESOLVED_STATUSES)
        .distinct()
        .count()
    )
    return {"open_tickets": open_tickets}


def _usage(actor):
    """Seat utilisation, shelfware and capacity, whole-org — the Usage
    Overview's own rows and rollup."""
    customers = usage.filtered_customers(actor, {})
    return usage.build_stats(usage.rows_for(customers, actor.organisation))


def _knowledge(actor):
    """What the company is asking and telling itself — services.knowledge.
    Questions waiting on people, how many have waited too long, and how much
    the functions have written down lately."""
    from datetime import timedelta

    from services.knowledge.aging import STALE_DAYS, open_questions, stale_open_questions
    from services.knowledge.models import Contribution

    organisation = actor.organisation
    return {
        "open_questions": open_questions(organisation).count(),
        "stale_questions": stale_open_questions(organisation, STALE_DAYS).count(),
        "contributions_30d": Contribution.objects.filter(
            organisation=organisation, created_at__gte=timezone.now() - timedelta(days=30)
        ).count(),
    }


#: Each source is computed once per `compute_all`, however many metrics read it.
SOURCES = {
    "portfolio": lambda actor: portfolio.build_stats(actor, {}),
    "products": lambda actor: product_usage.build_stats(actor, {}),
    "forecast": _forecast,
    "usage": _usage,
    "activity": lambda actor: activity_tracking.build_stats(actor, {}),
    "health": _health,
    "support": _support,
    "knowledge": _knowledge,
}


METRICS = [
    Metric(
        "active_customers",
        "Active customers",
        COUNT,
        NONE,
        "portfolio",
        lambda s: s["kpis"]["active"],
        "Live customers: not archived, not churned.",
        slices={
            SEGMENT: _portfolio_rows(SEGMENT, "customers"),
            LIFECYCLE: _portfolio_rows(LIFECYCLE, "customers"),
        },
    ),
    Metric(
        "active_arr",
        "ARR",
        MONEY,
        UP,
        "portfolio",
        lambda s: s["kpis"]["active_arr"],
        "Annual recurring revenue across the live book, converted to the organisation's currency; "
        "customers with no exchange rate are counted in logos and in no money figure.",
        slices={
            SEGMENT: _portfolio_rows(SEGMENT, "arr"),
            LIFECYCLE: _portfolio_rows(LIFECYCLE, "arr"),
        },
    ),
    Metric(
        "average_arr",
        "Average ARR per customer",
        MONEY,
        UP,
        "portfolio",
        lambda s: s["kpis"]["average_arr"],
        "ARR over live customers. Null on an empty book rather than a flattering zero.",
    ),
    Metric(
        "logo_retention",
        "Logo retention",
        PERCENT,
        UP,
        "portfolio",
        lambda s: s["kpis"]["logo_retention"],
        "Customers kept, of every customer ever signed. Only meaningful because churned "
        "customers are counted.",
    ),
    Metric(
        "churned_arr_12m",
        "ARR churned, last 12 months",
        MONEY,
        DOWN,
        "portfolio",
        lambda s: s["kpis"]["churned_arr_12m"],
        "ARR that left with customers whose churn date is inside the last year.",
    ),
    Metric(
        "top_three_share",
        "Top-3 concentration",
        PERCENT,
        DOWN,
        "portfolio",
        lambda s: s["concentration"]["top_three_share"],
        "Share of ARR held by the three largest customers. Above 50% is the most important "
        "fact about the book.",
    ),
    Metric(
        "forecast_arr",
        "Forecast ARR",
        MONEY,
        UP,
        "forecast",
        lambda s: s["forecast_arr"],
        "Opening ARR minus expected churn and contraction at renewal, plus weighted expansion, "
        "over the next 12 months.",
        slices={
            OWNER: _bridge_slice(OWNER, lambda b: b["forecast_arr"]),
            PRODUCT: _bridge_slice(PRODUCT, lambda b: b["forecast_arr"]),
        },
    ),
    Metric(
        "nrr",
        "Net revenue retention",
        PERCENT,
        UP,
        "forecast",
        lambda s: s["nrr"],
        "Forecast ARR as a share of opening ARR, before any new logos. Null when there is no "
        "opening ARR.",
        slices={
            OWNER: _bridge_slice(OWNER, lambda b: b["nrr"]),
            PRODUCT: _bridge_slice(PRODUCT, lambda b: b["nrr"]),
        },
    ),
    Metric(
        "at_risk_arr",
        "ARR at risk",
        MONEY,
        DOWN,
        "forecast",
        lambda s: round(s["churn"] + s["contraction"], 2),
        "Expected churn plus expected contraction, weighted by the shared churn rule.",
        slices={
            OWNER: _bridge_slice(OWNER, lambda b: round(b["churn"] + b["contraction"], 2)),
            PRODUCT: _bridge_slice(PRODUCT, lambda b: round(b["churn"] + b["contraction"], 2)),
        },
    ),
    Metric(
        "seat_utilisation",
        "Seat utilisation",
        PERCENT,
        UP,
        "usage",
        lambda s: s["kpis"]["utilisation"],
        "Active seats over contracted seats across the live book — seats over seats, not the "
        "mean of per-account percentages. Null when no account has seats recorded.",
    ),
    Metric(
        "shelfware_arr",
        "Shelfware ARR",
        MONEY,
        DOWN,
        "usage",
        lambda s: s["kpis"]["shelfware_arr"],
        "The ARR attached to idle seats in accounts using under 75% of what they contracted — "
        "the money most likely to be questioned at renewal.",
    ),
    Metric(
        "at_capacity_arr",
        "ARR at capacity",
        MONEY,
        UP,
        "usage",
        lambda s: s["kpis"]["at_capacity_arr"],
        "ARR in accounts using 90% or more of their seats — the expansion conversation to have.",
    ),
    Metric(
        "coverage",
        "Coverage",
        PERCENT,
        UP,
        "activity",
        lambda s: s["kpis"]["coverage"],
        "Share of live customers with any logged contact inside the window (default 90 days).",
    ),
    Metric(
        "dark_accounts",
        "Accounts going dark",
        COUNT,
        DOWN,
        "activity",
        lambda s: s["kpis"]["dark_accounts"],
        "Live customers with no contact of any kind past the churn rule's cold-contact threshold.",
    ),
    Metric(
        "dark_arr",
        "ARR going dark",
        MONEY,
        DOWN,
        "activity",
        lambda s: s["kpis"]["dark_arr"],
        "The ARR sitting in those accounts.",
    ),
    Metric(
        "healthy_share",
        "Book in good health",
        PERCENT,
        UP,
        "health",
        lambda s: s["healthy_share"],
        "Share of live customers whose health score reads Good.",
        slices={
            OWNER: _health_slice(OWNER, "healthy_share"),
            PRODUCT: _health_slice(PRODUCT, "healthy_share"),
        },
    ),
    Metric(
        "poor_health_count",
        "Customers in poor health",
        COUNT,
        DOWN,
        "health",
        lambda s: s["poor_count"],
        "Live customers whose health score reads Poor.",
        slices={
            OWNER: _health_slice(OWNER, "poor_count"),
            PRODUCT: _health_slice(PRODUCT, "poor_count"),
        },
    ),
    Metric(
        "open_tickets",
        "Open support tickets",
        COUNT,
        DOWN,
        "support",
        lambda s: s["open_tickets"],
        "Tickets not resolved or closed, across the organisation's customers and their accounts.",
    ),
    Metric(
        "open_questions",
        "Questions waiting",
        COUNT,
        DOWN,
        "knowledge",
        lambda s: s["open_questions"],
        "Questions routed to a person that have not been answered yet.",
    ),
    Metric(
        "stale_questions",
        "Questions waiting over 3 days",
        COUNT,
        DOWN,
        "knowledge",
        lambda s: s["stale_questions"],
        "Open questions older than three days — where the company is slow to answer itself.",
    ),
    Metric(
        "contributions_30d",
        "Contributions, last 30 days",
        COUNT,
        UP,
        "knowledge",
        lambda s: s["contributions_30d"],
        "What every function wrote down about customers in the last thirty days, answers included.",
    ),
]

BY_KEY = {metric.key: metric for metric in METRICS}
assert len(BY_KEY) == len(METRICS), "metric keys must be unique"


def _sources_for(organisation, metrics):
    actor = SystemActor(organisation)
    computed = {}
    for metric in metrics:
        if metric.source not in computed:
            computed[metric.source] = SOURCES[metric.source](actor)
    return computed


def compute_all(organisation, today=None):
    """`{metric key: value}` for the whole organisation, right now.

    Each source rollup runs once. A source that raises takes its metrics down
    with it, loudly — a metric layer that swallowed errors and reported None
    would be reporting "unmeasured" for what is actually "broken".
    """
    computed = _sources_for(organisation, METRICS)
    return {metric.key: metric.read(computed[metric.source]) for metric in METRICS}


def compute_slices(organisation):
    """`{metric key: {dimension: [(member, label, value), ...]}}` for every
    metric that has a cut. Same sources, computed once."""
    sliced = [metric for metric in METRICS if metric.slices]
    computed = _sources_for(organisation, sliced)
    return {
        metric.key: {
            dimension: read(computed[metric.source]) for dimension, read in metric.slices.items()
        }
        for metric in sliced
    }


def compute_slice(organisation, metric, dimension):
    """One metric, one cut — `[(member, label, value)]`."""
    computed = _sources_for(organisation, [metric])
    return metric.slices[dimension](computed[metric.source])


def as_of():
    return timezone.localdate()
