"""DRF permission classes, one per real capability.

This used to be a single `IsOrgAdmin` comparing `user.role == "admin"`
against a hardcoded two-value field. Roles are now real, org-defined
rows carrying a set of capabilities (see models.Role), so the question
each view asks is "does this user hold *this* capability" rather than
"is this user the admin". The five subclasses below exist so views stay
as declarative as they were — `permission_classes = [CanManageUsers]`
reads the same way `[IsOrgAdmin]` did.

Every one of these is a plain `has_permission` check; there's no
object-level permission anywhere, because every view is already scoped
to `request.user.organisation` by its own queryset.
"""

from rest_framework.permissions import BasePermission

from .capabilities import Capability


class HasCapability(BasePermission):
    """Base class — subclasses set `capability`."""

    capability = None
    message = "You don't have permission to do this."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.has_capability(self.capability)
        )


class CanManageUsers(HasCapability):
    capability = Capability.MANAGE_USERS
    message = "You don't have permission to manage users and roles."


class CanManageOrgSettings(HasCapability):
    capability = Capability.MANAGE_ORG_SETTINGS
    message = "You don't have permission to change organisation settings."


class CanManageCustomObjects(HasCapability):
    capability = Capability.MANAGE_CUSTOM_OBJECTS
    message = "You don't have permission to manage custom objects."


class CanManageIntegrations(HasCapability):
    capability = Capability.MANAGE_INTEGRATIONS
    message = "You don't have permission to manage integrations."


class CanManageFxRates(HasCapability):
    capability = Capability.MANAGE_FX_RATES
    message = "You don't have permission to manage exchange rates."
