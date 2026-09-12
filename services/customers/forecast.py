"""The rollups behind the Revenue Forecast dashboard.

**What is this book worth twelve months from now, and what moves it?**

Deliberately a different question from the Health Overview's Renewal Date tab.
That one is operational — which renewals do I work this week, ranked by expected
loss, inside ninety days. This one is financial: an ARR bridge from what the
book is worth today to what it is forecast to be worth at the end of the year,
with the things that move it named and weighted.

    opening ARR  −  expected churn  −  expected contraction  +  expected
    expansion  =  forecast ARR

Three sources, each already in this database and none of them previously read
together:

* **Renewals** carry churn risk, weighted by `churn.risk_of_loss` — the same
  rule the Renewal tab prints on its own rows, because a forecast that disagrees
  with the work list is a forecast nobody trusts twice.
* **Open Risks** carry contraction: a logged risk with an MRR on it is money
  someone has already written down as in danger.
* **Open Opportunities** carry expansion, weighted by sales stage.

## Two rules that keep the downside honest

An account can be both renewing at risk *and* carrying an open Risk. The same
ARR cannot be lost twice, so per account the downside is the **larger of the
two**, never the sum. Without that rule a shaky account with a logged risk is
counted against the forecast twice.

And the downside is **capped at what the account actually pays**. A risk can be
logged with any MRR on it — a $60k/month risk against a customer paying $59k a
year is a typo, an aspiration, or a risk about something that isn't this
contract — and without the cap the worst case came out *negative*, which is a
forecast saying the book will owe money. You cannot lose more than you have.

## Stage weighting is an assumption, stated

The probabilities below are the ordinary sales-stage ladder, not something
fitted to this tenant's win rate — there is no closed-lost history here to fit
to. They are in one place so a customer who disagrees can change them once.

## What this deliberately is not

Not a time series. `Opportunity` and `Risk` carry no close date, so expansion
cannot be placed in a quarter; a chart that spread pipeline evenly across the
year would be inventing the one thing a forecast is asked for. The horizon is a
single window, and the scenario range is how uncertainty is shown instead.
"""

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from services.fx_rates.conversion import convert_to_org_currency, rates_for

from . import churn
from .models import Customer, Opportunity, Risk, with_health_inputs
from .scoping import live_customers

#: The forecast window. Twelve months is the horizon an ARR forecast is quoted
#: over, and it is long enough to contain every account's renewal exactly once.
DEFAULT_HORIZON_DAYS = 365

#: Probability an open opportunity closes, by sales stage. The ordinary ladder
#: — see the module docstring on why these are an assumption rather than a
#: measurement. Closed Won is certain by definition and carries no weighting.
STAGE_PROBABILITY = {
    Opportunity.Stage.DISCOVERY: 0.1,
    Opportunity.Stage.QUALIFICATION: 0.2,
    Opportunity.Stage.SOLUTION_VALIDATION: 0.4,
    Opportunity.Stage.PROPOSAL_PRICE_REVIEW: 0.6,
    Opportunity.Stage.NEGOTIATION: 0.8,
    Opportunity.Stage.CLOSED_WON: 1.0,
}

#: Probability an open risk lands, by the priority someone gave it. A logged
#: risk is a judgement already made; this only says how much of it to carry
#: into the number.
RISK_PROBABILITY = {
    Risk.Priority.HIGH: 0.6,
    Risk.Priority.MEDIUM: 0.3,
    Risk.Priority.LOW: 0.1,
}

#: Stages that are still live. A mitigated, realised or abandoned risk has
#: either stopped being a forecast question or already hit the ARR.
OPEN_RISK_STAGES = (Risk.Stage.OPEN,)

#: How many accounts the swing list carries.
LIST_LIMIT = 15


def _parse_int(raw):
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def filtered_customers(user, params):
    """The caller's visible book, narrowed by the bar's filters. Visibility
    first, then the filters narrow from there."""

    queryset = with_health_inputs(live_customers(user).select_related("owner", "primary_product"))

    owner = params.get("owner")
    if owner == "unassigned":
        queryset = queryset.filter(owner__isnull=True)
    else:
        owner_id = _parse_int(owner)
        if owner_id is not None:
            queryset = queryset.filter(owner_id=owner_id)

    lifecycle = params.get("lifecycle")
    if lifecycle in Customer.LifecycleStage.values:
        queryset = queryset.filter(lifecycle_stage=lifecycle)

    customer_id = _parse_int(params.get("customer"))
    if customer_id is not None:
        queryset = queryset.filter(pk=customer_id)

    return queryset


