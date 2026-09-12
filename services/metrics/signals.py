"""What moved materially since the last month-end, and what moved it.

One rule, read by the signals endpoint and by the management brief, so the
brief can never name a move the screen would not show.
"""

from decimal import Decimal

from .models import MetricSnapshot
from .registry import DIMENSION_LABELS, METRICS, as_of, compute_all, compute_slices

#: What counts as a material move since the last month-end. Percent-unit
#: metrics move in points; money and counts move relative to where they were.
#: Deliberately blunt — the point is a short list a manager reads, not a
#: statistical test over one month of history.
SIGNAL_POINTS = 5.0
SIGNAL_RELATIVE = 0.10

#: How many drivers a signal names.
DRIVER_LIMIT = 5


def number(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return value


def describe(metric):
    return {
        "key": metric.key,
        "label": metric.label,
        "unit": metric.unit,
        "better": metric.better,
        "note": metric.note,
        # The cuts this metric has, so a screen can offer them without a
        # round of 404s.
        "dimensions": sorted(metric.slices),
    }


def material(metric, now, previous):
    if now is None or previous is None:
        return False
    if metric.unit == "percent":
        return abs(now - previous) >= SIGNAL_POINTS
    if previous == 0:
        return now != 0
    return abs(now - previous) / abs(previous) >= SIGNAL_RELATIVE


def latest_whole_org(organisation):
    """The most recent month-end row per metric, whole-org."""
    latest = {}
    for row in MetricSnapshot.objects.filter(
        organisation=organisation, dimension="", member=""
    ).order_by("metric", "-period_end"):
        latest.setdefault(row.metric, row)
    return latest


def latest_by_member(organisation, metric_key, dimension):
    """The most recent month-end row per member for one cut."""
    latest = {}
    for row in MetricSnapshot.objects.filter(
        organisation=organisation, metric=metric_key, dimension=dimension
    ).order_by("member", "-period_end"):
        latest.setdefault(row.member, row)
    return latest


def signals_for(organisation, values=None):
    """The signals payload: `as_of`, `baseline`, `currency`, `signals`.

    `values` may be passed by a caller that has already run compute_all, so
    the brief doesn't pay for the rollups twice.
    """
    values = values if values is not None else compute_all(organisation)
    latest = latest_whole_org(organisation)
    baseline = max((row.period_end for row in latest.values()), default=None)

    signals = []
    slices = None
    for metric in METRICS:
        previous = latest.get(metric.key)
        now = number(values[metric.key])
        previous_value = number(previous.value) if previous else None
        if not material(metric, now, previous_value):
            continue
        change = round(now - previous_value, 4)
        improved = None if metric.better == "none" else (change > 0) == (metric.better == "up")

        drivers = []
        if metric.slices:
            if slices is None:
                slices = compute_slices(organisation)
            for dimension, members in slices[metric.key].items():
                by_member = latest_by_member(organisation, metric.key, dimension)
                for member, label, value in members:
                    was = by_member.get(member)
                    if value is None or was is None or was.value is None:
                        continue
                    move = round(number(value) - number(was.value), 4)
                    if move:
                        drivers.append(
                            {
                                "dimension": dimension,
                                "dimension_label": DIMENSION_LABELS[dimension],
                                "member": member,
                                "label": label,
                                "value": number(value),
                                "change": move,
                            }
                        )
            drivers.sort(key=lambda d: -abs(d["change"]))

        signals.append(
            {
                **describe(metric),
                "value": now,
                "previous": {
                    "period_end": previous.period_end.isoformat(),
                    "value": previous_value,
                },
                "change": change,
                "improved": improved,
                "drivers": drivers[:DRIVER_LIMIT],
            }
        )

    # Bad news first, biggest relative move first within it.
    def rank(signal):
        rel = abs(signal["change"]) / abs(signal["previous"]["value"] or 1)
        return (signal["improved"] is not False, -rel)

    signals.sort(key=rank)
    return {
        "as_of": as_of().isoformat(),
        "baseline": baseline.isoformat() if baseline else None,
        "currency": organisation.currency,
        "signals": signals,
    }
