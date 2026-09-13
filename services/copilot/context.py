"""Grounds Copilot's answers in the caller's own real data — a compact
text digest built fresh per request and dropped into the system prompt
(see anthropic_client.get_completion's own caller, SendMessageView), the
same numbers ChatView.tsx's old mock used to fabricate, now genuinely
queried, plus real retrieved communication content (see below). The
model can read this and talk about it; it can't run its own queries or
take real actions — that's a meaningfully bigger scope (real
tool-calling), deliberately deferred.

Scoped to the caller's own *owned* book of business — `owner=user` on
Customer/Account — not the whole tenant's. Copilot is a personal
assistant for whoever is logged in: their own organisation, the
Customers/Accounts *they* manage, and those companies' own open
pipeline/tickets — same "My" framing as Cockpit's own summary/task
panels (CockpitSummaryView/TaskListView's own `?mine=true`), not a
tenant-wide report a CSM would have no reason to ask their own Copilot
about.

Aggregates in Python over the caller's own rows, same reasoning as
CustomerStatsView's own docstring: health_category is a derived Python
property, not a real column to GROUP BY, and this is fine at the scale of
one CSM's own book. Money is converted into the org's own currency via
services.fx_rates.conversion.convert_to_org_currency before being summed,
same "don't silently mix currencies" discipline as CustomerStatsView; a
customer whose currency has no configured rate is still counted but
excluded from the ARR total.

Past the aggregate numbers, this also pulls real retrieved content — see
retrieval.py's own docstring for exactly what "real retrieval" means
here (an exact name match first, a real local-embeddings semantic
fallback second, embedding each company's name plus its real
hand-entered `industry` when one has been set — e.g. "that video
conferencing account" still finding Zoom once its industry is filled
in — no vector DB, a documented real limitation for a company whose
industry is still blank and whose name is also a common word). Once
identified, that company's own recent real Emails/Notes/open
Tickets/Activities are retrieved, relevance-ranked against the
question; otherwise a smaller slice for each of the top few at-risk
companies keeps the digest from being pure numbers even with none
identified."""

from dataclasses import dataclass, field

from django.db.models import Q

from services.customers.models import Account, Customer, Opportunity, Risk, Ticket
from services.fx_rates.conversion import convert_to_org_currency

from .retrieval import (
    find_mentioned_company,
    find_relevant_company_semantic,
    retrieve_with_sources,
)


@dataclass
class Grounding:
    """What a Copilot answer was built from: the digest that went into
    the prompt, and the records that digest quoted.

    The two travel together because they're the same act — the summary
    is what the model saw, `sources` is what the user gets shown as the
    citation for it. Splitting them would let an answer cite records
    that never reached the prompt."""

    summary: str
    sources: list = field(default_factory=list)
    #: The customer or account the question was found to be about, if any —
    #: so a caller can attach things (a routed question) to it.
    company: object = None


def build_org_context_summary(organisation, user, query: str = "") -> str:
    """The digest alone — the shape this had before citations existed,
    and what most callers still want. See build_grounding for the same
    digest with the cited records attached."""

    return build_grounding(organisation, user, query).summary


def _responsible_line(company) -> str:
    """Who answers for this customer in each function, so the model can
    point at a person when the summary runs out (services.knowledge)."""
    if company.__class__.__name__ != "Customer":
        return ""
    people = {
        fo.get_function_display(): fo.user.name
        for fo in company.function_owners.select_related("user")
    }
    if company.owner_id:
        people.setdefault("Customer Success", company.owner.name)
    if not people:
        return ""
    return f"Responsible for {company.name}: " + "; ".join(
        f"{function} — {name}" for function, name in people.items()
    )


