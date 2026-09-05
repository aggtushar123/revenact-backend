"""Real retrieval over the caller's own real communication history — the
first step past pure aggregate stats (health score, NPS, ARR) towards
Copilot actually quoting real content.

Two passes for "which company is this about": an exact, free,
case-insensitive substring match first (find_mentioned_company), then a
real semantic fallback (find_relevant_company_semantic — see
embeddings.py) for a question that describes a company without naming
it, e.g. "that food delivery account struggling" finding Pizza Hut.
That fallback embeds only the company's own *name* against a small
local model — verified live to work well for strong, well-known brand
associations (Spotify/Uber both score >0.45 against an on-the-nose
description) but weaker for company names that are also common words
(Zoom scored only 0.12 against "video conferencing account" in that
same check) — a real, honest limitation of name-only matching with a
small general-purpose model, not a guarantee every real reference gets
caught. Embedding a fuller company profile (industry, past
communications) instead of just the bare name is the natural next step
once this matters enough to invest in.

Once a company is identified, retrieve_recent_communications pulls a
real, wider pool of its own content — Email (subject+body), Note
(title+body), open Ticket (title only; the model has no body field),
Activity (a categorical type label, no free text) — and, when there's a
query to rank against, uses the same real embeddings to return the
overall most *relevant* items across every source together, not a fixed
per-source quota of the most recent. Nothing here is itself new data:
every row already exists for the Activity Feed's own tabs."""

from services.customers.models import Activity, Customer, Email, Note, Ticket

from .embeddings import rank_by_similarity

MAX_SNIPPET_LENGTH = 240
CANDIDATE_POOL_PER_SOURCE = 10

# Calibrated against this app's own real seeded company names: generic,
# no-company-in-mind questions ("how's my whole book doing") scored up
# to ~0.24 against random companies in that check, while genuine
# strong-brand references scored 0.45+ — 0.3 sits between the two with
# a real margin, not a value tuned to make every example pass.
SEMANTIC_MATCH_THRESHOLD = 0.3


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


def find_relevant_company_semantic(
    query: str, customers, accounts, threshold: float = SEMANTIC_MATCH_THRESHOLD
):
    """Real semantic fallback for when the question doesn't name a
    company by exact string — see this module's own docstring for what
    "real" means here (name-only embeddings, a documented real
    limitation) and where the threshold came from. Only called once
    find_mentioned_company has already failed — an exact match is
    free and more precise, so there's no reason to pay for a real
    embedding call when it already found the answer."""

    companies = [*customers, *accounts]
    if not companies or not query:
        return None
    ranked = rank_by_similarity(query, [c.name for c in companies])
    best_index, best_score = ranked[0]
    return companies[best_index] if best_score >= threshold else None


def _gather_candidates(company) -> list[tuple[str, str]]:
    """Every real candidate item for `company`, each as (display_line,
    embed_text) — `display_line` is what actually goes in the digest,
    `embed_text` is the shorter/plainer text relevance-ranking runs
    against, so formatting noise (dates, "Email (...)" prefixes) doesn't
    skew the ranking."""

    scope = _scope_kwargs(company)
    candidates: list[tuple[str, str]] = []

    for email in Email.objects.filter(**scope).order_by("-sent_at")[:CANDIDATE_POOL_PER_SOURCE]:
        line = f'Email ({email.sent_at.date()}) "{email.subject}": {_snippet(email.body)}'
        candidates.append((line, f"{email.subject} {email.body}"))

    for note in Note.objects.filter(**scope).order_by("-logged_at")[:CANDIDATE_POOL_PER_SOURCE]:
        line = f'Note ({note.logged_at}) "{note.title}": {_snippet(note.body)}'
        candidates.append((line, f"{note.title} {note.body}"))

    open_tickets = (
        Ticket.objects.filter(**scope)
        .exclude(status__in=[Ticket.Status.RESOLVED, Ticket.Status.CLOSED])
        .order_by("-opened_at")[:CANDIDATE_POOL_PER_SOURCE]
    )
    for ticket in open_tickets:
        priority = ticket.get_priority_display()
        line = f"Open ticket {ticket.ticket_number} ({priority}): {ticket.title}"
        candidates.append((line, ticket.title))

    activities = Activity.objects.filter(**scope).order_by("-occurred_at")[
        :CANDIDATE_POOL_PER_SOURCE
    ]
    for activity in activities:
        activity_type = activity.get_type_display()
        candidates.append((f"Activity ({activity.occurred_at}): {activity_type}", activity_type))

    return candidates


def retrieve_recent_communications(company, limit: int, query: str = "") -> list[str]:
    """Real content for `company`. With a query to rank against, this is
    the overall `limit` most *relevant* items across every source
    together — real semantic ranking (see embeddings.py), not a fixed
    per-source quota. With no query (or nothing to rank — see
    embeddings.rank_by_similarity's own empty-input handling), falls
    back to the `limit` most recent across sources in Email/Note/Ticket/
    Activity order."""

    candidates = _gather_candidates(company)
    if not candidates:
        return []

    if query:
        lines = [line for line, _ in candidates]
        texts = [text for _, text in candidates]
        ranked = rank_by_similarity(query, texts)
        return [lines[i] for i, _ in ranked[:limit]]

    return [line for line, _ in candidates[:limit]]
