from datetime import timedelta

from django.db.models import CharField, Q
from django.db.models.functions import Cast
from django.utils import timezone
from rest_framework import generics, views
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Customer
from .serializers import CustomerSerializer


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
