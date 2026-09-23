"""The rollups behind the Activity Tracking dashboard.

**Is the team actually working the book, and where isn't it?**

Not the same question as AI Trending Topics, which counts what *customers* are
talking about — subject matter and sentiment across emails, calls and tickets.
This counts what *we* did: touches logged, accounts covered, cadence kept,
follow-through on tasks. One is voice-of-customer, the other is an operations
review.

## What counts as a touch

Activities, calls, notes and meetings — work somebody on this side logged.
**Tickets and inbound emails are deliberately excluded from the touch count**:
a customer raising a ticket is not evidence that anyone called them back, and
counting it as team output would let a screen full of complaints read as a
screen full of coverage. Tickets appear in the inbound figure instead, beside
the touches, because the ratio between the two is itself worth seeing.

## Two definitions of "last touch", on purpose

`Customer.health_inputs()` measures days-since-touch from **Activity rows
only** — that is what the health rubric's Customer Touch component scores, and
what the renewal churn rule reads. This module measures `last_contact` across
*every* kind of logged contact, which is the honest answer to "has anyone
spoken to them".

They can therefore differ, and the response returns both rather than quietly
picking one. Broadening the rubric's own definition would be defensible — a
logged call is plainly a touch — but it would silently move every health score
and every snapshot of history already recorded, which is a decision to make
deliberately and not as a side effect of building a dashboard.

## Attribution is by account owner, never by name

Email carries `sender_name`, Call a `host_name`, Task an `assignee_name`, Note
an `author_name` — all free text, none of them a user. Grouping a
team-performance view on free text would silently split "J. Smith" from "John
Smith" and invent a person. So per-CSM figures are **activity on the accounts
that CSM owns**, which is a real foreign key, and the response says so. It
answers "is this book being worked", not "who did the work".
"""

from datetime import timedelta

from django.db.models import Count
from django.db.models.functions import TruncWeek
from django.utils import timezone

from services.fx_rates.conversion import convert_to_org_currency, rates_for

from . import churn
from .contact import TOUCH_SOURCES, last_contact_by_customer, parent_q
from .models import Call, Customer, Email, Task, Ticket
from .scoping import live_customers

#: The analysis window, in days. A quarter: long enough that a weekly trend has
#: shape, short enough that "did we work it" is still a live question.
DEFAULT_WINDOW_DAYS = 90

#: What counts as a touch, and the field each one dates itself by.
#:
#: `Email` is here because a logged email is work somebody did — the model has
#: no direction flag, so an inbound reply counts too. That is a known
#: overstatement, named here rather than hidden: the alternative is dropping
#: the largest source of real contact from a coverage metric.
#: Counted separately: customer-initiated, so not evidence of team output.
INBOUND_SOURCES = {"tickets": (Ticket, "opened_at", "Tickets")}

#: Days since the last contact past which an account is "going dark". The same
#: threshold the churn rule uses for its cold-contact adjustment, imported
#: rather than repeated so the two screens can't disagree about what stale
#: means.
GOING_DARK_DAYS = churn.CONTACT_COLD_DAYS

#: Cadence buckets, freshest first. `to` is exclusive; the last is open-ended.
CADENCE_BUCKETS = (
    ("week", "This week", 0, 8),
    ("month", "8–30 days", 8, 31),
    ("stale", "31–60 days", 31, 61),
    ("dark", "61–90 days", 61, 91),
    ("cold", "90+ days", 91, None),
)

#: How many rows the going-dark list carries.
LIST_LIMIT = 15


def _parse_int(raw):
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def window_days(params):
    """`?days=`, clamped. Under a week there is no trend to draw; past two
    years the question stops being operational."""
    value = _parse_int(params.get("days"))
    if value is None:
        return DEFAULT_WINDOW_DAYS
    return max(7, min(730, value))


def filtered_customers(user, params):
    """The visible book, narrowed by the bar's filters. Visibility first."""

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


def _counts_by_week(model, field, ids, since):
    """`{week_start: count}` for one source inside the window.

    `.order_by()` before the annotate is load-bearing: every one of these
    models has a `Meta.ordering`, which Django folds into the GROUP BY, and
    grouping by (week, occurred_at, id) makes every bucket count exactly one.
    """
    lookup = f"{field}__date__gte" if model in (Email, Call) else f"{field}__gte"
    rows = (
        model.objects.filter(parent_q(ids))
        .filter(**{lookup: since})
        .order_by()
        .annotate(week=TruncWeek(field))
        .values("week")
        .annotate(n=Count("id", distinct=True))
    )
    return {row["week"]: row["n"] for row in rows if row["week"] is not None}


def _bucket_for(days):
    for key, _label, floor, ceiling in CADENCE_BUCKETS:
        if days >= floor and (ceiling is None or days < ceiling):
            return key
    return CADENCE_BUCKETS[-1][0]


def dark_accounts(customers, latest, today, organisation, rates):
    """Every account nobody has touched in GOING_DARK_DAYS, never-contacted
    first, then longest silence. The KPI, the page's capped list and the
    drill all read this one list."""

    rows = []
    for customer in customers:
        last = latest.get(customer.pk)
        age = None if last is None else (today - last).days
        if age is not None and age < GOING_DARK_DAYS:
            continue
        converted = convert_to_org_currency(
            customer.arr_billed_at_account, customer.currency, organisation, rates=rates
        )
        rows.append(
            {
                "id": customer.id,
                "name": customer.name,
                "owner": customer.owner.name if customer.owner else "Unassigned",
                "arr": None if converted is None else float(converted),
                "health_category": customer.health_category,
                "lifecycle_stage": customer.get_lifecycle_stage_display(),
                "last_contact": last.isoformat() if last else None,
                "days_since_contact": age,
                "renewal_date": (
                    customer.renewal_date.isoformat() if customer.renewal_date else None
                ),
            }
        )
    # Longest silence first; "never" sorts to the top, since an account nobody
    # has ever logged a contact for is the worst case, not a missing value.
    rows.sort(
        key=lambda row: (row["days_since_contact"] is not None, -(row["days_since_contact"] or 0))
    )
    return rows


