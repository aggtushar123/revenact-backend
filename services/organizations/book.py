"""The Organizations portfolio's book: the viewer's customers, narrowed by the
page's filters, with every signal a row shows.

**Visibility first.** Everything starts from `visible_customers(user)`; a raw
`?ids=` or `?owner=` can only narrow that, never widen it.

**Scope.** Archived customers are hidden unless named by `ids`, the way
`/customers/?ids=` treats them. Churned customers — a `churn_date`, or the
Churn stage — are hidden unless the viewer asks (`include_churned=1`, or
`churn` among the lifecycle filters) or names them by id: the working list is
the book you hold.
"""

from datetime import timedelta

from django.db.models import CharField, Q
from django.db.models.functions import Cast

from services.customers.models import Customer
from services.customers.scoping import visible_customers

from .params import PortfolioParams

#: Either marker means the customer has left. The churn modal sets both; a
#: seeded or imported row may carry only one, and either should hide it.
CHURNED = Q(churn_date__isnull=False) | Q(lifecycle_stage=Customer.LifecycleStage.CHURN)

#: `CustomerStatsView`'s buckets: NPS is stored per customer as −100…100, so
#: the band is its sign.
NPS_Q = {
    "promoter": Q(nps_score__gt=0),
    "passive": Q(nps_score=0),
    "detractor": Q(nps_score__lt=0),
}


def health_q(category):
    """`Customer.health_category` as SQL, read off the same thresholds, so the
    filter and the ring can never disagree about a boundary score."""
    upper = None
    for threshold, name in Customer.HEALTH_THRESHOLDS:
        if name == category:
            lower = Q(health_score__gte=threshold)
            return lower if upper is None else lower & Q(health_score__lt=upper)
        upper = threshold
    return Q(health_score__lt=upper)


def filtered_queryset(user, params: PortfolioParams, *, today):
    # SOC2:AUTH-02 record visibility comes first; filters only narrow it
    customers = visible_customers(user)

    if params.ids is None:
        customers = customers.filter(is_archived=False)
        asked_for_churned = (
            params.include_churned or Customer.LifecycleStage.CHURN in params.lifecycles
        )
        if not asked_for_churned:
            customers = customers.exclude(CHURNED)
    elif params.ids:
        customers = customers.filter(pk__in=params.ids)
    else:
        return customers.none()

    if params.search:
        customers = customers.annotate(id_as_text=Cast("id", CharField())).filter(
            Q(name__icontains=params.search) | Q(id_as_text__icontains=params.search)
        )

    if params.owner == "unassigned":
        customers = customers.filter(owner__isnull=True)
    elif params.owner is not None:
        customers = customers.filter(owner_id=params.owner)

    if params.lifecycles:
        customers = customers.filter(lifecycle_stage__in=params.lifecycles)

    if params.health:
        bands = Q()
        for category in params.health:
            bands |= health_q(category)
        customers = customers.filter(bands)

    if params.products:
        customers = customers.filter(primary_product_id__in=params.products)

    if params.renews_within is not None:
        deadline = today + timedelta(days=params.renews_within)
        customers = customers.filter(renewal_date__isnull=False, renewal_date__lte=deadline)

    if params.nps:
        customers = customers.filter(NPS_Q[params.nps])

    return customers
