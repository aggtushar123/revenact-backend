"""The Organizations portfolio's book: the viewer's customers, narrowed by the
page's filters, with every signal a row shows.

**Visibility first.** Everything starts from `visible_customers(user)`; a raw
`?ids=` or `?owner=` can only narrow that, never widen it.

**Scope.** Archived customers are hidden unless named by `ids`, the way
`/customers/?ids=` treats them. Churned customers — a `churn_date`, or the
Churn stage — are hidden unless the viewer asks (`include_churned=1`, or
`churn` among the lifecycle filters) or names them by id: the working list is
the book you hold.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import timedelta

from django.db.models import (
    BooleanField,
    Case,
    CharField,
    ExpressionWrapper,
    Prefetch,
    Q,
    Value,
    When,
)
from django.db.models.functions import Cast

from services.attention.rules import SUPPORT_PRIORITIES
from services.customers.models import Customer, HealthSnapshot, Product, Ticket, with_health_inputs
from services.customers.personal import visible_tickets
from services.customers.scoping import visible_children_q, visible_customers
from services.customers.triage import ACTION_THRESHOLD, Triage, triage
from services.customers.views import CustomerHealthView
from services.fx_rates.conversion import convert_to_org_currency, rates_for

from .params import RENEWS_WITHIN_DAYS, PortfolioParams

#: The Renewing tile's switch: the first two of the filter's windows.
RENEWING_WINDOWS = RENEWS_WITHIN_DAYS[:2]

#: Either marker means the customer has left. The churn modal sets both; a
#: seeded or imported row may carry only one, and either should hide it. The
#: one definition: the scope excludes it, `renewing_q` excludes it, and each
#: row reads it back as the `is_churned` annotation.
CHURNED = Q(churn_date__isnull=False) | Q(lifecycle_stage=Customer.LifecycleStage.CHURN)

#: `CustomerStatsView`'s buckets: NPS is stored per customer as −100…100, so
#: the band is its sign. The one definition: the filter reads it, and each row
#: reads its band back as the `nps_band` annotation the NPS tile counts.
NPS_Q = {
    "promoter": Q(nps_score__gt=0),
    "passive": Q(nps_score=0),
    "detractor": Q(nps_score__lt=0),
}


def renewing_q(days, *, today):
    """Renews within `days` — overdue included — and has not churned. The
    `renews_within` filter and the Renewing tile both read this, so clicking
    the tile lists exactly the N it showed, with or without churned rows."""
    deadline = today + timedelta(days=days)
    return Q(renewal_date__isnull=False, renewal_date__lte=deadline) & ~CHURNED


def _flag(q):
    return ExpressionWrapper(q, output_field=BooleanField())


def health_q(category):
    """`Customer.health_category` as SQL, read off the same thresholds, so the
    filter and the ring can never disagree about a boundary score."""
    upper = None
    for threshold, name in Customer.HEALTH_THRESHOLDS:
        if name == category:
            lower = Q(health_score__gte=threshold)
            return lower if upper is None else lower & Q(health_score__lt=upper)
        upper = threshold
    return Q(health_score__lt=upper)


def filtered_queryset(user, params: PortfolioParams, *, today):
    # SOC2:AUTH-02 record visibility comes first; filters only narrow it
    customers = visible_customers(user)

    if params.ids is None:
        customers = customers.filter(is_archived=False)
        asked_for_churned = (
            params.include_churned or Customer.LifecycleStage.CHURN in params.lifecycles
        )
        if not asked_for_churned:
            customers = customers.exclude(CHURNED)
    elif params.ids:
        customers = customers.filter(pk__in=params.ids)
    else:
        return customers.none()

    if params.search:
        customers = customers.annotate(id_as_text=Cast("id", CharField())).filter(
            Q(name__icontains=params.search) | Q(id_as_text__icontains=params.search)
        )

    if params.owner == "unassigned":
        customers = customers.filter(owner__isnull=True)
    elif params.owner is not None:
        customers = customers.filter(owner_id=params.owner)

    if params.lifecycles:
        customers = customers.filter(lifecycle_stage__in=params.lifecycles)

    if params.health:
        bands = Q()
        for category in params.health:
            bands |= health_q(category)
        customers = customers.filter(bands)

    if params.products:
        customers = customers.filter(primary_product_id__in=params.products)

    if params.renews_within is not None:
        customers = customers.filter(renewing_q(params.renews_within, today=today))

    if params.nps:
        customers = customers.filter(NPS_Q[params.nps])

    return customers


#: How far back the row's sparkline reads. Snapshots are month-end, so six
#: months is five of them plus today's score.
TREND_MONTHS = 6


@dataclass
class Entry:
    customer: Customer
    arr: float | None
    trend: list[float]
    triage: Triage
    last_touch_days: int | None
    renewal_days: int | None
    urgent_tickets: int
    churned: bool
    signal: dict | None
    #: The Renewing tile's windows (`RENEWING_WINDOWS`) this row falls in.
    renewing: frozenset[int] = frozenset()


@dataclass
class Portfolio:
    entries: list[Entry]
    organisation: object
    rates: dict


def signal_for(*, churned, renewal_days, risk, urgent_tickets):
    """At most one tag per row: overdue renewal, then risk at or above the
    Triage action threshold, then open High/Critical tickets. A churned
    account has already left, so nothing about it is urgent."""
    if churned:
        return None
    if renewal_days is not None and renewal_days < 0:
        return {"kind": "renewal_overdue", "label": "Renewal overdue"}
    if risk >= ACTION_THRESHOLD:
        return {"kind": "risk", "label": f"Risk {risk}"}
    if urgent_tickets:
        noun = "ticket" if urgent_tickets == 1 else "tickets"
        return {"kind": "tickets", "label": f"{urgent_tickets} open {noun}"}
    return None


def urgent_ticket_counts(user, ids):
    """Open High/Critical tickets per company — the attention list's support
    rule (`attention.rules._support_items`): read under the department rule,
    and a ticket on an account counts for each of its companies in `ids`."""
    if not ids:
        return Counter()
    # SOC2:AUTH-02 tickets are read department-wise, on visible parents only
    tickets = (
        visible_tickets(user, Ticket.objects.filter(visible_children_q(user)).distinct())
        .filter(priority__in=SUPPORT_PRIORITIES)
        .exclude(status__in=Ticket.RESOLVED_STATUSES)
        .filter(Q(customer_id__in=ids) | Q(account__customers__id__in=ids))
        .distinct()
        .prefetch_related("account__customers")
    )
    wanted = set(ids)
    counts = Counter()
    for ticket in tickets:
        targets = {ticket.customer_id} if ticket.customer_id else set()
        if ticket.account_id:
            targets |= {customer.pk for customer in ticket.account.customers.all()}
        for customer_id in targets & wanted:
            counts[customer_id] += 1
    return counts


def _entry(customer, *, today, organisation, rates, urgent):
    converted = convert_to_org_currency(
        customer.arr_billed_at_account, customer.currency, organisation, rates=rates
    )
    snapshots = list(customer.health_snapshots.all())
    # CustomerHealthRowSerializer._triage's own call, over the same window.
    result = triage(
        health_category=customer.health_category,
        csm_pulse=customer.csm_pulse_score,
        ai_pulse=customer.ai_pulse_value,
        renewal_date=customer.renewal_date,
        history=[snapshot.health_category for snapshot in snapshots],
        today=today,
    )
    since = today - timedelta(days=31 * TREND_MONTHS)
    recent = [float(s.health_score) for s in snapshots if s.captured_on >= since]
    trend = recent[-(TREND_MONTHS - 1) :] + [float(customer.health_score)]
    last_touch = customer._last_touch_on
    renewal_days = None if customer.renewal_date is None else (customer.renewal_date - today).days
    churned = customer.is_churned
    return Entry(
        customer=customer,
        arr=None if converted is None else float(converted),
        trend=trend,
        triage=result,
        last_touch_days=None if last_touch is None else (today - last_touch).days,
        renewal_days=renewal_days,
        urgent_tickets=urgent,
        churned=churned,
        signal=signal_for(
            churned=churned, renewal_days=renewal_days, risk=result.score, urgent_tickets=urgent
        ),
        renewing=frozenset(
            days for days in RENEWING_WINDOWS if getattr(customer, f"renews_within_{days}")
        ),
    )


def load_portfolio(user, params: PortfolioParams, *, today):
    """The whole filtered book with its signals, in a fixed number of queries:
    the customers (touch and ticket subqueries from `with_health_inputs`, the
    people and product joined), their snapshots, the FX table, and the urgent
    tickets. `with_health_inputs`' CSAT prefetch is dropped: no row reads it."""
    organisation = user.organisation
    earliest = today - timedelta(days=31 * CustomerHealthView.DEFAULT_HISTORY_MONTHS)
    snapshots = (
        HealthSnapshot.objects.filter(captured_on__gte=earliest)
        .only("id", "customer_id", "captured_on", "health_score")
        .order_by("captured_on")
    )
    queryset = (
        with_health_inputs(filtered_queryset(user, params, today=today))
        .prefetch_related(None)
        .select_related("owner", "primary_product", "created_by", "modified_by")
        .prefetch_related(Prefetch("health_snapshots", queryset=snapshots))
        .annotate(
            is_churned=_flag(CHURNED),
            nps_band=Case(
                *(When(q, then=Value(band)) for band, q in NPS_Q.items()),
                default=None,
                output_field=CharField(),
            ),
            **{
                f"renews_within_{days}": _flag(renewing_q(days, today=today))
                for days in RENEWING_WINDOWS
            },
        )
    )
    customers = list(queryset)
    rates = rates_for(organisation)
    urgent = urgent_ticket_counts(user, [customer.pk for customer in customers])
    entries = [
        _entry(
            customer,
            today=today,
            organisation=organisation,
            rates=rates,
            urgent=urgent.get(customer.pk, 0),
        )
        for customer in customers
    ]
    return Portfolio(entries=entries, organisation=organisation, rates=rates)


def filter_options(user):
    """The filter sheet's choices, scoped exactly as the rows are: the viewer's
    visible, non-archived customers — churned included, so "includes churned"
    has something to narrow. Three queries, whatever the book's size."""
    # SOC2:AUTH-02 options are scoped to the viewer's own visible customers
    customers = visible_customers(user).filter(is_archived=False).order_by()
    owners = list(customers.values_list("owner_id", "owner__name").distinct())
    named = sorted(
        ((pk, name) for pk, name in owners if pk is not None),
        key=lambda row: (row[1] or "").casefold(),
    )
    stages = set(customers.values_list("lifecycle_stage", flat=True).distinct())
    products = (
        Product.objects.filter(
            organisation=user.organisation, pk__in=customers.values("primary_product_id")
        )
        .order_by("name")
        .values_list("id", "name")
    )
    return {
        "owners": [{"value": str(pk), "name": name} for pk, name in named]
        + (
            [{"value": "unassigned", "name": "Unassigned"}]
            if any(pk is None for pk, _name in owners)
            else []
        ),
        "lifecycles": [
            {"value": value, "name": label}
            for value, label in Customer.LifecycleStage.choices
            if value in stages
        ],
        "products": [{"value": str(pk), "name": name} for pk, name in products],
    }
