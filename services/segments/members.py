"""A segment's members as the kind's own list reads them.

Organisations and accounts come from the Organizations and Accounts books
with the segment's members as `scope`: the same rows, groups, sort and
keyset cursor. Contacts come from the Contacts list's own filters and
serializer, paged by a keyset cursor by name. Everything is the viewer's
own: `members_queryset` runs the rules over their book, and the books apply
visibility again.
"""

from services.accounts_portfolio import book as account_book
from services.accounts_portfolio import params as account_params
from services.accounts_portfolio import rows as account_rows
from services.accounts_portfolio import shape as account_shape
from services.accounts_portfolio.export import table as account_table
from services.customers.contact_list import filtered_contacts, parse_contact_filters
from services.customers.models import Contact
from services.customers.scoping import visible_customers
from services.customers.serializers import ContactSerializer
from services.organizations import book as organisation_book
from services.organizations import params as organisation_params
from services.organizations import rows as organisation_rows
from services.organizations import shape as organisation_shape
from services.organizations.export import table as organisation_table
from services.organizations.params import DEFAULT_LIMIT, MAX_LIMIT, int_or_none
from services.organizations.rows import person
from services.portfolio_core.shape import fingerprint, keyset_page, rank_of

from .evaluate import hidden_count, members_queryset
from .export import contacts_table
from .summary import segment_summary

#: How many members the builder's live preview lists.
PREVIEW_SIZE = 10
#: The only query parameters the members tab passes to the list code (Decision
#: 15): paging, order, grouping and search. The lists' own filters (owner,
#: health, lifecycle, ids, ...) are dropped: the members tab does not filter.
LIST_KEYS = ("sort", "group", "group_value", "search", "cursor", "limit")


def list_query(query):
    """`query` narrowed to `LIST_KEYS`."""
    return {key: query.get(key) for key in LIST_KEYS if key in query}


def _contacts(scope, viewer, query):
    """The people in `scope`, through the Contacts list's own filters and
    readable-calls annotation."""
    return filtered_contacts(viewer, parse_contact_filters(query)).filter(pk__in=scope.values("pk"))


def _contact_rows(contacts, viewer):
    # SOC2:AUTH-02 a contact names only organisations the viewer may open
    visible = set(visible_customers(viewer).values_list("pk", flat=True))
    return ContactSerializer(contacts, many=True, context={"visible_customer_ids": visible}).data


def _name_rank(entry):
    pk, name = entry
    folded = name.casefold()
    return rank_of((), folded, False, (folded, pk))


def _contacts_page(scope, viewer, query):
    contacts = _contacts(scope, viewer, query)
    filters = parse_contact_filters(query)
    entries = sorted(
        Contact.objects.filter(pk__in=contacts.values("pk")).values_list("pk", "name"),
        key=_name_rank,
    )
    limit = int_or_none(query.get("limit"))
    page, next_cursor = keyset_page(
        entries,
        rank=_name_rank,
        cursor=query.get("cursor") or "",
        limit=DEFAULT_LIMIT if limit is None or limit < 1 else min(limit, MAX_LIMIT),
        descending=False,
        fingerprint=fingerprint(
            [filters.search, filters.customer, filters.account, filters.sentiment, filters.role]
        ),
        grouped=False,
    )
    wanted = [pk for pk, _name in page]
    found = {contact.pk: contact for contact in contacts.filter(pk__in=wanted)}
    return {
        "results": _contact_rows([found[pk] for pk in wanted], viewer),
        "next_cursor": next_cursor,
        "count": len(entries),
        "groups": [],
    }


def members_listing(segment, viewer, query, *, today):
    """The members endpoint's body: the kind's own rows and paging, the tiles
    over every member the viewer may open, and how many they may not."""
    query = list_query(query)
    scope = members_queryset(segment, viewer, today=today)
    rates = None
    if segment.kind == "customer":
        params = organisation_params.parse_params(query)
        portfolio = organisation_book.load_portfolio(viewer, params, today=today, scope=scope)
        entries, groups = organisation_shape.select(portfolio, params)
        page, next_cursor = organisation_shape.paginate(entries, params=params, portfolio=portfolio)
        body = {
            "results": [organisation_rows.row_payload(entry) for entry in page],
            "next_cursor": next_cursor,
            "count": len(entries),
            "groups": groups,
        }
        rates = portfolio.rates
    elif segment.kind == "account":
        params = account_params.parse_params(query)
        portfolio = account_book.load_portfolio(viewer, params, today=today, scope=scope)
        entries, groups = account_shape.select(portfolio, params)
        page, next_cursor = account_shape.paginate(entries, params=params)
        body = {
            "results": [account_rows.row_payload(entry) for entry in page],
            "next_cursor": next_cursor,
            "count": len(entries),
            "groups": groups,
        }
    else:
        body = _contacts_page(scope, viewer, query)
    body.update(
        kind=segment.kind,
        currency=viewer.organisation.currency,
        summary=segment_summary(scope, segment, viewer, today=today, rates=rates),
        hidden_count=hidden_count(segment, viewer, today=today),
    )
    return body


def members_table(segment, viewer, query, *, today):
    """`(csv rows, member count)`: every member the viewer may open, in the
    list's order (its `search` and `sort` honoured), with the kind's own
    export columns."""
    query = list_query(query)
    scope = members_queryset(segment, viewer, today=today)
    if segment.kind == "customer":
        params = organisation_params.parse_params(query)
        portfolio = organisation_book.load_portfolio(viewer, params, today=today, scope=scope)
        entries, _groups = organisation_shape.select(portfolio, params)
        rows = [organisation_rows.row_payload(entry) for entry in entries]
        return organisation_table(rows), len(rows)
    if segment.kind == "account":
        params = account_params.parse_params(query)
        portfolio = account_book.load_portfolio(viewer, params, today=today, scope=scope)
        entries, _groups = account_shape.select(portfolio, params)
        rows = [account_rows.row_payload(entry) for entry in entries]
        return account_table(rows, currency=viewer.organisation.currency), len(rows)
    contacts = sorted(
        _contacts(scope, viewer, query), key=lambda contact: (contact.name.casefold(), contact.pk)
    )
    rows = _contact_rows(contacts, viewer)
    return contacts_table(rows), len(rows)


def _parent_of(contact):
    """A contact's own organisation or account, which the viewer may open
    because they may open the contact."""
    if contact.customer_id:
        return {"kind": "customer", "id": contact.customer_id, "name": contact.customer.name}
    return {"kind": "account", "id": contact.account_id, "name": contact.account.name}


def preview(draft, viewer, *, today):
    """The builder's live preview: how many match, the first ten by name, and
    the totals. Never the whole list."""
    scope = members_queryset(draft, viewer, today=today).order_by("name", "pk")
    if draft.kind == "contact":
        first = scope.select_related("customer", "account")[:PREVIEW_SIZE]
        results = [
            {
                "id": contact.pk,
                "name": contact.name,
                "role": contact.get_role_display(),
                "parent": _parent_of(contact),
            }
            for contact in first
        ]
    else:
        first = scope.select_related("owner")[:PREVIEW_SIZE]
        results = [
            {
                "id": record.pk,
                "name": record.name,
                "owner": person(record.owner),
                "health": {"score": float(record.health_score), "category": record.health_category},
            }
            for record in first
        ]
    summary = segment_summary(scope, draft, viewer, today=today)
    return {"kind": draft.kind, "count": summary["members"], "results": results, "summary": summary}
