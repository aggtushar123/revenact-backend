"""Record the metric layer as a month ended.

Same rule as `HealthSnapshot`: one row per metric per period, and an existing
row is **kept, not overwritten** — it is a record of how the month ended, and
upserting it a week later would quietly replace it with the following month's
values so the history drifted forward every time the job ran.
"""

from decimal import Decimal

from .models import MetricSnapshot
from .registry import METRICS, compute_all


def record_period_end(organisation, period_end, dry_run=False):
    """Write this organisation's whole-org snapshots for `period_end`.

    Returns `(written, already_had)`. Computes nothing if every metric already
    has a row — the rollups are not free on a large book, and the answer is
    already on disk.
    """
    existing = set(
        MetricSnapshot.objects.filter(
            organisation=organisation, dimension="", member="", period_end=period_end
        ).values_list("metric", flat=True)
    )
    missing = [metric for metric in METRICS if metric.key not in existing]
    if not missing:
        return 0, len(existing)

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
    if not dry_run:
        MetricSnapshot.objects.bulk_create(rows)
    return len(rows), len(existing)
