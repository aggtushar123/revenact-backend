"""The segment page's tiles and the builder's totals.

They cover every member the viewer may open, not the page and not the
search, so a tile always equals the members it covers. Entered and left
count only records the viewer may open.
"""

from datetime import timedelta

from django.db.models import Avg, Count, Q, Sum

from services.customers.contact_list import contacts_summary
from services.fx_rates.conversion import rates_for

from .compiler import arr_expression
from .evaluate import visible_records
from .models import SegmentChange

#: The "entered" and "left" tiles' window, today included.
WINDOW_DAYS = 7


def _tenth(value):
    return None if value is None else round(float(value), 1)


def recent_changes(segment, viewer, *, today, days=WINDOW_DAYS):
    """Entries and exits over the last `days` on records `viewer` may open.
    None for an unsaved segment (the builder's preview)."""
    if segment.pk is None:
        return {"entered": None, "left": None}
    # SOC2:AUTH-02 only records the viewer may open are counted
    rows = SegmentChange.objects.filter(
        segment_id=segment.pk,
        changed_on__gt=today - timedelta(days=days),
        record_id__in=visible_records(segment.kind, viewer).values("pk"),
    )
    return rows.aggregate(
        entered=Count("pk", filter=Q(change=SegmentChange.Change.ENTERED)),
        left=Count("pk", filter=Q(change=SegmentChange.Change.LEFT)),
    )


def segment_summary(members, segment, viewer, *, today, rates=None):
    """The tiles over `members`, a queryset of the viewer's members. An
    organisation's ARR is `arr_expression`'s (the workspace mapping,
    converted, unconvertible counted not summed); an account's is its own
    `arr`. Pass `rates` when the caller has read them already."""
    organisation = viewer.organisation
    tiles = {
        "members": 0,
        "arr": None,
        "unconverted_count": 0,
        "avg_health": None,
        "avg_csat": None,
    }
    if segment.kind == "contact":
        people = contacts_summary(members)
        tiles.update(members=people["total"], contacts=people)
    else:
        aggregates = {
            "members": Count("pk"),
            "avg_health": Avg("health_score"),
            "avg_csat": Avg("csat_score"),
        }
        if segment.kind == "customer":
            rates = rates if rates is not None else rates_for(organisation)
            members = members.annotate(_seg_arr=arr_expression(organisation, rates))
            aggregates["arr"] = Sum("_seg_arr")
            # Unconverted: a value with no rate. A member with no value has
            # nothing to convert.
            column = organisation.effective_global_attributes()["arr"]
            aggregates["unconverted_count"] = Count(
                "pk", filter=Q(_seg_arr__isnull=True) & Q(**{f"{column}__isnull": False})
            )
        else:
            aggregates["arr"] = Sum("arr")
        row = members.aggregate(**aggregates)
        tiles.update(
            members=row["members"],
            arr=round(float(row["arr"] or 0), 2),
            unconverted_count=row.get("unconverted_count", 0),
            avg_health=_tenth(row["avg_health"]),
            avg_csat=_tenth(row["avg_csat"]),
        )
    changes = recent_changes(segment, viewer, today=today)
    tiles.update(
        entered_7d=changes["entered"], left_7d=changes["left"], currency=organisation.currency
    )
    return tiles
