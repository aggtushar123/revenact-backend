"""The Pipelines book's query parameters, parsed once, for either kind.

Organizations' rule (`organizations.params`): a value that is not understood
is dropped rather than rejected, so a stale link or a hand-edited URL still
opens the page. The stage filter has a default, the kind's open stages; a
closed stage is opt-in (`stage=closed_won,…`). `ids` (the selection bar's
"Export selected") reaches every stage, as Organizations' `ids` reaches
churned rows.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from services.accounts.models import User
from services.organizations.params import (
    DEFAULT_LIMIT,
    MAX_IDS,
    MAX_LIMIT,
    RENEWS_WITHIN_DAYS,
    comma_list,
    int_or_none,
)

from .kinds import Kind

SORT_KEYS = ("mrr", "date", "priority", "stage", "title")
GROUPS = ("stage", "month", "parent", "owner", "department", "priority")
DEFAULT_SORT = "-mrr"
#: "Closes or is due within 30/90/180 days": the renewal windows' own days.
DATE_WINDOWS = RENEWS_WITHIN_DAYS
DATE_FILTERS = (*(str(days) for days in DATE_WINDOWS), "overdue", "none")
#: The department value that stands for blank (everyone's) in a filter or group.
NO_DEPARTMENT = "none"
DEPARTMENTS = (*User.Function.values, NO_DEPARTMENT)
#: The owner values that are not a person: no owner, or an owner outside the
#: viewer's organisation (a bad import; the row reads "Not in your book").
UNASSIGNED = "unassigned"
OUTSIDE = "outside"
OWNER_BUCKETS = (UNASSIGNED, OUTSIDE)


@dataclass(frozen=True)
class PipelineParams:
    #: Always set: the kind's open stages unless `stage` or `ids` said otherwise.
    stages: tuple[str, ...]
    search: str = ""
    organisations: tuple[int, ...] = ()
    accounts: tuple[int, ...] = ()
    owner: int | str | None = None
    priorities: tuple[str, ...] = ()
    departments: tuple[str, ...] = ()
    #: One of DATE_FILTERS: a window in days, "overdue" or "none".
    date: str | None = None
    #: "quarter": the stage last changed this calendar quarter.
    changed: str | None = None
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


def _ints(raw):
    parsed = (int_or_none(part) for part in comma_list(raw))
    return tuple(value for value in parsed if value is not None)


def _choices(raw, allowed):
    """The allowed values, in the order given, each once."""
    return tuple(dict.fromkeys(value for value in comma_list(raw) if value in allowed))


def parse_params(query: Mapping, kind: Kind) -> PipelineParams:
    owner_raw = query.get("owner")
    owner = owner_raw if owner_raw in OWNER_BUCKETS else int_or_none(owner_raw)

    ids = None
    if "ids" in query:
        parsed = (int_or_none(part) for part in (query.get("ids") or "").split(",")[:MAX_IDS])
        ids = tuple(value for value in parsed if value is not None)

    stages = _choices(query.get("stage"), kind.stages)
    if not stages:
        stages = kind.stages if ids is not None else kind.open_stages

    sort = query.get("sort") or DEFAULT_SORT
    if sort.removeprefix("-") not in SORT_KEYS:
        sort = DEFAULT_SORT

    group = query.get("group") if query.get("group") in GROUPS else ""
    limit = int_or_none(query.get("limit"))

    return PipelineParams(
        stages=stages,
        search=(query.get("search") or "").strip(),
        organisations=_ints(query.get("organisation")),
        accounts=_ints(query.get("account")),
        owner=owner,
        priorities=_choices(query.get("priority"), kind.model.Priority.values),
        departments=_choices(query.get("department"), DEPARTMENTS),
        date=query.get("date") if query.get("date") in DATE_FILTERS else None,
        changed="quarter" if query.get("changed") == "quarter" else None,
        ids=ids,
        sort=sort,
        group=group,
        group_value=query.get("group_value") if group and "group_value" in query else None,
        cursor=query.get("cursor") or "",
        limit=DEFAULT_LIMIT if limit is None or limit < 1 else min(limit, MAX_LIMIT),
    )
