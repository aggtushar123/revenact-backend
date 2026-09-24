"""The companies behind one dashboard number.

A dashboard figure is only worth clicking if the list it opens is the
whole list. Each stats endpoint hands this module one value per customer,
computed from the same filtered set its totals use; this module turns that
into the response and does the second filter every record-derived list
here needs: an account-level record belongs to all of the account's
customers, and some of those may sit outside the viewer's book, so the
result is always intersected with ``visible_customers``.

The same fan-out means a filter naming one company (``?customer=``,
``?owner=``, ``?account=``) keeps a shared account's record, and with it
every sibling customer of that account. So the record-derived drills also
pass ``restrict_to`` (see ``filter_restriction``): the list names only the
companies the filter named.

A bad ``drill`` is ignored, like every other dashboard filter: the caller
gets the normal stats back rather than a 400.

Known limit: ``record_counts`` streams every matching row through Python
and ``companies_payload`` filters with an unbounded ``pk__in``. Fine at
current scale; revisit if one org's drill ever covers tens of thousands
of records or customers.
"""

from collections import Counter

from django.db.models import Q

from services.fx_rates.conversion import convert_to_org_currency, rates_for

from .interactions import _parse_int
from .models import Account, Customer
from .scoping import visible_customers

LIMIT = 500


def parse_segment(params, kinds):
    raw = (params.get("drill") or "").strip()
    if not raw:
        return None
    kind, sep, value = raw.partition(":")
    if kind not in kinds:
        return None
    needs_value = kinds[kind]
    if needs_value and not value:
        return None
    if not needs_value and sep:
        return None
    return kind, value


def record_counts(querysets):
    """Records per customer id. Counted by primary key, not by distinct
    (customer, account) pair, so three tickets on one company count three."""

    counts = Counter()
    by_account = Counter()
    for queryset in querysets:
        for _pk, customer_id, account_id in queryset.order_by().values_list(
            "pk", "customer_id", "account_id"
        ):
            if customer_id:
                counts[customer_id] += 1
            elif account_id:
                by_account[account_id] += 1
    if by_account:
        links = Account.customers.through.objects.filter(account_id__in=by_account)
        for account_id, customer_id in links.values_list("account_id", "customer_id"):
            counts[customer_id] += by_account[account_id]
    return counts


def filter_restriction(params, *, owner=False):
    """A Q over Customer naming the companies the caller's own filters named:
    ``?customer=`` that customer, ``?account=`` that account's customers and,
    where the endpoint filters by it, ``?owner=`` that owner's customers.
    ``None`` when no such filter is set."""

    q = Q()
    customer_id = _parse_int(params.get("customer"))
    if customer_id is not None:
        q &= Q(pk=customer_id)
    account_id = _parse_int(params.get("account"))
    if account_id is not None:
        q &= Q(accounts__id=account_id)
    owner_id = _parse_int(params.get("owner")) if owner else None
    if owner_id is not None:
        q &= Q(owner_id=owner_id)
    return q or None


def companies_payload(user, segment, values, *, value_label, none_first=False, restrict_to=None):
    organisation = user.organisation
    rates = rates_for(organisation)
    customers = Customer.objects.all()
    if restrict_to is not None:
        customers = customers.filter(restrict_to)
    customers = (
        visible_customers(user)
        .filter(pk__in=customers.filter(pk__in=list(values)).values("pk"))
        .select_related("owner")
    )

    rows = []
    for customer in customers:
        converted = convert_to_org_currency(
            customer.arr_billed_at_account, customer.currency, organisation, rates=rates
        )
        rows.append(
            {
                "id": customer.id,
                "name": customer.name,
                "owner": customer.owner.name if customer.owner else "Unassigned",
                "arr": None if converted is None else float(converted),
                "value": values[customer.pk],
            }
        )

    def order(row):
        missing = row["value"] is None
        return (not missing if none_first else missing, -(row["value"] or 0), row["name"])

    rows.sort(key=order)
    return {
        "drill": {
            "segment": segment,
            "value_label": value_label,
            "count": len(rows),
            "truncated": len(rows) > LIMIT,
            "companies": rows[:LIMIT],
        },
        "currency": organisation.currency,
    }
