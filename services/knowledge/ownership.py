"""The account owner: one accountable person per customer — and per account
— from any function.

Ownership used to mean "the CSM"; now it means the person on the hook
for the relationship, whoever they are, with the per-function owners
(services.knowledge.FunctionOwner) as the team around them. Two rules
live here so every path that changes an owner behaves the same:

- **Who may change it**: the current owner, anyone above them in the
  chart, or an organisation-settings manager. An unowned customer may be
  claimed by anyone.
- **A change is an event, not an edit**: the change and its handover note
  are written down as a contribution from the person who made it, so the
  history of who held an account and why it moved is knowledge the
  Copilot can answer from. The new owner is notified by the caller.
"""

from services.accounts.capabilities import Capability
from services.accounts.hierarchy import ancestors


class OwnershipDenied(Exception):
    """The actor may not change this customer's owner."""


def may_change_owner(actor, owned):
    """`owned` is a Customer or an Account: anything with an `owner`."""
    if owned.owner_id is None or owned.owner_id == actor.id:
        return True
    if actor.has_capability(Capability.MANAGE_ORG_SETTINGS):
        return True
    return actor.id in {a.id for a in ancestors(owned.owner)}


def record_handover(customer, actor, previous_owner, note=""):
    """Write the change down. Returns the contribution, or None when the
    owner did not actually change."""
    from .models import Contribution

    if previous_owner == customer.owner:
        return None
    was = previous_owner.name if previous_owner else "nobody"
    now = customer.owner.name if customer.owner else "nobody"
    body = f"Account owner changed from {was} to {now}."
    if note.strip():
        body += f" {note.strip()}"
    return Contribution.objects.create(
        organisation=customer.organisation,
        customer=customer,
        author=actor,
        function=actor.function,
        body=body,
    )


def record_account_handover(account, actor, previous_owner, note=""):
    """The same record for an account's owner, written on every customer
    the account belongs to (knowledge hangs off customers). Returns the
    contributions, or [] when the owner did not actually change."""
    from .models import Contribution

    if previous_owner == account.owner:
        return []
    was = previous_owner.name if previous_owner else "nobody"
    now = account.owner.name if account.owner else "nobody"
    body = f"Owner of account {account.name} changed from {was} to {now}."
    if note.strip():
        body += f" {note.strip()}"
    return [
        Contribution.objects.create(
            organisation=customer.organisation,
            customer=customer,
            author=actor,
            function=actor.function,
            body=body,
        )
        for customer in account.customers.all()
    ]
