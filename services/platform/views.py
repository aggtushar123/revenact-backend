"""The internal portal: Revenact staff administering every tenant.

    GET    /api/v1/platform/overview/
    GET    /api/v1/platform/organisations/?q=&status=
    POST   /api/v1/platform/organisations/                { name, owner_email, owner_name? }
    GET    /api/v1/platform/organisations/<id>/
    PATCH  /api/v1/platform/organisations/<id>/           { name }
    DELETE /api/v1/platform/organisations/<id>/           { reason }   (archives; nothing is purged)
    POST   /api/v1/platform/organisations/<id>/status/    { status, reason }
    POST   /api/v1/platform/organisations/<id>/owner/     { user_id }
    GET    /api/v1/platform/staff/

Every view requires `IsPlatformStaff`: a superuser signed in with a second
factor. Every action is audited with the tenant as `organisation`, so a
customer's audit trail shows what Revenact did to them alongside what they
did themselves.

**Metadata only.** Nothing here reaches a tenant's customers, emails, notes,
tickets or calls. What staff see is who owns an organisation, who is in it,
what it has claimed, what state it is in, and what happened to it. Support
questions about content are answered by asking the owner; this surface is
not a way around that. The test suite asserts the detail payload's keys.
"""

from django.db.models import Count, Q
from rest_framework import status, views
from rest_framework.response import Response

from core import audit
from core.models import AuditEvent
from services.accounts.models import Organisation, User
from services.accounts.permissions import IsPlatformStaff
from services.identity import ownership
from services.identity.models import (
    AccessRequest,
    Invitation,
    OrganizationDomain,
    OrganizationMembership,
)


def _error(code, message, http_status=status.HTTP_400_BAD_REQUEST):
    return Response({"success": False, "error": {"code": code, "message": message}}, http_status)


def _owner(organisation):
    membership = ownership.owner_membership(organisation)
    if membership is None:
        return None
    return {"id": membership.user_id, "name": membership.user.name, "email": membership.user.email}


def _summary(organisation, counts):
    return {
        "id": organisation.id,
        "name": organisation.name,
        "slug": organisation.slug,
        "status": organisation.status,
        "created_at": organisation.created_at,
        "owner": _owner(organisation),
        "members_active": counts.get("members_active", 0),
        "pending_requests": counts.get("pending_requests", 0),
        "domains": [
            {"domain": d.domain, "verification_status": d.verification_status}
            for d in organisation.domains.all()
        ],
        # Billing joins here in the next phase.
        "plan": None,
    }


class OverviewView(views.APIView):
    """GET /api/v1/platform/overview/ — the numbers on the portal's front page."""

    permission_classes = [IsPlatformStaff]

    def get(self, request):
        by_status = dict(
            Organisation.objects.values_list("status")
            .annotate(n=Count("id"))
            .values_list("status", "n")
        )
        return Response(
            {
                "organisations": {
                    "total": sum(by_status.values()),
                    **{s: by_status.get(s, 0) for s, _ in Organisation.Status.choices},
                },
                "members_active": OrganizationMembership.objects.filter(status="active").count(),
                "pending_requests": AccessRequest.objects.filter(status="pending").count(),
                "open_invitations": Invitation.objects.filter(status="pending").count(),
                "verified_domains": OrganizationDomain.objects.filter(
                    verification_status="verified"
                ).count(),
                "staff": User.objects.filter(is_superuser=True, is_active=True).count(),
            }
        )


