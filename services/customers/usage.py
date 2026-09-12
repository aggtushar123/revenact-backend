"""The rollups behind the Usage Overview dashboard.

One question, with two commercial answers: **are customers using what they pay
for?** Below the line it is shelfware — seats billed and not used, which is a
renewal argument being built for you and a discount demand waiting to happen.
Above it, it is capacity — an account with no room left is an expansion
conversation nobody has started.

Both are measured against the same number, `Customer.seat_utilization_percentage`
(active ÷ contracted), which the model has always computed and nothing has ever
charted.

## Unmeasured is not zero

A customer with no contracted seats recorded has **no utilisation**, not 0%.
Every average here excludes those rows and every response counts them, because
scoring a data gap as the worst possible number invents the most alarming
reading available and then puts it in a chart — the same rule the health rubric
follows for a component it can't measure.

## Money attached to unused seats is a proxy, and says so

`shelfware_arr` is `arr × (1 − utilisation)`: the share of what a customer pays
that maps to seats nobody logged into. Contracts are rarely priced purely per
seat, so this is an estimate of exposure rather than a refund calculation. It is
worth having anyway — "$62K of what these twelve accounts pay is attached to
seats nobody uses" is a sentence that starts a QBR, and no other number on this
screen starts one.
"""

from services.fx_rates.conversion import convert_to_org_currency, rates_for

from .models import Customer
from .scoping import live_customers

#: Utilisation bands, low to high. `to` is exclusive; the last is open-ended.
#:
#: The boundaries are the ones a CS team already argues in: a quarter of the
#: seats is a pilot that never landed, half is a rollout that stalled, three
#: quarters is healthy, and past ninety per cent the account is out of room.
#: Over 100% is its own band rather than an error — more actives than the
#: contract allows is a real, common state, and it is an expansion trigger (and
#: sometimes a compliance one), not a number to clamp.
BANDS = (
    ("dormant", "Dormant (<25%)", 0, 25),
    ("low", "Low (25–50%)", 25, 50),
    ("fair", "Fair (50–75%)", 50, 75),
    ("healthy", "Healthy (75–90%)", 75, 90),
    ("at_capacity", "At capacity (90–100%)", 90, 100),
    ("over", "Over-deployed (100%+)", 100, None),
)

#: Below this, unused seats are counted as shelfware. It is the floor of
#: "healthy" above: a customer using four fifths of what they bought is using
#: what they bought, and calling the rest shelfware would make the number
#: meaningless by making it enormous.
SHELFWARE_CEILING = 75

#: At or above this, an account is out of room and worth a conversation about
#: more of it.
CAPACITY_FLOOR = 90

#: How many rows the two work lists carry. They are "who to call", not a
#: record browser — the dashboard's own scatter plots the whole book.
LIST_LIMIT = 15

#: Cap on the scatter, the one part of the response that is per-account. Past
#: this the chart is a cloud and the screen needs server-side binning instead.
MAX_POINTS = 500


def band_for(utilisation):
    """The band key for a utilisation percentage, or None when unmeasured."""
    if utilisation is None:
        return None
    for key, _label, floor, ceiling in BANDS:
        if utilisation >= floor and (ceiling is None or utilisation < ceiling):
            return key
    return BANDS[-1][0]


def _parse_int(raw):
    """None for anything unparseable — the house convention for dashboard
    filters is to draw with the filters it understood."""
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def filtered_customers(user, params):
    """The caller's visible book, narrowed by the bar's three filters.

    Visibility first, then the filters narrow from there — applying a raw
    `?customer=` id before the visibility gate is the mistake that previously
    let members read other owners' custom-object records.

    Archived customers are excluded, matching every other working view: usage
    is a question about the book you have, not the one you had.
    """

    queryset = live_customers(user).select_related("owner")

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

    customer_id = _parse_int(params.get("customer"))
    if customer_id is not None:
        queryset = queryset.filter(pk=customer_id)

    return queryset


