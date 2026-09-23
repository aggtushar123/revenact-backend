from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from services.copilot.anthropic_client import (
    BudgetExceeded,
    CopilotNotConfigured,
    CopilotRequestFailed,
)
from services.customers.models import Call, Email, Ticket
from services.customers.personal import visible_tickets
from services.customers.scoping import visible_accounts, visible_customers
from services.mail.models import MailMessage
from services.mail.visibility import visible_emails

from .models import Translation
from .translate import looks_like_language, of_record, remember_language, translate


def _on_a_company_i_can_open(user):
    """Rows hanging off a customer or account this person may open."""
    return Q(customer__in=visible_customers(user)) | Q(account__in=visible_accounts(user))


def _readable(request, kind, record_id):
    """The record, if this person may read it — the record's own rule, not
    just its company's. A translation is a copy of its words, so it is read
    under exactly what the original is read under."""
    user = request.user
    mine = _on_a_company_i_can_open(user)
    if kind == Translation.Kind.EMAIL:
        return get_object_or_404(
            visible_emails(user, Email.objects.filter(mine).distinct()), pk=record_id
        )
    if kind == Translation.Kind.TICKET:
        return get_object_or_404(
            visible_tickets(user, Ticket.objects.filter(mine).distinct()), pk=record_id
        )
    if kind == Translation.Kind.CALL:
        return get_object_or_404(Call.objects.filter(mine).distinct(), pk=record_id)
    # A person's own mailbox is theirs alone, as everywhere else.
    return get_object_or_404(MailMessage.objects.filter(owner=user), pk=record_id)


def _text_of(kind, record) -> str:
    if kind == Translation.Kind.EMAIL:
        return f"{record.subject}\n\n{record.body}".strip()
    if kind == Translation.Kind.TICKET:
        return f"{record.title}\n\n{record.description}".strip()
    if kind == Translation.Kind.CALL:
        return f"{record.title}\n\n{record.summary}".strip()
    return f"{record.subject}\n\n{record.body or record.snippet}".strip()


class TranslateView(APIView):
    """POST /api/v1/translations/

    `{kind, id, to}` translates one record and keeps the result, so the
    next person to open the same message pays nothing; `{text, to}`
    translates a draft somebody is writing and keeps nothing, because a
    draft is not a record.

    A record is read under its own rule: mail is its mailbox owner's and
    their chain's, a ticket its department's, a mailbox message its
    owner's. Anything else is a 404."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        organisation = request.user.organisation
        to = (request.data.get("to") or "").strip()
        if not looks_like_language(to):
            return Response(
                {"detail": "A language code is required, like 'en' or 'pt-br'."}, status=400
            )
        if not organisation.ai_agent_enabled:
            return Response({"detail": "AI Copilot is disabled for your organisation."}, status=403)

        kind = request.data.get("kind")
        loose = (request.data.get("text") or "").strip()
        if kind and kind not in Translation.Kind.values:
            return Response({"detail": f"Unknown kind {kind!r}."}, status=400)
        if not kind and not loose:
            return Response({"detail": "Give a record to translate, or some text."}, status=400)

        try:
            if kind:
                record = _readable(request, kind, request.data.get("id"))
                text = _text_of(kind, record)
                if not text:
                    return Response({"detail": "There is nothing to translate."}, status=400)
                row, made = of_record(
                    kind,
                    record.id,
                    text,
                    to,
                    organisation=organisation,
                    actor=request.user,
                    request=request,
                )
                if made:
                    remember_language(record, row.detected_language)
                return Response(
                    {
                        "text": row.text,
                        "to": row.language,
                        "detected_language": row.detected_language,
                        "made_now": made,
                    },
                    status=status.HTTP_201_CREATED if made else status.HTTP_200_OK,
                )
            text, detected = translate(loose, to, organisation=organisation, actor=request.user)
        except BudgetExceeded as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        except CopilotNotConfigured as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except CopilotRequestFailed as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(
            {"text": text, "to": to, "detected_language": detected, "made_now": True},
            status=status.HTTP_201_CREATED,
        )
