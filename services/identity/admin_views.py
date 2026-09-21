"""What an organisation administrator does with domains and access requests.

Every view here is scoped to the caller's own tenant, derived from their
membership rather than from anything the request carries. There is no
`organizationId` parameter to tamper with, which is the cheapest possible
defence against reaching into another company: the identifier simply is not an
input.

Domains and access requests both gate on `manage_org_settings` and
`manage_users` respectively, the capabilities this codebase already has, rather
than inventing a parallel notion of "admin".
"""

from rest_framework import serializers, status, views
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core import audit
from services.accounts.models import Role
from services.accounts.permissions import CanManageOrgSettings, CanManageUsers
from services.mail.providers.base import ProviderError

from . import domains as domain_service
from . import onboarding
from .context import organisation_for
from .models import AccessRequest, Department, OrganizationDomain


def _error(code, message, http_status=status.HTTP_400_BAD_REQUEST):
    return Response({"success": False, "error": {"code": code, "message": message}}, http_status)


class DomainSerializer(serializers.ModelSerializer):
    dns_record = serializers.SerializerMethodField()

    class Meta:
        model = OrganizationDomain
        fields = [
            "id",
            "domain",
            "is_primary",
            "verification_status",
            "verified_at",
            "dns_record",
            "created_at",
        ]
        read_only_fields = ["verification_status", "verified_at", "created_at"]

    def get_dns_record(self, obj):
        """Exactly what to publish. Spelled out so nobody has to guess the
        record type or the prefix."""
        if obj.verification_status == OrganizationDomain.VerificationStatus.VERIFIED:
            return None
        return {
            "type": "TXT",
            "name": obj.domain,
            "value": domain_service.expected_record(obj),
        }


class AccessRequestSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.name", read_only=True)

    class Meta:
        model = AccessRequest
        fields = [
            "id",
            "email",
            "user_name",
            "status",
            "requested_at",
            "reviewed_at",
            "rejection_reason",
        ]


class DomainListCreateView(views.APIView):
    """GET/POST /api/v1/identity/domains/ — this organisation's own domains."""

    permission_classes = [IsAuthenticated, CanManageOrgSettings]

    def get(self, request):
        organisation = organisation_for(request.user)
        records = OrganizationDomain.objects.filter(organisation=organisation)
        return Response(DomainSerializer(records, many=True).data)

    def post(self, request):
        organisation = organisation_for(request.user)
        if organisation is None:
            return _error("ORGANIZATION_NOT_FOUND", "You do not belong to an organisation.")

        domain = domain_service.domain_of("@" + str(request.data.get("domain", "")).strip())
        if not domain or "." not in domain:
            return _error("INVALID_DOMAIN", "That does not look like a domain.")
        if domain_service.is_personal(domain):
            return _error(
                "PERSONAL_DOMAIN_NOT_ALLOWED",
                "A shared email provider cannot be claimed by one organisation.",
            )

        # Globally unique: two tenants cannot both claim a domain, or a
        # corporate sign-in would be ambiguous about where it lands.
        if OrganizationDomain.objects.filter(domain=domain).exists():
            return _error("DOMAIN_ALREADY_CLAIMED", "That domain is already claimed.")

        record = OrganizationDomain.objects.create(
            organisation=organisation,
            domain=domain,
            verification_token=domain_service.new_token(),
        )
        audit.record(  # SOC2:LOG-01
            "domain.added",
            request=request,
            actor=request.user,
            organisation=organisation,
            target=record,
            metadata={"domain": domain},
        )
        return Response(DomainSerializer(record).data, status=status.HTTP_201_CREATED)


class DomainVerifyView(views.APIView):
    """POST /api/v1/identity/domains/<pk>/verify/ — check the published record."""

    permission_classes = [IsAuthenticated, CanManageOrgSettings]

    def post(self, request, pk):
        organisation = organisation_for(request.user)
        record = OrganizationDomain.objects.filter(pk=pk, organisation=organisation).first()
        if record is None:
            # 404 rather than 403: a domain in another tenant does not exist as
            # far as this caller is concerned.
            return _error("DOMAIN_NOT_FOUND", "No such domain.", status.HTTP_404_NOT_FOUND)

        try:
            matched = domain_service.verify(record)
        except ProviderError as exc:
            # Could not check is not the same as did not match, and saying so
            # stops somebody re-publishing a record that was already correct.
            return _error("DNS_LOOKUP_FAILED", str(exc), status.HTTP_503_SERVICE_UNAVAILABLE)

        if not matched:
            return _error(
                "DOMAIN_NOT_VERIFIED",
                "That TXT record is not published yet. DNS can take a while to propagate.",
            )

        audit.record(  # SOC2:LOG-01
            "domain.verified",
            request=request,
            actor=request.user,
            organisation=organisation,
            target=record,
            metadata={"domain": record.domain},
        )
        return Response(DomainSerializer(record).data)


class AccessRequestListView(views.APIView):
    """GET /api/v1/identity/access-requests/ — who is waiting, in this tenant."""

    permission_classes = [IsAuthenticated, CanManageUsers]

    def get(self, request):
        organisation = organisation_for(request.user)
        requests = AccessRequest.objects.filter(organisation=organisation).select_related("user")
        wanted = request.query_params.get("status", AccessRequest.Status.PENDING)
        if wanted != "all":
            requests = requests.filter(status=wanted)
        return Response(AccessRequestSerializer(requests, many=True).data)


class AccessRequestDecisionView(views.APIView):
    """POST /api/v1/identity/access-requests/<pk>/<decision>/ — approve or reject."""

    permission_classes = [IsAuthenticated, CanManageUsers]

    def post(self, request, pk, decision):
        organisation = organisation_for(request.user)
        access_request = (
            AccessRequest.objects.filter(pk=pk, organisation=organisation)
            .select_related("user", "organisation")
            .first()
        )
        if access_request is None:
            return _error("ACCESS_REQUEST_NOT_FOUND", "No such request.", status.HTTP_404_NOT_FOUND)

        if decision == "reject":
            try:
                onboarding.reject(
                    access_request,
                    reviewer=request.user,
                    reason=request.data.get("reason", ""),
                    request=request,
                )
            except onboarding.OnboardingError as exc:
                return _error(exc.code, exc.message)
            return Response({"status": AccessRequest.Status.REJECTED})

        if decision != "approve":
            return _error("INVALID_DECISION", "Decision must be approve or reject.")

        role = Role.objects.filter(
            pk=request.data.get("role_id"), organisation=organisation
        ).first()
        department = Department.objects.filter(
            pk=request.data.get("department_id"), organisation=organisation
        ).first()

        try:
            onboarding.approve(
                access_request,
                reviewer=request.user,
                role=role,
                department=department,
                request=request,
            )
        except onboarding.OnboardingError as exc:
            http_status = (
                status.HTTP_403_FORBIDDEN
                if exc.code == "INSUFFICIENT_PERMISSION"
                else status.HTTP_400_BAD_REQUEST
            )
            return _error(exc.code, exc.message, http_status)

        return Response({"status": AccessRequest.Status.APPROVED})
