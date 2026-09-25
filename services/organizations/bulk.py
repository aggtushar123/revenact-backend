"""Bulk edits from the Organizations selection bar, applied one organization
at a time with exactly the rules a single edit follows: the record must be
visible to the editor, `CustomerSerializer` validates the change (an owner in
the same organisation; a reassign only by the current owner, their chain or a
settings manager), and an owner change is handed over and notified.

One failure never stops the rest, and each is reported by id. An id the editor
cannot see reads "Not found." — the same answer as one that does not exist,
so the endpoint cannot be used to discover records.
"""

from django.db import transaction

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


def _reason(errors):
    for messages in errors.values():
        if isinstance(messages, (list, tuple)) and messages:
            return str(messages[0])
        return str(messages)
    return "Could not be updated."


def apply(request, *, ids, action, value):
    field = FIELD_FOR_ACTION[action]
    # SOC2:AUTH-02 each record is read through the editor's own visibility
    visible = {
        customer.pk: customer
        for customer in visible_customers(request.user).filter(pk__in=ids).select_related("owner")
    }
    updated, failed = [], []
    for customer_id in ids:
        customer = visible.get(customer_id)
        if customer is None:
            failed.append({"id": customer_id, "reason": NOT_FOUND})
            continue
        previous_owner = customer.owner
        serializer = CustomerSerializer(
            customer, data={field: value}, partial=True, context={"request": request}
        )
        if not serializer.is_valid():
            failed.append({"id": customer_id, "reason": _reason(serializer.errors)})
            continue
        with transaction.atomic():
            saved = serializer.save()
            after_customer_update(saved, actor=request.user, previous_owner=previous_owner)
        updated.append(customer_id)
    return {"updated": updated, "failed": failed}
