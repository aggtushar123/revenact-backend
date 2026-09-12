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

from dataclasses import dataclass
from typing import Callable

from django.utils import timezone

from services.customers import activity_tracking, forecast, portfolio, usage
from services.customers.models import Customer, Ticket
from services.customers.scoping import SystemActor, live_customers, visible_children_q

#: Units the API and the screens agree on.
MONEY, PERCENT, COUNT = "money", "percent", "count"

#: Which direction is good news. "none" for a figure that is context rather
#: than a target — a count of customers is neither good nor bad on its own.
UP, DOWN, NONE = "up", "down", "none"


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    unit: str
    better: str
    source: str
    read: Callable[[dict], object]
    note: str


def _forecast(actor):
    """The ARR bridge over the default horizon, whole-org."""
    organisation = actor.organisation
    customers = list(forecast.filtered_customers(actor, {}))
    rows = forecast.build_rows(customers, organisation, horizon=forecast.horizon_days({}))
    return forecast.build_bridge(rows)


def _health(actor):
    """Health mix of the live book. A count over `health_category`, which is
    the rubric's own property — nothing is re-derived here."""
    customers = list(live_customers(actor))
    good = sum(1 for c in customers if c.health_category == Customer.HealthCategory.GOOD)
    poor = sum(1 for c in customers if c.health_category == Customer.HealthCategory.POOR)
    total = len(customers)
    return {
        "healthy_share": round(good / total * 100, 1) if total else None,
        "poor_count": poor,
    }


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


#: Each source is computed once per `compute_all`, however many metrics read it.
SOURCES = {
    "portfolio": lambda actor: portfolio.build_stats(actor, {}),
    "forecast": _forecast,
    "usage": _usage,
    "activity": lambda actor: activity_tracking.build_stats(actor, {}),
    "health": _health,
    "support": _support,
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
    ),
    Metric(
        "at_risk_arr",
        "ARR at risk",
        MONEY,
        DOWN,
        "forecast",
        lambda s: round(s["churn"] + s["contraction"], 2),
        "Expected churn plus expected contraction, weighted by the shared churn rule.",
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
    ),
    Metric(
        "poor_health_count",
        "Customers in poor health",
        COUNT,
        DOWN,
        "health",
        lambda s: s["poor_count"],
        "Live customers whose health score reads Poor.",
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
]

BY_KEY = {metric.key: metric for metric in METRICS}
assert len(BY_KEY) == len(METRICS), "metric keys must be unique"


def compute_all(organisation, today=None):
    """`{metric key: value}` for the whole organisation, right now.

    Each source rollup runs once. A source that raises takes its metrics down
    with it, loudly — a metric layer that swallowed errors and reported None
    would be reporting "unmeasured" for what is actually "broken".
    """
    actor = SystemActor(organisation)
    computed = {}
    values = {}
    for metric in METRICS:
        if metric.source not in computed:
            computed[metric.source] = SOURCES[metric.source](actor)
        values[metric.key] = metric.read(computed[metric.source])
    return values


def as_of():
    return timezone.localdate()
