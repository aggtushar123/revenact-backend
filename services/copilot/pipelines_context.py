"""Where on Pipelines a question was asked, as the client sends it.

The List and the Board send `{surface: "pipelines", kind, view, filters}`:
the page's kind (`opportunities` | `risks`; opportunities when absent, as
the URL omits it), its view and its URL filters. From "Ask about this" on an
item they also send `focus: {kind: "opportunity" | "risk", id}`. Nothing
else is read: never a name, never a figure, and never the client's `label`.

Filters go through the Pipelines book's own parser
(`services.pipelines_portfolio.params.parse_params`), so a value the page
would ignore is dropped here too. They are stored in one canonical form, the
page's own URL parameters, ready to restore it: `group=none` for "not
grouped", and the default sort, group and stages left out. The default
stages are each view's own (owner ruling 2026-10-01): the open stages on
the List and every stage on the Board, as each page lists them. An organisation
or account filter, or a focus item, the asker cannot open is a 400 that
reads the same whether it exists or not (spec §3). The label — the chip and
the History tag — is built here: "Pipelines · Opportunities · Owner: Carl
CSM".
"""

from dataclasses import replace

from rest_framework import serializers

from services.customers.scoping import visible_accounts
from services.customers.serializers import department_label
from services.organizations.params import comma_list
from services.pipelines_portfolio.book import OUTSIDE_OWNER, owner_q, scope
from services.pipelines_portfolio.kinds import KINDS, OPPORTUNITIES
from services.pipelines_portfolio.params import (
    DEFAULT_SORT,
    NO_DEPARTMENT,
    OUTSIDE,
    UNASSIGNED,
    parse_params,
)

from . import portfolio_context
from .accounts_context import organisation_names
from .portfolio_context import UNKNOWN, VIEWS

SURFACE = "pipelines"
TITLE = "Pipelines"
SEPARATOR = " · "
KIND_TITLES = {"opportunities": "Opportunities", "risks": "Risks"}
#: Both views group by stage when the URL names none (spec §1); the Board
#: also reads "not grouped" as stage (`boardPipelineParams`).
DEFAULT_GROUP = "stage"
#: How the page's URL writes "not grouped" (`pipelineParams.ts`).
NO_GROUP = "none"
BOARD = "board"

NOT_OPEN_ORGANISATION = "Not an organisation you can open."
NOT_OPEN_ACCOUNT = "Not an account you can open."
NOT_OPEN_ITEM = "Not an opportunity or risk you can open."

#: The book's parameters that decide which items are in view, plus sort and
#: group so a reopened conversation restores the page. `cursor`, `limit` and
#: `group_value` page the list; they are not part of it.
FILTER_KEYS = (
    "search",
    "organisation",
    "account",
    "owner",
    "stage",
    "priority",
    "department",
    "date",
    "changed",
    "ids",
    "sort",
    "group",
)

#: How the `date` filter reads, per kind.
DATE_LABELS = {
    "opportunities": {
        "overdue": "Overdue",
        "none": "No close date",
        "within": "Closes within {} days",
    },
    "risks": {"overdue": "Overdue", "none": "No due date", "within": "Due within {} days"},
}


def default_stages(kind, params, view):
    """The stages the page lists when the URL names none: every stage under
    `ids` or on the Board, else (the List) the kind's open ones
    (`pipelineParams.ts` `defaultStages`)."""
    return kind.stages if params.ids is not None or view == BOARD else kind.open_stages


def _joined(values):
    return ",".join(dict.fromkeys(str(value) for value in values))


def canonical(params, kind, view):
    """The parsed params back as the page's URL parameters: only what is set,
    in one spelling, with the default stages, sort and group left out."""
    filters = {}
    if params.search:
        filters["search"] = params.search
    if params.organisations:
        filters["organisation"] = _joined(params.organisations)
    if params.accounts:
        filters["account"] = _joined(params.accounts)
    if params.owner is not None:
        filters["owner"] = str(params.owner)
    if set(params.stages) != set(default_stages(kind, params, view)):
        filters["stage"] = _joined(params.stages)
    if params.priorities:
        filters["priority"] = _joined(params.priorities)
    if params.departments:
        filters["department"] = _joined(params.departments)
    if params.date:
        filters["date"] = params.date
    if params.changed:
        filters["changed"] = params.changed
    if params.ids is not None:
        filters["ids"] = _joined(params.ids)
    if params.sort != DEFAULT_SORT:
        filters["sort"] = params.sort
    if params.group and params.group != DEFAULT_GROUP:
        filters["group"] = params.group
    return filters


def clean_filters(raw, kind, view):
    """The client's filters, through the book's parser, in canonical form
    (`portfolio_context.clean_filters`): unknown keys and values are dropped,
    never rejected. "Not grouped" (`""` or the page's `none`) is stored as
    `none`."""
    filters = portfolio_context.clean_filters(
        raw,
        keys=FILTER_KEYS,
        parse=lambda query: params_of(query, kind, view),
        canonical=lambda params: canonical(params, kind, view),
    )
    if isinstance(raw, dict) and raw.get("group") in ("", NO_GROUP):
        filters["group"] = NO_GROUP
    return filters


def params_of(filters, kind, view):
    """Filters as `PipelineParams`, as the view lists them. A missing `group`
    is the default, stage; `none` is ungrouped on the List and stage on the
    Board. With no stage named (and no `ids`), the Board lists every stage,
    as `pipelineApiQuery` asks the endpoint for."""
    params = parse_params(filters, kind)
    if "group" not in filters or (view == BOARD and not params.group):
        params = replace(params, group=DEFAULT_GROUP)
    named = [stage for stage in comma_list(filters.get("stage")) if stage in kind.stages]
    if not named:
        params = replace(params, stages=default_stages(kind, params, view))
    return params


