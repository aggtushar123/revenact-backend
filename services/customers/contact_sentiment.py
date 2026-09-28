"""How a contact actually sounds, read from what they said.

A contact's stored `sentiment` used to be a hand-set pill. Now, once a
person has any classified interaction — a call they were on, an email
from their address, a ticket they raised — the pill is computed from
those: each interaction is +1 / 0 / -1 by its sentiment, weighted by kind
(a call outweighs an email) and by how recent it is, and the weighted
average lands on positive / neutral / negative. A contact with no
evidence keeps whatever was set by hand.
"""

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from .models import Contact, Email, Ticket

KIND_WEIGHT = {"call": 1.0, "ticket": 0.8, "email": 0.6}
VALUE = {"positive": 1.0, "neutral": 0.0, "negative": -1.0}
POSITIVE_ABOVE = 0.25
NEGATIVE_BELOW = -0.25


def recency_weight(when, now):
    age = (now - when).days
    if age <= 30:
        return 1.0
    if age <= 90:
        return 0.6
    if age <= 365:
        return 0.3
    return 0.1


def _aware(value):
    if hasattr(value, "hour"):
        return value if timezone.is_aware(value) else timezone.make_aware(value)
    return timezone.make_aware(timezone.datetime(value.year, value.month, value.day))


def organisation_id_of(contact):
    """The tenant a contact belongs to: its organisation's, or its
    account's first linked organisation's (an account never spans two)."""
    if contact.customer_id:
        return contact.customer.organisation_id
    # Through `.all()`, so a prefetch of `account__customers` is used.
    customers = sorted(contact.account.customers.all(), key=lambda customer: customer.pk)
    return customers[0].organisation_id if customers else None


def in_organisation_q(organisation_id):
    """Emails and tickets under one tenant — an address is matched only
    inside the contact's own organisation, never another tenant's mail."""
    return Q(customer__organisation_id=organisation_id) | Q(
        account__customers__organisation_id=organisation_id
    )


def interactions_for(contact):
    """Every classified interaction that is this person's, newest first,
    as dicts {kind, record, when, sentiment}. A call marked not analysable
    was read and said nothing, so it is not evidence."""
    rows = []
    calls = (
        contact.calls.exclude(ai_classified_at=None)
        .exclude(not_analysable=True)
        .select_related("connector")
    )
    for call in calls:
        rows.append(
            {"kind": "call", "record": call, "when": call.occurred_at, "sentiment": call.sentiment}
        )
    organisation_id = organisation_id_of(contact) if contact.email else None
    if organisation_id is not None:
        tenant = in_organisation_q(organisation_id)
        emails = (
            Email.objects.filter(tenant, from_address__iexact=contact.email)
            .exclude(ai_classified_at=None)
            .distinct()
        )
        for email in emails:
            rows.append(
                {
                    "kind": "email",
                    "record": email,
                    "when": email.sent_at,
                    "sentiment": email.sentiment,
                }
            )
        tickets = (
            Ticket.objects.filter(tenant, requester_email__iexact=contact.email)
            .exclude(ai_classified_at=None)
            .distinct()
        )
        for ticket in tickets:
            rows.append(
                {
                    "kind": "ticket",
                    "record": ticket,
                    "when": _aware(ticket.opened_at),
                    "sentiment": ticket.sentiment,
                }
            )
    rows.sort(key=lambda r: r["when"], reverse=True)
    return rows


def score(rows, now=None):
    """(weighted score in [-1, 1], label) — label is None with no rows."""
    now = now or timezone.now()
    total = weight_sum = 0.0
    for row in rows:
        w = KIND_WEIGHT[row["kind"]] * recency_weight(row["when"], now)
        total += w * VALUE.get(row["sentiment"], 0.0)
        weight_sum += w
    if weight_sum == 0:
        return 0.0, None
    value = total / weight_sum
    if value > POSITIVE_ABOVE:
        return value, Contact.Sentiment.POSITIVE
    if value < NEGATIVE_BELOW:
        return value, Contact.Sentiment.NEGATIVE
    return value, Contact.Sentiment.NEUTRAL


