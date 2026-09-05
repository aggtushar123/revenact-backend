"""Grounds Copilot's answers in the caller's own real data — not RAG, not
tool-calling: one compact text digest built fresh per request and dropped
into the system prompt (see anthropic_client.get_completion's own caller,
SendMessageView), the same numbers ChatView.tsx's old mock used to
fabricate, now genuinely queried. The model can read this and talk about
it; it can't run its own queries or take real actions — that's a
meaningfully bigger scope (real tool-calling), deliberately deferred.

Aggregates in Python over the caller's own Customer rows, same reasoning
as CustomerStatsView's own docstring: health_category is a derived Python
property, not a real column to GROUP BY, and this is fine at the scale of
one tenant's own customer list. Money is converted into the org's own
currency via services.fx_rates.conversion.convert_to_org_currency before
being summed, same "don't silently mix currencies" discipline as
CustomerStatsView; a customer whose currency has no configured rate is
still counted but excluded from the ARR total."""

from django.db.models import Q

from services.customers.models import Customer, Opportunity, Risk, Ticket
from services.fx_rates.conversion import convert_to_org_currency


def build_org_context_summary(organisation) -> str:
    customers = Customer.objects.filter(organisation=organisation, is_archived=False)

    total = customers.count()
    if total == 0:
        return "This organisation has no customers on record yet."

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

    avg_health = sum(c.health_score for c in customers) / total
    scored = [c.nps_score for c in customers if c.nps_score is not None]
    avg_nps = sum(scored) / len(scored) if scored else None

    org_scope = Q(customer__organisation=organisation) | Q(
        account__customers__organisation=organisation
    )
    open_opportunities = Opportunity.objects.filter(org_scope).exclude(
        stage=Opportunity.Stage.CLOSED_WON
    )
    open_risks = Risk.objects.filter(org_scope).exclude(stage=Risk.Stage.ABANDONED)
    open_tickets = Ticket.objects.filter(org_scope).exclude(
        status__in=[Ticket.Status.RESOLVED, Ticket.Status.CLOSED]
    )

    lines = [
        f"Customers: {total} total, average health score {avg_health:.1f}/10"
        + (f", average NPS {avg_nps:.0f}" if avg_nps is not None else ", no NPS scores recorded"),
        "Lifecycle stages: "
        + ", ".join(f"{stage} ({count})" for stage, count in sorted(lifecycle_counts.items())),
        f"Total ARR (in {organisation.currency}): {arr_total:,.2f}",
        "Top at-risk customers (lowest health score first):",
    ]
    for c in top_at_risk:
        nps = c.nps_score if c.nps_score is not None else "n/a"
        lines.append(
            f"  - {c.name}: health {c.health_score}/10, NPS {nps}, stage {c.lifecycle_stage}"
        )

    lines.append(
        f"Pipeline: {open_opportunities.count()} open opportunities, "
        f"{open_risks.count()} open risks, {open_tickets.count()} open tickets."
    )

    return "\n".join(lines)