def account_names(user, ids):
    """`{id: name}` of the accounts among `ids` the asker may open."""
    if not ids:
        return {}
    # SOC2:AUTH-02 only accounts the asker may open are named
    return dict(visible_accounts(user).filter(pk__in=ids).values_list("pk", "name"))


def _owner_label(user, kind, owner):
    if owner == UNASSIGNED:
        return "Owner: Unassigned"
    if owner == OUTSIDE:
        return f"Owner: {OUTSIDE_OWNER.name}"
    # SOC2:AUTH-02 a person is named only as `book.filter_options` offers
    # them: of the asker's own organisation, owning the organisation or
    # account of an item the asker may read; anyone else reads as the page's
    # own "Not in your book"
    row = (
        scope(user, kind)
        .filter(owner_q(owner, user.organisation_id))
        .values_list("customer__owner__name", "account__owner__name")
        .first()
    )
    name = next((value for value in row or () if value), None)
    return f"Owner: {name or OUTSIDE_OWNER.name}"


def _department(value):
    return "No department" if value == NO_DEPARTMENT else department_label(value)


def filter_labels(user, kind, params, view, *, organisations=None, accounts=None):
    """How the filters read to a person, in the toolbar's order.
    `organisations`/`accounts` are `organisation_names`/`account_names` when
    the caller already has them. An id nobody may be named for reads UNKNOWN;
    the filter still applies, so a label never hides a narrowing."""
    labels = []
    if params.ids is not None:
        labels.append(f"Chosen {kind.key} ({len(set(params.ids))})")
    if params.search:
        labels.append(f'Search: "{params.search}"')
    if params.organisations:
        if organisations is None:
            organisations = organisation_names(user, params.organisations)
        labels.append(
            "Organisation: "
            + ", ".join(organisations.get(pk, UNKNOWN) for pk in params.organisations)
        )
    if params.accounts:
        if accounts is None:
            accounts = account_names(user, params.accounts)
        labels.append("Account: " + ", ".join(accounts.get(pk, UNKNOWN) for pk in params.accounts))
    if params.owner is not None:
        labels.append(_owner_label(user, kind, params.owner))
    if set(params.stages) != set(default_stages(kind, params, view)):
        stages = dict(kind.model.Stage.choices)
        labels.append("Stage: " + ", ".join(stages[value] for value in params.stages))
    if params.priorities:
        priorities = dict(kind.model.Priority.choices)
        labels.append("Priority: " + ", ".join(priorities[value] for value in params.priorities))
    if params.departments:
        labels.append("Department: " + ", ".join(_department(v) for v in params.departments))
    if params.date:
        words = DATE_LABELS[kind.key]
        labels.append(words.get(params.date) or words["within"].format(params.date))
    if params.changed:
        labels.append("Stage changed this quarter")
    return labels


def list_label(kind, labels):
    """The chip and the History tag: "Pipelines · Opportunities · Owner: Carl CSM"."""
    return SEPARATOR.join([TITLE, KIND_TITLES[kind.key], *labels])


def focus_of(user, kind, focus):
    """The item "Ask about this" was pressed on, as `{kind, id}`, or None.
    Anything but a readable item of this page's kind is the one 400."""
    if focus is None:
        return None
    ident = focus.get("id") if isinstance(focus, dict) else None
    well_formed = (
        isinstance(focus, dict)
        and focus.get("kind") == kind.item
        and isinstance(ident, int)
        and not isinstance(ident, bool)
    )
    # SOC2:AUTH-02 the item must be one the asker may read: its organisation
    # or account openable and its department theirs (`book.scope`); the 400
    # reads the same whether it exists or not
    if not well_formed or not scope(user, kind).filter(pk=ident).exists():
        raise serializers.ValidationError({"focus": [NOT_OPEN_ITEM]})
    return {"kind": kind.item, "id": ident}


class PipelinesContextSerializer(serializers.Serializer):
    """Validates a Pipelines send's `context`. Needs `context={"user": user}`.
    Anything else the client sends — a `label` included — is ignored."""

    surface = serializers.ChoiceField(choices=[SURFACE])
    kind = serializers.ChoiceField(choices=list(KINDS), required=False, default=OPPORTUNITIES.key)
    view = serializers.ChoiceField(choices=list(VIEWS))
    filters = serializers.DictField(required=False, default=dict)
    #: Checked by hand (`focus_of`): every malformed shape is the same 400 as
    #: an item the asker may not read.
    focus = serializers.JSONField(required=False, allow_null=True, default=None)

    def validate(self, data):
        user = self.context["user"]
        kind = KINDS[data["kind"]]
        view = data["view"]
        filters = clean_filters(data.get("filters"), kind, view)
        params = params_of(filters, kind, view)
        organisations = organisation_names(user, params.organisations)
        # SOC2:AUTH-02 a named organisation must be one the asker may open;
        # the 400 reads the same whether it exists or not
        if set(params.organisations) - set(organisations):
            raise serializers.ValidationError(
                {"filters": {"organisation": [NOT_OPEN_ORGANISATION]}}
            )
        accounts = account_names(user, params.accounts)
        # SOC2:AUTH-02 likewise a named account
        if set(params.accounts) - set(accounts):
            raise serializers.ValidationError({"filters": {"account": [NOT_OPEN_ACCOUNT]}})
        labels = filter_labels(
            user, kind, params, view, organisations=organisations, accounts=accounts
        )
        return {
            "surface": SURFACE,
            "kind": kind.key,
            "view": view,
            "filters": filters,
            "label": list_label(kind, labels),
            "focus": focus_of(user, kind, data["focus"]),
        }
