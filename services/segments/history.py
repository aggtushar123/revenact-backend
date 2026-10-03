"""A segment's entries and exits, as a viewer may read them."""

from datetime import timedelta

from django.db.models import Count, Exists, F, OuterRef, Q, Subquery
from django.db.models.expressions import Window
from django.db.models.functions import RowNumber

from .evaluate import MODELS, visible_records
from .models import SegmentChange

DEFAULT_DAYS = 30
MAX_DAYS = 90
#: How many records one day names in each direction; the rest are counted.
MAX_NAMED = 100

DIRECTIONS = (SegmentChange.Change.ENTERED, SegmentChange.Change.LEFT)


def change_history(segment, viewer, *, today, days):
    """Entries and exits over the last `days` (today included), newest day
    first. Only records the viewer may open are named, at most `MAX_NAMED`
    per day and direction (lowest id first); `totals` counts them all and
    `more` what the cap left unnamed. The rest (deleted ones included) are
    counted in `hidden_count`. Reasons are field keys.

    Two queries whatever the size, and no id list: visibility is a
    correlated `EXISTS`, and the cap a window over each day and direction."""
    window = SegmentChange.objects.filter(
        segment=segment, changed_on__gt=today - timedelta(days=days)
    )
    # SOC2:AUTH-02 a record is named, or counted per day, only if the viewer may open it
    openable = Exists(visible_records(segment.kind, viewer).filter(pk=OuterRef("record_id")))
    totals = (
        window.order_by()
        .values("changed_on", "change")
        .annotate(total=Count("pk"), shown=Count("pk", filter=Q(openable)))
    )
    by_day, hidden = {}, 0
    for row in sorted(totals, key=lambda row: row["changed_on"], reverse=True):
        hidden += row["total"] - row["shown"]
        if not row["shown"]:
            continue
        day = by_day.setdefault(row["changed_on"], _day(row["changed_on"]))
        day["totals"][row["change"]] = row["shown"]
        day["more"][row["change"]] = max(row["shown"] - MAX_NAMED, 0)
    if not by_day:
        return {"kind": segment.kind, "days": [], "hidden_count": hidden}
    name = MODELS[segment.kind].objects.filter(pk=OuterRef("record_id")).values("name")[:1]
    named = (
        window.filter(openable)
        .annotate(
            name=Subquery(name),
            rank=Window(
                RowNumber(), partition_by=[F("changed_on"), F("change")], order_by="record_id"
            ),
        )
        .filter(rank__lte=MAX_NAMED)
        .order_by("-changed_on", "record_id")
        .values_list("record_id", "change", "changed_on", "reason", "name")
    )
    for record_id, change, changed_on, reason, record_name in named:
        by_day[changed_on][change].append(
            {"id": record_id, "name": record_name, "reason": list(reason)}
        )
    return {"kind": segment.kind, "days": list(by_day.values()), "hidden_count": hidden}


def _day(changed_on):
    return {
        "date": changed_on.isoformat(),
        "entered": [],
        "left": [],
        "totals": dict.fromkeys(DIRECTIONS, 0),
        "more": dict.fromkeys(DIRECTIONS, 0),
    }
