from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from services.customers.models import Contact, Email
from services.customers.scoping import visible_children_q
from services.email import send_campaign_email

from .models import Campaign
from .serializers import CampaignSerializer


def _resolve_recipients(request):
    """`recipient_ids` is never a real serializer field (see
    CampaignSerializer's own docstring) — read straight off raw request
    data and resolved against what the caller can actually see, so a
    client can't add a Contact from another organisation *or* from an
    account somebody else owns.

    Unreachable ids are a 400, not silently dropped. This used to drop
    them, which was defensible when the only way to hit it was naming
    another tenant's contact id — something no UI flow produces. Now
    that a same-tenant contact can be out of scope (a stale tab after a
    reassignment is enough), silently sending to seven of the ten
    people you picked is the worse failure: you can't unsend the seven,
    and nothing tells you about the three."""
    recipient_ids = request.data.get("recipient_ids")
    if recipient_ids is None:
        return None

    recipients = Contact.objects.filter(
        visible_children_q(request.user), id__in=recipient_ids
    ).distinct()

    missing = len(set(recipient_ids)) - recipients.count()
    if missing:
        raise ValidationError(
            {
                "recipient_ids": (
                    f"{missing} of the contacts you picked aren't available to you — "
                    "they belong to an account you don't have access to."
                )
            }
        )
    return recipients


class CampaignListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/v1/campaigns/ — every Campaign the caller's own
    organisation owns. Powers /campaigns and /campaigns/create.
    Pagination off — same reasoning as ScenarioListCreateView: a small,
    whole-collection list, not one meant to be paged through."""

    serializer_class = CampaignSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return Campaign.objects.filter(organisation=self.request.user.organisation)

    def perform_create(self, serializer):
        # Resolved *before* the save: _resolve_recipients can raise, and
        # doing it after left a campaign row behind on a 400 — a half-made
        # record from a request the caller was told had failed.
        recipients = _resolve_recipients(self.request)
        campaign = serializer.save(organisation=self.request.user.organisation)
        if recipients is not None:
            campaign.recipients.set(recipients)


class CampaignDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/v1/campaigns/<id>/ — scoped to the caller's
    own organisation. A sent Campaign is locked against further edits —
    you can't unsend a real email, so it shouldn't look editable."""

    serializer_class = CampaignSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Campaign.objects.filter(organisation=self.request.user.organisation)

    def perform_update(self, serializer):
        if serializer.instance.status == Campaign.Status.SENT:
            raise ValidationError({"detail": "Can't edit a campaign that's already been sent."})
        # Same ordering as perform_create's, same reason — a rejected
        # recipient list shouldn't leave the rest of the edit applied.
        recipients = _resolve_recipients(self.request)
        campaign = serializer.save()
        if recipients is not None:
            campaign.recipients.set(recipients)


class CampaignSendView(APIView):
    """POST /api/v1/campaigns/<id>/send/ — the real send. Body: none.
    Runs synchronously, in-request — no task queue exists in this
    codebase (same limit services.scenarios.engine's own docstring
    states outright), so the response IS the completed send, log and
    all. One recipient failing (no email on file, a raised send error)
    is logged and skipped — it never aborts the rest of the send, same
    per-item try/except resilience as run_scenario's own per-node loop.

    Creates one real Email row per successful send — the same "give a
    previously provenance-free thing real provenance" move Survey
    Tier 0 made for Customer.nps_score: every campaign send now shows
    up for real in that recipient's own parent Customer/Account's
    Activity Feed, not just in this campaign's own send report."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        campaign = get_object_or_404(Campaign, pk=pk, organisation=request.user.organisation)

        if campaign.status == Campaign.Status.SENT:
            return Response(
                {"detail": "This campaign has already been sent."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not campaign.subject or not campaign.body:
            return Response(
                {"detail": "Add a subject and body before sending."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        recipients = list(campaign.recipients.all())
        if not recipients:
            return Response(
                {"detail": "Add at least one recipient before sending."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        log = []
        for contact in recipients:
            try:
                send_campaign_email(contact, campaign.subject, campaign.body)
                Email.objects.create(
                    customer=contact.customer,
                    account=contact.account,
                    subject=campaign.subject,
                    body=campaign.body,
                    sender_name=request.user.organisation.name,
                    recipient_name=contact.name,
                    sent_at=timezone.now(),
                    campaign=campaign,
                )
                log.append(
                    {
                        "contact_id": contact.id,
                        "contact_name": contact.name,
                        "status": "sent",
                        "detail": f"Emailed {contact.email}",
                    }
                )
            except Exception as exc:  # noqa: BLE001 — deliberately broad: any
                # single recipient's failure becomes a log line, never a 500.
                log.append(
                    {
                        "contact_id": contact.id,
                        "contact_name": contact.name,
                        "status": "skipped",
                        "detail": str(exc),
                    }
                )

        campaign.send_log = log
        campaign.status = Campaign.Status.SENT
        campaign.sent_at = timezone.now()
        campaign.save(update_fields=["send_log", "status", "sent_at"])
        return Response(CampaignSerializer(campaign).data)
