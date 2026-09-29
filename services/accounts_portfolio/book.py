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

from datetime import timedelta

from django.db.models import CharField, Q
from django.db.models.functions import Cast

from services.customers.models import Customer
from services.customers.scoping import visible_accounts, visible_customers
from services.organizations.book import NPS_Q, health_q

from .params import AccountPortfolioParams


def account_renewing_q(days, *, today):
    """Renews within `days`, overdue included: the `renews_within` filter's
    rule, and (over `renewal_days`, in `shape.build_summary`) the Renewing
    tile's, so clicking the tile lists exactly the N it showed."""
    return Q(renewal_date__isnull=False, renewal_date__lte=today + timedelta(days=days))


def filtered_queryset(user, params: AccountPortfolioParams, *, today):
    # SOC2:AUTH-02 record visibility comes first; filters only narrow it
    accounts = visible_accounts(user)

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
