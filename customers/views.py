from rest_framework import generics
from rest_framework.permissions import IsAuthenticated

from .models import Customer
from .serializers import CustomerSerializer


class CustomerListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/v1/customers/ — scoped to the caller's own
    organisation (tenant). Any authenticated user (admin or CSM) can list
    and add — unlike User Management, there's no admin-only gate here."""

    serializer_class = CustomerSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Customer.objects.filter(organisation=self.request.user.organisation)


class CustomerDetailView(generics.RetrieveUpdateAPIView):
    """GET/PATCH /api/v1/customers/<id>/ — scoped to the caller's own
    organisation. 404, not 403, for a customer outside that scope."""

    serializer_class = CustomerSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Customer.objects.filter(organisation=self.request.user.organisation)
