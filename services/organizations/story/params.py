"""The story's query parameters, parsed once.

Every value that is not understood is dropped rather than rejected, the
portfolio's rule (`organizations.params`): a stale link or a hand-edited URL
still opens the page instead of an error.
"""

from collections.abc import Mapping
from dataclasses import dataclass

#: The filter row's groups and the record kinds behind each. Only kinds with
#: real data exist: Slack, in-app conversations and Revenact Support join
#: Conversations and Tickets when they are real (spec §6).
GROUP_KINDS = {
    "conversations": ("activity", "call", "email", "calendar_event"),
    "tickets": ("ticket",),
    "tasks": ("task", "note"),
    "feedback": ("survey",),
    "health": ("health",),
}
KINDS = tuple(sorted(kind for kinds in GROUP_KINDS.values() for kind in kinds))
#: `account=none`: the organisation's own records, the "Organisation" chip.
NO_ACCOUNT = "none"
DEFAULT_LIMIT = 30
MAX_LIMIT = 100
MAX_Q = 200
#: `Email.thread_id`'s own max_length.
MAX_THREAD = 255


@dataclass(frozen=True)
class StoryParams:
    group: str = ""
    sources: tuple[str, ...] = ()
    #: An account id, `NO_ACCOUNT`, or None for every account and the
    #: organisation's own records.
    account: int | str | None = None
    q: str = ""
    thread: str = ""
    cursor: str = ""
    limit: int = DEFAULT_LIMIT

    @property
    def selected_kinds(self) -> tuple[str, ...]:
        """The group's kinds (every kind without one), narrowed to `source`."""
        kinds = GROUP_KINDS[self.group] if self.group else KINDS
        if self.sources:
            kinds = [kind for kind in kinds if kind in self.sources]
        return tuple(sorted(kinds))

    @property
    def page_kinds(self) -> tuple[str, ...]:
        """The kinds the page reads: a thread is emails only."""
        if not self.thread:
            return self.selected_kinds
        return tuple(kind for kind in self.selected_kinds if kind == "email")


def _int(raw):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def parse_story_params(query: Mapping) -> StoryParams:
    group = query.get("group") if query.get("group") in GROUP_KINDS else ""
    parts = {part.strip() for part in (query.get("source") or "").split(",")}

    raw_account = (query.get("account") or "").strip()
    account_id = _int(raw_account)
    if raw_account == NO_ACCOUNT:
        account = NO_ACCOUNT
    elif account_id is not None and account_id > 0:
        account = account_id
    else:
        account = None

    limit = _int(query.get("limit"))
    return StoryParams(
        group=group,
        sources=tuple(sorted(parts & set(KINDS))),
        account=account,
        q=(query.get("q") or "").strip()[:MAX_Q],
        thread=(query.get("thread") or "").strip()[:MAX_THREAD],
        cursor=query.get("cursor") or "",
        limit=DEFAULT_LIMIT if limit is None or limit < 1 else min(limit, MAX_LIMIT),
    )
