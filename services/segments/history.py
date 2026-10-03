"""A segment's entries and exits, as a viewer may read them."""

from datetime import timedelta

from .evaluate import visible_records
from .models import SegmentChange

DEFAULT_DAYS = 30
MAX_DAYS = 90


def change_history(segment, viewer, *, today, days):
    """Entries and exits over the last `days` (today included), newest day
    first. Only records the viewer may open are named; the rest (deleted
    ones included) are counted in `hidden_count`. Reasons are field keys."""
    rows = list(
        SegmentChange.objects.filter(segment=segment, changed_on__gt=today - timedelta(days=days))
        .order_by("-changed_on", "record_id")
        .values_list("record_id", "change", "changed_on", "reason")
    )
    ids = {row[0] for row in rows}
    names = {}
    if ids:
        # SOC2:AUTH-02 a record is named only if the viewer may open it
        records = visible_records(segment.kind, viewer).filter(pk__in=ids).order_by()
        names = dict(records.values_list("pk", "name").distinct())
    by_day, hidden = {}, 0
    for record_id, change, changed_on, reason in rows:
        if record_id not in names:
            hidden += 1
            continue
        day = by_day.setdefault(
            changed_on, {"date": changed_on.isoformat(), "entered": [], "left": []}
        )
        day[change].append({"id": record_id, "name": names[record_id], "reason": list(reason)})
    return {"kind": segment.kind, "days": list(by_day.values()), "hidden_count": hidden}
