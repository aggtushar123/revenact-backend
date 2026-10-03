"""A segment as the API returns it: the full record, and the list's row."""

from collections import defaultdict
from datetime import timedelta

from django.db.models import Count

from services.organizations.rows import iso, person

from .evaluate import openable_ids
from .models import SegmentChange
from .rules import present_rules

#: The list row's size sparkline, in days.
SPARKLINE_DAYS = 30


def segment_payload(segment, viewer):
    """The segment as `viewer` may read it: rules with what they cannot open
    redacted, labels for what they can, and only the pins and keep-outs
    they may open. `member_count` is the owner's nightly figure, so only the
    owner gets it; anyone else reads their own members (Ruling S8)."""
    is_owner = segment.owner_id == viewer.pk
    rules, labels = present_rules(segment.rules, segment.kind, user=viewer)
    # SOC2:AUTH-02 teammates are named only from the viewer's own workspace
    teammates = (
        segment.shared_with.filter(organisation_id=viewer.organisation_id)
        .order_by("name", "pk")
        .values_list("pk", "name")
    )
    return {
        "id": segment.pk,
        "name": segment.name,
        "description": segment.description,
        "kind": segment.kind,
        "rules": rules,
        "labels": labels,
        "pinned_ids": openable_ids(segment.kind, viewer, segment.pinned_ids),
        "excluded_ids": openable_ids(segment.kind, viewer, segment.excluded_ids),
        "sharing": segment.sharing,
        "shared_with": [{"id": pk, "name": name} for pk, name in teammates],
        "owner": person(segment.owner),
        "is_owner": is_owner,
        "alert_on_changes": segment.alert_on_changes,
        "paused": segment.paused,
        "member_count": segment.member_count if is_owner else None,
        "last_evaluated_on": iso(segment.last_evaluated_on),
        "created_at": iso(segment.created_at),
        "updated_at": iso(segment.updated_at),
    }


def daily_changes(segment_ids, *, since):
    """`{segment_id: {date: (entered, left)}}` from `since` on, in one query
    (none when `segment_ids` is empty)."""
    daily = defaultdict(dict)
    rows = (
        SegmentChange.objects.filter(segment_id__in=segment_ids, changed_on__gte=since)
        .order_by()
        .values("segment_id", "changed_on", "change")
        .annotate(n=Count("id"))
    )
    for row in rows:
        entered, left = daily[row["segment_id"]].get(row["changed_on"], (0, 0))
        if row["change"] == SegmentChange.Change.ENTERED:
            entered = row["n"]
        else:
            left = row["n"]
        daily[row["segment_id"]][row["changed_on"]] = (entered, left)
    return daily


def sparkline(count, end, daily):
    """The size on each of the `SPARKLINE_DAYS` days ending `end`, oldest
    first. It is rebuilt backwards from `count` (the size on `end`) and each
    day's entries and exits, so no daily copy of the members is ever kept."""
    if count is None or end is None:
        return []
    sizes = [count]
    for offset in range(SPARKLINE_DAYS - 1):
        entered, left = daily.get(end - timedelta(days=offset), (0, 0))
        sizes.append(sizes[-1] - entered + left)
    return sizes[::-1]


def list_rows(segments, viewer, *, today):
    """The list's rows. Counts are the owner's nightly figures and name
    nobody, and only the owner gets them: on a row shared with the viewer,
    `member_count`, `today` and `sparkline` are null, since the viewer's own
    members can differ from the owner's (Ruling S8)."""
    segments = list(segments)
    owned = [segment.pk for segment in segments if segment.owner_id == viewer.pk]
    daily = daily_changes(owned, since=today - timedelta(days=SPARKLINE_DAYS))
    rows = []
    for segment in segments:
        row = {
            "id": segment.pk,
            "name": segment.name,
            "kind": segment.kind,
            "owner": person(segment.owner),
            "is_owner": segment.owner_id == viewer.pk,
            "sharing": segment.sharing,
            "paused": segment.paused,
            "member_count": None,
            "today": None,
            "sparkline": None,
            "updated_at": iso(segment.updated_at),
        }
        if row["is_owner"]:
            days = daily.get(segment.pk, {})
            entered, left = days.get(today, (0, 0))
            row["member_count"] = segment.member_count
            row["today"] = {"entered": entered, "left": left}
            row["sparkline"] = sparkline(segment.member_count, segment.last_evaluated_on, days)
        rows.append(row)
    return rows
