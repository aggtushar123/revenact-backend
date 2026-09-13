from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import CharField, Count, Prefetch, Q
from django.db.models.functions import Cast, TruncMonth
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status, views
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import SAFE_METHODS, IsAuthenticated
from rest_framework.response import Response

from services.accounts.models import User
from services.accounts.permissions import CanManageOrgSettings
from services.connectors.models import Connector
from services.copilot.anthropic_client import (
    BudgetExceeded,
    CopilotNotConfigured,
    CopilotRequestFailed,
)
from services.fx_rates.conversion import convert_to_org_currency, rates_for
from services.notifications.models import Notification
from services.notifications.realtime import notify as send_notification

from . import activity_tracking, forecast, interactions, portfolio, product_usage, usage
from .headline_generation import NothingToSummarise, generate_headlines
from .models import (
    Account,
    Canvas,
    Contact,
    Customer,
    Headline,
    HealthSnapshot,
    Opportunity,
    Product,
    Risk,
    Survey,
    Task,
    Ticket,
    with_health_inputs,
)
from .scoping import (
    get_visible_account,
    get_visible_customer,
    live_customers,
    visible_accounts,
    visible_children_q,
    visible_customers,
)
from .serializers import (
    AccountSerializer,
    ActivitySerializer,
    CalendarEventSerializer,
    CanvasSerializer,
    ContactSerializer,
    CustomerHealthRowSerializer,
    CustomerSerializer,
    EmailSerializer,
    HeadlineSerializer,
    NoteSerializer,
    OpportunitySerializer,
    ProductSerializer,
    RiskSerializer,
    SurveySerializer,
    TaskListSerializer,
    TaskSerializer,
    TicketSerializer,
)


def _notify_owner_assigned(*, instance, actor, kind, noun, link):
    """Real ownership-assignment notification — the caller is
    responsible for only calling this when `instance.owner` really is a
    *new* assignment (on create: any real owner; on update: only when
    it actually changed — see CustomerDetailView/AccountDetailView's
    own perform_update below). No-ops when there's no real owner, or
    when the owner is assigning it to themselves (no self-notifications).
    Shared by all four real call sites below (Customer/Account,
    create/update) rather than duplicated per view."""

    owner = instance.owner
    if owner is None or owner.id == actor.id:
        return
    send_notification(
        recipient=owner,
        actor=actor,
        kind=kind,
        message=f'{actor.name} assigned you {noun} "{instance.name}"',
        link=link,
    )


class CustomerListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/v1/customers/ — scoped to the caller's own
    organisation (tenant). Any authenticated user (admin or CSM) can list
    and add — unlike User Management, there's no admin-only gate here.

    GET supports `?search=` — matches against name (substring, case-
    insensitive) or Revenact ID (the row's own `id`, also substring —
    `id` is cast to text via `Cast` rather than relying on a database's
    own icontains-on-integer behaviour, which isn't portable). There's no
    separate "External ID" field in the schema yet, so that part of the
    frontend's search placeholder isn't wired to anything real.

    GET also supports `?renewal_within=<days>` — customers whose
    `renewal_date` is on or before today+<days>, ordered soonest/most-
    overdue-first instead of the model's default name ordering. No lower
    bound: an already-overdue renewal_date (the CSM hasn't updated it
    yet) is *more* urgent, not less, so it's included rather than
    filtered out. Excludes already-churned customers (renewal is moot
    for those) and anything with no `renewal_date` set. Powers the
    Organizations page's "Renewal" card/popover. A non-integer value is
    ignored rather than raising an error.

    Archived customers (`is_archived=True`) never appear here — soft-
    hidden, same as from the stats endpoint below. They're still
    reachable directly via the detail endpoint (not deleted), and PATCH
    `is_archived` on it to unarchive; there's just no "show archived"
    view yet."""

    serializer_class = CustomerSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Annotated up front: every serialized row renders health_breakdown,
        # which needs a last-touch date and an open-ticket count. Without this
        # each row runs two more queries — 120 extra on a 60-customer page.
        queryset = with_health_inputs(
            visible_customers(self.request.user).filter(is_archived=False)
        )

        search = self.request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.annotate(id_as_text=Cast("id", CharField())).filter(
                Q(name__icontains=search) | Q(id_as_text__icontains=search)
            )

        renewal_within = self.request.query_params.get("renewal_within")
        if renewal_within is not None:
            try:
                days = int(renewal_within)
            except ValueError:
                days = None
            if days is not None:
                deadline = timezone.localdate() + timedelta(days=days)
                queryset = (
                    queryset.exclude(lifecycle_stage=Customer.LifecycleStage.CHURN)
                    .filter(renewal_date__isnull=False, renewal_date__lte=deadline)
                    .order_by("renewal_date")
                )

        return queryset

    def perform_create(self, serializer):
        customer = serializer.save()
        _notify_owner_assigned(
            instance=customer,
            actor=self.request.user,
            kind=Notification.Kind.CUSTOMER_ASSIGNED,
            noun="the organization",
            link=f"/organizations/{customer.id}",
        )


class CustomerHealthView(generics.ListAPIView):
    """GET /api/v1/customers/health/ — the whole book with its health history,
    for the Health Overview dashboard (Triage / Divergence / Movement /
    Controls). Scoped to the caller's own organisation.

    One request for four tabs. They read the same rows differently — ranked by
    risk, plotted CSM-pulse against AI-pulse, counted as transitions between
    months — so splitting this into four endpoints would fetch the same book
    four times.

    **Unpaginated on purpose.** Every tab aggregates over the entire book:
    a triage queue that ranked only page one would rank nothing, and a flow
    chart missing half the accounts would show the wrong movement. Capped at
    MAX_ROWS so it can't become an unbounded response if a tenant grows past
    what this shape supports — the cap is reported in the payload rather than
    silently truncating.

    Archived customers are excluded, matching the list endpoint: they're
    deliberately hidden from the working views, and health is a working view.

    `?history_months=` trims how much history comes back (default 12, the most
    any tab offers). Movement's own window selector re-slices client-side;
    this is for keeping the payload down, not for the UI.

    **Money comes back converted.** Each row's `arr` is in the organisation's
    own reporting currency, or null where no FX rate is configured for that
    customer's contract currency — `currency` and `unconverted_count` in the
    payload say which, so the Renewal tab can name what it left out instead of
    adding up figures in different units. Same single conversion hook
    CustomerStatsView uses.
    """

    serializer_class = CustomerHealthRowSerializer
    pagination_class = None

    #: Beyond this the one-request-for-the-whole-book shape stops being
    #: reasonable and the dashboard needs server-side aggregation instead.
    MAX_ROWS = 500
    DEFAULT_HISTORY_MONTHS = 12

    def _history_months(self):
        raw = self.request.query_params.get("history_months")
        if raw is None:
            return self.DEFAULT_HISTORY_MONTHS
        try:
            months = int(raw)
        except ValueError as exc:
            raise ValidationError({"history_months": "Must be a whole number."}) from exc
        if months < 1:
            raise ValidationError({"history_months": "Must be at least 1."})
        return months

    def get_queryset(self):
        months = self._history_months()
        # Month-ends only go back so far; trimming by date rather than by count
        # keeps every customer's history aligned on the same months, which is
        # what the flow chart's columns depend on.
        earliest = timezone.localdate() - timedelta(days=31 * months)

        snapshots = HealthSnapshot.objects.filter(captured_on__gte=earliest).order_by("captured_on")
        return (
            # Annotated: every row serialises `days_since_touch`, which the
            # Renewal tab ranks by. Without this each row runs its own
            # aggregate — 500 extra queries on a full book.
            with_health_inputs(live_customers(self.request.user))
            .select_related("owner")
            .prefetch_related(Prefetch("health_snapshots", queryset=snapshots))
            # One past the cap, so `list` can tell "exactly MAX_ROWS rows"
            # from "more than MAX_ROWS and trimmed" without a second count.
            .order_by("name")[: self.MAX_ROWS + 1]
        )

    def get_serializer_context(self):
        # The row serializer converts each ARR into the org's own currency;
        # the rate table is fetched once here rather than per row.
        organisation = self.request.user.organisation
        return {
            **super().get_serializer_context(),
            "organisation": organisation,
            "fx_rates": rates_for(organisation),
        }

    def list(self, request, *args, **kwargs):
        customers = list(self.get_queryset())
        truncated = len(customers) > self.MAX_ROWS
        rows = self.get_serializer(customers[: self.MAX_ROWS], many=True).data
        return Response(
            {
                "results": rows,
                "count": len(rows),
                "history_months": self._history_months(),
                # True only when rows were actually dropped, so the client can
                # say so rather than quietly charting a partial book.
                "truncated": truncated,
                # The currency every `arr` above is denominated in, and how many
                # rows have none because their contract currency has no rate
                # configured. A money chart that silently drops rows is worse
                # than one that says how many it dropped.
                "currency": request.user.organisation.currency,
                "unconverted_count": sum(1 for row in rows if row["arr"] is None),
            }
        )


class ProductListView(generics.ListCreateAPIView):
    """GET/POST /api/v1/products/ — the tenant's own product catalogue.

    Mounted at its own top-level prefix rather than under /customers/,
    for two reasons: a product is organisation configuration rather than
    a customer's sub-resource, and /customers/products/ already means
    the Product Usage dashboard's rollup (ProductUsageView below).

    **Not scoped by ownership.** Every other list here is filtered to
    what the caller owns, because a CSM's book is their own. A product
    list is the shape of the business: a CSM who cannot see "Product C"
    cannot record a customer on it, and the pickers on the Add/Edit form
    would differ per user. So this is the whole organisation's list,
    ordered by name.

    Retired products (`is_active=False`) are returned too — the caller
    needs to see them to un-retire one, and the pickers filter on the
    flag rather than on the endpoint.

    Unpaginated: a product catalogue is tens of rows, and every consumer
    is a dropdown that needs all of them.

    **Reads for everyone, writes for `manage_org_settings`.** The pickers
    need the list, so any member can GET it. Adding, renaming and
    retiring are organisation configuration: a CSM able to add "Prodcut
    A" from a form would be the free-text problem this table replaced,
    back through a different door.
    """

    serializer_class = ProductSerializer
    pagination_class = None

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [IsAuthenticated()]
        return [CanManageOrgSettings()]

    def get_queryset(self):
        # `.order_by` explicitly: Product.Meta.ordering does not survive the
        # annotate() below (it folds into the GROUP BY instead), so without
        # this the catalogue comes back in insertion order.
        return (
            Product.objects.filter(organisation=self.request.user.organisation)
            .annotate(customer_count=Count("primary_customers", distinct=True))
            .order_by("name")
        )


class ProductDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/v1/products/<id>/ — one product.

    **DELETE is refused while customers are recorded against it**, with
    the count and the alternative in the message. `Customer.primary_product`
    is `PROTECT`ed so the database would refuse anyway; catching it here
    is the difference between an answer and a 500.

    Retiring (`PATCH {"is_active": false}`) is the operation people
    actually want: it takes the product out of the pickers and leaves
    every figure that was ever reported against it intact. Deleting is
    for a product added by mistake.

    Renaming is allowed and deliberately cheap — the customers point at
    the row, so fixing a typo fixes it everywhere at once. That is the
    whole difference from the free-text field this replaced, where a
    rename meant editing every customer and hoping.
    """

    serializer_class = ProductSerializer

    def get_permissions(self):
        # Same split as the list: anyone may read one, only org settings
        # managers may change or delete it.
        if self.request.method in SAFE_METHODS:
            return [IsAuthenticated()]
        return [CanManageOrgSettings()]

    def get_queryset(self):
        return Product.objects.filter(organisation=self.request.user.organisation).annotate(
            customer_count=Count("primary_customers", distinct=True)
        )

    def perform_destroy(self, instance):
        in_use = instance.primary_customers.count()
        if in_use:
            raise ValidationError(
                f"{in_use} customer{'' if in_use == 1 else 's'} "
                f"{'is' if in_use == 1 else 'are'} recorded against "
                f'"{instance.name}". Retire it instead (is_active=false) so their '
                "history keeps saying what they bought."
            )
        instance.delete()


class ProductUsageView(views.APIView):
    """GET /api/v1/customers/products/ — one row per product, for the
    Product Usage dashboard.

    Not the Usage Overview, which reads seats in aggregate across the
    whole book. This compares products against each other: ARR led,
    health mix, utilisation, satisfaction, support burden and churn. The
    Usage Overview is a CS operations screen; this is the one a product
    manager opens, and the only place that can answer "is one of these
    products responsible for most of the churn".

    **Attributed on `primary_product` only**, which the response says out
    loud in `attribution`. `additional_products_count` is a bare integer
    — nobody records *which* other products a customer has — so a
    customer on three products is counted once, under their primary.
    These are customers *led by* a product, never revenue split across
    products. Doing it properly needs a Product model and a per-customer
    join; see services/customers/product_usage.py.

    Includes churned customers, like the Customer Overview: a product
    whose customers all left would otherwise look like a product with no
    problems.

    Params: `owner`, `lifecycle`, `product`.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            {
                **product_usage.build_stats(request.user, request.query_params),
                "filters": product_usage.filter_options(request.user),
            }
        )


class CustomerOverviewView(views.APIView):
    """GET /api/v1/customers/overview/ — what the book is made of, for the
    Customer Overview dashboard.

    Every other dashboard asks how the customers you have are *doing*.
    This asks what they *are*: how many, how big, how concentrated, how
    long they stayed, and why the ones who left left. See
    services/customers/portfolio.py.

    **The one endpoint that counts customers you no longer have.**
    Everything else filters archived rows out and never reads a
    `churn_date`, which is right for a working view. Logo retention,
    cohort survival and churn reasons are questions about exactly those
    rows, and computing retention over survivors would return 100% every
    time.

    `owner`, `lifecycle` and `customer` match every other dashboard.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            {
                **portfolio.build_stats(request.user, request.query_params),
                "filters": portfolio.filter_options(request.user),
            }
        )


