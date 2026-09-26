"""Health & usage: what changed from one month-end reading to the next.

`HealthSnapshot` is the only stored history there is: health score, AI pulse
and CSM pulse, per customer or account, per month-end. A snapshot is an item
when its health category, AI pulse or CSM pulse differs from the same
parent's previous snapshot; the first snapshot has nothing to differ from.
`Customer.pulse` keeps undated dots and lifecycle changes are not recorded,
so neither is a source.

Snapshots are monthly, a few dozen per parent at most, so the organisation's
whole set loads in one query and pages in Python, like the portfolio.
"""

from datetime import UTC, datetime, time

from django.db.models import DateTimeField
from django.db.models.functions import Cast

from services.customers.models import Customer, HealthSnapshot

from .items import make_item

_RANK = {
    Customer.HealthCategory.POOR: 0,
    Customer.HealthCategory.AVERAGE: 1,
    Customer.HealthCategory.GOOD: 2,
}


def _score(value):
    return f"{float(value):.1f}"


def _pulse(value):
    return "—" if value is None else str(value)


def describe_change(before, after):
    old, new = before.health_category, after.health_category
    ai_moved = before.ai_pulse_value != after.ai_pulse_value
    csm_moved = before.csm_pulse_score != after.csm_pulse_score
    if old == new and not ai_moved and not csm_moved:
        return None
    label = Customer.HealthCategory(new).label
    if _RANK[new] < _RANK[old]:
        title = f"Health fell to {label}"
    elif _RANK[new] > _RANK[old]:
        title = f"Health rose to {label}"
    else:
        title = "Pulse changed"
    parts = [f"Health {_score(before.health_score)} → {_score(after.health_score)}"]
    if ai_moved:
        parts.append(f"AI pulse {_pulse(before.ai_pulse_value)} → {_pulse(after.ai_pulse_value)}")
    if csm_moved:
        parts.append(
            f"CSM pulse {_pulse(before.csm_pulse_score)} → {_pulse(after.csm_pulse_score)}"
        )
    return title, " · ".join(parts)


def health_entries(scope, *, horizon):
    snapshots = (
        HealthSnapshot.objects.filter(scope.parent_q())
        .annotate(_at=Cast("captured_on", DateTimeField()))
        .filter(_at__lt=horizon)
        .only(
            "id",
            "customer_id",
            "account_id",
            "captured_on",
            "health_score",
            "ai_pulse_value",
            "csm_pulse_score",
        )
        .order_by("customer_id", "account_id", "captured_on", "id")
    )
    entries, previous = [], {}
    for snapshot in snapshots:
        parent = (snapshot.customer_id, snapshot.account_id)
        before, previous[parent] = previous.get(parent), snapshot
        change = None if before is None else describe_change(before, snapshot)
        if change is None:
            continue
        title, summary = change
        at = datetime.combine(snapshot.captured_on, time.min, tzinfo=UTC)
        item = make_item(
            scope,
            ident=snapshot.pk,
            kind="health",
            at=at,
            all_day=True,
            account_id=snapshot.account_id,
            title=title,
            summary=summary,
        )
        entries.append(((at, "health", snapshot.pk), item))
    return entries
