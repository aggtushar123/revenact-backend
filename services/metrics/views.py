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
