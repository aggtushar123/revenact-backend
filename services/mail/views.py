"""Connect, sync and send through a person's own mailbox.

The OAuth dance: `connect/<provider>/` hands the browser an authorization
URL carrying a signed state (who is connecting, from which organisation);
the provider sends the browser to `oauth/<provider>/callback/`, which is
the one public route here — it trusts nothing but the signed state, swaps
the code for tokens, stores them encrypted and bounces the browser back to
Settings. IMAP takes a form instead. Every connect and disconnect is an
audit event: a mailbox is a credential.
"""

from django.conf import settings
from django.core import signing
from django.http import HttpResponseRedirect
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core import audit
from services.customers.scoping import get_visible_account, get_visible_customer
from services.customers.serializers import EmailSerializer

from . import providers
from .models import MailboxConnection
from .providers.base import ProviderError
from .serializers import ComposeSerializer, MailboxConnectionSerializer
from .sync import send_email, sync_mailbox

STATE_SALT = "mail.oauth"
STATE_MAX_AGE = 15 * 60


def _redirect_uri(request, provider_key):
    base = settings.MAIL_OAUTH_REDIRECT_BASE or request.build_absolute_uri("/").rstrip("/")
    # A URL for the provider, not an HTTP response body (semgrep's Flask rule misreads it).
    return f"{base}/api/v1/mail/oauth/{provider_key}/callback/"  # nosemgrep


def _back_to_settings(outcome, detail=""):
    from urllib.parse import urlencode

    query = {"mailbox": outcome}
    if detail:
        query["detail"] = detail[:200]
    return HttpResponseRedirect(f"{settings.FRONTEND_URL}/integrations?{urlencode(query)}")


class MyMailboxView(APIView):
    """GET /api/v1/mail/connection/ — my connection (or null) and the
    providers this deployment can connect. DELETE disconnects: the
    credentials are destroyed; synced emails stay on the record."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        connection = getattr(request.user, "mailbox", None)
        return Response(
            {
                "connection": MailboxConnectionSerializer(connection).data if connection else None,
                "providers": providers.available(),
            }
        )

    def delete(self, request):
        connection = getattr(request.user, "mailbox", None)
        if connection is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        # SOC2:LOG-01 a credential is being removed
        audit.record(
            "mailbox.disconnect",
            request=request,
            target=connection,
            metadata={"provider": connection.provider, "address": connection.address},
        )
        connection.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ConnectView(APIView):
    """POST /api/v1/mail/connect/<provider>/ — for an OAuth provider,
    `{authorize_url}` to send the browser to; for IMAP, the form
    `{address, password, imap_host, imap_port?, smtp_host, smtp_port?,
    username?, display_name?}` is verified by logging in and saved."""

    permission_classes = [IsAuthenticated]

    def post(self, request, provider_key):
        try:
            provider = providers.get_provider(provider_key)
        except ValueError:
            return Response({"detail": "Unknown provider."}, status=status.HTTP_400_BAD_REQUEST)
        if not provider.configured():
            return Response(
                {"detail": f"{provider.label} is not configured for this deployment."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if provider.uses_oauth:
            state = signing.dumps(
                {"user": request.user.id, "provider": provider_key}, salt=STATE_SALT
            )
            return Response(
                {
                    "authorize_url": provider.authorize_url(
                        state, _redirect_uri(request, provider_key)
                    )
                }
            )
        try:
            creds = provider.connect_with_password(request.data)
        except ProviderError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        connection = _save_connection(request.user, provider_key, creds)
        audit.record(  # SOC2:LOG-01 a credential was added
            "mailbox.connect",
            request=request,
            target=connection,
            metadata={"provider": provider_key, "address": connection.address},
        )
        return Response(
            MailboxConnectionSerializer(connection).data, status=status.HTTP_201_CREATED
        )


def _save_connection(user, provider_key, creds):
    connection, _ = MailboxConnection.objects.update_or_create(
        user=user,
        defaults={
            "organisation": user.organisation,
            "provider": provider_key,
            "address": creds.address,
            "display_name": creds.display_name or user.name,
            "status": MailboxConnection.Status.CONNECTED,
            "error": "",
            "sync_cursor": "",
        },
    )
    connection.set_credentials(creds.data)
    connection.save(update_fields=["credentials"])
    return connection


class OAuthCallbackView(APIView):
    """GET /api/v1/mail/oauth/<provider>/callback/?code=&state= — public by
    necessity (the provider's redirect carries no bearer token); the
    signed state is the only thing trusted."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, provider_key):
        from services.accounts.models import User

        if request.query_params.get("error"):
            return _back_to_settings(
                "error", request.query_params.get("error_description", "denied")
            )
        try:
            state = signing.loads(
                request.query_params.get("state", ""), salt=STATE_SALT, max_age=STATE_MAX_AGE
            )
        except signing.BadSignature:
            return _back_to_settings("error", "This connection link is invalid or has expired.")
        if state.get("provider") != provider_key:
            return _back_to_settings("error", "Provider mismatch.")
        user = User.objects.filter(pk=state.get("user"), is_active=True).first()
        if user is None:
            return _back_to_settings("error", "Unknown user.")
        provider = providers.get_provider(provider_key)
        try:
            creds = provider.exchange_code(
                request.query_params.get("code", ""), _redirect_uri(request, provider_key)
            )
        except ProviderError as exc:
            return _back_to_settings("error", str(exc))
        connection = _save_connection(user, provider_key, creds)
        audit.record(  # SOC2:LOG-01 a credential was added
            "mailbox.connect",
            request=request,
            actor=user,
            target=connection,
            metadata={"provider": provider_key, "address": connection.address},
        )
        return _back_to_settings("connected")


class SyncNowView(APIView):
    """POST /api/v1/mail/sync/ — pull my mailbox now."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        connection = getattr(request.user, "mailbox", None)
        if connection is None:
            return Response({"detail": "No mailbox connected."}, status=status.HTTP_400_BAD_REQUEST)
        filed = sync_mailbox(connection)
        connection.refresh_from_db()
        data = MailboxConnectionSerializer(connection).data
        data["filed"] = filed
        return Response(data)


class _ComposeView(APIView):
    permission_classes = [IsAuthenticated]

    def _parent(self, request, **kwargs):
        raise NotImplementedError

    def post(self, request, **kwargs):
        connection = getattr(request.user, "mailbox", None)
        if connection is None:
            return Response(
                {"detail": "Connect your mailbox in Settings › Integrations to send email."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        form = ComposeSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        customer, account = self._parent(request, **kwargs)
        try:
            email = send_email(
                connection,
                to=form.validated_data["to"],
                subject=form.validated_data["subject"],
                body=form.validated_data["body"],
                customer=customer,
                account=account,
            )
        except ProviderError as exc:
            if exc.reauth:
                connection.status = MailboxConnection.Status.ERROR
                connection.error = str(exc)[:255]
                connection.save(update_fields=["status", "error", "updated_at"])
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(EmailSerializer(email).data, status=status.HTTP_201_CREATED)


class CustomerComposeView(_ComposeView):
    """POST /api/v1/customers/<id>/emails/send/ — {to[], subject, body}."""

    def _parent(self, request, customer_id):
        return get_visible_customer(request, customer_id), None


class AccountComposeView(_ComposeView):
    """POST /api/v1/customers/<id>/accounts/<id>/emails/send/."""

    def _parent(self, request, customer_id, account_id):
        return None, get_visible_account(request, customer_id, account_id)


__all__ = ["timezone"]