def horizon_days(params):
    """`?horizon_days=`, clamped to something a forecast can mean. Under a
    month is a pipeline report, past three years the renewal dates are
    guesses."""
    value = _parse_int(params.get("horizon_days"))
    if value is None:
        return DEFAULT_HORIZON_DAYS
    return max(30, min(1095, value))


def _pipeline_by_customer(customers, organisation, rates):
    """Open expansion per customer id: `(weighted, unweighted)` ARR.

    One query for the whole page. Opportunities hang off a Customer *or* one of
    its Accounts, so both are counted toward the company — an expansion at a
    division is expansion of that company's ARR.
    """

    ids = [customer.pk for customer in customers]
    opportunities = (
        Opportunity.objects.filter(Q(customer_id__in=ids) | Q(account__customers__id__in=ids))
        .select_related("customer")
        .prefetch_related("account__customers")
        .distinct()
    )

    by_customer = {}
    for opportunity in opportunities:
        # An opportunity on an account counts toward every company that
        # account belongs to — the same "empty means everywhere" shape the
        # rest of this app uses for account membership.
        parents = (
            [opportunity.customer]
            if opportunity.customer_id
            else list(opportunity.account.customers.all())
        )
        annual = opportunity.mrr * 12
        weight = STAGE_PROBABILITY.get(opportunity.stage, 0.0)
        for parent in parents:
            if parent.pk not in set(ids):
                continue
            converted = convert_to_org_currency(annual, parent.currency, organisation, rates=rates)
            if converted is None:
                continue
            entry = by_customer.setdefault(parent.pk, {"weighted": 0.0, "open": 0.0})
            entry["weighted"] += float(converted) * weight
            entry["open"] += float(converted)

    return by_customer


def _risk_by_customer(customers, organisation, rates):
    """Open contraction risk per customer id, weighted by priority."""

    ids = [customer.pk for customer in customers]
    risks = (
        Risk.objects.filter(Q(customer_id__in=ids) | Q(account__customers__id__in=ids))
        .filter(stage__in=OPEN_RISK_STAGES)
        .select_related("customer")
        .prefetch_related("account__customers")
        .distinct()
    )

    by_customer = {}
    for risk in risks:
        parents = [risk.customer] if risk.customer_id else list(risk.account.customers.all())
        annual = risk.mrr * 12
        weight = RISK_PROBABILITY.get(risk.priority, 0.3)
        for parent in parents:
            if parent.pk not in set(ids):
                continue
            converted = convert_to_org_currency(annual, parent.currency, organisation, rates=rates)
            if converted is None:
                continue
            entry = by_customer.setdefault(parent.pk, {"weighted": 0.0, "open": 0.0})
            entry["weighted"] += float(converted) * weight
            entry["open"] += float(converted)

    return by_customer


class ForecastRow:
    """One account's contribution to the forecast."""

    __slots__ = (
        "customer",
        "arr",
        "renews_in_horizon",
        "days_to_renewal",
        "risk",
        "factors",
        "churn_exposure",
        "risk_exposure",
        "downside",
        "expansion",
        "open_pipeline",
        "open_risk",
    )

    def __init__(
        self, customer, arr, *, in_horizon, days_to_renewal, risk, factors, pipeline, risk_entry
    ):
        self.customer = customer
        self.arr = arr
        self.renews_in_horizon = in_horizon
        self.days_to_renewal = days_to_renewal
        self.risk = risk
        self.factors = factors

        # Churn only counts where a renewal actually falls inside the window:
        # an account renewing in three years cannot churn at renewal this year,
        # whatever its health.
        self.churn_exposure = (arr or 0.0) * risk if in_horizon and arr is not None else 0.0
        self.risk_exposure = risk_entry["weighted"] if risk_entry else 0.0
        self.open_risk = risk_entry["open"] if risk_entry else 0.0

        # The same ARR cannot be lost twice, and neither can more of it than
        # exists — see the module docstring on both rules.
        self.downside = min(max(self.churn_exposure, self.risk_exposure), arr or 0.0)

        self.expansion = pipeline["weighted"] if pipeline else 0.0
        self.open_pipeline = pipeline["open"] if pipeline else 0.0

    @property
    def net(self):
        """Expansion minus downside: this account's effect on the forecast."""
        return self.expansion - self.downside


