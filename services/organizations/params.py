"""The portfolio's query parameters, parsed once.

Every value that is not understood is dropped rather than rejected — the
dashboard endpoints' rule (`forecast.filtered_customers`), so a stale link or a
hand-edited URL still opens the page instead of an error.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from services.customers.models import Customer

SORT_KEYS = ("arr", "health", "renewal", "touch", "risk", "name")

#: Numeric Customer fields a list can be sorted by. The names are the keys the
#: row's `details` groups use, so a pinned chip and its sort share one name.
#: Money among them sorts in the organisation's currency (`shape.MONEY_FIELDS`).
NUMERIC_SORT_KEYS = (
    "arr_billed_at_hq",
    "implementation_fee",
    "total_contract_value",
    "total_forecasted_renewal_revenue",
    "total_contracted_seats",
    "total_active_seats",
    "seat_utilization_percentage",
    "total_hires",
    "nps_score",
    "csat_score",
    "ces_percentage",
    "ai_pulse_value",
    "csm_pulse_score",
)
GROUPS = ("health", "owner", "lifecycle", "product", "renewal")
RENEWS_WITHIN_DAYS = (30, 90, 180)
NPS_BANDS = ("promoter", "passive", "detractor")
DEFAULT_SORT = "-arr"
DEFAULT_LIMIT = 50
MAX_LIMIT = 100
#: The same ceiling `/customers/?ids=` reads — the dashboard's "Open as a list".
MAX_IDS = 500


@dataclass(frozen=True)
class PortfolioParams:
    search: str = ""
    owner: int | str | None = None
    lifecycles: tuple[str, ...] = ()
    health: tuple[str, ...] = ()
    products: tuple[int, ...] = ()
    renews_within: int | None = None
    nps: str | None = None
    #: None when `ids` was not sent; an empty tuple when it was sent with no
    #: usable id, which names nothing rather than the whole book.
    ids: tuple[int, ...] | None = None
    include_churned: bool = False
    sort: str = DEFAULT_SORT
    group: str = ""
    group_value: str | None = None
    cursor: str = ""
    limit: int = DEFAULT_LIMIT

    @property
    def sort_key(self) -> str:
        return self.sort.removeprefix("-")

    @property
    def descending(self) -> bool:
        return self.sort.startswith("-")


def _int(raw):
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _list(raw):
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def parse_params(query: Mapping) -> PortfolioParams:
    owner_raw = query.get("owner")
    owner = "unassigned" if owner_raw == "unassigned" else _int(owner_raw)

    ids = None
    if "ids" in query:
        parsed = (_int(part) for part in (query.get("ids") or "").split(",")[:MAX_IDS])
        ids = tuple(value for value in parsed if value is not None)

    sort = query.get("sort") or DEFAULT_SORT
    if sort.removeprefix("-") not in SORT_KEYS + NUMERIC_SORT_KEYS:
        sort = DEFAULT_SORT

    group = query.get("group") if query.get("group") in GROUPS else ""
    renews_within = _int(query.get("renews_within"))
    limit = _int(query.get("limit"))
    products = (_int(part) for part in _list(query.get("product")))

    return PortfolioParams(
        search=(query.get("search") or "").strip(),
        owner=owner,
        lifecycles=tuple(
            value
            for value in _list(query.get("lifecycle"))
            if value in Customer.LifecycleStage.values
        ),
        health=tuple(
            value for value in _list(query.get("health")) if value in Customer.HealthCategory.values
        ),
        products=tuple(value for value in products if value is not None),
        renews_within=renews_within if renews_within in RENEWS_WITHIN_DAYS else None,
        nps=query.get("nps") if query.get("nps") in NPS_BANDS else None,
        ids=ids,
        include_churned=query.get("include_churned") == "1",
        sort=sort,
        group=group,
        group_value=query.get("group_value") if group and "group_value" in query else None,
        cursor=query.get("cursor") or "",
        limit=DEFAULT_LIMIT if limit is None or limit < 1 else min(limit, MAX_LIMIT),
    )
