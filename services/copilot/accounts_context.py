"""Where on Accounts a question was asked, as the client sends it.

The list and the Board (`view: "list" | "board"`) send the portfolio's URL
filters; the account page (`view: "detail"`) sends the account's id and, from
"Ask about this" on a story item, `focus: {kind, id}`. Nothing else is read:
never a name, never a figure, and never the client's `label`.

Filters go through the portfolio's own parser
(`services.accounts_portfolio.params.parse_params`), so a value the list would
ignore is dropped here too, and they are stored in one canonical form: the
page's own URL parameters, ready to restore it. An organisation filter, an
account or a story item the asker cannot open is a 400 that reads the same
whether it exists or not (spec §3). The label — the chip and the History tag —
is built here, from rows the asker may open: "Accounts · Owner: Carl CSM" on
the list and the Board, the account's own name on its page.
"""

from django.http import Http404
from django.utils import timezone
from rest_framework import serializers

from services.account_story.scope import resolve_account_scope
from services.accounts_portfolio.params import parse_params
from services.customers.models import Customer
from services.customers.scoping import visible_accounts, visible_customers
from services.organizations.params import DEFAULT_SORT

from . import portfolio_context
from .organization_detail_context import DetailFocusSerializer, find_item
from .portfolio_context import NPS_LABELS, UNKNOWN, VIEWS

SURFACE = "accounts"
LIST, BOARD, DETAIL = "list", "board", "detail"
TITLE = "Accounts"
SEPARATOR = " · "

NOT_OPEN_ACCOUNT = "Not an account you can open."
NOT_OPEN_ORGANISATION = "Not an organisation you can open."
NOT_A_STORY_ITEM = "Not a story item you can open."

#: The portfolio parameters that decide which accounts are in view, plus sort
#: and group so a reopened conversation restores the page. `cursor`, `limit`
#: and `group_value` page the list; they are not part of it.
FILTER_KEYS = (
    "search",
    "organisation",
    "owner",
    "lifecycle",
    "health",
    "renews_within",
    "nps",
    "ids",
    "sort",
    "group",
)


def canonical(params):
    """The parsed params back as URL parameters: only what is set, in one
    spelling, the default sort left out."""
    filters = {}
    if params.search:
        filters["search"] = params.search
    if params.organisations:
        filters["organisation"] = ",".join(str(pk) for pk in params.organisations)
    if params.owner is not None:
        filters["owner"] = str(params.owner)
    if params.lifecycles:
        filters["lifecycle"] = ",".join(params.lifecycles)
    if params.health:
        filters["health"] = ",".join(params.health)
    if params.renews_within is not None:
        filters["renews_within"] = str(params.renews_within)
    if params.nps:
        filters["nps"] = params.nps
    if params.ids is not None:
        filters["ids"] = ",".join(str(pk) for pk in params.ids)
    if params.sort != DEFAULT_SORT:
        filters["sort"] = params.sort
    if params.group:
        filters["group"] = params.group
    return filters


def clean_filters(raw):
    """The client's filters, through the portfolio's parser, in canonical
    form (`portfolio_context.clean_filters`): unknown keys and values are
    dropped, never rejected, and an explicit empty `group` survives as "not
    grouped"."""
    return portfolio_context.clean_filters(
        raw, keys=FILTER_KEYS, parse=parse_params, canonical=canonical
    )


def params_of(filters, *, view=None):
    """Canonical filters as `AccountPortfolioParams`. With a view, a missing
    `group` is the view's default; an explicit `""` stays ungrouped."""
    return portfolio_context.params_of(filters, parse=parse_params, view=view)


def organisation_names(user, ids):
    """`{id: name}` of the organisations among `ids` the asker may open."""
    if not ids:
        return {}
    # SOC2:AUTH-02 only organisations the asker may open are named
    return dict(visible_customers(user).filter(pk__in=ids).values_list("pk", "name"))


def _owner_label(user, owner):
    """The owner as `book.filter_options` would offer them: a person in the
    asker's own organisation who owns one of the asker's visible accounts."""
    if owner == "unassigned":
        return "Owner: Unassigned"
    # SOC2:AUTH-02 an owner is named only from the asker's own visible book
    names = list(
        visible_accounts(user)
        .filter(owner_id=owner, owner__organisation=user.organisation)
        .order_by()
        .values_list("owner__name", flat=True)[:1]
    )
    return f"Owner: {names[0] if names else UNKNOWN}"


