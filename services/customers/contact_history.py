"""A person's history: their calls, emails and tickets, newest first, each
under its own record rule for the viewer.

Twice filtered, like every read of record text here. First by company: a
record on an account the viewer may not open is not theirs, whichever
organisation it hangs off (`visible_children_q`). Then by the record's own
rule: mail is its mailbox owner's and their chain's (`visible_emails`), a
ticket is its department's (`visible_tickets`), a call belongs to no one
person. Mail and tickets are matched by the contact's address inside the
contact's own tenant only (`contact_sentiment.in_organisation_q`).

`sentiment` is a reading only when `analysis` is `analysed`; otherwise it
is null, and the UI says "Not enough to analyse" or waits.
"""

from services.mail.visibility import visible_emails
from services.organizations.story.items import clip, safe_url

from .contact_sentiment import in_organisation_q, organisation_id_of
from .models import Email, Ticket
from .personal import visible_tickets
from .scoping import visible_children_q, visible_customers

#: Newest first, per kind. `counts` carries the whole visible total.
HISTORY_LIMIT = 100


def _ref(row):
    return {"id": row.pk, "name": row.name} if row is not None else None


def _parents(record, visible_customer_ids):
    """(organisation, account) refs. An account's organisation is the first of
    its linked organisations the viewer may open."""
    if record.customer_id:
        return _ref(record.customer), None
    organisation = next(
        (c for c in record.account.customers.all() if c.pk in visible_customer_ids), None
    )
    return _ref(organisation), _ref(record.account)


def _classification(record):
    return {
        "area": record.get_ai_area_display() or "",
        "category": record.get_ai_category_display() or "",
        "subcategory": record.get_ai_subcategory_display() or "",
    }


def _reading(record):
    analysis = record.analysis
    return {
        "analysis": analysis,
        "sentiment": record.sentiment if analysis == "analysed" else None,
        "classification": _classification(record),
    }


def _call(call, visible_customer_ids):
    organisation, account = _parents(call, visible_customer_ids)
    return {
        "id": call.pk,
        "title": call.title,
        "occurred_at": call.occurred_at.isoformat(),
        "duration_minutes": call.duration_minutes,
        "host_name": call.host_name,
        "summary": call.summary,
        **_reading(call),
        "organisation": organisation,
        "account": account,
        "link": {"url": safe_url(call.recording_url)},
    }


def _email(email, visible_customer_ids):
    organisation, account = _parents(email, visible_customer_ids)
    return {
        "id": email.pk,
        "subject": email.subject,
        "sent_at": email.sent_at.isoformat(),
        "sender_name": email.sender_name,
        "snippet": clip(email.body),
        **_reading(email),
        "organisation": organisation,
        "account": account,
        "link": {"thread_id": email.thread_id or None},
    }


def _ticket(ticket, visible_customer_ids):
    organisation, account = _parents(ticket, visible_customer_ids)
    return {
        "id": ticket.pk,
        "ticket_number": ticket.ticket_number,
        "title": ticket.title,
        "status": ticket.status,
        "status_display": ticket.get_status_display(),
        "opened_at": ticket.opened_at.isoformat(),
        **_reading(ticket),
        "organisation": organisation,
        "account": account,
        "link": {"url": safe_url(ticket.external_url)},
    }


def history_querysets(contact, viewer):
    """The viewer's view of this person's calls, emails and tickets, each
    already under its own rule, unordered and unsliced."""
    # SOC2:AUTH-02 each record follows its own company's visibility and its own rule
    company = visible_children_q(viewer)
    calls = contact.calls.filter(company).distinct()
    emails = Email.objects.none()
    tickets = Ticket.objects.none()
    organisation_id = organisation_id_of(contact) if contact.email else None
    if organisation_id is not None:
        tenant = in_organisation_q(organisation_id)
        emails = visible_emails(
            viewer,
            Email.objects.filter(tenant, company, from_address__iexact=contact.email).distinct(),
        )
        tickets = visible_tickets(
            viewer,
            Ticket.objects.filter(
                tenant, company, requester_email__iexact=contact.email
            ).distinct(),
        )
    return calls, emails, tickets


def build_history(contact, viewer):
    calls, emails, tickets = history_querysets(contact, viewer)
    visible_customer_ids = set(visible_customers(viewer).values_list("pk", flat=True))
    parents = ("customer", "account")
    call_rows = (
        calls.select_related(*parents)
        .prefetch_related("account__customers")
        .order_by("-occurred_at", "-pk")[:HISTORY_LIMIT]
    )
    email_rows = (
        emails.select_related(*parents)
        .prefetch_related("account__customers")
        .order_by("-sent_at", "-pk")[:HISTORY_LIMIT]
    )
    ticket_rows = (
        tickets.select_related(*parents)
        .prefetch_related("account__customers")
        .order_by("-opened_at", "-pk")[:HISTORY_LIMIT]
    )
    return {
        "contact_id": contact.pk,
        "sentiment": contact.sentiment,
        "sentiment_source": contact.sentiment_source,
        "sentiment_evidence": contact.sentiment_evidence,
        "counts": {
            "calls": calls.count(),
            "emails": emails.count(),
            "tickets": tickets.count(),
        },
        "calls": [_call(row, visible_customer_ids) for row in call_rows],
        "emails": [_email(row, visible_customer_ids) for row in email_rows],
        "tickets": [_ticket(row, visible_customer_ids) for row in ticket_rows],
    }
