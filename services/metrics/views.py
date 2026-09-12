"""The metric layer over the API.

Gated on `view_all_accounts`, not on `IsAuthenticated`: every number here is
the whole organisation's, and a CSM whose book is scoped to their own
customers would otherwise read the company's ARR off this endpoint.
"""

from decimal import Decimal

from rest_framework import views
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

from services.accounts.permissions import CanViewAllAccounts

from .models import MetricSnapshot
from .registry import BY_KEY, METRICS, as_of, compute_all


def _number(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return value


def _describe(metric):
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


class MetricListView(views.APIView):
    """GET /api/v1/metrics/ — every metric, its value now, and the last
    month-end it was recorded at, so a screen can show the number and
    which way it has moved without a second request."""

    permission_classes = [CanViewAllAccounts]

    def get(self, request):
        organisation = request.user.organisation
        values = compute_all(organisation)

        latest = {}
        for row in MetricSnapshot.objects.filter(
            organisation=organisation, dimension="", member=""
        ).order_by("metric", "-period_end"):
            latest.setdefault(row.metric, row)

        metrics = []
        for metric in METRICS:
            now = _number(values[metric.key])
            previous = latest.get(metric.key)
            previous_value = _number(previous.value) if previous else None
            metrics.append(
                {
                    **_describe(metric),
                    "value": now,
                    "previous": (
                        {"period_end": previous.period_end.isoformat(), "value": previous_value}
                        if previous
                        else None
                    ),
                    # Null when either side is unmeasured: a change from
                    # "unknown" to 40 is not a rise of 40.
                    "change": (
                        round(now - previous_value, 4)
                        if now is not None and previous_value is not None
                        else None
                    ),
                }
            )

        return Response(
            {"as_of": as_of().isoformat(), "currency": organisation.currency, "metrics": metrics}
        )


class MetricHistoryView(views.APIView):
    """GET /api/v1/metrics/<key>/history/ — the month-end series for one
    metric, oldest first. A month with no row is absent, not zero."""

    permission_classes = [CanViewAllAccounts]

    def get(self, request, key):
        metric = BY_KEY.get(key)
        if metric is None:
            raise NotFound(f"No metric called {key!r}.")

        points = [
            {"period_end": row.period_end.isoformat(), "value": _number(row.value)}
            for row in MetricSnapshot.objects.filter(
                organisation=request.user.organisation, metric=key, dimension="", member=""
            ).order_by("period_end")
        ]
        return Response(
            {
                "metric": _describe(metric),
                "currency": request.user.organisation.currency,
                "points": points,
            }
        )


def _latest_by_member(organisation, metric_key, dimension):
    """The most recent month-end row per member for one cut."""
    latest = {}
    for row in MetricSnapshot.objects.filter(
        organisation=organisation, metric=metric_key, dimension=dimension
    ).order_by("member", "-period_end"):
        latest.setdefault(row.member, row)
    return latest


def _members_payload(organisation, metric, dimension):
    from .registry import compute_slice

    latest = _latest_by_member(organisation, metric.key, dimension)
    members = []
    for member, label, value in compute_slice(organisation, metric, dimension):
        now = _number(value)
        previous = latest.get(member)
        previous_value = _number(previous.value) if previous else None
        members.append(
            {
                "member": member,
                "label": label,
                "value": now,
                "previous": (
                    {"period_end": previous.period_end.isoformat(), "value": previous_value}
                    if previous
                    else None
                ),
                "change": (
                    round(now - previous_value, 4)
                    if now is not None and previous_value is not None
                    else None
                ),
            }
        )
    # Largest first, unmeasured last — the reader wants the biggest member on top.
    members.sort(key=lambda m: (m["value"] is None, -(m["value"] or 0)))
    return members


class MetricSliceView(views.APIView):
    """GET /api/v1/metrics/<key>/by/<dimension>/ — one metric cut one way,
    every member with its value now and its move since the last month-end.
    The "why" layer's raw material: ARR at risk by product, NRR by owner."""

    permission_classes = [CanViewAllAccounts]

    def get(self, request, key, dimension):
        from .registry import DIMENSION_LABELS

        metric = BY_KEY.get(key)
        if metric is None:
            raise NotFound(f"No metric called {key!r}.")
        if dimension not in metric.slices:
            raise NotFound(
                f"{key!r} cannot be cut by {dimension!r}; it can by "
                f"{', '.join(sorted(metric.slices)) or 'nothing'}."
            )
        organisation = request.user.organisation
        return Response(
            {
                "metric": _describe(metric),
                "dimension": {"key": dimension, "label": DIMENSION_LABELS[dimension]},
                "currency": organisation.currency,
                "members": _members_payload(organisation, metric, dimension),
            }
        )


#: What counts as a material move since the last month-end. Percent-unit
#: metrics move in points; money and counts move relative to where they were.
#: Deliberately blunt — the point is a short list a manager reads, not a
#: statistical test over one month of history.
SIGNAL_POINTS = 5.0
SIGNAL_RELATIVE = 0.10


def _material(metric, now, previous):
    if now is None or previous is None:
        return False
    if metric.unit == "percent":
        return abs(now - previous) >= SIGNAL_POINTS
    if previous == 0:
        return now != 0
    return abs(now - previous) / abs(previous) >= SIGNAL_RELATIVE


class MetricSignalsView(views.APIView):
    """GET /api/v1/metrics/signals/ — the metrics that moved materially since
    the last month-end, worst first, each naming the member that moved it
    most. Empty until a second month-end exists to compare against; the
    response says so rather than inventing a baseline."""

    permission_classes = [CanViewAllAccounts]

    def get(self, request):
        from .registry import DIMENSION_LABELS, compute_all, compute_slices

        organisation = request.user.organisation
        values = compute_all(organisation)

        latest = {}
        for row in MetricSnapshot.objects.filter(
            organisation=organisation, dimension="", member=""
        ).order_by("metric", "-period_end"):
            latest.setdefault(row.metric, row)
        baseline = max((row.period_end for row in latest.values()), default=None)

        signals = []
        slices = None
        for metric in METRICS:
            previous = latest.get(metric.key)
            now = _number(values[metric.key])
            previous_value = _number(previous.value) if previous else None
            if not _material(metric, now, previous_value):
                continue
            change = round(now - previous_value, 4)
            improved = None if metric.better == "none" else (change > 0) == (metric.better == "up")

            drivers = []
            if metric.slices:
                if slices is None:
                    slices = compute_slices(organisation)
                for dimension, members in slices[metric.key].items():
                    by_member = _latest_by_member(organisation, metric.key, dimension)
                    for member, label, value in members:
                        was = by_member.get(member)
                        if value is None or was is None or was.value is None:
                            continue
                        move = round(_number(value) - _number(was.value), 4)
                        if move:
                            drivers.append(
                                {
                                    "dimension": dimension,
                                    "dimension_label": DIMENSION_LABELS[dimension],
                                    "member": member,
                                    "label": label,
                                    "value": _number(value),
                                    "change": move,
                                }
                            )
                drivers.sort(key=lambda d: -abs(d["change"]))

            signals.append(
                {
                    **_describe(metric),
                    "value": now,
                    "previous": {
                        "period_end": previous.period_end.isoformat(),
                        "value": previous_value,
                    },
                    "change": change,
                    "improved": improved,
                    "drivers": drivers[:5],
                }
            )

        # Bad news first, biggest relative move first within it.
        def rank(signal):
            rel = abs(signal["change"]) / abs(signal["previous"]["value"] or 1)
            return (signal["improved"] is not False, -rel)

        signals.sort(key=rank)
        return Response(
            {
                "as_of": as_of().isoformat(),
                "baseline": baseline.isoformat() if baseline else None,
                "currency": organisation.currency,
                "signals": signals,
            }
        )
