# Account page, backend (delivery 2: the account's story) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve the redesigned `/accounts/:id` page: `GET /api/v1/accounts/<id>/story/` (items, counts and Needs attention in the organisation story's shapes and params), plus the page's tabs and create flows on account-keyed routes (`/api/v1/accounts/<id>/…`) that need no organisation id.

**Architecture:** The organisation story engine (`services/organizations/story/`) already runs over anything with `parent_q()`, `account_ref()` and `accounts`; two things tie it to an organisation: the cursor fingerprint reads `scope.customer.pk`, and `build_story` always calls the organisation's `build_attention`. Task 1 removes both ties (a `cursor_key` on the scope, an `attention=` argument defaulting to the organisation's), with no change to Organizations' output. A new model-less app, `services/account_story/`, adds the account's own rules: `AccountScope` / `resolve_account_scope` (404 unless `visible_accounts`; rows with `account_id` = this account only), `build_account_attention` (renewal, urgent tickets, overdue tasks), the story view, and one `urls.py` that also mounts the existing nested per-account views flat, resolved through a new `scoping.get_url_account`.

**Tech Stack:** Django 5, DRF, PostgreSQL, `SimpleTestCase` / `TestCase` / `APITestCase` / `APIClient`, `LiveServerTestCase` + `e2e.http` for e2e.