def build_rows(customers, organisation, *, horizon=DEFAULT_HORIZON_DAYS, today=None):
    today = today or timezone.localdate()
    cutoff = today + timedelta(days=horizon)
    rates = rates_for(organisation)

    customers = list(customers)
    pipeline = _pipeline_by_customer(customers, organisation, rates)
    risks = _risk_by_customer(customers, organisation, rates)

    rows = []
    for customer in customers:
        converted = convert_to_org_currency(
            customer.arr_billed_at_account, customer.currency, organisation, rates=rates
        )
        arr = None if converted is None else float(converted)

        renewal = customer.renewal_date
        # An overdue renewal is inside the window by definition: the date has
        # passed and the question is still open.
        in_horizon = renewal is not None and renewal <= cutoff
        days = (renewal - today).days if renewal else None

        risk, factors = churn.risk_of_loss(
            customer, days_since_touch=customer.health_inputs(today)["days_since_touch"]
        )

        rows.append(
            ForecastRow(
                customer,
                arr,
                in_horizon=in_horizon,
                days_to_renewal=days,
                risk=risk,
                factors=factors,
                pipeline=pipeline.get(customer.pk),
                risk_entry=risks.get(customer.pk),
            )
        )

    return rows


def build_bridge(rows):
    """Opening ARR to forecast ARR, with each step named.

    A waterfall, because a single forecast number hides which way it was
    reached: the same closing figure can be a quiet year or a year where a
    third of the book churned and the pipeline covered it.
    """

    opening = sum(row.arr or 0.0 for row in rows)
    # Attributed to whichever component the (possibly capped) downside came
    # from, so the two bars always sum to the downside the net uses. Summing
    # the raw exposures instead would let the bridge's steps disagree with its
    # own total the moment a cap bit.
    churn_loss = sum(row.downside for row in rows if row.churn_exposure >= row.risk_exposure)
    contraction = sum(row.downside for row in rows if row.churn_exposure < row.risk_exposure)
    expansion = sum(row.expansion for row in rows)
    forecast = opening - churn_loss - contraction + expansion

    return {
        "opening_arr": round(opening, 2),
        "churn": round(churn_loss, 2),
        "contraction": round(contraction, 2),
        "expansion": round(expansion, 2),
        "forecast_arr": round(forecast, 2),
        "net_change": round(forecast - opening, 2),
        # Net revenue retention, the number this whole screen exists to
        # produce: what the book becomes, before any new logos. Null rather
        # than a fake 100% when there is nothing to divide by.
        "nrr": round(forecast / opening * 100, 1) if opening else None,
    }


def build_scenarios(rows):
    """Worst, likely and best, because a forecast is a range.

    - **Worst**: every at-risk renewal in the window is lost, every open risk
      lands, nothing in the pipeline closes.
    - **Likely**: everything weighted, which is the bridge.
    - **Best**: nothing churns and the whole open pipeline closes.

    Worst is deliberately not "every account churns" — an account renewing
    next year cannot be lost in this window, and a floor nobody believes is a
    floor nobody uses.
    """

    opening = sum(row.arr or 0.0 for row in rows)
    worst_loss = sum(
        min(
            max((row.arr or 0.0) if row.renews_in_horizon else 0.0, row.open_risk),
            row.arr or 0.0,
        )
        for row in rows
    )
    likely = build_bridge(rows)["forecast_arr"]
    best = opening + sum(row.open_pipeline for row in rows)

    return {
        "worst": round(opening - worst_loss, 2),
        "likely": round(likely, 2),
        "best": round(best, 2),
    }