class UsageRow:
    """One customer's usage, with its money already converted.

    A small class rather than a dict because every rollup below reads the same
    handful of derived values off it, and deriving them once per customer is
    what keeps `build_stats` a single pass.
    """

    __slots__ = (
        "customer",
        "utilisation",
        "band",
        "arr",
        "idle_seats",
        "shelfware_arr",
        "products",
    )

    def __init__(self, customer, arr):
        self.customer = customer
        self.utilisation = customer.seat_utilization_percentage
        self.band = band_for(self.utilisation)
        self.arr = arr
        contracted = customer.total_contracted_seats or 0
        active = customer.total_active_seats or 0
        # Never negative: an over-deployed account has no idle seats, it has
        # the opposite problem.
        self.idle_seats = max(0, contracted - active) if self.utilisation is not None else 0
        self.shelfware_arr = (
            arr * (1 - self.utilisation / 100)
            if arr is not None
            and self.utilisation is not None
            and self.utilisation < SHELFWARE_CEILING
            else 0.0
        )
        # Primary counts as one; `additional_products_count` is null when
        # nobody recorded it, which is not the same as "no extra products" —
        # but for a breadth count the honest floor is what we can see.
        self.products = (1 if customer.primary_product else 0) + (
            customer.additional_products_count or 0
        )


def rows_for(customers, organisation):
    """Materialise the book with ARR converted into the org's own currency.

    Null ARR means the contract currency has no rate configured — the row still
    counts in every seat figure and is left out of every money figure, and the
    response says how many. See the health endpoint for the same rule.
    """

    rates = rates_for(organisation)
    rows = []
    for customer in customers:
        converted = convert_to_org_currency(
            customer.arr_billed_at_account, customer.currency, organisation, rates=rates
        )
        rows.append(UsageRow(customer, None if converted is None else float(converted)))
    return rows


def build_stats(rows):
    """Every rollup the Controls tab draws, from one pass over the book."""

    contracted = active = 0
    measured = unmeasured = 0
    shelfware_arr = 0.0
    capacity_arr = 0.0
    capacity_count = 0
    unpriced = 0
    idle_seats = 0

    bands = {
        key: {"key": key, "name": label, "accounts": 0, "arr": 0.0, "idle_seats": 0}
        for key, label, _floor, _ceiling in BANDS
    }
    adoption = {}

    for row in rows:
        if row.arr is None:
            unpriced += 1

        if row.utilisation is None:
            unmeasured += 1
        else:
            measured += 1
            contracted += row.customer.total_contracted_seats or 0
            active += row.customer.total_active_seats or 0
            idle_seats += row.idle_seats
            shelfware_arr += row.shelfware_arr

            bucket = bands[row.band]
            bucket["accounts"] += 1
            bucket["arr"] += row.arr or 0.0
            bucket["idle_seats"] += row.idle_seats

            if row.utilisation >= CAPACITY_FLOOR:
                capacity_count += 1
                capacity_arr += row.arr or 0.0

        key = str(row.products) if row.products < 4 else "4+"
        entry = adoption.setdefault(key, {"key": key, "accounts": 0, "arr": 0.0})
        entry["accounts"] += 1
        entry["arr"] += row.arr or 0.0

    return {
        "kpis": {
            "accounts": len(rows),
            # The book-wide rate, seats over seats — not the average of each
            # account's percentage, which would let a ten-seat pilot weigh as
            # much as a fifteen-hundred-seat rollout.
            "contracted_seats": contracted,
            "active_seats": active,
            "utilisation": round(active / contracted * 100, 1) if contracted else None,
            "idle_seats": idle_seats,
            "shelfware_arr": round(shelfware_arr, 2),
            "at_capacity_arr": round(capacity_arr, 2),
            "at_capacity_count": capacity_count,
            # Named, not hidden: these are the accounts this screen cannot
            # speak for at all.
            "unmeasured_count": unmeasured,
            "measured_count": measured,
            "unpriced_count": unpriced,
        },
        "bands": [
            {**bands[key], "arr": round(bands[key]["arr"], 2)}
            for key, _label, _floor, _ceiling in BANDS
        ],
        "adoption": _adoption_rows(adoption),
    }


