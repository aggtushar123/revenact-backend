"""The Pipelines book: the opportunities or risks the viewer may read,
narrowed by the page's filters, with what each row shows.

**Twice filtered, once, here.** An item exists only if the viewer may open
its organisation or account (`visible_children_q`) and may read it by
department (`pipeline_visible_q`) — the single-item endpoints' own rule.
Every filter narrows that scope; none widens it. An organisation or account
filter the viewer cannot open narrows to nothing.

**Stage last, in Python.** The query carries every other filter. The rows
keep the chosen stages (the open ones by default), while the summary reads
every stage of the same set (`PipelineBook.entries`), so "Won this quarter"
and the stage strip survive the default open-only view.

**Owner names stay in the tenant.** The owner of an item's organisation or
account is named only when that person is in the viewer's own organisation;
anyone else (a bad import) is `OUTSIDE_OWNER`, "Not in your book" — stricter
than the Accounts portfolio, on purpose.

**Money.** `mrr` is in the workspace's currency, as the Pipelines page and
the Deals & risks tabs have always shown it. No FX.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from types import SimpleNamespace

from django.db.models import Q

from services.accounts.models import User
from services.accounts_portfolio.book import linked_organisations
from services.customers.scoping import (
    pipeline_visible_q,
    visible_accounts,
    visible_children_q,
    visible_customers,
)
from services.customers.serializers import department_label

from .kinds import Kind
from .params import NO_DEPARTMENT, OUTSIDE, UNASSIGNED, PipelineParams

#: Who a row names as its owner when the parent's owner is outside the
#: viewer's organisation: no id, no name of theirs. It serialises as
#: `{"id": null, "name": "Not in your book"}`; `key` is its bucket in the
#: owner filter and grouping ("outside"), distinct from Unassigned.
OUTSIDE_OWNER = SimpleNamespace(pk=None, name="Not in your book", key=OUTSIDE)


def quarter_bounds(today):
    """The calendar quarter holding `today`, as (first day, last day)."""
    first_month = 3 * ((today.month - 1) // 3) + 1
    start = date(today.year, first_month, 1)
    if first_month == 10:
        return start, date(today.year, 12, 31)
    return start, date(today.year, first_month + 3, 1) - timedelta(days=1)


def scope(user, kind: Kind):
    """Every item of this kind the viewer may read: the detail endpoints' own
    queryset (`OpportunityDetailView`), without their `.distinct()` — both
    rules are `IN` subqueries, so no row repeats."""
    # SOC2:AUTH-02 twice filtered: the item's organisation or account must be
    # one the viewer may open, and its department one they may read
    return kind.model.objects.filter(visible_children_q(user)).filter(pipeline_visible_q(user))


def date_q(kind: Kind, value, *, today):
    """The `date` filter. A window is today to today+N inclusive (overdue
    has its own value); overdue is open by definition."""
    field = kind.date_field
    if value == "overdue":
        return Q(**{f"{field}__lt": today, "stage__in": kind.open_stages})
    if value == "none":
        return Q(**{f"{field}__isnull": True})
    return Q(**{f"{field}__gte": today, f"{field}__lte": today + timedelta(days=int(value))})


def filtered_queryset(user, kind: Kind, params: PipelineParams, *, today):
    items = scope(user, kind)

    if params.ids is not None:
        if not params.ids:
            return items.none()
        items = items.filter(pk__in=params.ids)

    if params.search:
        text = params.search
        items = items.filter(
            Q(title__icontains=text)
            | Q(customer__name__icontains=text)
            | Q(account__name__icontains=text)
        )

    if params.organisations:
        # SOC2:AUTH-02 an organisation the viewer cannot open narrows to
        # nothing; its accounts' items count only on accounts they may open
        openable = visible_customers(user).filter(pk__in=params.organisations)
        items = items.filter(
            Q(customer__in=openable)
            | Q(account__in=visible_accounts(user).filter(customers__in=openable))
        )

    if params.accounts:
        # SOC2:AUTH-02 an account the viewer cannot open narrows to nothing.
        # Defence in depth: `scope` already admits an account-level item only
        # through `visible_accounts`, so this repeats that rule at the filter.
        items = items.filter(account__in=visible_accounts(user).filter(pk__in=params.accounts))

    if params.owner == UNASSIGNED:
        items = items.filter(
            Q(customer__isnull=False, customer__owner__isnull=True)
            | Q(account__isnull=False, account__owner__isnull=True)
        )
    elif params.owner == OUTSIDE:
        # The rows that read "Not in your book": an owner, not of this tenant.
        items = items.filter(
            Q(customer__isnull=False, customer__owner__isnull=False)
            & ~Q(customer__owner__organisation_id=user.organisation_id)
            | Q(account__isnull=False, account__owner__isnull=False)
            & ~Q(account__owner__organisation_id=user.organisation_id)
        )
    elif params.owner is not None:
        # SOC2:AUTH-02 only a person of the viewer's organisation can be named:
        # another tenant's user id narrows to nothing (no linkage oracle)
        org = user.organisation_id
        items = items.filter(
            Q(customer__owner_id=params.owner, customer__owner__organisation_id=org)
            | Q(account__owner_id=params.owner, account__owner__organisation_id=org)
        )

    if params.priorities:
        items = items.filter(priority__in=params.priorities)

    if params.departments:
        wanted = Q(department__in=[d for d in params.departments if d != NO_DEPARTMENT])
        if NO_DEPARTMENT in params.departments:
            wanted |= Q(department="")
        items = items.filter(wanted)

    if params.date:
        items = items.filter(date_q(kind, params.date, today=today))

    if params.changed == "quarter":
        start, end = quarter_bounds(today)
        items = items.filter(stage_changed_at__date__gte=start, stage_changed_at__date__lte=end)

    return items


@dataclass
class PipelineEntry:
    item: object
    #: `mrr` as stored: the workspace's currency.
    mrr: float
    #: The kind's date (expected close / due by), or None.
    when: date | None
    #: Days from today to `when`; negative once it has passed.
    days: int | None
    is_open: bool
    #: Open, with its date in the past.
    overdue: bool
    #: The owner of the item's organisation or account (spec: no owner field),
    #: `OUTSIDE_OWNER` when they are not in the viewer's organisation.
    owner: object
    #: "Part of": {"type": "organisation" | "account", "id", "name"}.
    parent: dict
    #: The parent organisations the viewer may open, lowest id first.
    organisations: list[tuple[int, str]]
    signal: dict | None


@dataclass
class PipelineBook:
    kind: Kind
    #: Every stage of the filtered set: the summary's.
    entries: list[PipelineEntry]
    #: The chosen stages: the list's and the Board's.
    rows: list[PipelineEntry]
    organisation: object


def item_signal(*, overdue, is_open, priority):
    """At most one tag: Overdue first, then High priority on an open item."""
    if overdue:
        return {"kind": "overdue", "label": "Overdue"}
    if is_open and priority == "high":
        return {"kind": "high_priority", "label": "High priority"}
    return None


def _named_owner(owner, user):
    """`owner`, if the viewer's organisation is theirs; else `OUTSIDE_OWNER`."""
    if owner is None or owner.organisation_id == user.organisation_id:
        return owner
    # SOC2:AUTH-02 another tenant's person is never named to this viewer
    return OUTSIDE_OWNER


