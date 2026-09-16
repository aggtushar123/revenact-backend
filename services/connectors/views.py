import hashlib
import hmac
import json

from django.conf import settings
from django.core import signing
from django.db.models import DateField
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core import audit
from services.accounts.permissions import CanManageIntegrations

from . import providers
from .models import Connector
from .providers.base import ProviderError
from .providers.webhook import parse_inbound
from .serializers import ConnectorSerializer
from .sync import classify_new, file_tickets, summary_line, sync_connector

STATE_SALT = "connectors.oauth"
STATE_MAX_AGE = 15 * 60
INBOUND_MAX_BYTES = 512 * 1024
INBOUND_MAX_TICKETS = 200


def with_ingested(queryset):
    """Annotate what each connector has brought in. Two separate Max
    annotations rather than one expression across both joins: a ticket's
    `opened_at` is a date and a call's `occurred_at` a datetime, and the
    serializer picks the newer once both are Python values."""
    from django.db.models import Count, Max
    from django.db.models.functions import Cast, TruncDate

    return queryset.annotate(
        ticket_count=Count("tickets", distinct=True),
        call_count=Count("calls", distinct=True),
        last_ticket_at=Max("tickets__opened_at"),
        last_call_at=Cast(Max(TruncDate("calls__occurred_at")), output_field=DateField()),
    )


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
        return with_ingested(
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
        return with_ingested(
            Connector.objects.filter(organisation=self.request.user.organisation)
            .prefetch_related("customers", "accounts")
            .distinct()
        )


def _own_connector(request, pk):
    return get_object_or_404(
        Connector.objects.filter(organisation=request.user.organisation), pk=pk
    )


def _ticket_provider(connector):
    try:
        return providers.get_provider(connector.provider)
    except ValueError:
        return None


def _redirect_uri(request, provider_key):
    base = settings.MAIL_OAUTH_REDIRECT_BASE or request.build_absolute_uri("/").rstrip("/")
    # A URL for the provider, not an HTTP response body (semgrep's Flask rule misreads it).
    return f"{base}/api/v1/connectors/oauth/{provider_key}/callback/"  # nosemgrep


def _back_to_integrations(outcome, detail=""):
    from urllib.parse import urlencode

    query = {"connector": outcome}
    if detail:
        query["detail"] = detail[:200]
    return HttpResponseRedirect(f"{settings.FRONTEND_URL}/integrations?{urlencode(query)}")


def _store(connector, config, creds):
    connector.config = config
    connector.set_credentials(creds)
    connector.status = Connector.Status.CONNECTED
    connector.error = ""
    connector.sync_cursor = ""
    connector.last_sync_note = ""
    connector.save()


class ConnectorConnectView(APIView):
    """POST /api/v1/connectors/<id>/connect/ — hand the source its
    credentials. The body is the provider's own form (see `setup` on the
    connector); `{oauth: true, ...}` asks for `{authorize_url}` instead
    when the provider supports it. The webhook provider answers with the
    one-time `token` and the `inbound_url` to send tickets to."""

    permission_classes = [CanManageIntegrations]

    def post(self, request, pk):
        connector = _own_connector(request, pk)
        provider = _ticket_provider(connector)
        if provider is None:
            return Response(
                {"detail": f"{connector.get_provider_display()} does not sync tickets."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        form = request.data if isinstance(request.data, dict) else {}
        if form.get("oauth"):
            if not (provider.uses_oauth and provider.oauth_configured()):
                return Response(
                    {"detail": f"Sign-in with {provider.label} is not set up on this deployment."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            state = signing.dumps(
                {
                    "connector": connector.id,
                    "user": request.user.id,
                    "form": {k: v for k, v in form.items() if k != "oauth" and isinstance(v, str)},
                },
                salt=STATE_SALT,
            )
            return Response(
                {
                    "authorize_url": provider.authorize_url(
                        state, _redirect_uri(request, provider.key)
                    )
                }
            )
        try:
            config, creds = provider.connect(form)
        except ProviderError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        _store(connector, config, creds)
        audit.record(  # SOC2:LOG-01 a credential was added
            "connector.connect",
            request=request,
            target=connector,
            metadata={"provider": connector.provider, "department": connector.department},
        )
        data = ConnectorSerializer(connector, context={"request": request}).data
        if connector.provider == Connector.Provider.WEBHOOK:
            data["token"] = creds["token"]
            data["inbound_url"] = request.build_absolute_uri(
                f"/api/v1/connectors/{connector.id}/inbound/"
            )
        return Response(data, status=status.HTTP_201_CREATED)


class ConnectorOAuthCallbackView(APIView):
    """GET /api/v1/connectors/oauth/<provider>/callback/?code=&state= —
    public by necessity; the signed state is the only thing trusted."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, provider_key):
        from services.accounts.models import User

        if request.query_params.get("error"):
            return _back_to_integrations(
                "error", request.query_params.get("error_description", "denied")
            )
        try:
            state = signing.loads(
                request.query_params.get("state", ""), salt=STATE_SALT, max_age=STATE_MAX_AGE
            )
        except signing.BadSignature:
            return _back_to_integrations("error", "This connection link is invalid or has expired.")
        user = User.objects.filter(pk=state.get("user"), is_active=True).first()
        connector = Connector.objects.filter(
            pk=state.get("connector"), provider=provider_key
        ).first()
        if user is None or connector is None or connector.organisation_id != user.organisation_id:
            return _back_to_integrations("error", "Unknown connector.")
        provider = _ticket_provider(connector)
        try:
            config, creds = provider.exchange_code(
                request.query_params.get("code", ""),
                _redirect_uri(request, provider_key),
                state.get("form") or {},
            )
        except ProviderError as exc:
            return _back_to_integrations("error", str(exc))
        _store(connector, config, creds)
        audit.record(  # SOC2:LOG-01 a credential was added
            "connector.connect",
            request=request,
            actor=user,
            target=connector,
            metadata={"provider": connector.provider, "department": connector.department},
        )
        return _back_to_integrations("connected")


class ConnectorCredentialsView(APIView):
    """DELETE /api/v1/connectors/<id>/credentials/ — disconnect. The
    secret is destroyed; the tickets it brought in stay."""

    permission_classes = [CanManageIntegrations]

    def delete(self, request, pk):
        connector = _own_connector(request, pk)
        # SOC2:LOG-01 a credential is being removed
        audit.record(
            "connector.disconnect",
            request=request,
            target=connector,
            metadata={"provider": connector.provider},
        )
        connector.credentials = ""
        connector.config = {}
        connector.status = Connector.Status.NOT_CONNECTED
        connector.error = ""
        connector.sync_cursor = ""
        connector.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ConnectorSyncView(APIView):
    """POST /api/v1/connectors/<id>/sync/ — pull this source now."""

    permission_classes = [CanManageIntegrations]

    def post(self, request, pk):
        connector = _own_connector(request, pk)
        if _ticket_provider(connector) is None or not connector.has_credentials:
            return Response(
                {"detail": "Connect this source first."}, status=status.HTTP_400_BAD_REQUEST
            )
        counts = sync_connector(connector)
        connector = with_ingested(Connector.objects.filter(pk=connector.pk)).get()
        data = ConnectorSerializer(connector, context={"request": request}).data
        data.update(counts)
        return Response(data)


class ConnectorInboundView(APIView):
    """POST /api/v1/connectors/<id>/inbound/ — a source pushes tickets.

    Body: one ticket object or `{"tickets": [...]}`; each carries
    `external_id`, `title`, and optionally `description`, `status`,
    `priority`, `requester_email`, `requester_name`, `assignee_name`,
    `url`, `opened_at`, `resolved_at`. Authenticated by the connector's
    own secret: `X-Revenact-Token`, or `X-Revenact-Signature` (hex
    HMAC-SHA256 of the raw body). Unknown connectors and bad secrets both
    answer 404, so the endpoint confirms nothing."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, pk):
        connector = Connector.objects.filter(
            pk=pk, provider=Connector.Provider.WEBHOOK, is_enabled=True
        ).first()
        if connector is None or not connector.has_credentials:
            return Response(status=status.HTTP_404_NOT_FOUND)
        raw = request.body or b""
        if len(raw) > INBOUND_MAX_BYTES:
            return Response(
                {"detail": "Payload too large."}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            )
        secret = connector.get_credentials().get("token", "")
        # SOC2:API-11 the source proves it holds this connector's secret
        if not self._authentic(request, raw, secret):
            audit.record(
                "connector.inbound_rejected",
                request=request,
                organisation=connector.organisation,
                target=connector,
                outcome="failure",
            )
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            payload = json.loads(raw.decode() or "{}")
        except (ValueError, UnicodeDecodeError):
            return Response({"detail": "Body must be JSON."}, status=status.HTTP_400_BAD_REQUEST)
        items = (
            payload.get("tickets")
            if isinstance(payload, dict) and "tickets" in payload
            else [payload]
        )
        if not isinstance(items, list) or len(items) > INBOUND_MAX_TICKETS:
            return Response(
                {"detail": f"Send one ticket or up to {INBOUND_MAX_TICKETS} in `tickets`."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            remotes = [parse_inbound(item) for item in items]  # SOC2:API-01 validated shape
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        counts, created = file_tickets(connector, remotes)
        from django.utils import timezone

        connector.status = Connector.Status.CONNECTED
        connector.last_synced_at = timezone.now()
        connector.last_sync_note = summary_line(counts)
        connector.save(update_fields=["status", "last_synced_at", "last_sync_note"])
        classify_new(connector, created)
        return Response(counts, status=status.HTTP_202_ACCEPTED)

    @staticmethod
    def _authentic(request, raw, secret):
        if not secret:
            return False
        token = request.headers.get("X-Revenact-Token", "")
        if token and hmac.compare_digest(token, secret):
            return True
        signature = request.headers.get("X-Revenact-Signature", "").lower().removeprefix("sha256=")
        if signature:
            expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
            return hmac.compare_digest(signature, expected)
        return False