#: Product-count buckets, always all four, so an empty one reads as empty
#: rather than vanishing.
ADOPTION_LABELS = (
    ("0", "No product recorded"),
    ("1", "1 product"),
    ("2", "2 products"),
    ("3", "3 products"),
    ("4+", "4+ products"),
)


def _adoption_rows(counted):
    return [
        {
            "key": key,
            "name": label,
            "accounts": counted.get(key, {}).get("accounts", 0),
            "arr": round(counted.get(key, {}).get("arr", 0.0), 2),
        }
        for key, label in ADOPTION_LABELS
    ]


def _point(row):
    customer = row.customer
    return {
        "id": customer.id,
        "name": customer.name,
        "owner": customer.owner.name if customer.owner else "Unassigned",
        "lifecycle_stage": customer.get_lifecycle_stage_display(),
        "health_category": customer.health_category,
        "utilisation": row.utilisation,
        "active_seats": customer.total_active_seats,
        "contracted_seats": customer.total_contracted_seats,
        "idle_seats": row.idle_seats,
        "arr": row.arr,
        "shelfware_arr": round(row.shelfware_arr, 2),
        "products": row.products,
        "renewal_date": customer.renewal_date.isoformat() if customer.renewal_date else None,
        "band": row.band,
    }


def scatter_points(rows):
    """Every measured account as a point: utilisation against what it pays.

    Unmeasured rows are left out rather than plotted at zero — a point on the
    axis is a claim, and "we don't know" isn't one. The KPI count is where they
    are reported.
    """
    return [_point(row) for row in rows if row.utilisation is not None][:MAX_POINTS]


def shelfware_list(rows):
    """The accounts with the most money attached to unused seats.

    Ranked by shelfware ARR rather than by lowest utilisation: a dormant
    ten-seat pilot is a worse percentage and a smaller problem than a
    half-used enterprise rollout, and only one of the two is worth a call this
    week.
    """
    ranked = sorted(
        (row for row in rows if row.shelfware_arr > 0),
        key=lambda row: (-row.shelfware_arr, row.utilisation or 0),
    )
    return [_point(row) for row in ranked[:LIST_LIMIT]]


def capacity_list(rows):
    """Accounts at or past their contracted seats — expansion, in order of how
    much of it there is. Ranked by ARR: the bigger the account already is, the
    bigger the expansion it implies."""
    ranked = sorted(
        (row for row in rows if row.utilisation is not None and row.utilisation >= CAPACITY_FLOOR),
        key=lambda row: (-(row.arr or 0), -(row.utilisation or 0)),
    )
    return [_point(row) for row in ranked[:LIST_LIMIT]]


def filter_options(user):
    """The bar's dropdowns, scoped exactly as the numbers are, so a CSM can't
    filter by a company they can't see."""

    customers = live_customers(user)
    owners = (
        customers.exclude(owner__isnull=True)
        .values_list("owner_id", "owner__name")
        .distinct()
        .order_by("owner__name")
    )
    unassigned = customers.filter(owner__isnull=True).exists()

    return {
        "owners": [{"value": str(owner_id), "name": name} for owner_id, name in owners]
        + ([{"value": "unassigned", "name": "Unassigned"}] if unassigned else []),
        "lifecycles": [
            {"value": value, "name": label}
            for value, label in Customer.LifecycleStage.choices
            if customers.filter(lifecycle_stage=value).exists()
        ],
        "customers": [
            {"value": str(pk), "name": name}
            for pk, name in customers.order_by("name").values_list("id", "name")
        ],
    }


__all__ = [
    "BANDS",
    "CAPACITY_FLOOR",
    "SHELFWARE_CEILING",
    "band_for",
    "build_stats",
    "capacity_list",
    "filter_options",
    "filtered_customers",
    "rows_for",
    "scatter_points",
    "shelfware_list",
]
