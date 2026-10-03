from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from services.customers.models import Email
from services.email import send_campaign_email

from . import audit
from .models import Campaign
from .serializers import (
    CampaignSerializer,
    forget_recipient_visibility,
    visible_contacts,
    with_recipient_visibility,
)


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
    if request.data.get("recipient_ids") is None:
        return None
    # Shape first: anything but a list of ints is a 400, never a 500 from
    # the ORM choking on `id__in=["x"]`.
    field = serializers.ListField(child=serializers.IntegerField())
    try:
        recipient_ids = field.run_validation(request.data["recipient_ids"])
    except ValidationError as exc:
        raise ValidationError({"recipient_ids": exc.detail}) from exc

    # SOC2:AUTH-02 only a contact the caller may open can be added
    recipients = visible_contacts(request.user).filter(id__in=recipient_ids)

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


def _campaigns(request):
    """Every campaign in the caller's organisation — campaigns are org-wide
    (no owner or creator to scope them by) — read twice filtered."""
    # SOC2:AUTH-02 tenant scope; recipients are filtered per reader by the serializer
    return with_recipient_visibility(
        Campaign.objects.filter(organisation=request.user.organisation), request.user
    )


class CampaignListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/v1/campaigns/ — every Campaign the caller's own
    organisation owns. Powers /campaigns and /campaigns/create.
    Pagination off — same reasoning as ScenarioListCreateView: a small,
    whole-collection list, not one meant to be paged through.

    Org-wide, but twice filtered: a recipient the reader may not open is
    left out and only counted (CampaignSerializer)."""

    serializer_class = CampaignSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return _campaigns(self.request)

    def perform_create(self, serializer):
        # Resolved *before* the save: _resolve_recipients can raise, and
        # doing it after left a campaign row behind on a 400 — a half-made
        # record from a request the caller was told had failed.
        recipients = _resolve_recipients(self.request)
        with transaction.atomic():
            campaign = serializer.save(organisation=self.request.user.organisation)
            if recipients is not None:
                campaign.recipients.set(recipients)
            audit.record(
                self.request,
                campaign,
                "created",
                {"recipient_ids": sorted(campaign.recipients.values_list("id", flat=True))},
            )


class CampaignDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/v1/campaigns/<id>/ — scoped to the caller's
    own organisation. A sent Campaign is locked against further edits —
    you can't unsend a real email, so it shouldn't look editable.

    `recipient_ids` replaces only the recipients the editor may open.
    Recipients they can't see were never shown to them, so the list they
    send back can't mention them: replacing the whole set would silently
    drop people a colleague picked, and refusing the edit would confirm
    that somebody hidden is there beyond the count already shown. So the
    hidden ones are kept untouched."""

    serializer_class = CampaignSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return _campaigns(self.request)

    def perform_update(self, serializer):
        with transaction.atomic():
            # Row lock for the whole edit. Without it a send could land
            # between the status check and the save (editing a campaign that
            # has gone out), and two editors' read-hidden-then-set could
            # interleave so one drops what the other just added. A send takes
            # the same lock, so it waits for this edit or this edit waits
            # for it and then sees `sent`.
            locked = Campaign.objects.select_for_update().get(pk=serializer.instance.pk)
            if locked.status == Campaign.Status.SENT:
                raise ValidationError({"detail": "Can't edit a campaign that's already been sent."})
            # Resolved before the save, same reason as perform_create's — a
            # rejected recipient list shouldn't leave the rest of the edit
            # applied (the transaction rolls back either way).
            recipients = _resolve_recipients(self.request)
            changed = audit.changed_fields(locked, serializer.validated_data)
            campaign = serializer.save()
            if recipients is not None:
                before = set(campaign.recipients.values_list("id", flat=True))
                # SOC2:AUTH-02 recipients the editor can't open are kept, never replaced
                hidden = campaign.recipients.exclude(
                    pk__in=visible_contacts(self.request.user).values("pk")
                )
                campaign.recipients.set([*recipients, *hidden])
                if set(campaign.recipients.values_list("id", flat=True)) != before:
                    changed = sorted([*changed, "recipients"])
            if changed:
                audit.record(self.request, campaign, "updated", {"fields": changed})
        forget_recipient_visibility(campaign)

    def perform_destroy(self, instance):
        with transaction.atomic():
            audit.record(self.request, instance, "deleted")
            instance.delete()


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
    Activity Feed, not just in this campaign's own send report.

    Any member of the organisation may send, as before: a campaign has no
    owner or creator to restrict it to. The send reaches every recipient,
    but the response is read twice filtered like any other — the log
    lines of contacts the sender can't open are left out."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        # SOC2:AUTH-02 tenant scope: another organisation's campaign is a 404
        get_object_or_404(Campaign, pk=pk, organisation=request.user.organisation)
        with transaction.atomic():
            # Row lock held for the whole send, and the status checked under
            # it: a second send of the same campaign blocks here until the
            # first commits, then reads `sent` and is refused — a double
            # send (every recipient emailed twice) can't happen. Holding one
            # row's lock through a synchronous send is the price; there is no
            # task queue to hand it to, and an edit of this campaign waiting
            # meanwhile is the right outcome.
            campaign = Campaign.objects.select_for_update().get(pk=pk)
            return self._send(request, campaign)

    def _send(self, request, campaign):
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
        audit.record(
            request,
            campaign,
            "sent",
            {
                "sent": sum(1 for e in log if e["status"] == "sent"),
                "skipped": sum(1 for e in log if e["status"] == "skipped"),
            },
        )
        return Response(CampaignSerializer(campaign, context={"request": request}).data)