class ActivityTrackingView(views.APIView):
    """GET /api/v1/customers/activity/ — the operations review behind the
    Activity Tracking dashboard: is the team working the book, and where
    isn't it?

    Not the same question as AI Trending Topics, which counts what
    *customers* are talking about. This counts what we did — touches
    logged, accounts covered, cadence kept, follow-through on tasks. See
    services/customers/activity_tracking.py, which owns the definitions
    and states the two that could mislead: what counts as a touch, and
    why per-CSM figures are by account owner rather than by the free-text
    name on a record.

    `?days=` sets the window (default 90, clamped 7–730). `owner`,
    `lifecycle` and `customer` match every other dashboard.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            {
                **activity_tracking.build_stats(request.user, request.query_params),
                "filters": activity_tracking.filter_options(request.user),
            }
        )


class CustomerForecastView(views.APIView):
    """GET /api/v1/customers/forecast/ — the ARR bridge behind the Revenue
    Forecast dashboard.

    What is this book worth a year from now, and what moves it: opening
    ARR, minus expected churn at renewal, minus expected contraction from
    open risks, plus expected expansion from open opportunities. See
    services/customers/forecast.py, which owns the arithmetic and states
    every assumption in it.

    Deliberately a different question from the Health Overview's Renewal
    Date tab. That one is operational — which renewals do I work this
    week, inside ninety days. This is financial, over a year, and it is
    the only screen that reads renewals, risks and pipeline together.

    The churn weighting comes from `churn.risk_of_loss`, the same rule
    the Renewal tab prints on its own rows. A forecast that disagrees
    with the work list is a forecast nobody trusts twice.

    `?horizon_days=` moves the window (default 365, clamped to 30–1095).
    The other three params match every other dashboard: `owner`,
    `lifecycle`, `customer`.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        organisation = request.user.organisation
        customers = list(forecast.filtered_customers(request.user, request.query_params))
        horizon = forecast.horizon_days(request.query_params)
        rows = forecast.build_rows(customers, organisation, horizon=horizon)

        return Response(
            {
                "horizon_days": horizon,
                "bridge": forecast.build_bridge(rows),
                "scenarios": forecast.build_scenarios(rows),
                "pipeline": forecast.pipeline_by_stage(customers, organisation),
                "swing": forecast.swing_list(rows),
                "accounts": len(rows),
                "unpriced_count": sum(1 for row in rows if row.arr is None),
                "renewing_count": sum(1 for row in rows if row.renews_in_horizon),
                "currency": organisation.currency,
                "filters": forecast.filter_options(request.user),
            }
        )


