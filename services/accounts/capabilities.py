"""The closed set of things a Role can be allowed to do.

Deliberately small: each key here corresponds to real endpoints that
actually exist and were previously gated by the single `IsOrgAdmin`
check (see permissions.py's own docstring for that history). Everyday
work — customers, contacts, tasks, notes, custom-object *records*,
Copilot — has never been role-gated and still isn't, so it has no key
here; anything not listed is open to every authenticated member of the
organisation.

Adding a capability means adding real gating for it in a view. Don't
add speculative keys: an unenforced capability is a checkbox that lies
to the admin ticking it.
"""

from django.db import models


class Capability(models.TextChoices):
    MANAGE_USERS = "manage_users", "Manage users & roles"
    MANAGE_ORG_SETTINGS = "manage_org_settings", "Manage organisation settings"
    MANAGE_CUSTOM_OBJECTS = "manage_custom_objects", "Manage custom objects"
    MANAGE_INTEGRATIONS = "manage_integrations", "Manage integrations & webhooks"
    MANAGE_FX_RATES = "manage_fx_rates", "Manage exchange rates"
    # Unlike the five above, this one widens a result set rather than
    # opening an endpoint: every customer/account view stays reachable
    # by everyone, and what changes is how much comes back. See
    # services/customers/scoping.py for the rule it switches off.
    VIEW_ALL_ACCOUNTS = "view_all_accounts", "View all customers & accounts"


ALL_CAPABILITIES = [choice.value for choice in Capability]
