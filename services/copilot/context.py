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
fallback second — e.g. "that food delivery account" still finding
Pizza Hut — no vector DB, a documented real limitation for company
names that are also common words). Once identified, that company's own
recent real Emails/Notes/open Tickets/Activities are retrieved,
relevance-ranked against the question; otherwise a smaller slice for
each of the top few at-risk companies keeps the digest from being pure
numbers even with none identified."""

from django.db.models import Q

from services.customers.models import Account, Customer, Opportunity, Risk, Ticket
from services.fx_rates.conversion import convert_to_org_currency

from .retrieval import (
    find_mentioned_company,
    find_relevant_company_semantic,
    retrieve_recent_communications,
)


def build_org_context_summary(organisation, user, query: str = "") -> str:
    customers = Customer.objects.filter(organisation=organisation, owner=user, is_archived=False)
    # `.distinct()` — same fan-out reasoning as AccountListView's own.
    accounts = Account.objects.filter(customers__organisation=organisation, owner=user).distinct()

    customer_total = customers.count()
    account_total = accounts.count()
    if customer_total == 0 and account_total == 0:
        return "You don't own any customers or accounts yet — nothing to summarize."

    lines = []
    top_at_risk: list[Customer] = []

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
    else:
        lines.append("You don't own any customers directly — only sub-accounts (see below).")

    if account_total:
        avg_account_health = sum(a.health_score for a in accounts) / account_total
        lines.append(
            f"Your accounts: {account_total} total, "
            f"average health score {avg_account_health:.1f}/10"
        )

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
    mentioned = find_mentioned_company(query, customers, accounts) if query else None
    match_label = "named in the question"
    if mentioned is None and query:
        mentioned = find_relevant_company_semantic(query, customers, accounts)
        match_label = "the account your question seems to be about"

    if mentioned is not None:
        comms = retrieve_recent_communications(mentioned, limit=6, query=query)
        if comms:
            lines.append(f"Recent real communications for {mentioned.name} ({match_label}):")
            lines.extend(f"  - {line}" for line in comms)
    else:
        for company in top_at_risk[:3]:
            comms = retrieve_recent_communications(company, limit=2, query=query)
            if comms:
                lines.append(f"Recent real communications for {company.name}:")
                lines.extend(f"  - {line}" for line in comms)

    return "\n".join(lines)
