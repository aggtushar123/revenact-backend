"""Where on the Dashboard a question was asked, as the client sends it.

The client sends *where* it is — area, view, the shared filters and an
optional focus — never figures; the server recomputes what is there
(`dashboard_grounding`). This module is the gate: it checks the place against
the catalogue, keeps only the shared filters, and narrows a focus to what the
viewer may see, silently, so a refused id says nothing about whose it was.
"""

from rest_framework import serializers

from services.attention.rules import current_item
from services.customers import forecast

AREA_LABELS = {
    "overview": "Overview",
    "revenue": "Revenue",
    "health": "Health",
    "support": "Support",
}

#: Mirrors react-ts-app/src/pages/dashboard/areas.ts (view path -> label). The
#: Overview is the dashboard root and has no sub-view.
DASHBOARD_VIEWS = {
    "overview": {},
    "revenue": {"forecast": "Forecast", "customers": "Customers", "products": "Products"},
    "health": {
        "triage": "Triage",
        "divergence": "Divergence",
        "movement": "Movement",
        "renewals": "Renewals",
        "usage": "Usage",
        "activity": "Activity",
        "distribution": "Distribution",
    },
    "support": {"tickets": "Tickets", "topics": "Topics"},
}

#: The filters every book-level view shares; anything else is dropped.
FILTER_KEYS = ("owner", "lifecycle", "customer")

#: A filter value longer than this is not an id or a stage; it is ignored.
MAX_FILTER_LENGTH = 64

#: How many companies a drill's "Ask about these" may carry.
MAX_FOCUS_IDS = 200

#: One message for a key that is malformed, someone else's or gone, so the
#: answer never tells a caller whose item a key was.
NOT_ON_LIST = "Not an item on your list."


def _filter_value(value):
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and len(value) <= MAX_FILTER_LENGTH:
        return value
    return ""


def clean_filters(raw):
    """The three shared keys, always present, as strings. A value that is not
    text or a whole number is ignored ("" means "all"), like the endpoints do."""
    return {key: _filter_value(raw.get(key)) for key in FILTER_KEYS}


def origin_of(context):
    """What a conversation remembers about where it started: the place, not
    the focus."""
    return {key: value for key, value in context.items() if key != "focus"}


def company_ids(focus):
    """A companies focus's ids, checked for shape and size — the same 400 on
    every Ask surface."""
    ids = focus.get("ids")
    if not isinstance(ids, list) or not all(
        isinstance(pk, int) and not isinstance(pk, bool) for pk in ids
    ):
        raise serializers.ValidationError({"focus": {"ids": ["A list of company ids."]}})
    if len(ids) > MAX_FOCUS_IDS:
        raise serializers.ValidationError(
            {"focus": {"ids": [f"At most {MAX_FOCUS_IDS} companies."]}}
        )
    return ids


class DashboardContextSerializer(serializers.Serializer):
    """Validates a send's `context`. Needs `context={"user": user}`."""

    surface = serializers.ChoiceField(choices=["dashboard"])
    area = serializers.ChoiceField(choices=list(DASHBOARD_VIEWS))
    view = serializers.CharField(allow_null=True, required=False, default=None)
    filters = serializers.DictField(required=False, default=dict)
    focus = serializers.DictField(allow_null=True, required=False, default=None)

    def validate(self, data):
        area, view = data["area"], data.get("view")
        views = DASHBOARD_VIEWS[area]
        if (view is None and views) or (view is not None and view not in views):
            raise serializers.ValidationError({"view": [f"Not a view of {AREA_LABELS[area]}."]})
        filters = clean_filters(data.get("filters") or {})
        return {
            "surface": data["surface"],
            "area": area,
            "view": view,
            "filters": filters,
            "focus": self._focus(data.get("focus"), filters),
        }

    def _focus(self, focus, filters):
        if focus is None:
            return None
        user = self.context["user"]
        kind = focus.get("kind")
        if kind == "companies":
            ids = company_ids(focus)
            # SOC2:AUTH-02 a focus is narrowed to the viewer's filtered, visible book;
            # ids outside it are dropped without saying so
            kept = (
                forecast.filtered_customers(user, filters)
                .filter(pk__in=ids)
                .values_list("pk", flat=True)
            )
            return {"kind": "companies", "ids": sorted(kept)}
        if kind == "attention":
            key = focus.get("key")
            if current_item(user, key) is None:
                raise serializers.ValidationError({"focus": {"key": [NOT_ON_LIST]}})
            return {"kind": "attention", "key": key}
        raise serializers.ValidationError({"focus": {"kind": ["Must be companies or attention."]}})