def build_grounding(organisation, user, query: str = "") -> Grounding:
    # "Your customers": the ones you own or answer for in your function
    # (services.knowledge.FunctionOwner) — the account team, not only the
    # account owner.
    customers = Customer.objects.filter(
        Q(owner=user) | Q(function_owners__user=user),
        organisation=organisation,
        is_archived=False,
    ).distinct()
    # `.distinct()` — same fan-out reasoning as AccountListView's own.
    accounts = Account.objects.filter(customers__organisation=organisation, owner=user).distinct()
    # Knowledge is company-wide (see services.knowledge): the company a
    # question is about is looked up across the whole organisation, so an
    # engineer, a sales rep or the CEO — none of whom own a book — can ask
    # about any customer. The digest's own figures stay about the asker's
    # book, which is what "your customers" has always meant.
    company_customers = Customer.objects.filter(organisation=organisation, is_archived=False)
    company_accounts = Account.objects.filter(customers__organisation=organisation).distinct()

    customer_total = customers.count()
    account_total = accounts.count()

    lines = []
    top_at_risk: list[Customer] = []
    if customer_total == 0 and account_total == 0:
        lines.append(
            "You own no customers or accounts yourself; answering from what the company "
            "knows about its customers."
        )

    if customer_total:
        arr_total = 0
        lifecycle_counts: dict[str, int] = {}
        at_risk = []
        for customer in customers:
            lifecycle_counts[customer.lifecycle_stage] = (
                lifecycle_counts.get(customer.lifecycle_stage, 0) + 1
            )
            converted = convert_to_org_currency(
                customer.arr_billed_at_hq, customer.currency, organisation
            )
            if converted is not None:
                arr_total += converted
            at_risk.append(customer)

        at_risk.sort(key=lambda c: c.health_score)
        top_at_risk = at_risk[:5]

        avg_health = sum(c.health_score for c in customers) / customer_total
        scored = [c.nps_score for c in customers if c.nps_score is not None]
        avg_nps = sum(scored) / len(scored) if scored else None

        lines.append(
            f"Your customers: {customer_total} total, average health score {avg_health:.1f}/10"
            + (
                f", average NPS {avg_nps:.0f}"
                if avg_nps is not None
                else ", no NPS scores recorded"
            )
        )
        lines.append(
            "Lifecycle stages: "
            + ", ".join(f"{stage} ({count})" for stage, count in sorted(lifecycle_counts.items()))
        )
        lines.append(f"Total ARR (in {organisation.currency}): {arr_total:,.2f}")
        lines.append("Top at-risk customers (lowest health score first):")
        for c in top_at_risk:
            nps = c.nps_score if c.nps_score is not None else "n/a"
            lines.append(
                f"  - {c.name}: health {c.health_score}/10, NPS {nps}, stage {c.lifecycle_stage}"
            )
    elif account_total:
        lines.append("You don't own any customers directly — only sub-accounts (see below).")

    if account_total:
        avg_account_health = sum(a.health_score for a in accounts) / account_total
        lines.append(
            f"Your accounts: {account_total} total, "
            f"average health score {avg_account_health:.1f}/10"
        )

    # Own-book figures only make sense for someone with a book.
    if customer_total or account_total:
        my_scope = Q(customer__organisation=organisation, customer__owner=user) | Q(
            account__customers__organisation=organisation, account__owner=user
        )
        open_opportunities = Opportunity.objects.filter(my_scope).exclude(
            stage=Opportunity.Stage.CLOSED_WON
        )
        open_risks = Risk.objects.filter(my_scope).exclude(stage=Risk.Stage.ABANDONED)
        open_tickets = Ticket.objects.filter(my_scope).exclude(
            status__in=[Ticket.Status.RESOLVED, Ticket.Status.CLOSED]
        )

        lines.append(
            f"Your pipeline: {open_opportunities.count()} open opportunities, "
            f"{open_risks.count()} open risks, {open_tickets.count()} open tickets."
        )

    # Real retrieved content, not just aggregate numbers — see
    # retrieval.py's own docstring. A company identified from the
    # question (exact name match first, a real semantic fallback
    # second) gets its own fuller, relevance-ranked retrieval;
    # otherwise a smaller slice per top-at-risk company keeps the
    # digest from being pure stats even with none identified.
    mentioned = (
        find_mentioned_company(query, company_customers, company_accounts) if query else None
    )
    match_label = "named in the question"
    if mentioned is None and query:
        mentioned = find_relevant_company_semantic(query, company_customers, company_accounts)
        match_label = "the account your question seems to be about"

    # Sources are collected from exactly the items appended to the
    # digest, never gathered separately — so a citation can only ever
    # name something the model actually saw.
    sources: list[dict] = []

    if mentioned is not None:
        comms = retrieve_with_sources(mentioned, limit=6, query=query, viewer=user)
        if comms:
            lines.append(f"Recent real communications for {mentioned.name} ({match_label}):")
            lines.extend(f"  - {item.line}" for item in comms)
            sources.extend(item.source for item in comms)
        responsible = _responsible_line(mentioned)
        if responsible:
            lines.append(responsible)
    else:
        for company in top_at_risk[:3]:
            comms = retrieve_with_sources(company, limit=2, query=query, viewer=user)
            if comms:
                lines.append(f"Recent real communications for {company.name}:")
                lines.extend(f"  - {item.line}" for item in comms)
                sources.extend(item.source for item in comms)

    return Grounding("\n".join(lines), sources, mentioned)