def _row_payload(row):
    customer = row.customer
    return {
        "id": customer.id,
        "name": customer.name,
        "owner": customer.owner.name if customer.owner else "Unassigned",
        "arr": row.arr,
        "renewal_date": customer.renewal_date.isoformat() if customer.renewal_date else None,
        "days_to_renewal": row.days_to_renewal,
        "renews_in_horizon": row.renews_in_horizon,
        "risk": row.risk,
        "factors": row.factors,
        "churn_exposure": round(row.churn_exposure, 2),
        "risk_exposure": round(row.risk_exposure, 2),
        "downside": round(row.downside, 2),
        "expansion": round(row.expansion, 2),
        "open_pipeline": round(row.open_pipeline, 2),
        "net": round(row.net, 2),
        "health_category": customer.health_category,
    }


def bridge_by(rows, key):
    """The bridge for each group of rows, keyed by `key(row)`.

    Reuses `build_bridge` per group rather than re-summing, so a slice by
    owner or product can never disagree with the whole — the groups' opening
    ARRs add up to the book's, and their downsides to its downside, because
    the same capped-per-row arithmetic produced both.
    """
    groups = {}
    for row in rows:
        groups.setdefault(key(row), []).append(row)
    return {member: build_bridge(group) for member, group in groups.items()}


def swing_list(rows):
    """The accounts that decide the number, by how far they move it either way.

    Ranked on the absolute net, so the biggest expansion and the biggest
    exposure sit in the same list — a forecast review works one list of names,
    not two, and an account that is both is exactly the one to talk about.
    """
    ranked = sorted(
        (row for row in rows if row.net != 0),
        key=lambda row: -abs(row.net),
    )
    return [_row_payload(row) for row in ranked[:LIST_LIMIT]]


def pipeline_by_stage(customers, organisation):
    """Open expansion by sales stage, weighted and not.

    Both numbers, deliberately: the gap between them is how much of the upside
    is a conversation rather than a commitment, and a screen that showed only
    the weighted figure would hide where the pipeline actually sits.
    """

    rates = rates_for(organisation)
    ids = [customer.pk for customer in customers]
    by_id = {customer.pk: customer for customer in customers}

    totals = {
        stage: {"key": stage, "name": label, "open": 0.0, "weighted": 0.0, "count": 0}
        for stage, label in Opportunity.Stage.choices
    }

    opportunities = (
        Opportunity.objects.filter(Q(customer_id__in=ids) | Q(account__customers__id__in=ids))
        .prefetch_related("account__customers")
        .distinct()
    )

    for opportunity in opportunities:
        parents = (
            [opportunity.customer]
            if opportunity.customer_id
            else list(opportunity.account.customers.all())
        )
        parent = next((p for p in parents if p.pk in by_id), None)
        if parent is None:
            continue
        converted = convert_to_org_currency(
            opportunity.mrr * 12, parent.currency, organisation, rates=rates
        )
        if converted is None:
            continue

        bucket = totals[opportunity.stage]
        bucket["open"] += float(converted)
        bucket["weighted"] += float(converted) * STAGE_PROBABILITY.get(opportunity.stage, 0.0)
        bucket["count"] += 1

    return [
        {
            **totals[stage],
            "open": round(totals[stage]["open"], 2),
            "weighted": round(totals[stage]["weighted"], 2),
        }
        for stage, _label in Opportunity.Stage.choices
    ]


def filter_options(user):
    """The bar's dropdowns, scoped exactly as the numbers are."""

    customers = live_customers(user)
    owners = (
        customers.exclude(owner__isnull=True)
        .values_list("owner_id", "owner__name")
        .distinct()
        .order_by("owner__name")
    )
    return {
        "owners": [{"value": str(owner_id), "name": name} for owner_id, name in owners]
        + (
            [{"value": "unassigned", "name": "Unassigned"}]
            if customers.filter(owner__isnull=True).exists()
            else []
        ),
        "lifecycles": [
            {"value": value, "name": label}
            for value, label in Customer.LifecycleStage.choices
            if customers.filter(lifecycle_stage=value).exists()
        ],
        "customers": [
            {"value": str(pk), "name": name}
            for pk, name in customers.order_by("name").values_list("id", "name")
        ],
    }
