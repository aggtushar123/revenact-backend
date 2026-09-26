"""The Ask rails' surfaces, in one place.

For each surface the client may ask from: the serializer that validates its
`context`, the grounding that recomputes its screen for the asker, the
system prompt that fences that digest in `<dashboard_data>`, and the purpose
the call is metered under. `SendMessageView` reads only this table.

Adding a surface means one row here, a purpose in `usage.PURPOSES` and a
`Skill` in `skills.py` (tests fail otherwise), and a book rule for shared
readers (see `asker_book`).
"""

from collections.abc import Callable
from dataclasses import dataclass, replace

from django.utils import timezone
from rest_framework import serializers

from services.customers import forecast
from services.organizations.book import filtered_queryset

from .dashboard_context import DashboardContextSerializer
from .dashboard_grounding import build_dashboard_grounding, dashboard_system_prompt
from .organizations_context import OrganizationsContextSerializer, params_of
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


def _dashboard_book(author, filters):
    return forecast.filtered_customers(author, filters)


def _organizations_book(author, filters):
    """The asker's filtered list as it stands when the reply is read, widened
    by dropping `renews_within` — the one filter that moves with the date —
    so it holds every account the reply could have drawn on when asked.
    Built by the portfolio's own `filtered_queryset` (what `load_portfolio`
    grounded the reply on), so churned rows brought back by `include_churned`
    or a churn lifecycle, and churned or archived rows named by `ids`, are in
    it exactly as they were in the digest."""
    params = replace(params_of(filters), renews_within=None)
    return filtered_queryset(author, params, today=timezone.localdate())


#: How a shared reader's check rebuilds the asker's book, per surface.
BOOKS = {"dashboard": _dashboard_book, "organizations": _organizations_book}


def asker_book(author, context):
    """The customers an Ask reply's aggregates could have drawn on: the
    asker's filtered book under the surface's own filter rules. None for a
    surface this code does not know, so the caller fails closed."""
    book = BOOKS.get(context.get("surface"))
    if book is None:
        return None
    return book(author, context.get("filters") or {})
