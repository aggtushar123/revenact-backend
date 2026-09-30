"""The Accounts portfolio's query parameters, parsed once.

Organizations' rule (`organizations.params`): a value that is not understood
is dropped rather than rejected, so a stale link or a hand-edited URL still
opens the page. The account list has its own filters — organisation instead
of product, and no churned switch (accounts have no churn) — and its own five
sorts and four groups.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from services.customers.models import Customer
from services.organizations.params import (
    DEFAULT_LIMIT,
    DEFAULT_SORT,
    MAX_IDS,
    MAX_LIMIT,
    NPS_BANDS,
    RENEWS_WITHIN_DAYS,
    comma_list,
    int_or_none,
)

SORT_KEYS = ("risk", "arr", "renewal", "health", "name")
GROUPS = ("health", "lifecycle", "owner", "renewal")


@dataclass(frozen=True)
class AccountPortfolioParams:
    search: str = ""
    #: Linked organisation (Customer) ids; an account on any of them matches.
    organisations: tuple[int, ...] = ()
    owner: int | str | None = None
    lifecycles: tuple[str, ...] = ()
    health: tuple[str, ...] = ()
    renews_within: int | None = None
    nps: str | None = None
    #: None when `ids` was not sent; an empty tuple when it was sent with no
    #: usable id, which names nothing rather than the whole book.
    ids: tuple[int, ...] | None = None
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


def parse_params(query: Mapping) -> AccountPortfolioParams:
    owner_raw = query.get("owner")
    owner = "unassigned" if owner_raw == "unassigned" else int_or_none(owner_raw)

    ids = None
    if "ids" in query:
        parsed = (int_or_none(part) for part in (query.get("ids") or "").split(",")[:MAX_IDS])
        ids = tuple(value for value in parsed if value is not None)

    sort = query.get("sort") or DEFAULT_SORT
    if sort.removeprefix("-") not in SORT_KEYS:
        sort = DEFAULT_SORT

    group = query.get("group") if query.get("group") in GROUPS else ""
    renews_within = int_or_none(query.get("renews_within"))
    limit = int_or_none(query.get("limit"))
    organisations = (int_or_none(part) for part in comma_list(query.get("organisation")))

    return AccountPortfolioParams(
        search=(query.get("search") or "").strip(),
        organisations=tuple(value for value in organisations if value is not None),
        owner=owner,
        lifecycles=tuple(
            value
            for value in comma_list(query.get("lifecycle"))
            if value in Customer.LifecycleStage.values
        ),
        health=tuple(
            value
            for value in comma_list(query.get("health"))
            if value in Customer.HealthCategory.values
        ),
        renews_within=renews_within if renews_within in RENEWS_WITHIN_DAYS else None,
        nps=query.get("nps") if query.get("nps") in NPS_BANDS else None,
        ids=ids,
        sort=sort,
        group=group,
        group_value=query.get("group_value") if group and "group_value" in query else None,
        cursor=query.get("cursor") or "",
        limit=DEFAULT_LIMIT if limit is None or limit < 1 else min(limit, MAX_LIMIT),
    )
