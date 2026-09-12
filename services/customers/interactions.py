"""The rollups behind the AI Trending Topics dashboard.

One screen, seven charts, three record types: Email, Call and Ticket — what this
module calls an *interaction*. They are separate models (see Call's own docstring
for why a call isn't a calendar event, and Activity's for the parent shape they
share), so every number here is computed per model and merged, rather than read
from one table.

**One grouped query per model, not one per chart.** All six breakdowns — source,
sentiment, the weekly trend, area, category, subcategory — are different
projections of the same group-by, so each model is grouped once over
(week, sentiment, area, category, subcategory) and the rollups are folded out of
those rows in Python. Seven charts × three models would otherwise be twenty-one
round trips to draw one screen.

**The AI breakdowns count classified rows only.** An interaction nothing has
classified yet has a blank area/category/subcategory, and it is left out of those
three charts rather than counted into an "Unknown" bucket: the taxonomy charts
answer "what are customers talking about", and a row nobody has read yet is not
evidence about that. `classified`/`total` come back alongside, so the screen can
say how much of the book it is actually describing — see
`manage.py classify_interactions`, which is what fills those fields in.

Source and sentiment charts, by contrast, count everything. Both are true of a
record the moment it exists: its type is structural, and sentiment carries a real
default.
"""

from django.db.models import Count, Q
from django.db.models.functions import TruncWeek

from . import segments, taxonomy
from .models import Call, Email, Ticket
from .scoping import visible_accounts, visible_children_q, visible_customers

#: Per interaction type: the model, the date field its timeline is bucketed on,
#: and the label the source donut shows.
#:
#: The date field differs per model and each is the one that means "when this
#: happened" — never `created_at`, which is auto_now_add, so every seeded row
#: shares one timestamp and a trend over it would be a single spike (the same
#: trap `_sentiment_timeline` documents for tickets).
SOURCES = {
    "email": {"model": Email, "date_field": "sent_at", "label": "Email", "is_datetime": True},
    "call": {"model": Call, "date_field": "occurred_at", "label": "Call", "is_datetime": True},
    # A ticket is opened on a day, not at a time — so its date filter needs no
    # `__date` transform, and asking for one on a DateField is an error rather
    # than a no-op. Hence the flag instead of one lookup for all three.
    "ticket": {"model": Ticket, "date_field": "opened_at", "label": "Ticket", "is_datetime": False},
}

#: The "Revenue Bracket" filter's own brackets, shared with the Customer
#: Overview's composition chart — see segments.py. Two screens disagreeing
#: about what a mid-size account is would be worse than neither offering the
#: cut.
REVENUE_BRACKETS = segments.REVENUE_BRACKETS

#: How many rows the Detailed Activity Breakdown table gets. It is a "recent
#: examples" table, not a record browser — the mock it replaces showed fifteen
#: rows and has no paging, and an unbounded list here would put every
#: interaction in the tenant on the wire to draw one card.
RECENT_LIMIT = 50


def _parse_int(raw):
    """None for anything unparseable, which the caller treats as "no filter" —
    this app's own ignore-don't-400 convention for dashboard filters."""
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _parse_date(raw):
    """`YYYY-MM-DD` only, ignored otherwise — same as `_parse_int`."""
    from datetime import datetime

    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _revenue_q(bracket):
    """The ARR filter for one bracket, across both parent shapes.

    A customer's ARR is `arr_billed_at_account` (what the frontend's own
    `mapToOrgRow` already treats as its ARR) and an account's is `arr`. An
    interaction hangs off exactly one of the two, so the filter has to name both
    sides or it would silently drop every account-level row."""

    for value, _label, floor, ceiling in REVENUE_BRACKETS:
        if value != bracket:
            continue
        q = Q(customer__arr_billed_at_account__gte=floor) | Q(account__arr__gte=floor)
        if ceiling is not None:
            q &= Q(customer__arr_billed_at_account__lt=ceiling) | Q(account__arr__lt=ceiling)
        return q
    return None


