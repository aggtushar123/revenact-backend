"""Real retrieval over the caller's own real communication history — the
first step past pure aggregate stats (health score, NPS, ARR) towards
Copilot actually quoting real content. No vector DB/embeddings yet:
"relevant" here means "this company was named in the question" or "this
is one of your own top at-risk companies" — a real, simple heuristic, not
semantic search. That's the deliberate next step once this needs to scale
past what a plain filter+order_by can rank well (see this module's own
docstring reasoning mirrored in context.py's own).

Pulls from every real, already-real-for-other-features source that
actually carries free-text content — Email (subject+body), Note
(title+body) — plus Ticket (title only; the model has no body field) and
Activity (a categorical type label only, no free text) for real-but-
structured signal. Nothing here is itself new data: every row already
exists for the Activity Feed's own Email/Notes/Tickets/Activities tabs.
"""

from services.customers.models import Activity, Customer, Email, Note, Ticket

MAX_SNIPPET_LENGTH = 240


def _snippet(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= MAX_SNIPPET_LENGTH:
        return collapsed
    return collapsed[:MAX_SNIPPET_LENGTH].rstrip() + "…"


def _scope_kwargs(company) -> dict:
    return {"customer": company} if isinstance(company, Customer) else {"account": company}


def find_mentioned_company(query: str, customers, accounts):
    """The simplest possible "relevance" signal: does the question name
    one of the caller's own companies by name? Case-insensitive substring
    match — no NLP, no fuzzy matching. Customers are checked before
    Accounts so an organisation-level match wins over a same-named
    sub-account's own name being a substring of it."""

    query_lower = query.lower()
    for customer in customers:
        if customer.name.lower() in query_lower:
            return customer
    for account in accounts:
        if account.name.lower() in query_lower:
            return account
    return None


def retrieve_recent_communications(company, limit: int) -> list[str]:
    """`company` is a real owned Customer or Account (see
    find_mentioned_company/context.py's own top-at-risk list) — every
    query below is scoped to it alone, `limit` per source, most-recent
    first, same ordering each source's own model already uses elsewhere."""

    scope = _scope_kwargs(company)
    lines = []

    for email in Email.objects.filter(**scope).order_by("-sent_at")[:limit]:
        lines.append(f'Email ({email.sent_at.date()}) "{email.subject}": {_snippet(email.body)}')

    for note in Note.objects.filter(**scope).order_by("-logged_at")[:limit]:
        lines.append(f'Note ({note.logged_at}) "{note.title}": {_snippet(note.body)}')

    open_tickets = (
        Ticket.objects.filter(**scope)
        .exclude(status__in=[Ticket.Status.RESOLVED, Ticket.Status.CLOSED])
        .order_by("-opened_at")[:limit]
    )
    for ticket in open_tickets:
        priority = ticket.get_priority_display()
        lines.append(f"Open ticket {ticket.ticket_number} ({priority}): {ticket.title}")

    for activity in Activity.objects.filter(**scope).order_by("-occurred_at")[:limit]:
        lines.append(f"Activity ({activity.occurred_at}): {activity.get_type_display()}")

    return lines
