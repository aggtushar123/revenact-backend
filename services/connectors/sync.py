"""Pull a ticket source and file what belongs to a customer.

For every ticket the provider hands back: if it is already here (same
connector, same external id) update what changed — status, priority,
assignee, resolution; otherwise find the organisation or account its
requester belongs to (a known contact's address first, then the domain,
the same lookup mail uses) and create it there, stamped with the
connector's department. A ticket whose requester matches nothing — and
whose connector is not scoped to exactly one company — is counted and
skipped: a source is synced, not copied.
"""

import logging

from django.db import transaction
from django.utils import timezone

from services.customers.models import Ticket

from .models import Connector
from .providers import get_provider
from .providers.base import ProviderError

logger = logging.getLogger(__name__)


def _parent_for(connector, remote):
    """(customer, account) for one remote ticket, or (None, None)."""
    from services.mail.sync import match_parent

    customer = account = None
    if remote.requester_email:
        customer, account = match_parent(connector.organisation, [remote.requester_email])
    if customer is None and account is None:
        customers = list(connector.customers.all()[:2])
        accounts = list(connector.accounts.all()[:2])
        if len(customers) == 1 and not accounts:
            customer = customers[0]
        elif len(accounts) == 1 and not customers:
            account = accounts[0]
    if (customer or account) and not connector.covers(customer=customer, account=account):
        return None, None
    return customer, account


def _apply(ticket, remote):
    ticket.title = remote.title[:255] or ticket.title
    ticket.status = remote.status
    ticket.priority = remote.priority
    ticket.assignee_name = (remote.assignee_name or ticket.assignee_name or "Unassigned")[:150]
    ticket.description = remote.description
    ticket.external_url = remote.url[:500]
    ticket.requester_name = remote.requester_name[:150]
    ticket.requester_email = remote.requester_email[:254]
    if remote.opened_at:
        ticket.opened_at = remote.opened_at
    if remote.status in Ticket.RESOLVED_STATUSES:
        ticket.resolved_at = remote.resolved_at or ticket.resolved_at or timezone.now().date()
    else:
        ticket.resolved_at = None
    ticket.synced_at = timezone.now()


def file_ticket(connector, remote):
    """Create or update the Ticket for one remote ticket. Returns
    "created", "updated" or "unmatched"; the created Ticket comes back as
    the second element so the caller can classify it."""
    existing = Ticket.objects.filter(connector=connector, external_id=remote.external_id).first()
    if existing is not None:
        _apply(existing, remote)
        existing.save()
        return "updated", None
    customer, account = _parent_for(connector, remote)
    if customer is None and account is None:
        return "unmatched", None
    ticket = Ticket(
        customer=customer,
        account=account,
        connector=connector,
        department=connector.department,
        external_id=remote.external_id[:128],
        ticket_number=(remote.number or f"{remote.external_id}")[:32],
        opened_at=remote.opened_at or timezone.now().date(),
    )
    _apply(ticket, remote)
    ticket.save()
    return "created", ticket


def file_tickets(connector, remotes):
    """File a batch. Returns (summary dict, created tickets)."""
    counts = {"created": 0, "updated": 0, "unmatched": 0}
    created = []
    with transaction.atomic():
        for remote in remotes:
            outcome, ticket = file_ticket(connector, remote)
            counts[outcome] += 1
            if ticket is not None:
                created.append(ticket)
    return counts, created


def summary_line(counts):
    parts = [f"{counts['created']} new", f"{counts['updated']} updated"]
    if counts["unmatched"]:
        parts.append(f"{counts['unmatched']} without a matching account")
    return ", ".join(parts)


def classify_new(connector, tickets):
    """Sentiment now, not at the next scheduled pass: the pulse counts
    only classified conversations (services/customers/pulse.py)."""
    if not tickets:
        return
    from services.customers.classification import classify_records

    classify_records(tickets, organisation=connector.organisation)


def sync_connector(connector):
    """One pass for one connector. Returns the summary counts. Marks the
    connector when the provider needs the admin to reconnect."""
    if not connector.has_credentials or not connector.is_enabled:
        return {"created": 0, "updated": 0, "unmatched": 0}
    provider = get_provider(connector.provider)
    try:
        creds = connector.get_credentials()
        remotes, cursor, creds = provider.fetch_tickets(
            connector.config, creds, connector.sync_cursor
        )
    except (ProviderError, ValueError, KeyError) as exc:
        connector.status = Connector.Status.ERROR
        connector.error = str(exc)[:255]
        connector.save(update_fields=["status", "error"])
        logger.warning("connector %s: %s", connector, exc)
        return {"created": 0, "updated": 0, "unmatched": 0}
    counts, created = file_tickets(connector, remotes)
    connector.set_credentials(creds)
    connector.sync_cursor = (cursor or "")[:512]
    connector.status = Connector.Status.CONNECTED
    connector.error = ""
    connector.last_synced_at = timezone.now()
    connector.last_sync_note = summary_line(counts)
    connector.save()
    classify_new(connector, created)
    return counts
