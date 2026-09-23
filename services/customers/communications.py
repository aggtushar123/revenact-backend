"""The Communications queue: the things where a person is waiting on you.

Four channels mean the same thing to whoever opens the page — somebody asked
and nobody has answered — so they are one list, ordered by how long each has
waited rather than by which model it came from:

- **email**    a customer wrote and no reply has gone back
- **question** a colleague @mentioned you and the question is still open
- **ticket**   a ticket in your department is open
- **call**     a call was logged with no summary, so nothing from it reached
               the record, the health rubric or Copilot retrieval

This module owns *what waiting means*. It owns no visibility rules of its own:
every queryset starts from the helper that already decides who may read that
model (`services.mail.visibility` for mail, `personal.visible_tickets` for
tickets, `scoping.visible_children_q` for the rest), so the queue can never
show a row the record's own page would hide.

**Why "the last message in the thread is inbound" rather than "no reply
exists".** A thread where they wrote three times and nobody answered is one
debt, not three. Excluding any email that has a *later* email of any direction
in the same thread leaves exactly the newest message per thread, and it is owed
precisely when that newest message came from them. It dedupes and it answers
the question in one predicate.

**Only mail that came through a connected mailbox counts.** Replies owed is
about your own inbox, and an email with no `mailbox_owner` (seeded, or filed by
a campaign) belongs to nobody — counting it in everybody's queue would make it
nobody's job. Those rows stay out of `mine` and appear only under `team`, where
they are at least attributable to a chain.
"""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Exists, OuterRef
from django.utils import timezone

from services.accounts.hierarchy import subtree_ids
from services.mail.visibility import visible_emails

from .models import Call, Email, Ticket
from .personal import visible_tickets
from .scoping import visible_children_q

#: The four channels, in the order the tiles show them.
KINDS = ("email", "question", "ticket", "call")

#: How many rows of one kind the queue will read before it stops counting.
#: A queue is a working list: past a couple of hundred debts of one kind the
#: number is the story, not the rows. The payload says when it has been hit
#: rather than quietly serving a prefix.
MAX_PER_KIND = 200

#: How much of a body travels with a row. Enough for the detail pane to render
#: without a second request, short enough that a page of 25 stays small.
PREVIEW_CHARS = 1200

#: Snippet shown under the subject in the queue.
SNIPPET_CHARS = 140


def _scope_ids(user, scope):
    """Whose records `scope` means. `mine` is the person; `team` is the person
    and everyone below them, which is what the chain rules already permit."""
    if scope == "team":
        return {user.id, *subtree_ids(user)}
    return {user.id}


# --------------------------------------------------------------------------
# One queryset per channel. Each starts from that model's own visibility rule.
# --------------------------------------------------------------------------


def replies_owed(user, scope="mine"):
    """Received mail that is the newest message in its thread."""
    base = Email.objects.filter(visible_children_q(user))
    # SOC2:AUTH-02 mail stays behind its own chain rule, queue or not
    base = visible_emails(user, base).filter(
        direction=Email.Direction.RECEIVED,
        mailbox_owner__isnull=False,
    )
    base = base.filter(mailbox_owner_id__in=_scope_ids(user, scope))

    newer_in_thread = Email.objects.filter(
        thread_id=OuterRef("thread_id"),
        mailbox_owner_id=OuterRef("mailbox_owner_id"),
        sent_at__gt=OuterRef("sent_at"),
    )
    return (
        base.exclude(thread_id="")
        .exclude(Exists(newer_in_thread))
        .select_related("customer", "account", "mailbox_owner")
        .distinct()
    )


def questions_for(user, scope="mine"):
    """Open questions routed to this person (or, for `team`, to their reports)."""
    from services.knowledge.models import Question

    return (
        Question.objects.filter(
            organisation=user.organisation,
            status=Question.Status.OPEN,
            assignee_id__in=_scope_ids(user, scope),
        )
        .select_related("customer", "asked_by", "assignee")
        .distinct()
    )


def open_tickets(user, scope="mine"):
    """Unresolved tickets in the reader's department.

    `scope` does not narrow this one: a ticket carries a department, not an
    assignee, so there is no personal ticket to separate from a team one. The
    payload says so rather than pretending the two differ.
    """
    base = Ticket.objects.filter(visible_children_q(user)).exclude(
        status__in=Ticket.RESOLVED_STATUSES
    )
    # SOC2:AUTH-02 department rule, same one the Tickets tab uses
    return visible_tickets(user, base).select_related("customer", "account", "connector").distinct()


def calls_to_wrap_up(user, scope="mine"):
    """Calls logged without a summary.

    A call with no summary never reached the account record, the health rubric
    or Copilot retrieval, so the work of having had it is still outstanding.
    """
    return (
        Call.objects.filter(visible_children_q(user))
        .filter(summary="", logged_by_id__in=_scope_ids(user, scope))
        .select_related("customer", "account", "logged_by")
        .distinct()
    )


