"""The metric layer over the API.

Gated on `view_all_accounts`, not on `IsAuthenticated`: every number here is
the whole organisation's, and a CSM whose book is scoped to their own
customers would otherwise read the company's ARR off this endpoint.
"""

from rest_framework import generics, views
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

from services.accounts.permissions import CanViewAllAccounts

from .models import MetricSnapshot
from .registry import BY_KEY, METRICS, as_of, compute_all
from .signals import describe as _describe
from .signals import latest_by_member as _latest_by_member
from .signals import number as _number


class MetricListView(views.APIView):
    """GET /api/v1/metrics/ — every metric, its value now, and the last
    month-end it was recorded at, so a screen can show the number and
    which way it has moved without a second request."""

    permission_classes = [CanViewAllAccounts]

    def get(self, request):
        organisation = request.user.organisation
        values = compute_all(organisation)

        latest = {}
        for row in MetricSnapshot.objects.filter(
            organisation=organisation, dimension="", member=""
        ).order_by("metric", "-period_end"):
            latest.setdefault(row.metric, row)

        metrics = []
        for metric in METRICS:
            now = _number(values[metric.key])
            previous = latest.get(metric.key)
            previous_value = _number(previous.value) if previous else None
            metrics.append(
                {
                    **_describe(metric),
                    "value": now,
                    "previous": (
                        {"period_end": previous.period_end.isoformat(), "value": previous_value}
                        if previous
                        else None
                    ),
                    # Null when either side is unmeasured: a change from
                    # "unknown" to 40 is not a rise of 40.
                    "change": (
                        round(now - previous_value, 4)
                        if now is not None and previous_value is not None
                        else None
                    ),
                }
            )

        return Response(
            {"as_of": as_of().isoformat(), "currency": organisation.currency, "metrics": metrics}
        )


class MetricHistoryView(views.APIView):
    """GET /api/v1/metrics/<key>/history/ — the month-end series for one
    metric, oldest first. A month with no row is absent, not zero."""

    permission_classes = [CanViewAllAccounts]

    def get(self, request, key):
        metric = BY_KEY.get(key)
        if metric is None:
            raise NotFound(f"No metric called {key!r}.")

        points = [
            {"period_end": row.period_end.isoformat(), "value": _number(row.value)}
            for row in MetricSnapshot.objects.filter(
                organisation=request.user.organisation, metric=key, dimension="", member=""
            ).order_by("period_end")
        ]
        return Response(
            {
                "metric": _describe(metric),
                "currency": request.user.organisation.currency,
                "points": points,
            }
        )


def _members_payload(organisation, metric, dimension):
    from .registry import compute_slice

    latest = _latest_by_member(organisation, metric.key, dimension)
    members = []
    for member, label, value in compute_slice(organisation, metric, dimension):
        now = _number(value)
        previous = latest.get(member)
        previous_value = _number(previous.value) if previous else None
        members.append(
            {
                "member": member,
                "label": label,
                "value": now,
                "previous": (
                    {"period_end": previous.period_end.isoformat(), "value": previous_value}
                    if previous
                    else None
                ),
                "change": (
                    round(now - previous_value, 4)
                    if now is not None and previous_value is not None
                    else None
                ),
            }
        )
    # Largest first, unmeasured last — the reader wants the biggest member on top.
    members.sort(key=lambda m: (m["value"] is None, -(m["value"] or 0)))
    return members


class MetricSliceView(views.APIView):
    """GET /api/v1/metrics/<key>/by/<dimension>/ — one metric cut one way,
    every member with its value now and its move since the last month-end.
    The "why" layer's raw material: ARR at risk by product, NRR by owner."""

    permission_classes = [CanViewAllAccounts]

    def get(self, request, key, dimension):
        from .registry import DIMENSION_LABELS

        metric = BY_KEY.get(key)
        if metric is None:
            raise NotFound(f"No metric called {key!r}.")
        if dimension not in metric.slices:
            raise NotFound(
                f"{key!r} cannot be cut by {dimension!r}; it can by "
                f"{', '.join(sorted(metric.slices)) or 'nothing'}."
            )
        organisation = request.user.organisation
        return Response(
            {
                "metric": _describe(metric),
                "dimension": {"key": dimension, "label": DIMENSION_LABELS[dimension]},
                "currency": organisation.currency,
                "members": _members_payload(organisation, metric, dimension),
            }
        )


class MetricSignalsView(views.APIView):
    """GET /api/v1/metrics/signals/ — the metrics that moved materially since
    the last month-end, worst first, each naming the member that moved it
    most. Empty until a second month-end exists to compare against; the
    response says so rather than inventing a baseline. The rule lives in
    signals.py, shared with the management brief."""

    permission_classes = [CanViewAllAccounts]

    def get(self, request):
        from .signals import signals_for

        return Response(signals_for(request.user.organisation))


def _brief_payload(brief):
    return {
        "id": brief.id,
        "as_of": brief.as_of.isoformat(),
        "baseline": brief.baseline.isoformat() if brief.baseline else None,
        "headline": brief.headline,
        "body": brief.body,
        "watch": brief.watch,
        "generated_at": brief.generated_at.isoformat(),
        "generated_by": brief.generated_by.name if brief.generated_by else None,
    }


class BriefView(views.APIView):
    """GET /api/v1/metrics/brief/ — the latest management brief, or
    `{"brief": null}` before one has been written. Reading is free."""

    permission_classes = [CanViewAllAccounts]

    def get(self, request):
        from .models import Brief

        brief = Brief.objects.filter(organisation=request.user.organisation).first()
        return Response({"brief": _brief_payload(brief) if brief else None})


