"""The Accounts portfolio's book: the viewer's accounts, narrowed by the
page's filters, with every signal a row shows.

**Visibility first.** Everything starts from `visible_accounts(user)`; a raw
`?ids=`, `?owner=` or `?organisation=` can only narrow that, never widen it.
Whatever is counted from records (urgent tickets) is then read under that
record's own rule.

**No archive, no churn.** Accounts have neither, so unlike the Organizations
book nothing is hidden by default: the scope is every visible account, and
the Churn lifecycle stage is an ordinary stage. A parent organisation's own
archive or churn does not hide its accounts, as on `/accounts/` today.

**Reuse.** The health bands (`health_q`), the NPS bands (`NPS_Q`), the signal
(`signal_for`), the snapshot read (`snapshot_history`), the sparkline
(`health_trend`) and `triage` are Organizations' and the dashboard's own.
The account's own: the renewal rule (`organizations.book.renewing_q` also
excludes churned customers through `Customer.churn_date`, which an account
does not have), the urgent-ticket count (an account's own tickets, not a
company's fan-out), ARR as stored (see `AccountStatsView`: an account's `arr`
is in the workspace's currency), and the linked organisations.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import timedelta

from django.db.models import CharField, Count, Q
from django.db.models.functions import Cast

from services.attention.rules import SUPPORT_PRIORITIES
from services.customers.contact import last_account_contact_annotation
from services.customers.models import Account, Customer, Ticket, health_category_for
from services.customers.personal import visible_tickets
from services.customers.scoping import visible_accounts, visible_customers
from services.customers.triage import Triage, triage
from services.customers.views import CustomerHealthView
from services.organizations.book import (
    NPS_Q,
    health_q,
    health_trend,
    signal_for,
    snapshot_history,
)

from .params import AccountPortfolioParams


def account_renewing_q(days, *, today):
    """Renews within `days`, overdue included: the `renews_within` filter's
    rule, and (over `renewal_days`, in `shape.build_summary`) the Renewing
    tile's, so clicking the tile lists exactly the N it showed."""
    return Q(renewal_date__isnull=False, renewal_date__lte=today + timedelta(days=days))


def filtered_queryset(user, params: AccountPortfolioParams, *, today, scope=None):
    """`scope` is a saved segment's members (services.segments): it can only
    narrow the visible book."""
    # SOC2:AUTH-02 record visibility comes first; filters only narrow it
    accounts = visible_accounts(user)
    if scope is not None:
        accounts = accounts.filter(pk__in=scope.values("pk"))

    if params.ids is not None:
        if not params.ids:
            return accounts.none()
        accounts = accounts.filter(pk__in=params.ids)

    if params.search:
        accounts = accounts.annotate(id_as_text=Cast("id", CharField())).filter(
            Q(name__icontains=params.search) | Q(id_as_text__icontains=params.search)
        )

    if params.organisations:
        # SOC2:AUTH-02 an organisation the viewer cannot open narrows to
        # nothing, so the filter cannot reveal which accounts it holds
        openable = visible_customers(user).filter(pk__in=params.organisations)
        accounts = accounts.filter(customers__in=openable)

    if params.owner == "unassigned":
        accounts = accounts.filter(owner__isnull=True)
    elif params.owner is not None:
        accounts = accounts.filter(owner_id=params.owner)

    if params.lifecycles:
        accounts = accounts.filter(lifecycle_stage__in=params.lifecycles)

    if params.health:
        bands = Q()
        for category in params.health:
            bands |= health_q(category)
        accounts = accounts.filter(bands)

    if params.renews_within is not None:
        accounts = accounts.filter(account_renewing_q(params.renews_within, today=today))

    if params.nps:
        accounts = accounts.filter(NPS_Q[params.nps])

    return accounts


@dataclass
class AccountEntry:
    account: Account
    #: `Account.arr` as stored: already in the workspace's currency.
    arr: float
    trend: list[float]
    triage: Triage
    last_touch_days: int | None
    renewal_days: int | None
    urgent_tickets: int
    signal: dict | None
    #: The linked organisations the viewer may open, lowest id first, as
    #: `(id, name)`: the row names the first and counts the rest.
    organisations: list[tuple[int, str]]


@dataclass
class AccountPortfolio:
    entries: list[AccountEntry]
    organisation: object


def account_urgent_tickets(user, ids):
    """Open High/Critical tickets filed on these accounts — the attention
    list's support priorities, read under the department rule. Only the
    accounts' own tickets: one filed on an organisation belongs to the
    organisation. A row's urgent count and an Ask reply's ticket snapshot
    (`copilot.accounts_grounding`) both read this one queryset."""
    # SOC2:AUTH-02 tickets are read department-wise; `ids` are visible accounts
    return (
        visible_tickets(user, Ticket.objects.filter(account_id__in=ids))
        .filter(priority__in=SUPPORT_PRIORITIES)
        .exclude(status__in=Ticket.RESOLVED_STATUSES)
    )


