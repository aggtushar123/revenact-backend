from rest_framework import generics

from core import audit
from services.accounts.permissions import CanManageIntegrations

from .models import WebhookSubscription
from .serializers import WebhookSubscriptionSerializer


class WebhookListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/v1/webhooks/ — every WebhookSubscription the
    caller's own organisation owns. Admin-only both ways (unlike
    Scenario's own GET, which any authenticated user can view) — a
    webhook's URL/secret is credential-adjacent configuration, same
    reasoning `/auth/csms/` gates member management to admins only."""

    serializer_class = WebhookSubscriptionSerializer
    permission_classes = [CanManageIntegrations]
    pagination_class = None

    def get_queryset(self):
        return WebhookSubscription.objects.filter(organisation=self.request.user.organisation)

    def perform_create(self, serializer):
        webhook = serializer.save(organisation=self.request.user.organisation)
        # SOC2:LOG-01 config change — where the org's data gets sent
        audit.record(
            "webhook.create",
            request=self.request,
            target=webhook,
            metadata={"url": webhook.url, "event": webhook.event},
        )


class WebhookDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/v1/webhooks/<id>/ — scoped to the caller's
    own organisation, requires `manage_integrations`. PATCH is mainly for toggling
    `is_active`; `url`/`event` can be changed too (re-validated the
    same way as on create)."""

    serializer_class = WebhookSubscriptionSerializer
    permission_classes = [CanManageIntegrations]

    def get_queryset(self):
        return WebhookSubscription.objects.filter(organisation=self.request.user.organisation)

    def perform_update(self, serializer):
        webhook = serializer.save()
        audit.record(
            "webhook.update",
            request=self.request,
            target=webhook,
            metadata={"fields": sorted(serializer.validated_data.keys())},
        )

    def perform_destroy(self, instance):
        audit.record(
            "webhook.delete",
            request=self.request,
            target=instance,
            metadata={"url": instance.url, "event": instance.event},
        )
        instance.delete()
