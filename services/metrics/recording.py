"""Record the metric layer as a month ended.

Same rule as `HealthSnapshot`: one row per metric per period, and an existing
row is **kept, not overwritten** — it is a record of how the month ended, and
upserting it a week later would quietly replace it with the following month's
values so the history drifted forward every time the job ran.
"""

from decimal import Decimal

from .models import MetricSnapshot
from .registry import METRICS, compute_all, compute_slices


def record_period_end(organisation, period_end, dry_run=False):
    """Write this organisation's snapshots for `period_end` — the whole-org
    rows and every cut — skipping any row that already exists.

    Returns `(written, already_had)`, where `already_had` counts the
    whole-org metrics that were already recorded. The cuts are checked
    separately, so a period recorded before a cut existed gets that cut
    filled in — the same "added later is filled in" rule the metrics have.
    """
    existing = set(
        MetricSnapshot.objects.filter(
            organisation=organisation, dimension="", member="", period_end=period_end
        ).values_list("metric", flat=True)
    )
    missing = [metric for metric in METRICS if metric.key not in existing]
    rows = []
    if missing:
        values = compute_all(organisation)
        rows = [
            MetricSnapshot(
                organisation=organisation,
                metric=metric.key,
                period_end=period_end,
                value=None if values[metric.key] is None else Decimal(str(values[metric.key])),
            )
            for metric in missing
        ]
    # The cuts, for the metrics that have them. A member absent this month
    # (an owner with no customers now) simply has no row; a member's row
    # from a past month is kept like any other.
    have_members = set(
        MetricSnapshot.objects.filter(organisation=organisation, period_end=period_end)
        .exclude(dimension="")
        .values_list("metric", "dimension", "member")
    )
    for metric_key, by_dimension in compute_slices(organisation).items():
        for dimension, members in by_dimension.items():
            for member, _label, value in members:
                if (metric_key, dimension, member) in have_members:
                    continue
                rows.append(
                    MetricSnapshot(
                        organisation=organisation,
                        metric=metric_key,
                        dimension=dimension,
                        member=member,
                        period_end=period_end,
                        value=None if value is None else Decimal(str(value)),
                    )
                )

    if not dry_run:
        MetricSnapshot.objects.bulk_create(rows)
    return len(rows), len(existing)
