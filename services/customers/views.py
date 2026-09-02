from datetime import timedelta

from django.db.models import CharField, Q
from django.db.models.functions import Cast
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, views
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Account, Customer
from .serializers import AccountSerializer, ActivitySerializer, CustomerSerializer


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
        queryset = Customer.objects.filter(
            organisation=self.request.user.organisation, is_archived=False
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
        customers = Customer.objects.filter(
            organisation=request.user.organisation, is_archived=False
        )

        health = {
            cat: {"count": 0, "mrr": 0.0, "arr": 0.0} for cat in Customer.HealthCategory.values
        }
        lifecycle = {
            stage: {"count": 0, "mrr": 0.0, "arr": 0.0} for stage in Customer.LifecycleStage.values
        }
        promoters = passives = detractors = 0
        scored = 0

        for customer in customers:
            arr = float(customer.arr_billed_at_account)
            mrr = arr / 12

            health_bucket = health[customer.health_category]
            health_bucket["count"] += 1
            health_bucket["mrr"] += mrr
            health_bucket["arr"] += arr

            lifecycle_bucket = lifecycle[customer.lifecycle_stage]
            lifecycle_bucket["count"] += 1
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
            }
        )


class CustomerDetailView(generics.RetrieveUpdateAPIView):
    """GET/PATCH /api/v1/customers/<id>/ — scoped to the caller's own
    organisation. 404, not 403, for a customer outside that scope."""

    serializer_class = CustomerSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Customer.objects.filter(organisation=self.request.user.organisation)


class AccountListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/v1/customers/<customer_id>/accounts/ — every Account
    under one Customer (GET), or adds a new one to it (POST), scoped to
    the caller's own organisation. `customer` is never client-supplied —
    taken from the URL and validated against the caller's org before
    either operation.

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
        return get_object_or_404(
            Customer, pk=self.kwargs["customer_id"], organisation=self.request.user.organisation
        )

    def get_queryset(self):
        return self.get_customer().accounts.all()

    def perform_create(self, serializer):
        serializer.save(customer=self.get_customer())


class AccountDetailView(generics.RetrieveUpdateAPIView):
    """GET/PATCH /api/v1/customers/<customer_id>/accounts/<id>/ — scoped
    to the caller's own organisation and the given customer_id. 404, not
    403, for either id outside that scope. See AccountListCreateView's
    docstring for what's actually editable."""

    serializer_class = AccountSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Account.objects.filter(
            customer_id=self.kwargs["customer_id"],
            customer__organisation=self.request.user.organisation,
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
        customer = get_object_or_404(
            Customer, pk=self.kwargs["customer_id"], organisation=self.request.user.organisation
        )
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
        account = get_object_or_404(
            Account,
            pk=self.kwargs["account_id"],
            customer_id=self.kwargs["customer_id"],
            customer__organisation=self.request.user.organisation,
        )
        return account.activities.all()
