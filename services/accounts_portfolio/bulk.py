"""Bulk edits from the Accounts selection bar, applied one account at a time
with exactly the rules a single edit follows: the record must be visible to
the editor, `AccountSerializer` validates the change (an active owner in the
same organisation; a reassign only by the current owner, their chain or a
settings manager), and an owner change is handed over and notified
(`after_account_update`).

Each id is its own transaction: the row is re-read under a lock, so the
permission check judges the owner it has now, not when the batch began. One
failure never stops the rest (a database error included), and each is
reported by id. An id the editor cannot see reads "Not found." — the same
answer as one that does not exist, so the endpoint cannot be used to discover
records. Organizations' bulk edit, for accounts.
"""

import logging

from django.db import DatabaseError, transaction

from services.customers.models import Account
from services.customers.scoping import visible_accounts
from services.customers.serializers import AccountSerializer
from services.customers.views import after_account_update
from services.organizations.bulk import NOT_FOUND, NOT_UPDATED, first_reason

#: Each action is one writable field of the ordinary account update.
FIELD_FOR_ACTION = {
    "set_owner": "owner_id",
    "set_lifecycle": "lifecycle_stage",
}

logger = logging.getLogger(__name__)


def _locked(user, account_id):
    """The row as it is now, locked until this id's transaction ends, and
    only if the editor can still see it. Visibility is a subquery because
    Postgres will not lock a DISTINCT query (`visible_accounts` is one)."""
    # SOC2:AUTH-02 each record is read through the editor's own visibility
    visible = visible_accounts(user).filter(pk=account_id).values("pk")
    return Account.objects.select_for_update(of=("self",)).filter(pk__in=visible).first()


def _apply_one(request, account_id, field, value):
    """Returns None when the id was updated, else the reason it was not."""
    with transaction.atomic():
        account = _locked(request.user, account_id)
        if account is None:
            return NOT_FOUND
        previous_owner = account.owner
        serializer = AccountSerializer(
            account, data={field: value}, partial=True, context={"request": request}
        )
        if not serializer.is_valid():
            return first_reason(serializer.errors)
        saved = serializer.save()
        after_account_update(saved, actor=request.user, previous_owner=previous_owner)
    return None


def apply(request, *, ids, action, value, result=None):
    """`result`, when given, is filled in place as each id finishes, so a
    caller still knows what was done if something unexpected escapes."""
    field = FIELD_FOR_ACTION[action]
    result = result if result is not None else {"updated": [], "failed": []}
    for account_id in ids:
        try:
            reason = _apply_one(request, account_id, field, value)
        except DatabaseError as exc:
            # The id and the error's class only: the message can quote row data.
            logger.warning("Accounts bulk: id %s not updated (%s)", account_id, type(exc).__name__)
            reason = NOT_UPDATED
        if reason is None:
            result["updated"].append(account_id)
        else:
            result["failed"].append({"id": account_id, "reason": reason})
    return result
