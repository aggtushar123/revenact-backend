"""The Ask rails' surfaces, in one place.

For each surface the client may ask from: the serializer that validates its
`context`, the grounding that recomputes its screen for the asker, the
system prompt that fences that digest in `<dashboard_data>`, and the purpose
the call is metered under. `SendMessageView` reads only this table.

Adding a surface means one row here, a purpose in `usage.PURPOSES` and a
`Skill` in `skills.py` (tests fail otherwise), and a grounding that fills
`Grounding.customer_ids`, the snapshot a shared reader is checked against
(`views._reply_readable_by`); without it the surface's replies are withheld
from mentioned-only readers.
"""

from collections.abc import Callable
from dataclasses import dataclass

from rest_framework import serializers

from .dashboard_context import DashboardContextSerializer
from .dashboard_grounding import build_dashboard_grounding, dashboard_system_prompt
from .organizations_context import OrganizationsContextSerializer
from .organizations_grounding import build_organizations_grounding, organizations_system_prompt


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
