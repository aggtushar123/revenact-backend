from rest_framework import generics
from rest_framework.permissions import IsAuthenticated

from services.accounts.permissions import CanManageIntegrations

from .models import Connector
from .serializers import ConnectorSerializer


class ConnectorListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/v1/connectors/ — every Connector the caller's own
    organisation has set up.

    Unlike Webhooks, which is admin-only both ways, **GET is open to
    any member**: the Ticket Overview dashboard's "Tickets By Origin"
    chart labels its bars with connector names, and its origin filter
    lists them, so a CSM has to be able to read them. There's nothing
    credential-adjacent to protect here — a Connector deliberately
    stores no secrets (see the model's own docstring).

    Writing still needs `manage_integrations`, the same capability
    Webhooks uses; connecting a system is org configuration, not
    everyday work."""

    serializer_class = ConnectorSerializer
    pagination_class = None

    def get_permissions(self):
        if self.request.method == "POST":
            return [CanManageIntegrations()]
        return [IsAuthenticated()]

    def get_queryset(self):
        return (
            Connector.objects.filter(organisation=self.request.user.organisation)
            .prefetch_related("customers", "accounts")
            .distinct()
        )

    def perform_create(self, serializer):
        serializer.save(organisation=self.request.user.organisation)


class ConnectorDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/v1/connectors/<id>/ — scoped to the
    caller's own organisation. Same read-open/write-gated split as the
    list view above.

    DELETE leaves the tickets that came from it intact: `Ticket.
    connector` is SET_NULL, so removing a connector makes its tickets
    look manually raised rather than deleting real support history."""

    serializer_class = ConnectorSerializer

    def get_permissions(self):
        if self.request.method in ("PATCH", "PUT", "DELETE"):
            return [CanManageIntegrations()]
        return [IsAuthenticated()]

    def get_queryset(self):
        return (
            Connector.objects.filter(organisation=self.request.user.organisation)
            .prefetch_related("customers", "accounts")
            .distinct()
        )
