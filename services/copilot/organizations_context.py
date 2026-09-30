"""Where on Organizations a question was asked, as the client sends it.

The client sends where it is — the view, the portfolio filters from its URL
and an optional focus — never figures or names; the server recomputes the
list (`organizations_grounding`). This module is the gate. Filters go through
the portfolio's own parser (`services.organizations.params.parse_params`), so
a value the list would ignore is dropped here too, and they are stored in one
canonical form: the page's own URL parameters, ready to restore it. A focus is
narrowed to the asker's filtered, visible list, silently, so a refused id says
nothing about whose it was. The labels that name the filters are built here,
on the server, from the asker's own filter options — never from anything the
client sent.
"""

from django.utils import timezone
from rest_framework import serializers

from services.customers.models import Customer
from services.organizations.book import filter_options, filtered_queryset
from services.organizations.params import DEFAULT_SORT, parse_params

from . import portfolio_context
from .dashboard_context import company_ids
from .organization_detail_context import DETAIL, OrganizationDetailContextSerializer
from .portfolio_context import (  # noqa: F401 (re-exported for the grounding and tests)
    MAX_SEARCH_LENGTH,
    NPS_LABELS,
    UNKNOWN,
    VIEWS,
)

#: The portfolio parameters that decide which accounts are in view, plus sort
#: and group so a reopened conversation restores the page. `cursor`, `limit`
#: and `group_value` page the list; they are not part of it.
FILTER_KEYS = (
    "search",
    "owner",
    "lifecycle",
    "health",
    "product",
    "renews_within",
    "nps",
    "ids",
    "include_churned",
    "sort",
    "group",
)


def canonical(params):
    """The parsed params back as URL parameters: only what is set, in one
    spelling, the default sort left out."""
    filters = {}
    if params.search:
        filters["search"] = params.search
    if params.owner is not None:
        filters["owner"] = str(params.owner)
    if params.lifecycles:
        filters["lifecycle"] = ",".join(params.lifecycles)
    if params.health:
        filters["health"] = ",".join(params.health)
    if params.products:
        filters["product"] = ",".join(str(pk) for pk in params.products)
    if params.renews_within is not None:
        filters["renews_within"] = str(params.renews_within)
    if params.nps:
        filters["nps"] = params.nps
    if params.ids is not None:
        filters["ids"] = ",".join(str(pk) for pk in params.ids)
    if params.include_churned:
        filters["include_churned"] = "1"
    if params.sort != DEFAULT_SORT:
        filters["sort"] = params.sort
    if params.group:
        filters["group"] = params.group
    return filters


def clean_filters(raw):
    """The client's filters, through the portfolio's parser, in canonical
    form (`portfolio_context.clean_filters`)."""
    return portfolio_context.clean_filters(
        raw, keys=FILTER_KEYS, parse=parse_params, canonical=canonical
    )


def params_of(filters, *, view=None):
    """Canonical filters as `PortfolioParams`. With a view, a missing `group`
    is the view's default; an explicit `""` stays ungrouped."""
    return portfolio_context.params_of(filters, parse=parse_params, view=view)


def filter_labels(params, options):
    """How the filters read to a person — "Owner: Carl CSM" — named from the
    asker's own filter options (`book.filter_options`, owners from their own
    organisation only). An id those options do not name reads UNKNOWN; the
    filter still applies, so the label never hides a narrowing."""
    owners = {option["value"]: option["name"] for option in options["owners"]}
    products = {option["value"]: option["name"] for option in options["products"]}
    stages = dict(Customer.LifecycleStage.choices)
    health = dict(Customer.HealthCategory.choices)
    labels = []
    if params.ids is not None:
        labels.append(f"Opened from the dashboard ({len(params.ids)})")
    if params.search:
        labels.append(f'Search: "{params.search}"')
    if params.owner == "unassigned":
        labels.append("Owner: Unassigned")
    elif params.owner is not None:
        labels.append(f"Owner: {owners.get(str(params.owner), UNKNOWN)}")
    if params.lifecycles:
        labels.append("Lifecycle: " + ", ".join(stages[value] for value in params.lifecycles))
    if params.health:
        labels.append("Health: " + ", ".join(health[value] for value in params.health))
    if params.products:
        labels.append(
            "Product: " + ", ".join(products.get(str(pk), UNKNOWN) for pk in params.products)
        )
    if params.renews_within is not None:
        labels.append(f"Renews within {params.renews_within} days")
    if params.nps:
        labels.append(f"NPS: {NPS_LABELS[params.nps]}")
    if params.include_churned:
        labels.append("Includes churned")
    return labels


class OrganizationsContextSerializer(serializers.Serializer):
    """Validates an Organizations send's `context`. Needs
    `context={"user": user}`. Anything else the client sends — `labels`
    included — is ignored. A send from one organisation's page
    (`view: "detail"`) is validated by `OrganizationDetailContextSerializer`
    instead, and its errors come back unchanged."""

    surface = serializers.ChoiceField(choices=["organizations"])
    view = serializers.ChoiceField(choices=[*VIEWS, DETAIL])
    filters = serializers.DictField(required=False, default=dict)
    focus = serializers.DictField(allow_null=True, required=False, default=None)

    def to_internal_value(self, data):
        if isinstance(data, dict) and data.get("view") == DETAIL:
            inner = OrganizationDetailContextSerializer(data=data, context=self.context)
            inner.is_valid(raise_exception=True)
            return inner.validated_data
        return super().to_internal_value(data)

    def validate(self, data):
        if data["view"] == DETAIL:
            return data  # already validated whole by its own serializer
        user = self.context["user"]
        filters = clean_filters(data.get("filters") or {})
        params = params_of(filters, view=data["view"])
        return {
            "surface": "organizations",
            "view": data["view"],
            "filters": filters,
            "labels": filter_labels(params, filter_options(user)),
            "focus": self._focus(data.get("focus"), params),
        }

    def _focus(self, focus, params):
        if focus is None:
            return None
        if focus.get("kind") != "companies":
            raise serializers.ValidationError({"focus": {"kind": ["Must be companies."]}})
        ids = company_ids(focus)
        # SOC2:AUTH-02 a focus is narrowed to the asker's filtered, visible list;
        # ids outside it are dropped without saying so
        kept = (
            filtered_queryset(self.context["user"], params, today=timezone.localdate())
            .filter(pk__in=ids)
            .values_list("pk", flat=True)
        )
        return {"kind": "companies", "ids": sorted(kept)}
