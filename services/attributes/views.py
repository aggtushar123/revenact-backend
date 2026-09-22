from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core import audit
from services.accounts.permissions import CanManageCustomObjects
from services.copilot.anthropic_client import (
    BudgetExceeded,
    CopilotNotConfigured,
    CopilotRequestFailed,
)
from services.customers.scoping import visible_accounts, visible_customers

from .fill import companies_for, fill
from .models import AIAttribute, AIAttributeValue
from .serializers import (
    AIAttributeSerializer,
    AIAttributeValueSerializer,
    AttributeBriefSerializer,
    OverrideSerializer,
)

#: How many companies one "fill everything" request answers before handing
#: the rest to the nightly pass: each is a model call, and a request that
#: ran for minutes would time out at the proxy anyway.
FILL_CAP = 25


def _org_attributes(request):
    return AIAttribute.objects.filter(organisation=request.user.organisation)


def _company(request, data_or_params, *, required=True):
    """The customer or account named by `customer=`/`account=`, visible to the
    viewer or 404. Returns None when neither is given and not required."""
    customer_id, account_id = data_or_params.get("customer"), data_or_params.get("account")
    if customer_id and account_id:
        return Response(
            {"detail": "Give one of customer or account, not both."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if customer_id:
        return get_object_or_404(visible_customers(request.user), pk=customer_id)
    if account_id:
        return get_object_or_404(visible_accounts(request.user), pk=account_id)
    if required:
        return Response({"detail": "customer or account is required."}, status=400)
    return None


class AIAttributeListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/v1/attributes/definitions/. Reading is open to every
    member (the panel on a company page needs the list); defining needs
    `manage_custom_objects`, the same capability as custom objects, since
    both are tenant-wide schema."""

    serializer_class = AIAttributeSerializer
    pagination_class = None

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), CanManageCustomObjects()]
        return [IsAuthenticated()]

    def get_queryset(self):
        return _org_attributes(self.request)

    def perform_create(self, serializer):
        attribute = serializer.save()
        audit.record(
            "attribute.define",
            request=self.request,
            target=attribute,
            metadata={"name": attribute.name, "value_type": attribute.value_type},
        )


class AIAttributeDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = AIAttributeSerializer

    def get_permissions(self):
        if self.request.method == "GET":
            return [IsAuthenticated()]
        return [IsAuthenticated(), CanManageCustomObjects()]

    def get_queryset(self):
        return _org_attributes(self.request)


class FillView(APIView):
    """POST /api/v1/attributes/definitions/<id>/fill/ {customer|account}

    Answers the attribute for one company (201, the new row), or with an
    empty body for every applicable company the viewer may open, up to
    FILL_CAP (200, {filled, remaining}). Each answer is a model call
    charged as `attribute`."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        attribute = get_object_or_404(_org_attributes(request), pk=pk)
        if not request.user.organisation.ai_agent_enabled:
            return Response(
                {"detail": "AI Copilot is disabled for your organisation."},
                status=status.HTTP_403_FORBIDDEN,
            )
        company = _company(request, request.data, required=False)
        if isinstance(company, Response):
            return company
        try:
            if company is not None:
                if not attribute.applies_to(company):
                    return Response(
                        {"detail": f"{attribute.name} does not apply to this kind of company."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                row = fill(
                    attribute, company, actor=request.user, viewer=request.user, request=request
                )
                return Response(
                    AIAttributeValueSerializer(row).data, status=status.HTTP_201_CREATED
                )
            companies = companies_for(attribute, request.user)
            rows = [
                fill(attribute, c, actor=request.user, viewer=request.user, request=request)
                for c in companies[:FILL_CAP]
            ]
        except BudgetExceeded as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        except CopilotNotConfigured as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except CopilotRequestFailed as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(
            {
                "filled": len(rows),
                "remaining": max(len(companies) - len(rows), 0),
                "values": AIAttributeValueSerializer(rows, many=True).data,
            }
        )


class ValueListView(APIView):
    """GET /api/v1/attributes/values/?customer=<id>|account=<id> — every
    attribute that applies to the company, each with its latest row or
    null. POST {attribute, customer|account, value} — a person's own
    answer, written as a `human` row on top of the timeline."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        company = _company(request, request.query_params)
        if isinstance(company, Response):
            return company
        is_account = company.__class__.__name__ == "Account"
        applicable = _org_attributes(request).filter(
            **{"applies_to_account" if is_account else "applies_to_customer": True}
        )
        where = {"account": company} if is_account else {"customer": company}
        out = []
        for attribute in applicable:
            latest = AIAttributeValue.objects.filter(attribute=attribute, **where).first()
            out.append(
                {
                    "attribute": AttributeBriefSerializer(attribute).data,
                    "latest": AIAttributeValueSerializer(latest).data if latest else None,
                }
            )
        return Response(out)

    def post(self, request):
        serializer = OverrideSerializer(data=request.data, organisation=request.user.organisation)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        company = _company(request, data)
        if isinstance(company, Response):
            return company
        attribute = data["attribute"]
        if not attribute.applies_to(company):
            return Response(
                {"detail": f"{attribute.name} does not apply to this kind of company."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        is_account = company.__class__.__name__ == "Account"
        row = AIAttributeValue.objects.create(
            attribute=attribute,
            value=data["value"],
            origin=AIAttributeValue.Origin.HUMAN,
            status=AIAttributeValue.Status.FILLED,
            set_by=request.user,
            **({"account": company} if is_account else {"customer": company}),
        )
        audit.record(
            "attribute.override",
            request=request,
            target=row,
            metadata={"attribute": attribute.api_name, "company": company.name},
        )
        return Response(AIAttributeValueSerializer(row).data, status=status.HTTP_201_CREATED)


class ValueHistoryView(APIView):
    """GET /api/v1/attributes/values/history/?attribute=<id>&customer=<id>|account=<id>
    — every row, newest first."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        attribute = get_object_or_404(
            _org_attributes(request), pk=request.query_params.get("attribute")
        )
        company = _company(request, request.query_params)
        if isinstance(company, Response):
            return company
        is_account = company.__class__.__name__ == "Account"
        rows = AIAttributeValue.objects.filter(
            attribute=attribute, **({"account": company} if is_account else {"customer": company})
        ).select_related("set_by")
        return Response(AIAttributeValueSerializer(rows, many=True).data)
