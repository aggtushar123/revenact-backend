"""An initiative judged against the metric layer.

The registry is the only source of "where is it now": the same value the
Brain overview shows, whole-org or one cut of it. Nothing here re-derives a
number; it reads one and compares it with the starting line and the target.
"""

from decimal import Decimal

from django.utils import timezone

from .models import MetricSnapshot
from .registry import BY_KEY, compute_all, compute_slices
from .signals import number


class Figures:
    """The registry, computed once for a request and read many times —
    a page of twenty initiatives must not run the rollups twenty times."""

    def __init__(self, organisation):
        self.organisation = organisation
        self._values = None
        self._slices = None

    def value_of(self, initiative):
        metric = BY_KEY.get(initiative.metric)
        if metric is None:
            return None
        if not initiative.dimension:
            if self._values is None:
                self._values = compute_all(self.organisation)
            return number(self._values.get(initiative.metric))
        if self._slices is None:
            self._slices = compute_slices(self.organisation)
        members = self._slices.get(initiative.metric, {}).get(initiative.dimension, [])
        for member, _label, value in members:
            if member == initiative.member:
                return number(value)
        return None

    def label_of(self, initiative):
        """The member's label as it reads now, or the one stored at creation."""
        if not initiative.dimension:
            return ""
        if self._slices is None:
            self._slices = compute_slices(self.organisation)
        for member, label, _value in self._slices.get(initiative.metric, {}).get(
            initiative.dimension, []
        ):
            if member == initiative.member:
                return label
        return initiative.member_label


def progress(initiative, current, today=None):
    """Where the initiative stands: `progress_pct` of the way from the
    starting line to the target (clamped 0-100), null when either end is
    unmeasured or the target is the baseline; `days_left`, negative once
    the date has passed; `direction`, which way the metric is being pushed.
    """
    today = today or timezone.localdate()
    baseline = number(initiative.baseline_value)
    target = number(initiative.target_value)
    metric = BY_KEY.get(initiative.metric)

    pct = None
    if current is not None and baseline is not None and target != baseline:
        pct = round(max(0.0, min(100.0, (current - baseline) / (target - baseline) * 100)), 1)

    return {
        "baseline": baseline,
        "current": current,
        "target": target,
        "progress_pct": pct,
        "days_left": (initiative.target_by - today).days,
        "direction": "up" if target > (baseline if baseline is not None else target) else "down",
        "unit": metric.unit if metric else None,
        "better": metric.better if metric else None,
    }


def history(initiative):
    """Month-end points for this initiative's number since its baseline."""
    rows = MetricSnapshot.objects.filter(
        organisation=initiative.organisation,
        metric=initiative.metric,
        dimension=initiative.dimension,
        member=initiative.member,
        period_end__gte=initiative.baseline_as_of,
    ).order_by("period_end")
    return [{"period_end": row.period_end.isoformat(), "value": number(row.value)} for row in rows]


def as_decimal(value):
    return None if value is None else Decimal(str(value))


def create_initiative(organisation, created_by, **fields):
    """Write an initiative with its starting line captured from the registry
    as it stands today. The one place this happens — the API serializer and
    an approved agent proposal both come through here."""
    from .models import Initiative

    probe = Initiative(
        organisation=organisation,
        **{k: v for k, v in fields.items() if k in ("metric", "dimension", "member")},
    )
    return Initiative.objects.create(
        organisation=organisation,
        created_by=created_by,
        baseline_value=as_decimal(Figures(organisation).value_of(probe)),
        baseline_as_of=timezone.localdate(),
        **fields,
    )