def _entry(item, kind: Kind, *, user, today, linked):
    when = getattr(item, kind.date_field)
    days = None if when is None else (when - today).days
    is_open = item.stage in kind.open_stages
    overdue = is_open and days is not None and days < 0
    if item.customer_id is not None:
        parent = {"type": "organisation", "id": item.customer_id, "name": item.customer.name}
        owner = item.customer.owner
        # `scope` let the item in through this organisation, so it is openable.
        organisations = [(item.customer_id, item.customer.name)]
    else:
        parent = {"type": "account", "id": item.account_id, "name": item.account.name}
        owner = item.account.owner
        organisations = linked.get(item.account_id, [])
    return PipelineEntry(
        item=item,
        mrr=float(item.mrr),
        when=when,
        days=days,
        is_open=is_open,
        overdue=overdue,
        owner=_named_owner(owner, user),
        parent=parent,
        organisations=organisations,
        signal=item_signal(overdue=overdue, is_open=is_open, priority=item.priority),
    )


def load_book(user, kind: Kind, params: PipelineParams, *, today):
    """The whole filtered book in a fixed number of queries: the items (both
    parents and their owners joined), then the openable organisations of the
    account-level ones (skipped when there are none)."""
    organisation = user.organisation
    items = list(
        filtered_queryset(user, kind, params, today=today).select_related(
            "customer__owner", "account__owner"
        )
    )
    linked = linked_organisations(
        user, sorted({item.account_id for item in items if item.account_id})
    )
    entries = [_entry(item, kind, user=user, today=today, linked=linked) for item in items]
    rows = [entry for entry in entries if entry.item.stage in params.stages]
    return PipelineBook(kind=kind, entries=entries, rows=rows, organisation=organisation)