BUILDERS = {
    "email": replies_owed,
    "question": questions_for,
    "ticket": open_tickets,
    "call": calls_to_wrap_up,
}


def waiting_querysets(user, *, scope="mine", kinds=None):
    """One waiting queryset per requested channel."""
    wanted = [k for k in KINDS if k in (kinds or KINDS)] or list(KINDS)
    return {kind: BUILDERS[kind](user, scope) for kind in wanted}


# --------------------------------------------------------------------------
# Rows
# --------------------------------------------------------------------------


def _as_date(value):
    """The day something happened, whether it was stored as a date or a time."""
    return value.date() if hasattr(value, "date") else value


def _truncate(text, limit):
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _parent_of(record):
    return getattr(record, "customer", None) or getattr(record, "account", None)


def _context_for(parent):
    """The four numbers that sit above the reply box.

    A renewal question answered without the renewal date in view is the exact
    mistake this page exists to stop, so the context travels with the row
    rather than waiting for a second request.
    """
    if parent is None:
        return None

    owner = getattr(parent, "owner", None)
    renewal = getattr(parent, "renewal_date", None)
    days_to_renewal = (renewal - timezone.localdate()).days if renewal else None

    arr = getattr(parent, "arr", None)
    if arr is None:
        arr = getattr(parent, "arr_billed_at_account", None)

    return {
        "health_score": float(parent.health_score) if parent.health_score is not None else None,
        "health_category": parent.health_category,
        "arr": float(arr) if arr is not None else None,
        "renewal_date": renewal.isoformat() if renewal else None,
        "days_to_renewal": days_to_renewal,
        "owner": owner.name if owner else "",
    }


def _account_of(record):
    parent = _parent_of(record)
    if parent is None:
        return None
    is_customer = getattr(record, "customer_id", None) is not None
    return {
        "id": parent.id,
        "name": parent.name,
        "type": "customer" if is_customer else "account",
    }


def _writer_language(record) -> str:
    """The language the person who wrote this writes in, when a contact of
    theirs says so — what the reply box offers to write back in."""
    from services.customers.models import Account, Contact

    address = (
        getattr(record, "from_address", "") or getattr(record, "requester_email", "") or ""
    ).strip()
    company = record.customer or record.account
    if not address or company is None:
        return ""
    where = {"account": company} if isinstance(company, Account) else {"customer": company}
    contact = Contact.objects.filter(email__iexact=address, **where).exclude(language="").first()
    return contact.language if contact else ""


def _email_row(record, today):
    when = _as_date(record.sent_at)
    return {
        "kind": "email",
        "writer_language": _writer_language(record),
        "who": record.sender_name or record.from_address or "Unknown sender",
        "detail": "",
        "subject": record.subject,
        "snippet": _truncate(record.body, SNIPPET_CHARS),
        "preview": _truncate(record.body, PREVIEW_CHARS),
        "sentiment": record.sentiment,
        "waiting_since": when.isoformat(),
        "waiting_days": (today - when).days,
        "account": _account_of(record),
        "context": _context_for(_parent_of(record)),
        "action": "reply",
        "external_url": "",
    }


def _question_row(record, today):
    when = _as_date(record.created_at)
    return {
        "kind": "question",
        "who": record.asked_by.name,
        "detail": record.asked_by.get_function_display(),
        "subject": _truncate(record.text, 120),
        "snippet": "Asked you directly. Stale after three days.",
        "preview": record.text,
        "sentiment": "",
        "waiting_since": when.isoformat(),
        "waiting_days": (today - when).days,
        "account": (
            {"id": record.customer.id, "name": record.customer.name, "type": "customer"}
            if record.customer_id
            else None
        ),
        "context": _context_for(record.customer),
        "action": "answer",
        "external_url": "",
    }


def _ticket_row(record, today):
    when = _as_date(record.opened_at)
    return {
        "kind": "ticket",
        "who": record.ticket_number or "Ticket",
        "detail": record.get_priority_display(),
        "subject": record.title,
        "snippet": _truncate(record.description, SNIPPET_CHARS)
        or f"Open since {when.isoformat()}.",
        "preview": _truncate(record.description, PREVIEW_CHARS),
        "sentiment": record.sentiment,
        "waiting_since": when.isoformat(),
        "waiting_days": (today - when).days,
        "account": _account_of(record),
        "context": _context_for(_parent_of(record)),
        "action": "open_external" if record.external_url else "none",
        "external_url": record.external_url,
    }


