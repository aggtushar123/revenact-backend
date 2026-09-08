from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from services.customers.scoping import visible_customers

from .engine import run_scenario
from .models import Scenario, ScenarioRun
from .serializers import ScenarioRunSerializer, ScenarioSerializer


class ScenarioListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/v1/scenarios/ — every Scenario the caller's own
    organisation owns. Powers /scenarios and /scenarios/create.
    Pagination off — same reasoning as OpportunityListView/RiskListView:
    a small, whole-collection list, not one meant to be paged through."""

    serializer_class = ScenarioSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return Scenario.objects.filter(organisation=self.request.user.organisation)

    def perform_create(self, serializer):
        serializer.save(organisation=self.request.user.organisation)


class ScenarioDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/v1/scenarios/<id>/ — scoped to the caller's
    own organisation. 404, not 403, for a scenario outside that scope,
    same convention as every other detail view in this codebase."""

    serializer_class = ScenarioSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Scenario.objects.filter(organisation=self.request.user.organisation)


class ScenarioRunView(APIView):
    """POST /api/v1/scenarios/<id>/run/ — the builder's "Run Now" button.
    Body: `{"customer_id": <id>}`. Only `apply_to == "organizations"`
    scenarios are runnable in v1 (see engine.py's own docstring on why);
    everything else 400s with a message the frontend surfaces directly
    rather than silently disabling the button for reasons the user can't
    see. Runs synchronously — there's no task queue in this codebase, so
    the response IS the completed run, log and all."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        scenario = get_object_or_404(Scenario, pk=pk, organisation=request.user.organisation)
        if scenario.apply_to != Scenario.ApplyTo.ORGANIZATIONS:
            return Response(
                {"detail": "Only Organizations scenarios can be run right now."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Scoped to what the caller can actually see, not just to the
        # tenant. A run isn't a read: the engine really does write
        # lifecycle_stage/churn_date and send mail (see engine.py), so
        # an ungated target let any member churn an account they
        # couldn't even open.
        customer_id = request.data.get("customer_id")
        customer = get_object_or_404(visible_customers(request.user), pk=customer_id)

        run = run_scenario(scenario, customer, triggered_by=ScenarioRun.TriggeredBy.MANUAL)
        return Response(ScenarioRunSerializer(run).data, status=status.HTTP_201_CREATED)


class ScenarioRunListView(generics.ListAPIView):
    """GET /api/v1/scenarios/<id>/runs/ — this scenario's own run
    history, newest first (Meta.ordering on ScenarioRun), scoped to the
    caller's own organisation via the parent Scenario lookup."""

    serializer_class = ScenarioRunSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        scenario = get_object_or_404(
            Scenario, pk=self.kwargs["pk"], organisation=self.request.user.organisation
        )
        # The Scenario itself is a tenant-wide automation and stays
        # visible to everyone, but its runs are per-customer: each row
        # names the customer and its `log` quotes contact addresses
        # ("Emailed x@y: ..."), so the history is filtered even though
        # the scenario isn't.
        #
        # `customer` is nullable (SET_NULL — the customer may since have
        # been deleted). Such a run names nobody, so it has nothing to
        # hide and stays in the list rather than vanishing.
        return scenario.runs.filter(
            Q(customer__in=visible_customers(self.request.user)) | Q(customer__isnull=True)
        )
