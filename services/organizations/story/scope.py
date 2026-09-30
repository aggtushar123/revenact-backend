"""Which organisation, and which of its accounts: the first half of the
twice-filter.

The organisation must be one the viewer may open (`visible_customers`), or the
response is a 404 that does not confirm it exists. Its accounts are the ones
linked to it that the viewer may open (`visible_accounts`), the rule every
per-account endpoint applies through `get_visible_account`. An account shared
with another organisation is read here, as it is on its own page; a record on
another organisation, or on an account not linked to this one, never is.
"""

from dataclasses import dataclass
from typing import Protocol

from django.db.models import Q
from django.shortcuts import get_object_or_404

from services.customers.models import Customer
from services.customers.scoping import visible_accounts, visible_customers

from .params import NO_ACCOUNT


class StoryScope(Protocol):
    """What the story engine (`build_story` and the modules it calls) reads
    from a scope. `Scope` below is an organisation and its accounts in scope;
    the account page's `services.account_story.scope.AccountScope` is one
    account. The record rules are the sources' own, never the scope's."""

    #: The accounts in scope, id -> name: `counts.by_account` lists these.
    accounts: dict[int, str]

    @property
    def cursor_key(self) -> int | str:
        """What a cursor is cut under, beside the filters."""

    def parent_q(self, account=None) -> Q:
        """The rows in scope; `account` narrows as `StoryParams.account` does."""

    def account_ref(self, account_id) -> dict | None:
        """An item's `account`: `{id, name}`, or None for the parent's own row."""


@dataclass(frozen=True)
class Scope:
    customer: Customer
    #: This organisation's accounts the viewer may open, id -> name, by name.
    accounts: dict[int, str]

    def parent_q(self, account=None) -> Q:
        """Rows filed on this organisation or on one of its accounts in scope.
        `account` narrows: an id to that account (nothing when it is not in
        scope), `NO_ACCOUNT` to the organisation's own rows."""
        if account == NO_ACCOUNT:
            return Q(customer_id=self.customer.pk)
        if account is not None:
            return Q(account_id=account) if account in self.accounts else Q(pk__in=[])
        return Q(customer_id=self.customer.pk) | Q(account_id__in=list(self.accounts))

    def account_ref(self, account_id):
        if account_id is None:
            return None
        return {"id": account_id, "name": self.accounts[account_id]}

    @property
    def cursor_key(self) -> int:
        """The organisation id, as cursors were always keyed, so a cursor
        served before the account page existed still reads."""
        return self.customer.pk


def resolve_scope(user, customer_id) -> Scope:
    # SOC2:AUTH-02 the organisation must be one the viewer may open, else 404
    customer = get_object_or_404(visible_customers(user), pk=customer_id)
    accounts = dict(
        visible_accounts(user)
        .filter(customers=customer)
        .order_by("name", "id")
        .values_list("id", "name")
    )
    return Scope(customer=customer, accounts=accounts)
