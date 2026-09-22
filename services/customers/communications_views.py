"""The two endpoints behind the Communications page.

Its own module rather than another thousand lines in `views.py`: this surface
reads four models through one merged list and shares none of that machinery
with the per-customer views next door.

What waiting means lives in `communications.py`; this file only turns it into
HTTP.
"""

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status, views
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core import audit
from services.mail.providers.base import ProviderError
from services.mail.sync import send_email
from services.mail.visibility import visible_emails

from . import communications
from .models import Email


class QueuePagination(PageNumberPagination):
    """DRF's own paginator over the merged list: the merge happens in Python,
    and `Paginator` slices plain lists. The queue is bounded by
    `communications.MAX_PER_KIND` before it gets here, which keeps that
    honest."""

    page_size_query_param = "page_size"
    max_page_size = 100


def _page(request, rows, view):
    paginator = QueuePagination()
    return paginator.get_paginated_response(
        paginator.paginate_queryset(rows, request, view=view)
    ).data


def _scope(request):
    return "team" if request.query_params.get("scope") == "team" else "mine"


def _kinds(request):
    wanted = [k for k in request.query_params.getlist("kind") if k in communications.KINDS]
    return wanted or None


class CommunicationsListView(views.APIView):
    """GET /api/v1/communications/ — the queue, or the whole stream.

    `?needs=false` switches from "who is waiting on you" to "what has happened
    recently", which is a different question over the same records. The queue
    is the default because it is the reason the page exists.

    Scoped by each record's own visibility rule (see `communications.py`), so
    there is nothing to gate here beyond being signed in.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        scope = _scope(request)
        kinds = _kinds(request)

        if request.query_params.get("needs") == "false":
            rows = communications.everything_rows(request.user, kinds=kinds)
            payload = _page(request, rows, self)
            payload["truncated"] = False
            payload["mode"] = "everything"
            return Response(payload)

        querysets = communications.waiting_querysets(request.user, scope=scope, kinds=kinds)
        rows, truncated = communications.rows(querysets)

        search = (request.query_params.get("q") or "").strip().lower()
        if search:
            rows = [row for row in rows if _matches(row, search)]

        payload = _page(request, rows, self)
        payload["truncated"] = truncated
        payload["mode"] = "needs"
        payload["scope"] = scope
        return Response(payload)


def _matches(row, needle):
    account = (row.get("account") or {}).get("name", "")
    haystack = " ".join(
        [row.get("who", ""), row.get("subject", ""), row.get("snippet", ""), account]
    ).lower()
    return needle in haystack


class CommunicationsStatsView(views.APIView):
    """GET /api/v1/communications/stats/ — the four tile numbers.

    Shipped separately from the list so the tiles stay right while the list is
    filtered: clicking a tile narrows the queue, and the other three counts
    must not change underneath it. Same reasoning as the Contacts and Tickets
    stats endpoints.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        scope = _scope(request)
        payload = communications.counts(request.user, scope=scope)
        payload["scope"] = scope
        payload["stale_questions"] = communications.stale_question_count(request.user, scope=scope)
        payload["has_mailbox"] = hasattr(request.user, "mailbox")
        return Response(payload)


class EmailReplyView(views.APIView):
    """POST /api/v1/communications/emails/<id>/reply/ {body}

    Answer a queue email from the person's own mailbox. The email must be
    one they may read (the mailbox rule: owner and management chain) and
    one somebody wrote to them; the reply goes to its sender under
    `Re: <subject>` and is filed on the same customer or account, so the
    debt leaves the queue. 409 without a connected mailbox."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        organisation = request.user.organisation
        rows = Email.objects.filter(
            Q(customer__organisation=organisation)
            | Q(account__customers__organisation=organisation)
        ).distinct()
        email = get_object_or_404(visible_emails(request.user, rows), pk=pk)
        body = (request.data.get("body") or "").strip()
        if not body:
            return Response({"detail": "Reply can't be empty."}, status=status.HTTP_400_BAD_REQUEST)
        if email.direction != Email.Direction.RECEIVED or not email.from_address:
            return Response(
                {"detail": "This email has nobody to reply to."}, status=status.HTTP_400_BAD_REQUEST
            )
        connection = getattr(request.user, "mailbox", None)
        if connection is None:
            return Response(
                {"detail": "Connect a mailbox to reply from Revenact."},
                status=status.HTTP_409_CONFLICT,
            )
        subject = (
            email.subject if email.subject.lower().startswith("re:") else f"Re: {email.subject}"
        )
        try:
            sent = send_email(
                connection,
                to=[email.from_address],
                subject=subject[:255],
                body=body,
                customer=email.customer,
                account=email.account,
            )
        except ProviderError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        # SOC2:LOG-01 mail left through a credential we hold
        audit.record(
            "mailbox.reply",
            request=request,
            target=email,
            metadata={"to": [email.from_address], "subject": subject},
        )
        return Response(
            {
                "id": sent.id,
                "direction": sent.direction,
                "subject": sent.subject,
                "sent_at": sent.sent_at,
                "thread_id": sent.thread_id,
            },
            status=status.HTTP_201_CREATED,
        )
