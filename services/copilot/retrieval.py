"""Real retrieval over the caller's own real communication history — the
first step past pure aggregate stats (health score, NPS, ARR) towards
Copilot actually quoting real content.

Two passes for "which company is this about": an exact, free,
case-insensitive substring match first (find_mentioned_company), then a
real semantic fallback (find_relevant_company_semantic — see
embeddings.py) for a question that describes a company without naming
it, e.g. "that video conferencing account struggling" finding Zoom.

That fallback embeds `_company_profile_text` for each of the caller's
own companies — the name alone, unless a real `industry` has been
hand-entered (Customer.industry / Account.industry, see those models'
own help_text), in which case the industry is folded in too:
"Zoom. Industry: Video conferencing software." Real communications
content (Notes/Emails/etc.) is deliberately NOT part of this profile —
live testing found this app's own seeded CS-ops content (onboarding
notes, usage reviews) is generic and templated across every company,
carrying no real company-identity signal to embed. Name-only matching
verified live to work well for strong, well-known brand associations
(Spotify/Uber both score >0.45 against an on-the-nose description) but
weaker for company names that are also common words (Zoom scored only
0.12 against "video conferencing account" with no industry set) — a
real, honest limitation, not a guarantee every real reference gets
caught, and one that stays in force for any company whose `industry`
is still blank. Filling in `industry` is a real, optional per-company
action (Add/Edit Organization or Account form), not automatic — a
blank one costs nothing beyond the pre-existing name-only limitation.

Once a company is identified, retrieve_recent_communications pulls a
real, wider pool of its own content — Email (subject+body), Note
(title+body), open Ticket (title only; the model has no body field),
Activity (a categorical type label, no free text) — and, when there's a
query to rank against, uses the same real embeddings to return the
overall most *relevant* items across every source together, not a fixed
per-source quota of the most recent. Nothing here is itself new data:
every row already exists for the Activity Feed's own tabs."""

from typing import NamedTuple

from services.customers.models import Account, Activity, Customer, Email, Note, Ticket

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


def _effective_industry(company) -> str:
    """The real industry text for `company` — its own when set, else (for
    an Account) the first linked Customer's, the same fallback
    convention as domain/address/email/phone (see Account's own
    docstring) — just resolved here in Python rather than in the
    frontend's mapAccountToAccountRow.ts, since this runs server-side."""

    if company.industry:
        return company.industry
    if isinstance(company, Account):
        parent = company.customers.first()
        if parent is not None:
            return parent.industry
    return ""


def _company_profile_text(company) -> str:
    """What actually gets embedded for company-identification — see this
    module's own docstring for why the name alone is often not enough,
    and why real communications content isn't folded in here too."""

    industry = _effective_industry(company)
    return f"{company.name}. Industry: {industry}." if industry else company.name


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
    ranked = rank_by_similarity(query, [_company_profile_text(c) for c in companies])
    best_index, best_score = ranked[0]
    return companies[best_index] if best_score >= threshold else None


class RetrievedItem(NamedTuple):
    """One candidate record, in three forms.

    `line` is what goes in the prompt. `embed_text` is the plainer text
    relevance-ranking runs against, so formatting noise (dates, "Email
    (...)" prefixes) doesn't skew the ranking. `source` is the record's
    own identity, carried so an answer can cite what it was built from
    — until this existed, retrieval formatted records into a string and
    threw away which records they were, which made "where did that come
    from?" unanswerable."""

    line: str
    embed_text: str
    source: dict


def _source_ref(*, kind: str, record_id: int, label: str, date, company) -> dict:
    """A citation is a **snapshot**, not a foreign key.

    The record it points at may later be edited or deleted, and an
    answer given in March shouldn't silently start citing April's
    version of a note — or vanish because someone tidied up. The `id`
    is kept so the UI can still offer a link, which simply doesn't
    resolve if the record is gone."""

    is_account = company.__class__.__name__ == "Account"
    return {
        "type": kind,
        "id": record_id,
        "label": label,
        "date": str(date),
        "company": company.name,
        "company_type": "account" if is_account else "customer",
        "company_id": company.id,
    }


