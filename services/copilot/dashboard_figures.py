"""The figures on each dashboard area, recomputed on the server for Ask Revenact.

Each function calls the same service code as its area's endpoint, for the
same viewer and filters, so what the assistant is told equals what the screen
shows:

- Revenue: `/customers/forecast/` — `forecast.build_rows` and `build_bridge`.
- Health: `/customers/health/` — the Triage score from `triage.triage`, over the
  book `attention.rules.filtered_customers` loads (the Health view's own history
  window), filtered as the screen filters it.
- Support: `/tickets/stats/` — `ticket_filters.filtered_tickets`. The Support
  screen has no lifecycle filter, so none is applied here either.
- Overview: the three headline cards (the three above) and the top of
  `/dashboard/attention/`.

Every set starts from `forecast.filtered_customers(user, filters)` or the
endpoint's own visibility-first filter. Nothing outside it is read.

Each function takes an optional `customers`: the book for these same `user`
and `filters`, already loaded, so one question loads it once (`load_book`).
Without it, each loads its own, exactly as its endpoint does.
"""

from collections import Counter

from django.db.models import Count, Min, Q

from services.attention import rules as attention_rules
from services.attention.snooze import visible_items
from services.customers import forecast
from services.customers.models import Customer, Ticket
from services.customers.ticket_filters import filtered_tickets
from services.customers.triage import ACTION_THRESHOLD, RENEWAL_URGENT_DAYS, triage

#: How many rows any list in the digest carries.
LIST_LIMIT = 10

#: The Triage tile's blind-spot rule: the AI reads the account this many
#: points colder than the CSM does (frontend `summarise`).
BLIND_SPOT_GAP = 2

DIRECTIONS = ("declining", "improving", "flat", "unknown")

#: The Support screen's filters. It has no lifecycle filter.
SUPPORT_FILTER_KEYS = ("owner", "customer")


def load_book(user, filters, *, history):
    """The viewer's filtered book as a list, loaded once for a question — the
    `customers` every function here accepts. `history` loads the Health
    view's snapshot window with it (`attention.rules.filtered_customers`),
    which the Health figures and the attention list read; without it, it is
    `forecast.filtered_customers` and nothing more."""
    return list(attention_rules.filtered_customers(user, filters, history=history))


def revenue_figures(user, filters, *, customers=None):
    organisation = user.organisation
    if customers is None:
        customers = list(forecast.filtered_customers(user, filters))
    rows = forecast.build_rows(customers, organisation, horizon=forecast.DEFAULT_HORIZON_DAYS)
    bridge = forecast.build_bridge(rows)
    return {
        "currency": organisation.currency,
        "bridge": bridge,
        "at_risk": round(bridge["churn"] + bridge["contraction"], 2),
        "movers": [
            {
                "id": row["id"],
                "name": row["name"],
                "net": row["net"],
                "downside": row["downside"],
                "expansion": row["expansion"],
            }
            for row in forecast.swing_list(rows)[:LIST_LIMIT]
        ],
        "unpriced_count": sum(1 for row in rows if row.arr is None),
    }


def _scored(user, filters, today, customers=None):
    """Every customer in the filtered book with its Triage result — the Health
    serializer's own call (`CustomerHealthRowSerializer._triage`). A passed
    `customers` must carry the snapshot history (`load_book(history=True)`)."""
    if customers is None:
        customers = attention_rules.filtered_customers(user, filters)
    return [
        (
            customer,
            triage(
                health_category=customer.health_category,
                csm_pulse=customer.csm_pulse_score,
                ai_pulse=customer.ai_pulse_value,
                renewal_date=customer.renewal_date,
                history=[s.health_category for s in customer.health_snapshots.all()],
                today=today,
            ),
        )
        for customer in customers
    ]


def health_figures(user, filters, *, today, customers=None):
    scored = _scored(user, filters, today, customers)
    acting = sorted(
        ((c, r) for c, r in scored if r.score >= ACTION_THRESHOLD),
        key=lambda pair: (-pair[1].score, pair[0].name),
    )
    by_direction = {direction: 0 for direction in DIRECTIONS}
    for _customer, result in scored:
        by_direction[result.direction] += 1
    return {
        "total": len(scored),
        "at_good": sum(1 for c, _r in scored if c.health_category == "good"),
        "needs_action": len(acting),
        "needs_action_renewing_soon": sum(
            1
            for _c, r in acting
            if r.days_to_renewal is not None and r.days_to_renewal <= RENEWAL_URGENT_DAYS
        ),
        "blind_spots": sum(
            1
            for c, _r in scored
            if c.csm_pulse_score is not None
            and c.ai_pulse_value is not None
            and c.csm_pulse_score - c.ai_pulse_value >= BLIND_SPOT_GAP
        ),
        "by_direction": by_direction,
        "accounts_needing_action": [
            {
                "id": customer.pk,
                "name": customer.name,
                "score": result.score,
                "factors": [factor["label"] for factor in result.factors],
            }
            for customer, result in acting[:LIST_LIMIT]
        ],
    }


