from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from services.accounts.models import User
from services.accounts.permissions import CanViewAllAccounts
from services.customers.models import Customer

from .models import Contribution, FunctionOwner
from .serializers import ContributionSerializer


def _company_customer(request, pk):
    """Any customer in the caller's organisation — knowledge is company-wide,
    so this deliberately does not apply the CSM book scoping."""
    return get_object_or_404(Customer, organisation=request.user.organisation, pk=pk)


class CustomerContributionListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/v1/customers/<id>/contributions/ — what every function
    knows about this customer, newest first; and one more piece of it. Any
    member of the organisation may read and write: the author's function
    is stamped from their profile."""

    serializer_class = ContributionSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        customer = _company_customer(self.request, self.kwargs["pk"])
        queryset = customer.contributions.select_related("author", "customer")
        wanted = self.request.query_params.get("function")
        if wanted in User.Function.values:
            queryset = queryset.filter(function=wanted)
        return queryset

    def perform_create(self, serializer):
        customer = _company_customer(self.request, self.kwargs["pk"])
        serializer.save(
            organisation=self.request.user.organisation,
            customer=customer,
            author=self.request.user,
            function=self.request.user.function,
        )


class ContributionDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/v1/contributions/<id>/ — the author may edit or
    remove their own; someone who manages users may remove anyone's."""

    serializer_class = ContributionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Contribution.objects.filter(organisation=self.request.user.organisation)

    def _check(self, obj):
        user = self.request.user
        if obj.author_id != user.id and not user.has_capability("manage_users"):
            raise PermissionDenied("Only the author can change this.")

    def perform_update(self, serializer):
        self._check(serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        self._check(instance)
        instance.delete()


class CustomerResponsibleView(APIView):
    """GET/PATCH /api/v1/customers/<id>/responsible/ — who answers for this
    customer in each function. CS is the customer's owner; the others are
    FunctionOwner rows. PATCH `{"function": "engineering", "user_id": 7}`
    (or null to clear) needs view-all-accounts; reading is for everyone."""

    permission_classes = [IsAuthenticated]

    @staticmethod
    def _payload(customer):
        owners = {fo.function: fo.user for fo in customer.function_owners.select_related("user")}
        if customer.owner_id:
            owners.setdefault(User.Function.CS, customer.owner)
        return {
            "customer_id": customer.id,
            "responsible": [
                {
                    "function": function,
                    "function_display": label,
                    "user": (
                        {"id": owners[function].id, "name": owners[function].name}
                        if function in owners
                        else None
                    ),
                }
                for function, label in User.Function.choices
            ],
        }

    def get(self, request, pk):
        return Response(self._payload(_company_customer(request, pk)))

    def patch(self, request, pk):
        if not CanViewAllAccounts().has_permission(request, self):
            raise PermissionDenied("Setting who is responsible needs view-all-accounts.")
        customer = _company_customer(request, pk)
        function = request.data.get("function")
        if function not in User.Function.values:
            return Response({"detail": "Unknown function."}, status=status.HTTP_400_BAD_REQUEST)
        user_id = request.data.get("user_id")
        if user_id is None:
            if function == User.Function.CS:
                customer.owner = None
                customer.save(update_fields=["owner"])
            else:
                customer.function_owners.filter(function=function).delete()
        else:
            user = get_object_or_404(User, pk=user_id, organisation=request.user.organisation)
            if function == User.Function.CS:
                customer.owner = user
                customer.save(update_fields=["owner"])
            else:
                FunctionOwner.objects.update_or_create(
                    customer=customer, function=function, defaults={"user": user}
                )
        return Response(self._payload(customer))