**Spec:** `react-ts-app/docs/superpowers/specs/2026-09-29-accounts-redesign-design.md`. This plan covers §2 "Backend (delivery 2)", the parts of §2 items 2, 5 and 7–9 that decide what the backend must serve, the Decisions table, §4 (delivery order) and §5 Testing (backend). Delivery 1 (`GET /accounts/portfolio/`, PR #74) and delivery 3 (Ask) are out of scope. For the engine being reused, read `docs/superpowers/plans/2026-09-26-organization-story-backend.md` and the shipped `services/organizations/story/`.

## Global Constraints

Copied from the spec (quoted text is verbatim):

- Decisions, Backend: "Account-specific services (`services/accounts_portfolio/`). They reuse Organizations' generic helpers (health and renewal filters, `signal_for`, `snapshot_history`, `triage`, the story sources) and keep account rules and fields their own. A generic "company kind" through every Organizations module was rejected: it couples two sets of rules." (See Decision 1 for the package name.)
- Decisions: "No archive or churn: Accounts have neither, so there is no Archive, Churn or churned filter on Accounts."
- Decisions, Knowledge: "Company knowledge is per organisation. The account page has no Knowledge tab; Details links to the organisation's".
- §2: "`/accounts/:id` is **the account's story**: the organisation page's design, scoped to one account."
- §2.2: "**Part of** links beneath. Each opens `/organizations/:id?account=<this>`, with this account's chip chosen. There is one link per linked organisation the viewer may open." "**Edit** and **⋯** on the right. ⋯ holds Add contact, Log a call and New task."
- §2.4: "There are no account chips."
- §2.5 Needs attention: "renewal overdue, or due within 30 days"; "open High or Critical tickets (count and oldest age)"; "overdue tasks".
- §2.5: "**Filters:** All · Conversations · Tickets · Tasks & notes · Feedback · Health & usage, plus the Sources picker and search." "**+ Add:** Log a call, New task, New note, Log survey. These are the existing account create flows." "**Stream:** grouped by day, newest first. Only records filed on this account appear."
- §2.7: "**People:** the account's contacts as list items. A name opens `/contacts/:id`, and the list has a summary line and search."
- §2.8: "**Deals & risks:** the Opportunities / Risks switch and its list items, as on the organisation page. Adds go to this account."
- §2.9: "**Files:** Files and Calls sections, as on the organisation page. Uploads and logged calls go to this account."
- Backend (delivery 2): "**`GET /api/v1/accounts/<id>/story/`** — Returns items, counts and attention, in the organisation story's shapes and with its params (filters, sources, search, cursor)."
- Backend (delivery 2): "An account scope for the story code (`resolve_account_scope`) gives a 404 when the viewer cannot open the account, whether or not it exists. Each source then applies its own record rule: mail by mailbox owner and chain, tickets by department, notes and tasks by their personal rules."
- Backend (delivery 2): "Only records with `account_id` = this account are included."
- Backend (delivery 2): "**The `/accounts/<id>/…` endpoints** (contacts, opportunities, risks, files, calls, surveys, tasks, notes) serve the tabs and the create flows. An account filter or field is added only where one is missing."
- §4: "A backend PR merges and deploys before its frontend, unless its change breaks the deployed frontend; then the frontend goes first, tolerating both shapes." Nothing here changes an existing response, so this PR goes first.
- §5 Backend: "Unit, integration and e2e tests." "Privacy through the "blind to one account" setup: an account the viewer cannot open is absent from … the story." "Mail, ticket, note and task record rules on the story." "Query counts pinned flat as rows and records grow."

House rules (every task):

- `docs/API_CONTRACTS.md` changes in the same PR (skill `.claude/skills/api-contracts`), and so do the product docs (`docs/product/01-prd.md` feature row and release history, `02-trd.md`, `05-backend-schema.md`). `docs/data-classification.md` and `docs/audit-events.md` need no change: no new stored data, no new audit action (file upload and call log keep their existing `file.upload` / `call.log` records).
- Three test tiers (skill `.claude/skills/backend-testing`): unit (`SimpleTestCase`), integration (`TestCase` / `APITestCase` + `APIClient`), e2e (`LiveServerTestCase` in `e2e/`). Privacy uses `blind_to_one_account` from `services/customers/tests/test_views.py`. Focused runs: `venv/bin/python manage.py test <label> --noinput`. Full suite: `venv/bin/python manage.py test --parallel --noinput` (serial hits CI's 30-minute timeout).
- `venv/bin/ruff check .` and `venv/bin/ruff format --check .` must pass (line length 100). Ruff formats Python fences inside Markdown; the doc edits in this plan use only `json` fences and prose.
- `# SOC2:AUTH-02 <why>` on every access check.
- Semgrep runs in CI (`p/security-audit`, `p/secrets`, `p/owasp-top-ten`, `p/django`): return the opaque cursor string, never a built URL; no hand-built `HttpResponse`.
- No model, no migration: `venv/bin/python manage.py makemigrations --check --dry-run` stays clean.
- Commits are conventional (`refactor(organizations): …`, `feat(accounts): …`, `test(e2e): …`) and end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Branch: `feat/account-story`, cut from `main`. PR #74 (delivery 1) is still open and touches the same three shared lines (see Decision 10).

## Decisions this plan makes where the spec is silent

1. **Package: `services/account_story/`, not `services/accounts_portfolio/`.** `accounts_portfolio` exists only on the open PR #74; building inside it would stack this PR on #74 (a known trap in this repo). The story shares no code with the portfolio, so a separate model-less app keeps both PRs independent. It is still account-specific code that reuses Organizations' story engine, as the Decisions row asks. Delivery 3 imports from both apps.
2. **The smallest engine change: a scope protocol plus an attention hook.** `Scope` gains `cursor_key` (the organisation id, so every existing fingerprint and cursor is byte-identical), `cursor.fingerprint(params, key)` takes that key, and `build_story(..., attention=build_attention)` takes the Needs-attention builder. `AccountScope` implements `accounts`, `cursor_key` (`"account:<id>"`, so an organisation's cursor never reads on an account page), `parent_q(account=None)` and `account_ref(account_id)`. A `StoryScope` `Protocol` in `scope.py` documents the four. Nothing else in `services/organizations/story/` changes; `sources.py`, `items.py`, `health.py`, `params.py` are reused as they are.
3. **`account` is ignored on the account story.** `parse_story_params` still parses it (the params stay the organisation story's), and the view drops it (`dataclasses.replace(params, account=None)`): the page has no account chips (§2.4), and a stale `?account=` must neither empty the page nor error. `counts.by_account` keeps its shape: `{"all": n, "none": 0, "<this account id>": n}`.
4. **Attention keeps the organisation story's five keys.** `renewal`, `tickets`, `overdue_tasks` are computed; `questions` and `anomaly` are always `null` (Knowledge is per organisation; the spec's account list omits both), so the frontend's attention component takes either body unchanged. Tickets and tasks reuse `organizations.story.attention.urgent_tickets` / `overdue_tasks` over the story's own base querysets. The renewal reads `Account.renewal_date` with the same 30-day window (`RENEWAL_WINDOW_DAYS`) and no churn test, because accounts have no churn.
5. **The tabs use new flat routes, `/api/v1/accounts/<id>/…`, that mount the existing nested view classes.** An account can be visible (`visible_accounts`: unowned, or owned by you or your reports) while none of its organisations is (`visible_customers`), so the page cannot always name an organisation id. The nested `/customers/<cid>/accounts/<id>/…` routes stay unchanged. Both route shapes resolve the account through one helper, `scoping.get_url_account(request, kwargs)`. The nested route calls `get_visible_account` with the organisation pin; the flat route calls `get_object_or_404(visible_accounts(user), pk=…)`. Every queryset, record rule, create path and serializer is the nested view's own, so nothing is copied.
6. **Flat routes: exactly the eight tabs the spec names, plus `GET/PATCH /accounts/<id>/`.** The name row's **Edit** needs a write path that works without an organisation id, so the flat detail mounts `AccountDetailView`. Activities, emails, tickets and calendar events get no flat route: the page reads them only through the story, and opening an email's thread is `?thread=` on the story. Headlines and canvases get none either: the spec removes those tabs.
7. **No field or filter is added (the audit is in "Tab audit" below).** Every tab's serializer already carries what the page reads. The contact list is unpaginated, so People's summary line and search run client-side, as on the organisation page. The header, tiles, Details panels and "Part of" links (organisations the viewer may open) come from delivery 1's `GET /accounts/portfolio/?ids=<id>`, which already serves them.
8. **`AccountSerializer.customers` stays as it is.** It names every linked organisation, including ones the viewer cannot open. That is existing behaviour on the nested detail and on `GET /accounts/`, and the flat detail does not widen it. The page takes "Part of" from the portfolio row, which is filtered. This is recorded as a follow-up, not changed here.
9. **Pinned query count: 24 per story request**, whatever the account's size. The enumeration is in Task 6.
10. **Merge with #74.** `config/settings.py` (`INSTALLED_APPS`), `config/urls.py` (the `api/v1/accounts/` includes), the `API_CONTRACTS.md` feature table and the PRD release history take adjacent lines in both PRs. Whichever merges second keeps both sides' lines. The `include()`s are independent: neither app defines `""` or `stats/`, and their route sets do not overlap (`portfolio/`, `bulk/` vs `<int>/…`).

## Tab audit: what exists vs what the page needs (spec §2 items 2, 5, 7–9)

Every row below was checked in `services/customers/views.py` / `serializers.py` on `main`.

| Page need | Existing endpoint (nested) | Fields the page reads | Missing | This plan |
|---|---|---|---|---|
| People list (§2.7), ⋯ Add contact | `GET/POST /customers/<cid>/accounts/<id>/contacts/` (`AccountContactListView`, unpaginated, `readable_calls_count` annotated) | `id`, `name`, `role`, `email`, `sentiment`, `calls`, `account_id`, `account_name` | Only an organisation-free route | Flat `/accounts/<id>/contacts/` |
| Opportunities (§2.8), add | `…/opportunities/` (`AccountOpportunityListView`, `pipeline_visible_q`) | `id`, `title`, `stage`, `mrr`, `priority`, `department`, `account_id` | Only the route | Flat `/accounts/<id>/opportunities/` |
| Risks (§2.8), add | `…/risks/` (`AccountRiskListView`, `pipeline_visible_q`) | same shape as opportunities | Only the route | Flat `/accounts/<id>/risks/` |
| Files (§2.9), upload | `…/files/` (`AccountFileListView`, multipart, audited `file.upload`) | `id`, `name`, `size`, `uploaded_by`, `account_id`, `account_name` | Only the route | Flat `/accounts/<id>/files/` |
| Calls (§2.9), ⋯ / + Log a call | `…/calls/` (`AccountCallListView`, JSON or multipart, audited `call.log`, `classify_call`) | `id`, `title`, `summary`, `participants`, `account_id`, `account_name` | Only the route | Flat `/accounts/<id>/calls/` |
| + Log survey (§2.5) | `…/surveys/` (`AccountSurveyListView`, CES refused) | `id`, `survey_type`, `sent_at`, `account_id` | Only the route | Flat `/accounts/<id>/surveys/` |
| ⋯ / + New task (§2.5) | `…/tasks/` (`AccountTaskListView`, `visible_tasks`) | `id`, `title`, `due_date`, `priority`, `status`, `assignee` | Only the route | Flat `/accounts/<id>/tasks/` |
| + New note (§2.5) | `…/notes/` (`AccountNoteListView`, `visible_notes`) | `id`, `title`, `body`, `author`, `logged_at` | Only the route | Flat `/accounts/<id>/notes/` |
| Edit (§2.2, §2.6 "Edit details") | `GET/PATCH /customers/<cid>/accounts/<id>/` (`AccountDetailView`) | the editable account fields | Only the route | Flat `/accounts/<id>/` |
| Story, filters, attention (§2.5) | none | — | The endpoint | `GET /accounts/<id>/story/` |
| Name row, tiles, panels, Part of (§2.2, 2.3, 2.6) | delivery 1 `GET /accounts/portfolio/?ids=<id>` | row, `details`, linked organisations the viewer may open | Nothing | None |

## Reuse map

Before relying on each helper, the plan read its code and confirmed its signature on `main`.

| Need | Source (signature) | Use |
|---|---|---|
| Record sources and their rules | `organizations.story.sources.SOURCES`; `Source.base(self, user, scope, *, horizon)` reads `self.model.objects.filter(scope.parent_q())` then `self.rule(user, rows)` | Unchanged; `AccountScope.parent_q()` narrows to the account |
| Page, counts, attention, merge | `organizations.story.build.build_story(user, scope, params, *, today)` → Task 1 adds `attention=build_attention` | Called with `attention=build_account_attention` |
| Items | `organizations.story.items.render(kind, row, scope)`, `make_item(scope, *, ident, kind, at, all_day, account_id, title, …)` → `scope.account_ref(account_id)` | Unchanged |
| Health changes | `organizations.story.health.health_entries(scope, *, horizon)` → `HealthSnapshot.objects.filter(scope.parent_q())` | Unchanged; only this account's snapshots |
| Params | `organizations.story.params.parse_story_params(query) -> StoryParams` (frozen dataclass) | Reused; `account` replaced with `None` in the view |
| Cursor | `organizations.story.cursor.fingerprint(params, customer_id: int) -> str` | Task 1 renames the second parameter `key: int \| str`; positional callers unchanged |
| Counts | `build.build_counts(counts, params, scope)` lists `scope.accounts` | `AccountScope.accounts` is `{id: name}` of the one account |
| Tickets / tasks attention | `organizations.story.attention.urgent_tickets(tickets, today)`, `overdue_tasks(tasks, today)`, `RENEWAL_WINDOW_DAYS = 30` | Imported |
| Organisation renewal | `organizations.story.attention.renewal(customer, today)` | **Not reused**: it reads `customer.churn_date`, which `Account` does not have. `account_renewal` is the account's own |
| Account visibility | `customers.scoping.visible_accounts(user)` (distinct), `get_visible_account(request, customer_id, account_id)` | `resolve_account_scope` and `get_url_account` |
| Per-tab views | `customers.views.AccountContactListView`, `AccountOpportunityListView`, `AccountRiskListView`, `AccountFileListView`, `AccountCallListView`, `AccountSurveyListView`, `AccountTaskListView`, `AccountNoteListView`, `AccountDetailView` | Mounted flat; their account lookup goes through `get_url_account` |
| Test fixture | `organizations.tests.story_fixtures.StoryFixture` (Carl's Pizza Hut; unowned EMEA and APAC; makers per kind; `keys(body)`) | Subclassed by `AccountStoryFixture` |

## File Structure

| File | Responsibility |
|---|---|
| `services/organizations/story/scope.py` (modify) | `StoryScope` protocol; `Scope.cursor_key` |
| `services/organizations/story/cursor.py` (modify) | `fingerprint(params, key)` |
| `services/organizations/story/build.py` (modify) | `build_page` keys the cursor by `scope.cursor_key`; `build_story(..., attention=build_attention)` |
| `services/organizations/tests/test_story_scope.py` (new) | Pins the protocol: any scope, any attention builder |
| `services/account_story/__init__.py`, `apps.py` (new) | App config `AccountStoryConfig` |
| `services/account_story/scope.py` (new) | `AccountScope`, `resolve_account_scope(user, account_id)` |
| `services/account_story/attention.py` (new) | `account_renewal(account, today)`, `build_account_attention(user, scope, bases, account, *, today)` |
| `services/account_story/views.py` (new) | `AccountStoryView` |
| `services/account_story/urls.py` (new) | The story route and the flat per-account routes |
| `services/account_story/tests/__init__.py`, `fixtures.py`, `test_scope.py`, `test_attention.py`, `test_views.py`, `test_story_queries.py` (new) | Unit, integration, privacy, parity, query count |
| `services/customers/scoping.py` (modify) | `get_url_account(request, kwargs)` |
| `services/customers/views.py` (modify) | The nine account views resolve through `get_url_account` / either route |
| `services/customers/tests/test_account_routes.py` (new) | The flat routes: parity, 404s, record rules, creates, query parity |
| `config/settings.py`, `config/urls.py` (modify) | Register the app; mount `api/v1/accounts/` |
| `e2e/test_account_story_flow.py` (new) | Create through the flat routes, read the story over real HTTP |
| docs (modify) | `API_CONTRACTS.md`, `product/01-prd.md`, `product/02-trd.md`, `product/05-backend-schema.md` |

---

### Task 1: Let the story engine run over any scope

A refactor with no change to Organizations' output. The existing `services.organizations` and `services.copilot` suites are the regression gate (the copilot organisation-detail grounding calls `build_story` and `health_entries`).

**Files:**
- Modify: `services/organizations/story/scope.py`, `services/organizations/story/cursor.py:8-45`, `services/organizations/story/build.py:43-45,117-127`
- Test: `services/organizations/tests/test_story_scope.py` (new)

**Interfaces:**
- Consumes: the existing story modules.
- Produces:
  - `services.organizations.story.scope.StoryScope`, a `typing.Protocol` with `accounts: dict[int, str]`, `cursor_key -> int | str` (property), `parent_q(self, account=None) -> Q`, and `account_ref(self, account_id) -> dict | None`.
  - `Scope.cursor_key -> int` (the organisation id).
  - `services.organizations.story.cursor.fingerprint(params, key: int | str) -> str`.
  - `services.organizations.story.build.build_story(user, scope, params, *, today, attention=build_attention) -> dict`. `attention(user, scope, bases, account, *, today)` returns the `attention` value.

- [ ] **Step 1: Write the failing tests**

`services/organizations/tests/test_story_scope.py`:

```python
"""What the story engine needs from a scope, pinned so a scope other than the
organisation's (the account page's) can drive it: `accounts`, `cursor_key`,
`parent_q` and `account_ref`, plus the Needs attention builder it is given."""

import hashlib
import json
from dataclasses import dataclass

from django.db.models import Q

from services.organizations.story.build import build_story
from services.organizations.story.cursor import fingerprint
from services.organizations.story.params import StoryParams, parse_story_params

from .story_fixtures import StoryFixture


@dataclass(frozen=True)
class OneAccount:
    """The smallest scope the engine accepts: one account's rows."""

    account_id: int
    name: str

    @property
    def accounts(self):
        return {self.account_id: self.name}

    @property
    def cursor_key(self):
        return f"test:{self.account_id}"

    def parent_q(self, account=None):
        return Q(account_id=self.account_id)

    def account_ref(self, account_id):
        return None if account_id is None else {"id": account_id, "name": self.name}


def count_tickets(user, scope, bases, account, *, today):
    return {"tickets": bases["ticket"].count()}


class ScopeCursorKeyTests(StoryFixture):
    def test_the_organisation_scope_keys_its_cursors_by_the_organisation_id(self):
        self.assertEqual(self.scope().cursor_key, self.pizza.pk)

    def test_an_organisation_fingerprint_is_what_it_always_was(self):
        # Guards cursors already served: the key is the bare id, as before.
        state = [1, "", [], None, "", ""]
        expected = hashlib.sha256(json.dumps(state, separators=(",", ":")).encode()).hexdigest()[
            :16
        ]
        self.assertEqual(fingerprint(StoryParams(), 1), expected)

    def test_a_text_key_never_matches_the_organisation_id(self):
        self.assertNotEqual(fingerprint(StoryParams(), "account:1"), fingerprint(StoryParams(), 1))


class BuildStoryOverAnyScopeTests(StoryFixture):
    def story_of(self, scope, **query):
        return build_story(
            self.csm,
            scope,
            parse_story_params(query),
            today=self.today,
            attention=count_tickets,
        )

    def test_build_story_runs_over_any_scope_with_its_own_attention(self):
        self.note(self.pizza)
        self.note(self.apac)
        on_emea = self.note(self.emea)
        ticket = self.ticket(self.emea)
        body = self.story_of(OneAccount(self.emea.pk, "EMEA"))
        self.assertEqual(self.keys(body), [("ticket", ticket.pk), ("note", on_emea.pk)])
        self.assertEqual(body["items"][0]["account"], {"id": self.emea.pk, "name": "EMEA"})
        self.assertEqual(body["attention"], {"tickets": 1})
        self.assertEqual(body["counts"]["by_account"], {"all": 2, "none": 0, str(self.emea.pk): 2})

    def test_the_default_attention_is_still_the_organisation_s(self):
        self.assertEqual(
            set(self.story()["attention"]),
            {"renewal", "tickets", "overdue_tasks", "questions", "anomaly"},
        )

    def test_a_cursor_cut_under_another_scope_reads_the_first_page(self):
        for _ in range(3):
            self.note(self.emea)
        cursor = self.story(limit="1")["next_cursor"]
        self.assertIsNotNone(cursor)
        scope = OneAccount(self.emea.pk, "EMEA")
        first = self.story_of(scope, limit="1")
        moved = self.story_of(scope, limit="1", cursor=cursor)
        self.assertEqual(self.keys(moved), self.keys(first))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.organizations.tests.test_story_scope --noinput`
Expected: FAIL. `test_the_organisation_scope_keys_its_cursors_by_the_organisation_id` raises `AttributeError: 'Scope' object has no attribute 'cursor_key'`, and the `BuildStoryOverAnyScopeTests` that pass `attention=` raise `TypeError: build_story() got an unexpected keyword argument 'attention'`. The two fingerprint tests and `test_the_default_attention_is_still_the_organisation_s` pass already; they guard behaviour that must not change.

- [ ] **Step 3: Implement**

In `services/organizations/story/scope.py`, change the imports and add the protocol above `Scope`. Add the property to `Scope` after `account_ref`:

```python
from dataclasses import dataclass
from typing import Protocol

from django.db.models import Q
from django.shortcuts import get_object_or_404

from services.customers.models import Customer
from services.customers.scoping import visible_accounts, visible_customers

from .params import NO_ACCOUNT


class StoryScope(Protocol):
    """What the story engine (`build_story` and the modules it calls) reads
    from a scope. `Scope` below is an organisation and its accounts in scope;
    the account page's `services.account_story.scope.AccountScope` is one
    account. The record rules are the sources' own, never the scope's."""

    #: The accounts in scope, id -> name: `counts.by_account` lists these.
    accounts: dict[int, str]

    @property
    def cursor_key(self) -> int | str:
        """What a cursor is cut under, beside the filters."""

    def parent_q(self, account=None) -> Q:
        """The rows in scope; `account` narrows as `StoryParams.account` does."""

    def account_ref(self, account_id) -> dict | None:
        """An item's `account`: `{id, name}`, or None for the parent's own row."""
```

```python
    @property
    def cursor_key(self) -> int:
        """The organisation id, as cursors were always keyed, so a cursor
        served before the account page existed still reads."""
        return self.customer.pk
```

In `services/organizations/story/cursor.py`, replace the second paragraph of the module docstring and `fingerprint`:

```python
The cursor also carries a hash of the scope's `cursor_key` (the organisation
id, or the account page's `account:<id>`) and the filters it was cut under,
the portfolio's rule (`shape.filter_fingerprint`): changing any filter, or the
organisation or account, while keeping the cursor reads the new list from its
first page. A malformed or tampered cursor reads as absent, the first page too.
```

```python
def fingerprint(params, key: int | str) -> str:
    """Everything that decides which items a list holds, but not `cursor`
    or `limit`: the scope's `cursor_key` and the filters. `sources` is
    already sorted and de-duplicated."""
    state = [
        key,
        params.group,
        list(params.sources),
        params.account,
        params.q,
        params.thread,
    ]
    return hashlib.sha256(json.dumps(state, separators=(",", ":")).encode()).hexdigest()[:16]
```

In `services/organizations/story/build.py`, key the cursor by the scope and take the attention builder:

```python
def build_page(bases, health, params, scope):
    fp = fingerprint(params, scope.cursor_key)
```

```python
def build_story(user, scope, params, *, today, attention=build_attention):
    """`scope` is any `StoryScope`. `attention(user, scope, bases, account,
    *, today)` builds the Needs attention block from the same base
    querysets: the organisation's by default, the account page's
    (`services.account_story.attention.build_account_attention`) there."""
    horizon = horizon_for(today)
    bases = {kind: source.base(user, scope, horizon=horizon) for kind, source in SOURCES.items()}
    health = health_entries(scope, horizon=horizon)
    items, next_cursor = build_page(bases, health, params, scope)
    return {
        "items": items,
        "next_cursor": next_cursor,
        "counts": build_counts(tally(bases, health, params.q), params, scope),
        "attention": attention(user, scope, bases, params.account, today=today),
    }
```

- [ ] **Step 4: Run the tests to verify they pass, and the regression gate**

Run: `venv/bin/python manage.py test services.organizations services.copilot --parallel --noinput`
Expected: PASS, including every existing `test_story_*` module unchanged.

- [ ] **Step 5: Commit**

```bash
git add services/organizations/story/scope.py services/organizations/story/cursor.py \
  services/organizations/story/build.py services/organizations/tests/test_story_scope.py
git commit -m "refactor(organizations): the story engine runs over any scope and attention builder

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: The account scope

**Files:**
- Create: `services/account_story/__init__.py` (empty), `services/account_story/apps.py`, `services/account_story/scope.py`, `services/account_story/tests/__init__.py` (empty), `services/account_story/tests/fixtures.py`, `services/account_story/tests/test_scope.py`
- Modify: `config/settings.py:93` (`INSTALLED_APPS`)

**Interfaces:**
- Consumes: `services.customers.scoping.visible_accounts(user)`; `StoryFixture` (Task 1's file is unchanged).
- Produces:
  - `services.account_story.scope.AccountScope(account: Account)`, a frozen dataclass implementing `StoryScope`. `accounts -> {account.pk: account.name}`, `cursor_key -> "account:<pk>"`, `parent_q(account=None) -> Q(account_id=pk)`, where any other `account` gives `Q(pk__in=[])`, and `account_ref(account_id) -> {"id", "name"} | None`.
  - `services.account_story.scope.resolve_account_scope(user, account_id) -> AccountScope`, which raises `Http404` unless the account is in `visible_accounts(user)`.
  - `services.account_story.tests.fixtures.AccountStoryFixture(StoryFixture)` with `account_scope(user=None, account=None) -> AccountScope` (defaults: `self.csm`, `self.emea`).

- [ ] **Step 1: Write the failing tests**

`services/account_story/tests/fixtures.py`:

```python
"""The organisation story's fixture (Carl's Pizza Hut with the unowned EMEA
and APAC accounts, one maker per record kind), read through the account
page's scope."""

from services.account_story.scope import resolve_account_scope
from services.organizations.tests.story_fixtures import StoryFixture


class AccountStoryFixture(StoryFixture):
    def account_scope(self, user=None, account=None):
        return resolve_account_scope(user or self.csm, (account or self.emea).pk)
```

`services/account_story/tests/test_scope.py`:

```python
from django.db.models import Q
from django.http import Http404
from django.test import SimpleTestCase

from services.account_story.scope import AccountScope, resolve_account_scope
from services.customers.models import Account
from services.customers.tests.test_views import blind_to_one_account

from .fixtures import AccountStoryFixture


class AccountScopeTests(SimpleTestCase):
    def setUp(self):
        self.scope = AccountScope(account=Account(pk=7, name="EMEA"))

    def test_rows_filed_on_this_account_only(self):
        self.assertEqual(self.scope.parent_q(), Q(account_id=7))
        self.assertEqual(self.scope.parent_q(7), Q(account_id=7))

    def test_any_other_narrowing_reads_nothing(self):
        for account in (8, "none"):
            with self.subTest(account=account):
                self.assertEqual(self.scope.parent_q(account), Q(pk__in=[]))

    def test_the_one_account_is_the_whole_account_list(self):
        self.assertEqual(self.scope.accounts, {7: "EMEA"})
        self.assertEqual(self.scope.account_ref(7), {"id": 7, "name": "EMEA"})
        self.assertIsNone(self.scope.account_ref(None))

    def test_cursors_are_keyed_apart_from_an_organisation_s(self):
        self.assertEqual(self.scope.cursor_key, "account:7")


class ResolveAccountScopeTests(AccountStoryFixture):
    def test_an_account_the_viewer_may_open(self):
        self.assertEqual(self.account_scope().account, self.emea)

    def test_an_account_the_viewer_cannot_open_is_a_404_whether_or_not_it_exists(self):
        danas_co = self.customer("Dana's", owner=self.other)
        danas = self.account("Dana's div", customers=[danas_co], owner=self.other)
        globex = self.customer("Globex's", owner=None, organisation=self.other_org)
        stranger = self.account("Stranger div", customers=[globex])
        for account_id in (danas.pk, stranger.pk, 999_999):
            with self.subTest(account_id=account_id), self.assertRaises(Http404):
                resolve_account_scope(self.csm, account_id)

    def test_blind_to_one_account(self):
        viewer, seen, hidden = blind_to_one_account(self.pizza)
        self.assertEqual(resolve_account_scope(viewer, seen.pk).account, seen)
        with self.assertRaises(Http404):
            resolve_account_scope(viewer, hidden.pk)

    def test_an_account_shared_by_two_organisations_resolves_once(self):
        taco = self.customer("Taco Co")
        shared = self.account("Shared div", customers=[self.pizza, taco])
        for user in (self.csm, self.admin):
            with self.subTest(user=user.name):
                self.assertEqual(resolve_account_scope(user, shared.pk).account, shared)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.account_story --noinput`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.account_story.scope'` (the test package is importable once `services/account_story/__init__.py` and `tests/__init__.py` exist; create both empty files first).

- [ ] **Step 3: Implement**

`services/account_story/apps.py`:

```python
from django.apps import AppConfig


class AccountStoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "services.account_story"
    verbose_name = "Account page"
```

`services/account_story/scope.py`:

```python
"""Which account: the first half of the twice-filter on the account page.

The account must be one the viewer may open (`visible_accounts`, the rule
every per-account endpoint applies), or the response is a 404 that does not
confirm it exists. Its story reads only rows filed on it (`account_id` = this
account): never its organisations' own records, nor a sibling account's. The
second half, each record's own rule, is the organisation story's
`Source.base`, unchanged (`services.organizations.story.sources`).
"""

from dataclasses import dataclass

from django.db.models import Q
from django.shortcuts import get_object_or_404

from services.customers.models import Account
from services.customers.scoping import visible_accounts


@dataclass(frozen=True)
class AccountScope:
    """A `services.organizations.story.scope.StoryScope` over one account."""

    account: Account

    @property
    def accounts(self) -> dict[int, str]:
        return {self.account.pk: self.account.name}

    @property
    def cursor_key(self) -> str:
        # Not the bare id: an organisation's cursor must never read here.
        return f"account:{self.account.pk}"

    def parent_q(self, account=None) -> Q:
        """Rows filed on this account. `account` is the organisation story's
        narrowing; the account page's view drops it, and any value other
        than this account reads nothing."""
        if account is None or account == self.account.pk:
            return Q(account_id=self.account.pk)
        return Q(pk__in=[])

    def account_ref(self, account_id):
        if account_id is None:
            return None
        return {"id": account_id, "name": self.accounts[account_id]}


def resolve_account_scope(user, account_id) -> AccountScope:
    # SOC2:AUTH-02 the account must be one the viewer may open, else 404 whether or not it exists
    return AccountScope(account=get_object_or_404(visible_accounts(user), pk=account_id))
```

In `config/settings.py`, add the app after `"services.organizations",`:

```python
    "services.organizations",
    "services.account_story",
]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.account_story --noinput`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add services/account_story config/settings.py
git commit -m "feat(accounts): the account scope for the story, 404 unless the viewer may open it

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Needs attention for an account

**Files:**
- Create: `services/account_story/attention.py`, `services/account_story/tests/test_attention.py`
- Modify: `services/account_story/tests/fixtures.py` (adds `account_story`)

**Interfaces:**
- Consumes: `services.organizations.story.attention.urgent_tickets(tickets, today)`, `overdue_tasks(tasks, today)`, `RENEWAL_WINDOW_DAYS`; `build_story(..., attention=)` (Task 1); `AccountScope` (Task 2).
- Produces:
  - `services.account_story.attention.account_renewal(account, today) -> dict | None` returns `{"date", "days", "overdue"}`, or `None` when the account has no date or the date is more than 30 days out.
  - `services.account_story.attention.build_account_attention(user, scope, bases, account, *, today) -> dict` with keys `renewal`, `tickets`, `overdue_tasks`, `questions` (always `None`) and `anomaly` (always `None`).
  - `AccountStoryFixture.account_story(user=None, account=None, **query) -> dict`: `build_story` over `AccountScope` with `build_account_attention`.

- [ ] **Step 1: Write the failing tests**

Replace `services/account_story/tests/fixtures.py` with:

```python
"""The organisation story's fixture (Carl's Pizza Hut with the unowned EMEA
and APAC accounts, one maker per record kind), read through the account
page's scope."""

from services.account_story.attention import build_account_attention
from services.account_story.scope import resolve_account_scope
from services.organizations.story.build import build_story
from services.organizations.story.params import parse_story_params
from services.organizations.tests.story_fixtures import StoryFixture


class AccountStoryFixture(StoryFixture):
    def account_scope(self, user=None, account=None):
        return resolve_account_scope(user or self.csm, (account or self.emea).pk)

    def account_story(self, user=None, account=None, **query):
        user = user or self.csm
        return build_story(
            user,
            self.account_scope(user, account),
            parse_story_params(query),
            today=self.today,
            attention=build_account_attention,
        )
```

`services/account_story/tests/test_attention.py`:

```python
from datetime import date, timedelta
from types import SimpleNamespace

from django.test import SimpleTestCase

from services.account_story.attention import account_renewal
from services.customers.models import Account, Customer, Task, Ticket
from services.knowledge.models import Question
from services.organizations.story.attention import RENEWAL_WINDOW_DAYS

from .fixtures import AccountStoryFixture

TODAY = date(2026, 9, 30)


class AccountRenewalTests(SimpleTestCase):
    def renewal(self, days):
        due = None if days is None else TODAY + timedelta(days=days)
        return account_renewal(SimpleNamespace(renewal_date=due), TODAY)

    def test_no_date_is_nothing_to_renew(self):
        self.assertIsNone(self.renewal(None))

    def test_beyond_the_window_is_not_yet_attention(self):
        self.assertIsNone(self.renewal(RENEWAL_WINDOW_DAYS + 1))

    def test_overdue_or_due_within_thirty_days(self):
        for days, overdue in ((-5, True), (0, False), (30, False)):
            with self.subTest(days=days):
                self.assertEqual(
                    self.renewal(days),
                    {
                        "date": (TODAY + timedelta(days=days)).isoformat(),
                        "days": days,
                        "overdue": overdue,
                    },
                )


class AccountAttentionTests(AccountStoryFixture):
    def attention(self, user=None, account=None):
        return self.account_story(user, account)["attention"]

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

    def test_the_account_s_own_renewal_not_its_organisation_s(self):
        Customer.objects.filter(pk=self.pizza.pk).update(
            renewal_date=self.today - timedelta(days=3)
        )
        self.assertIsNone(self.attention()["renewal"])
        due = self.today + timedelta(days=12)
        Account.objects.filter(pk=self.emea.pk).update(renewal_date=due)
        self.assertEqual(
            self.attention()["renewal"], {"date": due.isoformat(), "days": 12, "overdue": False}
        )

    def test_open_high_and_critical_tickets_on_this_account_only(self):
        self.ticket(self.emea, number="T-1", priority=Ticket.Priority.HIGH, day=self.days_ago(4))
        self.ticket(
            self.emea, number="T-2", priority=Ticket.Priority.CRITICAL, day=self.days_ago(1)
        )
        self.ticket(self.emea, number="T-3", priority=Ticket.Priority.MEDIUM, day=self.days_ago(9))
        self.ticket(
            self.emea,
            number="T-4",
            priority=Ticket.Priority.CRITICAL,
            status=Ticket.Status.RESOLVED,
            day=self.days_ago(20),
        )
        self.ticket(
            self.pizza, number="T-5", priority=Ticket.Priority.CRITICAL, day=self.days_ago(30)
        )
        self.ticket(
            self.apac, number="T-6", priority=Ticket.Priority.CRITICAL, day=self.days_ago(30)
        )
        self.assertEqual(self.attention()["tickets"], {"count": 2, "oldest_days": 4})

    def test_a_ticket_outside_the_viewer_s_department_is_not_counted(self):
        self.ticket(
            self.emea,
            priority=Ticket.Priority.CRITICAL,
            department="engineering",
            day=self.days_ago(6),
        )
        self.assertIsNone(self.attention()["tickets"])
        self.assertEqual(self.attention(self.engineer)["tickets"], {"count": 1, "oldest_days": 6})

    def test_overdue_tasks_on_this_account_the_viewer_may_read(self):
        self.task(self.emea, due=self.days_ago(3))
        self.task(self.emea, due=self.days_ago(8), status=Task.Status.COMPLETED)
        self.task(self.emea, due=self.days_ago(10), created_by=self.other, assignee=self.other)
        self.task(self.emea, due=self.today)
        self.task(self.pizza, due=self.days_ago(5))
        self.assertEqual(self.attention()["overdue_tasks"], {"count": 1, "oldest_days": 3})

    def test_no_knowledge_questions_and_no_anomaly_on_an_account(self):
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.csm,
            assignee=self.engineer,
            text="Why?",
        )
        attention = self.attention()
        self.assertIsNone(attention["questions"])
        self.assertIsNone(attention["anomaly"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.account_story --noinput`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.account_story.attention'` (from both `fixtures.py` and `test_attention.py`).

- [ ] **Step 3: Implement**

`services/account_story/attention.py`:

```python
"""Needs attention on the account page (spec §2.5): the renewal overdue or
due within 30 days, open High/Critical tickets (count and oldest age), and
overdue tasks.

Tickets and tasks reuse the organisation story's aggregates over the story's
own base querysets, so the block reads exactly the rows the stream does
(`visible_tickets`, `visible_tasks`, this account only). Company knowledge is
per organisation and an anomaly is not an account's, so `questions` and
`anomaly` are always null; the keys stay so the block has the organisation
story's shape. Nothing here follows a filter or a search.
"""

from services.organizations.story.attention import (
    RENEWAL_WINDOW_DAYS,
    overdue_tasks,
    urgent_tickets,
)


def account_renewal(account, today):
    """Accounts have no churn, so only the date decides."""
    if account.renewal_date is None:
        return None
    days = (account.renewal_date - today).days
    if days > RENEWAL_WINDOW_DAYS:
        return None
    return {"date": account.renewal_date.isoformat(), "days": days, "overdue": days < 0}


def build_account_attention(user, scope, bases, account, *, today):
    """`build_attention`'s signature, so `build_story` takes either. `bases`
    are already this account's readable rows; `user` and `account` are not
    needed here."""
    return {
        "renewal": account_renewal(scope.account, today),
        "tickets": urgent_tickets(bases["ticket"], today),
        "overdue_tasks": overdue_tasks(bases["task"], today),
        "questions": None,
        "anomaly": None,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.account_story --noinput`
Expected: PASS (17 tests).

- [ ] **Step 5: Commit**

```bash
git add services/account_story/attention.py services/account_story/tests/fixtures.py \
  services/account_story/tests/test_attention.py
git commit -m "feat(accounts): needs attention for an account: renewal, urgent tickets, overdue tasks

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Account-keyed routes for the page's tabs and create flows

**Files:**
- Modify: `services/customers/scoping.py` (after `get_visible_account`, line 249)
- Modify: `services/customers/views.py`: the imports (line 49), `AccountDetailView.get_queryset` (lines 876-879), `AccountTaskListView` (1159-1175), `AccountNoteListView` (1309-1322), and the `get_account` methods of `AccountContactListView` (1497-1500), `AccountOpportunityListView` (1727-1730), `AccountRiskListView` (1851-1854) and `AccountSurveyListView` (1991-1994). Also `AccountFileListView._parent` (2876-2879) and `AccountCallListView._parent` (3056-3059)
- Create: `services/account_story/urls.py`, `services/customers/tests/test_account_routes.py`
- Modify: `config/urls.py` (above `path("api/v1/accounts/stats/", …)`, line 201), `docs/API_CONTRACTS.md`

**Interfaces:**
- Consumes: `get_visible_account(request, customer_id, account_id)`, `visible_accounts(user)`.
- Produces:
  - `services.customers.scoping.get_url_account(request, kwargs) -> Account`. With `customer_id` in `kwargs` it is `get_visible_account`. Without it, it is `get_object_or_404(visible_accounts(request.user), pk=kwargs["account_id"])`.
  - Routes under `/api/v1/accounts/`:
    - `<int:pk>/` (`AccountDetailView`, name `account-page-detail`).
    - `<int:account_id>/{contacts,opportunities,risks,files,calls,surveys,tasks,notes}/` (the nested `Account*ListView` classes, names `account-page-<tab>`).
  - `services/account_story/urls.py` `urlpatterns`. Task 5 appends the story route to it.

- [ ] **Step 1: Write the failing tests**

`services/customers/tests/test_account_routes.py`:

```python
"""The account page's per-account routes, keyed by the account alone
(`/api/v1/accounts/<id>/…`): the nested views' own querysets, record rules
and create paths, reached without an organisation id."""

from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers.models import (
    Attachment,
    Call,
    Contact,
    Customer,
    Note,
    Opportunity,
    Risk,
    Survey,
    Task,
)
from services.customers.tests.test_views import blind_to_one_account, create_account

TABS = ("contacts", "opportunities", "risks", "files", "calls", "surveys", "tasks", "notes")

#: A valid JSON create body per tab (files are multipart, tested on their own).
BODIES = {
    "contacts": {"name": "Sam Buyer", "email": "sam@pizza.example"},
    "opportunities": {"title": "Upsell SSO"},
    "risks": {"title": "Champion left"},
    "calls": {"title": "QBR", "occurred_at": "2026-09-29T10:00:00Z"},
    "surveys": {"survey_type": "nps", "sent_at": "2026-09-01"},
    "tasks": {"title": "Send the deck", "due_date": "2026-10-01", "priority": "high"},
    "notes": {"title": "Kickoff", "body": "Went well."},
}
MODELS = {
    "contacts": Contact,
    "opportunities": Opportunity,
    "risks": Risk,
    "calls": Call,
    "surveys": Survey,
    "tasks": Task,
    "notes": Note,
}


def flat(account_id, tab):
    return f"/api/v1/accounts/{account_id}/{tab}/"


class AccountRouteFixture(APITestCase):
    """Acme's Pizza Hut, owned by a colleague; the viewer owns its Seen
    account and cannot open its Hidden one (`blind_to_one_account`). Globex
    is another tenant."""

    def setUp(self):
        from services.copilot.anthropic_client import CopilotNotConfigured

        no_model = patch(
            "services.customers.classification.get_completion",
            side_effect=CopilotNotConfigured("no model in tests"),
        )
        no_model.start()
        self.addCleanup(no_model.stop)
        self.today = timezone.localdate()
        self.org = Organisation.objects.create(name="Acme")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="x",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.pizza = Customer.objects.create(organisation=self.org, name="Pizza Hut")
        self.viewer, self.seen, self.hidden = blind_to_one_account(self.pizza)
        self.colleague = self.pizza.owner
        globex = Customer.objects.create(
            organisation=Organisation.objects.create(name="Globex"), name="Globex"
        )
        self.stranger = create_account(globex, name="Stranger")

    def fill(self, account):
        """One record of every tab's kind on `account`."""
        Contact.objects.create(account=account, name="Sam Buyer", email="sam@pizza.example")
        Opportunity.objects.create(account=account, title="Upsell SSO")
        Risk.objects.create(account=account, title="Champion left")
        Attachment.objects.create(
            organisation=self.org,
            account=account,
            file=SimpleUploadedFile("a.txt", b"hello", content_type="text/plain"),
            name="a.txt",
            content_type="text/plain",
            size=5,
        )
        Call.objects.create(
            account=account, title="QBR", host_name="Viewer", occurred_at=timezone.now()
        )
        Survey.objects.create(
            account=account, survey_type=Survey.SurveyType.NPS, sent_at=self.today
        )
        Task.objects.create(
            account=account,
            title="Send the deck",
            assignee_name="Viewer",
            due_date=self.today,
            priority=Task.Priority.HIGH,
        )
        Note.objects.create(
            account=account,
            title="Kickoff",
            author_name="Viewer",
            body="Went well.",
            logged_at=self.today,
        )

    def get(self, user, path):
        self.client.force_authenticate(user)
        return self.client.get(path)


class AccountRouteReadTests(AccountRouteFixture):
    def test_each_tab_reads_what_its_nested_route_reads(self):
        self.fill(self.seen)
        for tab in TABS:
            with self.subTest(tab=tab):
                response = self.get(self.viewer, flat(self.seen.pk, tab))
                nested = self.get(
                    self.viewer,
                    f"/api/v1/customers/{self.pizza.pk}/accounts/{self.seen.pk}/{tab}/",
                )
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(len(response.data), 1)
                self.assertEqual(response.data, nested.data)

    def test_an_account_the_viewer_cannot_open_is_a_404_on_every_route(self):
        self.fill(self.hidden)
        for account_id in (self.hidden.pk, self.stranger.pk, 999_999):
            paths = [f"/api/v1/accounts/{account_id}/"] + [flat(account_id, t) for t in TABS]
            for path in paths:
                with self.subTest(path=path):
                    self.assertEqual(self.get(self.viewer, path).status_code, 404)

    def test_an_account_opens_even_when_none_of_its_organisations_does(self):
        taco = Customer.objects.create(organisation=self.org, name="Taco Co", owner=self.colleague)
        unowned = create_account(taco, name="Unowned")
        Note.objects.create(
            account=unowned, title="Hello", author_name="", body="x", logged_at=self.today
        )
        self.assertEqual(self.get(self.viewer, f"/api/v1/customers/{taco.pk}/").status_code, 404)
        response = self.get(self.viewer, flat(unowned.pk, "notes"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

    def test_record_rules_still_apply(self):
        Note.objects.create(
            account=self.seen,
            title="Theirs",
            author=self.colleague,
            author_name="Owner",
            body="x",
            logged_at=self.today,
        )
        Task.objects.create(
            account=self.seen,
            title="Theirs",
            assignee_name="Owner",
            created_by=self.colleague,
            assignee=self.colleague,
            due_date=self.today,
            priority=Task.Priority.LOW,
        )
        Opportunity.objects.create(account=self.seen, title="Eng only", department="engineering")
        for tab in ("notes", "tasks", "opportunities"):
            with self.subTest(tab=tab):
                self.assertEqual(self.get(self.viewer, flat(self.seen.pk, tab)).data, [])
        self.assertEqual(len(self.get(self.admin, flat(self.seen.pk, "opportunities")).data), 1)

    def test_the_detail_reads_and_edits_the_account(self):
        response = self.get(self.viewer, f"/api/v1/accounts/{self.seen.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["name"], "Seen")
        response = self.client.patch(
            f"/api/v1/accounts/{self.seen.pk}/", {"lifecycle_stage": "adoption"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.seen.refresh_from_db()
        self.assertEqual(self.seen.lifecycle_stage, "adoption")
        response = self.client.patch(
            f"/api/v1/accounts/{self.hidden.pk}/", {"lifecycle_stage": "adoption"}, format="json"
        )
        self.assertEqual(response.status_code, 404)
        self.hidden.refresh_from_db()
        self.assertEqual(self.hidden.lifecycle_stage, "onboarding")


class AccountRouteCreateTests(AccountRouteFixture):
    def test_each_create_lands_on_the_account(self):
        self.client.force_authenticate(self.viewer)
        for tab, body in BODIES.items():
            with self.subTest(tab=tab):
                response = self.client.post(flat(self.seen.pk, tab), body, format="json")
                self.assertEqual(response.status_code, 201, response.data)
                row = MODELS[tab].objects.get(pk=response.data["id"])
                self.assertEqual((row.account_id, row.customer_id), (self.seen.pk, None))

    def test_a_file_upload_lands_on_the_account(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.post(
            flat(self.seen.pk, "files"),
            {"file": SimpleUploadedFile("c.pdf", b"%PDF-1.4\n", content_type="application/pdf")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.data)
        row = Attachment.objects.get(pk=response.data["id"])
        self.assertEqual(
            (row.account_id, row.customer_id, row.organisation_id),
            (self.seen.pk, None, self.org.pk),
        )

    def test_a_ces_survey_is_refused_as_on_the_nested_route(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.post(
            flat(self.seen.pk, "surveys"),
            {"survey_type": "ces", "sent_at": "2026-09-01"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_nothing_is_created_on_an_account_the_viewer_cannot_open(self):
        self.client.force_authenticate(self.viewer)
        for tab, body in BODIES.items():
            with self.subTest(tab=tab):
                response = self.client.post(flat(self.hidden.pk, tab), body, format="json")
                self.assertEqual(response.status_code, 404)
                self.assertFalse(MODELS[tab].objects.filter(account=self.hidden).exists())


class AccountRouteQueryTests(AccountRouteFixture):
    def test_a_flat_route_costs_what_its_nested_route_costs(self):
        self.fill(self.seen)
        self.client.force_authenticate(self.viewer)
        for tab in TABS:
            with self.subTest(tab=tab):
                nested = f"/api/v1/customers/{self.pizza.pk}/accounts/{self.seen.pk}/{tab}/"
                self.client.get(nested)  # the viewer's memoised lookups, read once
                with CaptureQueriesContext(connection) as nested_ctx:
                    self.client.get(nested)
                with CaptureQueriesContext(connection) as flat_ctx:
                    self.client.get(flat(self.seen.pk, tab))
                self.assertEqual(len(flat_ctx), len(nested_ctx))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.customers.tests.test_account_routes --noinput`
Expected: FAIL: every flat request is a 404 (no route), so `test_each_tab_reads_what_its_nested_route_reads`, `test_an_account_opens_even_when_none_of_its_organisations_does`, the detail and create tests fail on status codes. (`test_an_account_the_viewer_cannot_open_is_a_404_on_every_route` and `test_nothing_is_created_on_an_account_the_viewer_cannot_open` pass for the wrong reason until the routes exist; they guard the 404 afterwards.)

- [ ] **Step 3: Implement**

In `services/customers/scoping.py`, after `get_visible_account`:

```python
def get_url_account(request, kwargs):
    """The account a per-account view reads, from either of its routes:
    nested (`/customers/<customer_id>/accounts/<account_id>/…`, pinned under
    that organisation by `get_visible_account`) or flat
    (`/accounts/<account_id>/…`, the account page's, which may have no
    organisation the viewer can open). Both 404 alike for an account the
    viewer cannot open, whether or not it exists."""
    if "customer_id" in kwargs:
        return get_visible_account(request, kwargs["customer_id"], kwargs["account_id"])
    # SOC2:AUTH-02 the flat route: the account must be one the viewer may open, else 404
    return get_object_or_404(visible_accounts(request.user), pk=kwargs["account_id"])
```

In `services/customers/views.py`, add `get_url_account` to the `from .scoping import (…)` list (alphabetical, after `get_visible_customer`). Then make these replacements.

`AccountDetailView.get_queryset`:

```python
    def get_queryset(self):
        accounts = visible_accounts(self.request.user)
        # Nested: pinned under the URL's organisation. Flat (the account
        # page's /accounts/<id>/): the account's own visibility is the rule.
        if "customer_id" in self.kwargs:
            accounts = accounts.filter(customers=self.kwargs["customer_id"])
        # SOC2:AUTH-02 either route reads only an account the viewer may open
        return with_pulse_inputs(accounts)
```

In `AccountTaskListView.get_queryset`, `AccountTaskListView.perform_create`, `AccountNoteListView.get_queryset` and `AccountNoteListView.perform_create`, replace

```python
account = get_visible_account(self.request, self.kwargs["customer_id"], self.kwargs["account_id"])
```

with

```python
        account = get_url_account(self.request, self.kwargs)
```

In `AccountContactListView.get_account`, `AccountOpportunityListView.get_account`, `AccountRiskListView.get_account` and `AccountSurveyListView.get_account`, replace

```python
return get_visible_account(self.request, self.kwargs["customer_id"], self.kwargs["account_id"])
```

with

```python
        return get_url_account(self.request, self.kwargs)
```

In `AccountFileListView._parent` and `AccountCallListView._parent`, replace

```python
        return None, get_visible_account(
            self.request, self.kwargs["customer_id"], self.kwargs["account_id"]
        )
```

with

```python
        return None, get_url_account(self.request, self.kwargs)
```

Add one line to the docstring of each of those nine classes, after the first sentence: "Also mounted flat at `/api/v1/accounts/<id>/…` for the account page (`get_url_account`)." Leave `AccountCanvasListView`, `AccountHeadlineListCreateView`, `AccountActivityListView`, `AccountEmailListView`, `AccountTicketListView` and `AccountCalendarEventListView` on `get_visible_account`: they have no flat route (Decision 6).

`services/account_story/urls.py`:

```python
"""The account page's routes (`/accounts/:id`), mounted at /api/v1/accounts/.

The per-account tabs are the nested /customers/<cid>/accounts/<id>/… view
classes, reached by the account alone (`scoping.get_url_account`): the viewer
may open an account but none of its organisations. None of these routes is
"" or "stats/", so /accounts/ and /accounts/stats/ still resolve."""

from django.urls import path

from services.customers import views as customers

urlpatterns = [
    path("<int:pk>/", customers.AccountDetailView.as_view(), name="account-page-detail"),
    path(
        "<int:account_id>/contacts/",
        customers.AccountContactListView.as_view(),
        name="account-page-contacts",
    ),
    path(
        "<int:account_id>/opportunities/",
        customers.AccountOpportunityListView.as_view(),
        name="account-page-opportunities",
    ),
    path(
        "<int:account_id>/risks/",
        customers.AccountRiskListView.as_view(),
        name="account-page-risks",
    ),
    path(
        "<int:account_id>/files/",
        customers.AccountFileListView.as_view(),
        name="account-page-files",
    ),
    path(
        "<int:account_id>/calls/",
        customers.AccountCallListView.as_view(),
        name="account-page-calls",
    ),
    path(
        "<int:account_id>/surveys/",
        customers.AccountSurveyListView.as_view(),
        name="account-page-surveys",
    ),
    path(
        "<int:account_id>/tasks/",
        customers.AccountTaskListView.as_view(),
        name="account-page-tasks",
    ),
    path(
        "<int:account_id>/notes/",
        customers.AccountNoteListView.as_view(),
        name="account-page-notes",
    ),
]
```

In `config/urls.py`, directly above `path("api/v1/accounts/stats/", …)`:

```python
# The account page's own routes (/accounts/:id): its story and its tabs,
# keyed by the account alone — see services/account_story/urls.py. None
# of its routes is "" or "stats/", so both exact paths below still resolve.
(path("api/v1/accounts/", include("services.account_story.urls")),)
```

In `docs/API_CONTRACTS.md`, insert a new section directly above `## Files — the Files tab on organisations and accounts`:

```markdown
## `account_story` — the account page (`/accounts/:id`)

The account page's own endpoints, in `services/account_story/`. No model.

### `GET/PATCH /api/v1/accounts/<id>/`, `GET/POST /api/v1/accounts/<id>/{contacts,opportunities,risks,files,calls,surveys,tasks,notes}/`

The account page's tabs and create flows, keyed by the account alone. Each is the nested
`/customers/<cid>/accounts/<id>/…` endpoint of the same name (same view class, same request and response
shapes, same record rules, same audit records), documented in its own section; only the path differs. They exist
because an account can be open to a viewer while none of its organisations is, so the page may have no
organisation id to put in the nested path.

Auth: `IsAuthenticated` — `401` unauthenticated. The account must be in `visible_accounts(user)`, otherwise `404`
(another tenant's account, one the viewer cannot open and an id that does not exist read the same). Then each list
applies its record's own rule: contacts and files by the account; opportunities and risks by department
(`pipeline_visible_q`); tasks by creator, assignee and their chains; notes by author and chain. A create is always
on this account (`account_id` set, `customer_id` null); the parent never comes from the body. A CES survey is a
`400`, as on the nested route. `PATCH /accounts/<id>/` is the nested `AccountDetailView` PATCH (the owner-change
rule and handover included). The nested routes are unchanged. Activities, emails, tickets and calendar events
have no flat route: the page reads them through `GET /accounts/<id>/story/`.
```

- [ ] **Step 4: Run the tests to verify they pass, and the customers suite**

Run: `venv/bin/python manage.py test services.customers --parallel --noinput`
Expected: PASS (the new module's 10 tests and every existing nested-route test).

- [ ] **Step 5: Commit**

```bash
git add services/customers/scoping.py services/customers/views.py services/account_story/urls.py \
  services/customers/tests/test_account_routes.py config/urls.py docs/API_CONTRACTS.md
git commit -m "feat(accounts): account-keyed routes for the account page's tabs and creates

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `GET /api/v1/accounts/<id>/story/`

**Files:**
- Create: `services/account_story/views.py`, `services/account_story/tests/test_views.py`
- Modify: `services/account_story/urls.py` (story route), `docs/API_CONTRACTS.md`, `docs/product/01-prd.md`, `docs/product/02-trd.md`, `docs/product/05-backend-schema.md`

**Interfaces:**
- Consumes: `resolve_account_scope(user, account_id)` (Task 2), `build_account_attention` (Task 3), `build_story(..., attention=)` (Task 1), `parse_story_params(query)`.
- Produces: `services.account_story.views.AccountStoryView` (GET), route `<int:pk>/story/` named `account-page-story`. The response is `{"items", "next_cursor", "counts", "attention"}` in the organisation story's shapes.

- [ ] **Step 1: Write the failing tests**

`services/account_story/tests/test_views.py`:

```python
from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APIClient

from services.accounts.models import User
from services.customers.models import Ticket
from services.customers.tests.test_views import blind_to_one_account

from .fixtures import AccountStoryFixture


def story_url(account_id):
    return f"/api/v1/accounts/{account_id}/story/"


class AccountStoryEndpointFixture(AccountStoryFixture):
    @staticmethod
    def client_for(user):
        api = APIClient()
        api.force_authenticate(user)
        return api

    def get(self, user=None, account=None, **query):
        api = self.client_for(user or self.csm)
        response = api.get(story_url((account or self.emea).pk), query)
        self.assertEqual(response.status_code, 200, response.content)
        return response.data

    def ids(self, user=None, account=None, **query):
        return set(self.keys(self.get(user, account, **query)))


class AccountStoryEndpointTests(AccountStoryEndpointFixture):
    def test_requires_authentication(self):
        self.assertEqual(APIClient().get(story_url(self.emea.pk)).status_code, 401)

    def test_post_is_not_allowed(self):
        api = self.client_for(self.csm)
        self.assertEqual(api.post(story_url(self.emea.pk), {}).status_code, 405)

    def test_an_account_the_viewer_cannot_open_is_a_404_whether_or_not_it_exists(self):
        danas_co = self.customer("Dana's", owner=self.other)
        danas = self.account("Dana's div", customers=[danas_co], owner=self.other)
        globex = self.customer("Globex's", owner=None, organisation=self.other_org)
        stranger = self.account("Stranger div", customers=[globex])
        self.note(danas)
        self.note(stranger)
        api = self.client_for(self.csm)
        for account_id in (danas.pk, stranger.pk, 999_999):
            with self.subTest(account_id=account_id):
                self.assertEqual(api.get(story_url(account_id)).status_code, 404)

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
        self.assertEqual(set(body["counts"]), {"by_group", "by_kind", "by_account"})
        self.assertEqual(set(body["counts"]["by_account"]), {"all", "none", str(self.emea.pk)})
        self.assertEqual(
            set(body["attention"]),
            {"renewal", "tickets", "overdue_tasks", "questions", "anomaly"},
        )
        self.assertIsNone(body["next_cursor"])

    def test_only_records_filed_on_this_account(self):
        taco = self.customer("Taco Co")
        shared = self.account("Shared div", customers=[self.pizza, taco])
        self.note(self.pizza)
        self.note(self.apac)
        self.note(taco)
        on_emea = self.note(self.emea)
        on_shared = self.note(shared)
        self.snapshot(self.pizza, self.days_ago(70), "8.0")
        self.snapshot(self.pizza, self.days_ago(40), "3.0")
        self.snapshot(self.emea, self.days_ago(70), "8.0")
        fell = self.snapshot(self.emea, self.days_ago(40), "3.0")
        body = self.get()
        self.assertEqual(self.keys(body), [("note", on_emea.pk), ("health", fell.pk)])
        self.assertEqual(body["items"][0]["account"], {"id": self.emea.pk, "name": "EMEA"})
        self.assertEqual(body["counts"]["by_account"], {"all": 2, "none": 0, str(self.emea.pk): 2})
        self.assertEqual(self.keys(self.get(account=shared)), [("note", on_shared.pk)])

    def test_the_account_parameter_is_ignored(self):
        on_emea = self.note(self.emea)
        self.note(self.apac)
        for value in (str(self.apac.pk), "none", "abc"):
            with self.subTest(account=value):
                api = self.client_for(self.csm)
                body = api.get(story_url(self.emea.pk), {"account": value}).data
                self.assertEqual(self.keys(body), [("note", on_emea.pk)])
                self.assertEqual(body["counts"]["by_group"]["all"], 1)

    def test_unknown_values_are_ignored_not_rejected(self):
        self.note(self.emea)
        body = self.get(group="mood", source="slack", limit="lots", cursor="%%%")
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["counts"]["by_group"]["all"], 1)

    def test_group_source_search_thread_and_paging(self):
        now = timezone.now()
        first = self.email(self.emea, thread_id="t-1", at=now - timedelta(minutes=5))
        self.email(self.emea, subject="Other", body="Hello", thread_id="t-2", at=now)
        note = self.note(self.emea, title="SSO blocker", body="SSO is blocking the rollout")
        self.assertEqual(self.ids(group="tasks"), {("note", note.pk)})
        self.assertEqual(self.ids(source="note"), {("note", note.pk)})
        self.assertEqual(self.ids(q="sso"), {("note", note.pk)})
        self.assertEqual(self.ids(thread="t-1"), {("email", first.pk)})
        everything = self.keys(self.get())
        seen, cursor = [], None
        while True:
            body = self.get(limit="1", **({"cursor": cursor} if cursor else {}))
            seen += self.keys(body)
            cursor = body["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(seen, everything)
        self.assertEqual(len(seen), 3)

    def test_a_cursor_from_the_organisation_story_reads_the_first_page(self):
        for _ in range(3):
            self.note(self.emea)
        api = self.client_for(self.csm)
        org_cursor = api.get(f"/api/v1/organizations/{self.pizza.pk}/story/", {"limit": "1"}).data[
            "next_cursor"
        ]
        self.assertIsNotNone(org_cursor)
        first = self.get(limit="1")
        self.assertEqual(self.keys(self.get(limit="1", cursor=org_cursor)), self.keys(first))


class AccountStoryPrivacyTests(AccountStoryEndpointFixture):
    def test_records_the_viewer_may_not_read_reach_no_item_count_attention_or_search(self):
        self.email(self.emea, mailbox_owner=self.other, subject="Secret pricing", body="Secret")
        self.note(self.emea, author=self.other, title="Secret note", body="Secret pricing")
        self.ticket(
            self.emea,
            department="engineering",
            title="Secret pricing bug",
            priority=Ticket.Priority.CRITICAL,
        )
        self.task(
            self.emea,
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

    def test_mail_is_its_mailbox_owner_s_and_their_chain_s(self):
        User.objects.filter(pk=self.other.pk).update(reports_to=self.csm)
        peer = User.objects.create_user(
            email="pat@acme.io",
            password="supersecret1",
            name="Pat CSM",
            organisation=self.org,
            role=User.Role.CSM,
        )
        now = timezone.now()
        mine = self.email(self.emea, mailbox_owner=self.csm, at=now - timedelta(minutes=1))
        report_s = self.email(self.emea, mailbox_owner=self.other, at=now - timedelta(minutes=2))
        self.email(self.emea, mailbox_owner=peer, at=now - timedelta(minutes=3))
        unowned = self.email(self.emea, at=now - timedelta(minutes=4))
        carl = User.objects.get(pk=self.csm.pk)
        dana = User.objects.get(pk=self.other.pk)
        self.assertEqual(
            self.ids(carl),
            {("email", mine.pk), ("email", report_s.pk), ("email", unowned.pk)},
        )
        self.assertEqual(self.ids(dana), {("email", report_s.pk), ("email", unowned.pk)})

    def test_tickets_are_read_by_department(self):
        cs = self.ticket(self.emea, number="T-1", department="cs")
        eng = self.ticket(self.emea, number="T-2", department="engineering")
        anyone = self.ticket(self.emea, number="T-3")
        self.assertEqual(self.ids(), {("ticket", cs.pk), ("ticket", anyone.pk)})
        self.assertEqual(self.ids(self.engineer), {("ticket", eng.pk), ("ticket", anyone.pk)})
        self.assertEqual(
            self.ids(self.admin), {("ticket", cs.pk), ("ticket", eng.pk), ("ticket", anyone.pk)}
        )

    def test_notes_and_tasks_follow_their_personal_rules(self):
        mine_note = self.note(self.emea, author=self.csm)
        self.note(self.emea, author=self.other)
        mine_task = self.task(self.emea, created_by=self.csm, assignee=self.csm)
        assigned = self.task(self.emea, created_by=self.other, assignee=self.csm)
        self.task(self.emea, created_by=self.other, assignee=self.other)
        self.assertEqual(
            self.ids(),
            {("note", mine_note.pk), ("task", mine_task.pk), ("task", assigned.pk)},
        )
        # Alice sees every account, but is in neither person's chain.
        self.assertEqual(self.ids(self.admin), set())

    def test_blind_to_one_account(self):
        viewer, seen, hidden = blind_to_one_account(self.pizza)
        on_seen = self.note(seen)
        self.note(hidden)
        self.ticket(hidden, priority=Ticket.Priority.CRITICAL)
        body = self.get(viewer, seen)
        self.assertEqual(self.keys(body), [("note", on_seen.pk)])
        self.assertEqual(body["counts"]["by_group"]["all"], 1)
        self.assertIsNone(body["attention"]["tickets"])
        self.assertEqual(self.client_for(viewer).get(story_url(hidden.pk)).status_code, 404)

    def test_another_tenant_s_account_is_a_404_even_for_an_admin(self):
        globex = self.customer("Globex's", owner=None, organisation=self.other_org)
        stranger = self.account("Stranger div", customers=[globex])
        self.note(stranger)
        for user in (self.csm, self.admin):
            with self.subTest(user=user.name):
                response = self.client_for(user).get(story_url(stranger.pk))
                self.assertEqual(response.status_code, 404)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.account_story.tests.test_views --noinput`
Expected: FAIL. Every `self.get(...)` gets a 404 where 200 is asserted (no story route). The 401, 405 and 404 tests also fail or pass for the wrong reason until the route exists.

- [ ] **Step 3: Implement**

`services/account_story/views.py`:

```python
from dataclasses import replace

from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from services.organizations.story.build import build_story
from services.organizations.story.params import parse_story_params

from .attention import build_account_attention
from .scope import resolve_account_scope


class AccountStoryView(APIView):
    """GET /api/v1/accounts/<id>/story/ — the account page's Story: every
    record filed on this account, newest first across all sources under one
    keyset cursor, filtered by group, source, search and email thread, with
    counts over the whole filtered set and the account's Needs attention
    block. Twice filtered: the account must be visible (404 otherwise,
    before anything is read), then each record is read under its own rule.
    `account` is ignored: one account has no account chips. Unknown
    parameter values are ignored. See docs/API_CONTRACTS.md."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        scope = resolve_account_scope(request.user, pk)
        params = replace(parse_story_params(request.query_params), account=None)
        return Response(
            build_story(
                request.user,
                scope,
                params,
                today=timezone.localdate(),
                attention=build_account_attention,
            )
        )
```

In `services/account_story/urls.py`, add `from . import views` below the `customers` import, and append to `urlpatterns`:

```python
(path("<int:pk>/story/", views.AccountStoryView.as_view(), name="account-page-story"),)
```

In `docs/API_CONTRACTS.md`, add a feature-table row directly below the "Organizations portfolio" row:

```markdown
| Account page (`/accounts/:id` redesign) | `account_story` | ✅ Built — the account's Story is `GET /accounts/<id>/story/`; its tabs and creates are `GET/POST /accounts/<id>/{contacts,opportunities,risks,files,calls,surveys,tasks,notes}/` and `GET/PATCH /accounts/<id>/` (the nested account endpoints, keyed by the account alone) |
```

In the `account_story` section Task 4 added, append after the routes subsection:

````markdown
### `GET /api/v1/accounts/<id>/story/`

The account page's Story tab (spec: react-ts-app `docs/superpowers/specs/2026-09-29-accounts-redesign-design.md`
§2.5): every record filed on this account, newest first under one cursor across all sources, with counts and the
Needs attention block. The organisation story's engine (`GET /organizations/<id>/story/`, `services/organizations/story/`)
over one account (`services/account_story/scope.py`); items, counts, params, order, horizon, cursor rules and the
record rules are exactly that endpoint's, except as listed here.

Auth: `IsAuthenticated` — `401` unauthenticated. GET only. The account must be in `visible_accounts(user)`,
otherwise `404`, whether or not it exists (another tenant's, one the viewer cannot open, an unknown id). Unknown
parameter values are ignored, never a 400.

Params: `group`, `source`, `q`, `thread`, `cursor`, `limit`, as on the organisation story. `account` is ignored
(one account has no account chips); any value reads the whole account.

```json
{
  "items": [{
    "id": 41, "kind": "call", "source": "zoom",
    "occurred_at": "2026-09-29T14:05:00+00:00", "all_day": false,
    "account": {"id": 9, "name": "EMEA"},
    "title": "Quarterly review", "summary": "They want SSO before the renewal.",
    "actor": {"id": null, "name": "Carl CSM"},
    "link": {"thread_id": null, "url": "https://zoom.us/rec/1"}
  }],
  "next_cursor": null,
  "counts": {
    "by_group": {"all": 7, "conversations": 3, "tickets": 2, "tasks": 1, "feedback": 1, "health": 0},
    "by_kind": {"activity": 0, "calendar_event": 1, "call": 1, "email": 1, "health": 0, "note": 1,
                "survey": 1, "task": 0, "ticket": 2},
    "by_account": {"all": 7, "none": 0, "9": 7}
  },
  "attention": {
    "renewal": {"date": "2026-10-12", "days": 12, "overdue": false},
    "tickets": {"count": 2, "oldest_days": 9},
    "overdue_tasks": null,
    "questions": null,
    "anomaly": null
  }
}
```

- **Scope.** Only records with `account_id` = this account: never its organisations' own records nor another
  account's. A shared account's records are the same on each of its organisations' stories and here. Every item's
  `account` is this account. Health items are this account's own month-end `HealthSnapshot` changes.
- **Privacy (twice filtered).** After the account, each record is read under its own rule: mail by its mailbox
  owner and their chain (`visible_emails`), notes by author and chain (`visible_notes`), tasks by creator, assignee
  and the chains above them (`visible_tasks`), tickets by department (`visible_tickets`); activities, calls,
  meetings, surveys and health readings by the account's rule alone. Items, counts, attention and search all read
  the same filtered rows.
- **Counts.** As on the organisation story; `by_account` is always `all`, `none` (`0`) and this account's id.
- **Cursor.** Keyed by the account (`account:<id>`) and the filters: a cursor from an organisation's story, from
  another account, or cut under other filters reads the first page.
- **Attention** follows no filter. `renewal`: the account's `renewal_date` overdue or within 30 days (accounts have
  no churn). `tickets`: open High/Critical on this account the viewer may read, count and oldest age in days.
  `overdue_tasks`: not completed, due before today, readable. `questions` and `anomaly` are always `null`: company
  knowledge is per organisation. Each entry is `null` when nothing needs attention.
- 24 constant queries per request, whatever the account's size (pinned by `AccountStoryQueryCountTests`).
````

In `docs/product/01-prd.md`, add a feature row directly below the "Organisation page lists (detail redesign, delivery 2)" row:

```markdown
| Account page Story and tabs (accounts redesign, delivery 2) | Built (backend) | `GET /accounts/<id>/story/` is the organisation story over one account: only records filed on it, the same filters, counts and cursor, each record read under its own rule. Needs attention is the account's renewal, urgent tickets and overdue tasks. The page's tabs and creates use `/accounts/<id>/…`, keyed by the account alone, so an account opens even when none of its organisations does |
```

and a release-history row at the end of the §9 table:

```markdown
| 2026-09-30 | Account page (backend): `GET /accounts/<id>/story/` and the account-keyed tab routes |
```

In `docs/product/02-trd.md` §2.4, add a row after the `services/copilot/dashboard_grounding.py, …` row:

```markdown
| `services/account_story/` | The account page: `AccountScope` (one account, `visible_accounts`) and its Needs attention over the organisation story's engine (`services/organizations/story/`), and the flat `/accounts/<id>/…` routes that mount the nested account views (`scoping.get_url_account`) |
```

In `docs/product/05-backend-schema.md`, after the `### organizations` block's last paragraph (before its `---`), add:

```markdown
### `account_story`

No model. The account page's Story reads the organisation story's sources (`Activity`, `Call`, `Email`,
`CalendarEvent`, `Ticket`, `Task`, `Note`, `Survey`, `HealthSnapshot`) filed on one visible account, each under
its own record rule, and `Account.renewal_date` for Needs attention.
```

and in §11 "Visibility rules", a row after "Organisation roll-ups":

```markdown
| The account page (`/accounts/<id>/story/` and `/accounts/<id>/…`) | The account in `visible_accounts`, else 404 whether or not it exists; then each record's own rule (rows below) | `services/account_story/scope.py`, `scoping.get_url_account` |
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.account_story --noinput`
Expected: PASS (17 earlier + 15 new).

- [ ] **Step 5: Commit**

```bash
git add services/account_story/views.py services/account_story/urls.py \
  services/account_story/tests/test_views.py docs/API_CONTRACTS.md docs/product/01-prd.md \
  docs/product/02-trd.md docs/product/05-backend-schema.md
git commit -m "feat(accounts): GET /accounts/<id>/story/, the account page's story

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: The story matches the per-type endpoints, at a pinned query count

**Files:**
- Create: `services/account_story/tests/test_story_queries.py`

**Interfaces:**
- Consumes: the story route (Task 5); the nested per-type routes `/customers/<cid>/accounts/<id>/<path>/`; `PER_TYPE` from `services.organizations.tests.test_story_views` (a plain dict of kind to path).
- Produces: nothing new. These tests are the gate that the story reads exactly what the per-type endpoints show the same viewer, and that its cost does not grow with the account.

- [ ] **Step 1: Write the tests**

`services/account_story/tests/test_story_queries.py`:

```python
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from services.accounts.models import User
from services.customers.models import Task, Ticket
from services.organizations.tests.test_story_views import PER_TYPE

from .fixtures import AccountStoryFixture


def story_url(account_id):
    return f"/api/v1/accounts/{account_id}/story/"


def client_for(user):
    api = APIClient()
    api.force_authenticate(user)
    return api


class AccountStoryMatchesPerTypeEndpointsTests(AccountStoryFixture):
    def per_type(self, user, account):
        api = client_for(user)
        found = set()
        for kind, path in PER_TYPE.items():
            url = f"/api/v1/customers/{self.pizza.pk}/accounts/{account.pk}/{path}/"
            response = api.get(url)
            self.assertEqual(response.status_code, 200, url)
            found |= {(kind, row["id"]) for row in response.data}
        return found

    def story_keys(self, user, account):
        api = client_for(user)
        seen, cursor = [], None
        while True:
            query = {"limit": "3", **({"cursor": cursor} if cursor else {})}
            body = api.get(story_url(account.pk), query).data
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
        self.email(self.emea, mailbox_owner=self.csm)
        self.note(self.emea, author=self.other)
        self.task(self.apac, created_by=self.other, assignee=self.other)
        self.ticket(self.emea, department="engineering")
        for user in (self.csm, self.admin):
            for account in (self.emea, self.apac):
                with self.subTest(user=user.name, account=account.name):
                    expected = self.per_type(user, account)
                    self.assertEqual(self.story_keys(user, account), expected)
                    self.assertGreaterEqual(len(expected), 8)


class AccountStoryQueryCountTests(AccountStoryFixture):
    """The page, the counts and the attention block cost a fixed number of
    queries. A per-row query anywhere would make the count grow with the
    account, which the two-size test catches.

    Twenty-four, for an admin or a CSM, with a fresh user per request as a
    real request loads one:
      1. `user.organisation` (`visible_accounts`' own
         `customers__organisation=user.organisation`), a real FK fetch
      2. the caller's membership (`sees_everything`, memoised on the user)
      3. `user.role` (`capabilities_for` reads the column)
      4. the account (`resolve_account_scope`, get_object_or_404)
      5. the org chart below the caller (`subtree_ids`), walked once and
         memoised: `visible_accounts` (a CSM) and the mail, note and task
         rules all reuse it
      6. the health snapshots
      7-14. one page query per record source (activity, calendar_event,
            call, email, note, survey, task, ticket), related rows joined
      15-22. one count aggregate per record source
      23. attention: urgent tickets (one aggregate)
      24. attention: overdue tasks (one aggregate)
    The organisation story's 28 minus its organisation and accounts lookups
    (2) plus this account (1), minus open questions (2) and the anomaly (1).

    If the pinned number is ever wrong, print `[q["sql"] for q in
    ctx.captured_queries]`: every query must be one of these kinds; an extra
    one is a bug to fix, not a number to bump."""

    EXPECTED = 24

    def setUp(self):
        super().setUp()
        self.next_day = 30

    def book(self, size):
        for _ in range(size):
            for kind in PER_TYPE:
                self.make(kind, self.emea)
            self.snapshot(self.emea, self.days_ago(self.next_day + 1), "8.0")
            self.snapshot(self.emea, self.days_ago(self.next_day), "3.0")
            self.next_day += 2
        # Content for every computed entry of `attention`: the aggregates run
        # whether or not a row matches, so this must not move the count.
        self.ticket(self.emea, priority=Ticket.Priority.HIGH, day=self.days_ago(2))
        self.task(self.emea, due=self.days_ago(2), status=Task.Status.PENDING)

    def count(self, user=None, **query):
        api = client_for(User.objects.get(pk=(user or self.admin).pk))
        with CaptureQueriesContext(connection) as ctx:
            response = api.get(story_url(self.emea.pk), query)
        self.assertEqual(response.status_code, 200)
        return len(ctx.captured_queries)

    def test_the_count_is_pinned_and_does_not_grow_with_the_account(self):
        self.book(3)
        small = self.count()
        self.book(12)
        self.assertEqual(self.count(), small)
        self.assertEqual(small, self.EXPECTED)

    def test_a_csm_s_count_does_not_grow_either(self):
        self.book(3)
        small = self.count(self.csm)
        self.book(12)
        self.assertEqual(self.count(self.csm), small)
        self.assertEqual(small, self.EXPECTED)

    def test_the_next_page_costs_the_same(self):
        self.book(5)
        cursor = (
            client_for(self.admin).get(story_url(self.emea.pk), {"limit": "5"}).data["next_cursor"]
        )
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

`Task.Status.PENDING` is the model's `"pending"` choice (`Task.Status`: pending, in-progress, completed).

- [ ] **Step 2: Run the tests**

Run: `venv/bin/python manage.py test services.account_story.tests.test_story_queries --noinput`
Expected: PASS. This task adds guards over Task 5's code, so there is no red step. If a test fails, the code is wrong, not the test. A parity failure means a record rule differs between the story and a per-type endpoint: fix the source rule, never the expected set. A count other than 24 means a query was added: print `ctx.captured_queries` and remove it. Do not bump `EXPECTED` unless the enumeration in the docstring is updated with the new query's reason and it is constant.

- [ ] **Step 3: Commit**

```bash
git add services/account_story/tests/test_story_queries.py
git commit -m "test(accounts): the account story matches its per-type endpoints at a pinned query count

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: End-to-end: create from the account page, read its story

**Files:**
- Create: `e2e/test_account_story_flow.py`

**Interfaces:**
- Consumes: `e2e.http.http_get(url, token=None)` and `http_post(url, payload, token=None)`, both returning `(status, body)`. Also `/auth/signup/`, `/auth/users/`, `/auth/login/`, `/customers/`, `/customers/<cid>/accounts/`, and the flat routes and story (Tasks 4 and 5).
- Produces: nothing.

- [ ] **Step 1: Write the test**

`e2e/test_account_story_flow.py`:

```python
"""End-to-end tier: real server, real HTTP, real test DB. A CSM builds an
organisation with an account, then adds a task, a note and a survey from the
account page, keyed by the account alone, and a note on the organisation
itself. The account's story holds the three account records only, filtered,
searched and paged, with the account's Needs attention. A peer cannot open
the account; the admin can, but never sees the CSM's personal note or task."""

from datetime import timedelta
from urllib.parse import urlencode

from django.test import LiveServerTestCase
from django.utils import timezone

from e2e.http import http_get, http_post


class AccountStoryFlowTests(LiveServerTestCase):
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

    def story(self, account_id, token, **query):
        suffix = f"?{urlencode(query)}" if query else ""
        return http_get(self.api(f"/accounts/{account_id}/story/{suffix}"), token=token)

    def kinds(self, account_id, token, **query):
        status, body = self.story(account_id, token, **query)
        self.assertEqual(status, 200, body)
        return [item["kind"] for item in body["items"]]

    def created(self, path, payload, token):
        status, body = http_post(self.api(path), payload, token=token)
        self.assertEqual(status, 201, body)
        return body

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

        # 2. Carl's organisation and its EMEA account, renewing in ten days.
        pizza = self.created("/customers/", {"name": "Pizza Hut"}, carl)["id"]
        renewal = (today + timedelta(days=10)).isoformat()
        emea = self.created(
            f"/customers/{pizza}/accounts/", {"name": "EMEA", "renewal_date": renewal}, carl
        )["id"]

        # 3. From the account page (no organisation id): an overdue task, a
        #    note and a survey. And a note on the organisation itself.
        self.created(
            f"/accounts/{emea}/tasks/",
            {
                "title": "Send the QBR deck",
                "due_date": (today - timedelta(days=1)).isoformat(),
                "priority": "high",
            },
            carl,
        )
        self.created(
            f"/accounts/{emea}/notes/",
            {"title": "Champion left", "body": "Sam moved on to Globex."},
            carl,
        )
        self.created(
            f"/accounts/{emea}/surveys/", {"survey_type": "nps", "sent_at": today.isoformat()}, carl
        )
        self.created(
            f"/customers/{pizza}/notes/", {"title": "Org plan", "body": "For everyone."}, carl
        )

        # 4. The account's story: the task (a timestamp) first, then today's
        #    survey and note (dates, ordered by kind). The organisation's own
        #    note is not the account's story.
        status, body = self.story(emea, carl)
        self.assertEqual(status, 200, body)
        self.assertEqual([item["kind"] for item in body["items"]], ["task", "survey", "note"])
        self.assertEqual(body["items"][0]["account"], {"id": emea, "name": "EMEA"})
        self.assertEqual(body["counts"]["by_account"], {"all": 3, "none": 0, str(emea): 3})
        self.assertEqual(
            body["counts"]["by_group"],
            {"all": 3, "conversations": 0, "tickets": 0, "tasks": 2, "feedback": 1, "health": 0},
        )
        self.assertEqual(
            body["attention"],
            {
                "renewal": {"date": renewal, "days": 10, "overdue": False},
                "tickets": None,
                "overdue_tasks": {"count": 1, "oldest_days": 1},
                "questions": None,
                "anomaly": None,
            },
        )

        # 5. A filter, a search, an ignored account chip, and paging.
        self.assertEqual(self.kinds(emea, carl, group="tasks"), ["task", "note"])
        self.assertEqual(self.kinds(emea, carl, q="globex"), ["note"])
        self.assertEqual(self.kinds(emea, carl, account="none"), ["task", "survey", "note"])
        seen, cursor = [], None
        while True:
            query = {"limit": 1, **({"cursor": cursor} if cursor else {})}
            status, body = self.story(emea, carl, **query)
            self.assertEqual(status, 200, body)
            seen += [item["kind"] for item in body["items"]]
            cursor = body["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(seen, ["task", "survey", "note"])

        # 6. The tabs read back through the same account-keyed routes.
        status, body = http_get(self.api(f"/accounts/{emea}/tasks/"), token=carl)
        self.assertEqual(status, 200, body)
        self.assertEqual([task["title"] for task in body], ["Send the QBR deck"])

        # 7. Dana cannot open Carl's account, its story or its tabs. The admin
        #    can, but Carl's note and task are his (and his chain's) alone.
        status, _body = self.story(emea, dana)
        self.assertEqual(status, 404)
        status, _body = http_get(self.api(f"/accounts/{emea}/notes/"), token=dana)
        self.assertEqual(status, 404)
        status, body = self.story(emea, admin)
        self.assertEqual(status, 200, body)
        self.assertEqual([item["kind"] for item in body["items"]], ["survey"])
        self.assertIsNone(body["attention"]["overdue_tasks"])
```

- [ ] **Step 2: Run the test**

Run: `venv/bin/python manage.py test e2e.test_account_story_flow --noinput`
Expected: PASS. This is a guard over Tasks 4–5; there is no red step. A failure is a real bug: investigate with `superpowers:systematic-debugging`, do not loosen the assertion.

- [ ] **Step 3: Commit**

```bash
git add e2e/test_account_story_flow.py
git commit -m "test(e2e): the account page's creates and story over real HTTP

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Final verification

**Files:** none (fixes only, if a check fails).

**Interfaces:** none.

- [ ] **Step 1: Full suite**

Run: `venv/bin/python manage.py test --parallel --noinput`
Expected: `OK`, including `services.organizations` (story unchanged), `services.copilot` (the organisation-page grounding calls `build_story`), `services.customers` and `e2e`.

- [ ] **Step 2: Lint, format and migrations**

Run: `venv/bin/ruff check . && venv/bin/ruff format --check . && venv/bin/python manage.py makemigrations --check --dry-run`
Expected: `All checks passed!`, no files that would be reformatted, `No changes detected`.

- [ ] **Step 3: House-rule greps**

Run: `grep -n "SOC2:AUTH-02" services/account_story/scope.py services/customers/scoping.py services/customers/views.py | grep -n "get_url_account\|account must\|flat route\|either route"`
Expected: the comments on `resolve_account_scope`, `get_url_account` and `AccountDetailView.get_queryset`.

Run: `grep -rn "reverse(\|build_absolute_uri\|HttpResponse(" services/account_story`
Expected: no output (the cursor is an opaque string; no built URLs).

- [ ] **Step 4: Contract check**

Read `docs/API_CONTRACTS.md` "`account_story`" against a live response. Start the server with `venv/bin/python manage.py runserver`, sign in as a seeded CSM and `GET /api/v1/accounts/<id>/story/` for an account they own. Every key in the doc's JSON example must be in the response, and nothing else. Then `GET /api/v1/accounts/<id>/story/` for an account id from another seeded owner and confirm `404`.

- [ ] **Step 5: Whole-branch review, then hand off**

Use `superpowers:requesting-code-review` on `main...feat/account-story`, then `superpowers:finishing-a-development-branch`. The PR body names the merge order: this backend PR merges and deploys before the delivery-2 frontend PR. If #74 merged first, rebase and keep both sides of `INSTALLED_APPS`, the `api/v1/accounts/` includes, the API_CONTRACTS feature rows and the PRD release rows (Decision 10).

---

## Self-review

**1. Spec coverage.**

| Spec requirement | Task |
|---|---|
| `GET /api/v1/accounts/<id>/story/`: items, counts, attention in the organisation story's shapes and params | 1 (engine over any scope), 5 (view, shape and params tests) |
| `resolve_account_scope`: 404 whether or not the account exists | 2 (unit and integration), 5 (endpoint), 7 (e2e) |
| Each source applies its own record rule (mail by owner and chain, tickets by department, notes and tasks personal) | Reused `Source.base` (unchanged); 5 (`AccountStoryPrivacyTests`), 6 (parity with per-type endpoints) |
| Only records with `account_id` = this account | 2 (`parent_q`), 5 (`test_only_records_filed_on_this_account`), 7 |
| Needs attention: renewal overdue or within 30 days, open High/Critical tickets (count, oldest age), overdue tasks | 3 |
| No Knowledge on the account page | 3 (`questions` always null), Decision 4 |
| `/accounts/<id>/…` endpoints for contacts, opportunities, risks, files, calls, surveys, tasks, notes; adds go to this account | 4 (flat routes, creates, parity) |
| "An account filter or field is added only where one is missing" | Tab audit (nothing missing except the route), Decision 7 |
| Edit on the name row | 4 (flat `GET/PATCH /accounts/<id>/`) |
| Part of links: only organisations the viewer may open | Delivery 1's portfolio row, Decision 7; Decision 8 records the serializer's existing behaviour |
| No archive or churn | 3 (`account_renewal` has no churn test) |
| Privacy through `blind_to_one_account`; another tenant | 2, 4, 5 |
| Query counts pinned flat as records grow | 6 (story, 24), 4 (flat route equals nested) |
| Unit, integration, e2e | Unit: 2, 3. Integration: 1–6. E2e: 7 |
| Backend PR merges before the frontend | 8, Step 5 |

**2. Placeholder scan.** Every code step shows complete code. Each doc edit shows its exact text and says where it goes. No "TBD" or "similar to Task N". Tasks 6 and 7 have no red step on purpose, and each says why.

**3. Type consistency.**
- `AccountScope(account=…)` exposes `.account`, `.accounts`, `.cursor_key`, `.parent_q(account=None)` and `.account_ref(account_id)` in Tasks 2, 3, 5 and 6.
- `build_account_attention(user, scope, bases, account, *, today)` matches `build_attention`'s signature and the `attention(...)` call in Task 1's `build_story`.
- `resolve_account_scope(user, account_id)` is used the same way in the fixture and the view.
- `get_url_account(request, kwargs)` is used with `self.kwargs` at every call site.
- The flat routes use `<int:account_id>`, the kwarg the nested views and `get_url_account` read. The detail uses `<int:pk>` (`RetrieveUpdateAPIView`'s lookup).
- `fingerprint(params, key)` is called positionally everywhere: in `build_page` and in the existing `test_story_cursor.py`.
