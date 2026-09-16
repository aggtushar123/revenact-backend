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


def interactions_for(contact):
    """Every classified interaction that is this person's, newest first,
    as dicts {kind, record, when, sentiment}."""
    rows = []
    for call in contact.calls.exclude(ai_classified_at=None).select_related("connector"):
        rows.append(
            {"kind": "call", "record": call, "when": call.occurred_at, "sentiment": call.sentiment}
        )
    if contact.email:
        emails = Email.objects.filter(from_address__iexact=contact.email).exclude(
            ai_classified_at=None
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
        tickets = Ticket.objects.filter(requester_email__iexact=contact.email).exclude(
            ai_classified_at=None
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
    """The contacts whose sentiment these records bear on."""
    ids = set()
    addresses = set()
    for record in records:
        name = record._meta.model_name
        if name == "call":
            ids.update(record.participants.values_list("id", flat=True))
        elif name == "email" and record.from_address:
            addresses.add(record.from_address.lower())
        elif name == "ticket" and record.requester_email:
            addresses.add(record.requester_email.lower())
    query = Q(pk__in=ids)
    if addresses:
        query |= Q(email__in=addresses)
    return (
        Contact.objects.filter(query).distinct() if (ids or addresses) else Contact.objects.none()
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
            Q(customer__organisation=organisation)
            | Q(account__customers__organisation=organisation)
        ).distinct()
    now = timezone.now()
    changed = 0
    for contact in contacts.iterator():
        if recompute(contact, now) is not None:
            changed += 1
    return changed


def match_participants(parent_customer, parent_account, text, *, emails=()):
    """Contacts of this company mentioned in a transcript, by email address
    or full name, plus any explicit addresses. Used to pre-fill a logged
    call's participants."""
    from .models import Customer

    if parent_customer is not None:
        contacts = Contact.objects.filter(
            Q(customer=parent_customer) | Q(account__customers=parent_customer)
        )
    else:
        customers = Customer.objects.filter(accounts=parent_account)
        contacts = Contact.objects.filter(Q(account=parent_account) | Q(customer__in=customers))
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