def filtered_querysets(user, params):
    """One visibility-scoped, filtered queryset per interaction type.

    Visibility first, then the caller's filters narrow from there — applying a
    raw `?account=` id before the visibility gate is the mistake that previously
    let members read other owners' custom-object records.

    `?type=` drops whole models from the result rather than filtering rows: the
    source donut's own filter means "show me only calls", and a model excluded
    here costs nothing to aggregate.
    """

    wanted = params.getlist("type") or list(SOURCES)
    names = [name for name in SOURCES if name in wanted] or list(SOURCES)

    sentiment = params.get("sentiment")
    area = params.get("area")
    category = params.get("category")
    subcategory = params.get("subcategory")
    date_from = _parse_date(params.get("from"))
    date_to = _parse_date(params.get("to"))
    customer_id = _parse_int(params.get("customer"))
    account_id = _parse_int(params.get("account"))
    revenue_q = _revenue_q(params.get("revenue_bracket"))

    out = {}
    for name in names:
        spec = SOURCES[name]
        queryset = spec["model"].objects.filter(visible_children_q(user))

        if sentiment in taxonomy.Sentiment.values:
            queryset = queryset.filter(sentiment=sentiment)
        if area in taxonomy.AIArea.values:
            queryset = queryset.filter(ai_area=area)
        if category in taxonomy.AICategory.values:
            queryset = queryset.filter(ai_category=category)
        if subcategory in taxonomy.AISubcategory.values:
            queryset = queryset.filter(ai_subcategory=subcategory)

        field = spec["date_field"] + ("__date" if spec["is_datetime"] else "")
        if date_from:
            queryset = queryset.filter(**{f"{field}__gte": date_from})
        if date_to:
            queryset = queryset.filter(**{f"{field}__lte": date_to})

        if customer_id is not None:
            # An account-level interaction counts as its customer's too: the
            # filter names a company, and a company's conversations include the
            # ones its divisions had. Same direction of travel as visibility.
            queryset = queryset.filter(
                Q(customer_id=customer_id) | Q(account__customers__id=customer_id)
            )
        if account_id is not None:
            queryset = queryset.filter(account_id=account_id)
        if revenue_q is not None:
            queryset = queryset.filter(revenue_q)

        out[name] = queryset.distinct()
    return out


def _grouped_rows(name, queryset):
    """One group-by per model: every dimension the six charts need, counted once.

    `.order_by()` before the annotate is load-bearing, not tidying. Each of
    these models has a `Meta.ordering`, and Django folds a model's default
    ordering into the GROUP BY — grouping by (sentiment, …, sent_at, id) would
    make every bucket count exactly 1, which is the bug the Ticket dashboard
    shipped with and its own comment now warns about."""

    field = SOURCES[name]["date_field"]
    return (
        queryset.order_by()
        .annotate(week=TruncWeek(field))
        .values("week", "sentiment", "ai_area", "ai_category", "ai_subcategory")
        .annotate(n=Count("id"))
    )


def _labelled(choices_class, counts):
    """`{value: n}` → the chart's own `[{name, value}]`, every bucket present.

    Every choice is emitted even at zero, in the vocabulary's own order: a donut
    whose segment vanishes when a category empties out is harder to read than
    one with an empty segment, and a legend that reorders itself between two
    refreshes is harder still."""

    return [
        {"key": value, "name": label, "value": counts.get(value, 0)}
        for value, label in choices_class.choices
    ]


def build_stats(querysets):
    """Every rollup the Controls tab draws, from one pass per model."""

    by_type = []
    sentiment_counts = {}
    area_counts = {}
    category_counts = {}
    subcategory_counts = {}
    weekly = {}
    total = 0
    classified = 0

    for name, queryset in querysets.items():
        source_total = 0
        for row in _grouped_rows(name, queryset):
            n = row["n"]
            source_total += n

            sentiment_counts[row["sentiment"]] = sentiment_counts.get(row["sentiment"], 0) + n

            week = row["week"]
            if week is not None:
                bucket = weekly.setdefault(
                    week.date() if hasattr(week, "date") else week,
                    {"positive": 0, "neutral": 0, "negative": 0},
                )
                if row["sentiment"] in bucket:
                    bucket[row["sentiment"]] += n

            # Blank means unclassified, which is left out of the taxonomy
            # charts rather than bucketed as "Unknown" — see the module
            # docstring. `classified` counts a row with any taxonomy on it.
            if row["ai_area"]:
                area_counts[row["ai_area"]] = area_counts.get(row["ai_area"], 0) + n
            if row["ai_category"]:
                category_counts[row["ai_category"]] = category_counts.get(row["ai_category"], 0) + n
                classified += n
            if row["ai_subcategory"]:
                subcategory_counts[row["ai_subcategory"]] = (
                    subcategory_counts.get(row["ai_subcategory"], 0) + n
                )

        by_type.append({"key": name, "name": SOURCES[name]["label"], "value": source_total})
        total += source_total

    return {
        "total": total,
        "classified": classified,
        "by_type": by_type,
        "sentiment": _labelled(taxonomy.Sentiment, sentiment_counts),
        "areas": _labelled(taxonomy.AIArea, area_counts),
        # Categories and subcategories are ordered by size, biggest first:
        # both are horizontal bar charts, where the ranking *is* the reading,
        # unlike the donuts above whose legends want a stable order. Empty
        # buckets are dropped here for the same reason — a bar of length zero
        # is a label with nothing beside it.
        "categories": _ranked(taxonomy.AICategory, category_counts),
        "subcategories": _ranked(taxonomy.AISubcategory, subcategory_counts),
        "sentiment_timeline": [
            {
                # Pre-formatted because the chart renders it verbatim as a
                # category tick, in the same "MMM d, yyyy" the mock used. Built
                # by hand rather than with a "%-d" day: that's a glibc/BSD
                # extension, not portable, and a zero-padded "Jun 08" is not
                # what the axis said.
                "date": f"{week:%b} {week.day}, {week.year}",
                **counts,
            }
            for week, counts in sorted(weekly.items())
        ],
    }


