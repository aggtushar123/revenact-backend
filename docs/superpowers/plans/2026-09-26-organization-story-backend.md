# Organisation story, backend (delivery 1: the Story endpoint) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve the organisation page's Story tab from one endpoint, `GET /api/v1/organizations/{id}/story/`: every record on the organisation and on its accounts, newest first across all sources under one keyset cursor, filtered by group, source, account, search and email thread, with counts over the whole filtered set and the Needs attention block, each record read under its own privacy rule.

**Architecture:** A `story/` subpackage inside the existing model-less `services/organizations` app. `params.py` parses the query once. `scope.py` resolves the organisation (404 unless visible) and the accounts on it the viewer may open. `sources.py` defines, per record kind, the base queryset that applies the twice-filter (organisation first, then the record's own rule), the `_at` sort key and search. `items.py` renders one row as the contract's item. `health.py` turns month-end `HealthSnapshot`s into change items. `cursor.py` is the keyset cursor bound to the filters. `build.py` pages (one query per source, merged in Python), counts (one aggregate per source) and calls `attention.py`. The per-type `/customers/{id}/…` endpoints are not touched.

**Tech Stack:** Django 5, DRF, PostgreSQL, `TestCase`/`SimpleTestCase`/`APIClient`, `LiveServerTestCase` for the e2e test.

**Spec:** `react-ts-app/docs/superpowers/specs/2026-09-26-organization-detail-design.md`: §2 "Story (new)" is the contract; §1.6 sets the attention rules and the filters, and "Where today's 13 feed filters go" sets the groups; §4 item 1 "Backend" is this delivery; §5 "Backend" lists the tests. The header's two requests, the account chips' source (`/customers/{id}/accounts/`) and every frontend item in §1 and §4 belong to the frontend plan. Ask on the page (§3) is delivery 3.

## Global Constraints

- **Auth**: `IsAuthenticated`.
- **Scope**: "The organisation must be in `visible_customers(user)`; otherwise the response is 404." Archived and churned organisations open (spec §2 Header: "archived and churned organisations still open").
- **Unknown values are dropped, never a 400.**
- **Params**: `group` ∈ {conversations, tickets, tasks, feedback, health}; `source` (comma list of exact kinds); `account` (an account id, or `none` for "Organisation"); `q`; `cursor`; `limit` (default 30, max 100). This plan adds one more, `thread` (pre-flight #14).
- **Response**: `{items, next_cursor, counts: {by_group, by_account}, attention}`. "Each item is `{id, kind, source, occurred_at, account: {id, name} | null, title, summary, actor: {id, name} | null, link}`." "`counts` and `attention` cover the whole filtered set, not the page." This plan adds `all_day` to the item and `counts.by_kind` (pre-flight #8 and #15).
- **Sources**: Activity (including calls with their summary), Email, CalendarEvent, Ticket, Task, Note, Survey, "health changes from HealthSnapshot", "pulse history only where the model stores it". "Slack, in-app conversations and Revenact Support are added when real." "A source with no real data never appears, either as a filter or in Sources."
- **Privacy (the twice-filter)**: "Each source then applies its own record rule: `visible_tickets` (departments), `visible_emails` (owner-only personal mail), `visible_notes`, activity gating, and the equivalents. Counts, attention and search obey the same rules." "The anomaly title in `attention` is withheld unless the viewer `sees_everything`, as on the Dashboard."
- **Paging**: "One keyset cursor over `(occurred_at, kind, id)` across all sources, bound to the filters (a stale cursor means page one)."
- **Performance**: "A constant number of queries per page, pinned by a test at two book sizes."
- **Links**: "The story only shows records linked to *this* organisation, and filtering by account keeps that restriction."
- **Attention**: renewal overdue or due within 30 days; open High or Critical tickets (count and oldest age); overdue tasks; unanswered Knowledge questions; the latest anomaly.
- "The `/customers/{id}/…` endpoints still handle create and edit … and serve other pages." None of them changes.
- No model and no migration.
- Every endpoint change updates `docs/API_CONTRACTS.md` in the same PR (`.claude/skills/api-contracts`). The product docs (`docs/product/01-prd.md`, `docs/product/05-backend-schema.md`) change in the same PR too. `docs/data-classification.md` and `docs/audit-events.md` are reviewed and need no change (pre-flight #25).
- Tests come in three tiers per `.claude/skills/testing`: unit (`SimpleTestCase`), integration (`TestCase` with `APIClient`), and e2e (`LiveServerTestCase` in the top-level `e2e/`). Run them with `venv/bin/python manage.py test <label> --noinput`.
- `venv/bin/ruff check .` and `venv/bin/ruff format --check .` must pass; `E501` (100 columns) is enforced, and ruff cannot wrap a long string or comment for you. **ruff formats Python inside Markdown fences**, so run `venv/bin/ruff format` on every doc you edit that contains a `python` fence. The doc edits in this plan use only `json`/`text` fences.
- Semgrep runs in CI (`p/django`, `p/security-audit`, `p/owasp-top-ten`). Return opaque strings, not formatted URLs; `next_cursor` is a string. Stored URLs are returned only when they are `http(s)` (pre-flight #13).
- Mark code with the SOC 2 tags the repo uses: `# SOC2:AUTH-02` on object-level visibility rules.
- Commits are conventional (`feat(organizations): …`) and end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- The backend PR merges and deploys before the frontend PR (spec §4).

## Pre-flight: where the spec meets the code

| # | Spec says | Code has | Resolution |
|---|---|---|---|
| 1 | Sources: "Activity (including calls with their summary)" | `Activity` has no text at all (type, date, two counters). CallSense is a separate model, `Call`, whose `summary` is written at log time (typed, or `summarise_transcript` over an attached transcript) and whose `occurred_at` is a timestamp. | Two kinds, `activity` and `call`, both in the Conversations group. A call item's `summary` is `Call.summary`. |
| 2 | "account: {id, name} \| null"; "Organisation" when an item has no account | Every source model (Activity, Email, Call, CalendarEvent, Ticket, Task, Note, Survey, HealthSnapshot, AnomalyEvidence) has nullable `customer` and `account` FKs with a CheckConstraint that exactly one is set. | `account: null` exactly when the row's `customer` is set. A row belongs to this organisation when `customer = this` or its `account` is one of this organisation's accounts (`Scope.parent_q`). |
| 3 | Only records linked to this organisation; filter by account keeps that | `Account.customers` is many-to-many. The per-account endpoints resolve through `get_visible_account` (`visible_accounts`). But `/customers/{id}/accounts/` lists **every** linked account, and `/customers/{id}/surveys/` rolls up every account's surveys, visible or not. | Account-level rows are read only for this organisation's accounts that are in `visible_accounts(user)`, the rule every per-account endpoint uses. That is stricter than the survey roll-up, on purpose. A shared account's rows appear in each of its organisations' stories. `account=<id>` for an account outside that set reads nothing (not a 404). `counts.by_account` lists exactly the accounts in scope, so a chip whose account is absent from it reads 0. |
| 4 | "activity gating" | Since #64, Activity is gated only by its parent's visibility (`CustomerActivityListView` / `AccountActivityListView`); `personal.py` has no Activity rule. Call, CalendarEvent and Survey have no record rule either (`readable_evidence_q`: "a call belongs to no one person"). | These four kinds pass through `_open_to_the_organisation`: the organisation and account scope is their whole rule, as on their endpoints. |
| 5 | "`visible_tickets`, `visible_emails`, `visible_notes` … and the equivalents" | Tasks are personal too: `CustomerTaskListView` applies `personal.visible_tasks` (creator, assignee and the chain above either). | Email → `mail.visibility.visible_emails`; Note → `visible_notes`; Task → `visible_tasks`; Ticket → `visible_tickets`. Each is applied inside `Source.base`, which items, counts, attention and search all start from. |
| 6 | "pulse history only where the model stores it" | `Customer.pulse` / `Account.pulse` are undated dot lists (`pulse_recorded_on` holds only the last day). `HealthSnapshot` stores `health_score`, `csm_pulse_score` and `ai_pulse_value` per month-end, per customer or account. | Pulse history is stored, in `HealthSnapshot`. One kind, `health`: a snapshot whose health category, AI pulse or CSM pulse differs from the same parent's previous snapshot. The first snapshot of a parent has nothing to differ from and is not an item. The dot lists are not a source. |
| 7 | Health & usage: "plus health and lifecycle changes" | Nothing stores lifecycle history. `Customer.lifecycle_stage` is overwritten, and the audit log is security data, not product data. | Lifecycle changes are **not** a source. Documented in API_CONTRACTS as "not stored". |
| 8 | One cursor over `(occurred_at, …)` | Activity, CalendarEvent, Ticket, Note, Survey and HealthSnapshot store a date. Email, Call and Task (`created_at`) store a timestamp. `TIME_ZONE = "UTC"`. | The sort key `_at` is a timestamp: dates are cast to midnight UTC (`Cast(date, DateTimeField())`). `occurred_at` is always an ISO 8601 UTC timestamp. A new boolean, `all_day`, marks a date-only item, whose date is the date part of `occurred_at`, not a moment. |
| 9 | Tasks in the story | `Task.due_date` is often in the future. | A task is in the story from when it was created: `occurred_at = created_at`. Overdue tasks are the attention block's job. |
| 10 | The story | `CalendarEvent.event_date` can be in the future (the old feed listed upcoming meetings). | The story is what has happened: rows with `_at` on or after tomorrow's midnight UTC (`horizon_for(today)`) are left out of items and counts. `/customers/{id}/calendar-events/` still serves upcoming meetings. |
| 11 | Param `source` = "comma list of exact kinds"; the item also has `source` | Tickets and calls carry a `Connector` (provider), synced mail a `MailboxConnection` (provider). | The **param** filters kinds (spec). The **item's** `source` is where the record came from: the connector's `provider` (`zendesk`, `zoom`, …), the mailbox's `provider` (`google`, `microsoft`, `imap`), or `revenact` for anything logged in the app. |
| 12 | `actor: {id, name} \| null` | Most sources store a display name only (`sender_name`, `host_name`, `requester_name`, `author_name`, `assignee_name`). | `actor.id` is a user id when the row links a user, else `null` with the stored name. Email: the mailbox owner for synced sent mail, else `sender_name`. Call: `host_name`. Ticket: `requester_name`. Task: the assignee. Note: the author. Activity, CalendarEvent, Survey and health: `null`. |
| 13 | `link` | The frontend has no per-record routes (only `/organizations/:id`, `/accounts/:id`, …), and "other items open their existing detail", which is the frontend's own card detail. | `link = {thread_id, url}`. `thread_id` is an email's `thread_id` (null when blank). `url` is a ticket's `external_url` or a call's `recording_url`, returned only when it starts with `http://` or `https://`: both are synced or typed strings that land in an `href`. |
| 14 | "Opening an email shows its thread" | There is no thread endpoint, and a thread can span the organisation and its accounts. | A seventh parameter, `thread=<thread_id>`: the story's emails of that thread only, under the same rules. It narrows items, not counts or attention. |
| 15 | "A source with no real data never appears … in Sources" | `by_group` alone cannot say which kinds inside Conversations have data. | `counts.by_kind`, one count per kind, computed from the same aggregates. |
| 16 | "`counts` … cover the whole filtered set" | The chips need a count per account while one account is selected, and the filter row a count per group while one group is selected. | `by_kind` and `by_group` apply `account` and `q` and ignore `group`/`source`. `by_account` applies `group`, `source` and `q` and ignores `account`. All three are under every record rule. `by_group.all` and `by_account.all` are the totals. |
| 17 | Attention "covers the whole filtered set" | `Question` has no account FK, and the renewal date is the organisation's. | Attention follows `account` only: tickets, overdue tasks and the anomaly narrow to it; renewal and questions stay organisation-wide. It ignores `group`, `source`, `q` and `thread`, so searching never hides the block. |
| 18 | Renewal "overdue, or due within 30 days" | The Dashboard's renewal window is 90 days (`attention.rules.RENEWAL_WINDOW_DAYS`). The portfolio gives a churned row no signal. | The spec's 30 days (its own constant). A churned organisation (`churn_date` set or the Churn stage, `book.CHURNED`'s rule) has no renewal item. Tickets reuse `attention.rules.SUPPORT_PRIORITIES`. |
| 19 | "the latest anomaly (title withheld unless the viewer sees everything)" | `attention.rules._anomaly_items` reads LIVE anomalies through `visible_evidence` and titles them with `anomalies.views.title_for`. | The latest LIVE anomaly (by `last_seen_at`) with evidence on this organisation or its in-scope accounts that the viewer may read (`personal.readable_evidence_q`, the rule `visible_evidence` applies). Its title is `title_for(title, 1, sees_all=sees_everything(user))`, so a viewer who does not see everything reads "Similar reports across 1 of your companies". The summary is never returned. |
| 20 | Search "obeys the same rules" | Some kinds have no text: Activity has only a type; Survey only a type and score. | `q` is a case-insensitive contains over: activity type label; call title, summary, host; email subject, body, sender, recipient; meeting title, description; ticket title, number, description, requester; task title, assignee; note title, body, author; survey type label. It applies to the rule-filtered querysets. Health items are never matched, so with `q` they drop out of items and counts. |
| 21 | Cursor "bound to the filters" | The portfolio's `shape.filter_fingerprint` hashes every filter into the cursor. | The same approach: the cursor carries a hash of `group`, `source`, `account`, `q` and `thread`. A cursor cut under other filters, or malformed, reads as the first page. |
| 22 | "+ Add: Log activity, New task, New note, Log survey. These are the existing create flows." | There is no Activity create endpoint (`CustomerActivityListView` is list-only). Calls can be logged (`POST …/calls/`). | Flagged for the frontend plan: "Log activity" can only be logging a call. Nothing is added here. |
| 23 | Constant queries per page | Each personal rule computes the org chart (`subtree_ids`) when it is built. | Every base queryset is built once per request and reused by the page, the counts and the attention block. The count is pinned at 31 for an admin (enumerated in Task 8). |
| 24 | A new endpoint | `services/organizations` is flat and already holds the portfolio's modules. | A subpackage, `services/organizations/story/`, mounted as `organizations/<int:pk>/story/`. |
| 25 | "data-classification if needed" | The endpoint stores nothing and sends nothing outside the tenant. It reads existing classified records under their rules. List reads are not audited (the portfolio list is not). | No change to `docs/data-classification.md` or `docs/audit-events.md`. |

## File Structure

| File | Responsibility |
|---|---|
| `services/organizations/story/__init__.py` (new, empty) | The package |
| `services/organizations/story/params.py` (new) | `StoryParams`, `parse_story_params(query)`, `KINDS`, `GROUP_KINDS`, `NO_ACCOUNT`, the limits |
| `services/organizations/story/cursor.py` (new) | `Cut`, `fingerprint`, `encode_cursor`, `decode_cursor`, `after_q` (SQL side), `is_after` (Python side) |
| `services/organizations/story/scope.py` (new) | `Scope(customer, accounts)`, `Scope.parent_q(account)`, `Scope.account_ref(id)`, `resolve_scope(user, customer_id)` (404) |
| `services/organizations/story/sources.py` (new) | `Source` and `SOURCES` (one per record kind): `base(user, scope, *, horizon)` applies the twice-filter and `_at`; `search_q(q)`; `horizon_for(today)` |
| `services/organizations/story/items.py` (new) | `make_item(...)`, `render(kind, row, scope)`, `clip`, `person`, `safe_url` |
| `services/organizations/story/health.py` (new) | `describe_change(before, after)`, `health_entries(scope)` |
| `services/organizations/story/build.py` (new) | `build_page`, `tally`, `build_counts`, `build_story` |
| `services/organizations/story/attention.py` (new) | `renewal`, `urgent_tickets`, `overdue_tasks`, `open_questions`, `latest_anomaly`, `build_attention` |
| `services/organizations/views.py`, `urls.py` | `OrganizationStoryView` at `organizations/<int:pk>/story/` |
| `services/organizations/tests/story_fixtures.py` (new) | `StoryFixture`: Pizza Hut with EMEA and APAC, one maker per kind |
| `services/organizations/tests/test_story_*.py` (new) | `params`, `cursor`, `sources`, `items`, `health`, `build`, `attention`, `views` |
| `e2e/test_organization_story_flow.py` (new) | End to end: create through the existing endpoints, read the story, filter, search, page, privacy |
| docs | `API_CONTRACTS.md`, `product/01-prd.md`, `product/05-backend-schema.md` |

---

### Task 1: The story's parameters

**Files:**
- Create: `services/organizations/story/__init__.py` (empty), `services/organizations/story/params.py`, `services/organizations/tests/test_story_params.py`

**Interfaces:**
- Produces:
  - `GROUP_KINDS: dict[str, tuple[str, ...]]` (`conversations`, `tickets`, `tasks`, `feedback`, `health`), `KINDS: tuple[str, ...]` (the nine kinds, sorted), `NO_ACCOUNT = "none"`, `DEFAULT_LIMIT = 30`, `MAX_LIMIT = 100`, `MAX_Q = 200`, `MAX_THREAD = 255`.
  - `StoryParams` (frozen dataclass): `group: str = ""`, `sources: tuple[str, ...] = ()`, `account: int | str | None = None` (an id, `NO_ACCOUNT`, or None), `q: str = ""`, `thread: str = ""`, `cursor: str = ""`, `limit: int = 30`; properties `selected_kinds` (group ∩ sources, sorted; what counts read) and `page_kinds` (`selected_kinds`, narrowed to `("email",)` when `thread` is set).
  - `parse_story_params(query: Mapping) -> StoryParams` (a DRF `QueryDict` or a plain `dict`).

- [ ] **Step 1: Write the failing tests**

`services/organizations/tests/test_story_params.py`:

```python
from django.http import QueryDict
from django.test import SimpleTestCase

from services.organizations.story.params import (
    DEFAULT_LIMIT,
    GROUP_KINDS,
    KINDS,
    MAX_LIMIT,
    NO_ACCOUNT,
    StoryParams,
    parse_story_params,
)


class ParseStoryParamsTests(SimpleTestCase):
    def test_nothing_given_is_the_defaults(self):
        params = parse_story_params({})
        self.assertEqual(params, StoryParams())
        self.assertEqual(params.limit, 30)
        self.assertEqual(params.selected_kinds, KINDS)
        self.assertEqual(params.page_kinds, KINDS)

    def test_the_kinds_are_the_nine_with_real_data(self):
        self.assertEqual(
            KINDS,
            ("activity", "calendar_event", "call", "email", "health", "note", "survey", "task", "ticket"),
        )
        self.assertEqual(
            set(GROUP_KINDS), {"conversations", "tickets", "tasks", "feedback", "health"}
        )

    def test_group_narrows_the_kinds(self):
        self.assertEqual(
            parse_story_params({"group": "conversations"}).selected_kinds,
            ("activity", "calendar_event", "call", "email"),
        )
        self.assertEqual(parse_story_params({"group": "tasks"}).selected_kinds, ("note", "task"))
        self.assertEqual(parse_story_params({"group": "mood"}).group, "")

    def test_source_is_a_list_of_exact_kinds(self):
        params = parse_story_params(QueryDict("source=email,%20call,slack,,email"))
        self.assertEqual(params.sources, ("call", "email"))
        self.assertEqual(params.selected_kinds, ("call", "email"))
        self.assertEqual(parse_story_params({"source": "slack"}).sources, ())

    def test_group_and_source_intersect(self):
        params = parse_story_params({"group": "conversations", "source": "email,ticket"})
        self.assertEqual(params.selected_kinds, ("email",))
        params = parse_story_params({"group": "tickets", "source": "email"})
        self.assertEqual(params.selected_kinds, ())

    def test_account_is_an_id_or_none(self):
        self.assertEqual(parse_story_params({"account": "12"}).account, 12)
        self.assertEqual(parse_story_params({"account": "none"}).account, NO_ACCOUNT)
        self.assertIsNone(parse_story_params({"account": "emea"}).account)
        self.assertIsNone(parse_story_params({"account": "-3"}).account)
        self.assertIsNone(parse_story_params({"account": ""}).account)

    def test_q_is_trimmed_and_capped(self):
        self.assertEqual(parse_story_params({"q": "  sso "}).q, "sso")
        self.assertEqual(len(parse_story_params({"q": "x" * 500}).q), 200)

    def test_thread_reads_only_emails_and_leaves_the_counted_kinds_alone(self):
        params = parse_story_params({"thread": " abc "})
        self.assertEqual(params.thread, "abc")
        self.assertEqual(params.page_kinds, ("email",))
        self.assertEqual(params.selected_kinds, KINDS)
        params = parse_story_params({"thread": "abc", "group": "tickets"})
        self.assertEqual(params.page_kinds, ())

    def test_limit_defaults_and_is_capped(self):
        self.assertEqual(parse_story_params({"limit": "10"}).limit, 10)
        self.assertEqual(parse_story_params({"limit": "1000"}).limit, MAX_LIMIT)
        self.assertEqual(parse_story_params({"limit": "0"}).limit, DEFAULT_LIMIT)
        self.assertEqual(parse_story_params({"limit": "lots"}).limit, DEFAULT_LIMIT)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.organizations.tests.test_story_params --noinput`
Expected: an ERROR, `ModuleNotFoundError: No module named 'services.organizations.story'`

- [ ] **Step 3: Implement `params.py`**

Create the empty `services/organizations/story/__init__.py`, then `services/organizations/story/params.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.organizations.tests.test_story_params --noinput`
Expected: all 9 tests pass.

- [ ] **Step 5: Lint and commit**

```bash
venv/bin/ruff check --fix services/organizations && venv/bin/ruff format services/organizations
git add services/organizations/story services/organizations/tests/test_story_params.py
git commit -m "feat(organizations): the story's query parameters

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: The keyset cursor, bound to the filters

**Files:**
- Create: `services/organizations/story/cursor.py`, `services/organizations/tests/test_story_cursor.py`

**Interfaces:**
- Consumes: `StoryParams` and `KINDS` (Task 1).
- Produces:
  - `Cut` (frozen dataclass): `at: datetime` (aware), `kind: str`, `id: int`.
  - `fingerprint(params: StoryParams) -> str`: 16 hex characters over `group`, `sources`, `account`, `q`, `thread`.
  - `encode_cursor(cut: Cut, fp: str) -> str` (unpadded urlsafe base64 JSON) and `decode_cursor(cursor: str, fp: str) -> Cut | None` (None for empty, malformed, tampered or other-filter cursors).
  - `after_q(kind: str, cut: Cut | None) -> Q`: the rows of `kind` that come after `cut` in newest-first `(occurred_at, kind, id)` order, as a filter on the `_at` annotation (Task 3).
  - `is_after(key: tuple[datetime, str, int], cut: Cut | None) -> bool`: the same rule in Python, for the health entries (Task 5).

- [ ] **Step 1: Write the failing tests**

`services/organizations/tests/test_story_cursor.py`:

```python
import base64
import json
from datetime import UTC, datetime

from django.test import SimpleTestCase

from services.organizations.story.cursor import (
    Cut,
    decode_cursor,
    encode_cursor,
    fingerprint,
    is_after,
)
from services.organizations.story.params import StoryParams

AT = datetime(2026, 9, 20, 10, 30, 15, 123456, tzinfo=UTC)


def raw_token(data):
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode()


class CursorTests(SimpleTestCase):
    def test_a_cursor_round_trips(self):
        fp = fingerprint(StoryParams())
        token = encode_cursor(Cut(AT, "email", 7), fp)
        self.assertNotIn("=", token)
        self.assertEqual(decode_cursor(token, fp), Cut(AT, "email", 7))

    def test_a_cursor_is_bound_to_the_filters_it_was_cut_under(self):
        token = encode_cursor(Cut(AT, "email", 7), fingerprint(StoryParams(group="tickets")))
        self.assertIsNone(decode_cursor(token, fingerprint(StoryParams())))

    def test_the_fingerprint_ignores_only_cursor_and_limit(self):
        base = fingerprint(StoryParams())
        self.assertEqual(fingerprint(StoryParams(cursor="x", limit=5)), base)
        for changed in (
            StoryParams(group="tasks"),
            StoryParams(sources=("email",)),
            StoryParams(account=3),
            StoryParams(account="none"),
            StoryParams(q="sso"),
            StoryParams(thread="t-1"),
        ):
            self.assertNotEqual(fingerprint(changed), base, changed)

    def test_garbage_is_the_first_page(self):
        fp = fingerprint(StoryParams())
        truncated = encode_cursor(Cut(AT, "email", 7), fp)[:-3]
        for token in ("", "%%%", "bm90IGpzb24", truncated):
            self.assertIsNone(decode_cursor(token, fp), token)

    def test_a_well_formed_cursor_with_bad_fields_is_the_first_page(self):
        fp = fingerprint(StoryParams())
        good = {"at": AT.isoformat(), "k": "email", "id": 7, "f": fp}
        self.assertEqual(decode_cursor(raw_token(good), fp), Cut(AT, "email", 7))
        for bad in (
            {**good, "k": "slack"},
            {**good, "id": "7"},
            {**good, "id": True},
            {**good, "at": "2026-09-20T10:30:00"},
            {**good, "at": "yesterday"},
            ["not", "a", "dict"],
        ):
            self.assertIsNone(decode_cursor(raw_token(bad), fp), bad)

    def test_is_after_follows_newest_first_order(self):
        cut = Cut(AT, "email", 7)
        self.assertTrue(is_after((AT.replace(hour=9), "ticket", 99), cut))
        self.assertTrue(is_after((AT, "call", 99), cut))
        self.assertTrue(is_after((AT, "email", 6), cut))
        self.assertFalse(is_after((AT, "email", 7), cut))
        self.assertFalse(is_after((AT, "note", 1), cut))
        self.assertFalse(is_after((AT.replace(hour=11), "activity", 1), cut))
        self.assertTrue(is_after((AT, "zzz", 1), None))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.organizations.tests.test_story_cursor --noinput`
Expected: an ERROR, `ModuleNotFoundError: No module named 'services.organizations.story.cursor'`

- [ ] **Step 3: Implement `cursor.py`**

`services/organizations/story/cursor.py`:

```python
"""The story's one keyset cursor.

Items are newest first by `(occurred_at, kind, id)`, all three descending,
across every source. The cursor names the last item served; the next page is
every item that sorts strictly after it. The cut is by value, not by a row
count, so rows added or removed elsewhere never cause a skip or a repeat.

The cursor also carries a hash of the filters it was cut under, the
portfolio's rule (`shape.filter_fingerprint`): changing any filter while
keeping the cursor reads the new list from its first page. A malformed or
tampered cursor reads as absent, the first page too.
"""

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from django.db.models import Q

from .params import KINDS


@dataclass(frozen=True)
class Cut:
    at: datetime
    kind: str
    id: int


def fingerprint(params) -> str:
    """Everything that decides which items a list holds, but not `cursor`
    or `limit`. `sources` is already sorted and de-duplicated."""
    state = [params.group, list(params.sources), params.account, params.q, params.thread]
    return hashlib.sha256(json.dumps(state, separators=(",", ":")).encode()).hexdigest()[:16]


def encode_cursor(cut: Cut, fp: str) -> str:
    raw = json.dumps(
        {"at": cut.at.isoformat(), "k": cut.kind, "id": cut.id, "f": fp},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str, fp: str) -> Cut | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        if not isinstance(data, dict) or data.get("f") != fp:
            return None
        at, kind, ident = datetime.fromisoformat(data["at"]), data["k"], data["id"]
    except (binascii.Error, ValueError, UnicodeError, KeyError, TypeError):
        return None
    if at.tzinfo is None or kind not in KINDS or type(ident) is not int:
        return None
    return Cut(at=at, kind=kind, id=ident)


def after_q(kind: str, cut: Cut | None) -> Q:
    """Rows of `kind` after `cut`, as a filter on the `_at` annotation.

    `kind` is fixed within one source's query, so the tuple comparison
    `(_at, kind, id) < (cut.at, cut.kind, cut.id)` folds to one of three
    shapes."""
    if cut is None:
        return Q()
    if kind < cut.kind:
        return Q(_at__lte=cut.at)
    if kind == cut.kind:
        return Q(_at__lt=cut.at) | Q(_at=cut.at, id__lt=cut.id)
    return Q(_at__lt=cut.at)


def is_after(key, cut: Cut | None) -> bool:
    """`after_q` for an item already in memory: `key` is `(at, kind, id)`."""
    return cut is None or key < (cut.at, cut.kind, cut.id)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.organizations.tests.test_story_cursor --noinput`
Expected: all 6 tests pass. `after_q` is exercised against the database by Task 6's cursor walk.

- [ ] **Step 5: Lint and commit**

```bash
venv/bin/ruff check --fix services/organizations && venv/bin/ruff format services/organizations
git add services/organizations/story/cursor.py services/organizations/tests/test_story_cursor.py
git commit -m "feat(organizations): the story's keyset cursor, bound to its filters

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Scope and sources: the twice-filter, the sort key and search

**Files:**
- Create: `services/organizations/story/scope.py`, `services/organizations/story/sources.py`, `services/organizations/tests/story_fixtures.py`, `services/organizations/tests/test_story_sources.py`

**Interfaces:**
- Consumes: `NO_ACCOUNT` (Task 1); `PortfolioFixture` (`services/organizations/tests/fixtures.py`: `self.today`, `self.org`, `self.admin` (Leadership, sees everything), `self.csm` (Carl, CS), `self.other` (Dana, CS), `self.other_org`, `customer(name, *, owner=<Carl>, organisation=None, **fields)`).
- Produces:
  - `scope.Scope` (frozen dataclass): `customer: Customer`, `accounts: dict[int, str]` (this organisation's accounts the viewer may open, id → name, in name order); `parent_q(account=None) -> Q` (None: the organisation's own rows and its in-scope accounts'; an id: that account only, nothing when out of scope; `NO_ACCOUNT`: the organisation's own rows); `account_ref(account_id) -> {"id", "name"} | None`.
  - `scope.resolve_scope(user, customer_id) -> Scope`, raising `Http404` for an organisation outside `visible_customers(user)`.
  - `sources.Source` (frozen dataclass): `kind`, `model`, `at` (the sort-key expression), `all_day: bool`, `rule(user, queryset) -> queryset`, `text_fields`, `choice_field`, `choices`, `related`; `base(user, scope, *, horizon) -> QuerySet` (organisation scope, then the record rule, annotated `_at`, dated before `horizon`); `search_q(q) -> Q` (`Q()` for an empty `q`).
  - `sources.SOURCES: dict[str, Source]`, keyed by the eight record kinds (every kind but `health`).
  - `sources.horizon_for(today: date) -> datetime`: midnight UTC after `today`.
  - `tests.story_fixtures.StoryFixture(PortfolioFixture)`: `self.pizza` (Carl's), `self.emea`, `self.apac` (its accounts, unowned), `self.engineer` (Erin, Engineering); `account(name, *, customers=None, owner=None)`; `days_ago(n)`; makers `activity`, `email`, `call`, `meeting`, `ticket`, `task`, `note`, `survey`, each `(parent, *, <date>=…, **fields)`, and `snapshot(parent, day, score, *, ai=None, csm=None)`; `make(kind, parent, **fields)`; `scope(user=None, customer=None)`; `base(kind, user=None, customer=None)`.

- [ ] **Step 1: Write the fixture**

`services/organizations/tests/story_fixtures.py`:

```python
from datetime import time, timedelta
from decimal import Decimal

from django.utils import timezone

from services.accounts.models import User
from services.customers.models import (
    Account,
    Activity,
    CalendarEvent,
    Call,
    Email,
    HealthSnapshot,
    Note,
    Survey,
    Task,
    Ticket,
)
from services.organizations.story.scope import resolve_scope
from services.organizations.story.sources import SOURCES, horizon_for

from .fixtures import PortfolioFixture


class StoryFixture(PortfolioFixture):
    """Carl's Pizza Hut with two unowned accounts, EMEA and APAC, and one maker
    per record kind. Every maker dates its row today unless told otherwise
    and files it on `parent`: the organisation, or one of its accounts."""

    def setUp(self):
        super().setUp()
        self.pizza = self.customer("Pizza Hut")
        self.emea = self.account("EMEA")
        self.apac = self.account("APAC")
        self.engineer = User.objects.create_user(
            email="erin@acme.io",
            password="supersecret1",
            name="Erin Engineer",
            organisation=self.org,
            role=User.Role.CSM,
            function=User.Function.ENGINEERING,
        )

    def account(self, name, *, customers=None, owner=None):
        account = Account.objects.create(name=name, owner=owner)
        account.customers.add(*(customers or [self.pizza]))
        return account

    @staticmethod
    def on(parent):
        return {"account": parent} if isinstance(parent, Account) else {"customer": parent}

    def days_ago(self, days):
        return self.today - timedelta(days=days)

    def activity(
        self, parent, *, day=None, activity_type=Activity.ActivityType.HEALTH_CHECK_REVIEW
    ):
        return Activity.objects.create(
            type=activity_type, occurred_at=day or self.today, **self.on(parent)
        )

    def email(
        self,
        parent,
        *,
        at=None,
        subject="Renewal terms",
        body="Can we talk about the renewal?",
        **fields,
    ):
        return Email.objects.create(
            subject=subject,
            sender_name="Pat Buyer",
            recipient_name="Carl CSM",
            body=body,
            sent_at=at or timezone.now(),
            **self.on(parent),
            **fields,
        )

    def call(self, parent, *, at=None, title="Quarterly review", summary="They want SSO.", **fields):
        return Call.objects.create(
            title=title,
            host_name="Carl CSM",
            occurred_at=at or timezone.now(),
            summary=summary,
            **self.on(parent),
            **fields,
        )

    def meeting(self, parent, *, day=None, title="Kickoff", **fields):
        return CalendarEvent.objects.create(
            title=title,
            description="Plan the rollout",
            type=CalendarEvent.EventType.MEETING,
            event_date=day or self.today,
            start_time=time(10),
            end_time=time(11),
            attendee_count=3,
            **self.on(parent),
            **fields,
        )

    def ticket(
        self,
        parent,
        *,
        day=None,
        number="T-1",
        title="Login broken",
        priority=Ticket.Priority.MEDIUM,
        **fields,
    ):
        return Ticket.objects.create(
            ticket_number=number,
            title=title,
            assignee_name="Support",
            priority=priority,
            opened_at=day or self.today,
            **self.on(parent),
            **fields,
        )

    def task(self, parent, *, due=None, title="Send the QBR deck", **fields):
        return Task.objects.create(
            title=title,
            assignee_name="Carl CSM",
            due_date=due or self.today,
            priority=Task.Priority.HIGH,
            **self.on(parent),
            **fields,
        )

    def note(self, parent, *, day=None, title="Champion left", body="Sam moved on.", **fields):
        return Note.objects.create(
            title=title,
            author_name="Carl CSM",
            body=body,
            logged_at=day or self.today,
            **self.on(parent),
            **fields,
        )

    def survey(self, parent, *, day=None, survey_type=Survey.SurveyType.NPS, **fields):
        return Survey.objects.create(
            survey_type=survey_type, sent_at=day or self.today, **self.on(parent), **fields
        )

    def snapshot(self, parent, day, score, *, ai=None, csm=None):
        return HealthSnapshot.objects.create(
            captured_on=day,
            health_score=Decimal(score),
            ai_pulse_value=ai,
            csm_pulse_score=csm,
            **self.on(parent),
        )

    def make(self, kind, parent, **fields):
        makers = {
            "activity": self.activity,
            "calendar_event": self.meeting,
            "call": self.call,
            "email": self.email,
            "note": self.note,
            "survey": self.survey,
            "task": self.task,
            "ticket": self.ticket,
        }
        return makers[kind](parent, **fields)

    def scope(self, user=None, customer=None):
        return resolve_scope(user or self.csm, (customer or self.pizza).pk)

    def base(self, kind, user=None, customer=None):
        user = user or self.csm
        return SOURCES[kind].base(user, self.scope(user, customer), horizon=horizon_for(self.today))
```

- [ ] **Step 2: Write the failing tests**

`services/organizations/tests/test_story_sources.py`:

```python
from datetime import UTC, datetime, time, timedelta

from django.http import Http404
from django.utils import timezone

from services.customers.models import Activity, Customer, Survey, Task
from services.organizations.story.params import NO_ACCOUNT
from services.organizations.story.scope import resolve_scope
from services.organizations.story.sources import SOURCES

from .story_fixtures import StoryFixture

RECORD_KINDS = tuple(SOURCES)


def ids(queryset):
    return set(queryset.values_list("id", flat=True))


class ScopeTests(StoryFixture):
    def test_an_organisation_the_viewer_cannot_open_is_a_404(self):
        danas = self.customer("Dana's", owner=self.other)
        globex = self.customer("Globex's", owner=None, organisation=self.other_org)
        for customer_id in (danas.pk, globex.pk, 999_999):
            with self.assertRaises(Http404):
                resolve_scope(self.csm, customer_id)

    def test_archived_and_churned_organisations_still_open(self):
        gone = self.customer(
            "Gone", is_archived=True, lifecycle_stage=Customer.LifecycleStage.CHURN
        )
        self.assertEqual(resolve_scope(self.csm, gone.pk).customer, gone)

    def test_accounts_are_this_organisation_s_that_the_viewer_may_open(self):
        taco = self.customer("Taco Co")
        self.account("Taco only", customers=[taco])
        accounts = list(self.scope().accounts.items())
        self.assertEqual(accounts, [(self.apac.pk, "APAC"), (self.emea.pk, "EMEA")])
        open_co = self.customer("Open Co", owner=None)
        free = self.account("Free div", customers=[open_co])
        self.account("Dana's div", customers=[open_co], owner=self.other)
        self.assertEqual(self.scope(customer=open_co).accounts, {free.pk: "Free div"})

    def test_parent_q_narrows_to_one_account_or_the_organisation(self):
        on_org, on_emea, on_apac = self.note(self.pizza), self.note(self.emea), self.note(self.apac)
        scope = self.scope()
        rows = self.base("note")
        self.assertEqual(ids(rows), {on_org.pk, on_emea.pk, on_apac.pk})
        self.assertEqual(ids(rows.filter(scope.parent_q(self.emea.pk))), {on_emea.pk})
        self.assertEqual(ids(rows.filter(scope.parent_q(NO_ACCOUNT))), {on_org.pk})
        stranger = self.account("Not ours", customers=[self.customer("Taco Co")])
        self.assertEqual(ids(rows.filter(scope.parent_q(stranger.pk))), set())


class SourceScopeTests(StoryFixture):
    def test_every_source_reads_only_this_organisation_and_its_accounts(self):
        taco = self.customer("Taco Co")
        taco_div = self.account("Taco div", customers=[taco])
        for kind in RECORD_KINDS:
            with self.subTest(kind=kind):
                mine = {self.make(kind, self.pizza).pk, self.make(kind, self.emea).pk}
                self.make(kind, taco)
                self.make(kind, taco_div)
                self.assertEqual(ids(self.base(kind)), mine)

    def test_records_on_an_account_the_viewer_may_not_open_stay_out(self):
        open_co = self.customer("Open Co", owner=None)
        free = self.account("Free div", customers=[open_co])
        danas = self.account("Dana's div", customers=[open_co], owner=self.other)
        for kind in RECORD_KINDS:
            with self.subTest(kind=kind):
                seen = {self.make(kind, open_co).pk, self.make(kind, free).pk}
                self.make(kind, danas)
                self.assertEqual(ids(self.base(kind, customer=open_co)), seen)

    def test_a_shared_account_s_records_belong_to_each_of_its_organisations(self):
        taco = self.customer("Taco Co")
        shared = self.account("Shared div", customers=[self.pizza, taco])
        note = self.note(shared)
        self.assertIn(note.pk, ids(self.base("note")))
        self.assertIn(note.pk, ids(self.base("note", customer=taco)))


class RecordRuleTests(StoryFixture):
    def test_mail_is_its_mailbox_owner_s_and_their_chain_s(self):
        mine = self.email(self.pizza, mailbox_owner=self.csm)
        danas = self.email(self.emea, mailbox_owner=self.other)
        logged = self.email(self.pizza)
        self.assertEqual(ids(self.base("email")), {mine.pk, logged.pk})
        self.other.reports_to = self.csm
        self.other.save(update_fields=["reports_to"])
        self.assertEqual(ids(self.base("email")), {mine.pk, danas.pk, logged.pk})

    def test_a_note_is_its_author_s_and_their_chain_s(self):
        mine = self.note(self.pizza, author=self.csm)
        self.note(self.pizza, author=self.other)
        seeded = self.note(self.emea)
        self.assertEqual(ids(self.base("note")), {mine.pk, seeded.pk})

    def test_a_task_is_its_creator_s_and_its_assignee_s(self):
        handed = self.task(self.pizza, created_by=self.other, assignee=self.csm)
        self.task(self.pizza, created_by=self.other, assignee=self.other)
        seeded = self.task(self.apac)
        self.assertEqual(ids(self.base("task")), {handed.pk, seeded.pk})

    def test_a_ticket_is_its_department_s_and_leadership_reads_all(self):
        everyone = self.ticket(self.pizza)
        ours = self.ticket(self.pizza, department="cs")
        engineering = self.ticket(self.emea, department="engineering")
        self.assertEqual(ids(self.base("ticket")), {everyone.pk, ours.pk})
        self.assertEqual(
            ids(self.base("ticket", user=self.admin)), {everyone.pk, ours.pk, engineering.pk}
        )


class OccurredAtTests(StoryFixture):
    def test_dates_read_as_midnight_utc_and_timestamps_as_themselves(self):
        day = self.days_ago(3)
        midnight = datetime.combine(day, time.min, tzinfo=UTC)
        activity = self.activity(self.pizza, day=day)
        self.assertEqual(self.base("activity").get(pk=activity.pk)._at, midnight)
        sent = timezone.now() - timedelta(hours=5)
        email = self.email(self.pizza, at=sent)
        self.assertEqual(self.base("email").get(pk=email.pk)._at, sent)
        answered = self.survey(
            self.pizza,
            day=self.days_ago(9),
            responded_at=day,
            status=Survey.Status.RESPONDED,
            score=40,
        )
        self.assertEqual(self.base("survey").get(pk=answered.pk)._at, midnight)

    def test_a_task_reads_as_when_it_was_created_not_when_it_is_due(self):
        task = self.task(self.pizza, due=self.today + timedelta(days=30))
        created = Task.objects.get(pk=task.pk).created_at
        self.assertEqual(self.base("task").get(pk=task.pk)._at, created)

    def test_nothing_dated_after_today_is_in_the_story(self):
        today = self.meeting(self.pizza)
        self.meeting(self.pizza, day=self.today + timedelta(days=1))
        self.assertEqual(ids(self.base("calendar_event")), {today.pk})


class SearchTests(StoryFixture):
    def search(self, kind, q):
        return ids(self.base(kind).filter(SOURCES[kind].search_q(q)))

    def test_text_fields_match_case_insensitively(self):
        email = self.email(self.pizza, body="They asked about SSO pricing")
        self.email(self.pizza)
        self.assertEqual(self.search("email", "sso"), {email.pk})
        ticket = self.ticket(self.pizza, number="TKT-1042")
        self.assertEqual(self.search("ticket", "tkt-1042"), {ticket.pk})
        call = self.call(self.pizza, summary="Renewal is at risk")
        self.call(self.pizza)
        self.assertEqual(self.search("call", "AT RISK"), {call.pk})

    def test_activity_and_survey_match_on_their_type_label(self):
        check = self.activity(self.pizza)
        self.activity(self.pizza, activity_type=Activity.ActivityType.ONBOARDING_MILESTONE)
        self.assertEqual(self.search("activity", "health check"), {check.pk})
        nps = self.survey(self.pizza)
        self.survey(self.pizza, survey_type=Survey.SurveyType.CSAT)
        self.assertEqual(self.search("survey", "nps"), {nps.pk})

    def test_no_match_is_nothing_and_no_query_is_everything(self):
        note = self.note(self.pizza)
        self.activity(self.pizza)
        self.assertEqual(self.search("note", "zebra"), set())
        self.assertEqual(self.search("activity", "zebra"), set())
        self.assertEqual(self.search("note", ""), {note.pk})
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.organizations.tests.test_story_sources --noinput`
Expected: an ERROR, `ModuleNotFoundError: No module named 'services.organizations.story.scope'`

- [ ] **Step 4: Implement `scope.py`**

`services/organizations/story/scope.py`:

```python
"""Which organisation, and which of its accounts: the first half of the
twice-filter.

The organisation must be one the viewer may open (`visible_customers`), or the
response is a 404 that does not confirm it exists. Its accounts are the ones
linked to it that the viewer may open (`visible_accounts`), the rule every
per-account endpoint applies through `get_visible_account`. An account shared
with another organisation is read here, as it is on its own page; a record on
another organisation, or on an account not linked to this one, never is.
"""

from dataclasses import dataclass

from django.db.models import Q
from django.shortcuts import get_object_or_404

from services.customers.models import Customer
from services.customers.scoping import visible_accounts, visible_customers

from .params import NO_ACCOUNT


@dataclass(frozen=True)
class Scope:
    customer: Customer
    #: This organisation's accounts the viewer may open, id -> name, by name.
    accounts: dict[int, str]

    def parent_q(self, account=None) -> Q:
        """Rows filed on this organisation or on one of its accounts in scope.
        `account` narrows: an id to that account (nothing when it is not in
        scope), `NO_ACCOUNT` to the organisation's own rows."""
        if account == NO_ACCOUNT:
            return Q(customer_id=self.customer.pk)
        if account is not None:
            return Q(account_id=account) if account in self.accounts else Q(pk__in=[])
        return Q(customer_id=self.customer.pk) | Q(account_id__in=list(self.accounts))

    def account_ref(self, account_id):
        if account_id is None:
            return None
        return {"id": account_id, "name": self.accounts[account_id]}


def resolve_scope(user, customer_id) -> Scope:
    # SOC2:AUTH-02 the organisation must be one the viewer may open, else 404
    customer = get_object_or_404(visible_customers(user), pk=customer_id)
    accounts = dict(
        visible_accounts(user)
        .filter(customers=customer)
        .order_by("name", "id")
        .values_list("id", "name")
    )
    return Scope(customer=customer, accounts=accounts)
```

- [ ] **Step 5: Implement `sources.py`**

`services/organizations/story/sources.py`:

```python
"""Which rows each story source reads: the second half of the twice-filter.

A row is read when it is filed on this organisation or one of its accounts in
scope (`Scope.parent_q`) **and** its own record rule admits it, the rule its
per-type endpoint applies: mail is its mailbox owner's and their chain's,
notes their author's and their chain's, tasks their creator's and assignee's
and the chains above them, tickets their department's. Activities, calls,
meetings and surveys carry no author, mailbox or department, so the
organisation's rule is their whole rule, as on their endpoints (#64 gated
activities exactly so).

Items, counts, the attention block and search all start from `Source.base`,
so none of them can read a row the others would not.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from django.db.models import DateTimeField, F, Q
from django.db.models.functions import Cast, Coalesce

from services.customers.models import (
    Activity,
    CalendarEvent,
    Call,
    Email,
    Note,
    Survey,
    Task,
    Ticket,
)
from services.customers.personal import visible_notes, visible_tasks, visible_tickets
from services.mail.visibility import visible_emails


def _open_to_the_organisation(user, queryset):
    """No record rule beyond the organisation's: see the module docstring."""
    return queryset


def _day(expression):
    """A date as the story's timestamp: midnight UTC, the project TIME_ZONE."""
    return Cast(expression, DateTimeField())


def _labels(choices, q):
    needle = q.casefold()
    return [value for value, label in choices if needle in str(label).casefold()]


def horizon_for(today: date) -> datetime:
    """The first moment after `today`. Nothing dated from then on has
    happened yet, so it is not story (an upcoming meeting, a future-dated
    ticket)."""
    return datetime.combine(today + timedelta(days=1), time.min, tzinfo=UTC)


@dataclass(frozen=True)
class Source:
    kind: str
    model: type
    #: The sort key, annotated as `_at`: always a timestamp.
    at: object
    #: True when the model stores only a date.
    all_day: bool
    rule: Callable
    text_fields: tuple[str, ...] = ()
    choice_field: str = ""
    choices: tuple = ()
    related: tuple[str, ...] = ()

    def base(self, user, scope, *, horizon):
        rows = self.model.objects.filter(scope.parent_q())
        # SOC2:AUTH-02 each record's own rule, after the organisation's
        return self.rule(user, rows).annotate(_at=self.at).filter(_at__lt=horizon)

    def search_q(self, q) -> Q:
        if not q:
            return Q()
        match = Q(pk__in=[])
        for field in self.text_fields:
            match |= Q(**{f"{field}__icontains": q})
        if self.choice_field:
            match |= Q(**{f"{self.choice_field}__in": _labels(self.choices, q)})
        return match


SOURCES = {
    "activity": Source(
        kind="activity",
        model=Activity,
        at=_day("occurred_at"),
        all_day=True,
        rule=_open_to_the_organisation,
        choice_field="type",
        choices=tuple(Activity.ActivityType.choices),
    ),
    "calendar_event": Source(
        kind="calendar_event",
        model=CalendarEvent,
        at=_day("event_date"),
        all_day=True,
        rule=_open_to_the_organisation,
        text_fields=("title", "description"),
    ),
    "call": Source(
        kind="call",
        model=Call,
        at=F("occurred_at"),
        all_day=False,
        rule=_open_to_the_organisation,
        text_fields=("title", "summary", "host_name"),
        related=("connector",),
    ),
    "email": Source(
        kind="email",
        model=Email,
        at=F("sent_at"),
        all_day=False,
        rule=visible_emails,
        text_fields=("subject", "body", "sender_name", "recipient_name"),
        related=("mailbox", "mailbox_owner"),
    ),
    "note": Source(
        kind="note",
        model=Note,
        at=_day("logged_at"),
        all_day=True,
        rule=visible_notes,
        text_fields=("title", "body", "author_name"),
        related=("author",),
    ),
    "survey": Source(
        kind="survey",
        model=Survey,
        at=_day(Coalesce("responded_at", "sent_at")),
        all_day=True,
        rule=_open_to_the_organisation,
        choice_field="survey_type",
        choices=tuple(Survey.SurveyType.choices),
    ),
    "task": Source(
        kind="task",
        model=Task,
        at=F("created_at"),
        all_day=False,
        rule=visible_tasks,
        text_fields=("title", "assignee_name"),
        related=("assignee",),
    ),
    "ticket": Source(
        kind="ticket",
        model=Ticket,
        at=_day("opened_at"),
        all_day=True,
        rule=visible_tickets,
        text_fields=("title", "ticket_number", "description", "requester_name"),
        related=("connector",),
    ),
}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.organizations.tests.test_story_sources --noinput`
Expected: all 17 tests pass.

- [ ] **Step 7: Lint and commit**

```bash
venv/bin/ruff check --fix services/organizations && venv/bin/ruff format services/organizations
git add services/organizations/story services/organizations/tests/story_fixtures.py services/organizations/tests/test_story_sources.py
git commit -m "feat(organizations): story scope and sources under each record's own rule

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: How a row reads: the item

**Files:**
- Create: `services/organizations/story/items.py`, `services/organizations/tests/test_story_items.py`

**Interfaces:**
- Consumes: `Scope.account_ref` (Task 3), `SOURCES[kind].all_day` and `.related` (Task 3), `StoryFixture` (Task 3).
- Produces:
  - `SUMMARY_CHARS = 240`, `REVENACT = "revenact"`.
  - `clip(text, limit=SUMMARY_CHARS) -> str`, `person(user=None, name="") -> {"id", "name"} | None`, `safe_url(raw) -> str | None`.
  - `make_item(scope, *, ident, kind, at, all_day, account_id, title, summary="", actor=None, source=REVENACT, thread_id="", url="") -> dict`: the contract's item, with keys `id, kind, source, occurred_at, all_day, account, title, summary, actor, link` (`link = {"thread_id", "url"}`). Task 5 builds health items with it.
  - `render(kind, row, scope) -> dict`: one row of a record source (annotated `_at`, with `SOURCES[kind].related` selected) as an item.

- [ ] **Step 1: Write the failing tests**

`services/organizations/tests/test_story_items.py`:

```python
from django.test import SimpleTestCase

from services.connectors.models import Connector
from services.customers.models import Email, Survey, Ticket
from services.mail.models import MailboxConnection
from services.organizations.story.items import SUMMARY_CHARS, clip, person, render, safe_url
from services.organizations.story.sources import SOURCES

from .story_fixtures import StoryFixture


class HelperTests(SimpleTestCase):
    def test_clip_flattens_whitespace_and_cuts_at_a_word(self):
        self.assertEqual(clip("  two\n\nlines  "), "two lines")
        self.assertEqual(clip(None), "")
        clipped = clip("word " * 100)
        self.assertLessEqual(len(clipped), SUMMARY_CHARS)
        self.assertTrue(clipped.endswith("word…"))

    def test_person_is_the_user_or_the_stored_name(self):
        self.assertIsNone(person())
        self.assertIsNone(person(name="  "))
        self.assertEqual(person(name="Pat Buyer"), {"id": None, "name": "Pat Buyer"})

    def test_only_web_links_are_returned(self):
        self.assertEqual(safe_url("https://acme.zendesk.com/t/1"), "https://acme.zendesk.com/t/1")
        self.assertEqual(safe_url("HTTP://x.io/a"), "HTTP://x.io/a")
        for raw in ("", None, "javascript:alert(1)", "ftp://x.io", "//x.io"):
            self.assertIsNone(safe_url(raw), raw)


class RenderTests(StoryFixture):
    def item(self, kind, record, user=None):
        user = user or self.csm
        row = self.base(kind, user).select_related(*SOURCES[kind].related).get(pk=record.pk)
        return render(kind, row, self.scope(user))

    def test_every_item_has_the_contract_s_fields(self):
        activity = self.activity(self.emea, day=self.days_ago(2))
        self.assertEqual(
            self.item("activity", activity),
            {
                "id": activity.pk,
                "kind": "activity",
                "source": "revenact",
                "occurred_at": f"{self.days_ago(2).isoformat()}T00:00:00+00:00",
                "all_day": True,
                "account": {"id": self.emea.pk, "name": "EMEA"},
                "title": "Health Check Review",
                "summary": "",
                "actor": None,
                "link": {"thread_id": None, "url": None},
            },
        )

    def test_an_organisation_level_record_has_no_account(self):
        self.assertIsNone(self.item("note", self.note(self.pizza))["account"])

    def test_a_call_carries_its_callsense_summary_and_recording(self):
        zoom = Connector.objects.create(
            organisation=self.org, provider=Connector.Provider.ZOOM, name="Zoom"
        )
        call = self.call(
            self.pizza,
            summary="They want SSO.\n\nRenewal in Q3.",
            connector=zoom,
            recording_url="https://zoom.us/rec/1",
        )
        item = self.item("call", call)
        self.assertEqual(item["title"], "Quarterly review")
        self.assertEqual(item["summary"], "They want SSO. Renewal in Q3.")
        self.assertEqual(item["source"], "zoom")
        self.assertEqual(item["actor"], {"id": None, "name": "Carl CSM"})
        self.assertEqual(item["link"], {"thread_id": None, "url": "https://zoom.us/rec/1"})
        self.assertFalse(item["all_day"])

    def test_synced_mail_names_its_mailbox_and_thread(self):
        mailbox = MailboxConnection.objects.create(
            organisation=self.org,
            user=self.csm,
            provider=MailboxConnection.Provider.GOOGLE,
            address="carl@acme.io",
            credentials="x",
        )
        sent = self.email(
            self.pizza,
            mailbox=mailbox,
            mailbox_owner=self.csm,
            direction=Email.Direction.SENT,
            thread_id="t-1",
        )
        item = self.item("email", sent)
        self.assertEqual(item["source"], "google")
        self.assertEqual(item["actor"], {"id": self.csm.pk, "name": "Carl CSM"})
        self.assertEqual(item["link"]["thread_id"], "t-1")
        logged = self.item("email", self.email(self.pizza))
        self.assertEqual(logged["source"], "revenact")
        self.assertEqual(logged["actor"], {"id": None, "name": "Pat Buyer"})
        self.assertEqual(logged["summary"], "Can we talk about the renewal?")
        self.assertIsNone(logged["link"]["thread_id"])

    def test_a_ticket_reads_number_priority_status_and_links_out_safely(self):
        desk = Connector.objects.create(
            organisation=self.org, provider=Connector.Provider.ZENDESK, name="Support desk"
        )
        ticket = self.ticket(
            self.pizza,
            number="TKT-9",
            priority=Ticket.Priority.HIGH,
            connector=desk,
            requester_name="Pat Buyer",
            external_url="https://acme.zendesk.com/t/9",
        )
        item = self.item("ticket", ticket)
        self.assertEqual(item["summary"], "TKT-9 · High · Open")
        self.assertEqual(item["source"], "zendesk")
        self.assertEqual(item["actor"], {"id": None, "name": "Pat Buyer"})
        self.assertEqual(item["link"]["url"], "https://acme.zendesk.com/t/9")
        Ticket.objects.filter(pk=ticket.pk).update(external_url="javascript:alert(1)")
        self.assertIsNone(self.item("ticket", ticket)["link"]["url"])

    def test_a_task_reads_its_due_date_and_assignee(self):
        item = self.item("task", self.task(self.pizza, assignee=self.csm))
        self.assertEqual(item["summary"], f"Due {self.today.isoformat()} · High · Pending")
        self.assertEqual(item["actor"], {"id": self.csm.pk, "name": "Carl CSM"})

    def test_a_note_reads_its_body_and_author(self):
        item = self.item("note", self.note(self.pizza, author=self.csm))
        self.assertEqual((item["title"], item["summary"]), ("Champion left", "Sam moved on."))
        self.assertEqual(item["actor"], {"id": self.csm.pk, "name": "Carl CSM"})

    def test_a_meeting_reads_its_type_time_and_attendees(self):
        item = self.item("calendar_event", self.meeting(self.pizza))
        self.assertEqual(item["title"], "Kickoff")
        self.assertEqual(item["summary"], "Meeting · 10:00–11:00 · 3 attendees · Plan the rollout")
        self.assertTrue(item["all_day"])

    def test_a_survey_reads_its_state(self):
        sent = self.item("survey", self.survey(self.pizza))
        self.assertEqual((sent["title"], sent["summary"]), ("NPS survey", "Sent · awaiting a response"))
        answered = self.survey(
            self.pizza, status=Survey.Status.RESPONDED, score=40, responded_at=self.today
        )
        self.assertEqual(self.item("survey", answered)["summary"], "Responded · score 40")
        expired = self.survey(self.pizza, status=Survey.Status.EXPIRED)
        self.assertEqual(self.item("survey", expired)["summary"], "Expired without a response")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.organizations.tests.test_story_items --noinput`
Expected: an ERROR, `ModuleNotFoundError: No module named 'services.organizations.story.items'`

- [ ] **Step 3: Implement `items.py`**

`services/organizations/story/items.py`:

```python
"""How one record reads in the story: the contract's item.

`title` and `summary` are built from the row's own fields; the row has
already passed its record rule (`sources.py`), so its text may be shown to
this viewer. `summary` is one line: whitespace flattened, cut at a word.
`actor` is a user when the row links one, else the name the row stores.
`source` is where the record came from: a connector's or mailbox's provider,
or `revenact` for anything logged in the app. `link.url` is returned only
for `http(s)` URLs, because synced and typed URLs land in an `href`.
"""

from services.customers.models import Email, Survey

from .sources import SOURCES

SUMMARY_CHARS = 240
REVENACT = "revenact"


def clip(text, limit=SUMMARY_CHARS) -> str:
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    head = flat[: limit - 1]
    if " " in head:
        head = head.rsplit(" ", 1)[0]
    return head + "…"


def person(user=None, name=""):
    if user is not None:
        return {"id": user.pk, "name": user.name}
    name = (name or "").strip()
    return {"id": None, "name": name} if name else None


def safe_url(raw):
    raw = raw or ""
    return raw if raw.lower().startswith(("https://", "http://")) else None


def make_item(
    scope,
    *,
    ident,
    kind,
    at,
    all_day,
    account_id,
    title,
    summary="",
    actor=None,
    source=REVENACT,
    thread_id="",
    url="",
):
    return {
        "id": ident,
        "kind": kind,
        "source": source,
        "occurred_at": at.isoformat(),
        "all_day": all_day,
        "account": scope.account_ref(account_id),
        "title": title,
        "summary": summary,
        "actor": actor,
        "link": {"thread_id": thread_id or None, "url": safe_url(url)},
    }


def _origin(connector):
    return connector.provider if connector is not None else REVENACT


def _activity(row):
    return {"title": row.get_type_display()}


def _calendar_event(row):
    parts = [row.get_type_display(), f"{row.start_time:%H:%M}–{row.end_time:%H:%M}"]
    if row.attendee_count:
        noun = "attendee" if row.attendee_count == 1 else "attendees"
        parts.append(f"{row.attendee_count} {noun}")
    if row.description.strip():
        parts.append(row.description)
    return {"title": row.title, "summary": clip(" · ".join(parts))}


def _call(row):
    return {
        "title": row.title,
        "summary": clip(row.summary),
        "actor": person(name=row.host_name),
        "source": _origin(row.connector),
        "url": row.recording_url,
    }


def _email(row):
    sent_by_owner = row.mailbox_owner_id is not None and row.direction == Email.Direction.SENT
    return {
        "title": row.subject,
        "summary": clip(row.body),
        "actor": person(row.mailbox_owner) if sent_by_owner else person(name=row.sender_name),
        "source": row.mailbox.provider if row.mailbox_id else REVENACT,
        "thread_id": row.thread_id,
    }


def _note(row):
    return {
        "title": row.title,
        "summary": clip(row.body),
        "actor": person(row.author, row.author_name),
    }


def _survey(row):
    if row.status == Survey.Status.RESPONDED:
        summary = f"Responded · score {row.score}"
    elif row.status == Survey.Status.EXPIRED:
        summary = "Expired without a response"
    else:
        summary = "Sent · awaiting a response"
    return {"title": f"{row.get_survey_type_display()} survey", "summary": summary}


def _task(row):
    return {
        "title": row.title,
        "summary": " · ".join(
            [f"Due {row.due_date.isoformat()}", row.get_priority_display(), row.get_status_display()]
        ),
        "actor": person(row.assignee, row.assignee_name),
    }


def _ticket(row):
    return {
        "title": row.title,
        "summary": " · ".join(
            [row.ticket_number, row.get_priority_display(), row.get_status_display()]
        ),
        "actor": person(name=row.requester_name),
        "source": _origin(row.connector),
        "url": row.external_url,
    }


_FIELDS = {
    "activity": _activity,
    "calendar_event": _calendar_event,
    "call": _call,
    "email": _email,
    "note": _note,
    "survey": _survey,
    "task": _task,
    "ticket": _ticket,
}


def render(kind, row, scope):
    return make_item(
        scope,
        ident=row.pk,
        kind=kind,
        at=row._at,
        all_day=SOURCES[kind].all_day,
        account_id=row.account_id,
        **_FIELDS[kind](row),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.organizations.tests.test_story_items --noinput`
Expected: all 12 tests pass.

- [ ] **Step 5: Lint and commit**

```bash
venv/bin/ruff check --fix services/organizations && venv/bin/ruff format services/organizations
git add services/organizations/story/items.py services/organizations/tests/test_story_items.py
git commit -m "feat(organizations): story items, with CallSense summaries and safe links

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Health & usage: changes between month-end snapshots

**Files:**
- Create: `services/organizations/story/health.py`, `services/organizations/tests/test_story_health.py`

**Interfaces:**
- Consumes: `make_item` (Task 4), `Scope.parent_q` (Task 3), `StoryFixture.snapshot` (Task 3).
- Produces:
  - `describe_change(before: HealthSnapshot, after: HealthSnapshot) -> tuple[str, str] | None`: `(title, summary)` when the health category, AI pulse or CSM pulse moved; None otherwise.
  - `health_entries(scope) -> list[tuple[tuple[datetime, str, int], dict]]`: every change on the organisation and its in-scope accounts as `(sort key, item)`, the key being `(midnight UTC of captured_on, "health", snapshot id)`. One query.

- [ ] **Step 1: Write the failing tests**

`services/organizations/tests/test_story_health.py`:

```python
from datetime import UTC, datetime, time
from decimal import Decimal

from django.test import SimpleTestCase

from services.customers.models import HealthSnapshot
from services.organizations.story.health import describe_change, health_entries

from .story_fixtures import StoryFixture


def reading(score, ai=None, csm=None):
    return HealthSnapshot(health_score=Decimal(score), ai_pulse_value=ai, csm_pulse_score=csm)


class DescribeChangeTests(SimpleTestCase):
    def test_a_category_change_names_its_direction(self):
        self.assertEqual(
            describe_change(reading("7.5"), reading("5.0")),
            ("Health fell to Average", "Health 7.5 → 5.0"),
        )
        self.assertEqual(
            describe_change(reading("3.0"), reading("7.0")),
            ("Health rose to Good", "Health 3.0 → 7.0"),
        )

    def test_a_pulse_change_alone_is_a_change(self):
        self.assertEqual(
            describe_change(reading("8.0", ai=4, csm=4), reading("8.2", ai=2, csm=4)),
            ("Pulse changed", "Health 8.0 → 8.2 · AI pulse 4 → 2"),
        )
        self.assertEqual(
            describe_change(reading("8.0"), reading("8.0", csm=3)),
            ("Pulse changed", "Health 8.0 → 8.0 · CSM pulse — → 3"),
        )

    def test_movement_inside_one_category_is_not_a_change(self):
        self.assertIsNone(describe_change(reading("8.0", ai=3), reading("7.1", ai=3)))


class HealthEntriesTests(StoryFixture):
    def test_each_change_between_consecutive_readings_is_one_item(self):
        self.snapshot(self.pizza, self.days_ago(90), "8.0")
        self.snapshot(self.pizza, self.days_ago(60), "7.6")
        fell = self.snapshot(self.pizza, self.days_ago(30), "3.5")
        self.snapshot(self.emea, self.days_ago(60), "5.0", ai=3)
        moved = self.snapshot(self.emea, self.days_ago(30), "5.0", ai=1)

        by_id = {item["id"]: (key, item) for key, item in health_entries(self.scope())}
        self.assertEqual(set(by_id), {fell.pk, moved.pk})

        key, item = by_id[fell.pk]
        midnight = datetime.combine(self.days_ago(30), time.min, tzinfo=UTC)
        self.assertEqual(key, (midnight, "health", fell.pk))
        self.assertEqual(item["kind"], "health")
        self.assertEqual(item["title"], "Health fell to Poor")
        self.assertEqual(item["summary"], "Health 7.6 → 3.5")
        self.assertEqual(item["occurred_at"], f"{self.days_ago(30).isoformat()}T00:00:00+00:00")
        self.assertTrue(item["all_day"])
        self.assertIsNone(item["account"])
        self.assertIsNone(item["actor"])

        _key, item = by_id[moved.pk]
        self.assertEqual(item["account"], {"id": self.emea.pk, "name": "EMEA"})
        self.assertEqual(item["summary"], "Health 5.0 → 5.0 · AI pulse 3 → 1")

    def test_only_this_organisation_and_the_accounts_the_viewer_may_open(self):
        taco = self.customer("Taco Co")
        open_co = self.customer("Open Co", owner=None)
        danas = self.account("Dana's div", customers=[open_co], owner=self.other)
        for parent in (taco, self.account("Taco div", customers=[taco]), danas):
            self.snapshot(parent, self.days_ago(60), "8.0")
            self.snapshot(parent, self.days_ago(30), "2.0")
        self.assertEqual(health_entries(self.scope()), [])
        self.assertEqual(health_entries(self.scope(customer=open_co)), [])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.organizations.tests.test_story_health --noinput`
Expected: an ERROR, `ModuleNotFoundError: No module named 'services.organizations.story.health'`

- [ ] **Step 3: Implement `health.py`**

`services/organizations/story/health.py`:

```python
"""Health & usage: what changed from one month-end reading to the next.

`HealthSnapshot` is the only stored history there is: health score, AI pulse
and CSM pulse, per customer or account, per month-end. A snapshot is an item
when its health category, AI pulse or CSM pulse differs from the same
parent's previous snapshot; the first snapshot has nothing to differ from.
`Customer.pulse` keeps undated dots and lifecycle changes are not recorded,
so neither is a source.

Snapshots are monthly, a few dozen per parent at most, so the organisation's
whole set loads in one query and pages in Python, like the portfolio.
"""

from datetime import UTC, datetime, time

from services.customers.models import Customer, HealthSnapshot

from .items import make_item

_RANK = {
    Customer.HealthCategory.POOR: 0,
    Customer.HealthCategory.AVERAGE: 1,
    Customer.HealthCategory.GOOD: 2,
}


def _score(value):
    return f"{float(value):.1f}"


def _pulse(value):
    return "—" if value is None else str(value)


def describe_change(before, after):
    old, new = before.health_category, after.health_category
    ai_moved = before.ai_pulse_value != after.ai_pulse_value
    csm_moved = before.csm_pulse_score != after.csm_pulse_score
    if old == new and not ai_moved and not csm_moved:
        return None
    label = Customer.HealthCategory(new).label
    if _RANK[new] < _RANK[old]:
        title = f"Health fell to {label}"
    elif _RANK[new] > _RANK[old]:
        title = f"Health rose to {label}"
    else:
        title = "Pulse changed"
    parts = [f"Health {_score(before.health_score)} → {_score(after.health_score)}"]
    if ai_moved:
        parts.append(f"AI pulse {_pulse(before.ai_pulse_value)} → {_pulse(after.ai_pulse_value)}")
    if csm_moved:
        parts.append(
            f"CSM pulse {_pulse(before.csm_pulse_score)} → {_pulse(after.csm_pulse_score)}"
        )
    return title, " · ".join(parts)


def health_entries(scope):
    snapshots = (
        HealthSnapshot.objects.filter(scope.parent_q())
        .only(
            "id",
            "customer_id",
            "account_id",
            "captured_on",
            "health_score",
            "ai_pulse_value",
            "csm_pulse_score",
        )
        .order_by("customer_id", "account_id", "captured_on", "id")
    )
    entries, previous = [], {}
    for snapshot in snapshots:
        parent = (snapshot.customer_id, snapshot.account_id)
        before, previous[parent] = previous.get(parent), snapshot
        change = None if before is None else describe_change(before, snapshot)
        if change is None:
            continue
        title, summary = change
        at = datetime.combine(snapshot.captured_on, time.min, tzinfo=UTC)
        item = make_item(
            scope,
            ident=snapshot.pk,
            kind="health",
            at=at,
            all_day=True,
            account_id=snapshot.account_id,
            title=title,
            summary=summary,
        )
        entries.append(((at, "health", snapshot.pk), item))
    return entries
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.organizations.tests.test_story_health --noinput`
Expected: all 5 tests pass.

- [ ] **Step 5: Lint and commit**

```bash
venv/bin/ruff check --fix services/organizations && venv/bin/ruff format services/organizations
git add services/organizations/story/health.py services/organizations/tests/test_story_health.py
git commit -m "feat(organizations): health and pulse changes as story items

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: The page and the counts, merged across sources

**Files:**
- Create: `services/organizations/story/build.py`, `services/organizations/tests/test_story_build.py`
- Modify: `services/organizations/tests/story_fixtures.py` (add `story`, `keys`, `walk`)

**Interfaces:**
- Consumes: `StoryParams`, `parse_story_params`, `GROUP_KINDS`, `KINDS`, `NO_ACCOUNT` (Task 1); `Cut`, `fingerprint`, `encode_cursor`, `decode_cursor`, `after_q`, `is_after` (Task 2); `Scope`, `SOURCES`, `horizon_for` (Task 3); `render` (Task 4); `health_entries` (Task 5).
- Produces:
  - `matches_account(account_id: int | None, account: int | str | None) -> bool`.
  - `build_page(bases, health, params, scope) -> tuple[list[dict], str | None]`.
  - `tally(bases, health, q) -> dict[str, dict[int | None, int]]`: per kind, rows per account id (None: the organisation's own), over the whole searched set.
  - `build_counts(tally, params, scope) -> {"by_group", "by_kind", "by_account"}`.
  - `build_story(user, scope, params, *, today) -> dict` with keys `items`, `next_cursor`, `counts` (Task 7 adds `attention`). `bases` is `{kind: SOURCES[kind].base(user, scope, horizon=horizon_for(today))}`, built once per request.
  - `StoryFixture.story(user=None, customer=None, **query) -> dict`, `StoryFixture.keys(body) -> list[tuple[str, int]]`, `StoryFixture.walk(user=None, **query) -> list[tuple[str, int]]` (follows `next_cursor` to the end).

- [ ] **Step 1: Add the fixture helpers**

In `services/organizations/tests/story_fixtures.py`, add these imports under the existing `from services.organizations.story.scope import resolve_scope` line:

```python
from services.organizations.story.build import build_story
from services.organizations.story.params import parse_story_params
```

Then append these methods to `StoryFixture`:

```python
    def story(self, user=None, customer=None, **query):
        user = user or self.csm
        scope = self.scope(user, customer)
        return build_story(user, scope, parse_story_params(query), today=self.today)

    @staticmethod
    def keys(body):
        return [(item["kind"], item["id"]) for item in body["items"]]

    def walk(self, user=None, **query):
        seen, cursor = [], None
        while True:
            body = self.story(user, **query, **({"cursor": cursor} if cursor else {}))
            seen += self.keys(body)
            cursor = body["next_cursor"]
            if cursor is None:
                return seen
```

- [ ] **Step 2: Write the failing tests**

`services/organizations/tests/test_story_build.py`:

```python
from datetime import timedelta

from django.utils import timezone

from .story_fixtures import StoryFixture


class StoryOrderTests(StoryFixture):
    def test_newest_first_across_sources(self):
        email = self.email(self.pizza, at=timezone.now() - timedelta(hours=1))
        activity = self.activity(self.emea, day=self.days_ago(1))
        ticket = self.ticket(self.apac, day=self.days_ago(2))
        note = self.note(self.pizza, day=self.days_ago(3))
        self.snapshot(self.pizza, self.days_ago(70), "8.0")
        fell = self.snapshot(self.pizza, self.days_ago(40), "3.0")
        self.assertEqual(
            self.keys(self.story()),
            [
                ("email", email.pk),
                ("activity", activity.pk),
                ("ticket", ticket.pk),
                ("note", note.pk),
                ("health", fell.pk),
            ],
        )

    def test_one_moment_orders_by_kind_then_id_descending(self):
        day = self.days_ago(1)
        first = self.activity(self.pizza, day=day)
        second = self.activity(self.pizza, day=day)
        note = self.note(self.pizza, day=day)
        survey = self.survey(self.pizza, day=day)
        ticket = self.ticket(self.pizza, day=day)
        self.assertEqual(
            self.keys(self.story()),
            [
                ("ticket", ticket.pk),
                ("survey", survey.pk),
                ("note", note.pk),
                ("activity", second.pk),
                ("activity", first.pk),
            ],
        )

    def test_the_cursor_reads_every_item_once_across_sources(self):
        day = self.days_ago(1)
        for parent in (self.pizza, self.emea, self.apac):
            for kind in ("activity", "calendar_event", "note", "survey", "ticket"):
                self.make(kind, parent, day=day)
            self.email(parent, at=timezone.now() - timedelta(minutes=5))
            self.call(parent, at=timezone.now() - timedelta(minutes=5))
            self.task(parent)
        self.snapshot(self.pizza, self.days_ago(70), "8.0")
        self.snapshot(self.pizza, self.days_ago(40), "3.0")
        everything = self.keys(self.story(limit="100"))
        self.assertEqual(len(everything), 25)
        self.assertEqual(len(set(everything)), 25)
        for limit in ("1", "2", "7"):
            with self.subTest(limit=limit):
                self.assertEqual(self.walk(limit=limit), everything)

    def test_a_cursor_cut_under_other_filters_reads_the_first_page(self):
        for _ in range(3):
            self.note(self.pizza)
        first = self.story(limit="1")
        self.assertIsNotNone(first["next_cursor"])
        moved = self.story(limit="1", group="tasks", cursor=first["next_cursor"])
        self.assertEqual(self.keys(moved), self.keys(first))


class StoryFilterTests(StoryFixture):
    def setUp(self):
        super().setUp()
        self.org_note = self.note(self.pizza, body="SSO is blocking the rollout")
        self.emea_email = self.email(self.emea)
        self.emea_ticket = self.ticket(self.emea)
        self.apac_survey = self.survey(self.apac)
        self.snapshot(self.apac, self.days_ago(70), "8.0")
        self.apac_fell = self.snapshot(self.apac, self.days_ago(40), "3.0")

    def found(self, **query):
        return set(self.keys(self.story(**query)))

    def test_group_narrows_to_its_kinds(self):
        self.assertEqual(self.found(group="conversations"), {("email", self.emea_email.pk)})
        self.assertEqual(self.found(group="tasks"), {("note", self.org_note.pk)})
        self.assertEqual(self.found(group="feedback"), {("survey", self.apac_survey.pk)})
        self.assertEqual(self.found(group="health"), {("health", self.apac_fell.pk)})

    def test_source_picks_exact_kinds(self):
        self.assertEqual(
            self.found(source="ticket,survey"),
            {("ticket", self.emea_ticket.pk), ("survey", self.apac_survey.pk)},
        )

    def test_account_narrows_to_one_account_or_the_organisation(self):
        self.assertEqual(
            self.found(account=str(self.emea.pk)),
            {("email", self.emea_email.pk), ("ticket", self.emea_ticket.pk)},
        )
        self.assertEqual(self.found(account="none"), {("note", self.org_note.pk)})
        self.assertEqual(
            self.found(account=str(self.apac.pk), group="health"),
            {("health", self.apac_fell.pk)},
        )

    def test_an_account_of_another_organisation_reads_nothing(self):
        stranger = self.account("Taco div", customers=[self.customer("Taco Co")])
        self.note(stranger)
        body = self.story(account=str(stranger.pk))
        self.assertEqual(body["items"], [])
        self.assertEqual(body["counts"]["by_group"]["all"], 0)

    def test_search_matches_text_and_leaves_health_out(self):
        self.assertEqual(self.found(q="sso"), {("note", self.org_note.pk)})
        self.assertEqual(self.found(q="poor"), set())
        self.assertEqual(self.story(q="sso")["counts"]["by_group"]["health"], 0)

    def test_thread_reads_one_email_thread(self):
        first = self.email(self.pizza, thread_id="t-1")
        reply = self.email(self.emea, thread_id="t-1")
        self.email(self.pizza, thread_id="t-2")
        self.assertEqual(self.found(thread="t-1"), {("email", first.pk), ("email", reply.pk)})

    def test_counts_cover_the_whole_filtered_set_not_the_page(self):
        body = self.story(limit="1")
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(
            body["counts"]["by_group"],
            {"all": 5, "conversations": 1, "tickets": 1, "tasks": 1, "feedback": 1, "health": 1},
        )
        self.assertEqual(
            body["counts"]["by_kind"],
            {
                "activity": 0,
                "calendar_event": 0,
                "call": 0,
                "email": 1,
                "health": 1,
                "note": 1,
                "survey": 1,
                "task": 0,
                "ticket": 1,
            },
        )
        self.assertEqual(
            body["counts"]["by_account"],
            {"all": 5, "none": 1, str(self.apac.pk): 2, str(self.emea.pk): 2},
        )

    def test_by_group_ignores_the_group_and_by_account_ignores_the_account(self):
        body = self.story(group="conversations", account=str(self.emea.pk))
        self.assertEqual(body["counts"]["by_group"]["all"], 2)
        self.assertEqual(body["counts"]["by_group"]["tickets"], 1)
        self.assertEqual(
            body["counts"]["by_account"],
            {"all": 1, "none": 0, str(self.apac.pk): 0, str(self.emea.pk): 1},
        )
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.organizations.tests.test_story_build --noinput`
Expected: an ERROR while importing `story_fixtures`, `ModuleNotFoundError: No module named 'services.organizations.story.build'`

- [ ] **Step 4: Implement `build.py`**

`services/organizations/story/build.py`:

```python
"""The story: one page and its counts, merged across every source.

Each record source contributes at most `limit + 1` rows after the cursor, in
one query, newest first; the merge keeps the newest `limit`, and there is a
next page exactly when more than `limit` rows came back in total. Counts are
one aggregate per source over the whole searched set, never the page, grouped
by account; every figure is summed from those. Health changes come from one
snapshot query (`health.health_entries`) and page in Python.

The base querysets are built once per request and shared by the page, the
counts and the attention block: each personal rule reads the org chart when
it is built, so building them again would add queries.
"""

from collections import Counter

from django.db.models import Count

from .cursor import Cut, after_q, decode_cursor, encode_cursor, fingerprint, is_after
from .health import health_entries
from .items import render
from .params import GROUP_KINDS, KINDS, NO_ACCOUNT
from .sources import SOURCES, horizon_for

HEALTH = "health"


def _account_id(item):
    return item["account"]["id"] if item["account"] else None


def matches_account(account_id, account) -> bool:
    """Whether a row filed on `account_id` (None: on the organisation itself)
    passes the `account` parameter."""
    if account is None:
        return True
    if account == NO_ACCOUNT:
        return account_id is None
    return account_id == account


def build_page(bases, health, params, scope):
    fp = fingerprint(params)
    cut = decode_cursor(params.cursor, fp)
    candidates = []
    for kind in params.page_kinds:
        if kind == HEALTH:
            if not params.q:
                candidates += [
                    (key, item)
                    for key, item in health
                    if is_after(key, cut) and matches_account(_account_id(item), params.account)
                ]
            continue
        source = SOURCES[kind]
        rows = bases[kind].filter(
            scope.parent_q(params.account), source.search_q(params.q), after_q(kind, cut)
        )
        if params.thread:
            rows = rows.filter(thread_id=params.thread)
        rows = rows.select_related(*source.related).order_by("-_at", "-id")[: params.limit + 1]
        candidates += [((row._at, kind, row.pk), render(kind, row, scope)) for row in rows]

    candidates.sort(key=lambda pair: pair[0], reverse=True)
    page = candidates[: params.limit]
    next_cursor = None
    if len(candidates) > params.limit:
        at, kind, ident = page[-1][0]
        next_cursor = encode_cursor(Cut(at=at, kind=kind, id=ident), fp)
    return [item for _key, item in page], next_cursor


def tally(bases, health, q):
    counts = {}
    for kind, source in SOURCES.items():
        rows = (
            bases[kind]
            .filter(source.search_q(q))
            .order_by()
            .values("account_id")
            .annotate(n=Count("id"))
        )
        counts[kind] = {row["account_id"]: row["n"] for row in rows}
    # Health items have no text to search, so a search leaves them all out.
    counts[HEALTH] = {} if q else dict(Counter(_account_id(item) for _key, item in health))
    return counts


def build_counts(counts, params, scope):
    """`by_kind` and `by_group` follow `account` and `q` but not `group` or
    `source`, so every filter chip keeps its number while one is selected;
    `by_account` follows `group`, `source` and `q` but not `account`, for the
    same reason. The accounts listed are exactly the ones in scope."""

    def total(kinds, account):
        return sum(
            n
            for kind in kinds
            for account_id, n in counts[kind].items()
            if matches_account(account_id, account)
        )

    by_kind = {kind: total((kind,), params.account) for kind in KINDS}
    by_group = {"all": sum(by_kind.values())}
    by_group.update(
        {group: sum(by_kind[kind] for kind in kinds) for group, kinds in GROUP_KINDS.items()}
    )
    selected = params.selected_kinds
    by_account = {"all": total(selected, None), NO_ACCOUNT: total(selected, NO_ACCOUNT)}
    by_account.update({str(account_id): total(selected, account_id) for account_id in scope.accounts})
    return {"by_group": by_group, "by_kind": by_kind, "by_account": by_account}


def build_story(user, scope, params, *, today):
    horizon = horizon_for(today)
    bases = {kind: source.base(user, scope, horizon=horizon) for kind, source in SOURCES.items()}
    health = health_entries(scope)
    items, next_cursor = build_page(bases, health, params, scope)
    return {
        "items": items,
        "next_cursor": next_cursor,
        "counts": build_counts(tally(bases, health, params.q), params, scope),
    }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.organizations.tests.test_story_build --noinput`
Expected: all 12 tests pass. If the cursor walk repeats or skips, compare `after_q` against `is_after` for the kind at the cut: the two must be the same comparison.

- [ ] **Step 6: Run the earlier story tests, which import the fixture too**

Run: `venv/bin/python manage.py test services.organizations --noinput`
Expected: every test passes.

- [ ] **Step 7: Lint and commit**

```bash
venv/bin/ruff check --fix services/organizations && venv/bin/ruff format services/organizations
git add services/organizations/story/build.py services/organizations/tests/story_fixtures.py services/organizations/tests/test_story_build.py
git commit -m "feat(organizations): story page, cursor and counts across every source

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Needs attention

**Files:**
- Create: `services/organizations/story/attention.py`, `services/organizations/tests/test_story_attention.py`
- Modify: `services/organizations/story/build.py` (`build_story` adds `attention`)

**Interfaces:**
- Consumes: `Scope.parent_q` and `Scope.customer` (Task 3); the `bases` dict built in `build_story` (Task 6), whose `"ticket"` and `"task"` querysets already carry `visible_tickets` / `visible_tasks`; `StoryFixture.story` (Task 6).
- Produces:
  - `RENEWAL_WINDOW_DAYS = 30`.
  - `build_attention(user, scope, bases, account, *, today) -> dict` with exactly these keys, each `None` when nothing needs attention:
    - `renewal`: `{"date": "YYYY-MM-DD", "days": int, "overdue": bool}`
    - `tickets`: `{"count": int, "oldest_days": int}`
    - `overdue_tasks`: `{"count": int, "oldest_days": int}`
    - `questions`: `{"count": int}`
    - `anomaly`: `{"id": int, "title": str, "first_seen_at": iso, "last_seen_at": iso}`
  - `build_story(...)` now also returns `"attention"`.

- [ ] **Step 1: Write the failing tests**

`services/organizations/tests/test_story_attention.py`:

```python
from datetime import timedelta

from django.utils import timezone

from services.accounts.models import User
from services.anomalies.models import Anomaly, AnomalyEvidence
from services.customers.models import Customer, Task, Ticket
from services.knowledge.models import Question

from .story_fixtures import StoryFixture


class AttentionTests(StoryFixture):
    def attention(self, user=None, customer=None, **query):
        return self.story(user, customer, **query)["attention"]

    def anomaly(self, title, *, seen_days_ago=0, status=Anomaly.Status.LIVE):
        now = timezone.now()
        return Anomaly.objects.create(
            organisation=self.org,
            title=title,
            status=status,
            first_seen_at=now - timedelta(days=seen_days_ago + 3),
            last_seen_at=now - timedelta(days=seen_days_ago),
        )

    def evidence(self, anomaly, parent, *, record_id, kind=AnomalyEvidence.Kind.CALL, **fields):
        return AnomalyEvidence.objects.create(
            anomaly=anomaly,
            organisation=self.org,
            kind=kind,
            record_id=record_id,
            snippet="checkout fails",
            occurred_at=timezone.now(),
            **self.on(parent),
            **fields,
        )

    def test_nothing_needs_attention(self):
        self.assertEqual(
            self.attention(),
            {
                "renewal": None,
                "tickets": None,
                "overdue_tasks": None,
                "questions": None,
                "anomaly": None,
            },
        )

    def test_a_renewal_overdue_or_due_within_thirty_days(self):
        for days, overdue in ((-5, True), (0, False), (30, False)):
            renewal_date = self.today + timedelta(days=days)
            Customer.objects.filter(pk=self.pizza.pk).update(renewal_date=renewal_date)
            self.assertEqual(
                self.attention()["renewal"],
                {"date": renewal_date.isoformat(), "days": days, "overdue": overdue},
            )
        Customer.objects.filter(pk=self.pizza.pk).update(
            renewal_date=self.today + timedelta(days=31)
        )
        self.assertIsNone(self.attention()["renewal"])

    def test_a_churned_organisation_has_no_renewal_to_chase(self):
        Customer.objects.filter(pk=self.pizza.pk).update(
            renewal_date=self.days_ago(5), churn_date=self.days_ago(1)
        )
        self.assertIsNone(self.attention()["renewal"])
        Customer.objects.filter(pk=self.pizza.pk).update(
            churn_date=None, lifecycle_stage=Customer.LifecycleStage.CHURN
        )
        self.assertIsNone(self.attention()["renewal"])

    def test_open_high_and_critical_tickets_the_viewer_may_read(self):
        self.ticket(self.pizza, priority=Ticket.Priority.HIGH, day=self.days_ago(9))
        self.ticket(self.emea, priority=Ticket.Priority.CRITICAL, day=self.days_ago(2))
        self.ticket(self.pizza, priority=Ticket.Priority.MEDIUM, day=self.days_ago(20))
        self.ticket(
            self.pizza,
            priority=Ticket.Priority.HIGH,
            status=Ticket.Status.RESOLVED,
            day=self.days_ago(30),
        )
        self.ticket(
            self.apac, priority=Ticket.Priority.HIGH, department="engineering", day=self.days_ago(40)
        )
        self.assertEqual(self.attention()["tickets"], {"count": 2, "oldest_days": 9})
        self.assertEqual(self.attention(self.admin)["tickets"], {"count": 3, "oldest_days": 40})
        self.assertEqual(
            self.attention(account=str(self.emea.pk))["tickets"], {"count": 1, "oldest_days": 2}
        )

    def test_overdue_tasks_the_viewer_may_read(self):
        self.task(self.pizza, due=self.days_ago(4))
        self.task(self.emea, due=self.days_ago(1))
        self.task(self.pizza, due=self.today)
        self.task(self.pizza, due=self.days_ago(8), status=Task.Status.COMPLETED)
        self.task(self.pizza, due=self.days_ago(12), created_by=self.other, assignee=self.other)
        self.assertEqual(self.attention()["overdue_tasks"], {"count": 2, "oldest_days": 4})
        self.assertEqual(
            self.attention(account="none")["overdue_tasks"], {"count": 1, "oldest_days": 4}
        )

    def test_unanswered_questions_the_viewer_may_read(self):
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.csm,
            assignee=self.engineer,
            text="Why is usage down?",
        )
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.csm,
            assignee=self.engineer,
            text="Answered already",
            status=Question.Status.ANSWERED,
        )
        self.assertEqual(self.attention()["questions"], {"count": 1})

        open_co = self.customer("Open Co", owner=None)
        colleague = User.objects.create_user(
            email="eli@acme.io",
            password="supersecret1",
            name="Eli Engineer",
            organisation=self.org,
            role=User.Role.CSM,
            function=User.Function.ENGINEERING,
        )
        Question.objects.create(
            organisation=self.org,
            customer=open_co,
            asked_by=self.engineer,
            assignee=colleague,
            text="Between engineers",
        )
        self.assertIsNone(self.attention(customer=open_co)["questions"])
        self.assertEqual(self.attention(self.engineer, customer=open_co)["questions"], {"count": 1})

    def test_the_latest_live_anomaly_with_its_title_withheld_unless_seeing_everything(self):
        older = self.anomaly("Checkout fails at Pizza Hut and Taco Co", seen_days_ago=5)
        latest = self.anomaly("SSO outage at Pizza Hut", seen_days_ago=1)
        resolved = self.anomaly("Old fault", status=Anomaly.Status.RESOLVED)
        self.evidence(older, self.pizza, record_id=1)
        self.evidence(latest, self.emea, record_id=2)
        self.evidence(resolved, self.pizza, record_id=3)

        mine = self.attention()["anomaly"]
        self.assertEqual(set(mine), {"id", "title", "first_seen_at", "last_seen_at"})
        self.assertEqual(mine["id"], latest.pk)
        self.assertEqual(mine["title"], "Similar reports across 1 of your companies")
        self.assertEqual(self.attention(self.admin)["anomaly"]["title"], "SSO outage at Pizza Hut")
        self.assertIsNone(self.attention(account=str(self.apac.pk))["anomaly"])
        self.assertEqual(self.attention(account="none")["anomaly"]["id"], older.pk)

    def test_an_anomaly_the_viewer_may_not_read_or_on_another_organisation_is_not_shown(self):
        hidden = self.anomaly("Pricing complaints")
        self.evidence(
            hidden,
            self.pizza,
            record_id=4,
            kind=AnomalyEvidence.Kind.EMAIL,
            mailbox_owner=self.other,
        )
        elsewhere = self.anomaly("Only at Taco")
        self.evidence(elsewhere, self.customer("Taco Co"), record_id=5)
        self.assertIsNone(self.attention()["anomaly"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.organizations.tests.test_story_attention --noinput`
Expected: FAIL (ERROR) with `KeyError: 'attention'`.

- [ ] **Step 3: Implement `attention.py`**

`services/organizations/story/attention.py`:

```python
"""Needs attention: the block pinned above the stream (spec §1.6).

Each entry reuses a rule another screen owns, read under the same record
rules as the stream: tickets and tasks come from the story's own base
querysets (`visible_tickets`, `visible_tasks`); questions from the knowledge
layer's `visible_questions`; the anomaly from evidence the viewer may read
(`personal.readable_evidence_q`, the rule `anomalies.views.visible_evidence`
applies), titled by `title_for`, so only a viewer who sees everything reads
the model-written title. The summary is never returned.

It follows `account` (tickets, tasks, anomaly evidence) and nothing else: the
renewal is the organisation's, a question names no account, and a search or
a group must never hide the block.
"""

from django.db.models import Count, Min

from services.attention.rules import SUPPORT_PRIORITIES
from services.customers.models import Customer, Task, Ticket

#: The spec's window (§1.6), shorter than the Dashboard's 90 days.
RENEWAL_WINDOW_DAYS = 30


def renewal(customer, today):
    churned = (
        customer.churn_date is not None
        or customer.lifecycle_stage == Customer.LifecycleStage.CHURN
    )
    if churned or customer.renewal_date is None:
        return None
    days = (customer.renewal_date - today).days
    if days > RENEWAL_WINDOW_DAYS:
        return None
    return {"date": customer.renewal_date.isoformat(), "days": days, "overdue": days < 0}


def _aged(row, today):
    if not row["count"]:
        return None
    return {"count": row["count"], "oldest_days": max(0, (today - row["oldest"]).days)}


def urgent_tickets(tickets, today):
    row = (
        tickets.filter(priority__in=SUPPORT_PRIORITIES)
        .exclude(status__in=Ticket.RESOLVED_STATUSES)
        .aggregate(count=Count("id"), oldest=Min("opened_at"))
    )
    return _aged(row, today)


def overdue_tasks(tasks, today):
    row = (
        tasks.exclude(status=Task.Status.COMPLETED)
        .filter(due_date__lt=today)
        .aggregate(count=Count("id"), oldest=Min("due_date"))
    )
    return _aged(row, today)


def open_questions(user, customer):
    # Imported here: knowledge.views is a views module with a wide import tree.
    from services.knowledge.models import Question
    from services.knowledge.views import visible_questions

    # SOC2:AUTH-02 the knowledge layer's own rule for questions
    count = visible_questions(
        user,
        Question.objects.filter(
            organisation_id=customer.organisation_id,
            customer=customer,
            status=Question.Status.OPEN,
        ),
    ).count()
    return {"count": count} if count else None


def latest_anomaly(user, scope, account):
    from services.anomalies.models import Anomaly, AnomalyEvidence
    from services.anomalies.views import title_for
    from services.customers.personal import readable_evidence_q
    from services.customers.scoping import sees_everything

    row = (
        AnomalyEvidence.objects.filter(
            organisation_id=scope.customer.organisation_id,
            anomaly__status=Anomaly.Status.LIVE,
        )
        .filter(scope.parent_q(account))
        # SOC2:AUTH-02 evidence is read under the rule of the record it copies
        .filter(
            readable_evidence_q(
                user,
                email=AnomalyEvidence.Kind.EMAIL,
                ticket=AnomalyEvidence.Kind.TICKET,
                call=AnomalyEvidence.Kind.CALL,
            )
        )
        .select_related("anomaly")
        .order_by("-anomaly__last_seen_at", "-anomaly_id")
        .first()
    )
    if row is None:
        return None
    anomaly = row.anomaly
    return {
        "id": anomaly.pk,
        "title": title_for(anomaly.title, 1, sees_all=sees_everything(user)),
        "first_seen_at": anomaly.first_seen_at.isoformat(),
        "last_seen_at": anomaly.last_seen_at.isoformat(),
    }


def build_attention(user, scope, bases, account, *, today):
    return {
        "renewal": renewal(scope.customer, today),
        "tickets": urgent_tickets(bases["ticket"].filter(scope.parent_q(account)), today),
        "overdue_tasks": overdue_tasks(bases["task"].filter(scope.parent_q(account)), today),
        "questions": open_questions(user, scope.customer),
        "anomaly": latest_anomaly(user, scope, account),
    }
```

- [ ] **Step 4: Add `attention` to the story**

In `services/organizations/story/build.py`, add `from .attention import build_attention` to the imports (first of the relative imports), and make the `return` of `build_story` read:

```python
    return {
        "items": items,
        "next_cursor": next_cursor,
        "counts": build_counts(tally(bases, health, params.q), params, scope),
        "attention": build_attention(user, scope, bases, params.account, today=today),
    }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.organizations --noinput`
Expected: every test passes, the 8 new attention tests among them.

- [ ] **Step 6: Lint and commit**

```bash
venv/bin/ruff check --fix services/organizations && venv/bin/ruff format services/organizations
git add services/organizations/story services/organizations/tests/test_story_attention.py
git commit -m "feat(organizations): the story's Needs attention block

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: `GET /api/v1/organizations/{id}/story/`

**Files:**
- Modify: `services/organizations/views.py`, `services/organizations/urls.py`
- Create: `services/organizations/tests/test_story_views.py`

**Interfaces:**
- Consumes: `resolve_scope` (Task 3), `parse_story_params` (Task 1), `build_story` (Tasks 6 and 7), `StoryFixture` (Tasks 3 and 6).
- Produces: `views.OrganizationStoryView`, mounted at `api/v1/organizations/<int:pk>/story/` with the URL name `organizations-story`. `config/urls.py` already mounts `services.organizations.urls` at `api/v1/organizations/`.

- [ ] **Step 1: Write the failing tests**

`services/organizations/tests/test_story_views.py`:

```python
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from services.accounts.models import User
from services.anomalies.models import Anomaly, AnomalyEvidence
from services.customers.models import Ticket
from services.knowledge.models import Question

from .story_fixtures import StoryFixture

#: Each record kind and its per-type endpoint under /customers/<id>/ and
#: /customers/<id>/accounts/<account_id>/.
PER_TYPE = {
    "activity": "activities",
    "calendar_event": "calendar-events",
    "call": "calls",
    "email": "emails",
    "note": "notes",
    "survey": "surveys",
    "task": "tasks",
    "ticket": "tickets",
}


def story_url(customer_id):
    return f"/api/v1/organizations/{customer_id}/story/"


class StoryEndpointFixture(StoryFixture):
    @staticmethod
    def client_for(user):
        api = APIClient()
        api.force_authenticate(user)
        return api

    def get(self, user=None, customer=None, **query):
        api = self.client_for(user or self.csm)
        response = api.get(story_url((customer or self.pizza).pk), query)
        self.assertEqual(response.status_code, 200, response.content)
        return response.data


class StoryEndpointTests(StoryEndpointFixture):
    def test_requires_authentication(self):
        self.assertEqual(APIClient().get(story_url(self.pizza.pk)).status_code, 401)

    def test_an_organisation_the_viewer_cannot_open_is_a_404(self):
        danas = self.customer("Dana's", owner=self.other)
        globex = self.customer("Globex's", owner=None, organisation=self.other_org)
        api = self.client_for(self.csm)
        for customer_id in (danas.pk, globex.pk, 999_999):
            self.assertEqual(api.get(story_url(customer_id)).status_code, 404, customer_id)

    def test_an_archived_organisation_still_opens(self):
        gone = self.customer("Gone", is_archived=True)
        self.note(gone)
        self.assertEqual(len(self.get(customer=gone)["items"]), 1)

    def test_response_shape(self):
        self.note(self.emea)
        body = self.get()
        self.assertEqual(set(body), {"items", "next_cursor", "counts", "attention"})
        self.assertEqual(
            set(body["items"][0]),
            {
                "id",
                "kind",
                "source",
                "occurred_at",
                "all_day",
                "account",
                "title",
                "summary",
                "actor",
                "link",
            },
        )
        self.assertEqual(set(body["items"][0]["link"]), {"thread_id", "url"})
        self.assertEqual(set(body["counts"]), {"by_group", "by_kind", "by_account"})
        self.assertEqual(
            set(body["attention"]),
            {"renewal", "tickets", "overdue_tasks", "questions", "anomaly"},
        )
        self.assertIsNone(body["next_cursor"])

    def test_unknown_values_are_ignored_not_rejected(self):
        self.note(self.pizza)
        body = self.get(group="mood", source="slack", account="emea", limit="lots", cursor="%%%")
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["counts"]["by_group"]["all"], 1)


class StoryMatchesPerTypeEndpointsTests(StoryEndpointFixture):
    def per_type(self, user):
        api = self.client_for(user)
        found = set()
        for kind, path in PER_TYPE.items():
            urls = [f"/api/v1/customers/{self.pizza.pk}/{path}/"] + [
                f"/api/v1/customers/{self.pizza.pk}/accounts/{account.pk}/{path}/"
                for account in (self.emea, self.apac)
            ]
            for url in urls:
                response = api.get(url)
                self.assertEqual(response.status_code, 200, url)
                found |= {(kind, row["id"]) for row in response.data}
        return found

    def story_keys(self, user):
        api = self.client_for(user)
        seen, cursor = [], None
        while True:
            query = {"limit": "3", **({"cursor": cursor} if cursor else {})}
            body = api.get(story_url(self.pizza.pk), query).data
            seen += [(item["kind"], item["id"]) for item in body["items"]]
            cursor = body["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(len(seen), len(set(seen)))
        return {key for key in seen if key[0] != "health"}

    def test_the_story_is_what_the_per_type_endpoints_show_the_same_viewer(self):
        for parent in (self.pizza, self.emea, self.apac):
            for kind in PER_TYPE:
                self.make(kind, parent)
        self.email(self.emea, mailbox_owner=self.other)
        self.email(self.pizza, mailbox_owner=self.csm)
        self.note(self.pizza, author=self.other)
        self.task(self.apac, created_by=self.other, assignee=self.other)
        self.ticket(self.pizza, department="engineering")
        for user in (self.csm, self.admin):
            with self.subTest(user=user.name):
                expected = self.per_type(user)
                self.assertEqual(self.story_keys(user), expected)
                self.assertGreaterEqual(len(expected), 24)


class StoryPrivacyTests(StoryEndpointFixture):
    def test_records_the_viewer_may_not_read_reach_no_item_count_attention_or_search(self):
        self.email(self.emea, mailbox_owner=self.other, subject="Secret pricing", body="Secret")
        self.note(self.pizza, author=self.other, title="Secret note", body="Secret pricing")
        self.ticket(
            self.pizza,
            department="engineering",
            title="Secret pricing bug",
            priority=Ticket.Priority.CRITICAL,
        )
        self.task(
            self.pizza,
            title="Secret pricing task",
            created_by=self.other,
            assignee=self.other,
            due=self.days_ago(3),
        )
        body = self.get()
        self.assertEqual(body["items"], [])
        self.assertEqual(body["counts"]["by_group"]["all"], 0)
        self.assertEqual(set(body["counts"]["by_kind"].values()), {0})
        self.assertEqual(set(body["counts"]["by_account"].values()), {0})
        self.assertIsNone(body["attention"]["tickets"])
        self.assertIsNone(body["attention"]["overdue_tasks"])
        searched = self.get(q="secret")
        self.assertEqual((searched["items"], searched["counts"]["by_group"]["all"]), ([], 0))
        # Readable to Leadership, and then search finds it: the rule, not the search, hid it.
        leadership = self.get(self.admin, q="secret")
        self.assertEqual([item["kind"] for item in leadership["items"]], ["ticket"])

    def test_only_records_linked_to_this_organisation(self):
        taco = self.customer("Taco Co")
        shared = self.account("Shared div", customers=[self.pizza, taco])
        taco_div = self.account("Taco div", customers=[taco])
        on_shared = self.note(shared)
        on_taco = self.note(taco)
        on_taco_div = self.note(taco_div)
        self.assertEqual(
            [(item["kind"], item["id"]) for item in self.get()["items"]],
            [("note", on_shared.pk)],
        )
        self.assertEqual(self.get(account=str(taco_div.pk))["items"], [])
        self.assertEqual(
            {item["id"] for item in self.get(customer=taco)["items"]},
            {on_shared.pk, on_taco.pk, on_taco_div.pk},
        )


class StoryQueryCountTests(StoryEndpointFixture):
    """The page, the counts and the attention block cost a fixed number of
    queries. A per-row query anywhere would make the count grow with the book,
    which the two-size test catches.

    Thirty-one for an admin, who sees everything (so no org-chart lookup in
    `visible_customers`):
      1. the caller's membership (services.identity.context, memoised per request)
      2. the organisation (`visible_customers`, get_object_or_404)
      3. its accounts the caller may open (`visible_accounts`)
      4-6. the org chart below the caller, once each as the mail, note and task
           rules are built (`chain_visible_q`, `visible_tasks`)
      7. the health snapshots
      8-15. one page query per record source (activity, calendar_event, call,
            email, note, survey, task, ticket), related rows joined
      16-23. one count aggregate per record source
      24. attention: urgent tickets (one aggregate)
      25. attention: overdue tasks (one aggregate)
      26-29. attention: open questions: `scope_ids` (the org chart below, the
             caller's function), the org chart below again (`visible_questions`),
             then the count
      30-31. attention: the evidence rule's org chart (`readable_evidence_q`),
             then the latest readable evidence with its anomaly
    If the pinned number is ever wrong, print `[q["sql"] for q in
    ctx.captured_queries]`: every query must be one of these kinds; an extra
    one is a bug to fix, not a number to bump.
    """

    EXPECTED = 31

    def book(self, size):
        for i in range(size):
            account = self.account(f"Div {size}-{i}")
            for kind in PER_TYPE:
                self.make(kind, account)
            self.snapshot(account, self.days_ago(70), "8.0")
            self.snapshot(account, self.days_ago(40), "3.0")
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.csm,
            assignee=self.engineer,
            text=f"Why {size}?",
        )
        now = timezone.now()
        anomaly = Anomaly.objects.create(
            organisation=self.org, title=f"Fault {size}", first_seen_at=now, last_seen_at=now
        )
        AnomalyEvidence.objects.create(
            anomaly=anomaly,
            organisation=self.org,
            kind=AnomalyEvidence.Kind.CALL,
            record_id=size,
            customer=self.pizza,
            snippet="fault",
            occurred_at=now,
        )

    def count(self, user=None, **query):
        # A fresh user each time, as a real request loads one: memoised lookups
        # cached on a reused instance would otherwise flatter the second call.
        api = self.client_for(User.objects.get(pk=(user or self.admin).pk))
        with CaptureQueriesContext(connection) as ctx:
            response = api.get(story_url(self.pizza.pk), query)
        self.assertEqual(response.status_code, 200)
        return len(ctx.captured_queries)

    def test_the_count_is_pinned_and_does_not_grow_with_the_book(self):
        self.book(3)
        small = self.count()
        self.book(12)
        self.assertEqual(self.count(), small)
        self.assertEqual(small, self.EXPECTED)

    def test_a_csm_s_count_does_not_grow_with_the_book_either(self):
        self.book(3)
        small = self.count(self.csm)
        self.book(12)
        self.assertEqual(self.count(self.csm), small)

    def test_the_next_page_costs_the_same(self):
        self.book(5)
        api = self.client_for(self.admin)
        cursor = api.get(story_url(self.pizza.pk), {"limit": "5"}).data["next_cursor"]
        self.assertIsNotNone(cursor)
        self.assertEqual(self.count(limit="5", cursor=cursor), self.count(limit="5"))

    def test_filters_never_add_queries(self):
        self.book(5)
        plain = self.count()
        for query in (
            {"group": "tickets"},
            {"source": "email,note"},
            {"account": "none"},
            {"q": "renewal"},
            {"thread": "t-1"},
        ):
            with self.subTest(**query):
                self.assertLessEqual(self.count(**query), plain)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.organizations.tests.test_story_views --noinput`
Expected: FAIL. No URL is mounted yet, so the authenticated tests get 404 where they expect 200 and `test_requires_authentication` gets 404 != 401.

- [ ] **Step 3: Implement the view and the URL**

In `services/organizations/views.py`, add these imports beside the existing relative ones (isort keeps `.story…` after `.shape`):

```python
from .story.build import build_story
from .story.params import parse_story_params
from .story.scope import resolve_scope
```

Append the view:

```python
class OrganizationStoryView(APIView):
    """GET /api/v1/organizations/<id>/story/ — the organisation page's Story:
    every record on the organisation and on its accounts the viewer may open,
    newest first across all sources under one keyset cursor, filtered by
    group, source, account, search and email thread, with counts over the
    whole filtered set and the Needs attention block. Twice filtered: the
    organisation must be visible (404 otherwise, before anything is read),
    then each record is read under its own rule. Unknown parameter values are
    ignored. See docs/API_CONTRACTS.md."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        scope = resolve_scope(request.user, pk)
        params = parse_story_params(request.query_params)
        return Response(build_story(request.user, scope, params, today=timezone.localdate()))
```

In `services/organizations/urls.py`, add this entry at the end of `urlpatterns`:

```python
    path("<int:pk>/story/", views.OrganizationStoryView.as_view(), name="organizations-story"),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.organizations --noinput`
Expected: every test passes, the 12 new endpoint tests among them, including `EXPECTED = 31`. If the pinned count differs, follow the class docstring: list the captured SQL and reconcile each query against the listed kinds before changing anything. A number that grows between the two book sizes is always a bug.

- [ ] **Step 5: Lint and commit**

```bash
venv/bin/ruff check --fix services/organizations && venv/bin/ruff format services/organizations
git add services/organizations/views.py services/organizations/urls.py services/organizations/tests/test_story_views.py
git commit -m "feat(organizations): GET /organizations/<id>/story/

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Documentation

**Files:**
- Modify: `docs/API_CONTRACTS.md`, `docs/product/01-prd.md`, `docs/product/05-backend-schema.md`

**Interfaces:**
- Consumes: the endpoint from Task 8. Everything written here must match its behaviour and the pre-flight table.

- [ ] **Step 1: `docs/API_CONTRACTS.md`: Status table**

In the row that begins `| Organizations portfolio (`/organizations` list redesign) | `organizations` |`, replace its ending `(see `copilot`) |` with:

```text
(see `copilot`); the organisation page's Story is `GET /organizations/<id>/story/` |
```

- [ ] **Step 2: `docs/API_CONTRACTS.md`: the endpoint's section**

Insert the following immediately above the line `### Ask Revenact on Organizations`:

````text
### `GET /api/v1/organizations/<id>/story/`

The organisation page's Story tab (spec: react-ts-app `docs/superpowers/specs/2026-09-26-organization-detail-design.md`
§2): every record on the organisation and on its accounts, newest first under one cursor across all sources, with
counts and the Needs attention block. No model: `services/organizations/story/` reads the existing records. The
per-type `/customers/<id>/…` endpoints are unchanged and still handle create and edit.

Auth: `IsAuthenticated`. The organisation must be in `visible_customers(user)`, otherwise `404` (archived and
churned organisations open). Unknown parameter values are ignored, never a 400.

| Param | Meaning |
|---|---|
| `group` | `conversations` (activity, call, email, calendar_event), `tickets` (ticket), `tasks` (task, note), `feedback` (survey), `health` (health) |
| `source` | comma list of exact kinds: `activity`, `calendar_event`, `call`, `email`, `health`, `note`, `survey`, `task`, `ticket`; intersected with `group` |
| `account` | an account id: that account's records only (an account not linked to this organisation, or one the viewer can't open, reads nothing); or `none`: the organisation's own records |
| `q` | case-insensitive contains, first 200 characters, over: activity type; call title, summary, host; email subject, body, sender, recipient; meeting title, description; ticket title, number, description, requester; task title, assignee; note title, body, author; survey type. `health` items are never matched |
| `thread` | an email `thread_id`: the emails of that thread only (opening an email shows its thread). Narrows `items` only |
| `cursor` | opaque, from `next_cursor` |
| `limit` | default 30, max 100 |

```json
{
  "items": [{
    "id": 41, "kind": "call", "source": "zoom",
    "occurred_at": "2026-09-25T14:05:00+00:00", "all_day": false,
    "account": {"id": 9, "name": "EMEA"},
    "title": "Quarterly review", "summary": "They want SSO before the renewal.",
    "actor": {"id": null, "name": "Carl CSM"},
    "link": {"thread_id": null, "url": "https://zoom.us/rec/1"}
  }, {
    "id": 17, "kind": "note", "source": "revenact",
    "occurred_at": "2026-09-24T00:00:00+00:00", "all_day": true,
    "account": null,
    "title": "Champion left", "summary": "Sam moved on to Globex.",
    "actor": {"id": 2, "name": "Carl CSM"},
    "link": {"thread_id": null, "url": null}
  }],
  "next_cursor": "eyJhdCI6IjIwMjYtMDktMjRUMDA6MDA6MDArMDA6MDAiLCJrIjoibm90ZSIsImlkIjoxNywiZiI6IjEyMzQ1Njc4OWFiY2RlZjAifQ",
  "counts": {
    "by_group": {"all": 12, "conversations": 6, "tickets": 2, "tasks": 3, "feedback": 1, "health": 0},
    "by_kind": {"activity": 1, "calendar_event": 1, "call": 2, "email": 2, "health": 0, "note": 2,
                "survey": 1, "task": 1, "ticket": 2},
    "by_account": {"all": 12, "none": 5, "9": 7}
  },
  "attention": {
    "renewal": {"date": "2026-10-08", "days": 12, "overdue": false},
    "tickets": {"count": 2, "oldest_days": 9},
    "overdue_tasks": {"count": 1, "oldest_days": 3},
    "questions": {"count": 1},
    "anomaly": {"id": 5, "title": "Similar reports across 1 of your companies",
                "first_seen_at": "2026-09-20T08:00:00+00:00", "last_seen_at": "2026-09-25T10:00:00+00:00"}
  }
}
```

- **Kinds.** Only kinds with real data exist: `activity` (logged activities), `call` (CallSense: `summary` is the
  call's own summary), `email`, `calendar_event` (meetings up to today; upcoming ones stay on
  `/customers/<id>/calendar-events/`), `ticket`, `task` (from when it was created), `note`, `survey`, and `health`:
  a month-end `HealthSnapshot` whose health category, AI pulse or CSM pulse differs from the same parent's previous
  one. Not sources: Slack, in-app conversations and Revenact Support (added when real), lifecycle changes (not
  stored), and `Customer.pulse`'s undated dots.
- **Scope.** A record is in the story when it is filed on this organisation (`account: null`) or on one of its
  accounts the viewer may open (`visible_accounts`, the per-account endpoints' rule; a shared account's records
  appear on each of its organisations). Records on any other organisation or account never appear.
- **Privacy (twice filtered).** After the organisation, each record is read under its own rule: mail by its
  mailbox owner and their chain (`visible_emails`), notes by author and chain (`visible_notes`), tasks by creator,
  assignee and the chains above them (`visible_tasks`), tickets by department (`visible_tickets`); activities,
  calls, meetings, surveys and health readings by the organisation's rule alone, as on their endpoints. Items,
  counts, attention and search all read the same filtered rows.
- **Items.** `occurred_at` is ISO 8601 UTC. `all_day: true` marks a record that stores only a date (activity,
  meeting, ticket, note, survey, health): read the date part, it is not a moment. `source` is where the record came
  from: the connector's provider (`zendesk`, `jira`, `zoom`, …), the mailbox's provider (`google`, `microsoft`,
  `imap`), or `revenact`. `summary` is one line, at most 240 characters. `actor.id` is a user id when the record
  links a user, else `null` with the name the record stores; `actor` is `null` for activities, meetings, surveys and
  health. `link.thread_id` is an email's thread (pass it as `thread`); `link.url` is a ticket's source-system URL
  or a call's recording, only when it is `http(s)`.
- **Order and pages.** Newest first by `(occurred_at, kind, id)`, all descending. `next_cursor` is `null` on the
  last page. It names the last item served, so rows added or removed elsewhere never cause a skip or a repeat. A
  cursor from other `group`/`source`/`account`/`q`/`thread` values, or a malformed one, reads the first page.
- **Counts** cover the whole filtered set, not the page. `by_kind` and `by_group` follow `account` and `q` but not
  `group`/`source`; `by_account` follows `group`, `source` and `q` but not `account`, and lists `all`, `none` (the
  organisation's own records) and exactly the accounts in scope.
- **Attention** follows `account` (tickets, tasks, anomaly) and nothing else. `renewal`: renewal date overdue or
  within 30 days, `null` for a churned organisation. `tickets`: open High/Critical. `overdue_tasks`: not completed,
  due before today. `questions`: open Knowledge questions on this organisation the viewer may read
  (`visible_questions`). `anomaly`: the latest live anomaly with evidence the viewer may read on this organisation
  or its accounts; its title is the stored one only for a viewer who sees every account, otherwise "Similar reports
  across 1 of your companies". Its summary is never returned. Each entry is `null` when nothing needs attention.
- Constant query count per page, whatever the organisation's size (pinned by `StoryQueryCountTests`).
````

- [ ] **Step 3: `docs/product/01-prd.md`**

In §5.2 Records, add this row directly under the `| Organizations portfolio (list redesign) | Built (backend) |` row:

```text
| Organisation page Story (detail redesign) | Built (backend) | One endpoint for the Story tab: every record on the organisation and its accounts (activities, CallSense calls, emails, meetings, tickets, tasks, notes, surveys, health and pulse changes), newest first under one cursor; group, source, account, search and thread filters; counts per group, kind and account; Needs attention (renewal, urgent tickets, overdue tasks, open questions, latest anomaly). Every record read under its own rule |
```

In the milestones table, add this row after the `| 2026-09-26 | Ask Revenact on Organizations (backend) |` row:

```text
| 2026-09-26 | Organisation page Story (backend): `GET /organizations/<id>/story/` |
```

- [ ] **Step 4: `docs/product/05-backend-schema.md`**

In the `### organizations` subsection, add this paragraph after the one ending "Bulk edits write `Customer` through `CustomerSerializer`.":

```text
The organisation page's Story (`services.organizations.story`) has no model either. It reads `Activity`, `Call`,
`Email`, `CalendarEvent`, `Ticket`, `Task`, `Note`, `Survey` and `HealthSnapshot` for one organisation and its
visible accounts, each under its own record rule, plus `Question` and `AnomalyEvidence` for the Needs attention
block.
```

- [ ] **Step 5: Confirm the two docs that do not change**

Run: `grep -n "organizations" docs/data-classification.md docs/audit-events.md`
Expected: only the portfolio's export and bulk rows. The story stores nothing new, sends nothing outside the tenant and, like the portfolio list, is a read that is not audited, so neither file changes (pre-flight #25).

- [ ] **Step 6: Verify the docs do not break the build, then commit**

Run: `venv/bin/ruff format --check . && venv/bin/ruff check .`
Expected: no changes needed and no lint errors. The inserted blocks contain no `python` fences. If ruff reformats a doc, keep ruff's version.

```bash
git add docs/API_CONTRACTS.md docs/product/01-prd.md docs/product/05-backend-schema.md
git commit -m "docs(organizations): the organisation story's contract, PRD row and schema note

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: End-to-end flow

**Files:**
- Create: `e2e/test_organization_story_flow.py`

**Interfaces:**
- Consumes: `e2e.http.http_get` and `http_post`; the existing create endpoints (`POST /customers/`, `/customers/<id>/accounts/`, `/customers/<id>/accounts/<id>/tasks/`, `/customers/<id>/notes/`, `/customers/<id>/surveys/`); the story endpoint (Task 8).

- [ ] **Step 1: Write the test**

`e2e/test_organization_story_flow.py`:

```python
"""End-to-end tier: real server, real HTTP, real test DB. A CSM builds an
organisation with an account and adds a task, a note and a survey through the
existing create endpoints, then reads them back as one story: filtered by
account, searched and paged, with the attention block. A peer cannot open the
organisation; the admin can, but never sees the CSM's personal note or task."""

from datetime import timedelta
from urllib.parse import urlencode

from django.test import LiveServerTestCase
from django.utils import timezone

from e2e.http import http_get, http_post


class OrganizationStoryFlowTests(LiveServerTestCase):
    def api(self, path):
        return f"{self.live_server_url}/api/v1{path}"

    def add_user(self, admin, name, email, password):
        status, body = http_post(
            self.api("/auth/users/"),
            {"name": name, "email": email, "password": password},
            token=admin,
        )
        self.assertEqual(status, 201, body)
        status, body = http_post(self.api("/auth/login/"), {"email": email, "password": password})
        self.assertEqual(status, 200, body)
        return body["access"]

    def story(self, customer_id, token, **query):
        suffix = f"?{urlencode(query)}" if query else ""
        return http_get(self.api(f"/organizations/{customer_id}/story/{suffix}"), token=token)

    def kinds(self, customer_id, token, **query):
        status, body = self.story(customer_id, token, **query)
        self.assertEqual(status, 200, body)
        return [item["kind"] for item in body["items"]]

    def test_full_flow(self):
        today = timezone.localdate()

        # 1. An organisation signs up; its admin adds two CSMs, who log in.
        status, body = http_post(
            self.api("/auth/signup/"),
            {
                "organisation_name": "Acme Inc",
                "name": "Alice Admin",
                "email": "alice@acme.io",
                "password": "supersecret1",
            },
        )
        self.assertEqual(status, 201, body)
        admin = body["access"]
        carl = self.add_user(admin, "Carl CSM", "carl@acme.io", "csmpassword1")
        dana = self.add_user(admin, "Dana CSM", "dana@acme.io", "csmpassword2")

        # 2. Carl's organisation, renewing in ten days, with one account.
        status, body = http_post(
            self.api("/customers/"),
            {"name": "Pizza Hut", "renewal_date": (today + timedelta(days=10)).isoformat()},
            token=carl,
        )
        self.assertEqual(status, 201, body)
        pizza = body["id"]
        status, body = http_post(self.api(f"/customers/{pizza}/accounts/"), {"name": "EMEA"}, token=carl)
        self.assertEqual(status, 201, body)
        emea = body["id"]

        # 3. An overdue task on EMEA, a note and a survey on the organisation.
        status, body = http_post(
            self.api(f"/customers/{pizza}/accounts/{emea}/tasks/"),
            {
                "title": "Send the QBR deck",
                "due_date": (today - timedelta(days=1)).isoformat(),
                "priority": "high",
            },
            token=carl,
        )
        self.assertEqual(status, 201, body)
        status, body = http_post(
            self.api(f"/customers/{pizza}/notes/"),
            {"title": "Champion left", "body": "Sam moved on to Globex."},
            token=carl,
        )
        self.assertEqual(status, 201, body)
        status, body = http_post(
            self.api(f"/customers/{pizza}/surveys/"),
            {"survey_type": "nps", "sent_at": today.isoformat()},
            token=carl,
        )
        self.assertEqual(status, 201, body)

        # 4. Carl's story: the task (a timestamp) first, then today's survey and
        #    note (dates, ordered by kind); counts and attention over the lot.
        status, body = self.story(pizza, carl)
        self.assertEqual(status, 200, body)
        self.assertEqual([item["kind"] for item in body["items"]], ["task", "survey", "note"])
        self.assertEqual(body["items"][0]["account"], {"id": emea, "name": "EMEA"})
        self.assertIsNone(body["items"][2]["account"])
        self.assertEqual(body["counts"]["by_account"], {"all": 3, "none": 2, str(emea): 1})
        self.assertEqual(
            body["counts"]["by_group"],
            {"all": 3, "conversations": 0, "tickets": 0, "tasks": 2, "feedback": 1, "health": 0},
        )
        self.assertEqual(
            body["attention"]["renewal"],
            {"date": (today + timedelta(days=10)).isoformat(), "days": 10, "overdue": False},
        )
        self.assertEqual(body["attention"]["overdue_tasks"], {"count": 1, "oldest_days": 1})

        # 5. The account chip, the Organisation chip, a search and paging.
        self.assertEqual(self.kinds(pizza, carl, account=emea), ["task"])
        self.assertEqual(self.kinds(pizza, carl, account="none"), ["survey", "note"])
        self.assertEqual(self.kinds(pizza, carl, q="globex"), ["note"])
        seen, cursor = [], None
        while True:
            query = {"limit": 1, **({"cursor": cursor} if cursor else {})}
            status, body = self.story(pizza, carl, **query)
            self.assertEqual(status, 200, body)
            seen += [item["kind"] for item in body["items"]]
            cursor = body["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(seen, ["task", "survey", "note"])

        # 6. Dana cannot open Carl's organisation. The admin can, but Carl's
        #    note and task are his (and his chain's) alone.
        status, _body = self.story(pizza, dana)
        self.assertEqual(status, 404)
        status, body = self.story(pizza, admin)
        self.assertEqual(status, 200, body)
        self.assertEqual([item["kind"] for item in body["items"]], ["survey"])
        self.assertEqual(body["counts"]["by_group"]["all"], 1)
        self.assertIsNone(body["attention"]["overdue_tasks"])
```

- [ ] **Step 2: Run it**

Run: `venv/bin/python manage.py test e2e.test_organization_story_flow --noinput`
Expected: PASS. It exercises the real server, JWT auth, the existing create endpoints and the story. If it fails, the bug is in the implementation: debug with superpowers:systematic-debugging, and do not weaken the test.

- [ ] **Step 3: Lint and commit**

```bash
venv/bin/ruff check --fix e2e && venv/bin/ruff format e2e
git add e2e/test_organization_story_flow.py
git commit -m "test(organizations): end-to-end organisation story flow

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Full verification

**Files:** none are created. Fix whatever a step below uncovers in the task that owns it, then re-run from Step 1.

- [ ] **Step 1: Run the whole suite**

Run: `venv/bin/python manage.py test --parallel auto --noinput`
Expected: every test passes. Record the final `Ran N tests … OK` line.

- [ ] **Step 2: Lint, format and migrations**

Run:

```bash
venv/bin/ruff check . && venv/bin/ruff format --check .
venv/bin/python manage.py makemigrations --check --dry-run
```

Expected: no lint errors, no files to reformat, and "No changes detected". The story has no models.

- [ ] **Step 3: Run the security gates as CI runs them**

Run:

```bash
semgrep scan --error --metrics=off --config p/security-audit --config p/secrets --config p/owasp-top-ten --config p/django --exclude .claude --exclude venv --exclude '**/tests/**' --exclude '**/migrations/**' --exclude e2e services/organizations
python3 .claude/skills/soc2-dev/scripts/soc2_scan.py . --format md --fail-on critical
```

Expected: semgrep reports 0 findings, and the soc2 scan has no critical findings. If `semgrep` is not installed, run it as `pipx run semgrep scan …`.

- [ ] **Step 4: Check the schema**

Run: `venv/bin/python manage.py spectacular --file /tmp/revenact-schema.yml`
Expected: it exits 0 and `/api/v1/organizations/{id}/story/` appears in the file (`grep story /tmp/revenact-schema.yml`). "Unable to guess serializer" warnings are expected on `APIView`s, as on the portfolio views. Errors are not.

- [ ] **Step 5: Smoke test against the seeded dev database**

Run:

```bash
venv/bin/python manage.py runserver 8011 &
TOKEN=$(curl -s -X POST localhost:8011/api/v1/auth/login/ -H 'Content-Type: application/json' \
  -d '{"email":"<a seeded admin email>","password":"<its password>"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access"])')
ORG=$(curl -s "localhost:8011/api/v1/organizations/portfolio/?limit=1" -H "Authorization: Bearer $TOKEN" | python3 -c 'import sys,json;print(json.load(sys.stdin)["results"][0]["id"])')
curl -s "localhost:8011/api/v1/organizations/$ORG/story/?limit=5" -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | head -80
curl -s "localhost:8011/api/v1/organizations/$ORG/story/?group=health" -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | head -30
kill %1
```

Take the seeded admin's email and password from `README.md` ("Seed data"). Expected: five items newest first, each with every contract field; `counts` with `by_group`, `by_kind` and `by_account`; the seeded year of snapshots shows as `health` items where a category or pulse moved; `attention` has all five keys.

- [ ] **Step 6: Report**

Use superpowers:verification-before-completion. Report the test count, the ruff/semgrep/soc2 results and the smoke output. Then hand off to superpowers:finishing-a-development-branch. The backend PR merges and deploys before the frontend PR (spec §4). The PR description lists the frontend-facing additions from the pre-flight table: `all_day`, `counts.by_kind`, the `thread` parameter, `link = {thread_id, url}`, `by_account` covering only accounts in scope (#3), and "Log activity" having no create endpoint (#22).

---

## Self-review

**Spec coverage.**

| Spec requirement | Where it is covered |
|---|---|
| §2 organisation in `visible_customers`, else 404 | Task 3 (`resolve_scope`), Task 8 (API 404 and 401 tests) |
| Every parameter; unknown values dropped | Task 1 (parsing), Task 6 (each filter's effect), Task 8 (API) |
| Response shape; `counts` and `attention` over the whole filtered set | Task 4 (item), Task 6 (counts with `limit=1`), Task 7 (attention), Task 8 (shape) |
| Sources, only kinds with real data, documented | Task 1 (`KINDS`), Task 3 (`SOURCES`), Task 5 (health), Task 9 (the Kinds note) |
| Calls with their CallSense summary | Task 4 (`test_a_call_carries_its_callsense_summary_and_recording`) |
| Pulse history where stored; health changes | Task 5 |
| Privacy: each record's own rule; counts, attention, search follow | Task 3 (rules), Task 6 (counts and search start from the same bases), Task 7 (attention), Task 8 (`StoryPrivacyTests`) |
| Anomaly title withheld unless `sees_everything` | Task 7 |
| One keyset cursor over `(occurred_at, kind, id)`, bound to the filters | Task 2, Task 6 (walk at three page sizes; stale cursor), Task 8 (walk over HTTP) |
| Constant queries per page, pinned at two book sizes | Task 8 (`StoryQueryCountTests`) |
| Only records linked to this organisation; account filter keeps it | Task 3 (`parent_q`, shared and foreign accounts), Task 6, Task 8 |
| Attention rules exactly as the spec | Task 7 (renewal ≤ 30 days or overdue, High/Critical count and oldest age, overdue tasks, unanswered questions, latest anomaly) |
| §5 story items equal the per-type endpoints' records | Task 8 (`StoryMatchesPerTypeEndpointsTests`, for a CSM and an admin) |
| §5 every filter, source, account and search; cursor to the end without gaps or repeats | Task 6, Task 8 |
| §5 invisible organisation 404; other departments' tickets, others' mail, private notes never appear | Task 3, Task 8 |
| Docs: API_CONTRACTS, data classification if needed, product docs | Task 9 (data classification and audit events reviewed, no change) |
| E2E | Task 10 |
| Verification | Task 11 |
| §3 Ask on the page, §4 deliveries 2 and 3, the frontend | Out of scope. `build_story` and `Scope` are what delivery 3's grounding will reuse |

**Placeholder scan.** Every code step has complete code. The values read at run time are the pinned query count, which Task 8 enumerates query by query, and the seeded login in Task 11's smoke test, which `README.md` holds.

**Type consistency.** `Scope(customer, accounts)` and `Scope.parent_q(account=None)` are used the same way by `sources.Source.base`, `health.health_entries`, `build.build_page` and `attention.build_attention`. `SOURCES[kind].base(user, scope, *, horizon)` is called by `build_story` and the fixture's `base()`. The sort key is `(datetime, kind, id)` everywhere: `render` rows (`row._at`), `health_entries`, `Cut`, `after_q` and `is_after`. `make_item(scope, *, ident, kind, at, all_day, account_id, title, …)` is shared by `render` and `health_entries`. `build_attention(user, scope, bases, account, *, today)` takes the same `bases` dict `build_story` builds. `StoryParams.selected_kinds` feeds the counts and `page_kinds` the page.