def _gather_candidates(company, viewer=None) -> list[RetrievedItem]:
    """Every real candidate item for `company` — see RetrievedItem.
    `viewer` scopes each source to what that person may see: mail/notes by
    their own chain-visibility rule, tickets by department, activities by
    whether they may open `company` itself (it carries no finer rule of
    its own), and contributions by the knowledge layer's own rule
    (services.accounts.hierarchy). `None` means every real caller's own
    default — always pass the asker."""

    scope = _scope_kwargs(company)
    is_account = company.__class__.__name__ == "Account"
    candidates: list[RetrievedItem] = []

    emails = Email.objects.filter(**scope)
    if viewer is not None:
        from services.mail.visibility import visible_emails

        # SOC2:AUTH-02 the Copilot reads mail under the asker's own rule
        emails = visible_emails(viewer, emails)
    for email in emails.order_by("-sent_at")[:CANDIDATE_POOL_PER_SOURCE]:
        line = f'Email ({email.sent_at.date()}) "{email.subject}": {_snippet(email.body)}'
        candidates.append(
            RetrievedItem(
                line,
                f"{email.subject} {email.body}",
                _source_ref(
                    kind="email",
                    record_id=email.id,
                    label=email.subject,
                    date=email.sent_at.date(),
                    company=company,
                ),
            )
        )

    notes = Note.objects.filter(**scope)
    if viewer is not None:
        from services.customers.personal import visible_notes

        # SOC2:AUTH-02 the Copilot reads notes under the asker's own rule
        notes = visible_notes(viewer, notes)
    for note in notes.order_by("-logged_at")[:CANDIDATE_POOL_PER_SOURCE]:
        line = f'Note ({note.logged_at}) "{note.title}": {_snippet(note.body)}'
        candidates.append(
            RetrievedItem(
                line,
                f"{note.title} {note.body}",
                _source_ref(
                    kind="note",
                    record_id=note.id,
                    label=note.title,
                    date=note.logged_at,
                    company=company,
                ),
            )
        )

    open_tickets = Ticket.objects.filter(**scope).exclude(
        status__in=[Ticket.Status.RESOLVED, Ticket.Status.CLOSED]
    )
    if viewer is not None:
        from services.customers.personal import visible_tickets

        # SOC2:AUTH-02 the Copilot reads tickets under the asker's department
        open_tickets = visible_tickets(viewer, open_tickets)
    for ticket in open_tickets.order_by("-opened_at")[:CANDIDATE_POOL_PER_SOURCE]:
        priority = ticket.get_priority_display()
        line = f"Open ticket {ticket.ticket_number} ({priority}): {ticket.title}"
        candidates.append(
            RetrievedItem(
                line,
                ticket.title,
                _source_ref(
                    kind="ticket",
                    record_id=ticket.id,
                    label=f"{ticket.ticket_number} {ticket.title}",
                    date=ticket.opened_at,
                    company=company,
                ),
            )
        )

    activities = Activity.objects.filter(**scope).order_by("-occurred_at")
    if viewer is not None:
        from services.customers.scoping import visible_accounts, visible_customers

        # SOC2:AUTH-02 an Activity carries no author or department of its
        # own — unlike Note/Ticket/Contribution above, it has no finer
        # object-level rule than the company it belongs to. Company
        # *matching* is deliberately organisation-wide (see context.py's
        # own docstring), so a company the viewer can't open can still be
        # the `company` passed in here; this is the one check standing
        # between that and its activities reaching the model.
        visible = visible_accounts(viewer) if is_account else visible_customers(viewer)
        if not visible.filter(pk=company.pk).exists():
            activities = Activity.objects.none()
    for activity in activities[:CANDIDATE_POOL_PER_SOURCE]:
        activity_type = activity.get_type_display()
        candidates.append(
            RetrievedItem(
                f"Activity ({activity.occurred_at}): {activity_type}",
                activity_type,
                _source_ref(
                    kind="activity",
                    record_id=activity.id,
                    label=activity_type,
                    date=activity.occurred_at,
                    company=company,
                ),
            )
        )

    # What the rest of the company knows — an engineer's, a sales rep's, an
    # analyst's note on this customer (services.knowledge). Organisation-
    # level only: contributions hang off the Customer. Each line names the
    # function and the person, so an answer can say who said so.
    if not is_account:
        from services.knowledge.models import Contribution

        rows = Contribution.objects.filter(customer=company)
        if viewer is not None:
            from services.knowledge.views import visible_contributions

            rows = visible_contributions(viewer, rows)
        for row in rows.select_related("author").order_by("-created_at")[
            :CANDIDATE_POOL_PER_SOURCE
        ]:
            when = row.created_at.date()
            line = f"{row.get_function_display()} ({row.author.name}, {when}): {_snippet(row.body)}"
            candidates.append(
                RetrievedItem(
                    line=line,
                    embed_text=row.body,
                    source=_source_ref(
                        kind="contribution",
                        record_id=row.id,
                        label=f"{row.get_function_display()} · {row.author.name}",
                        date=when,
                        company=company,
                    ),
                )
            )

    return candidates


def retrieve_with_sources(company, limit: int, query: str = "", viewer=None) -> list[RetrievedItem]:
    """Real content for `company`, each item still carrying the record
    it came from. With a query to rank against, this is the overall
    `limit` most *relevant* items across every source together — real
    semantic ranking (see embeddings.py), not a fixed per-source quota.
    With no query (or nothing to rank — see
    embeddings.rank_by_similarity's own empty-input handling), falls
    back to the `limit` most recent across sources in Email/Note/Ticket/
    Activity order."""

    candidates = _gather_candidates(company, viewer)
    if not candidates:
        return []

    if query:
        ranked = rank_by_similarity(query, [item.embed_text for item in candidates])
        return [candidates[i] for i, _ in ranked[:limit]]

    return candidates[:limit]


def retrieve_recent_communications(company, limit: int, query: str = "") -> list[str]:
    """The prompt lines alone — what most callers want, and the shape
    this had before citations existed. See retrieve_with_sources for the
    same items with their record identities attached."""

    return [item.line for item in retrieve_with_sources(company, limit, query)]