class OrganisationListView(views.APIView):
    """GET /api/v1/platform/organisations/?q=<name or domain>&status=<status>
    POST /api/v1/platform/organisations/ { name, owner_email, owner_name? }

    Creating a tenant creates its **root user** in the same transaction: the
    owner, holding the built-in Admin role, with no password (they sign in
    with Google or Microsoft on that address, or set a password from the
    reset email if mail is configured). One tenant per person in this phase,
    so an address already in use anywhere is refused.

    Archived organisations are left out unless asked for by status.
    """

    permission_classes = [IsPlatformStaff]

    def post(self, request):
        from django.db import transaction

        from services.email import send_password_reset_email

        name = str(request.data.get("name", "")).strip()
        owner_email = str(request.data.get("owner_email", "")).strip().lower()
        owner_name = str(request.data.get("owner_name", "")).strip()
        if not name:
            return _error("INVALID_NAME", "Give the organisation a name.")
        if "@" not in owner_email:
            return _error("INVALID_EMAIL", "The owner needs a valid email address.")
        if User.objects.filter(email__iexact=owner_email).exists():
            return _error("EMAIL_TAKEN", "That address already belongs to an account.")

        with transaction.atomic():
            organisation = Organisation.objects.create(name=name)
            owner = User.objects.create_user(
                email=owner_email,
                password=None,
                name=owner_name or owner_email.split("@")[0],
                organisation=organisation,
                role=User.Role.ADMIN,
            )
            ownership.claim(owner, organisation)

        audit.record(  # SOC2:LOG-01
            "platform.organisation.created",
            request=request,
            actor=request.user,
            organisation=organisation,
            target=organisation,
            metadata={"name": name, "owner": owner_email},
        )
        # Best effort: lets the owner choose a password where mail is set up;
        # a provider sign-in on the same address works regardless.
        try:
            send_password_reset_email(owner)
            mailed = True
        except Exception:  # noqa: BLE001 - mail is optional here
            mailed = False

        organisation = Organisation.objects.prefetch_related("domains").get(pk=organisation.pk)
        return Response(
            {**_summary(organisation, {"members_active": 1}), "owner_mailed": mailed},
            status=status.HTTP_201_CREATED,
        )

    def get(self, request):
        rows = Organisation.objects.all().prefetch_related("domains").order_by("name")
        wanted = request.query_params.get("status", "")
        if wanted:
            rows = rows.filter(status=wanted)
        else:
            rows = rows.exclude(status=Organisation.Status.ARCHIVED)
        q = request.query_params.get("q", "").strip()
        if q:
            rows = rows.filter(Q(name__icontains=q) | Q(domains__domain__icontains=q)).distinct()

        ids = [o.id for o in rows]
        members = dict(
            OrganizationMembership.objects.filter(organisation_id__in=ids, status="active")
            .values_list("organisation_id")
            .annotate(n=Count("id"))
            .values_list("organisation_id", "n")
        )
        pending = dict(
            AccessRequest.objects.filter(organisation_id__in=ids, status="pending")
            .values_list("organisation_id")
            .annotate(n=Count("id"))
            .values_list("organisation_id", "n")
        )
        return Response(
            [
                _summary(
                    o,
                    {
                        "members_active": members.get(o.id, 0),
                        "pending_requests": pending.get(o.id, 0),
                    },
                )
                for o in rows
            ]
        )


class OrganisationDetailView(views.APIView):
    """GET / PATCH / DELETE /api/v1/platform/organisations/<id>/.

    PATCH renames. DELETE **archives**: the organisation stops accepting
    sign-ins (like suspension) and drops out of the default list, but every
    row it owns stays where it is. Purging a tenant's data is a deliberate,
    separate act with its own retention rules, not something a button on a
    list page does.
    """

    permission_classes = [IsPlatformStaff]

    def patch(self, request, pk):
        organisation = Organisation.objects.filter(pk=pk).first()
        if organisation is None:
            return _error(
                "ORGANIZATION_NOT_FOUND", "No such organisation.", status.HTTP_404_NOT_FOUND
            )
        name = str(request.data.get("name", "")).strip()
        if not name:
            return _error("INVALID_NAME", "Give the organisation a name.")
        previous = organisation.name
        organisation.name = name
        organisation.save(update_fields=["name"])
        audit.record(  # SOC2:LOG-01
            "platform.organisation.updated",
            request=request,
            actor=request.user,
            organisation=organisation,
            target=organisation,
            metadata={"fields": ["name"], "from": previous, "to": name},
        )
        return Response(
            {"id": organisation.id, "name": organisation.name, "slug": organisation.slug}
        )

    def delete(self, request, pk):
        organisation = Organisation.objects.filter(pk=pk).first()
        if organisation is None:
            return _error(
                "ORGANIZATION_NOT_FOUND", "No such organisation.", status.HTTP_404_NOT_FOUND
            )
        reason = str(request.data.get("reason", "")).strip()
        if not reason:
            return _error("REASON_REQUIRED", "Say why; it goes on the record.")
        previous = organisation.status
        Organisation.objects.filter(pk=pk).update(status=Organisation.Status.ARCHIVED)
        audit.record(  # SOC2:LOG-01
            "platform.organisation.status",
            request=request,
            actor=request.user,
            organisation=organisation,
            target=organisation,
            metadata={"from": previous, "to": "archived", "reason": reason[:500]},
        )
        return Response({"status": Organisation.Status.ARCHIVED})

    def get(self, request, pk):
        organisation = Organisation.objects.filter(pk=pk).prefetch_related("domains").first()
        if organisation is None:
            return _error(
                "ORGANIZATION_NOT_FOUND", "No such organisation.", status.HTTP_404_NOT_FOUND
            )

        memberships = (
            OrganizationMembership.objects.filter(organisation=organisation)
            .select_related("user", "role", "department")
            .order_by("-is_owner", "user__name")
        )
        recent = AuditEvent.objects.filter(organisation=organisation).order_by("-created_at")[:20]
        counts = {
            "members_active": sum(1 for m in memberships if m.status == "active"),
            "pending_requests": AccessRequest.objects.filter(
                organisation=organisation, status="pending"
            ).count(),
        }
        return Response(
            {
                **_summary(organisation, counts),
                "open_invitations": Invitation.objects.filter(
                    organisation=organisation, status="pending"
                ).count(),
                "memberships": [
                    {
                        "id": m.id,
                        "user_id": m.user_id,
                        "name": m.user.name,
                        "email": m.user.email,
                        "role": m.role.name if m.role_id else None,
                        "department": m.department.name if m.department_id else None,
                        "status": m.status,
                        "is_owner": m.is_owner,
                        "is_active": m.user.is_active,
                        "approved_at": m.approved_at,
                        "last_login": m.user.last_login,
                    }
                    for m in memberships
                ],
                "domains": [
                    {
                        "id": d.id,
                        "domain": d.domain,
                        "is_primary": d.is_primary,
                        "verification_status": d.verification_status,
                        "verified_at": d.verified_at,
                    }
                    for d in organisation.domains.all()
                ],
                "recent_events": [
                    {
                        "action": e.action,
                        "actor": e.actor_email,
                        "outcome": e.outcome,
                        "target": e.target_repr,
                        "at": e.created_at,
                    }
                    for e in recent
                ],
            }
        )


