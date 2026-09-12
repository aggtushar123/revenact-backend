"""The rollups behind the Product Usage dashboard.

**Which products carry the book, and how are the customers on each one doing?**

Not the Usage Overview, which reads seats in aggregate — one utilisation rate,
one shelfware figure, one book. This compares *products against each other*:
ARR led, health mix, utilisation, satisfaction, support burden and churn, one
row per product. The Usage Overview is a CS operations screen; this is the one
a product manager opens.

On any real book it answers the question nobody else here asks: is one of these
products quietly responsible for most of the churn?

## The limit that has to be on the screen, not just in this docstring

`Customer.primary_product` names **one** product. `additional_products_count`
is a bare integer — nobody recorded *which* other products a customer has. So
a customer on three products is attributed entirely to their primary one, and
every figure below is therefore "customers **led by** this product", never
"revenue split across products".

That is the single most misleading thing this screen could hide, so the
response carries `attribution` and the UI states it above the numbers. Doing
this properly needs a Product model and a per-customer join — worth having, and
not something a dashboard can invent.

## Free text, again

`primary_product` is a CharField somebody types. Rows fold on case and
surrounding whitespace, and `spellings` reports how many raw strings went into
each — the same treatment, and the same argument for choices, as
`churn_reason` in portfolio.py.
"""

from django.db.models import Count, Q

from services.fx_rates.conversion import convert_to_org_currency, rates_for

from .models import Customer, Ticket
from .scoping import visible_customers

#: Label for customers with no product recorded. Its own row rather than
#: dropped: "we don't know what they bought" is a finding about the CRM, and
#: hiding it would make every share-of-book figure add up to less than 100%
#: with no explanation.
NO_PRODUCT = "No product recorded"


def _parse_int(raw):
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def filtered_customers(user, params):
    """The whole visible book, **including churned** — like the Customer
    Overview and unlike the working dashboards.

    Churn by product is half of what this screen is for: a product whose
    customers all left would otherwise look like a product with no problems.
    """

    queryset = visible_customers(user).select_related("owner")

    owner = params.get("owner")
    if owner == "unassigned":
        queryset = queryset.filter(owner__isnull=True)
    else:
        owner_id = _parse_int(owner)
        if owner_id is not None:
            queryset = queryset.filter(owner_id=owner_id)

    lifecycle = params.get("lifecycle")
    if lifecycle in Customer.LifecycleStage.values:
        queryset = queryset.filter(lifecycle_stage=lifecycle)

    product = params.get("product")
    if product:
        if product == NO_PRODUCT:
            queryset = queryset.filter(primary_product="")
        else:
            queryset = queryset.filter(primary_product__iexact=product.strip())

    return queryset


def _open_tickets(customers):
    """Open tickets per customer id, counted once for the whole page.

    Tickets hang off a Customer or one of its Accounts, so both count toward
    the company — support load on a division is support load on that product.
    """
    ids = [customer.pk for customer in customers]
    if not ids:
        return {}

    rows = (
        Customer.objects.filter(pk__in=ids)
        .annotate(
            open_tickets=Count(
                "tickets",
                filter=~Q(tickets__status__in=Ticket.RESOLVED_STATUSES),
                distinct=True,
            )
        )
        .values_list("pk", "open_tickets")
    )
    return dict(rows)


def _average(values):
    """Mean of the values that exist, or None when none do.

    Never zero for "nobody answered": a product with no survey responses has
    no score, and a zero would make it the worst-rated product on the screen.
    """
    present = [value for value in values if value is not None]
    return round(sum(present) / len(present), 1) if present else None


