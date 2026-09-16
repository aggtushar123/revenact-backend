"""Pull a mailbox and file what belongs to a customer.

For every message the provider hands back, decide which side is the
counterpart (the other party, given the mailbox's own address), find
the organisation or account that party belongs to — a known contact's
address first, then the domain — and store it as that record's Email,
stamped with whose mailbox it came from and which way it went. Messages
with no counterpart in the book are ignored: a mailbox is synced, not
copied.
"""

import logging

from django.db import transaction
from django.utils import timezone

from services.customers.models import Account, Contact, Customer, Email

from .models import MailboxConnection
from .providers import get_provider
from .providers.base import Credentials, ProviderError

logger = logging.getLogger(__name__)

PUBLIC_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "yahoo.com",
    "icloud.com",
    "me.com",
    "proton.me",
    "protonmail.com",
    "aol.com",
}


def _credentials(connection):
    data = connection.get_credentials()
    return Credentials(address=connection.address, display_name=connection.display_name, data=data)


def _store(connection, creds):
    connection.address = creds.address or connection.address
    connection.set_credentials(creds.data)


def counterpart_addresses(message, own_address):
    """The addresses on the other side of `message`, and the direction."""
    own = (own_address or "").lower()
    if message.from_address == own:
        return [
            address for _, address in message.to if address and address != own
        ], Email.Direction.SENT
    return [message.from_address] if message.from_address else [], Email.Direction.RECEIVED


def match_parent(organisation, addresses):
    """(customer, account) for the first address that belongs to a contact
    or to a customer/account domain in this organisation, else (None, None)."""
    for address in addresses:
        contact = (
            Contact.objects.filter(email__iexact=address)
            .filter(
                __import__("django.db.models", fromlist=["Q"]).Q(
                    customer__organisation=organisation
                )
                | __import__("django.db.models", fromlist=["Q"]).Q(
                    account__customers__organisation=organisation
                )
            )
            .select_related("customer", "account")
            .first()
        )
        if contact is not None:
            return contact.customer, contact.account
    for address in addresses:
        domain = address.rsplit("@", 1)[-1].lower() if "@" in address else ""
        if not domain or domain in PUBLIC_DOMAINS:
            continue
        account = (
            Account.objects.filter(domain__iexact=domain, customers__organisation=organisation)
            .distinct()
            .first()
        )
        if account is not None:
            return None, account
        customer = Customer.objects.filter(domain__iexact=domain, organisation=organisation).first()
        if customer is not None:
            return customer, None
    return None, None


def file_message(connection, message):
    """Store one message as an Email if it belongs to someone in the book.
    Returns the Email, or None when nothing matched or it was seen already."""
    addresses, direction = counterpart_addresses(message, connection.address)
    customer, account = match_parent(connection.organisation, addresses)
    if customer is None and account is None:
        return None
    if (
        message.provider_id
        and Email.objects.filter(
            mailbox=connection, provider_message_id=message.provider_id
        ).exists()
    ):
        return None
    counterpart_name = (
        next((name for name, address in message.to if address in addresses and name), "")
        if direction == Email.Direction.SENT
        else message.from_name
    )
    own_name = connection.display_name or connection.user.name
    return Email.objects.create(
        customer=customer,
        account=account,
        subject=message.subject[:255],
        sender_name=(
            own_name
            if direction == Email.Direction.SENT
            else counterpart_name or message.from_address
        )[:150],
        recipient_name=(
            counterpart_name or (addresses[0] if addresses else "")
            if direction == Email.Direction.SENT
            else own_name
        )[:150],
        body=message.body,
        sent_at=message.date,
        mailbox=connection,
        mailbox_owner=connection.user,
        direction=direction,
        from_address=message.from_address[:254],
        to_addresses=[address for _, address in message.to][:50],
        thread_id=message.thread_id[:255],
        provider_message_id=message.provider_id[:255],
        synced_at=timezone.now(),
    )


def sync_mailbox(connection):
    """One pass for one mailbox. Returns how many emails were filed. Marks
    the connection when the provider needs the person to reconnect."""
    provider = get_provider(connection.provider)
    try:
        creds = _credentials(connection)
        messages, cursor, creds = provider.fetch_messages(creds, connection.sync_cursor)
    except ProviderError as exc:
        connection.status = MailboxConnection.Status.ERROR
        connection.error = str(exc)[:255]
        connection.save(update_fields=["status", "error", "updated_at"])
        logger.warning("mailbox %s: %s", connection.address, exc)
        return 0
    filed = []
    with transaction.atomic():
        for message in sorted(messages, key=lambda m: m.date):
            email = file_message(connection, message)
            if email is not None:
                filed.append(email)
        _store(connection, creds)
        connection.sync_cursor = cursor[:512]
        connection.status = MailboxConnection.Status.CONNECTED
        connection.error = ""
        connection.last_synced_at = timezone.now()
        connection.save()
    # Sentiment now, not at the next scheduled pass: the pulse counts only
    # classified conversations (services/customers/pulse.py).
    if filed:
        from services.customers.classification import classify_records

        classify_records(filed, organisation=connection.organisation, user=connection.user)
    return len(filed)


def send_email(connection, *, to, subject, body, customer=None, account=None):
    """Send through the person's own mailbox and file the copy."""
    provider = get_provider(connection.provider)
    creds = _credentials(connection)
    message = provider.send(creds, to=to, subject=subject, body=body)
    _store(connection, creds)
    connection.save(update_fields=["credentials", "address", "updated_at"])
    email = Email.objects.create(
        customer=customer,
        account=account,
        subject=subject[:255],
        sender_name=(connection.display_name or connection.user.name)[:150],
        recipient_name=", ".join(to)[:150],
        body=body,
        sent_at=message.date,
        mailbox=connection,
        mailbox_owner=connection.user,
        direction=Email.Direction.SENT,
        from_address=connection.address[:254],
        to_addresses=[a.lower() for a in to][:50],
        thread_id=message.thread_id[:255],
        provider_message_id=message.provider_id[:255],
        synced_at=timezone.now(),
    )
    from services.customers.classification import classify_records

    classify_records([email], organisation=connection.organisation, user=connection.user)
    email.refresh_from_db()
    return email