def recompute(contact, now=None):
    """Recompute one contact's sentiment from their interactions. Returns
    the label, or None when there is no evidence (the hand-set value stays)."""
    now = now or timezone.now()
    rows = interactions_for(contact)
    latest = rows[0]["when"] if rows else None
    # Being on a call is contact whether or not the model has read it yet.
    latest_call = (
        contact.calls.order_by("-occurred_at").values_list("occurred_at", flat=True).first()
    )
    if latest_call and (latest is None or latest_call > latest):
        latest = latest_call
    update = []
    if latest and (contact.last_contacted_at is None or latest > contact.last_contacted_at):
        contact.last_contacted_at = latest
        update.append("last_contacted_at")
    value, label = score(rows, now)
    if label is None:
        if contact.sentiment_source == Contact.SentimentSource.COMPUTED:
            # The evidence went away (records deleted): back to hand-set.
            contact.sentiment_source = Contact.SentimentSource.MANUAL
            contact.sentiment_evidence = {}
            contact.sentiment_computed_at = None
            update += ["sentiment_source", "sentiment_evidence", "sentiment_computed_at"]
        if update:
            contact.save(update_fields=update)
        return None
    counts = {"calls": 0, "emails": 0, "tickets": 0, "positive": 0, "neutral": 0, "negative": 0}
    for row in rows:
        counts[row["kind"] + "s"] += 1
        counts[row["sentiment"] if row["sentiment"] in counts else "neutral"] += 1
    contact.sentiment = label
    contact.sentiment_source = Contact.SentimentSource.COMPUTED
    contact.sentiment_evidence = {
        "score": round(value, 3),
        **counts,
        "latest_at": latest.isoformat() if latest else None,
    }
    contact.sentiment_computed_at = now
    contact.save(
        update_fields=[
            *update,
            "sentiment",
            "sentiment_source",
            "sentiment_evidence",
            "sentiment_computed_at",
        ]
    )
    return label


def contacts_for_records(records):
    """The contacts whose sentiment these records bear on: the people on
    the calls, and the senders of the emails and tickets, matched by address
    inside each record's own tenant only. A fixed number of queries however
    many records there are."""
    from .models import Account, Customer

    call_ids = set()
    by_parent = []
    for record in records:
        name = record._meta.model_name
        if name == "call":
            call_ids.add(record.pk)
            continue
        address = record.from_address if name == "email" else record.requester_email
        if address:
            by_parent.append((record.customer_id, record.account_id, address.lower()))

    # Each record's tenant, by its customer or its account's first customer
    # (an account never spans two tenants).
    customer_ids = {c for c, _a, _addr in by_parent if c}
    account_ids = {a for c, a, _addr in by_parent if not c and a}
    tenant_of_customer = dict(
        Customer.objects.filter(pk__in=customer_ids).values_list("pk", "organisation_id")
    )
    tenant_of_account = {}
    links = Account.customers.through.objects.filter(account_id__in=account_ids)
    for account_id, organisation_id in links.order_by("customer_id").values_list(
        "account_id", "customer__organisation_id"
    ):
        tenant_of_account.setdefault(account_id, organisation_id)
    addresses = {}
    for customer_id, account_id, address in by_parent:
        tenant = (
            tenant_of_customer.get(customer_id)
            if customer_id
            else tenant_of_account.get(account_id)
        )
        if tenant is not None:
            addresses.setdefault(tenant, set()).add(address)

    query = Q(calls__in=call_ids) if call_ids else Q()
    for tenant, emails in addresses.items():
        query |= Q(email__in=emails) & in_organisation_q(tenant)
    if not query:
        return Contact.objects.none()
    return (
        Contact.objects.filter(pk__in=Contact.objects.filter(query).values("pk"))
        .select_related("customer", "account")
        .prefetch_related("account__customers")
    )


def recompute_for_records(records):
    now = timezone.now()
    count = 0
    for contact in contacts_for_records(records):
        recompute(contact, now)
        count += 1
    return count


def recompute_all(organisation=None):
    contacts = Contact.objects.all()
    if organisation is not None:
        contacts = contacts.filter(
            pk__in=Contact.objects.filter(
                Q(customer__organisation=organisation)
                | Q(account__customers__organisation=organisation)
            ).values("pk")
        )
    # Each contact's tenant is read off these, not one query per contact.
    contacts = contacts.select_related("customer", "account").prefetch_related("account__customers")
    now = timezone.now()
    changed = 0
    for contact in contacts.order_by("pk").iterator(chunk_size=500):
        if recompute(contact, now) is not None:
            changed += 1
    return changed


def match_participants(parent_customer, parent_account, text, *, viewer, emails=()):
    """Contacts of this company mentioned in a transcript, by email address
    or full name, plus any explicit addresses. Used to pre-fill a logged
    call's participants.

    Only contacts `viewer` (the person logging the call) may see: an
    organisation's own, and those on accounts they may open — a contact on
    a colleague's account under the same organisation is not theirs to
    attach (the twice-filter)."""
    from .models import Customer
    from .scoping import visible_children_q

    if parent_customer is not None:
        contacts = Contact.objects.filter(
            Q(customer=parent_customer) | Q(account__customers=parent_customer)
        )
    else:
        customers = Customer.objects.filter(accounts=parent_account)
        contacts = Contact.objects.filter(Q(account=parent_account) | Q(customer__in=customers))
    # SOC2:AUTH-02 a logged call links only contacts the logger may see
    contacts = contacts.filter(visible_children_q(viewer))
    lowered = (text or "").lower()
    wanted = {e.lower() for e in emails if e}
    matched = []
    for contact in contacts.distinct():
        email = (contact.email or "").lower()
        name = (contact.name or "").strip().lower()
        if (email and (email in wanted or email in lowered)) or (
            name and " " in name and name in lowered
        ):
            matched.append(contact)
    return matched


__all__ = ["timedelta"]
