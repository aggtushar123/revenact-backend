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
"""

from services.attention import rules as attention_rules
from services.customers import forecast
from services.customers.triage import ACTION_THRESHOLD, RENEWAL_URGENT_DAYS, triage

#: How many rows any list in the digest carries.
LIST_LIMIT = 10

#: The Triage tile's blind-spot rule: the AI reads the account this many
#: points colder than the CSM does (frontend `summarise`).
BLIND_SPOT_GAP = 2

DIRECTIONS = ("declining", "improving", "flat", "unknown")


def revenue_figures(user, filters):
    organisation = user.organisation
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


def _scored(user, filters, today):
    """Every customer in the filtered book with its Triage result — the Health
    serializer's own call (`CustomerHealthRowSerializer._triage`)."""
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


def health_figures(user, filters, *, today):
    scored = _scored(user, filters, today)
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
