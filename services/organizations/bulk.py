"""Bulk edits from the Organizations selection bar, applied one organization
at a time with exactly the rules a single edit follows: the record must be
visible to the editor, `CustomerSerializer` validates the change (an owner in
the same organisation; a reassign only by the current owner, their chain or a
settings manager), and an owner change is handed over and notified.

Each id is its own transaction: the row is re-read under a lock, so the
permission check judges the owner it has now, not when the batch began.
One failure never stops the rest (a database error included), and each is
reported by id. An id the editor
cannot see reads "Not found." — the same answer as one that does not exist,
so the endpoint cannot be used to discover records.
"""

import logging

from django.db import DatabaseError, transaction

from services.customers.models import Customer
from services.customers.scoping import visible_customers
from services.customers.serializers import CustomerSerializer
from services.customers.views import after_customer_update

#: Each action is one writable field of the ordinary customer update.
FIELD_FOR_ACTION = {
    "set_owner": "owner_id",
    "set_lifecycle": "lifecycle_stage",
    "archive": "is_archived",
}

NOT_FOUND = "Not found."
NOT_UPDATED = "Could not be updated."

logger = logging.getLogger(__name__)


def _reason(errors):
    for messages in errors.values():
        if isinstance(messages, (list, tuple)) and messages:
            return str(messages[0])
        return str(messages)
    return NOT_UPDATED


def _locked(user, customer_id):
    """The row as it is now, locked until this id's transaction ends, and
    only if the editor can still see it. Visibility is a subquery because
    Postgres will not lock a DISTINCT query (`visible_customers` is one)."""
    # SOC2:AUTH-02 each record is read through the editor's own visibility
    visible = visible_customers(user).filter(pk=customer_id).values("pk")
    return Customer.objects.select_for_update(of=("self",)).filter(pk__in=visible).first()


def _apply_one(request, customer_id, field, value):
    """Returns None when the id was updated, else the reason it was not."""
    with transaction.atomic():
        customer = _locked(request.user, customer_id)
        if customer is None:
            return NOT_FOUND
        previous_owner = customer.owner
        serializer = CustomerSerializer(
            customer, data={field: value}, partial=True, context={"request": request}
        )
        if not serializer.is_valid():
            return _reason(serializer.errors)
        saved = serializer.save()
        after_customer_update(saved, actor=request.user, previous_owner=previous_owner)
    return None


def apply(request, *, ids, action, value, result=None):
    """`result`, when given, is filled in place as each id finishes, so a
    caller still knows what was done if something unexpected escapes."""
    field = FIELD_FOR_ACTION[action]
    result = result if result is not None else {"updated": [], "failed": []}
    for customer_id in ids:
        try:
            reason = _apply_one(request, customer_id, field, value)
        except DatabaseError as exc:
            # The id and the error's class only: the message can quote row data.
            logger.warning(
                "Organizations bulk: id %s not updated (%s)", customer_id, type(exc).__name__
            )
            reason = NOT_UPDATED
        if reason is None:
            result["updated"].append(customer_id)
        else:
            result["failed"].append({"id": customer_id, "reason": reason})
    return result
