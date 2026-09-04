from rest_framework import generics

from services.accounts.permissions import IsOrgAdmin

from .models import FxRate
from .serializers import FxRateSerializer


class FxRateListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/v1/fx-rates/ — every FxRate the caller's own
    organisation has configured. Admin-only both ways, same reasoning
    as WebhookListCreateView's own: exchange rates are financial
    config, not everyday customer data."""

    serializer_class = FxRateSerializer
    permission_classes = [IsOrgAdmin]
    pagination_class = None

    def get_queryset(self):
        return FxRate.objects.filter(organisation=self.request.user.organisation)

    def perform_create(self, serializer):
        serializer.save(organisation=self.request.user.organisation)


class FxRateDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/v1/fx-rates/<id>/ — scoped to the caller's
    own organisation, admin-only. PATCH is mainly for updating
    `rate_to_org_currency` as real-world rates move."""

    serializer_class = FxRateSerializer
    permission_classes = [IsOrgAdmin]

    def get_queryset(self):
        return FxRate.objects.filter(organisation=self.request.user.organisation)