def filter_options(user, kind: Kind):
    """The filter sheet's choices, scoped exactly as the rows are: the
    organisations and accounts the viewer may open that hold one of their
    readable items (an organisation also through its accounts' items), the
    owners of those items' parents, and the departments present. Stages and
    priorities are the kind's fixed lists. Items whose parent's owner is
    outside the viewer's organisation are offered as one "Not in your book"
    option, never by name. Three queries."""
    items = scope(user, kind).order_by()
    # SOC2:AUTH-02 an organisation is offered only if the viewer may open it
    organisations = (
        visible_customers(user)
        .filter(Q(pk__in=items.values("customer_id")) | Q(accounts__in=items.values("account_id")))
        .order_by("name", "id")
        .values_list("id", "name")
        .distinct()
    )
    # SOC2:AUTH-02 an account is offered only if the viewer may open it
    accounts = (
        visible_accounts(user)
        .filter(pk__in=items.values("account_id"))
        .order_by("name", "id")
        .values_list("id", "name")
    )
    parents = items.values_list(
        "customer_id",
        "customer__owner_id",
        "customer__owner__name",
        "customer__owner__organisation_id",
        "account__owner_id",
        "account__owner__name",
        "account__owner__organisation_id",
        "department",
    ).distinct()
    owners, unassigned, outside, departments = {}, False, False, set()
    for customer_id, *owner_columns, department in parents:
        departments.add(department)
        on_account = customer_id is None
        pk, name, organisation_id = owner_columns[3:] if on_account else owner_columns[:3]
        if pk is None:
            unassigned = True
        elif organisation_id == user.organisation_id:
            owners[pk] = name
        else:
            # SOC2:AUTH-02 owners only from the viewer's own organisation: a
            # bad import never puts another tenant's name in this menu, only
            # the "Not in your book" bucket
            outside = True
    named = sorted(owners.items(), key=lambda row: (row[1] or "").casefold())
    return {
        "organisations": [{"value": str(pk), "name": name} for pk, name in organisations],
        "accounts": [{"value": str(pk), "name": name} for pk, name in accounts],
        "owners": [{"value": str(pk), "name": name} for pk, name in named]
        + ([{"value": OUTSIDE, "name": OUTSIDE_OWNER.name}] if outside else [])
        + ([{"value": UNASSIGNED, "name": "Unassigned"}] if unassigned else []),
        "stages": [{"value": value, "name": label} for value, label in kind.model.Stage.choices],
        "priorities": [
            {"value": value, "name": label} for value, label in kind.model.Priority.choices
        ],
        "departments": [
            {"value": value, "name": department_label(value)}
            for value in User.Function.values
            if value in departments
        ]
        + ([{"value": NO_DEPARTMENT, "name": "No department"}] if "" in departments else []),
    }
