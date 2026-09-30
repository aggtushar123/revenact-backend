"""What an Ask on a portfolio page (Organizations, Accounts) shares: the two
views, the rules for reading URL filters, and how a filter is named.

Each surface keeps its own filter keys, parser (`parse_params`) and canonical
form, since the parameters differ (products and churned for organisations,
organisations for accounts). They pass them in here, so both read a client's
filters by the same rules: unknown keys and values are dropped, never
rejected, and an explicit empty `group` survives as "not grouped".
"""

from dataclasses import replace

VIEWS = {"list": "List", "board": "Board"}

#: The group each view opens with when the URL names none (spec §1: health on
#: the List, lifecycle on the Board).
DEFAULT_GROUP = {"list": "health", "board": "lifecycle"}

#: A search longer than this is not a search; it is ignored.
MAX_SEARCH_LENGTH = 100
#: Any value longer than this is ignored (500 ids fit well inside it).
MAX_VALUE_LENGTH = 6000

#: What a label says for an owner, product or organisation the asker may not
#: be told about.
UNKNOWN = "not in your book"

NPS_LABELS = {"promoter": "Promoters", "passive": "Passives", "detractor": "Detractors"}


def _text(value):
    """A filter value as the URL would carry it, or None: text, a whole
    number, or a list of them joined with commas."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(
        isinstance(item, (str, int)) and not isinstance(item, bool) for item in value
    ):
        return ",".join(str(item) for item in value)
    return None


def clean_filters(raw, *, keys, parse, canonical):
    """The client's filters, through the surface's parser (`parse`), in its
    canonical form (`canonical`). Only `keys` are read. Unknown keys and
    values are dropped, never rejected. An explicit empty `group` survives as
    "not grouped"."""
    if not isinstance(raw, dict):
        return {}
    query = {}
    for key in keys:
        if key not in raw:
            continue
        value = _text(raw[key])
        if value is None or len(value) > MAX_VALUE_LENGTH:
            continue
        if key == "search" and len(value.strip()) > MAX_SEARCH_LENGTH:
            continue
        query[key] = value
    filters = canonical(parse(query))
    if query.get("group") == "":
        filters["group"] = ""
    return filters


def params_of(filters, *, parse, view=None):
    """Canonical filters parsed by the surface's `parse`. With a view, a
    missing `group` is the view's default; an explicit `""` stays ungrouped."""
    params = parse(filters)
    if view is not None and "group" not in filters:
        params = replace(params, group=DEFAULT_GROUP[view])
    return params