def support_filters(filters):
    return {key: filters.get(key, "") for key in SUPPORT_FILTER_KEYS}


def _most_urgent(user, open_tickets, params, customers=None):
    """The companies with the most open High/Critical tickets, inside the
    filtered book. A ticket on an account counts for each of that account's
    companies in the book — the attention list's own rule. `customers` is
    the book under the Support screen's own filters (`params`)."""
    if customers is None:
        customers = forecast.filtered_customers(user, params)
    book = {customer.pk: customer for customer in customers}
    if not book:
        return []
    ids = list(book)
    urgent = (
        open_tickets.filter(priority__in=attention_rules.SUPPORT_PRIORITIES)
        .filter(Q(customer_id__in=ids) | Q(account__customers__id__in=ids))
        .distinct()
        .prefetch_related("account__customers")
    )
    counts = Counter()
    for ticket in urgent:
        targets = {ticket.customer_id} if ticket.customer_id else set()
        if ticket.account_id:
            targets |= {customer.pk for customer in ticket.account.customers.all()}
        for pk in targets & book.keys():
            counts[pk] += 1
    ranked = sorted(counts.items(), key=lambda pair: (-pair[1], book[pair[0]].name))
    return [{"id": pk, "name": book[pk].name, "open_urgent": n} for pk, n in ranked[:LIST_LIMIT]]


def support_book(filters, customers):
    """The shared book is the Support book only when no lifecycle filter
    narrowed it: the Support screen has none, and a narrower book must never
    stand in for a wider one. Otherwise None, and Support loads its own."""
    if customers is None or filters.get("lifecycle") in Customer.LifecycleStage.values:
        return None
    return customers


def support_figures(user, filters, *, today, customers=None):
    params = support_filters(filters)
    tickets = filtered_tickets(user, params)
    open_tickets = tickets.exclude(status__in=Ticket.RESOLVED_STATUSES)
    stats = open_tickets.aggregate(count=Count("id"), oldest=Min("opened_at"))
    open_count = stats["count"]
    oldest = None
    if open_count > 0 and stats["oldest"] is not None:
        oldest = (today - stats["oldest"]).days

    # `.order_by()` before the annotate: Ticket's default ordering would
    # otherwise join the GROUP BY (see TicketStatsView).
    split = {p: {s: 0 for s in Ticket.Status.values} for p in Ticket.Priority.values}
    for priority, status_value, n in (
        tickets.order_by()
        .values_list("priority", "status")
        .annotate(n=Count("id"))
        .values_list("priority", "status", "n")
    ):
        split.setdefault(priority, {s: 0 for s in Ticket.Status.values})[status_value] = n

    return {
        "open_count": open_count,
        "oldest_open_days": oldest,
        "priority_by_status": split,
        "most_urgent": _most_urgent(user, open_tickets, params, support_book(filters, customers)),
    }


def attention_top(user, filters, *, today, now, limit=LIST_LIMIT, customers=None):
    """The top of the viewer's own "Needs attention" list — `AttentionListView`
    exactly: every candidate, snoozes dropped, score then title. A passed
    `customers` must carry the snapshot history (`load_book(history=True)`)."""
    items = attention_rules.build_items(user, filters, today=today, customers=customers)
    items = visible_items(user, items, now=now)
    items.sort(key=lambda item: (-item["score"], item["title"]))
    return items[:limit]


def overview_figures(user, filters, *, today, now, customers=None):
    """The Overview's three headline cards and its attention list, all read
    from one load of the book (with its snapshot history)."""
    if customers is None:
        customers = load_book(user, filters, history=True)
    revenue = revenue_figures(user, filters, customers=customers)
    health = health_figures(user, filters, today=today, customers=customers)
    support = support_figures(user, filters, today=today, customers=customers)
    return {
        "currency": revenue["currency"],
        "arr_today": revenue["bridge"]["opening_arr"],
        "at_risk": revenue["at_risk"],
        "at_good": health["at_good"],
        "total": health["total"],
        "needs_action": health["needs_action"],
        "open_tickets": support["open_count"],
        "oldest_open_days": support["oldest_open_days"],
        "attention": attention_top(user, filters, today=today, now=now, customers=customers),
    }
