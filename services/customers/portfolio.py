"""The rollups behind the Customer Overview dashboard.

**What is this book made of, how exposed is it, and how has it held up?**

Every other dashboard asks how the customers you have are *doing* — health,
usage, revenue, voice, coverage. This one asks what they *are*: how many, how
big, how concentrated, how long they have stayed, and why the ones who left
left.

## The one screen that counts customers you no longer have

Every other view filters `is_archived=False` and never looks at a `churn_date`,
which is right for a working view: nobody triages an account that left. But
logo retention, cohort survival and churn reasons are all questions about
exactly those rows, and computing retention over survivors only would produce
100% every time.

So this module reads the whole book, and every figure says which population it
speaks for — `active` for what you hold today, `ever` for everything that was
ever signed.

## Concentration is the number nobody else asks for

If the largest three customers are forty per cent of ARR, that is the most
important fact about the business and no other screen states it. `concentration`
returns the ranked accounts with a running share, so the Pareto is readable
rather than asserted.

## Churn reasons are free text, and are reported as such

`churn_reason` is a CharField somebody types into. "Budget cuts" and "Budget
Cut" are the same reason and will not group themselves. They are folded on case
and surrounding whitespace here — that is all that can be done honestly. The
response returns the count of distinct raw spellings alongside the grouped
figure, so a reader can see when the grouping is doing heavy lifting and the
field deserves choices instead.
"""

from collections import defaultdict

from django.utils import timezone

from services.fx_rates.conversion import convert_to_org_currency, rates_for

from . import segments
from .models import Customer
from .scoping import visible_customers

#: How many accounts the concentration list names before the rest are folded
#: into an "everyone else" row. Ten fits on the screen and is the cut a board
#: pack uses.
TOP_N = 10

#: Cohorts are grouped by the calendar year a customer joined. Quarters would
#: be finer, and on a book this size would produce cohorts of one — a retention
#: rate of 0% or 100% and nothing in between.
COHORT_FIELD = "joined_date"