def filter_labels(user, params, names=None):
    """How the filters read to a person, in the toolbar's order. `names` is
    `organisation_names` when the caller already has it. An id nobody may be
    named for reads UNKNOWN; the filter still applies, so a label never hides
    a narrowing."""
    stages = dict(Customer.LifecycleStage.choices)
    health = dict(Customer.HealthCategory.choices)
    labels = []
    if params.ids is not None:
        labels.append(f"Chosen accounts ({len(set(params.ids))})")
    if params.search:
        labels.append(f'Search: "{params.search}"')
    if params.organisations:
        if names is None:
            names = organisation_names(user, params.organisations)
        labels.append(
            "Organisation: " + ", ".join(names.get(pk, UNKNOWN) for pk in params.organisations)
        )
    if params.owner is not None:
        labels.append(_owner_label(user, params.owner))
    if params.lifecycles:
        labels.append("Lifecycle: " + ", ".join(stages[value] for value in params.lifecycles))
    if params.health:
        labels.append("Health: " + ", ".join(health[value] for value in params.health))
    if params.renews_within is not None:
        labels.append(f"Renews within {params.renews_within} days")
    if params.nps:
        labels.append(f"NPS: {NPS_LABELS[params.nps]}")
    return labels


def list_label(labels):
    """The chip and the History tag: "Accounts · Owner: Carl CSM"."""
    return SEPARATOR.join([TITLE, *labels])


class AccountsContextSerializer(serializers.Serializer):
    """Validates an Accounts send's `context`. Needs `context={"user": user}`.
    Anything else the client sends — a `label` included — is ignored."""

    surface = serializers.ChoiceField(choices=[SURFACE])
    view = serializers.ChoiceField(choices=[*VIEWS, DETAIL])
    filters = serializers.DictField(required=False, default=dict)
    #: No `min_value`: an id that cannot exist is the same 400 as one hidden.
    account = serializers.IntegerField(allow_null=True, required=False)
    focus = DetailFocusSerializer(allow_null=True, required=False, default=None)

    #: Read only from the account page; the list and the Board drop them
    #: unvalidated, so they are never stored with a list context.
    DETAIL_ONLY = ("account", "focus")

    def to_internal_value(self, data):
        if isinstance(data, dict) and data.get("view") != DETAIL:
            data = {key: value for key, value in data.items() if key not in self.DETAIL_ONLY}
        return super().to_internal_value(data)

    def validate(self, data):
        user = self.context["user"]
        if data["view"] == DETAIL:
            return self._detail(user, data)
        return self._list(user, data)

    def _list(self, user, data):
        filters = clean_filters(data.get("filters"))
        params = params_of(filters, view=data["view"])
        names = organisation_names(user, params.organisations)
        # SOC2:AUTH-02 a named organisation must be one the asker may open; the
        # 400 reads the same whether it exists or not
        if set(params.organisations) - set(names):
            raise serializers.ValidationError(
                {"filters": {"organisation": [NOT_OPEN_ORGANISATION]}}
            )
        return {
            "surface": SURFACE,
            "view": data["view"],
            "filters": filters,
            "label": list_label(filter_labels(user, params, names)),
        }

    def _detail(self, user, data):
        pk = data.get("account")
        try:
            # SOC2:AUTH-02 the account must be one the asker may open
            scope = resolve_account_scope(user, pk) if pk is not None else None
        except Http404:
            scope = None
        # the 400 reads the same whether the account exists or not
        if scope is None:
            raise serializers.ValidationError({"account": [NOT_OPEN_ACCOUNT]})
        return {
            "surface": SURFACE,
            "view": DETAIL,
            "account": scope.account.pk,
            "label": scope.account.name,
            "focus": self._focus(user, scope, data["focus"]),
        }

    def _focus(self, user, scope, focus):
        if focus is None:
            return None
        kind, ident = focus["kind"], focus["id"]
        # SOC2:AUTH-02 the item is read with the story's own rules: filed on
        # this account, dated up to today, and admitted by its record rule
        item = find_item(user, scope, kind, ident, account=None, today=timezone.localdate())
        if item is None:
            raise serializers.ValidationError({"focus": [NOT_A_STORY_ITEM]})
        return {"kind": kind, "id": ident}
