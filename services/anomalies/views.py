from collections import defaultdict
from decimal import Decimal

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core import audit
from services.accounts.permissions import CanViewAllAccounts
from services.copilot.anthropic_client import BudgetExceeded, CopilotNotConfigured
from services.customers.models import Account
from services.customers.personal import readable_evidence_q
from services.customers.scoping import sees_everything, visible_accounts, visible_customers

from .detect import DetectionStopped, detect
from .models import Anomaly, AnomalyEvidence


def _org_anomalies(request):
    return Anomaly.objects.filter(organisation=request.user.organisation)


def visible_evidence(organisation, viewer, *, anomaly=None):
    """Evidence this person may read: on an organisation or account they
    may open, and a record they may read (the shared rule in
    services.customers.personal). An account is its own rule: one under an
    organisation they may open can still be closed to them."""
    customer_ids = set(visible_customers(viewer).values_list("id", flat=True))
    # SOC2:AUTH-02 accounts by their own rule, not by their organisation's
    account_ids = set(visible_accounts(viewer).values_list("id", flat=True))
    rows = AnomalyEvidence.objects.filter(organisation=organisation)
    if anomaly is not None:
        rows = rows.filter(anomaly=anomaly)
    return (
        rows.filter(Q(customer_id__in=customer_ids) | Q(account_id__in=account_ids))
        .filter(
            readable_evidence_q(
                viewer,
                email=AnomalyEvidence.Kind.EMAIL,
                ticket=AnomalyEvidence.Kind.TICKET,
                call=AnomalyEvidence.Kind.CALL,
            )
        )
        .select_related("customer", "account")
    )


def title_for(stored: str, companies: int, *, sees_all: bool) -> str:
    """The title one viewer may read.

    The stored title was written by a model from reports across the whole
    organisation, so it can describe companies outside this viewer's book
    (or count them). Only someone who sees every account gets it as
    written; everyone else gets one built from fields, counting only the
    companies they themselves can see behind it. The dashboard's attention
    list, Copilot's grounding, the MCP server and this API all read it
    through here, so they cannot drift apart."""
    if sees_all:
        return stored
    # "1 of your companies" is already singular-correct.
    return f"Similar reports across {companies} of your companies"


def summary_for(stored: str, *, sees_all: bool):
    """The summary one viewer may read: as written, or nothing."""
    return stored if sees_all else None


class Companies:
    """The companies behind a page of evidence, resolved once."""

    def __init__(self, rows, organisation, viewer):
        self.field = organisation.effective_global_attributes()["arr"]
        visible = {c.id: c for c in visible_customers(viewer)}
        self.customers = {
            row.customer_id: visible[row.customer_id] for row in rows if row.customer_id in visible
        }
        self.by_account = defaultdict(list)
        account_ids = {row.account_id for row in rows if row.account_id}
        if account_ids:
            for account in Account.objects.filter(id__in=account_ids).prefetch_related("customers"):
                for customer in account.customers.all():
                    if customer.id in visible:
                        self.by_account[account.id].append(visible[customer.id])

    def behind(self, rows) -> dict:
        out = {}
        for row in rows:
            if row.customer_id and row.customer_id in self.customers:
                out[row.customer_id] = self.customers[row.customer_id]
            elif row.account_id:
                for customer in self.by_account.get(row.account_id, []):
                    out[customer.id] = customer
        return out

    def arr_of(self, customer) -> Decimal:
        return Decimal(getattr(customer, self.field, 0) or 0)


def _row(anomaly, rows, companies, *, sees_all) -> dict:
    behind = companies.behind(rows)
    return {
        "id": anomaly.id,
        "title": title_for(anomaly.title, len(behind), sees_all=sees_all),
        "summary": summary_for(anomaly.summary, sees_all=sees_all),
        "status": anomaly.status,
        "arr": str(sum((companies.arr_of(c) for c in behind.values()), Decimal(0))),
        "companies": len(behind),
        "interactions": len(rows),
        "first_seen_at": anomaly.first_seen_at,
        "last_seen_at": anomaly.last_seen_at,
        "acknowledged_by": (
            {"id": anomaly.acknowledged_by.id, "name": anomaly.acknowledged_by.name}
            if anomaly.acknowledged_by_id
            else None
        ),
        "_behind": behind,
    }


