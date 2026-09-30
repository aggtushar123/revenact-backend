"""Which account: the first half of the twice-filter on the account page.

The account must be one the viewer may open (`visible_accounts`, the rule
every per-account endpoint applies), or the response is a 404 that does not
confirm it exists. Its story reads only rows filed on it (`account_id` = this
account): never its organisations' own records, nor a sibling account's. The
second half, each record's own rule, is the organisation story's
`Source.base`, unchanged (`services.organizations.story.sources`).
"""

from dataclasses import dataclass

from django.db.models import Q
from django.shortcuts import get_object_or_404

from services.customers.models import Account
from services.customers.scoping import visible_accounts


@dataclass(frozen=True)
class AccountScope:
    """A `services.organizations.story.scope.StoryScope` over one account."""

    account: Account

    @property
    def accounts(self) -> dict[int, str]:
        return {self.account.pk: self.account.name}

    @property
    def cursor_key(self) -> str:
        # Not the bare id: an organisation's cursor must never read here.
        return f"account:{self.account.pk}"

    def parent_q(self, account=None) -> Q:
        """Rows filed on this account. `account` is the organisation story's
        narrowing; the account page's view drops it, and any value other
        than this account reads nothing."""
        if account is None or account == self.account.pk:
            return Q(account_id=self.account.pk)
        return Q(pk__in=[])

    def account_ref(self, account_id):
        if account_id is None:
            return None
        return {"id": account_id, "name": self.accounts[account_id]}


def resolve_account_scope(user, account_id) -> AccountScope:
    # SOC2:AUTH-02 the account must be one the viewer may open, else 404 whether or not it exists
    return AccountScope(account=get_object_or_404(visible_accounts(user), pk=account_id))
