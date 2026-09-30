"""The bulk-edit machinery every portfolio selection bar shares (Accounts,
Pipelines): applied one record at a time with exactly the rules the single
edit follows.

Each id is its own transaction: the row is re-read under a lock through the
editor's own visibility, so the check judges the record as it is now, not
when the batch began, and the single edit's serializer validates and saves
the change (`save()` runs, so model-level bookkeeping such as the pipeline
stage clock happens as it would on a PATCH). One failure never stops the
rest (a database error included), and each is reported by id. An id the
editor cannot see reads "Not found." — the same answer as one that does not
exist, so the endpoint cannot be used to discover records.

`audited_batch` runs a batch and records it once, whatever happens: the ids
already saved stay saved (each is its own transaction), so the event says
which they were even if something unexpected escapes mid-batch.
"""

from django.db import DatabaseError, transaction

from core import audit
from core.models import AuditEvent

NOT_FOUND = "Not found."
NOT_UPDATED = "Could not be updated."


def first_reason(errors):
    for messages in errors.values():
        if isinstance(messages, (list, tuple)) and messages:
            return str(messages[0])
        return str(messages)
    return NOT_UPDATED


def update_one(
    request, *, model, visible, serializer_class, field, value, snapshot=None, after_save=None
):
    """Sets `field` to `value` on the one row of `visible` (the editor's own
    visibility, already narrowed to the id), through `serializer_class`.
    Returns None when it was updated, else the reason it was not.

    `snapshot(row)` is taken before the change and handed to
    `after_save(saved, snapshot)`, which runs inside the same transaction."""
    with transaction.atomic():
        # SOC2:AUTH-02 each record is read through the editor's own
        # visibility. It is a subquery because Postgres will not lock a
        # DISTINCT query, and some visibility querysets are one.
        row = (
            model.objects.select_for_update(of=("self",))
            .filter(pk__in=visible.values("pk"))
            .first()
        )
        if row is None:
            return NOT_FOUND
        before = snapshot(row) if snapshot else None
        serializer = serializer_class(
            row, data={field: value}, partial=True, context={"request": request}
        )
        if not serializer.is_valid():
            return first_reason(serializer.errors)
        saved = serializer.save()
        if after_save:
            after_save(saved, before)
    return None


def apply_each(ids, apply_one, *, logger, result=None):
    """`apply_one(id)` returns None or the reason. `result`, when given, is
    filled in place as each id finishes, so a caller still knows what was
    done if something unexpected escapes."""
    result = result if result is not None else {"updated": [], "failed": []}
    for item_id in ids:
        try:
            reason = apply_one(item_id)
        except DatabaseError as exc:
            # The id and the error's class only: the message can quote row data.
            logger.warning("Bulk edit: id %s not updated (%s)", item_id, type(exc).__name__)
            reason = NOT_UPDATED
        if reason is None:
            result["updated"].append(item_id)
        else:
            result["failed"].append({"id": item_id, "reason": reason})
    return result


def audited_batch(request, audit_action, run, *, metadata):
    """Runs `run(result)` and records `audit_action` once, in a `finally`:
    SUCCESS only when the batch finished and updated something. `metadata`
    is the caller's own (never record content); the updated and failed ids
    are added here."""
    result = {"updated": [], "failed": []}
    finished = False
    try:
        run(result)
        finished = True
    finally:
        audit.record(  # SOC2:LOG-01
            audit_action,
            request=request,
            outcome=(
                AuditEvent.Outcome.SUCCESS
                if finished and result["updated"]
                else AuditEvent.Outcome.FAILURE
            ),
            metadata={
                **metadata,
                "ids": result["updated"],
                "failed_ids": [row["id"] for row in result["failed"]],
            },
        )
    return result