def _parse_int(raw):
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def filtered_customers(user, params):
    """The caller's whole visible book — **including archived and churned**.

    See the module docstring: this is the one screen where excluding them
    would make the answer wrong rather than tidier.
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

    customer_id = _parse_int(params.get("customer"))
    if customer_id is not None:
        queryset = queryset.filter(pk=customer_id)

    return queryset


def is_churned(customer):
    """Churned means a date was recorded, not that the row was archived.

    Archiving is a filing decision — a customer can be archived for being a
    duplicate — while a churn date is a statement that they left. Counting
    archived rows as churn would inflate every retention figure's denominator
    with housekeeping.
    """
    return customer.churn_date is not None


def build_stats(user, params):
    organisation = user.organisation
    customers = list(filtered_customers(user, params))
    rates = rates_for(organisation)
    today = timezone.localdate()

    priced = {}
    for customer in customers:
        converted = convert_to_org_currency(
            customer.arr_billed_at_account, customer.currency, organisation, rates=rates
        )
        priced[customer.pk] = None if converted is None else float(converted)

    active = [c for c in customers if not is_churned(c) and not c.is_archived]
    churned = [c for c in customers if is_churned(c)]

    active_arr = sum(priced[c.pk] or 0.0 for c in active)
    churned_arr = sum(priced[c.pk] or 0.0 for c in churned)

    # Churn inside the last year, which is the rate anyone quotes. All-time
    # churn is a fact about history, not about how this year is going.
    year_ago = today.replace(year=today.year - 1)
    churned_12m = [c for c in churned if c.churn_date and c.churn_date >= year_ago]

    return {
        "kpis": {
            "active": len(active),
            "active_arr": round(active_arr, 2),
            "average_arr": round(active_arr / len(active), 2) if active else None,
            "churned": len(churned),
            "churned_arr": round(churned_arr, 2),
            "churned_12m": len(churned_12m),
            "churned_arr_12m": round(sum(priced[c.pk] or 0.0 for c in churned_12m), 2),
            # Logos kept, of every logo ever signed in this book. Null rather
            # than a flattering 100% when nothing has ever been signed.
            "logo_retention": (
                round(len(active) / (len(active) + len(churned)) * 100, 1)
                if active or churned
                else None
            ),
            "unpriced": sum(1 for c in customers if priced[c.pk] is None),
        },
        "concentration": _concentration(active, priced),
        "cohorts": _cohorts(customers),
        "churn_reasons": _churn_reasons(churned, priced),
        "segments": _segments(active, priced),
        "lifecycle": _lifecycle(active, priced),
        "currency": organisation.currency,
    }


def _concentration(active, priced):
    """Active accounts by ARR, largest first, with a running share.

    The running share is computed here rather than in the browser so the chart
    and any figure quoted from it come from one calculation. Accounts with no
    convertible ARR are excluded from the ranking and counted in `unpriced` —
    an account of unknown size cannot be placed in a ranking by size.
    """

    ranked = sorted(
        ((priced[c.pk], c) for c in active if priced[c.pk] is not None),
        key=lambda pair: -pair[0],
    )
    total = sum(arr for arr, _c in ranked)

    rows = []
    running = 0.0
    for index, (arr, customer) in enumerate(ranked[:TOP_N], start=1):
        running += arr
        rows.append(
            {
                "rank": index,
                "id": customer.id,
                "name": customer.name,
                "arr": round(arr, 2),
                "share": round(arr / total * 100, 1) if total else 0.0,
                "cumulative_share": round(running / total * 100, 1) if total else 0.0,
                "owner": customer.owner.name if customer.owner else "Unassigned",
                "health_category": customer.health_category,
            }
        )

    rest = ranked[TOP_N:]
    return {
        "rows": rows,
        "total_arr": round(total, 2),
        "counted": len(ranked),
        # Folded rather than dropped: a reader needs to see that the tail is
        # there, and how little of the book it is.
        "rest_count": len(rest),
        "rest_arr": round(sum(arr for arr, _c in rest), 2),
        "top_three_share": (
            round(sum(arr for arr, _c in ranked[:3]) / total * 100, 1) if total else None
        ),
    }


def _cohorts(customers):
    """Customers by the year they joined, with how many are still here.

    A customer with no `joined_date` cannot be placed in a cohort and is
    counted separately rather than dropped into the earliest one — which would
    make the oldest cohort look larger and its retention worse.
    """

    buckets = defaultdict(lambda: {"joined": 0, "retained": 0, "churned": 0})
    undated = 0

    for customer in customers:
        if customer.joined_date is None:
            undated += 1
            continue
        bucket = buckets[customer.joined_date.year]
        bucket["joined"] += 1
        if is_churned(customer):
            bucket["churned"] += 1
        else:
            bucket["retained"] += 1

    return {
        "rows": [
            {
                "year": year,
                **counts,
                "retention": (
                    round(counts["retained"] / counts["joined"] * 100, 1)
                    if counts["joined"]
                    else None
                ),
            }
            for year, counts in sorted(buckets.items())
        ],
        "undated": undated,
    }


def _churn_reasons(churned, priced):
    """Why the ones who left, left — grouped as well as free text allows.

    Folded on case and surrounding space, which is the only honest
    normalisation available: "Budget cuts" and "Budget Cut" are one reason,
    "Budget cuts" and "Lost exec sponsor" are two, and nothing here can tell
    "Price" from "Too expensive". `spellings` reports how many distinct raw
    strings went into each row, so a reader can see when the grouping is doing
    heavy lifting and the field wants choices instead of a text box.
    """

    grouped = {}
    for customer in churned:
        raw = (customer.churn_reason or "").strip()
        key = raw.casefold() or "__blank__"
        entry = grouped.setdefault(
            key,
            {"reason": raw or "No reason recorded", "customers": 0, "arr": 0.0, "spellings": set()},
        )
        entry["customers"] += 1
        entry["arr"] += priced[customer.pk] or 0.0
        if raw:
            entry["spellings"].add(raw)

    return sorted(
        (
            {
                "reason": entry["reason"],
                "customers": entry["customers"],
                "arr": round(entry["arr"], 2),
                "spellings": len(entry["spellings"]),
            }
            for entry in grouped.values()
        ),
        key=lambda row: (-row["arr"], -row["customers"]),
    )


def _segments(active, priced):
    """Active accounts by size band — one definition, shared (see segments.py).

    Both counts and ARR, because the two tell opposite stories on most books:
    the smallest band is usually the most accounts and the least money, and a
    screen showing only one of them argues for the wrong thing.
    """

    totals = {
        value: {"key": value, "name": label, "customers": 0, "arr": 0.0}
        for value, label, _floor, _ceiling in segments.REVENUE_BRACKETS
    }
    unplaced = 0

    for customer in active:
        band = segments.bracket_for(priced[customer.pk])
        if band is None:
            unplaced += 1
            continue
        totals[band]["customers"] += 1
        totals[band]["arr"] += priced[customer.pk] or 0.0

    return {
        "rows": [
            {**totals[value], "arr": round(totals[value]["arr"], 2)}
            for value, _l, _f, _c in segments.REVENUE_BRACKETS
        ],
        "unplaced": unplaced,
    }


def _lifecycle(active, priced):
    """Active accounts by lifecycle stage, with the ARR sitting in each."""

    totals = {
        value: {"key": value, "name": label, "customers": 0, "arr": 0.0}
        for value, label in Customer.LifecycleStage.choices
    }
    for customer in active:
        bucket = totals[customer.lifecycle_stage]
        bucket["customers"] += 1
        bucket["arr"] += priced[customer.pk] or 0.0

    # Empty stages dropped: unlike a fixed scale, the lifecycle has eight
    # values and most books use four, so keeping the empties would be four
    # bars of nothing.
    return [
        {**totals[value], "arr": round(totals[value]["arr"], 2)}
        for value, _label in Customer.LifecycleStage.choices
        if totals[value]["customers"]
    ]


def filter_options(user):
    """The bar's dropdowns. Scoped as the numbers are, and — unlike every other
    dashboard — including archived and churned customers, because those are
    exactly the rows this screen is about."""

    customers = visible_customers(user)
    owners = (
        customers.exclude(owner__isnull=True)
        .values_list("owner_id", "owner__name")
        .distinct()
        .order_by("owner__name")
    )
    return {
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
        "customers": [
            {"value": str(pk), "name": name}
            for pk, name in customers.order_by("name").values_list("id", "name")
        ],
    }
