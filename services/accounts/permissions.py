from rest_framework.permissions import BasePermission

from .models import User


class IsOrgAdmin(BasePermission):
    """Only an organisation's admin can manage that organisation's members
    (e.g. add a CSM)."""

    message = "Only an organisation admin can do this."

    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated and request.user.role == User.Role.ADMIN
        )
