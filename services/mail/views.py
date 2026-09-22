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
from django.db.models import Count, Q
from django.http import HttpResponseRedirect
from django.utils import timezone
from rest_framework import status
from rest_framework.generics import get_object_or_404
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core import audit
from services.customers.scoping import get_visible_account, get_visible_customer
from services.customers.serializers import EmailSerializer

from . import providers
from .models import MailboxConnection, MailMessage
from .providers.base import ProviderError
from .serializers import (
    ComposeSerializer,
    MailboxConnectionSerializer,
    MailMessageDetailSerializer,
    MailMessageSerializer,
    MailMessageUpdateSerializer,
    ReplySerializer,
)
from .sync import NothingToReplyTo, reply_to, send_email, sync_mailbox

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


# --- The person's own inbox --------------------------------------------------
#
# Every view below scopes on `owner=request.user`: a mailbox's whole contents
# are the person's, and nobody else's, however senior. The team reads the
# filed copies (`customers.Email`, under services.mail.visibility), not this.

#: What `?folder=` may ask for. The five real folders, this product's two
#: triage states, and two of the provider's flags, so one control on the
#: page covers every list the person can want.
FOLDERS = {
    "inbox": Q(folder=MailMessage.Folder.INBOX, state=MailMessage.State.OPEN),
    "drafts": Q(folder=MailMessage.Folder.DRAFTS),
    "sent": Q(folder=MailMessage.Folder.SENT),
    "done": Q(state=MailMessage.State.DONE),
    "muted": Q(state=MailMessage.State.MUTED),
    "spam": Q(folder=MailMessage.Folder.SPAM),
    "trash": Q(folder=MailMessage.Folder.TRASH),
    "starred": Q(is_starred=True),
    "important": Q(is_important=True),
}

CATEGORIES = [c for c in MailMessage.Category if c != MailMessage.Category.GENERAL]


def _truthy(raw):
    return str(raw).lower() in ("1", "true", "yes")


def _own_messages(request):
    return MailMessage.objects.filter(owner=request.user).select_related(
        "email__customer", "email__account"
    )


class MailMessageListView(APIView):
    """GET /api/v1/mail/messages/ — the person's own mail, one folder at a
    time, newest first. See FOLDERS for `?folder=`; `?category=` narrows to
    one of the categories; `?unread=true` and `?priority=true` are the two
    switches; `?q=` searches sender, subject and snippet."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        folder = request.query_params.get("folder", "inbox")
        if folder not in FOLDERS:
            return Response(
                {"detail": f"folder must be one of {', '.join(FOLDERS)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rows = _own_messages(request).filter(FOLDERS[folder])
        category = request.query_params.get("category")
        if category:
            if category not in MailMessage.Category.values:
                return Response(
                    {"detail": f"category must be one of {', '.join(MailMessage.Category.values)}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            rows = rows.filter(category=category)
        if _truthy(request.query_params.get("unread")):
            rows = rows.filter(is_read=False)
        if _truthy(request.query_params.get("priority")):
            rows = rows.filter(Q(is_important=True) | Q(email__isnull=False))
        q = request.query_params.get("q", "").strip()
        if q:
            rows = rows.filter(
                Q(subject__icontains=q)
                | Q(from_name__icontains=q)
                | Q(from_address__icontains=q)
                | Q(snippet__icontains=q)
            )
        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(MailMessageSerializer(page, many=True).data)


class MailSummaryView(APIView):
    """GET /api/v1/mail/messages/summary/ — the numbers beside the folders
    and the Categories block at the top of the inbox: for each category with
    unread mail waiting, how many, the latest subjects and who they are from."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        connection = getattr(request.user, "mailbox", None)
        mine = MailMessage.objects.filter(owner=request.user)
        folders = {
            name: mine.filter(FOLDERS[name]).count()
            for name in ("inbox", "drafts", "sent", "done", "muted")
        }
        unread = mine.filter(FOLDERS["inbox"], is_read=False)
        categories = []
        counted = {
            row["category"]: row["n"] for row in unread.values("category").annotate(n=Count("id"))
        }
        for category in CATEGORIES:
            if not counted.get(category):
                continue
            latest = list(
                unread.filter(category=category)
                .order_by("-sent_at")
                .values_list("subject", "from_name", "from_address")[:12]
            )
            subjects = list(dict.fromkeys(subject for subject, _, _ in latest))
            senders = list(
                dict.fromkeys(name or address for _, name, address in latest if name or address)
            )
            categories.append(
                {
                    "category": category,
                    "label": MailMessage.Category(category).label,
                    "count": counted[category],
                    "subjects": subjects[:2],
                    "senders": senders[:1],
                    "more_senders": max(len(senders) - 1, 0),
                }
            )
        return Response(
            {
                "has_mailbox": connection is not None,
                "address": connection.address if connection else "",
                "last_synced_at": connection.last_synced_at if connection else None,
                "folders": folders,
                "unread": unread.count(),
                "categories": categories,
            }
        )


class MailMessageDetailView(APIView):
    """GET /api/v1/mail/messages/<id>/ — the whole message. PATCH changes
    `is_read`, `is_starred` or `state`; nothing goes back to the provider."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        message = get_object_or_404(_own_messages(request), pk=pk)
        return Response(MailMessageDetailSerializer(message).data)

    def patch(self, request, pk):
        message = get_object_or_404(_own_messages(request), pk=pk)
        serializer = MailMessageUpdateSerializer(message, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        # From now on what the person did here outlives the provider's view.
        serializer.save(locally_changed_at=timezone.now())
        return Response(MailMessageDetailSerializer(message).data)


class MailReplyView(APIView):
    """POST /api/v1/mail/messages/<id>/reply/ {body} — answer from the
    mailbox the message arrived in. Filed against the customer too when the
    original was. Returns the sent message as it now sits in Sent."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        message = get_object_or_404(_own_messages(request), pk=pk)
        serializer = ReplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        connection = getattr(request.user, "mailbox", None)
        if connection is None or connection.id != message.connection_id:
            return Response(
                {"detail": "This message's mailbox is no longer connected."},
                status=status.HTTP_409_CONFLICT,
            )
        try:
            sent = reply_to(message, serializer.validated_data["body"])
        except NothingToReplyTo as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except ProviderError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        # SOC2:LOG-01 mail left the building through a credential we hold
        audit.record(
            "mailbox.reply",
            request=request,
            target=message,
            metadata={"to": sent.to if sent else [], "subject": sent.subject if sent else ""},
        )
        if not message.is_read:
            message.is_read = True
            message.save(update_fields=["is_read"])
        return Response(
            MailMessageDetailSerializer(sent).data if sent else {}, status=status.HTTP_201_CREATED
        )
