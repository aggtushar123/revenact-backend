"""Bulk edits from the Pipelines selection bar — set stage, priority,
department or date — applied one item at a time with exactly the rules the
single PATCH follows: the item is re-read under a lock through the editor's
own twice filter (`book.scope`, the detail endpoints' queryset and the
list's), and the kind's own serializer validates and saves the change, so a
stage change moves the stage clock (`StageClockMixin.save`) just as a Board
drag does; nothing here moves a stage with `QuerySet.update`.

An id the editor cannot read — another tenant's, on an organisation or
account they may not open, or in a department they may not read — reads
"Not found.", the same answer as one that does not exist. The machinery
(per-id transaction, lock, failures by id) is `services.portfolio_core.bulk`,
shared with Accounts. The batch is audited once by the view
(`pipelines.bulk_updated`); its items are not also audited one by one."""

import logging

from services.portfolio_core.bulk import apply_each, update_one

from .book import scope

logger = logging.getLogger(__name__)


def field_for(kind, action):
    """Each action is one writable field of the ordinary single edit."""
    return {
        "set_stage": "stage",
        "set_priority": "priority",
        "set_department": "department",
        "set_date": kind.date_field,
    }[action]


def _apply_one(request, kind, item_id, field, value):
    """Returns None when the id was updated, else the reason it was not."""
    return update_one(
        request,
        model=kind.model,
        # SOC2:AUTH-02 each item is re-read through the editor's own twice
        # filter, exactly as the single PATCH and the list read it
        visible=scope(request.user, kind).filter(pk=item_id),
        serializer_class=kind.serializer,
        field=field,
        value=value,
    )


def apply(request, kind, *, ids, action, value, result=None):
    """`result`, when given, is filled in place as each id finishes, so a
    caller still knows what was done if something unexpected escapes."""
    field = field_for(kind, action)
    return apply_each(
        ids,
        # Looked up per call, so a test can stand in for `_apply_one`.
        lambda item_id: _apply_one(request, kind, item_id, field, value),
        logger=logger,
        result=result,
    )
