"""What a tenant sees of its own billing.

    GET /api/v1/billing/account/   any member: plan, seats, credits, dates
    GET /api/v1/billing/ledger/    manage_org_settings: the credit movements
    GET /api/v1/billing/plans/     any member: what can be bought

Scoped through the caller's organisation; there is no account id in any path.
Buying happens in the provider phase; staff can change plans from the portal.
"""

from rest_framework import views
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from services.accounts.permissions import CanManageOrgSettings

from . import accounts
from .models import CreditLedger, Plan


def _serialise_row(row):
    return {
        "id": row.id,
        "kind": row.kind,
        "amount": row.amount,
        "balance_after": row.balance_after,
        "reason": row.reason,
        "actor": row.actor.name if row.actor_id else None,
        "at": row.created_at,
    }


class AccountView(views.APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organisation = request.user.organisation
        if organisation is None:
            return Response(
                {
                    "success": False,
                    "error": {
                        "code": "ORGANIZATION_NOT_FOUND",
                        "message": "You do not belong to an organisation.",
                    },
                },
                400,
            )
        return Response(accounts.summary(accounts.ensure(organisation)))


class LedgerView(views.APIView):
    permission_classes = [IsAuthenticated, CanManageOrgSettings]

    def get(self, request):
        account = accounts.ensure(request.user.organisation)
        try:
            limit = min(max(int(request.query_params.get("limit", 50)), 1), 200)
        except ValueError:
            limit = 50
        rows = CreditLedger.objects.filter(account=account).select_related("actor")[:limit]
        return Response([_serialise_row(r) for r in rows])


class PlanListView(views.APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            [
                {
                    "code": p.code,
                    "name": p.name,
                    "seats_included": p.seats_included,
                    "monthly_credits": p.monthly_credits,
                    "price_cents": p.price_cents,
                    "currency": p.currency,
                }
                for p in Plan.objects.filter(is_public=True)
            ]
        )
