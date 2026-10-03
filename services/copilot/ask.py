"""The Ask rails' surfaces, in one place.

For each surface the client may ask from: the serializer that validates its
`context`, the grounding that recomputes its screen for the asker, the
system prompt that fences that digest in `<dashboard_data>`, and the purpose
the call is metered under. `SendMessageView` reads only this table.

Adding a surface means one row here, a purpose in `usage.PURPOSES` and a
`Skill` in `skills.py` (tests fail otherwise), and a grounding that fills
`Grounding.customer_ids`, the snapshot a shared reader is checked against
(`views._reply_readable_by`); without it the surface's replies are withheld
from mentioned-only readers. For the `accounts` surface `customer_ids` holds
the organisations the digest names (and the organisation filter), while every
account it covers is recorded in `Grounding.records`. For the `pipelines`
surface `customer_ids` holds the organisation of every item counted (and the
organisation filter). Their accounts and departments are in
`Grounding.pipeline`, and every item quoted is in `Grounding.records`.
"""

from collections.abc import Callable
from dataclasses import dataclass

from rest_framework import serializers

from .accounts_context import AccountsContextSerializer
from .accounts_grounding import accounts_system_prompt, build_accounts_grounding
from .contacts_context import ContactsContextSerializer
from .contacts_grounding import build_contacts_grounding, contacts_system_prompt
from .dashboard_context import DashboardContextSerializer
from .dashboard_grounding import build_dashboard_grounding, dashboard_system_prompt
from .organizations_context import OrganizationsContextSerializer
from .organizations_grounding import build_organizations_grounding, organizations_system_prompt
from .pipelines_context import PipelinesContextSerializer
from .pipelines_grounding import build_pipelines_grounding, pipelines_system_prompt


@dataclass(frozen=True)
class Surface:
    #: Validates the client's `context`; needs `context={"user": user}`.
    serializer: type
    #: `(user, context, question) -> Grounding`, recomputing the screen.
    ground: Callable
    #: `(tone_instruction, summary) -> str`, the digest fenced as data.
    system_prompt: Callable
    #: The key in `usage.PURPOSES` the call is metered and budgeted under.
    purpose: str


SURFACES = {
    "dashboard": Surface(
        serializer=DashboardContextSerializer,
        ground=build_dashboard_grounding,
        system_prompt=dashboard_system_prompt,
        purpose="dashboard",
    ),
    "organizations": Surface(
        serializer=OrganizationsContextSerializer,
        ground=build_organizations_grounding,
        system_prompt=organizations_system_prompt,
        purpose="organizations",
    ),
    "contacts": Surface(
        serializer=ContactsContextSerializer,
        ground=build_contacts_grounding,
        system_prompt=contacts_system_prompt,
        purpose="contacts",
    ),
    "accounts": Surface(
        serializer=AccountsContextSerializer,
        ground=build_accounts_grounding,
        system_prompt=accounts_system_prompt,
        purpose="accounts",
    ),
    "pipelines": Surface(
        serializer=PipelinesContextSerializer,
        ground=build_pipelines_grounding,
        system_prompt=pipelines_system_prompt,
        purpose="pipelines",
    ),
}


class AskContextSerializer(serializers.Serializer):
    """Validates a send's `context` for whichever surface it names, with that
    surface's own serializer; its errors come back unchanged. Needs
    `context={"user": user}`."""

    surface = serializers.ChoiceField(choices=list(SURFACES))

    def to_internal_value(self, data):
        surface = super().to_internal_value(data)["surface"]
        inner = SURFACES[surface].serializer(data=data, context=self.context)
        inner.is_valid(raise_exception=True)
        return inner.validated_data