def gone_quiet_values(user, params):
    customers = list(filtered_customers(user, params))
    ids = [c.pk for c in customers]
    latest = last_contact_by_customer(ids) if ids else {}
    organisation = user.organisation
    rows = dark_accounts(
        customers, latest, timezone.localdate(), organisation, rates_for(organisation)
    )
    return {row["id"]: row["days_since_contact"] for row in rows}


def build_stats(user, params):
    """Every rollup the Activity Tracking tab draws."""

    organisation = user.organisation
    customers = list(filtered_customers(user, params))
    ids = [customer.pk for customer in customers]
    days = window_days(params)
    today = timezone.localdate()
    since = today - timedelta(days=days)
    rates = rates_for(organisation)

    # ── volume, by week and by source ────────────────────────────────
    weekly = {}
    by_source = []
    touches = 0
    for key, (model, field, label) in TOUCH_SOURCES.items():
        counts = _counts_by_week(model, field, ids, since) if ids else {}
        total = sum(counts.values())
        touches += total
        by_source.append({"key": key, "name": label, "count": total})
        for week, count in counts.items():
            week_date = week.date() if hasattr(week, "date") else week
            bucket = weekly.setdefault(week_date, {k: 0 for k in TOUCH_SOURCES})
            bucket[key] += count

    inbound = 0
    for model, field, _label in INBOUND_SOURCES.values():
        if not ids:
            continue
        lookup = f"{field}__date__gte" if model in (Email, Call) else f"{field}__gte"
        inbound += model.objects.filter(parent_q(ids)).filter(**{lookup: since}).distinct().count()

    timeline = [
        {"date": f"{week:%b} {week.day}", "iso": week.isoformat(), **counts}
        for week, counts in sorted(weekly.items())
    ]

    # ── coverage and cadence ─────────────────────────────────────────
    latest = last_contact_by_customer(ids) if ids else {}
    cadence = {
        key: {"key": key, "name": label, "accounts": 0, "arr": 0.0}
        for key, label, _f, _c in CADENCE_BUCKETS
    }
    never = {"key": "never", "name": "No contact logged", "accounts": 0, "arr": 0.0}

    touched_in_window = 0
    for customer in customers:
        converted = convert_to_org_currency(
            customer.arr_billed_at_account, customer.currency, organisation, rates=rates
        )
        arr = None if converted is None else float(converted)
        last = latest.get(customer.pk)

        if last is None:
            never["accounts"] += 1
            never["arr"] += arr or 0.0
        else:
            age = (today - last).days
            bucket = cadence[_bucket_for(age)]
            bucket["accounts"] += 1
            bucket["arr"] += arr or 0.0
            if last >= since:
                touched_in_window += 1

    dark = dark_accounts(customers, latest, today, organisation, rates)

    # ── tasks ────────────────────────────────────────────────────────
    tasks = Task.objects.filter(parent_q(ids)).distinct() if ids else Task.objects.none()
    overdue = tasks.filter(due_date__lt=today).exclude(status=Task.Status.COMPLETED).count()
    open_tasks = tasks.exclude(status=Task.Status.COMPLETED).count()
    completed = tasks.filter(status=Task.Status.COMPLETED).count()

    # ── per owner ────────────────────────────────────────────────────
    owners = {}
    for customer in customers:
        name = customer.owner.name if customer.owner else "Unassigned"
        entry = owners.setdefault(
            name, {"owner": name, "accounts": 0, "touched": 0, "dark": 0, "arr_dark": 0.0}
        )
        entry["accounts"] += 1
        last = latest.get(customer.pk)
        if last is not None and last >= since:
            entry["touched"] += 1
        age = None if last is None else (today - last).days
        if age is None or age >= GOING_DARK_DAYS:
            entry["dark"] += 1
            converted = convert_to_org_currency(
                customer.arr_billed_at_account, customer.currency, organisation, rates=rates
            )
            entry["arr_dark"] += float(converted) if converted is not None else 0.0

    by_owner = sorted(owners.values(), key=lambda row: (-row["dark"], -row["accounts"]))

    return {
        "window_days": days,
        "kpis": {
            "touches": touches,
            "inbound": inbound,
            "accounts": len(customers),
            "touched_accounts": touched_in_window,
            "coverage": (round(touched_in_window / len(customers) * 100, 1) if customers else None),
            "dark_accounts": len(dark),
            "dark_arr": round(sum(row["arr"] or 0.0 for row in dark), 2),
            "open_tasks": open_tasks,
            "overdue_tasks": overdue,
            "completed_tasks": completed,
        },
        "timeline": timeline,
        "sources": by_source,
        "cadence": [cadence[key] for key, _l, _f, _c in CADENCE_BUCKETS] + [never],
        "by_owner": by_owner,
        "going_dark": dark[:LIST_LIMIT],
        "going_dark_threshold": GOING_DARK_DAYS,
        "currency": organisation.currency,
    }


def filter_options(user):
    """The bar's dropdowns, scoped exactly as the numbers are."""

    customers = live_customers(user)
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