def build_rows(user, params):
    organisation = user.organisation
    customers = list(filtered_customers(user, params))
    rates = rates_for(organisation)
    tickets = _open_tickets(customers)

    grouped = {}
    for customer in customers:
        raw = (customer.primary_product or "").strip()
        key = raw.casefold() or "__none__"
        entry = grouped.setdefault(
            key,
            {
                "product": raw or NO_PRODUCT,
                "spellings": set(),
                "active": [],
                "churned": [],
            },
        )
        if raw:
            entry["spellings"].add(raw)
        if customer.churn_date is not None:
            entry["churned"].append(customer)
        elif not customer.is_archived:
            entry["active"].append(customer)

    def arr_of(customer):
        converted = convert_to_org_currency(
            customer.arr_billed_at_account, customer.currency, organisation, rates=rates
        )
        return None if converted is None else float(converted)

    rows = []
    for entry in grouped.values():
        active = entry["active"]
        churned = entry["churned"]

        contracted = sum(c.total_contracted_seats or 0 for c in active)
        used = sum(c.total_active_seats or 0 for c in active)
        arrs = [arr_of(c) for c in active]

        health = {category: 0 for category in Customer.HealthCategory.values}
        for customer in active:
            health[customer.health_category] += 1

        rows.append(
            {
                "product": entry["product"],
                "spellings": len(entry["spellings"]),
                "customers": len(active),
                "arr": round(sum(a or 0.0 for a in arrs), 2),
                "unpriced": sum(1 for a in arrs if a is None),
                # Seats over seats, not the mean of per-account percentages —
                # the same rule the Usage Overview uses, so the two screens
                # can't report different utilisation for the same accounts.
                "utilisation": round(used / contracted * 100, 1) if contracted else None,
                "contracted_seats": contracted,
                "active_seats": used,
                "health": health,
                "healthy_share": (
                    round(health[Customer.HealthCategory.GOOD] / len(active) * 100, 1)
                    if active
                    else None
                ),
                # ARR sitting in accounts that are not Good. What "weakest
                # product" is ranked on: a percentage lets one unhappy
                # customer on a tiny product outrank three on a large one.
                "unhealthy_arr": round(
                    sum(
                        arr or 0.0
                        for arr, customer in zip(arrs, active, strict=True)
                        if customer.health_category != Customer.HealthCategory.GOOD
                    ),
                    2,
                ),
                "ces": _average([c.ces_percentage and float(c.ces_percentage) for c in active]),
                "nps": _average([c.nps_score for c in active]),
                "open_tickets": sum(tickets.get(c.pk, 0) for c in active),
                "tickets_per_customer": (
                    round(sum(tickets.get(c.pk, 0) for c in active) / len(active), 1)
                    if active
                    else None
                ),
                "churned": len(churned),
                "churned_arr": round(sum(arr_of(c) or 0.0 for c in churned), 2),
                # Of everyone this product ever led, how many left. The figure
                # the screen exists for.
                "churn_rate": (
                    round(len(churned) / (len(active) + len(churned)) * 100, 1)
                    if active or churned
                    else None
                ),
            }
        )

    # Largest first, by the ARR each product leads. A product with no active
    # customers left still appears — that is the most important row on the
    # screen when it happens.
    return sorted(rows, key=lambda row: (-row["arr"], -row["customers"], row["product"]))


def build_stats(user, params):
    rows = build_rows(user, params)
    total_arr = sum(row["arr"] for row in rows)
    total_customers = sum(row["customers"] for row in rows)

    for row in rows:
        row["share"] = round(row["arr"] / total_arr * 100, 1) if total_arr else 0.0

    priced_rows = [row for row in rows if row["customers"]]
    largest = priced_rows[0] if priced_rows else None
    # "Weakest" is the product with the most ARR sitting in accounts that are
    # not in good health — money at stake, not a percentage. Ranking on the
    # share of healthy customers put a product with one unhappy customer
    # ($42K) above one with three unhappy customers and five times the revenue
    # ($203K), which is the wrong answer to "which product do we fix first".
    at_risk = [row for row in priced_rows if row["unhealthy_arr"] > 0]
    weakest = max(at_risk, key=lambda row: row["unhealthy_arr"]) if at_risk else None
    worst_churn = max(
        (row for row in rows if row["churned"]), key=lambda row: row["churned_arr"], default=None
    )

    return {
        "rows": rows,
        "kpis": {
            "products": len(rows),
            "customers": total_customers,
            "arr": round(total_arr, 2),
            "largest": (
                {"product": largest["product"], "share": largest["share"], "arr": largest["arr"]}
                if largest
                else None
            ),
            "weakest": (
                {
                    "product": weakest["product"],
                    "unhealthy_arr": weakest["unhealthy_arr"],
                    "healthy_share": weakest["healthy_share"],
                    "customers": weakest["customers"],
                    "healthy": weakest["health"][Customer.HealthCategory.GOOD],
                }
                if weakest
                else None
            ),
            "worst_churn": (
                {
                    "product": worst_churn["product"],
                    "churned": worst_churn["churned"],
                    "churned_arr": worst_churn["churned_arr"],
                }
                if worst_churn
                else None
            ),
        },
        # Carried in the payload so the screen states it rather than the docs
        # alone — see the module docstring.
        "attribution": {
            "basis": "primary_product",
            "note": (
                "Every figure counts customers whose *primary* product this is. "
                "Only the primary product is recorded per customer, so a customer "
                "on several products is counted once, here — this is not revenue "
                "split across products."
            ),
        },
        "currency": user.organisation.currency,
    }


def filter_options(user):
    """The bar's dropdowns, including a product list built from the book.

    Products come from the data rather than a table, because there is no
    product table — which is the same limitation the attribution note names.
    """

    customers = visible_customers(user)
    products = sorted(
        {
            (customer.primary_product or "").strip()
            for customer in customers
            if (customer.primary_product or "").strip()
        },
        key=str.casefold,
    )
    owners = (
        customers.exclude(owner__isnull=True)
        .values_list("owner_id", "owner__name")
        .distinct()
        .order_by("owner__name")
    )

    return {
        "products": [{"value": name, "name": name} for name in products]
        + (
            [{"value": NO_PRODUCT, "name": NO_PRODUCT}]
            if customers.filter(primary_product="").exists()
            else []
        ),
        "owners": [{"value": str(owner_id), "name": name} for owner_id, name in owners]
        + (
            [{"value": "unassigned", "name": "Unassigned"}]
            if customers.filter(owner__isnull=True).exists()
            else []
        ),
        "lifecycles": [
            {"value": value, "name": label}
            for value, label in Customer.LifecycleStage.choices
            if customers.filter(lifecycle_stage=value).exists()
        ],
    }