class BriefGenerateView(views.APIView):
    """POST /api/v1/metrics/brief/generate/ — write a new brief from the
    metric layer as it stands. A real, paid model call, so an explicit
    action rather than a side effect of viewing. Error mapping matches
    HeadlineGenerateView's: 503 when the provider isn't configured, 502
    when the call fails, 422 when there is nothing to write about."""

    permission_classes = [CanViewAllAccounts]

    def post(self, request):
        from rest_framework import status

        from services.copilot.anthropic_client import CopilotNotConfigured, CopilotRequestFailed

        from .brief import NothingToBrief, generate_brief

        try:
            brief = generate_brief(request.user.organisation, generated_by=request.user)
        except NothingToBrief as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        except CopilotNotConfigured as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except CopilotRequestFailed as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({"brief": _brief_payload(brief)}, status=status.HTTP_201_CREATED)


class _InitiativeViewMixin:
    """Shared by the list and detail: the organisation's own initiatives,
    with one `Figures` per request so a page of them runs the rollups once."""

    permission_classes = [CanViewAllAccounts]

    def get_serializer_class(self):
        from .serializers import InitiativeSerializer

        return InitiativeSerializer

    def get_queryset(self):
        from .models import Initiative

        return Initiative.objects.filter(
            organisation=self.request.user.organisation
        ).select_related("owner")

    def get_serializer_context(self):
        from .initiatives import Figures

        context = super().get_serializer_context()
        if not hasattr(self, "_figures"):
            self._figures = Figures(self.request.user.organisation)
        context["figures"] = self._figures
        return context


class InitiativeListCreateView(_InitiativeViewMixin, generics.ListCreateAPIView):
    """GET/POST /api/v1/metrics/initiatives/ — management's decisions, each
    judged live against the metric layer. Unpaginated: a board of decisions
    is tens of rows, and the page wants all of them."""

    pagination_class = None


class InitiativeDetailView(_InitiativeViewMixin, generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/v1/metrics/initiatives/<id>/. Closing one
    (status done or abandoned) stamps closed_at; reopening clears it."""


def _proposal_payload(proposal):
    return {
        "id": proposal.id,
        "batch": proposal.batch,
        "kind": proposal.kind,
        "kind_display": proposal.get_kind_display(),
        "title": proposal.title,
        "rationale": proposal.rationale,
        "evidence": proposal.evidence,
        "action": proposal.action,
        "initiative": (
            {"id": proposal.initiative.id, "title": proposal.initiative.title}
            if proposal.initiative_id
            else None
        ),
        "status": proposal.status,
        "status_display": proposal.get_status_display(),
        "decided_by": proposal.decided_by.name if proposal.decided_by else None,
        "decided_at": proposal.decided_at.isoformat() if proposal.decided_at else None,
        "decision_note": proposal.decision_note,
        "result": proposal.result,
        "generated_by": proposal.generated_by.name if proposal.generated_by else None,
        "created_at": proposal.created_at.isoformat(),
    }


class ProposalListView(views.APIView):
    """GET /api/v1/metrics/proposals/ — the review queue: proposed first,
    newest first within a status. `?status=proposed` narrows."""

    permission_classes = [CanViewAllAccounts]

    def get(self, request):
        from .models import Proposal

        queryset = Proposal.objects.filter(organisation=request.user.organisation).select_related(
            "initiative", "decided_by", "generated_by"
        )
        wanted = request.query_params.get("status")
        if wanted in Proposal.Status.values:
            queryset = queryset.filter(status=wanted)
        # Proposed first; newest batch first; within a batch, the order the
        # agent proposed them in — that order carries its priority, and the
        # microseconds between two rows of one run do not.
        rows = list(queryset)
        batch_started = {}
        for p in rows:
            batch_started[p.batch] = min(batch_started.get(p.batch, p.created_at), p.created_at)
        rows.sort(
            key=lambda p: (
                p.status != Proposal.Status.PROPOSED,
                -batch_started[p.batch].timestamp(),
                p.id,
            )
        )
        return Response(
            {
                "pending": sum(1 for p in rows if p.status == Proposal.Status.PROPOSED),
                "proposals": [_proposal_payload(p) for p in rows],
            }
        )


class ProposalGenerateView(views.APIView):
    """POST /api/v1/metrics/proposals/generate/ — ask the Ops agent for
    proposals. A real, paid model call; error mapping as the brief's."""

    permission_classes = [CanViewAllAccounts]

    def post(self, request):
        from rest_framework import status

        from services.copilot.anthropic_client import CopilotNotConfigured, CopilotRequestFailed

        from .proposals import NothingToProposeFrom, generate_proposals

        try:
            stored = generate_proposals(request.user.organisation, generated_by=request.user)
        except NothingToProposeFrom as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        except CopilotNotConfigured as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except (CopilotRequestFailed, ValueError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(
            {"proposals": [_proposal_payload(p) for p in stored]}, status=status.HTTP_201_CREATED
        )


class ProposalDecisionView(views.APIView):
    """POST /api/v1/metrics/proposals/<id>/approve/ and .../reject/ — a
    person's decision. Approving executes the action through the same path
    they would use by hand and records what it created; a proposal is
    decided once."""

    permission_classes = [CanViewAllAccounts]

    def post(self, request, pk, decision):
        from django.shortcuts import get_object_or_404
        from rest_framework import status

        from .models import Proposal
        from .proposals import AlreadyDecided, approve, reject

        proposal = get_object_or_404(Proposal, organisation=request.user.organisation, pk=pk)
        note = str(request.data.get("note") or "")
        try:
            if decision == "approve":
                approve(proposal, request.user, note)
            else:
                reject(proposal, request.user, note)
        except AlreadyDecided as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response({"proposal": _proposal_payload(proposal)})