class OrganisationStatusView(views.APIView):
    """POST /api/v1/platform/organisations/<id>/status/ — { status, reason }.

    Suspending is the platform's one blunt instrument: sign-in is refused and
    every member's capabilities resolve to nothing until reactivated. Nothing
    is deleted. The reason is required because this lands in the tenant's
    own audit trail.
    """

    permission_classes = [IsPlatformStaff]

    def post(self, request, pk):
        organisation = Organisation.objects.filter(pk=pk).first()
        if organisation is None:
            return _error(
                "ORGANIZATION_NOT_FOUND", "No such organisation.", status.HTTP_404_NOT_FOUND
            )

        new_status = str(request.data.get("status", ""))
        allowed = {
            Organisation.Status.ACTIVE,
            Organisation.Status.SUSPENDED,
            Organisation.Status.ARCHIVED,
        }
        if new_status not in allowed:
            return _error("INVALID_STATUS", "Status must be active, suspended or archived.")
        reason = str(request.data.get("reason", "")).strip()
        if not reason:
            return _error("REASON_REQUIRED", "Say why; it goes on the record.")

        previous = organisation.status
        Organisation.objects.filter(pk=pk).update(status=new_status)
        audit.record(  # SOC2:LOG-01
            "platform.organisation.status",
            request=request,
            actor=request.user,
            organisation=organisation,
            target=organisation,
            metadata={"from": previous, "to": new_status, "reason": reason[:500]},
        )
        return Response({"status": new_status})


class OrganisationOwnerView(views.APIView):
    """POST /api/v1/platform/organisations/<id>/owner/ — { user_id }.

    The platform route for a transfer the owner cannot make themselves (they
    left, or their account is gone). Audited with by_platform=true.
    """

    permission_classes = [IsPlatformStaff]

    def post(self, request, pk):
        organisation = Organisation.objects.filter(pk=pk).first()
        if organisation is None:
            return _error(
                "ORGANIZATION_NOT_FOUND", "No such organisation.", status.HTTP_404_NOT_FOUND
            )
        try:
            membership = ownership.transfer(
                organisation,
                to_user_id=request.data.get("user_id"),
                actor=request.user,
                request=request,
                by_platform=True,
            )
        except ownership.OwnershipError as exc:
            return _error(exc.code, exc.message)
        return Response({"owner": _owner(organisation), "membership_id": membership.id})


class StaffListView(views.APIView):
    """GET /api/v1/platform/staff/ — who holds platform access, and whether
    their second factor is on. The list an access review starts from."""

    permission_classes = [IsPlatformStaff]

    def get(self, request):
        from services.accounts.models import TOTPDevice

        enrolled = set(
            TOTPDevice.objects.filter(confirmed_at__isnull=False).values_list("user_id", flat=True)
        )
        staff = User.objects.filter(is_superuser=True).order_by("email")
        return Response(
            [
                {
                    "id": u.id,
                    "name": u.name,
                    "email": u.email,
                    "is_active": u.is_active,
                    "mfa_enrolled": u.id in enrolled,
                    "last_login": u.last_login,
                }
                for u in staff
            ]
        )