def _call_row(record, today):
    when = _as_date(record.occurred_at)
    return {
        "kind": "call",
        "who": record.title,
        "detail": f"{record.duration_minutes} min" if record.duration_minutes else "",
        "subject": "Call has no summary yet",
        "snippet": "Nothing from this call reached the record, the health score or Copilot.",
        "preview": (
            "This call was logged without a summary. Writing one also classifies it and "
            "moves the sentiment of everyone who was on it."
        ),
        "sentiment": record.sentiment,
        "waiting_since": when.isoformat(),
        "waiting_days": (today - when).days,
        "account": _account_of(record),
        "context": _context_for(_parent_of(record)),
        "action": "summarise",
        "external_url": "",
    }


ROW_BUILDERS = {
    "email": _email_row,
    "question": _question_row,
    "ticket": _ticket_row,
    "call": _call_row,
}

#: The field each channel is ordered by — the one that means "when this
#: started waiting", never `created_at`, which is auto_now_add and would make
#: every seeded row the same age.
ORDER_FIELD = {
    "email": "sent_at",
    "question": "created_at",
    "ticket": "opened_at",
    "call": "occurred_at",
}


def rows(querysets, *, today=None, limit_per_kind=MAX_PER_KIND):
    """Merge the waiting querysets into one list, longest wait first.

    Merged in Python rather than by a SQL union: the four models share no
    columns, and each contributes at most `limit_per_kind` rows, so the merge
    reads a bounded number regardless of how much is outstanding.
    """
    today = today or timezone.localdate()
    collected = []
    truncated = False

    for kind, queryset in querysets.items():
        ordered = queryset.order_by(ORDER_FIELD[kind])[: limit_per_kind + 1]
        records = list(ordered)
        if len(records) > limit_per_kind:
            truncated = True
            records = records[:limit_per_kind]
        for record in records:
            row = ROW_BUILDERS[kind](record, today)
            row["id"] = f"{kind}:{record.pk}"
            collected.append(row)

    collected.sort(key=lambda row: (-row["waiting_days"], row["id"]))
    return collected, truncated


def counts(user, *, scope="mine"):
    """The four tile numbers, plus the worst wait, as four count queries."""
    today = timezone.localdate()
    querysets = waiting_querysets(user, scope=scope)

    per_kind = {}
    oldest = None
    for kind, queryset in querysets.items():
        per_kind[kind] = queryset.count()
        first = queryset.order_by(ORDER_FIELD[kind]).values_list(ORDER_FIELD[kind], flat=True)[:1]
        for value in first:
            days = (today - _as_date(value)).days
            oldest = days if oldest is None else max(oldest, days)

    return {
        "counts": per_kind,
        "total": sum(per_kind.values()),
        "oldest_waiting_days": oldest,
        # Said out loud rather than implied: a ticket has a department, not an
        # assignee, so narrowing to "mine" cannot change its number.
        "ticket_scope_note": (
            "Tickets are read by department, so this count is the same for you and your team."
        ),
    }


def stale_question_count(user, *, scope="mine", stale_days=3):
    """How many of the open questions have gone stale, for the tile's hint."""
    cutoff = timezone.now() - timedelta(days=stale_days)
    return questions_for(user, scope).filter(created_at__lt=cutoff).count()


def everything_rows(user, *, kinds=None, today=None):
    """The Everything tab: recent communications whether or not they are owed.

    Deliberately a different question from the queue, and answered from the
    same models so the two tabs can never disagree about what exists.
    """
    from . import interactions

    today = today or timezone.localdate()
    wanted = kinds or ("email", "call", "ticket")
    querysets = {
        name: queryset
        for name, queryset in interactions.filtered_querysets(user, _EmptyParams()).items()
        if name in wanted
    }
    collected = []
    for kind, queryset in querysets.items():
        field = interactions.SOURCES[kind]["date_field"]
        for record in queryset.select_related("customer", "account").order_by(f"-{field}")[:100]:
            when = _as_date(getattr(record, field))
            collected.append(
                {
                    "id": f"{kind}:{record.pk}",
                    "kind": kind,
                    "who": _who_for_everything(kind, record),
                    "subject": record.subject if kind == "email" else record.title,
                    "sentiment": record.sentiment,
                    "occurred_on": when.isoformat(),
                    "waiting_days": (today - when).days,
                    "account": _account_of(record),
                    "direction": getattr(record, "direction", "") or "",
                }
            )
    collected.sort(key=lambda row: row["occurred_on"], reverse=True)
    return collected


def _who_for_everything(kind, record):
    if kind == "email":
        return record.sender_name or record.from_address or ""
    if kind == "ticket":
        return record.ticket_number or ""
    return record.host_name or ""


class _EmptyParams:
    """`interactions.filtered_querysets` reads a QueryDict; the Everything tab
    wants every row it would return unfiltered."""

    def getlist(self, _key):
        return []

    def get(self, _key, default=None):
        return default


__all__ = [
    "KINDS",
    "MAX_PER_KIND",
    "counts",
    "everything_rows",
    "rows",
    "stale_question_count",
    "waiting_querysets",
]
