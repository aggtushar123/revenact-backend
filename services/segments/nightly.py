"""The nightly step: who entered each segment and who left.

`run_health_maintenance` calls `evaluate_nightly` after the health scores
and pulse dots are fresh. Each segment is evaluated **as its owner** (their
book is what the segment means to them), under a row lock, and compared with
`last_members`:
- the differences become `SegmentChange` rows, with the field keys that
  moved them;
- the owner gets at most one in-app alert per segment per day.

Idempotent per date: a segment evaluated today is skipped, and change rows
are unique per record per day. A segment whose owner is inactive is paused;
when the owner is back it resumes from a fresh baseline rather than report
the gap as one burst. One segment's failure is logged and skipped, as the
command's other steps are.
"""

import logging
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from services.notifications.models import Notification
from services.notifications.realtime import notify
from services.organizations.book import CHURNED

from .baseline import rebaseline
from .compiler import compile_rules
from .evaluate import MODELS, member_ids, visible_records
from .models import MAX_TRACKED_MEMBERS, Segment, SegmentChange

logger = logging.getLogger(__name__)

#: The alert quotes the owner's own name for the segment, cut to fit.
ALERT_NAME_LENGTH = 200


@dataclass
class NightlyResult:
    evaluated: int = 0
    changes: int = 0
    alerts: int = 0
    paused: int = 0
    failed: int = 0


@dataclass(frozen=True)
class Outcome:
    evaluated: bool = False
    changes: int = 0
    alerted: bool = False
    paused: bool = False


def alert_message(segment, entered, left):
    """ "Renewal risk: 3 entered, 1 left": the owner's own name, counts only."""
    return f"{segment.name[:ALERT_NAME_LENGTH]}: {entered} entered, {left} left"


def _keys(compiled, holding, pk, *, holds):
    return sorted(
        {
            key
            for (keys, _q), held in zip(compiled.conditions, holding)
            if (pk in held) == holds
            for key in keys
        }
    )


def _default_exclusions(compiled, owner, ids):
    """For organisations: which of `ids` the churned or archived default took
    out while the rules may still match them."""
    found = {}
    if compiled.kind != "customer" or not ids:
        return found
    records = visible_records("customer", owner).filter(pk__in=ids).order_by()
    for key, q in (("churned", CHURNED), ("archived", Q(is_archived=True))):
        if key in compiled.named:
            continue
        for pk in records.filter(q).values_list("pk", flat=True):
            found.setdefault(pk, []).append(key)
    return found


def change_reasons(segment, owner, entered, left, *, today):
    """Why each record moved, as field keys, never values. A record that
    entered names the top-level conditions that now hold. One that left
    names those that no longer hold, or `deleted` (it is gone), `access`
    (the owner can no longer open it), or `churned`/`archived` (the
    organisations default took it out). A pin that entered reads `pinned`.
    At most one query per condition, plus four."""
    if not entered and not left:
        return {}
    kind = segment.kind
    existing, openable = set(), set()
    if left:
        existing = set(MODELS[kind].objects.filter(pk__in=left).values_list("pk", flat=True))
        records = visible_records(kind, owner).filter(pk__in=left).order_by()
        openable = set(records.values_list("pk", flat=True))
    compiled = compile_rules(segment.rules, kind, user=owner, today=today)
    candidates = compiled.annotate(visible_records(kind, owner).filter(pk__in=entered | openable))
    holding = [
        set(candidates.filter(q).order_by().values_list("pk", flat=True))
        for _keys_of, q in compiled.conditions
    ]
    defaults = _default_exclusions(compiled, owner, openable)
    pinned = set(segment.pinned_ids or [])
    reasons = {}
    for pk in entered:
        reasons[pk] = ["pinned"] if pk in pinned else _keys(compiled, holding, pk, holds=True)
    for pk in left:
        if pk not in existing:
            reasons[pk] = ["deleted"]
        elif pk not in openable:
            reasons[pk] = ["access"]
        else:
            reasons[pk] = _keys(compiled, holding, pk, holds=False) or defaults.get(pk, [])
    return reasons


def evaluate_segment(segment_id, *, today):
    """One segment's night, under a row lock. Call inside a transaction. A
    segment deleted since the due list was read is skipped, not failed."""
    locked = Segment.objects.select_for_update(of=("self",)).select_related("owner")
    try:
        segment = locked.get(pk=segment_id)
    except Segment.DoesNotExist:
        return Outcome()
    if segment.last_evaluated_on is not None and segment.last_evaluated_on >= today:
        return Outcome()
    owner = segment.owner
    if not owner.is_active:
        if not segment.paused:
            segment.paused = True
            segment.save(update_fields=["paused"])
        return Outcome(paused=True)
    if segment.paused:
        segment.paused = False
        segment.save(update_fields=["paused"])
        rebaseline(segment, today=today)
        return Outcome(evaluated=True)

    ids = member_ids(segment, owner, today=today)
    tracked = len(ids) <= MAX_TRACKED_MEMBERS
    if segment.last_members is None or not tracked:
        # No baseline yet, or too large to track: count it, record nothing.
        segment.member_count = len(ids)
        segment.last_members = ids if tracked else None
        segment.last_evaluated_on = today
        segment.save(update_fields=["member_count", "last_members", "last_evaluated_on"])
        return Outcome(evaluated=True)

    previous, current = set(segment.last_members), set(ids)
    entered, left = current - previous, previous - current
    reasons = change_reasons(segment, owner, entered, left, today=today)
    SegmentChange.objects.bulk_create(
        [
            SegmentChange(
                segment=segment,
                record_id=pk,
                change=change,
                changed_on=today,
                reason=reasons.get(pk, []),
            )
            for change, records in (
                (SegmentChange.Change.ENTERED, entered),
                (SegmentChange.Change.LEFT, left),
            )
            for pk in sorted(records)
        ],
        ignore_conflicts=True,
        batch_size=1000,
    )
    segment.last_members, segment.member_count, segment.last_evaluated_on = ids, len(ids), today
    segment.save(update_fields=["last_members", "member_count", "last_evaluated_on"])
    alerted = bool(segment.alert_on_changes and (entered or left))
    if alerted:
        notify(
            recipient=owner,
            actor=None,
            kind=Notification.Kind.SEGMENT_CHANGES,
            message=alert_message(segment, len(entered), len(left)),
            link=f"/segments/{segment.pk}?tab=changes",
            push_on_commit=True,
        )
    return Outcome(evaluated=True, changes=len(entered) + len(left), alerted=alerted)


def evaluate_nightly(organisation=None, *, today=None):
    """Every segment due today (not yet evaluated on `today`), in one
    workspace or all of them."""
    today = today or timezone.localdate()
    result = NightlyResult()
    due = Segment.objects.filter(Q(last_evaluated_on__isnull=True) | Q(last_evaluated_on__lt=today))
    if organisation is not None:
        due = due.filter(organisation=organisation)
    for segment_id in list(due.order_by("pk").values_list("pk", flat=True)):
        try:
            with transaction.atomic():
                outcome = evaluate_segment(segment_id, today=today)
        except Exception:  # noqa: BLE001 - one segment's failure never stops the rest
            logger.exception("Segment %s: nightly evaluation failed", segment_id)
            result.failed += 1
            continue
        result.evaluated += outcome.evaluated
        result.changes += outcome.changes
        result.alerts += outcome.alerted
        result.paused += outcome.paused
    return result