def _ranked(choices_class, counts):
    labels = dict(choices_class.choices)
    return [
        {"key": value, "name": labels[value], "value": count}
        for value, count in sorted(counts.items(), key=lambda pair: (-pair[1], labels[pair[0]]))
    ]


def recent_rows(querysets):
    """The Detailed Activity Breakdown table — the most recent interactions
    across all three types, newest first.

    Merged in Python rather than by a SQL UNION: the three models have
    different columns and different date fields, so a union would need every
    one of them aliased into a common shape for a fifty-row table. Each model
    contributes at most RECENT_LIMIT rows, so the merge reads at most 150 and
    keeps the newest 50 — not every interaction in the tenant.
    """

    collected = []
    for name, queryset in querysets.items():
        spec = SOURCES[name]
        rows = queryset.select_related("customer", "account").order_by(f"-{spec['date_field']}")[
            :RECENT_LIMIT
        ]
        for record in rows:
            parent = record.customer or record.account
            when = getattr(record, spec["date_field"])
            collected.append(
                {
                    "id": f"{name}:{record.pk}",
                    "source": spec["label"],
                    "account": parent.name if parent else "—",
                    "title": _title_for(name, record),
                    "sentiment": record.get_sentiment_display(),
                    "area": record.get_ai_area_display() or "",
                    "category": record.get_ai_category_display() or "",
                    "subcategory": record.get_ai_subcategory_display() or "",
                    # The stored values, for a screen that lets someone correct
                    # them — the labels above are for reading.
                    "keys": {
                        "sentiment": record.sentiment,
                        "area": record.ai_area,
                        "category": record.ai_category,
                        "subcategory": record.ai_subcategory,
                    },
                    "corrected": record.classification_corrected_at is not None,
                    "occurred_on": (when.date() if hasattr(when, "date") else when).isoformat(),
                }
            )

    collected.sort(key=lambda row: row["occurred_on"], reverse=True)
    return collected[:RECENT_LIMIT]


def _title_for(name, record):
    """What the row is called. `subject` on an email, `title` on the other two —
    the same field each one's own card already uses as its heading."""
    return record.subject if name == "email" else record.title


def filter_options(user):
    """The filter bar's dropdowns, shipped alongside the numbers so the bar
    needs no fetch of its own and its choices are scoped exactly as the numbers
    are — a CSM can't filter by a company they can't see.

    Subcategories carry their parent `category`, so the bar can narrow that
    dropdown to the category already chosen instead of offering twenty-five
    values of which three can match."""

    return {
        "customers": [
            {"id": c.id, "name": c.name} for c in visible_customers(user).order_by("name")
        ],
        "accounts": [{"id": a.id, "name": a.name} for a in visible_accounts(user).order_by("name")],
        "types": [{"value": name, "name": spec["label"]} for name, spec in SOURCES.items()],
        "sentiments": [{"value": v, "name": label} for v, label in taxonomy.Sentiment.choices],
        "areas": [{"value": v, "name": label} for v, label in taxonomy.AIArea.choices],
        "categories": [{"value": v, "name": label} for v, label in taxonomy.AICategory.choices],
        "subcategories": [
            {
                "value": sub.value,
                "name": sub.label,
                "category": category.value,
            }
            for category, subs in taxonomy.SUBCATEGORIES_BY_CATEGORY.items()
            for sub in subs
        ],
        "revenue_brackets": [
            {"value": value, "name": label} for value, label, _floor, _ceiling in REVENUE_BRACKETS
        ],
    }
