"""DRF permission classes, one per real capability.

This used to be a single `IsOrgAdmin` comparing `user.role == "admin"`
against a hardcoded two-value field. Roles are now real, org-defined
rows carrying a set of capabilities (see models.Role), so the question
each view asks is "does this user hold *this* capability" rather than
"is this user the admin". The five subclasses below exist so views stay
as declarative as they were — `permission_classes = [CanManageUsers]`
reads the same way `[IsOrgAdmin]` did.

Every one of these is a plain `has_permission` check. There's still no
DRF object-level permission anywhere — record-level visibility is
enforced by the querysets themselves (see
services/customers/scoping.py), which narrow to what the caller owns
rather than refusing the request. That keeps the "404, not an empty
list" convention those views already follow: a record you can't see
doesn't exist as far as the API is concerned.
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


class CanViewAllAccounts(HasCapability):
    """The odd one out: no view lists this in `permission_classes`,
    because holding it doesn't unlock an endpoint — it widens what the
    endpoint returns. The real check is
    `user.has_capability(Capability.VIEW_ALL_ACCOUNTS)` inside
    services/customers/scoping.py. This class exists so the capability
    has the same shape as the other five if a view ever does need to
    gate on it outright."""

    capability = Capability.VIEW_ALL_ACCOUNTS
    message = "You can only see the customers and accounts you own."
