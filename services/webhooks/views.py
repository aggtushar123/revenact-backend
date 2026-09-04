from rest_framework import generics

from services.accounts.permissions import IsOrgAdmin

from .models import WebhookSubscription
from .serializers import WebhookSubscriptionSerializer


class WebhookListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/v1/webhooks/ — every WebhookSubscription the
    caller's own organisation owns. Admin-only both ways (unlike
    Scenario's own GET, which any authenticated user can view) — a
    webhook's URL/secret is credential-adjacent configuration, same
    reasoning `/auth/csms/` gates member management to admins only."""

    serializer_class = WebhookSubscriptionSerializer
    permission_classes = [IsOrgAdmin]
    pagination_class = None

    def get_queryset(self):
        return WebhookSubscription.objects.filter(organisation=self.request.user.organisation)

    def perform_create(self, serializer):
        serializer.save(organisation=self.request.user.organisation)


class WebhookDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/v1/webhooks/<id>/ — scoped to the caller's
    own organisation, admin-only. PATCH is mainly for toggling
    `is_active`; `url`/`event` can be changed too (re-validated the
    same way as on create)."""

    serializer_class = WebhookSubscriptionSerializer
    permission_classes = [IsOrgAdmin]

    def get_queryset(self):
        return WebhookSubscription.objects.filter(organisation=self.request.user.organisation)