def _evidence_row(row) -> dict:
    company = row.company
    return {
        "id": row.id,
        "kind": row.kind,
        "record_id": row.record_id,
        "snippet": row.snippet,
        "occurred_at": row.occurred_at,
        "company": {
            "type": "account" if row.account_id else "customer",
            "id": company.id,
            "name": company.name,
        },
    }


class AnomalyListView(APIView):
    """GET /api/v1/anomalies/?status= — what is going wrong at several
    companies at once, most revenue first.

    Open to any member, but every figure is their own: the revenue, the
    counts and the evidence cover the companies they may open and the
    records they may read. A cluster they can see nothing of is left out
    rather than shown as a zero, since its title was written from reports
    they may not read."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        organisation = request.user.organisation
        wanted = request.query_params.get("status")
        if wanted and wanted not in Anomaly.Status.values:
            return Response({"detail": f"Unknown status {wanted!r}."}, status=400)
        rows = _org_anomalies(request).select_related("acknowledged_by")
        if wanted:
            rows = rows.filter(status=wanted)
        evidence = list(visible_evidence(organisation, request.user))
        companies = Companies(evidence, organisation, request.user)
        by_anomaly = defaultdict(list)
        for row in evidence:
            by_anomaly[row.anomaly_id].append(row)
        sees_all = sees_everything(request.user)
        out = []
        for anomaly in rows:
            mine = by_anomaly.get(anomaly.id)
            if not mine:
                continue
            row = _row(anomaly, mine, companies, sees_all=sees_all)
            row.pop("_behind")
            out.append(row)
        out.sort(key=lambda r: (-float(r["arr"]), -r["interactions"], r["title"]))
        return Response(out)


class AnomalyDetailView(APIView):
    """GET /api/v1/anomalies/<id>/ — the cluster with the companies hit and
    the reports, as this reader may see them. PATCH its status, which is a
    leadership decision because it speaks for the whole company."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        anomaly = get_object_or_404(_org_anomalies(request), pk=pk)
        organisation = request.user.organisation
        rows = list(visible_evidence(organisation, request.user, anomaly=anomaly))
        if not rows:
            return Response({"detail": "No reports you can read."}, status=404)
        companies = Companies(rows, organisation, request.user)
        row = _row(anomaly, rows, companies, sees_all=sees_everything(request.user))
        behind = row.pop("_behind")
        hit = sorted(
            (
                {"id": c.id, "name": c.name, "arr": str(companies.arr_of(c))}
                for c in behind.values()
            ),
            key=lambda c: -float(c["arr"]),
        )
        return Response({**row, "companies_hit": hit, "evidence": [_evidence_row(r) for r in rows]})

    def patch(self, request, pk):
        if not CanViewAllAccounts().has_permission(request, self):
            return Response({"detail": "Only leadership sets this."}, status=403)
        anomaly = get_object_or_404(_org_anomalies(request), pk=pk)
        wanted = request.data.get("status")
        if wanted not in Anomaly.Status.values:
            return Response({"detail": f"Unknown status {wanted!r}."}, status=400)
        anomaly.status = wanted
        anomaly.acknowledged_by = request.user
        anomaly.save(update_fields=["status", "acknowledged_by"])
        audit.record(
            "anomaly.update",
            request=request,
            target=anomaly,
            metadata={"status": wanted},
        )
        return self.get(request, pk)


class DetectView(APIView):
    """POST /api/v1/anomalies/detect/ — look now rather than tonight.
    Leadership only: naming a new cluster is a model call."""

    permission_classes = [IsAuthenticated, CanViewAllAccounts]

    def post(self, request):
        organisation = request.user.organisation
        if not organisation.ai_agent_enabled:
            return Response({"detail": "AI Copilot is disabled for your organisation."}, status=403)
        try:
            return Response(detect(organisation, actor=request.user, request=request))
        except DetectionStopped as stopped:
            code = (
                status.HTTP_429_TOO_MANY_REQUESTS
                if isinstance(stopped.cause, BudgetExceeded)
                else status.HTTP_503_SERVICE_UNAVAILABLE
                if isinstance(stopped.cause, CopilotNotConfigured)
                else status.HTTP_502_BAD_GATEWAY
            )
            return Response({**stopped.result, "detail": str(stopped.cause)}, status=code)
