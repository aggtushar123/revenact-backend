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
records. The machinery is `services.portfolio_core.bulk`, shared with
Pipelines.
"""

import logging

from services.customers.models import Account
from services.customers.scoping import visible_accounts
from services.customers.serializers import AccountSerializer
from services.customers.views import after_account_update
from services.portfolio_core.bulk import apply_each, update_one

#: Each action is one writable field of the ordinary account update.
FIELD_FOR_ACTION = {
    "set_owner": "owner_id",
    "set_lifecycle": "lifecycle_stage",
}

logger = logging.getLogger(__name__)


def _apply_one(request, account_id, field, value):
    """Returns None when the id was updated, else the reason it was not."""
    return update_one(
        request,
        model=Account,
        # SOC2:AUTH-02 each record is read through the editor's own visibility
        visible=visible_accounts(request.user).filter(pk=account_id),
        serializer_class=AccountSerializer,
        field=field,
        value=value,
        snapshot=lambda account: account.owner,
        after_save=lambda saved, previous_owner: after_account_update(
            saved, actor=request.user, previous_owner=previous_owner
        ),
    )


def apply(request, *, ids, action, value, result=None):
    """`result`, when given, is filled in place as each id finishes, so a
    caller still knows what was done if something unexpected escapes."""
    field = FIELD_FOR_ACTION[action]
    return apply_each(
        ids,
        lambda account_id: _apply_one(request, account_id, field, value),
        logger=logger,
        result=result,
    )