def account_urgent_ticket_counts(user, ids):
    """`account_urgent_tickets`, counted per account. One query."""
    if not ids:
        return Counter()
    rows = account_urgent_tickets(user, ids).order_by().values("account_id").annotate(n=Count("id"))
    return Counter({row["account_id"]: row["n"] for row in rows})


def linked_organisations(user, ids):
    """Each account's linked organisations the viewer may open, lowest id
    first, as `(id, name)` — one query through the M2M's own table. One the
    viewer cannot open is left out, so neither the row's name, its "+N" nor
    the Profile panel discloses it."""
    linked = defaultdict(list)
    if not ids:
        return linked
    # SOC2:AUTH-02 only organisations the viewer may open are named
    rows = (
        Account.customers.through.objects.filter(
            account_id__in=ids, customer__in=visible_customers(user)
        )
        .order_by("customer_id")
        .values_list("account_id", "customer_id", "customer__name")
    )
    for account_id, customer_id, name in rows:
        linked[account_id].append((customer_id, name))
    return linked


def _entry(account, *, today, snapshots, urgent, organisations):
    # CustomerHealthRowSerializer._triage's own call, over the same window.
    result = triage(
        health_category=account.health_category,
        csm_pulse=account.csm_pulse_score,
        ai_pulse=account.ai_pulse_value,
        renewal_date=account.renewal_date,
        history=[health_category_for(score) for _captured_on, score in snapshots],
        today=today,
    )
    last_touch = account._last_touch_on
    renewal_days = None if account.renewal_date is None else (account.renewal_date - today).days
    return AccountEntry(
        account=account,
        arr=float(account.arr),
        trend=health_trend(snapshots, account.health_score, today=today),
        triage=result,
        last_touch_days=None if last_touch is None else (today - last_touch).days,
        renewal_days=renewal_days,
        urgent_tickets=urgent,
        # Accounts have no churn, so nothing mutes the signal.
        signal=signal_for(
            churned=False, renewal_days=renewal_days, risk=result.score, urgent_tickets=urgent
        ),
        organisations=organisations,
    )


def load_portfolio(user, params: AccountPortfolioParams, *, today, scope=None):
    """The whole filtered book with its signals, in a fixed number of
    queries: the accounts (last-touch subquery, owner joined), their
    snapshots, the urgent tickets and the linked organisations."""
    organisation = user.organisation
    earliest = today - timedelta(days=31 * CustomerHealthView.DEFAULT_HISTORY_MONTHS)
    accounts = list(
        filtered_queryset(user, params, today=today, scope=scope)
        .annotate(_last_touch_on=last_account_contact_annotation())
        .select_related("owner")
    )
    ids = [account.pk for account in accounts]
    snapshots = snapshot_history(ids, since=earliest, parent="account")
    urgent = account_urgent_ticket_counts(user, ids)
    linked = linked_organisations(user, ids)
    entries = [
        _entry(
            account,
            today=today,
            snapshots=snapshots.get(account.pk, []),
            urgent=urgent.get(account.pk, 0),
            organisations=linked.get(account.pk, []),
        )
        for account in accounts
    ]
    return AccountPortfolio(entries=entries, organisation=organisation)


def filter_options(user):
    """The filter sheet's choices, scoped exactly as the rows are: the
    organisations the viewer may open that hold one of their visible
    accounts, the owners of those accounts, and the stages present. Three
    queries, whatever the book's size."""
    # SOC2:AUTH-02 options are scoped to the viewer's own visible accounts
    accounts = visible_accounts(user).order_by()
    # SOC2:AUTH-02 an organisation is offered only if the viewer may open it
    organisations = (
        visible_customers(user)
        .filter(accounts__in=accounts)
        .order_by("name", "id")
        .values_list("id", "name")
        .distinct()
    )
    # SOC2:AUTH-02 owners only from the viewer's own organisation: the
    # serializer enforces it on write, but a bad import or seed row must not
    # put another tenant's name in this menu.
    owners = list(
        accounts.filter(Q(owner__isnull=True) | Q(owner__organisation=user.organisation))
        .values_list("owner_id", "owner__name")
        .distinct()
    )
    named = sorted(
        ((pk, name) for pk, name in owners if pk is not None),
        key=lambda row: (row[1] or "").casefold(),
    )
    stages = set(accounts.values_list("lifecycle_stage", flat=True).distinct())
    return {
        "organisations": [{"value": str(pk), "name": name} for pk, name in organisations],
        "owners": [{"value": str(pk), "name": name} for pk, name in named]
        + (
            [{"value": "unassigned", "name": "Unassigned"}]
            if any(pk is None for pk, _name in owners)
            else []
        ),
        "lifecycles": [
            {"value": value, "name": label}
            for value, label in Customer.LifecycleStage.choices
            if value in stages
        ],
    }