class CustomerUsageView(views.APIView):
    """GET /api/v1/customers/usage/ — every rollup the Usage Overview
    dashboard's Controls tab needs, in one response.

    The screen answers one question — are customers using what they pay
    for? — and the answer has two commercial halves: shelfware below the
    line, expansion capacity above it. See services/customers/usage.py,
    which owns the arithmetic and the thresholds.

    Rollups computed server-side, like /tickets/stats/ and
    /interactions/stats/, rather than shipping the book for the client to
    aggregate. The Health endpoint ships rows instead, and that
    difference is deliberate: five Health tabs slice the same book five
    ways, while this screen has one view and asks the server one thing.
    The scatter is the exception — it is per-account by nature, capped at
    MAX_POINTS.

    Unfiltered by default, same trap and same choice as the other two
    dashboards: a rolling default window empties every chart the moment
    real time moves past the seeded demo dates.

    Customer-level only. Seats live on Customer (`total_active_seats` /
    `total_contracted_seats`); Account has no seat fields at all, so an
    account-grain usage view would have nothing to count.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        organisation = request.user.organisation
        customers = usage.filtered_customers(request.user, request.query_params)
        rows = usage.rows_for(customers, organisation)

        return Response(
            {
                **usage.build_stats(rows),
                "scatter": usage.scatter_points(rows),
                "shelfware": usage.shelfware_list(rows),
                "at_capacity": usage.capacity_list(rows),
                "currency": organisation.currency,
                "filters": usage.filter_options(request.user),
            }
        )


class CustomerStatsView(views.APIView):
    """GET /api/v1/customers/stats/ — aggregate rollups for the
    Organizations page's MetricsPanel (Health / NPS / Lifecycle Stages
    sections), scoped to the caller's own organisation.

    Every customer counts here, churned ones included — "churn" is
    itself one of the lifecycle buckets below, unlike ?renewal_within=
    (on the list endpoint), which excludes them because renewal is moot
    for an already-churned customer.

    There's no stored MRR field (see Customer model's docstring on why —
    financials are stored independently, not derived, except this one:
    MRR has no independent meaning of its own here, it's purely
    `arr_billed_at_account / 12`) — computed the same way here as the
    frontend already does it elsewhere (features/customers/mapToOrgRow.ts).

    Each customer's own `arr_billed_at_account` is in *its own*
    `currency` (see that model's own docstring — independent of
    Organisation.currency), so it's converted into the org's own
    currency (via services.fx_rates.conversion.convert_to_org_currency)
    before being added to any bucket sum — these buckets are one
    tenant-wide total and can't meaningfully mix currencies. A customer
    whose currency has no configured FxRate is still counted (`count`),
    but excluded from every `mrr`/`arr` sum rather than having its
    unconverted amount silently treated as if it were already in the
    org's currency — `unconverted_count` in the response is exactly how
    many customers that happened to, so the frontend can show a caveat
    instead of a silently-too-low total.

    NPS: a customer with no `nps_score` set is excluded from the
    promoters/passives/detractors breakdown and the score's denominator
    (there's nothing to bucket it as) rather than silently counted as a
    passive.

    This aggregates in Python over the caller's own customers rather
    than via SQL-side conditional aggregation, because `health_category`
    is a derived Python property (from `health_score`), not a real
    column to GROUP BY — see Customer.health_category. Fine at the scale
    of one tenant's own customer list.

    Archived customers are excluded, same as from the list endpoint."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        organisation = request.user.organisation
        customers = live_customers(request.user)

        health = {
            cat: {"count": 0, "mrr": 0.0, "arr": 0.0} for cat in Customer.HealthCategory.values
        }
        lifecycle = {
            stage: {"count": 0, "mrr": 0.0, "arr": 0.0} for stage in Customer.LifecycleStage.values
        }
        promoters = passives = detractors = 0
        scored = 0
        unconverted_count = 0

        for customer in customers:
            health_bucket = health[customer.health_category]
            health_bucket["count"] += 1
            lifecycle_bucket = lifecycle[customer.lifecycle_stage]
            lifecycle_bucket["count"] += 1

            converted = convert_to_org_currency(
                customer.arr_billed_at_account, customer.currency, organisation
            )
            if converted is None:
                unconverted_count += 1
            else:
                arr = float(converted)
                mrr = arr / 12
                health_bucket["mrr"] += mrr
                health_bucket["arr"] += arr
                lifecycle_bucket["mrr"] += mrr
                lifecycle_bucket["arr"] += arr

            if customer.nps_score is not None:
                scored += 1
                if customer.nps_score > 0:
                    promoters += 1
                elif customer.nps_score == 0:
                    passives += 1
                else:
                    detractors += 1

        for bucket in (*health.values(), *lifecycle.values()):
            bucket["mrr"] = round(bucket["mrr"], 2)
            bucket["arr"] = round(bucket["arr"], 2)

        nps_score = round((promoters - detractors) / scored * 100) if scored else 0

        return Response(
            {
                "health": health,
                "nps": {
                    "promoters": promoters,
                    "passives": passives,
                    "detractors": detractors,
                    "score": nps_score,
                },
                "lifecycle": lifecycle,
                "unconverted_count": unconverted_count,
            }
        )


class CustomerDetailView(generics.RetrieveUpdateAPIView):
    """GET/PATCH /api/v1/customers/<id>/ — scoped to the caller's own
    organisation. 404, not 403, for a customer outside that scope."""

    serializer_class = CustomerSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return with_health_inputs(visible_customers(self.request.user))

    def perform_update(self, serializer):
        previous_owner = serializer.instance.owner
        customer = serializer.save()
        if customer.owner_id != (previous_owner.id if previous_owner else None):
            from services.knowledge.ownership import record_handover

            record_handover(
                customer,
                self.request.user,
                previous_owner,
                serializer.context.get("handover_note", ""),
            )
            _notify_owner_assigned(
                instance=customer,
                actor=self.request.user,
                kind=Notification.Kind.CUSTOMER_ASSIGNED,
                noun="the organization",
                link=f"/organizations/{customer.id}",
            )


class AccountListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/v1/customers/<customer_id>/accounts/ — every Account
    linked to one Customer (GET, one of possibly several it's linked to
    — see Account's own docstring), or adds a new one to it (POST),
    scoped to the caller's own organisation. The URL's own customer_id
    is never *removed* by a POST/PATCH through this URL — see
    perform_create below and AccountSerializer's own `customer_ids`
    field for adding/removing any *other* linked organisations.

    404 (not 403) for a customer_id outside the caller's organisation or
    that doesn't exist, same convention as CustomerDetailView — checked
    once up front via get_object_or_404 rather than left to fall out of
    an empty queryset, so a real customer in another org 404s the same
    way a nonexistent id does, instead of silently returning `[]` either
    way and leaving the two indistinguishable to the frontend.

    Add/Edit Account (this view's POST + AccountDetailView's PATCH below)
    covers identity, ownership, lifecycle stage, and renewal date — the
    fields an account genuinely has going in. Health/pulse/AI-pulse/NPS/
    CSAT/ARR are technically writable via AccountSerializer too (not
    restricted at the API layer, same as CustomerSerializer) but the
    Add/Edit Account UI never sends them — meant to sync from other
    systems later, same reasoning as Customer's own Add/Edit form."""

    serializer_class = AccountSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_customer(self):
        return get_visible_customer(self.request, self.kwargs["customer_id"])

    def get_queryset(self):
        return self.get_customer().accounts.all()

    def perform_create(self, serializer):
        # `customer_ids` (if the client sent it) already set whatever
        # full membership it asked for via the serializer's own
        # `source="customers"` — this always additionally links the
        # URL's own customer, so "Add Account" from this URL never
        # silently creates an account that isn't actually linked to the
        # Customer it was added under.
        customer = self.get_customer()
        # Owner defaults to the creator unless they named someone —
        # same reasoning as CustomerSerializer.create's own, and done
        # here rather than in AccountSerializer because that serializer
        # has no create() of its own to hang it off.
        account = serializer.save(
            **({} if serializer.validated_data.get("owner") else {"owner": self.request.user})
        )
        account.customers.add(customer)
        _notify_owner_assigned(
            instance=account,
            actor=self.request.user,
            kind=Notification.Kind.ACCOUNT_ASSIGNED,
            noun="the account",
            link=f"/accounts/{account.id}",
        )


class AccountDetailView(generics.RetrieveUpdateAPIView):
    """GET/PATCH /api/v1/customers/<customer_id>/accounts/<id>/ — scoped
    to the caller's own organisation and the given customer_id (one of
    possibly several the Account is linked to). 404, not 403, for either
    id outside that scope. See AccountListCreateView's docstring for
    what's actually editable — including AccountSerializer's own
    `customer_ids`, which a PATCH through this URL can use to add/remove
    *any* linked organisation, this one included (down to the
    "at least one" floor validate_customer_ids enforces)."""

    serializer_class = AccountSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return visible_accounts(self.request.user).filter(customers=self.kwargs["customer_id"])

    def perform_update(self, serializer):
        previous_owner_id = serializer.instance.owner_id
        account = serializer.save()
        if account.owner_id != previous_owner_id:
            _notify_owner_assigned(
                instance=account,
                actor=self.request.user,
                kind=Notification.Kind.ACCOUNT_ASSIGNED,
                noun="the account",
                link=f"/accounts/{account.id}",
            )


class AccountListView(generics.ListAPIView):
    """GET /api/v1/accounts/ — every Account across every Customer the
    caller's own organisation owns. Powers the standalone Accounts page
    (react-ts-app's /accounts/list) — the one place an Account is
    browsed independent of which Customer it belongs to, same reasoning
    as ContactListView.

    GET-only — unlike OpportunityListView/RiskListView, there's no
    matching POST here. Account has only one possible parent (a
    Customer, not the two-tier Customer-or-Account shape those two
    have), so "Add Account" from the standalone list already knows
    exactly which nested endpoint to POST to once a company is picked
    (/customers/<customer_id>/accounts/, i.e. AccountListCreateView
    above) — same reasoning ContactListView's own standalone "Add
    Contact" already follows, no separate flat create endpoint needed.

    Paginated with the shared DEFAULT_PAGINATION_CLASS/PAGE_SIZE, same
    reasoning as ContactListView (this can span every account the
    tenant has, unlike the nested per-Customer list above, which stays
    unpaginated since it's normally small).

    `?search=` matches name (substring, case-insensitive), same
    convention as CustomerListCreateView/ContactListView's own search.
    `?company=<customer_id>` filters to one company's own Accounts."""

    serializer_class = AccountSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # `.distinct()` because `customers__organisation=` fans out one
        # row per matching linked Customer — an Account linked to two+
        # Customers in this same organisation would otherwise appear
        # once per match instead of once overall.
        queryset = (
            visible_accounts(self.request.user)
            .prefetch_related("customers")
            .select_related("owner")
        )

        search = self.request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(name__icontains=search)

        company = self.request.query_params.get("company")
        if company:
            try:
                company_id = int(company)
            except ValueError:
                company_id = None
            if company_id is not None:
                queryset = queryset.filter(customers__id=company_id)

        return queryset


class AccountStatsView(views.APIView):
    """GET /api/v1/accounts/stats/ — aggregate rollups for the standalone
    Accounts page's own MetricsPanel (Health / NPS / Lifecycle Stages
    sections), scoped to the caller's own organisation. Same shape and
    same reasoning as CustomerStatsView — see that view's own docstring
    for the health/lifecycle bucketing and NPS-denominator rules, all
    identical here.

    Unlike CustomerStatsView, `account.arr` is used as-is, with no FX
    conversion step — Account has no `currency` field of its own (see
    that model's docstring: it can belong to more than one Customer, so
    an independent per-Account currency has no clean meaning). Every
    Account's `arr` is always treated as already being in the org's own
    currency. This is a real, explicit scope boundary of the per-
    Customer-currency feature, not an oversight.

    Every Account under the caller's org counts here — Account has no
    `is_archived` field to exclude anything by (see the model's own
    docstring: there's no "hide this account" concept yet). MRR is
    `arr / 12`, computed the same way as CustomerStatsView's own (and
    features/customers/mapToAccountRow.ts's own) — Account doesn't
    store MRR independently either.

    Aggregates in Python over the caller's own accounts, same reasoning
    as CustomerStatsView: `health_category` is a derived Python
    property, not a real column to GROUP BY. Fine at the scale of one
    tenant's own account list."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        # `.distinct()` for the same "an Account linked to two+ Customers
        # in this organisation would otherwise fan out" reason as
        # AccountListView's own — critical here, since this loop counts
        # each row once towards the health/lifecycle/NPS buckets; a
        # duplicate row would double-count that one Account.
        accounts = visible_accounts(request.user)

        health = {
            cat: {"count": 0, "mrr": 0.0, "arr": 0.0} for cat in Customer.HealthCategory.values
        }
        lifecycle = {
            stage: {"count": 0, "mrr": 0.0, "arr": 0.0} for stage in Customer.LifecycleStage.values
        }
        promoters = passives = detractors = 0
        scored = 0

        for account in accounts:
            arr = float(account.arr)
            mrr = arr / 12

            health_bucket = health[account.health_category]
            health_bucket["count"] += 1
            health_bucket["mrr"] += mrr
            health_bucket["arr"] += arr

            lifecycle_bucket = lifecycle[account.lifecycle_stage]
            lifecycle_bucket["count"] += 1
            lifecycle_bucket["mrr"] += mrr
            lifecycle_bucket["arr"] += arr

            if account.nps_score is not None:
                scored += 1
                if account.nps_score > 0:
                    promoters += 1
                elif account.nps_score == 0:
                    passives += 1
                else:
                    detractors += 1

        for bucket in (*health.values(), *lifecycle.values()):
            bucket["mrr"] = round(bucket["mrr"], 2)
            bucket["arr"] = round(bucket["arr"], 2)

        nps_score = round((promoters - detractors) / scored * 100) if scored else 0

        return Response(
            {
                "health": health,
                "nps": {
                    "promoters": promoters,
                    "passives": passives,
                    "detractors": detractors,
                    "score": nps_score,
                },
                "lifecycle": lifecycle,
            }
        )


class CustomerActivityListView(generics.ListAPIView):
    """GET /api/v1/customers/<customer_id>/activities/ — every
    organization-level Activity for one Customer, scoped to the
    caller's own organisation. 404 (not an empty list) for a
    customer_id outside that scope, same convention as
    AccountListCreateView. Powers ActivityFeed's "Activities" filter on
    the Organization Details page's General tab."""

    serializer_class = ActivitySerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        customer = get_visible_customer(self.request, self.kwargs["customer_id"])
        return customer.activities.all()


class AccountActivityListView(generics.ListAPIView):
    """GET /api/v1/customers/<customer_id>/accounts/<account_id>/activities/
    — every account-level Activity for one Account, scoped to both its
    customer_id and the caller's own organisation. 404 for either
    mismatch, same reasoning as AccountDetailView. Powers ActivityFeed's
    "Activities" filter on the standalone Account page — same component
    as the Customer-scoped view above, reading a different scope."""

    serializer_class = ActivitySerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        account = get_visible_account(
            self.request, self.kwargs["customer_id"], self.kwargs["account_id"]
        )
        return account.activities.all()


class CustomerEmailListView(generics.ListAPIView):
    """GET /api/v1/customers/<customer_id>/emails/ — every
    organization-level Email for one Customer, scoped to the caller's
    own organisation. Same 404-not-empty-list convention as
    CustomerActivityListView. Powers ActivityFeed's "Emails" filter on
    the Organization Details page's General tab."""

    serializer_class = EmailSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        customer = get_visible_customer(self.request, self.kwargs["customer_id"])
        return customer.emails.all()


class AccountEmailListView(generics.ListAPIView):
    """GET /api/v1/customers/<customer_id>/accounts/<account_id>/emails/
    — every account-level Email for one Account, scoped to both its
    customer_id and the caller's own organisation. Same reasoning as
    AccountActivityListView. Powers ActivityFeed's "Emails" filter on
    the standalone Account page."""

    serializer_class = EmailSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        account = get_visible_account(
            self.request, self.kwargs["customer_id"], self.kwargs["account_id"]
        )
        return account.emails.all()


class CustomerTaskListView(generics.ListAPIView):
    """GET /api/v1/customers/<customer_id>/tasks/ — every
    organization-level Task for one Customer, scoped to the caller's
    own organisation. Same 404-not-empty-list convention as
    CustomerActivityListView. Powers ActivityFeed's "Tasks" filter on
    the Organization Details page's General tab."""

    serializer_class = TaskSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        customer = get_visible_customer(self.request, self.kwargs["customer_id"])
        return customer.tasks.all()


class AccountTaskListView(generics.ListAPIView):
    """GET /api/v1/customers/<customer_id>/accounts/<account_id>/tasks/
    — every account-level Task for one Account, scoped to both its
    customer_id and the caller's own organisation. Same reasoning as
    AccountActivityListView. Powers ActivityFeed's "Tasks" filter on
    the standalone Account page."""

    serializer_class = TaskSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        account = get_visible_account(
            self.request, self.kwargs["customer_id"], self.kwargs["account_id"]
        )
        return account.tasks.all()


class TaskListView(generics.ListAPIView):
    """GET /api/v1/tasks/ — every Task across every Customer/Account the
    caller's own organisation owns, organisation-level and account-level
    alike. Same top-level, cross-company reasoning as OpportunityListView/
    RiskListView above — the one place a Task is browsed independent of
    which Customer/Account it belongs to. Powers Cockpit's own "My Tasks"
    panel (react-ts-app's src/pages/copilot/CockpitView.tsx), which used
    to read from an entirely separate, purely local mock Redux list
    (features/tasks/tasksSlice.ts) with no relation to this real model at
    all — that mock list is untouched here (still used by CallSenseTab's
    own, unrelated "Create Tasks from Actions" mockup).

    `?mine=true` additionally filters to Task rows whose parent Customer/
    Account's own `owner` is the caller — Cockpit's own "My Tasks" is
    exactly that, one CSM's own assigned book. Without it, this lists
    every Task the caller can *see*, which is a wider set than "mine":
    it also covers unowned parents and ones reached through the other
    side of the Customer/Account relationship (see
    services/customers/scoping.py).

    Unpaginated, same reasoning as OpportunityListView/RiskListView — a
    small, whole-collection list, not one meant to be paged through."""

    serializer_class = TaskListSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        # `.distinct()` — same fan-out reasoning as OpportunityListView's own.
        queryset = (
            Task.objects.filter(visible_children_q(self.request.user))
            .select_related("customer", "account")
            .distinct()
        )

        if self.request.query_params.get("mine") == "true":
            user = self.request.user
            queryset = queryset.filter(Q(customer__owner=user) | Q(account__owner=user))

        return queryset


class CustomerNoteListView(generics.ListAPIView):
    """GET /api/v1/customers/<customer_id>/notes/ — every
    organization-level Note for one Customer, scoped to the caller's
    own organisation. Same 404-not-empty-list convention as
    CustomerActivityListView. Powers ActivityFeed's "Notes" filter on
    the Organization Details page's General tab."""

    serializer_class = NoteSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        customer = get_visible_customer(self.request, self.kwargs["customer_id"])
        return customer.notes.all()


class AccountNoteListView(generics.ListAPIView):
    """GET /api/v1/customers/<customer_id>/accounts/<account_id>/notes/
    — every account-level Note for one Account, scoped to both its
    customer_id and the caller's own organisation. Same reasoning as
    AccountActivityListView. Powers ActivityFeed's "Notes" filter on
    the standalone Account page."""

    serializer_class = NoteSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        account = get_visible_account(
            self.request, self.kwargs["customer_id"], self.kwargs["account_id"]
        )
        return account.notes.all()


class CustomerTicketListView(generics.ListAPIView):
    """GET /api/v1/customers/<customer_id>/tickets/ — every
    organization-level Ticket for one Customer, scoped to the caller's
    own organisation. Same 404-not-empty-list convention as
    CustomerActivityListView. Powers ActivityFeed's "Tickets" filter on
    the Organization Details page's General tab."""

    serializer_class = TicketSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        customer = get_visible_customer(self.request, self.kwargs["customer_id"])
        return customer.tickets.all()


class AccountTicketListView(generics.ListAPIView):
    """GET /api/v1/customers/<customer_id>/accounts/<account_id>/tickets/
    — every account-level Ticket for one Account, scoped to both its
    customer_id and the caller's own organisation. Same reasoning as
    AccountActivityListView. Powers ActivityFeed's "Tickets" filter on
    the standalone Account page."""

    serializer_class = TicketSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        account = get_visible_account(
            self.request, self.kwargs["customer_id"], self.kwargs["account_id"]
        )
        return account.tickets.all()


class CustomerCalendarEventListView(generics.ListAPIView):
    """GET /api/v1/customers/<customer_id>/calendar-events/ — every
    organization-level CalendarEvent for one Customer, scoped to the
    caller's own organisation. Same 404-not-empty-list convention as
    CustomerActivityListView. Powers ActivityFeed's "Calendar Events"
    filter on the Organization Details page's General tab."""

    serializer_class = CalendarEventSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        customer = get_visible_customer(self.request, self.kwargs["customer_id"])
        return customer.calendar_events.all()


class AccountCalendarEventListView(generics.ListAPIView):
    """GET /api/v1/customers/<customer_id>/accounts/<account_id>/calendar-events/
    — every account-level CalendarEvent for one Account, scoped to
    both its customer_id and the caller's own organisation. Same
    reasoning as AccountActivityListView. Powers ActivityFeed's
    "Calendar Events" filter on the standalone Account page."""

    serializer_class = CalendarEventSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        account = get_visible_account(
            self.request, self.kwargs["customer_id"], self.kwargs["account_id"]
        )
        return account.calendar_events.all()


class CustomerContactListView(generics.ListCreateAPIView):
    """GET/POST /api/v1/customers/<customer_id>/contacts/ — GET returns
    every Contact under this Customer, rolled up from both levels a
    Contact can exist at: organization-level (directly on this
    Customer) *and* account-level (on any of its Accounts) — see
    Contact's own docstring on why both shapes exist. Powers the
    Organization Details page's own Contacts tab, which shows both
    kinds together (ContactSerializer's `account_name` is how the
    frontend tells them apart — null for an organization-level row).

    POST always adds an organization-level Contact here — `customer`
    is taken from the URL, never client-supplied, same as
    AccountListCreateView's own `customer`. An account-level Contact is
    added via AccountContactListView below instead — including from
    the Organization Details page's own Add Contact form, which POSTs
    there directly once the caller picks one of this customer's
    accounts (see ContactFormModal.tsx's own account picker) — and via
    a customer_id the frontend picks from a dropdown rather than a URL
    param, the standalone /contacts/list page's "Add Contact".

    Scoped to the caller's own organisation. Same 404-not-empty-list
    convention as CustomerActivityListView."""

    serializer_class = ContactSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_customer(self):
        return get_visible_customer(self.request, self.kwargs["customer_id"])

    def get_queryset(self):
        customer = self.get_customer()
        return Contact.objects.filter(Q(customer=customer) | Q(account__customers=customer))

    def perform_create(self, serializer):
        serializer.save(customer=self.get_customer())


class AccountContactListView(generics.ListCreateAPIView):
    """GET/POST /api/v1/customers/<customer_id>/accounts/<account_id>/contacts/
    — every account-level Contact for one Account (GET), or adds a new
    one to it (POST), scoped to both its customer_id and the caller's
    own organisation. Same reasoning as AccountActivityListView/
    AccountListCreateView. Powers the standalone Account page's own
    Contacts tab."""

    serializer_class = ContactSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_account(self):
        return get_visible_account(
            self.request, self.kwargs["customer_id"], self.kwargs["account_id"]
        )

    def get_queryset(self):
        return self.get_account().contacts.all()

    def perform_create(self, serializer):
        serializer.save(account=self.get_account())


class ContactListView(generics.ListAPIView):
    """GET /api/v1/contacts/ — every Contact across every Customer/
    Account the caller's own organisation owns, organization-level and
    account-level alike. Powers the standalone Contacts page
    (react-ts-app's /contacts/list) — the one place a Contact is
    browsed independent of which Customer/Account it belongs to, so
    this is the one Contact view that isn't nested under
    /customers/<id>/... (mounted directly at /api/v1/contacts/ in the
    project's root urls.py instead).

    Paginated with the shared DEFAULT_PAGINATION_CLASS/PAGE_SIZE —
    unlike every other List view in this file, which turns pagination
    off for what's normally a single entity's already-small nested
    list — since this one can span every contact the tenant has, same
    reasoning as CustomerListCreateView.

    `?search=` matches name/email/role (substring, case-insensitive),
    same convention as CustomerListCreateView's own search. `?company=
    <customer_id>` filters to one company, matching a contact directly
    on that Customer or on any of its Accounts."""

    serializer_class = ContactSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # `.distinct()` because `account__customers__organisation=` fans
        # out one row per matching linked Customer on the account — an
        # account-level Contact whose Account is linked to two+
        # Customers in this same organisation would otherwise appear
        # more than once.
        queryset = (
            Contact.objects.filter(visible_children_q(self.request.user))
            .select_related("customer", "account")
            .prefetch_related("account__customers")
            .distinct()
        )

        search = self.request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(email__icontains=search) | Q(role__icontains=search)
            )

        company = self.request.query_params.get("company")
        if company:
            try:
                company_id = int(company)
            except ValueError:
                company_id = None
            if company_id is not None:
                queryset = queryset.filter(
                    Q(customer_id=company_id) | Q(account__customers__id=company_id)
                )

        return queryset


class ContactStatsView(views.APIView):
    """GET /api/v1/contacts/stats/ — aggregate rollups for the
    standalone Contacts page's MetricsPanel (Total/Active/Sentiment/
    Growth cards), scoped to the caller's own organisation, across
    every Contact (org-level and account-level alike) — same
    "spans everything, not just the current page" reasoning as
    CustomerStatsView.

    `growth_30d_pct` compares today's total against the total as of 30
    days ago (contacts whose `created_at` already predates the
    cutoff) — the only "growth" there's real data for; there's no
    historical daily-snapshot table to compare a true count-30-days-ago
    against anything richer. `None` (not 0) when there were no
    contacts yet 30 days ago, since a percentage change off a zero base
    is undefined, not zero."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        # `.distinct()` — same "an Account linked to two+ Customers in
        # this organisation would otherwise fan out" reason as
        # ContactListView's own.
        contacts = Contact.objects.filter(visible_children_q(self.request.user)).distinct()

        total = contacts.count()
        active = contacts.filter(status=Contact.Status.ACTIVE).count()

        sentiment_counts = {
            sentiment: contacts.filter(sentiment=sentiment).count()
            for sentiment in Contact.Sentiment.values
        }
        sentiment_pct = {
            sentiment: (round(count / total * 100) if total else 0)
            for sentiment, count in sentiment_counts.items()
        }

        cutoff = timezone.now() - timedelta(days=30)
        total_30d_ago = contacts.filter(created_at__lte=cutoff).count()
        growth_30d_pct = (
            round((total - total_30d_ago) / total_30d_ago * 100, 1) if total_30d_ago else None
        )

        return Response(
            {
                "total": total,
                "active": active,
                "sentiment": sentiment_counts,
                "sentiment_pct": sentiment_pct,
                "growth_30d_pct": growth_30d_pct,
            }
        )


class ContactDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/v1/contacts/<id>/ — a single Contact,
    scoped to the caller's own organisation (via either its `customer`
    or its `account`'s own `customers`, same reasoning as ContactListView's
    own queryset) regardless of whether it's an organization-level or
    account-level Contact. 404 (not 403) outside that scope.

    Deliberately flat (not nested under /customers/<id>/... like the
    two ListCreateAPIViews above) — Edit/Delete on any of the three
    Contacts UIs (standalone list, Organization Details, standalone
    Account page) only ever needs the Contact's own id, never its
    parent's, so there's no reason to make the caller thread a
    customer_id/account_id it may not even have on hand (the
    standalone /contacts/list page's own rows don't carry an
    account_id, only companies/account_name for display).

    PATCH can't move a Contact between parents — `customer`/`account`
    aren't in ContactSerializer's own `fields` list at all, so a PATCH
    body naming either is silently ignored rather than erroring."""

    serializer_class = ContactSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # `.distinct()` — same fan-out reasoning as ContactListView's own.
        return Contact.objects.filter(visible_children_q(self.request.user)).distinct()


class CustomerOpportunityListView(generics.ListCreateAPIView):
    """GET/POST /api/v1/customers/<customer_id>/opportunities/ — same
    shape as CustomerContactListView: GET rolls up every Opportunity
    under this Customer, both organisation-level (directly on it) and
    account-level (on any of its Accounts); POST always adds an
    organisation-level one, `customer` taken from the URL. An
    account-level Opportunity is added via AccountOpportunityListView
    below instead. Scoped to the caller's own organisation."""

    serializer_class = OpportunitySerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_customer(self):
        return get_visible_customer(self.request, self.kwargs["customer_id"])

    def get_queryset(self):
        customer = self.get_customer()
        return Opportunity.objects.filter(Q(customer=customer) | Q(account__customers=customer))

    def perform_create(self, serializer):
        serializer.save(customer=self.get_customer())


class AccountOpportunityListView(generics.ListCreateAPIView):
    """GET/POST /api/v1/customers/<customer_id>/accounts/<account_id>/opportunities/
    — every account-level Opportunity for one Account (GET), or adds a
    new one to it (POST); `account` taken from the URL. Same reasoning
    as AccountContactListView."""

    serializer_class = OpportunitySerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_account(self):
        return get_visible_account(
            self.request, self.kwargs["customer_id"], self.kwargs["account_id"]
        )

    def get_queryset(self):
        return self.get_account().opportunities.all()

    def perform_create(self, serializer):
        serializer.save(account=self.get_account())


class OpportunityListView(generics.ListCreateAPIView):
    """GET/POST /api/v1/opportunities/ — every Opportunity across every
    Customer/Account the caller's own organisation owns, organisation-
    level and account-level alike. Powers the standalone Pipelines
    board's own "Opportunities" tab (react-ts-app's
    src/pages/pipelines/PipelinesPage.tsx) — the one place an
    Opportunity is browsed independent of which Customer/Account it
    belongs to, same reasoning as ContactListView.

    Unlike ContactListView, pagination is off here — a Kanban board
    needs every card in every column to render/drag-and-drop
    correctly, not one page of them; there's no reasonable way to
    paginate a board and keep every column complete.

    POST takes a `customer_id` or an `account_id` in the request body
    (neither is a real serializer field — `perform_create` below reads
    whichever one was sent directly off the raw request) and creates
    the Opportunity under that parent — the standalone board's own "Add
    Opportunity" picks a company (and optionally one of its accounts)
    the same way the standalone Contacts page's "Add Contact" does.
    Exactly one of the two must be given, same invariant as the model's
    own CheckConstraint."""

    serializer_class = OpportunitySerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        # `.distinct()` — same fan-out reasoning as ContactListView's own.
        return (
            Opportunity.objects.filter(visible_children_q(self.request.user))
            .select_related("customer", "account")
            .prefetch_related("account__customers")
            .distinct()
        )

    def perform_create(self, serializer):
        account_id = self.request.data.get("account_id")
        customer_id = self.request.data.get("customer_id")
        if account_id:
            # `.distinct()` before `get_object_or_404` — an Account
            # linked to two+ Customers in this organisation would
            # otherwise fan out into more than one row for the *same*
            # pk, which `.get()` (what get_object_or_404 calls) treats
            # as MultipleObjectsReturned rather than a single match.
            account = get_object_or_404(visible_accounts(self.request.user), pk=account_id)
            serializer.save(account=account)
        elif customer_id:
            customer = get_object_or_404(visible_customers(self.request.user), pk=customer_id)
            serializer.save(customer=customer)
        else:
            raise ValidationError("Provide either customer_id or account_id.")


class OpportunityDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/v1/opportunities/<id>/ — a single
    Opportunity, scoped to the caller's own organisation, regardless of
    whether it's organisation-level or account-level. Flat, not nested
    — same reasoning as ContactDetailView. Powers both the board's
    drag-and-drop (PATCH `stage`) and its Edit/Delete card actions."""

    serializer_class = OpportunitySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # `.distinct()` — same fan-out reasoning as ContactDetailView's own.
        return Opportunity.objects.filter(visible_children_q(self.request.user)).distinct()


class CustomerRiskListView(generics.ListCreateAPIView):
    """GET/POST /api/v1/customers/<customer_id>/risks/ — same shape as
    CustomerOpportunityListView: GET rolls up every Risk under this
    Customer, both organisation-level (directly on it) and
    account-level (on any of its Accounts); POST always adds an
    organisation-level one, `customer` taken from the URL. An
    account-level Risk is added via AccountRiskListView below instead.
    Scoped to the caller's own organisation."""

    serializer_class = RiskSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_customer(self):
        return get_visible_customer(self.request, self.kwargs["customer_id"])

    def get_queryset(self):
        customer = self.get_customer()
        return Risk.objects.filter(Q(customer=customer) | Q(account__customers=customer))

    def perform_create(self, serializer):
        serializer.save(customer=self.get_customer())


class AccountRiskListView(generics.ListCreateAPIView):
    """GET/POST /api/v1/customers/<customer_id>/accounts/<account_id>/risks/
    — every account-level Risk for one Account (GET), or adds a new one
    to it (POST); `account` taken from the URL. Same reasoning as
    AccountOpportunityListView."""

    serializer_class = RiskSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_account(self):
        return get_visible_account(
            self.request, self.kwargs["customer_id"], self.kwargs["account_id"]
        )

    def get_queryset(self):
        return self.get_account().risks.all()

    def perform_create(self, serializer):
        serializer.save(account=self.get_account())


class RiskListView(generics.ListCreateAPIView):
    """GET/POST /api/v1/risks/ — every Risk across every Customer/
    Account the caller's own organisation owns, organisation-level and
    account-level alike. Powers the standalone Pipelines board's own
    "Risks" tab (react-ts-app's src/pages/pipelines/PipelinesPage.tsx)
    — the one place a Risk is browsed independent of which Customer/
    Account it belongs to, same reasoning as OpportunityListView.

    Unpaginated for the same reason as OpportunityListView — a Kanban
    board needs every card in every column to render/drag-and-drop
    correctly, not one page of them.

    POST takes a `customer_id` or an `account_id` in the request body
    (neither is a real serializer field — `perform_create` below reads
    whichever one was sent directly off the raw request) and creates
    the Risk under that parent. Exactly one of the two must be given,
    same invariant as the model's own CheckConstraint."""

    serializer_class = RiskSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        # `.distinct()` — same fan-out reasoning as ContactListView's own.
        return (
            Risk.objects.filter(visible_children_q(self.request.user))
            .select_related("customer", "account")
            .prefetch_related("account__customers")
            .distinct()
        )

    def perform_create(self, serializer):
        account_id = self.request.data.get("account_id")
        customer_id = self.request.data.get("customer_id")
        if account_id:
            # `.distinct()` before `get_object_or_404` — same
            # reasoning as OpportunityListView.perform_create's own.
            account = get_object_or_404(visible_accounts(self.request.user), pk=account_id)
            serializer.save(account=account)
        elif customer_id:
            customer = get_object_or_404(visible_customers(self.request.user), pk=customer_id)
            serializer.save(customer=customer)
        else:
            raise ValidationError("Provide either customer_id or account_id.")


class RiskDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/v1/risks/<id>/ — a single Risk, scoped to
    the caller's own organisation, regardless of whether it's
    organisation-level or account-level. Flat, not nested — same
    reasoning as OpportunityDetailView. Powers both the board's
    drag-and-drop (PATCH `stage`) and its Edit/Delete card actions."""

    serializer_class = RiskSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # `.distinct()` — same fan-out reasoning as ContactDetailView's own.
        return Risk.objects.filter(visible_children_q(self.request.user)).distinct()


# Maps a Survey's own `survey_type` to the Customer/Account field its score
# syncs onto once responded — see SurveyDetailView.perform_update below,
# the one place this whole feature's real behavior lives.
_SURVEY_SCORE_FIELD = {
    Survey.SurveyType.NPS: "nps_score",
    Survey.SurveyType.CSAT: "csat_score",
    Survey.SurveyType.CES: "ces_percentage",
}


def _reject_ces_for_account(serializer):
    # Account has no ces_percentage field (see Survey model's own
    # docstring on this real, pre-existing asymmetry) — a CES survey
    # would have nowhere to sync its score once responded, so it's
    # rejected at creation rather than silently accepted and later
    # having its response go nowhere.
    if serializer.validated_data.get("survey_type") == Survey.SurveyType.CES:
        raise ValidationError(
            {
                "survey_type": "CES surveys aren't supported for Accounts — "
                "log this against the parent Organization instead."
            }
        )


class CustomerSurveyListView(generics.ListCreateAPIView):
    """GET/POST /api/v1/customers/<customer_id>/surveys/ — same shape as
    CustomerOpportunityListView: GET rolls up every Survey under this
    Customer, both organisation-level (directly on it) and
    account-level (on any of its Accounts); POST always adds an
    organisation-level one, `customer` taken from the URL. An
    account-level Survey is added via AccountSurveyListView below
    instead. Powers the Activity Feed's own "Surveys" filter. Scoped to
    the caller's own organisation."""

    serializer_class = SurveySerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_customer(self):
        return get_visible_customer(self.request, self.kwargs["customer_id"])

    def get_queryset(self):
        customer = self.get_customer()
        return Survey.objects.filter(Q(customer=customer) | Q(account__customers=customer))

    def perform_create(self, serializer):
        serializer.save(customer=self.get_customer())


class AccountSurveyListView(generics.ListCreateAPIView):
    """GET/POST /api/v1/customers/<customer_id>/accounts/<account_id>/surveys/
    — every account-level Survey for one Account (GET), or adds a new
    one to it (POST); `account` taken from the URL. Same reasoning as
    AccountOpportunityListView. Rejects survey_type=CES — see
    _reject_ces_for_account's own docstring."""

    serializer_class = SurveySerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_account(self):
        return get_visible_account(
            self.request, self.kwargs["customer_id"], self.kwargs["account_id"]
        )

    def get_queryset(self):
        return self.get_account().surveys.all()

    def perform_create(self, serializer):
        _reject_ces_for_account(serializer)
        serializer.save(account=self.get_account())


class SurveyListView(generics.ListCreateAPIView):
    """GET/POST /api/v1/surveys/ — every Survey across every Customer/
    Account the caller's own organisation owns, organisation-level and
    account-level alike. Powers the standalone Surveys page
    (react-ts-app's src/pages/surveys/SurveysPage.tsx) — the one place
    a Survey is browsed independent of which Customer/Account it
    belongs to, same reasoning as OpportunityListView. Its own rollup
    cards (response rate / average score per type) are computed
    client-side from this same unpaginated list, same approach
    PipelinesPage.tsx's own "Pipelines Overview" banner already uses —
    no separate stats endpoint.

    Unpaginated for the same reason as OpportunityListView — the
    rollup page needs every record to compute real totals from, not
    one page of them.

    POST takes a `customer_id` or an `account_id` in the request body
    (neither is a real serializer field — `perform_create` below reads
    whichever one was sent directly off the raw request) and creates
    the Survey under that parent. Exactly one of the two must be
    given, same invariant as the model's own CheckConstraint. Rejects
    survey_type=CES for an account_id — see _reject_ces_for_account's
    own docstring."""

    serializer_class = SurveySerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        # `.distinct()` — same fan-out reasoning as ContactListView's own.
        return (
            Survey.objects.filter(visible_children_q(self.request.user))
            .select_related("customer", "account")
            .prefetch_related("account__customers")
            .distinct()
        )

    def perform_create(self, serializer):
        account_id = self.request.data.get("account_id")
        customer_id = self.request.data.get("customer_id")
        if account_id:
            _reject_ces_for_account(serializer)
            # `.distinct()` before `get_object_or_404` — same
            # reasoning as OpportunityListView.perform_create's own.
            account = get_object_or_404(visible_accounts(self.request.user), pk=account_id)
            serializer.save(account=account)
        elif customer_id:
            customer = get_object_or_404(visible_customers(self.request.user), pk=customer_id)
            serializer.save(customer=customer)
        else:
            raise ValidationError("Provide either customer_id or account_id.")


class SurveyDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/v1/surveys/<id>/ — a single Survey, scoped
    to the caller's own organisation, regardless of whether it's
    organisation-level or account-level. Flat, not nested — same
    reasoning as OpportunityDetailView. PATCH is "Log Response"
    (`status`/`score`) as well as any other edit; DELETE removes it
    outright (no soft-delete concept here, unlike Customer's own
    is_archived).

    perform_update is the one place in this whole feature that syncs a
    responded Survey's score onto its parent Customer/Account's own
    nps_score/csat_score/ces_percentage field — see _SURVEY_SCORE_FIELD
    and the Survey model's own docstring for why that's deliberately
    centralized here rather than duplicated at every write path."""

    serializer_class = SurveySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # `.distinct()` — same fan-out reasoning as ContactDetailView's own.
        return Survey.objects.filter(visible_children_q(self.request.user)).distinct()

    def perform_update(self, serializer):
        survey = serializer.save()
        if survey.status == Survey.Status.RESPONDED and survey.score is not None:
            parent = survey.customer or survey.account
            field = _SURVEY_SCORE_FIELD[survey.survey_type]
            setattr(parent, field, survey.score)
            parent.save(update_fields=[field])


class CustomerCanvasListView(generics.ListCreateAPIView):
    """GET/POST /api/v1/customers/<customer_id>/canvases/ — same shape as
    CustomerSurveyListView: GET rolls up every Canvas under this
    Customer, both organisation-level and account-level (any of its
    Accounts' own); POST always adds an organisation-level one,
    `customer` taken from the URL. Powers the "Canvas List" tab on the
    Organization Details page. Scoped to the caller's own organisation."""

    serializer_class = CanvasSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_customer(self):
        return get_visible_customer(self.request, self.kwargs["customer_id"])

    def get_queryset(self):
        customer = self.get_customer()
        return Canvas.objects.filter(Q(customer=customer) | Q(account__customers=customer))

    def perform_create(self, serializer):
        serializer.save(customer=self.get_customer())


class AccountCanvasListView(generics.ListCreateAPIView):
    """GET/POST /api/v1/customers/<customer_id>/accounts/<account_id>/canvases/
    — every account-level Canvas for one Account (GET), or adds a new
    one to it (POST); `account` taken from the URL. Powers the "Canvas
    List" tab on the standalone Account page."""

    serializer_class = CanvasSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_account(self):
        return get_visible_account(
            self.request, self.kwargs["customer_id"], self.kwargs["account_id"]
        )

    def get_queryset(self):
        return self.get_account().canvases.all()

    def perform_create(self, serializer):
        serializer.save(account=self.get_account())


class CanvasListView(generics.ListCreateAPIView):
    """GET/POST /api/v1/canvases/ — every Canvas across every Customer/
    Account the caller's own organisation owns. Powers the standalone
    Canvas gallery (react-ts-app's src/pages/canvas/CanvasPage.tsx) —
    the one place a Canvas is browsed independent of which Customer/
    Account it belongs to, same reasoning as SurveyListView. Unpaginated
    for the same reason — the gallery needs every record, not one page.

    POST takes a `customer_id` or an `account_id` in the request body
    (neither is a real serializer field — `perform_create` below reads
    whichever one was sent directly off the raw request) and creates
    the Canvas under that parent. Exactly one of the two must be given,
    same invariant as the model's own CheckConstraint."""

    serializer_class = CanvasSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        # `.distinct()` — same fan-out reasoning as SurveyListView's own.
        return (
            Canvas.objects.filter(visible_children_q(self.request.user))
            .select_related("customer", "account")
            .prefetch_related("account__customers")
            .distinct()
        )

    def perform_create(self, serializer):
        account_id = self.request.data.get("account_id")
        customer_id = self.request.data.get("customer_id")
        if account_id:
            # `.distinct()` before `get_object_or_404` — same reasoning
            # as SurveyListView.perform_create's own.
            account = get_object_or_404(visible_accounts(self.request.user), pk=account_id)
            serializer.save(account=account)
        elif customer_id:
            customer = get_object_or_404(visible_customers(self.request.user), pk=customer_id)
            serializer.save(customer=customer)
        else:
            raise ValidationError("Provide either customer_id or account_id.")


class CanvasDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/v1/canvases/<id>/ — a single Canvas, scoped
    to the caller's own organisation, regardless of whether it's
    organisation-level or account-level. Flat, not nested — same
    reasoning as SurveyDetailView. PATCH is a plain save (name/nodes/
    edges) — unlike Survey, there's no parent field to sync on update."""

    serializer_class = CanvasSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # `.distinct()` — same fan-out reasoning as SurveyDetailView's own.
        return Canvas.objects.filter(visible_children_q(self.request.user)).distinct()


class CockpitSummaryView(views.APIView):
    """GET /api/v1/cockpit/summary/ — real numbers for Cockpit's own
    "My Portfolio Summary"/"Renewals" tiles (react-ts-app's
    src/pages/copilot/CockpitView.tsx), which used to show fixed literal
    counts/values unrelated to any real Customer/Account. Scoped to the
    caller's own *owned* book of business — `owner=request.user` on
    Customer/Account — not the whole tenant's, same "My" framing as the
    panel's own heading and as TaskListView's own `?mine=true`.

    `customers`/`accounts` mirror CustomerStatsView/AccountStatsView's
    own health-bucketing and FX-conversion rules exactly (Customer's own
    `arr_billed_at_hq` converted via convert_to_org_currency, excluded
    rather than mis-summed when unconvertible; Account's own `arr` used
    as-is, no FX step — see AccountStatsView's own docstring for why).

    `renewals` reuses CustomerListCreateView's own `?renewal_within=`
    window/exclusion rules (today through +`?days=` days inclusive,
    already-churned excluded, no `renewal_date` excluded) — same real
    numbers a CSM would get by checking each list by hand. `?days=`
    defaults to 30 (any positive int; a bad value falls back to 30
    rather than erroring, same "ignore, don't 400" convention as
    `?renewal_within=`'s own). Unlike the first Tier 0 pass, this is a
    real drill-down, not just a count/value pair — `items` is every
    renewing Customer/Account, merged and sorted soonest-first, so
    Cockpit can actually show *which* ones (same "give me the real
    list, not just a stat" reasoning as `TaskListView`).

    Aggregates in Python over the caller's own rows, same reasoning as
    CustomerStatsView/AccountStatsView: health_category is a derived
    property, not a real column to GROUP BY, and this is fine at the
    scale of one CSM's own book."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        organisation = request.user.organisation
        user = request.user

        days_param = request.query_params.get("days")
        try:
            window_days = int(days_param) if days_param is not None else 30
        except ValueError:
            window_days = 30
        if window_days <= 0:
            window_days = 30
        renewal_deadline = timezone.localdate() + timedelta(days=window_days)

        customers = Customer.objects.filter(
            organisation=organisation, owner=user, is_archived=False
        )
        customer_summary = {"count": 0, "value": 0.0, "unconverted_count": 0, "health": {}}
        for cat in Customer.HealthCategory.values:
            customer_summary["health"][cat] = 0
        for customer in customers:
            customer_summary["count"] += 1
            customer_summary["health"][customer.health_category] += 1
            converted = convert_to_org_currency(
                customer.arr_billed_at_hq, customer.currency, organisation
            )
            if converted is None:
                customer_summary["unconverted_count"] += 1
            else:
                customer_summary["value"] += float(converted)
        customer_summary["value"] = round(customer_summary["value"], 2)

        # `.distinct()` — same fan-out reasoning as AccountListView's own.
        accounts = Account.objects.filter(
            customers__organisation=organisation, owner=user
        ).distinct()
        account_summary = {"count": 0, "value": 0.0, "health": {}}
        for cat in Customer.HealthCategory.values:
            account_summary["health"][cat] = 0
        for account in accounts:
            account_summary["count"] += 1
            account_summary["health"][account.health_category] += 1
            account_summary["value"] += float(account.arr)
        account_summary["value"] = round(account_summary["value"], 2)

        renewing_customers = customers.exclude(
            lifecycle_stage=Customer.LifecycleStage.CHURN
        ).filter(renewal_date__isnull=False, renewal_date__lte=renewal_deadline)
        renewing_accounts = accounts.exclude(lifecycle_stage=Customer.LifecycleStage.CHURN).filter(
            renewal_date__isnull=False, renewal_date__lte=renewal_deadline
        )

        items = []
        customer_renewals = {"count": 0, "value": 0.0}
        for customer in renewing_customers:
            converted = convert_to_org_currency(
                customer.arr_billed_at_hq, customer.currency, organisation
            )
            value = float(converted) if converted is not None else 0.0
            customer_renewals["count"] += 1
            customer_renewals["value"] += value
            items.append(
                {
                    "id": customer.id,
                    "name": customer.name,
                    "type": "customer",
                    "value": round(value, 2),
                    "renewal_date": customer.renewal_date,
                }
            )
        customer_renewals["value"] = round(customer_renewals["value"], 2)

        account_renewals = {"count": 0, "value": 0.0}
        for account in renewing_accounts:
            value = float(account.arr)
            account_renewals["count"] += 1
            account_renewals["value"] += value
            items.append(
                {
                    "id": account.id,
                    "name": account.name,
                    "type": "account",
                    "value": round(value, 2),
                    "renewal_date": account.renewal_date,
                }
            )
        account_renewals["value"] = round(account_renewals["value"], 2)

        items.sort(key=lambda item: item["renewal_date"])

        return Response(
            {
                "customers": customer_summary,
                "accounts": account_summary,
                "renewals": {
                    "window_days": window_days,
                    "customers": customer_renewals,
                    "accounts": account_renewals,
                    "items": items,
                },
            }
        )


class CustomerHeadlineListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/v1/customers/<customer_id>/headlines/ — every
    organization-level Headline for one Customer (GET), or writes one
    by hand (POST); `customer` comes from the URL. Powers the
    "Headlines" sub-tab of ActivityFeed on the Organization Details
    page. Scoped to the caller's own organisation.

    Writable, unlike the Note/Activity/Email lists: a headline is
    normally generated (see HeadlineGenerateView below), but a CSM
    writing or correcting one by hand is a real case — and a
    hand-written card is exactly what regeneration must not clobber."""

    serializer_class = HeadlineSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return get_visible_customer(self.request, self.kwargs["customer_id"]).headlines.all()

    def perform_create(self, serializer):
        serializer.save(customer=get_visible_customer(self.request, self.kwargs["customer_id"]))


class AccountHeadlineListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/v1/customers/<customer_id>/accounts/<account_id>/headlines/
    — every account-level Headline for one Account (GET), or writes one
    by hand (POST); `account` comes from the URL. Powers the
    "Headlines" sub-tab on the standalone Account page. Same reasoning
    as CustomerHeadlineListCreateView."""

    serializer_class = HeadlineSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return get_visible_account(
            self.request, self.kwargs["customer_id"], self.kwargs["account_id"]
        ).headlines.all()

    def perform_create(self, serializer):
        serializer.save(
            account=get_visible_account(
                self.request, self.kwargs["customer_id"], self.kwargs["account_id"]
            )
        )


class HeadlineDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/v1/headlines/<pk>/ — edit or remove one
    card. Flat rather than nested under its parent, same shape as
    OpportunityDetailView/RiskDetailView/CanvasDetailView: the id is
    unique on its own, and the card's own edit/delete controls have the
    Headline in hand without needing to know which of the two parent
    shapes it came from.

    Scoping is done on the queryset here rather than by a parent
    lookup — there's no parent in the URL to 404 on — matching how
    those same flat detail views scope themselves."""

    serializer_class = HeadlineSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Headline.objects.filter(visible_children_q(self.request.user)).distinct()


class HeadlineGenerateView(views.APIView):
    """POST /api/v1/customers/<customer_id>/headlines/generate/ and
    .../accounts/<account_id>/headlines/generate/ — reads the parent's
    real Notes/Emails/Tickets/Activities and writes its Headline cards
    from them, making the card footer's "Data sources" line true rather
    than decorative.

    Replaces only previously *generated* cards (`generated_at` is not
    null), never anything hand-written through the POST endpoints
    above — so regenerating is safe to run repeatedly and a CSM's own
    correction survives it.

    Error mapping matches SendMessageView's exactly, since it is the
    same one external call underneath: 503 when the provider isn't
    configured, 502 when the call itself fails. Adds a 422 for "this
    account has nothing to summarise", which is a real, expected state
    for a newly created account rather than a failure of ours."""

    permission_classes = [IsAuthenticated]

    def post(self, request, customer_id, account_id=None):
        if account_id is None:
            parent = get_visible_customer(request, customer_id)
        else:
            parent = get_visible_account(request, customer_id, account_id)

        window_days = request.data.get("window_days")
        kwargs = {}
        if window_days is not None:
            try:
                kwargs["window_days"] = int(window_days)
            except (TypeError, ValueError):
                raise ValidationError({"window_days": "Must be a whole number of days."}) from None
            if kwargs["window_days"] < 1:
                raise ValidationError({"window_days": "Must be at least 1 day."})
        if request.data.get("time_period_label"):
            kwargs["time_period_label"] = request.data["time_period_label"]

        try:
            built = generate_headlines(parent, **kwargs)
        except NothingToSummarise as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        except BudgetExceeded as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        except CopilotNotConfigured as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except CopilotRequestFailed as exc:
            return Response(
                {"detail": f"The summary couldn't be generated: {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except ValueError as exc:
            return Response(
                {"detail": f"The summary couldn't be generated: {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # One transaction so a failure mid-write can't leave the tab
        # showing the old cards deleted and the new ones missing.
        with transaction.atomic():
            parent.headlines.filter(generated_at__isnull=False).delete()
            Headline.objects.bulk_create(built)

        return Response(
            HeadlineSerializer(parent.headlines.all(), many=True).data,
            status=status.HTTP_201_CREATED,
        )


class InteractionStatsView(views.APIView):
    """GET /api/v1/interactions/stats/ — every rollup the AI Trending Topics
    dashboard's Controls tab needs, in one response.

    An *interaction* is an Email, a Call or a Ticket (see
    services/customers/interactions.py, which owns the aggregation). One
    endpoint rather than seven for the same reason TicketStatsView is
    one: the seven charts are seven views of the same filtered set, and
    splitting them would apply the same filters seven times and let the
    charts disagree with each other mid-render.

    **Unfiltered by default**, same deliberate choice and same trap as
    TicketStatsView: a rolling "last 30 days" default renders every chart
    empty once real time moves past the seeded demo dates, which looks
    like a broken integration rather than an empty window.

    Every filter ignores a bad value rather than 400ing — the house
    convention for dashboard filters. `?type=` is repeatable and drops
    whole record types; the rest narrow rows.

    Three of the seven charts count only *classified* interactions, and
    `total`/`classified` come back so the screen can say so rather than
    implying an empty AI Area donut means nobody talked about anything.
    Nothing classifies on write — `manage.py classify_interactions` does,
    and it costs a real model call per batch."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        querysets = interactions.filtered_querysets(request.user, request.query_params)
        return Response(
            {
                **interactions.build_stats(querysets),
                "recent": interactions.recent_rows(querysets),
                "filters": interactions.filter_options(request.user),
            }
        )


class TicketStatsView(views.APIView):
    """GET /api/v1/tickets/stats/ — every rollup the Ticket Overview
    dashboard's Controls tab needs, in one response.

    One endpoint rather than six because the six charts are six views
    of the same filtered set: splitting them would mean applying the
    same filters six times and risking them disagreeing with each
    other mid-render. Same "spans everything, not just the current
    page" reasoning as ContactStatsView, which this is otherwise
    modelled on.

    **Unfiltered by default, deliberately.** A rolling default window
    would be the obvious choice and is a trap here: the seeded demo
    data is fixed 2026 dates, so a "last 30 days" default renders every
    chart empty the moment real time moves past it — which looks
    exactly like a broken integration rather than an empty window.
    Callers that want a window ask for one.

    Every filter ignores a bad value rather than 400ing, the same
    convention `?renewal_within=` and `?days=` already follow. A
    dashboard should render with the filters it understood, not refuse
    to draw because one dropdown sent something odd.

    Colours are not in this response. Every real-data chart in this
    frontend maps its own name -> CSS variable (see SurveyTrendChart);
    only the mock this replaces carried `fill` in its data."""

    permission_classes = [IsAuthenticated]

    def _filtered_tickets(self, request):
        """Visibility first, then the caller's filters narrow from
        there. Applying a raw `?account=` id before the visibility gate
        is what previously let members read other owners' custom-object
        records — see CustomObjectRecordListCreateView's own note."""

        tickets = Ticket.objects.filter(visible_children_q(request.user)).distinct()
        params = request.query_params

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
            tickets = tickets.filter(
                Q(customer_id=customer_id) | Q(account__customers__id=customer_id)
            )

        account_id = _parse_int(params.get("account"))
        if account_id is not None:
            tickets = tickets.filter(account_id=account_id)

        connector_id = _parse_int(params.get("connector"))
        if connector_id is not None:
            tickets = tickets.filter(connector_id=connector_id)

        return tickets.distinct()

    def get(self, request):
        tickets = self._filtered_tickets(request)
        total = tickets.count()

        resolved = tickets.filter(status__in=Ticket.RESOLVED_STATUSES)
        lifetimes = [
            (t.resolved_at - t.opened_at).days
            for t in resolved.exclude(resolved_at__isnull=True).only("resolved_at", "opened_at")
        ]

        kpis = {
            "total": total,
            "on_hold": tickets.filter(status=Ticket.Status.ON_HOLD).count(),
            # None, not 0, when nothing has been resolved — an average
            # of no samples is undefined, and 0 days would read as
            # "everything closed instantly". Same convention as
            # ContactStatsView's own growth_30d_pct.
            "avg_lifetime_days": (round(sum(lifetimes) / len(lifetimes), 2) if lifetimes else None),
            "resolution_rate": round(resolved.count() / total * 100, 2) if total else 0,
            "positive_sentiment": tickets.filter(sentiment=Ticket.Sentiment.POSITIVE).count(),
            "negative_sentiment": tickets.filter(sentiment=Ticket.Sentiment.NEGATIVE).count(),
        }

        # Every bucket pre-seeded so an absent one comes back as a zero
        # row rather than a missing key — a donut with a vanishing
        # segment is harder to read than one with an empty one.
        #
        # `.order_by()` before each annotate is load-bearing, not tidying:
        # Ticket.Meta.ordering is ["-opened_at", "-id"], and Django folds
        # a model's default ordering into the GROUP BY. Grouping by
        # (priority, opened_at, id) makes every bucket count exactly 1,
        # which is what these returned before the empty order_by.
        priority_counts = dict(
            tickets.order_by()
            .values_list("priority")
            .annotate(n=Count("id"))
            .values_list("priority", "n")
        )
        status_counts = dict(
            tickets.order_by()
            .values_list("status")
            .annotate(n=Count("id"))
            .values_list("status", "n")
        )

        origin = [
            {
                "name": row["connector__name"] or "Revenact",
                "value": row["n"],
                "provider": row["connector__provider"] or "",
                "connector_id": row["connector_id"],
            }
            for row in tickets.order_by()
            .values("connector_id", "connector__name", "connector__provider")
            .annotate(n=Count("id"))
            .order_by("-n")
        ]

        return Response(
            {
                "kpis": kpis,
                "priority": [
                    {"name": label, "value": priority_counts.get(value, 0)}
                    for value, label in Ticket.Priority.choices
                ],
                "status": [
                    {"name": label, "value": status_counts.get(value, 0)}
                    for value, label in Ticket.Status.choices
                ],
                "origin": origin,
                "assignees": _assignee_breakdown(tickets),
                "sentiment_timeline": _sentiment_timeline(tickets),
                "filters": _filter_options(request.user),
            }
        )


def _parse_int(raw):
    """Shared by every id-shaped filter above. Returns None for
    anything unparseable, which the caller treats as "no filter" —
    this module's own ignore-don't-400 convention."""
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _parse_date(raw):
    """`YYYY-MM-DD` only. Anything else is ignored rather than 400ing,
    same as _parse_int."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _assignee_breakdown(tickets):
    """One row per assignee, counts keyed by the status's *display*
    label, plus a precomputed `total`.

    Display labels rather than raw values because the chart stacks its
    bars by these keys and renders them in its legend — and `total` is
    precomputed because the chart draws it through an invisible bar
    rather than deriving it.

    Ordered biggest-first, then reversed: the chart is a horizontal
    Recharts bar chart, which draws its first row at the bottom."""

    labels = dict(Ticket.Status.choices)
    rows = {}
    for assignee, status_value, count in (
        tickets.order_by()
        .values_list("assignee_name", "status")
        .annotate(n=Count("id"))
        .values_list("assignee_name", "status", "n")
    ):
        row = rows.setdefault(
            assignee, {"name": assignee, **{label: 0 for label in labels.values()}, "total": 0}
        )
        row[labels[status_value]] = count
        row["total"] += count

    ordered = sorted(rows.values(), key=lambda r: r["total"], reverse=True)
    return list(reversed(ordered))


def _sentiment_timeline(tickets):
    """Positive/negative counts per calendar month of `opened_at`.

    Bucketed on `opened_at` rather than `created_at`: `created_at` is
    auto_now_add, so every seeded row shares one timestamp and a trend
    over it would be a single spike. Months with no tickets are left
    out rather than back-filled with zeros — the gap is the truth, and
    inventing a zero month implies data was checked and found empty.

    `date` is a pre-formatted label ("Jun 2026") because the chart
    renders it verbatim as a category tick."""

    buckets = {}
    for month, sentiment, count in (
        tickets.order_by()
        .annotate(month=TruncMonth("opened_at"))
        .values_list("month", "sentiment")
        .annotate(n=Count("id"))
        .values_list("month", "sentiment", "n")
    ):
        bucket = buckets.setdefault(month, {"positive": 0, "negative": 0})
        if sentiment in bucket:
            bucket[sentiment] = count

    return [
        {"date": month.strftime("%b %Y"), "positive": v["positive"], "negative": v["negative"]}
        for month, v in sorted(buckets.items())
    ]


def _filter_options(user):
    """The dropdown options for the filter bar, shipped alongside the
    numbers so it doesn't need a second round trip. Scoped the same way
    the stats are, so a CSM can't filter by a company they can't see."""

    customers = visible_customers(user)
    accounts = visible_accounts(user)
    owner_ids = set(customers.values_list("owner_id", flat=True)) | set(
        accounts.values_list("owner_id", flat=True)
    )

    return {
        "owners": [
            {"id": u.id, "name": u.name}
            for u in User.objects.filter(id__in=owner_ids - {None}).order_by("name")
        ],
        "customers": [{"id": c.id, "name": c.name} for c in customers.order_by("name")],
        "accounts": [{"id": a.id, "name": a.name} for a in accounts.order_by("name")],
        "connectors": [
            {"id": c.id, "name": c.name, "provider": c.provider}
            for c in Connector.objects.filter(organisation=user.organisation).order_by("name")
        ],
        "priorities": [{"value": v, "name": label} for v, label in Ticket.Priority.choices],
    }
