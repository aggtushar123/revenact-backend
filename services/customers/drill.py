"""The companies behind one dashboard number.

A dashboard figure is only worth clicking if the list it opens is the
whole list. Each stats endpoint hands this module one value per customer,
computed from the same filtered set its totals use; this module turns that
into the response and does the second filter every record-derived list
here needs: an account-level record belongs to all of the account's
customers, and some of those may sit outside the viewer's book, so the
result is always intersected with ``visible_customers``.

A bad ``drill`` is ignored, like every other dashboard filter: the caller
gets the normal stats back rather than a 400.
"""

from collections import Counter

from services.fx_rates.conversion import convert_to_org_currency, rates_for

from .models import Account
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


def companies_payload(user, segment, values, *, value_label, none_first=False):
    organisation = user.organisation
    rates = rates_for(organisation)
    customers = visible_customers(user).filter(pk__in=list(values)).select_related("owner")

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
