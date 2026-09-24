"""The Ticket Overview's filtered set.

Shared by `/tickets/stats/` (`TicketStatsView`) and the dashboard's Ask
Revenact digest (`services.copilot.dashboard_figures`), so the assistant and
the screen can never count different tickets.
"""

from django.db.models import Q

from .interactions import _parse_date, _parse_int
from .models import Ticket
from .personal import visible_tickets
from .scoping import visible_children_q


def filtered_tickets(user, params):
    """Visibility first, then the caller's filters narrow from there.
    Applying a raw `?account=` id before the visibility gate is what
    previously let members read other owners' custom-object records — see
    CustomObjectRecordListCreateView's own note. Every filter ignores a bad
    value rather than failing."""

    tickets = visible_tickets(user, Ticket.objects.filter(visible_children_q(user)).distinct())

    opened_from = _parse_date(params.get("from"))
    if opened_from:
        tickets = tickets.filter(opened_at__gte=opened_from)
    opened_to = _parse_date(params.get("to"))
    if opened_to:
        tickets = tickets.filter(opened_at__lte=opened_to)

    priority = params.get("priority")
    if priority in Ticket.Priority.values:
        tickets = tickets.filter(priority=priority)

    owner_id = _parse_int(params.get("owner"))
    if owner_id is not None:
        tickets = tickets.filter(Q(customer__owner_id=owner_id) | Q(account__owner_id=owner_id))

    customer_id = _parse_int(params.get("customer"))
    if customer_id is not None:
        tickets = tickets.filter(Q(customer_id=customer_id) | Q(account__customers__id=customer_id))

    account_id = _parse_int(params.get("account"))
    if account_id is not None:
        tickets = tickets.filter(account_id=account_id)

    connector_id = _parse_int(params.get("connector"))
    if connector_id is not None:
        tickets = tickets.filter(connector_id=connector_id)

    return tickets.distinct()
