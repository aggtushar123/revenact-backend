# Accounts portfolio, backend (delivery 1: list and Board data) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve the redesigned `/accounts` list and Board from `GET /api/v1/accounts/portfolio/` (rows, the opened-row `details`, groups, summary tiles, filter options, cursor pages, `group_value` for a Board column), plus `GET /api/v1/accounts/portfolio/export.csv` and `POST /api/v1/accounts/bulk/`.

**Architecture:** A new model-less app, `services/accounts_portfolio/`, laid out like `services/organizations/` (`params.py`, `book.py`, `shape.py`, `rows.py`, `fields.py`, `export.py`, `serializers.py`, `bulk.py`, `views.py`, `urls.py`). It imports Organizations' generic helpers rather than copying them: the health and NPS filters, `signal_for`, `snapshot_history`, the sparkline, `triage`, the keyset cursor, the section order, the pulse block, the CSV cell and renderer. Task 1 makes the few private ones public and lifts three inline pieces into functions so they can be imported. The account rules stay in the new app: visibility through `visible_accounts`, no archive or churn, ARR as stored, the account's own tickets, and the linked organisations the viewer may open. Bulk runs `AccountSerializer` per id, the way Organizations runs `CustomerSerializer`.

**Tech Stack:** Django 5, DRF, PostgreSQL, `SimpleTestCase`/`TestCase`/`APIClient`, `LiveServerTestCase` for e2e.

**Spec:** `react-ts-app/docs/superpowers/specs/2026-09-29-accounts-redesign-design.md`. This plan covers §1 "Backend (delivery 1)", plus the parts of §1 that set the data: the row, the panels, the tiles, filters, groups, sort and the Board. It also follows §5 Testing (backend) and the Decisions table. Deliveries 2 and 3 (the account story, Ask) are out of scope. For intent, see `react-ts-app/docs/superpowers/specs/2026-09-25-organizations-portfolio-design.md` §2, and the shipped code in `services/organizations/`.

## Global Constraints

Copied from the spec (quoted text is verbatim):

- Backend: "Account-specific services (`services/accounts_portfolio/`). They reuse Organizations' generic helpers (health and renewal filters, `signal_for`, `snapshot_history`, `triage`, the story sources) and keep account rules and fields their own. A generic "company kind" through every Organizations module was rejected: it couples two sets of rules."
- "No archive or churn: Accounts have neither, so there is no Archive, Churn or churned filter on Accounts."
- Summary tiles: "Health · NPS · Lifecycle · ARR · Renewing within 90 days. A tile sets its filter; tapping it again clears it."
- Group: "health on the List, lifecycle on the Board. Owner and renewal window are also offered."
- Sort: "risk, ARR, renewal, health, name."
- Filters: "organisation, owner (with Unassigned), lifecycle, health, renews within, NPS band."
- "Every filter, the sort and the group live in the URL."
- Row name line: "**"Organisation · owner · lifecycle · touched Nd ago"** beneath at 11px muted. An account linked to two or more organisations shows the first one the viewer may open (lowest id) and "+N"."
- Trend: "a 6-month health sparkline from the account's `HealthSnapshot`s".
- Renewal runway: "in Nd", or "Nd overdue".
- ARR: "in the workspace's currency (the tenant `Organisation.currency`, as on Organizations)."
- Pulse: ""AI n · CSM n", with "pulses disagree" as text when they differ by 2 or more."
- Signal: "at most one, from `signal_for`. Priority is renewal overdue, then risk ≥ 40 ("Risk NN"), then open High/Critical tickets ("N open tickets")."
- Panels: "Commercial: ARR; renewal date … Voice of the customer: NPS …, CSAT, AI pulse reason … Profile: Revenact ID, domain, industry, email, phone, address, organisation(s) as links … History: Created, updated, pulse recorded on". "Every account field appears exactly once."
- Board: "Every stage gets a column, empty ones included. Each column is one paged read (`group_value`) that loads its next page as it scrolls, and its header shows the count and ARR." "The move is optimistic, saved through the single-account update" (the existing `PATCH /customers/<cid>/accounts/<id>/`, which this plan does not change except for the inactive-owner rule, see Task 10).
- `GET /api/v1/accounts/portfolio/`: "Returns rows (the header fields), details (the panel fields), groups (key, label, count, ARR), a summary (the five tiles over the whole filtered set) and filter options (organisations and owners the viewer may see)." "Cursor pages, with `group_value` for a Board column."
- `GET /api/v1/accounts/portfolio/export.csv`: "The filtered set with every field." "Formula cells are neutralised, and the export is audited as `accounts.exported`."
- `POST /api/v1/accounts/bulk/`: "Owner and lifecycle for many ids." "Each id is checked through `visible_accounts`, and only a user who may reassign may change the owner, as on Organizations." "Results are reported per id."
- Access: "first `visible_accounts(user)`, then each record's own rule for anything counted from records (urgent tickets follow `visible_tickets`). The summary counts only what the viewer can see."
- Testing (backend): "Unit, integration and e2e tests." "Privacy through the "blind to one account" setup: an account the viewer cannot open is absent from rows, summary, groups, export and bulk (404 or skipped per id)". "Query counts pinned flat as rows and records grow." "Bulk owner-change gating." "The CSV formula neutralising."
- "A backend PR merges and deploys before its frontend, unless its change breaks the deployed frontend". Nothing here changes an existing response, so this PR goes first.

House rules (every task):

- `docs/API_CONTRACTS.md` changes in the same PR (skill `api-contracts`). So do the product docs (`docs/product/01-prd.md` feature row and release history, `02-trd.md`, `05-backend-schema.md`), `docs/audit-events.md` and `docs/data-classification.md`.
- Tests come in three tiers (skill `backend-testing`): unit (`SimpleTestCase`), integration (`TestCase` + `APIClient`), and e2e (`LiveServerTestCase` in `e2e/`). Privacy uses `blind_to_one_account` from `services/customers/tests/test_views.py`. Focused runs use `venv/bin/python manage.py test <label> --noinput`. The full suite runs with `venv/bin/python manage.py test --parallel --noinput`.
- `venv/bin/ruff check .` and `venv/bin/ruff format --check .` must pass. Ruff also formats Python fences inside Markdown, so run `venv/bin/ruff format <file.md>` on any doc you edit that has a `python` fence.
- Put `# SOC2:AUTH-02 <why>` on every access check and `# SOC2:LOG-01` on every `audit.record` call.
- Semgrep runs in CI. Return an opaque cursor string, never a built URL, and let a DRF renderer write the CSV (no hand-built `HttpResponse`).
- Commits are conventional (`feat(accounts): …`, `refactor(organizations): …`) and end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Reuse map: what is imported, what is the account's own

Before relying on each helper, the plan checked its code and signature in `services/organizations/`.

| Need | Source | Import / change | Why |
|---|---|---|---|
| Health band filter | `organizations.book.health_q(category)` | `from services.organizations.book import health_q` | Pure `Q(health_score…)` over `Customer.HEALTH_THRESHOLDS`, which `Account` shares (`health_category_for`) |
| NPS band filter | `organizations.book.NPS_Q` | import | Pure `Q(nps_score…)` by sign |
| Tile windows | `organizations.book.RENEWING_WINDOWS` (`(30, 90)`) | import | Same tile shape |
| Signal | `organizations.book.signal_for(*, churned, renewal_days, risk, urgent_tickets)` | import, always `churned=False` | Accounts have no churn |
| Snapshots | `organizations.book.snapshot_history(ids, *, since)` | **Task 1** adds `parent="customer"` (or `"account"`) | It filtered `customer_id` only |
| Sparkline | three inline lines in `organizations.book._entry` | **Task 1** lifts them into `health_trend(snapshots, current, *, today)` | So both kinds share one rule |
| Triage | `services.customers.triage.triage(...)` | import | Already generic |
| Renewal filter | `organizations.book.renewing_q(days, *, today)` | **not reused**: `account_renewing_q` | It ends `& ~CHURNED`, which reads `Customer.churn_date`. `Account` has no such field, so the query would fail. It is also the churn rule the spec excludes |
| Urgent tickets | `organizations.book.urgent_ticket_counts(user, ids)` | **not reused**: `account_urgent_ticket_counts` | It reads `attention.rules.support_tickets(user, customer_ids)`, which fans an account's ticket out to its companies. An account counts its own tickets |
| Params parsing | `organizations.params._int`, `_list` | **Task 1** renames them `int_or_none`, `comma_list` | Private names are not imported across apps |
| Constants | `organizations.params.RENEWS_WITHIN_DAYS, NPS_BANDS, DEFAULT_SORT, DEFAULT_LIMIT, MAX_LIMIT, MAX_IDS` | import | Same values |
| Cursor | `organizations.shape.paginate` body, `_Desc`, `_wrap`, `encode_cursor`, `decode_cursor` | **Task 1** extracts `keyset_page(entries, *, rank, cursor, limit, descending, fingerprint, grouped)`, renames `_Desc`→`Desc` and `_wrap`→`wrap_desc` | `paginate` called the Customer-only `_rank` directly |
| Section order | `organizations.shape._group_rank(key, label, group)` | **Task 1** renames it `group_rank` | Already kind-free: health, lifecycle (the shared `LifecycleStage`) and renewal by fixed order, anything else by name with the empty bucket last |
| Renewal windows | `organizations.shape.renewal_window(days)`, `RENEWAL_WINDOWS` | import | Pure |
| Group key, sort getters, fingerprint, summary | `organizations.shape.group_key`, `SORT_GETTERS`, `filter_fingerprint`, `build_summary` | **not reused**: the account's own | They read `entry.customer`, products, `include_churned`, the FX `unconverted` path and the `nps_band` annotation |
| Row helpers | `organizations.rows.initials`, `_iso`, `_number`, `_person`, `PULSE_DISAGREE_GAP` | **Task 1** renames `iso`, `number`, `person`, and lifts the pulse block into `pulse_payload(record)` | Both models have the same pulse fields |
| Export | `organizations.export.cell`, `CSVRenderer`; `organizations.fields.Field` | import | Pure |
| Bulk | `organizations.bulk._reason`, `NOT_FOUND`, `NOT_UPDATED` | **Task 1** renames `first_reason` | Same per-id reporting |
| After-save handover | `customers.views.after_customer_update` | **Task 10** adds `after_account_update` beside it, and `AccountDetailView.perform_update` uses it | So one reassignment means the same thing from either path, as on Organizations |

## File Structure

| File | Responsibility |
|---|---|
| `services/organizations/params.py`, `book.py`, `shape.py`, `rows.py`, `bulk.py` (modify) | Task 1 only: public names, `keyset_page`, `health_trend`, `pulse_payload`, `snapshot_history(parent=)`. No behaviour change |
| `services/organizations/tests/test_generic_helpers.py` (new) | Unit tests for what Task 1 exposes |
| `services/accounts_portfolio/__init__.py`, `apps.py` (new) | App config (`AccountsPortfolioConfig`, label `accounts_portfolio`) |
| `services/accounts_portfolio/params.py` (new) | `AccountPortfolioParams`, `parse_params(query)`, `SORT_KEYS`, `GROUPS` |
| `services/accounts_portfolio/book.py` (new) | `account_renewing_q`, `filtered_queryset`, `AccountEntry`, `AccountPortfolio`, `account_urgent_ticket_counts`, `linked_organisations`, `load_portfolio`, `filter_options` |
| `services/accounts_portfolio/shape.py` (new) | `SORT_GETTERS`, `group_key`, `order_entries`, `build_groups`, `select`, `filter_fingerprint`, `paginate`, `nps_band`, `build_summary`, `build_listing` |
| `services/accounts_portfolio/rows.py` (new) | `details_payload(account, organisations)`, `row_payload(entry)` |
| `services/accounts_portfolio/fields.py` (new) | `FIELDS`: every Account field as an export column |
| `services/accounts_portfolio/export.py` (new) | `table(rows, *, currency)` |
| `services/accounts_portfolio/serializers.py` (new) | `BulkRequestSerializer` |
| `services/accounts_portfolio/bulk.py` (new) | `apply(request, *, ids, action, value, result=None)` |
| `services/accounts_portfolio/views.py`, `urls.py` (new) | `AccountPortfolioView`, `AccountPortfolioExportView`, `AccountBulkUpdateView` |
| `services/accounts_portfolio/tests/` (new) | `__init__.py`, `fixtures.py`, `test_params.py`, `test_book.py`, `test_signals.py`, `test_rows.py`, `test_shape.py`, `test_summary.py`, `test_views.py`, `test_export.py`, `test_bulk.py` |
| `services/customers/views.py` (modify) | `after_account_update`; `AccountDetailView.perform_update` calls it |
| `services/customers/serializers.py` (modify) | `AccountSerializer.validate_owner_id` refuses an inactive owner, as `CustomerSerializer` does |
| `config/settings.py`, `config/urls.py` (modify) | Register the app; mount `api/v1/accounts/` |
| `e2e/test_accounts_flow.py` (new) | Read, filter, group, bulk, export over real HTTP |
| docs (modify) | `API_CONTRACTS.md`, `audit-events.md`, `data-classification.md`, `product/01-prd.md`, `product/02-trd.md`, `product/05-backend-schema.md` |

---

### Task 1: Expose Organizations' generic helpers

This is a refactor with no behaviour change. The existing `services.organizations` suite is the regression gate. New unit tests pin each helper the account app will import.

**Files:**
- Modify: `services/organizations/params.py`, `services/organizations/book.py`, `services/organizations/shape.py`, `services/organizations/rows.py`, `services/organizations/bulk.py`
- Test: `services/organizations/tests/test_generic_helpers.py` (new)

**Interfaces:**
- Consumes: the existing Organizations modules.
- Produces:
  - `services.organizations.params.int_or_none(raw) -> int | None` and `comma_list(raw) -> list[str]`.
  - `services.organizations.book.snapshot_history(ids, *, since, parent="customer") -> defaultdict[int, list[tuple[date, Decimal]]]`, where `parent` is `"customer"` or `"account"`. Any other value raises `ValueError`.
  - `services.organizations.book.health_trend(snapshots, current, *, today) -> list[float]`.
  - `services.organizations.shape.Desc`, `wrap_desc(value, descending)`, `group_rank(key, label, group) -> tuple[int, str, str]`.
  - `services.organizations.shape.keyset_page(entries, *, rank, cursor, limit, descending, fingerprint, grouped) -> tuple[list, str | None]`. `rank(entry)` returns `(section, bucket, wrapped value, (name, id))`.
  - `services.organizations.shape.paginate(entries, *, params, portfolio)`, whose signature is unchanged.
  - `services.organizations.rows.iso(value)`, `number(value)`, `person(user)`, and `pulse_payload(record) -> dict` for anything with `csm_pulse_score`, `ai_pulse_value`, `ai_pulse_score`, `ai_pulse_reason` and `pulse`.
  - `services.organizations.bulk.first_reason(errors) -> str`.

- [ ] **Step 1: Write the failing tests**

`services/organizations/tests/test_generic_helpers.py`:

```python
"""The Organizations helpers the Accounts portfolio imports, pinned on their
own so a change to one is seen by both lists."""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from services.customers.models import Account, HealthSnapshot
from services.organizations import book, bulk, rows, shape
from services.organizations.params import comma_list, int_or_none
from services.organizations.tests.fixtures import PortfolioFixture


def rank_by_arr(descending):
    def rank(entry):
        value = shape.wrap_desc(entry["arr"], descending)
        return ((), 0, value, (entry["name"], entry["id"]))

    return rank


class KeysetPageTests(SimpleTestCase):
    """`keyset_page` pages any kind's entries — here, plain dicts."""

    ENTRIES = [{"id": i, "name": f"n{i}", "arr": float(i)} for i in range(5)]

    def page(self, entries, cursor, *, descending=False, fingerprint="f", grouped=False):
        return shape.keyset_page(
            entries,
            rank=rank_by_arr(descending),
            cursor=cursor,
            limit=2,
            descending=descending,
            fingerprint=fingerprint,
            grouped=grouped,
        )

    def follow(self, *, descending):
        entries = sorted(self.ENTRIES, key=rank_by_arr(descending))
        seen, cursor = [], ""
        for _ in range(10):
            page, cursor = self.page(entries, cursor, descending=descending)
            seen += [entry["id"] for entry in page]
            if cursor is None:
                return seen
        self.fail(f"the cursor never ended: {seen}")

    def test_every_entry_once_in_either_direction(self):
        self.assertEqual(self.follow(descending=False), [0, 1, 2, 3, 4])
        self.assertEqual(self.follow(descending=True), [4, 3, 2, 1, 0])

    def test_a_cursor_cut_from_another_list_reads_the_first_page(self):
        entries = sorted(self.ENTRIES, key=rank_by_arr(False))
        _page, cursor = self.page(entries, "")
        self.assertIsNotNone(cursor)
        other_list, _next = self.page(entries, cursor, fingerprint="g")
        self.assertEqual([entry["id"] for entry in other_list], [0, 1])
        now_grouped, _next = self.page(entries, cursor, grouped=True)
        self.assertEqual([entry["id"] for entry in now_grouped], [0, 1])
        garbage, _next = self.page(entries, "%%%")
        self.assertEqual([entry["id"] for entry in garbage], [0, 1])


class GroupRankTests(SimpleTestCase):
    def test_fixed_orders_and_names_with_the_empty_bucket_last(self):
        self.assertLess(
            shape.group_rank("poor", "Poor", "health"), shape.group_rank("good", "Good", "health")
        )
        self.assertLess(
            shape.group_rank("onboarding", "Onboarding", "lifecycle"),
            shape.group_rank("live", "Live", "lifecycle"),
        )
        self.assertLess(
            shape.group_rank("7", "Zed", "owner"),
            shape.group_rank("unassigned", "Unassigned", "owner"),
        )


class HealthTrendTests(SimpleTestCase):
    def test_six_points_ending_at_todays_score(self):
        today = date(2026, 9, 29)
        snapshots = [
            (today - timedelta(days=30 * months), Decimal(score))
            for months, score in (
                (8, "9.0"),
                (6, "7.0"),
                (5, "6.2"),
                (4, "5.8"),
                (3, "5.5"),
                (2, "5.1"),
                (1, "5.0"),
            )
        ]
        self.assertEqual(
            book.health_trend(snapshots, Decimal("4.9"), today=today),
            [6.2, 5.8, 5.5, 5.1, 5.0, 4.9],
        )

    def test_no_snapshots_is_todays_score_alone(self):
        self.assertEqual(book.health_trend([], Decimal("4.9"), today=date(2026, 9, 29)), [4.9])


class PulsePayloadTests(SimpleTestCase):
    def record(self, **fields):
        values = {
            "csm_pulse_score": 3,
            "ai_pulse_value": 1,
            "ai_pulse_score": "high_risk",
            "ai_pulse_reason": "Quiet since the outage.",
            "pulse": [1, 2, 2],
            **fields,
        }
        return SimpleNamespace(**values)

    def test_both_pulses_the_label_and_the_disagreement(self):
        self.assertEqual(
            rows.pulse_payload(self.record()),
            {
                "csm": 3,
                "ai": 1,
                "ai_category": "high_risk",
                "ai_label": "High Risk",
                "reason": "Quiet since the outage.",
                "history": [1, 2, 2],
                "disagree": True,
            },
        )

    def test_one_side_unrated_never_disagrees(self):
        payload = rows.pulse_payload(
            self.record(ai_pulse_value=None, ai_pulse_score="", pulse=None)
        )
        self.assertEqual((payload["disagree"], payload["ai_label"]), (False, ""))
        self.assertEqual(payload["history"], [])


class SnapshotHistoryTests(PortfolioFixture):
    def test_reads_one_parent_kind(self):
        customer = self.customer("Pizza Hut")
        account = Account.objects.create(name="Pizza EMEA")
        account.customers.add(customer)
        day = self.today - timedelta(days=30)
        HealthSnapshot.objects.create(
            customer=customer, captured_on=day, health_score=Decimal("6.0")
        )
        HealthSnapshot.objects.create(account=account, captured_on=day, health_score=Decimal("3.0"))
        since = self.today - timedelta(days=365)
        self.assertEqual(
            dict(book.snapshot_history([customer.pk], since=since)),
            {customer.pk: [(day, Decimal("6.0"))]},
        )
        self.assertEqual(
            dict(book.snapshot_history([account.pk], since=since, parent="account")),
            {account.pk: [(day, Decimal("3.0"))]},
        )

    def test_an_unknown_parent_is_refused(self):
        with self.assertRaises(ValueError):
            book.snapshot_history([1], since=self.today, parent="contact")


class SmallHelperTests(SimpleTestCase):
    def test_params_helpers(self):
        self.assertEqual(int_or_none("7"), 7)
        self.assertIsNone(int_or_none("x"))
        self.assertIsNone(int_or_none(None))
        self.assertEqual(comma_list(" a, ,b "), ["a", "b"])
        self.assertEqual(comma_list(None), [])

    def test_first_reason(self):
        self.assertEqual(bulk.first_reason({"owner_id": ["Nope."]}), "Nope.")
        self.assertEqual(bulk.first_reason({}), bulk.NOT_UPDATED)
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `venv/bin/python manage.py test services.organizations.tests.test_generic_helpers --noinput`
Expected: `ImportError: cannot import name 'comma_list' from 'services.organizations.params'`.

- [ ] **Step 3: Rename the private helpers**

Run these substitutions. Each `\b…\(` pattern matches a definition or a call, and never a longer name such as `values_list(` or `comma_list(`:

```bash
perl -pi -e 's/\b_int\(/int_or_none(/g; s/\b_list\(/comma_list(/g' services/organizations/params.py
perl -pi -e 's/\b_iso\(/iso(/g; s/\b_number\(/number(/g; s/\b_person\(/person(/g' services/organizations/rows.py
perl -pi -e 's/\b_Desc\b/Desc/g; s/\b_wrap\(/wrap_desc(/g; s/\b_group_rank\(/group_rank(/g' services/organizations/shape.py
perl -pi -e 's/\b_reason\(/first_reason(/g' services/organizations/bulk.py
grep -nE '(^|[^[:alnum:]_])(_int|_list|_iso|_number|_person|_wrap|_group_rank|_reason)\(|(^|[^[:alnum:]_])_Desc([^[:alnum:]_]|$)' services/organizations/params.py services/organizations/rows.py services/organizations/shape.py services/organizations/bulk.py
```

Expected: the final `grep` prints nothing.

- [ ] **Step 4: Add `parent` to `snapshot_history` and lift `health_trend`**

In `services/organizations/book.py`, replace the whole `snapshot_history` function with:

```python
#: The parents a health snapshot can belong to (exactly one per row).
SNAPSHOT_PARENTS = ("customer", "account")


def snapshot_history(ids, *, since, parent="customer"):
    """Each parent's health snapshots since `since`, oldest first, as
    `(captured_on, health_score)` pairs — plain tuples in one query, not model
    instances: at a few thousand customers with a year of month-ends each,
    building the instances was most of a request's Python time. `parent` is
    `"customer"` (Organizations) or `"account"` (the Accounts portfolio)."""
    if parent not in SNAPSHOT_PARENTS:
        raise ValueError(parent)
    history = defaultdict(list)
    if not ids:
        return history
    key = f"{parent}_id"
    rows = (
        HealthSnapshot.objects.filter(**{f"{key}__in": ids, "captured_on__gte": since})
        .order_by("captured_on")
        .values_list(key, "captured_on", "health_score")
    )
    for parent_id, captured_on, score in rows:
        history[parent_id].append((captured_on, score))
    return history


def health_trend(snapshots, current, *, today):
    """The row's sparkline: the scores of the snapshots inside the last
    `TREND_MONTHS` (oldest first), the last five kept, then `current` — six
    points that end at the ring's own number. `snapshots` are
    `snapshot_history` pairs."""
    since = today - timedelta(days=31 * TREND_MONTHS)
    recent = [float(score) for captured_on, score in snapshots if captured_on >= since]
    return recent[-(TREND_MONTHS - 1) :] + [float(current)]
```

`TREND_MONTHS` is defined further down the module, so move the `TREND_MONTHS = 6` line and its comment up so they sit directly above `SNAPSHOT_PARENTS`. The function reads the name when it is called, so either placement works, but a reader should meet the constant first.

In `_entry`, replace these three lines:

```python
    since = today - timedelta(days=31 * TREND_MONTHS)
    recent = [float(score) for captured_on, score in snapshots if captured_on >= since]
    trend = recent[-(TREND_MONTHS - 1) :] + [float(customer.health_score)]
```

with:

```python
    trend = health_trend(snapshots, customer.health_score, today=today)
```

- [ ] **Step 5: Extract `keyset_page` from `paginate`**

In `services/organizations/shape.py`, replace the whole `paginate` function (from `def paginate(entries, *, params, portfolio):` through its `return page, next_cursor`) with:

```python
def keyset_page(entries, *, rank, cursor, limit, descending, fingerprint, grouped):
    """Keyset pagination over an already-ordered list, for any kind of entry.
    `rank(entry)` is the exact tuple the list was sorted by — `(section,
    bucket, wrapped value, (name, id))`, as `_rank` builds it. The cursor
    names the last served row's full rank, unwrapped, plus the fingerprint of
    the list it was cut from. The next page is every entry that ranks
    strictly after it, found with a scan over `entries`. Rows added or removed
    anywhere else in the set, in any number, never cause a skip or a repeat:
    the cut is by value, not by a row count. A malformed or tampered cursor,
    or one cut from a list with another fingerprint or grouping, is treated
    as absent — the first page."""
    start = 0
    decoded = decode_cursor(cursor)
    if decoded is not None and decoded[5] == fingerprint and bool(decoded[0]) == grouped:
        section, bucket, value, name, entry_id, _fingerprint = decoded
        # Missing values are never wrapped in a rank either — only a present
        # value's direction is reversed.
        wrapped = value if bucket == 1 else wrap_desc(value, descending)
        cursor_rank = (section, bucket, wrapped, (name, entry_id))
        try:
            start = next(
                (index for index, entry in enumerate(entries) if rank(entry) > cursor_rank),
                len(entries),
            )
        except TypeError:
            # A value of a type the sort cannot compare — the safest read is
            # the first page.
            start = 0
    page = entries[start : start + limit]
    next_cursor = None
    if page and start + len(page) < len(entries):
        section, last_bucket, last_value, (last_name, last_id) = rank(page[-1])
        raw_value = last_value.value if isinstance(last_value, Desc) else last_value
        next_cursor = encode_cursor(
            section, last_bucket, raw_value, last_name, last_id, fingerprint
        )
    return page, next_cursor


def paginate(entries, *, params, portfolio):
    """`keyset_page` over `select`'s order: ranked by `_rank` with this list's
    sort and group, cut against this list's `filter_fingerprint`."""
    sort_key, descending, group = params.sort_key, params.descending, params.group
    return keyset_page(
        entries,
        rank=lambda entry: _rank(entry, sort_key, descending, portfolio, group),
        cursor=params.cursor,
        limit=params.limit,
        descending=descending,
        fingerprint=filter_fingerprint(params),
        grouped=bool(group),
    )
```

- [ ] **Step 6: Lift `pulse_payload` out of `row_payload`**

In `services/organizations/rows.py`, add this above `details_payload`:

```python
def pulse_payload(record):
    """The row's pulse, for anything that carries the two pulses, the AI
    reason and the stored history dots — a Customer or an Account."""
    csm, ai = record.csm_pulse_score, record.ai_pulse_value
    ai_category = record.ai_pulse_score
    return {
        "csm": csm,
        "ai": ai,
        "ai_category": ai_category,
        "ai_label": Customer.AIPulseScore(ai_category).label if ai_category else "",
        "reason": record.ai_pulse_reason,
        # The old table's "Pulse" column: the stored history dots.
        "history": list(record.pulse or []),
        "disagree": csm is not None and ai is not None and abs(csm - ai) >= PULSE_DISAGREE_GAP,
    }
```

Then replace the whole `row_payload` function with:

```python
def row_payload(entry):
    customer = entry.customer
    return {
        "id": customer.pk,
        "name": customer.name,
        "initials": initials(customer.name),
        "owner": person(customer.owner),
        "lifecycle": {
            "value": customer.lifecycle_stage,
            "label": customer.get_lifecycle_stage_display(),
        },
        "health": {
            "score": float(customer.health_score),
            "category": customer.health_category,
            "trend": entry.trend,
        },
        "renewal": {"date": iso(customer.renewal_date), "days": entry.renewal_days},
        "arr": entry.arr,
        "risk": {"score": entry.triage.score, "direction": entry.triage.direction},
        "pulse": pulse_payload(customer),
        "last_touch_days": entry.last_touch_days,
        "urgent_tickets": entry.urgent_tickets,
        "signal": entry.signal,
        "is_archived": customer.is_archived,
        "churned": entry.churned,
        "details": details_payload(customer),
    }
```

- [ ] **Step 7: Run the new tests and the whole Organizations suite**

Run: `venv/bin/python manage.py test services.organizations services.copilot --parallel --noinput`
Expected: `OK`. The Organizations tests and the Copilot tests (whose Organizations grounding imports `organizations.book` and `shape`) all pass unchanged.

Run: `venv/bin/ruff format services/organizations && venv/bin/ruff check services/organizations`
Expected: `1 file reformatted` and `All checks passed!`. The rename makes the `health=` comprehension in `params.py` longer than 100 characters, so format re-wraps it. Run the tests again after formatting.

- [ ] **Step 8: Commit**

```bash
git add services/organizations
git commit -m "$(cat <<'EOF'
refactor(organizations): expose the portfolio's generic helpers

keyset_page, health_trend, pulse_payload, group_rank and
snapshot_history(parent=) become importable so the Accounts portfolio
reuses them instead of copying. No behaviour change.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: The app and its parameters

**Files:**
- Create: `services/accounts_portfolio/__init__.py` (empty), `services/accounts_portfolio/apps.py`, `services/accounts_portfolio/params.py`, `services/accounts_portfolio/tests/__init__.py` (empty), `services/accounts_portfolio/tests/test_params.py`
- Modify: `config/settings.py` (`INSTALLED_APPS`, after `"services.organizations",`)

**Interfaces:**
- Consumes: from Task 1, `services.organizations.params.int_or_none` and `comma_list`, plus the constants `DEFAULT_LIMIT`, `DEFAULT_SORT` (`"-arr"`), `MAX_IDS` (500), `MAX_LIMIT` (100), `NPS_BANDS` and `RENEWS_WITHIN_DAYS` (`(30, 90, 180)`).
- Produces:
  - `SORT_KEYS = ("risk", "arr", "renewal", "health", "name")` and `GROUPS = ("health", "lifecycle", "owner", "renewal")`.
  - `AccountPortfolioParams`, a frozen dataclass with `search: str`, `organisations: tuple[int, ...]`, `owner: int | str | None`, `lifecycles: tuple[str, ...]`, `health: tuple[str, ...]`, `renews_within: int | None`, `nps: str | None`, `ids: tuple[int, ...] | None`, `sort: str`, `group: str`, `group_value: str | None`, `cursor: str` and `limit: int`, and the properties `sort_key` and `descending`.
  - `parse_params(query: Mapping) -> AccountPortfolioParams`.

- [ ] **Step 1: Write the failing tests**

`services/accounts_portfolio/tests/test_params.py`:

```python
from django.apps import apps
from django.http import QueryDict
from django.test import SimpleTestCase

from services.accounts_portfolio.params import (
    GROUPS,
    SORT_KEYS,
    AccountPortfolioParams,
    parse_params,
)


class AppTests(SimpleTestCase):
    def test_the_app_is_installed(self):
        self.assertEqual(
            apps.get_app_config("accounts_portfolio").name, "services.accounts_portfolio"
        )


class ParseParamsTests(SimpleTestCase):
    def test_nothing_given_is_the_defaults(self):
        params = parse_params({})
        self.assertEqual(params, AccountPortfolioParams())
        self.assertEqual(
            (params.sort, params.limit, params.ids, params.group), ("-arr", 50, None, "")
        )
        self.assertEqual((params.sort_key, params.descending), ("arr", True))

    def test_the_five_sorts_and_four_groups(self):
        self.assertEqual(SORT_KEYS, ("risk", "arr", "renewal", "health", "name"))
        self.assertEqual(GROUPS, ("health", "lifecycle", "owner", "renewal"))
        self.assertEqual(parse_params({"sort": "-risk"}).sort, "-risk")
        self.assertEqual(parse_params({"sort": "name"}).sort, "name")
        # Organizations-only sorts and groups are unknown here, so dropped.
        for other in ("touch", "-nps_score", "arr_billed_at_hq"):
            self.assertEqual(parse_params({"sort": other}).sort, "-arr")
        self.assertEqual(parse_params({"group": "product"}).group, "")

    def test_a_query_dict_works_like_a_dict(self):
        params = parse_params(
            QueryDict(
                "organisation=3,x,4&lifecycle=live,churn,bogus&health=poor,purple&owner=unassigned"
            )
        )
        self.assertEqual(params.organisations, (3, 4))
        self.assertEqual(params.lifecycles, ("live", "churn"))
        self.assertEqual(params.health, ("poor",))
        self.assertEqual(params.owner, "unassigned")

    def test_owner_is_an_id_or_unassigned(self):
        self.assertEqual(parse_params({"owner": "7"}).owner, 7)
        self.assertEqual(parse_params({"owner": "unassigned"}).owner, "unassigned")
        self.assertIsNone(parse_params({"owner": "carl"}).owner)

    def test_windows_bands_and_limit(self):
        self.assertEqual(parse_params({"renews_within": "90"}).renews_within, 90)
        self.assertIsNone(parse_params({"renews_within": "45"}).renews_within)
        self.assertEqual(parse_params({"nps": "detractor"}).nps, "detractor")
        self.assertIsNone(parse_params({"nps": "fan"}).nps)
        self.assertEqual(parse_params({"limit": "500"}).limit, 100)
        self.assertEqual(parse_params({"limit": "0"}).limit, 50)
        self.assertEqual(parse_params({"limit": "x"}).limit, 50)
        self.assertEqual(parse_params({"limit": "20"}).limit, 20)

    def test_ids(self):
        self.assertIsNone(parse_params({}).ids)
        self.assertEqual(parse_params({"ids": "7,x,9"}).ids, (7, 9))
        self.assertEqual(parse_params({"ids": ""}).ids, ())
        many = ",".join(str(i) for i in range(1, 700))
        self.assertEqual(len(parse_params({"ids": many}).ids), 500)

    def test_group_value_needs_a_group(self):
        self.assertIsNone(parse_params({"group_value": "live"}).group_value)
        self.assertEqual(
            parse_params({"group": "lifecycle", "group_value": "live"}).group_value, "live"
        )

    def test_there_is_no_churned_switch(self):
        self.assertFalse(hasattr(parse_params({"include_churned": "1"}), "include_churned"))
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `venv/bin/python manage.py test services.accounts_portfolio.tests.test_params --noinput`
Expected: `ModuleNotFoundError: No module named 'services.accounts_portfolio'`.

- [ ] **Step 3: Write the app and the parser**

`services/accounts_portfolio/apps.py`:

```python
from django.apps import AppConfig


class AccountsPortfolioConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "services.accounts_portfolio"
    verbose_name = "Accounts portfolio"
```

`services/accounts_portfolio/params.py`:

```python
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
```

In `config/settings.py`, add `"services.accounts_portfolio",` on the line after `"services.organizations",` in `INSTALLED_APPS`.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `venv/bin/python manage.py test services.accounts_portfolio.tests.test_params --noinput`
Expected: `OK` (9 tests).

- [ ] **Step 5: Commit**

```bash
git add services/accounts_portfolio config/settings.py
git commit -m "$(cat <<'EOF'
feat(accounts): the accounts portfolio app and its parameters

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: The filtered book and the filter options

**Files:**
- Create: `services/accounts_portfolio/book.py`, `services/accounts_portfolio/tests/fixtures.py`, `services/accounts_portfolio/tests/test_book.py`

**Interfaces:**
- Consumes:
  - `parse_params` and `AccountPortfolioParams` (Task 2).
  - `health_q(category)` and `NPS_Q` from `services.organizations.book`.
  - `visible_accounts(user)` and `visible_customers(user)` from `services.customers.scoping`.
- Produces:
  - `account_renewing_q(days: int, *, today: date) -> Q`.
  - `filtered_queryset(user, params: AccountPortfolioParams, *, today: date) -> QuerySet[Account]`.
  - `filter_options(user) -> {"organisations": [{"value", "name"}], "owners": [{"value", "name"}], "lifecycles": [{"value", "name"}]}`.
  - The test fixture `AccountPortfolioFixture` with `self.today`, `self.org` (USD), `self.admin` (Alice, Leadership), `self.csm` (Carl, CS), `self.other` (Dana, CS), `self.other_org` (Globex), `self.pizza` (owned by Carl), `self.taco` (owned by Dana), `self.globex` (the other tenant's), and `account(name, *, customers=None, owner=<Carl>, **fields) -> Account`.

- [ ] **Step 1: Write the fixture**

`services/accounts_portfolio/tests/fixtures.py`:

```python
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from services.accounts.models import Organisation, User
from services.customers.models import Account, Customer

_CARL = object()


class AccountPortfolioFixture(TestCase):
    """Carl and Dana are CSMs in Acme, Alice is its admin (Leadership), and
    Globex is another tenant. Carl owns the organisation Pizza Hut and Dana
    owns Taco Bell, so Carl sees every account under Pizza Hut and only his
    own or unowned ones under Taco Bell. `account()` makes a Good,
    12,000-ARR account owned by Carl under Pizza Hut unless a test says
    otherwise, so each test's differences are its own."""

    def setUp(self):
        self.today = timezone.localdate()
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice Admin",
            organisation=self.org,
            role=User.Role.ADMIN,
            function=User.Function.LEADERSHIP,
        )
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="supersecret1",
            name="Carl CSM",
            organisation=self.org,
            role=User.Role.CSM,
            function=User.Function.CS,
        )
        self.other = User.objects.create_user(
            email="dana@acme.io",
            password="supersecret1",
            name="Dana CSM",
            organisation=self.org,
            role=User.Role.CSM,
            function=User.Function.CS,
        )
        self.other_org = Organisation.objects.create(name="Globex", currency="USD")
        self.pizza = Customer.objects.create(
            organisation=self.org, name="Pizza Hut", owner=self.csm
        )
        self.taco = Customer.objects.create(
            organisation=self.org, name="Taco Bell", owner=self.other
        )
        self.globex = Customer.objects.create(organisation=self.other_org, name="Globex Corp")

    def account(self, name, *, customers=None, owner=_CARL, **fields):
        values = {"health_score": Decimal("8.0"), "arr": Decimal("12000"), **fields}
        account = Account.objects.create(
            name=name, owner=self.csm if owner is _CARL else owner, **values
        )
        account.customers.add(*(customers if customers is not None else [self.pizza]))
        return account
```

- [ ] **Step 2: Write the failing tests**

`services/accounts_portfolio/tests/test_book.py`:

```python
from datetime import timedelta
from decimal import Decimal

from services.accounts.models import User
from services.accounts_portfolio import book
from services.accounts_portfolio.params import parse_params
from services.accounts_portfolio.tests.fixtures import AccountPortfolioFixture


class FilteredQuerysetTests(AccountPortfolioFixture):
    def names(self, user=None, **query):
        queryset = book.filtered_queryset(user or self.csm, parse_params(query), today=self.today)
        # Instances, not `values_list`: a duplicate row must show as one.
        return sorted(account.name for account in queryset)

    def test_visibility_comes_first(self):
        self.account("Mine")
        self.account("Dana's under Pizza", owner=self.other)
        self.account("Pool under Taco", customers=[self.taco], owner=None)
        self.account("Dana's under Taco", customers=[self.taco], owner=self.other)
        self.account("Globex's", customers=[self.globex], owner=None)
        self.assertEqual(self.names(), ["Dana's under Pizza", "Mine", "Pool under Taco"])
        self.assertEqual(
            self.names(user=self.admin),
            ["Dana's under Pizza", "Dana's under Taco", "Mine", "Pool under Taco"],
        )

    def test_ids_only_narrow(self):
        mine = self.account("Mine")
        hidden = self.account("Hidden", customers=[self.taco], owner=self.other)
        self.assertEqual(self.names(ids=f"{mine.pk},{hidden.pk}"), ["Mine"])
        self.assertEqual(self.names(ids="x,"), [])

    def test_an_account_on_two_organisations_is_one_row(self):
        self.account("Shared", customers=[self.pizza, self.taco])
        self.assertEqual(self.names(user=self.admin), ["Shared"])

    def test_search_matches_name_or_id(self):
        east = self.account("Pizza East")
        self.account("Other")
        self.assertEqual(self.names(search="east"), ["Pizza East"])
        self.assertIn("Pizza East", self.names(search=str(east.pk)))

    def test_organisation_filter_reads_only_openable_organisations(self):
        self.account("Pizza one")
        self.account("Pool", customers=[self.taco], owner=None)
        self.assertEqual(self.names(organisation=str(self.pizza.pk)), ["Pizza one"])
        # Carl may open Pool but not Taco Bell: naming Taco Bell finds nothing,
        # so the filter cannot reveal what Taco Bell holds.
        self.assertEqual(self.names(organisation=str(self.taco.pk)), [])
        self.assertEqual(self.names(organisation=f"{self.pizza.pk},{self.taco.pk}"), ["Pizza one"])
        self.assertEqual(self.names(user=self.admin, organisation=str(self.taco.pk)), ["Pool"])

    def test_owner_filter(self):
        self.account("Carl's")
        self.account("Dana's", owner=self.other)
        self.account("Nobody's", owner=None)
        self.assertEqual(self.names(owner=str(self.other.pk)), ["Dana's"])
        self.assertEqual(self.names(owner="unassigned"), ["Nobody's"])

    def test_lifecycle_filter_churn_is_an_ordinary_stage(self):
        self.account("Live", lifecycle_stage="live")
        self.account("Renewal", lifecycle_stage="renewal")
        self.account("Churn stage", lifecycle_stage="churn")
        self.account("Onboarding")
        self.assertEqual(self.names(lifecycle="live,renewal"), ["Live", "Renewal"])
        self.assertEqual(self.names(lifecycle="churn"), ["Churn stage"])
        self.assertEqual(len(self.names()), 4)

    def test_health_bands_share_the_ring_thresholds(self):
        for name, score in (
            ("seven", "7.0"),
            ("six-nine", "6.9"),
            ("four", "4.0"),
            ("three-nine", "3.9"),
        ):
            self.account(name, health_score=Decimal(score))
        self.assertEqual(self.names(health="good"), ["seven"])
        self.assertEqual(self.names(health="average"), ["four", "six-nine"])
        self.assertEqual(self.names(health="poor,good"), ["seven", "three-nine"])

    def test_renews_within_includes_overdue_and_the_churn_stage(self):
        self.account("Overdue", renewal_date=self.today - timedelta(days=5))
        self.account("Ninety", renewal_date=self.today + timedelta(days=90))
        self.account("Ninety-one", renewal_date=self.today + timedelta(days=91))
        self.account(
            "Churn stage", lifecycle_stage="churn", renewal_date=self.today + timedelta(days=1)
        )
        self.account("Never")
        self.assertEqual(self.names(renews_within="90"), ["Churn stage", "Ninety", "Overdue"])

    def test_nps_bands_by_sign(self):
        self.account("Promoter", nps_score=50)
        self.account("Passive", nps_score=0)
        self.account("Detractor", nps_score=-10)
        self.account("Unscored")
        self.assertEqual(self.names(nps="promoter"), ["Promoter"])
        self.assertEqual(self.names(nps="passive"), ["Passive"])
        self.assertEqual(self.names(nps="detractor"), ["Detractor"])


class AccountRenewingQTests(AccountPortfolioFixture):
    def test_the_rule(self):
        self.account("Due", renewal_date=self.today + timedelta(days=30))
        self.account("Later", renewal_date=self.today + timedelta(days=31))
        queryset = book.filtered_queryset(self.csm, parse_params({}), today=self.today)
        names = [a.name for a in queryset.filter(book.account_renewing_q(30, today=self.today))]
        self.assertEqual(names, ["Due"])


class FilterOptionsTests(AccountPortfolioFixture):
    def test_options_are_scoped_like_the_rows(self):
        self.account("Mine", lifecycle_stage="live")
        self.account("Dana's under Pizza", owner=self.other, lifecycle_stage="renewal")
        self.account("Pool", customers=[self.taco], owner=None, lifecycle_stage="live")
        self.account("Hidden", customers=[self.taco], owner=self.other, lifecycle_stage="expansion")
        options = book.filter_options(self.csm)
        self.assertEqual(set(options), {"organisations", "owners", "lifecycles"})
        # Pool is Carl's to see, but Taco Bell is not his to open.
        self.assertEqual(
            options["organisations"], [{"value": str(self.pizza.pk), "name": "Pizza Hut"}]
        )
        self.assertEqual(
            options["owners"],
            [
                {"value": str(self.csm.pk), "name": "Carl CSM"},
                {"value": str(self.other.pk), "name": "Dana CSM"},
                {"value": "unassigned", "name": "Unassigned"},
            ],
        )
        self.assertEqual(
            options["lifecycles"],
            [{"value": "live", "name": "Live"}, {"value": "renewal", "name": "Renewal"}],
        )
        admin = book.filter_options(self.admin)
        self.assertEqual(
            [row["name"] for row in admin["organisations"]], ["Pizza Hut", "Taco Bell"]
        )

    def test_owners_are_only_from_the_viewers_own_organisation(self):
        outsider = User.objects.create_user(
            email="olga@globex.io",
            password="supersecret1",
            name="Olga",
            organisation=self.other_org,
            role=User.Role.CSM,
        )
        self.account("Bad import", owner=outsider)
        names = [row["name"] for row in book.filter_options(self.admin)["owners"]]
        self.assertNotIn("Olga", names)
```

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `venv/bin/python manage.py test services.accounts_portfolio.tests.test_book --noinput`
Expected: `ImportError: cannot import name 'book' from 'services.accounts_portfolio'`.

- [ ] **Step 4: Write `book.py` (the scope and the options)**

`services/accounts_portfolio/book.py`:

```python
"""The Accounts portfolio's book: the viewer's accounts, narrowed by the
page's filters, with every signal a row shows.

**Visibility first.** Everything starts from `visible_accounts(user)`; a raw
`?ids=`, `?owner=` or `?organisation=` can only narrow that, never widen it.
Whatever is counted from records (urgent tickets) is then read under that
record's own rule.

**No archive, no churn.** Accounts have neither, so unlike the Organizations
book nothing is hidden by default: the scope is every visible account, and
the Churn lifecycle stage is an ordinary stage. A parent organisation's own
archive or churn does not hide its accounts, as on `/accounts/` today.

**Reuse.** The health bands (`health_q`), the NPS bands (`NPS_Q`), the signal
(`signal_for`), the snapshot read (`snapshot_history`), the sparkline
(`health_trend`) and `triage` are Organizations' and the dashboard's own.
The account's own: the renewal rule (`organizations.book.renewing_q` also
excludes churned customers through `Customer.churn_date`, which an account
does not have), the urgent-ticket count (an account's own tickets, not a
company's fan-out), ARR as stored (see `AccountStatsView`: an account's `arr`
is in the workspace's currency), and the linked organisations.
"""

from datetime import timedelta

from django.db.models import CharField, Q
from django.db.models.functions import Cast

from services.customers.models import Customer
from services.customers.scoping import visible_accounts, visible_customers
from services.organizations.book import NPS_Q, health_q

from .params import AccountPortfolioParams


def account_renewing_q(days, *, today):
    """Renews within `days`, overdue included: the `renews_within` filter's
    rule, and (over `renewal_days`, in `shape.build_summary`) the Renewing
    tile's, so clicking the tile lists exactly the N it showed."""
    return Q(renewal_date__isnull=False, renewal_date__lte=today + timedelta(days=days))


def filtered_queryset(user, params: AccountPortfolioParams, *, today):
    # SOC2:AUTH-02 record visibility comes first; filters only narrow it
    accounts = visible_accounts(user)

    if params.ids is not None:
        if not params.ids:
            return accounts.none()
        accounts = accounts.filter(pk__in=params.ids)

    if params.search:
        accounts = accounts.annotate(id_as_text=Cast("id", CharField())).filter(
            Q(name__icontains=params.search) | Q(id_as_text__icontains=params.search)
        )

    if params.organisations:
        # SOC2:AUTH-02 an organisation the viewer cannot open narrows to
        # nothing, so the filter cannot reveal which accounts it holds
        openable = visible_customers(user).filter(pk__in=params.organisations)
        accounts = accounts.filter(customers__in=openable)

    if params.owner == "unassigned":
        accounts = accounts.filter(owner__isnull=True)
    elif params.owner is not None:
        accounts = accounts.filter(owner_id=params.owner)

    if params.lifecycles:
        accounts = accounts.filter(lifecycle_stage__in=params.lifecycles)

    if params.health:
        bands = Q()
        for category in params.health:
            bands |= health_q(category)
        accounts = accounts.filter(bands)

    if params.renews_within is not None:
        accounts = accounts.filter(account_renewing_q(params.renews_within, today=today))

    if params.nps:
        accounts = accounts.filter(NPS_Q[params.nps])

    return accounts


def filter_options(user):
    """The filter sheet's choices, scoped exactly as the rows are: the
    organisations the viewer may open that hold one of their visible
    accounts, the owners of those accounts, and the stages present. Three
    queries, whatever the book's size."""
    # SOC2:AUTH-02 options are scoped to the viewer's own visible accounts
    accounts = visible_accounts(user).order_by()
    # SOC2:AUTH-02 an organisation is offered only if the viewer may open it
    organisations = (
        visible_customers(user)
        .filter(accounts__in=accounts)
        .order_by("name", "id")
        .values_list("id", "name")
        .distinct()
    )
    # SOC2:AUTH-02 owners only from the viewer's own organisation: the
    # serializer enforces it on write, but a bad import or seed row must not
    # put another tenant's name in this menu.
    owners = list(
        accounts.filter(Q(owner__isnull=True) | Q(owner__organisation=user.organisation))
        .values_list("owner_id", "owner__name")
        .distinct()
    )
    named = sorted(
        ((pk, name) for pk, name in owners if pk is not None),
        key=lambda row: (row[1] or "").casefold(),
    )
    stages = set(accounts.values_list("lifecycle_stage", flat=True).distinct())
    return {
        "organisations": [{"value": str(pk), "name": name} for pk, name in organisations],
        "owners": [{"value": str(pk), "name": name} for pk, name in named]
        + (
            [{"value": "unassigned", "name": "Unassigned"}]
            if any(pk is None for pk, _name in owners)
            else []
        ),
        "lifecycles": [
            {"value": value, "name": label}
            for value, label in Customer.LifecycleStage.choices
            if value in stages
        ],
    }
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `venv/bin/python manage.py test services.accounts_portfolio.tests.test_book --noinput`
Expected: `OK` (13 tests).

- [ ] **Step 6: Commit**

```bash
git add services/accounts_portfolio
git commit -m "$(cat <<'EOF'
feat(accounts): the portfolio's filtered book and filter options

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Row signals from the shared code

**Files:**
- Modify: `services/accounts_portfolio/book.py`
- Test: `services/accounts_portfolio/tests/test_signals.py` (new)

**Interfaces:**
- Consumes:
  - `filtered_queryset` (Task 3).
  - From Task 1: `health_trend`, `snapshot_history(..., parent="account")`, and `signal_for(*, churned, renewal_days, risk, urgent_tickets)` from `services.organizations.book`.
  - `triage(*, health_category, csm_pulse, ai_pulse, renewal_date, history, today) -> Triage` from `services.customers.triage`.
  - `last_account_contact_annotation()` from `services.customers.contact`.
  - `visible_tickets(user, queryset)` from `services.customers.personal`.
  - `SUPPORT_PRIORITIES` from `services.attention.rules`.
  - `CustomerHealthView.DEFAULT_HISTORY_MONTHS` (12) from `services.customers.views`.
- Produces:
  - The dataclass `AccountEntry(account, arr: float, trend: list[float], triage: Triage, last_touch_days: int | None, renewal_days: int | None, urgent_tickets: int, signal: dict | None, organisations: list[tuple[int, str]])`.
  - The dataclass `AccountPortfolio(entries: list[AccountEntry], organisation)`.
  - `account_urgent_ticket_counts(user, ids) -> Counter[int, int]`.
  - `linked_organisations(user, ids) -> defaultdict[int, list[tuple[int, str]]]`.
  - `load_portfolio(user, params, *, today) -> AccountPortfolio`.

- [ ] **Step 1: Write the failing tests**

`services/accounts_portfolio/tests/test_signals.py`:

```python
from datetime import timedelta
from decimal import Decimal

from services.accounts_portfolio import book
from services.accounts_portfolio.params import parse_params
from services.accounts_portfolio.tests.fixtures import AccountPortfolioFixture
from services.customers.contact import last_contact_by_account
from services.customers.models import Activity, HealthSnapshot, Ticket
from services.customers.triage import triage


class AccountSignalTests(AccountPortfolioFixture):
    def entry(self, account, user=None, **query):
        portfolio = book.load_portfolio(user or self.csm, parse_params(query), today=self.today)
        return next(e for e in portfolio.entries if e.account.pk == account.pk)

    def snapshot(self, account, months, score):
        return HealthSnapshot.objects.create(
            account=account,
            captured_on=self.today - timedelta(days=30 * months),
            health_score=Decimal(score),
        )

    def ticket(self, number, *, account=None, customer=None, **fields):
        values = {
            "status": Ticket.Status.OPEN,
            "priority": Ticket.Priority.HIGH,
            "opened_at": self.today,
            **fields,
        }
        return Ticket.objects.create(
            account=account, customer=customer, ticket_number=number, title="Down", **values
        )

    def test_arr_is_the_stored_figure(self):
        account = self.account("Plain", arr=Decimal("69600.50"))
        self.assertEqual(self.entry(account).arr, 69600.5)

    def test_trend_is_the_accounts_snapshots_then_todays_score(self):
        account = self.account("Falling", health_score=Decimal("4.9"))
        for months, score in (
            (8, "9.0"),
            (5, "6.2"),
            (4, "5.8"),
            (3, "5.5"),
            (2, "5.1"),
            (1, "5.0"),
        ):
            self.snapshot(account, months, score)
        # The organisation's own snapshots are not the account's.
        HealthSnapshot.objects.create(
            customer=self.pizza,
            captured_on=self.today - timedelta(days=30),
            health_score=Decimal("1.0"),
        )
        self.assertEqual(self.entry(account).trend, [6.2, 5.8, 5.5, 5.1, 5.0, 4.9])

    def test_risk_is_triage_over_the_twelve_month_history(self):
        account = self.account(
            "Slipping",
            health_score=Decimal("3.0"),
            csm_pulse_score=4,
            ai_pulse_value=2,
            renewal_date=self.today + timedelta(days=60),
        )
        for months, score in ((3, "8.0"), (2, "5.0"), (1, "3.0")):
            self.snapshot(account, months, score)
        expected = triage(
            health_category="poor",
            csm_pulse=4,
            ai_pulse=2,
            renewal_date=account.renewal_date,
            history=["good", "average", "poor"],
            today=self.today,
        )
        entry = self.entry(account)
        self.assertEqual(entry.triage, expected)
        self.assertEqual(entry.signal, {"kind": "risk", "label": f"Risk {expected.score}"})

    def test_overdue_renewal_beats_risk(self):
        account = self.account(
            "Late", health_score=Decimal("2.0"), renewal_date=self.today - timedelta(days=1)
        )
        entry = self.entry(account)
        self.assertEqual(entry.renewal_days, -1)
        self.assertEqual(entry.signal, {"kind": "renewal_overdue", "label": "Renewal overdue"})

    def test_a_churn_stage_account_still_signals(self):
        """No churn on accounts: the stage is only a stage, so a late renewal
        is still called out (Organizations would say nothing for a churned
        customer)."""
        account = self.account(
            "Stage only", lifecycle_stage="churn", renewal_date=self.today - timedelta(days=3)
        )
        self.assertEqual(self.entry(account).signal["kind"], "renewal_overdue")

    def test_last_touch_is_activity_trackings_account_rule(self):
        touched = self.account("Touched")
        silent = self.account("Silent")
        Activity.objects.create(
            account=touched,
            type=Activity.ActivityType.OTHER,
            occurred_at=self.today - timedelta(days=4),
        )
        # Contact logged on the organisation is the organisation's.
        Activity.objects.create(
            customer=self.pizza, type=Activity.ActivityType.OTHER, occurred_at=self.today
        )
        self.assertEqual(self.entry(touched).last_touch_days, 4)
        self.assertEqual(
            last_contact_by_account([touched.pk]), {touched.pk: self.today - timedelta(days=4)}
        )
        self.assertIsNone(self.entry(silent).last_touch_days)

    def test_urgent_tickets_are_the_accounts_own_under_the_department_rule(self):
        account = self.account("Busy")
        self.ticket("T-1", account=account)
        self.ticket("T-2", account=account, priority=Ticket.Priority.CRITICAL)
        self.ticket("T-3", account=account, priority=Ticket.Priority.LOW)
        self.ticket("T-4", account=account, status="resolved")
        self.ticket("T-5", account=account, department="engineering")
        self.ticket("T-6", customer=self.pizza)
        entry = self.entry(account)
        # Carl works in CS: the engineering ticket is not his to read.
        self.assertEqual(entry.urgent_tickets, 2)
        self.assertEqual(entry.signal, {"kind": "tickets", "label": "2 open tickets"})
        # Leadership reads every department.
        self.assertEqual(self.entry(account, user=self.admin).urgent_tickets, 3)

    def test_linked_organisations_name_only_what_the_viewer_may_open(self):
        pool = self.account("Pool", customers=[self.taco, self.pizza], owner=None)
        self.assertEqual(self.entry(pool).organisations, [(self.pizza.pk, "Pizza Hut")])
        self.assertEqual(
            self.entry(pool, user=self.admin).organisations,
            [(self.pizza.pk, "Pizza Hut"), (self.taco.pk, "Taco Bell")],
        )
        lone = self.account("Lone", customers=[self.taco], owner=None)
        self.assertEqual(self.entry(lone).organisations, [])

    def test_the_portfolio_carries_the_tenant(self):
        portfolio = book.load_portfolio(self.csm, parse_params({}), today=self.today)
        self.assertEqual(portfolio.organisation, self.org)
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `venv/bin/python manage.py test services.accounts_portfolio.tests.test_signals --noinput`
Expected: `AttributeError: module 'services.accounts_portfolio.book' has no attribute 'load_portfolio'`.

- [ ] **Step 3: Add the entries and `load_portfolio` to `book.py`**

Replace the import block at the top of `services/accounts_portfolio/book.py` (from `from datetime import timedelta` through `from .params import AccountPortfolioParams`) with:

```python
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import timedelta

from django.db.models import CharField, Count, Q
from django.db.models.functions import Cast

from services.attention.rules import SUPPORT_PRIORITIES
from services.customers.contact import last_account_contact_annotation
from services.customers.models import Account, Customer, Ticket, health_category_for
from services.customers.personal import visible_tickets
from services.customers.scoping import visible_accounts, visible_customers
from services.customers.triage import Triage, triage
from services.customers.views import CustomerHealthView
from services.organizations.book import (
    NPS_Q,
    health_q,
    health_trend,
    signal_for,
    snapshot_history,
)

from .params import AccountPortfolioParams
```

Then add this between `filtered_queryset` and `filter_options`:

```python
@dataclass
class AccountEntry:
    account: Account
    #: `Account.arr` as stored: already in the workspace's currency.
    arr: float
    trend: list[float]
    triage: Triage
    last_touch_days: int | None
    renewal_days: int | None
    urgent_tickets: int
    signal: dict | None
    #: The linked organisations the viewer may open, lowest id first, as
    #: `(id, name)`: the row names the first and counts the rest.
    organisations: list[tuple[int, str]]


@dataclass
class AccountPortfolio:
    entries: list[AccountEntry]
    organisation: object


def account_urgent_ticket_counts(user, ids):
    """Open High/Critical tickets filed on each account — the attention
    list's support priorities, read under the department rule. Only the
    account's own tickets: one filed on its organisation belongs to the
    organisation. One query."""
    if not ids:
        return Counter()
    # SOC2:AUTH-02 tickets are read department-wise; `ids` are visible accounts
    tickets = visible_tickets(user, Ticket.objects.filter(account_id__in=ids))
    rows = (
        tickets.filter(priority__in=SUPPORT_PRIORITIES)
        .exclude(status__in=Ticket.RESOLVED_STATUSES)
        .order_by()
        .values("account_id")
        .annotate(n=Count("id"))
    )
    return Counter({row["account_id"]: row["n"] for row in rows})


def linked_organisations(user, ids):
    """Each account's linked organisations the viewer may open, lowest id
    first, as `(id, name)` — one query through the M2M's own table. One the
    viewer cannot open is left out, so neither the row's name, its "+N" nor
    the Profile panel discloses it."""
    linked = defaultdict(list)
    if not ids:
        return linked
    # SOC2:AUTH-02 only organisations the viewer may open are named
    rows = (
        Account.customers.through.objects.filter(
            account_id__in=ids, customer__in=visible_customers(user)
        )
        .order_by("customer_id")
        .values_list("account_id", "customer_id", "customer__name")
    )
    for account_id, customer_id, name in rows:
        linked[account_id].append((customer_id, name))
    return linked


def _entry(account, *, today, snapshots, urgent, organisations):
    # CustomerHealthRowSerializer._triage's own call, over the same window.
    result = triage(
        health_category=account.health_category,
        csm_pulse=account.csm_pulse_score,
        ai_pulse=account.ai_pulse_value,
        renewal_date=account.renewal_date,
        history=[health_category_for(score) for _captured_on, score in snapshots],
        today=today,
    )
    last_touch = account._last_touch_on
    renewal_days = None if account.renewal_date is None else (account.renewal_date - today).days
    return AccountEntry(
        account=account,
        arr=float(account.arr),
        trend=health_trend(snapshots, account.health_score, today=today),
        triage=result,
        last_touch_days=None if last_touch is None else (today - last_touch).days,
        renewal_days=renewal_days,
        urgent_tickets=urgent,
        # Accounts have no churn, so nothing mutes the signal.
        signal=signal_for(
            churned=False, renewal_days=renewal_days, risk=result.score, urgent_tickets=urgent
        ),
        organisations=organisations,
    )


def load_portfolio(user, params: AccountPortfolioParams, *, today):
    """The whole filtered book with its signals, in a fixed number of
    queries: the accounts (last-touch subquery, owner joined), their
    snapshots, the urgent tickets and the linked organisations."""
    organisation = user.organisation
    earliest = today - timedelta(days=31 * CustomerHealthView.DEFAULT_HISTORY_MONTHS)
    accounts = list(
        filtered_queryset(user, params, today=today)
        .annotate(_last_touch_on=last_account_contact_annotation())
        .select_related("owner")
    )
    ids = [account.pk for account in accounts]
    snapshots = snapshot_history(ids, since=earliest, parent="account")
    urgent = account_urgent_ticket_counts(user, ids)
    linked = linked_organisations(user, ids)
    entries = [
        _entry(
            account,
            today=today,
            snapshots=snapshots.get(account.pk, []),
            urgent=urgent.get(account.pk, 0),
            organisations=linked.get(account.pk, []),
        )
        for account in accounts
    ]
    return AccountPortfolio(entries=entries, organisation=organisation)
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `venv/bin/python manage.py test services.accounts_portfolio.tests.test_signals services.accounts_portfolio.tests.test_book --noinput`
Expected: `OK` (22 tests).

- [ ] **Step 5: Commit**

```bash
git add services/accounts_portfolio
git commit -m "$(cat <<'EOF'
feat(accounts): row signals from the dashboard's own code

Trend, triage risk, last touch, the account's own urgent tickets under
the department rule, and the linked organisations the viewer may open.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: The row, its panels, and every field

**Files:**
- Create: `services/accounts_portfolio/rows.py`, `services/accounts_portfolio/fields.py`
- Test: `services/accounts_portfolio/tests/test_rows.py`

**Interfaces:**
- Consumes:
  - `AccountEntry` and `load_portfolio` (Task 4).
  - From Task 1: `initials`, `iso`, `number`, `person` and `pulse_payload` from `services.organizations.rows`.
  - `Field(id, label, value)` from `services.organizations.fields`.
- Produces:
  - `details_payload(account, organisations) -> dict` with the groups `commercial`, `voice`, `profile` and `history`.
  - `row_payload(entry: AccountEntry) -> dict`.
  - `FIELDS: tuple[Field, ...]`, 24 columns.

- [ ] **Step 1: Write the failing tests**

`services/accounts_portfolio/tests/test_rows.py`:

```python
from datetime import timedelta
from decimal import Decimal

from django.test import SimpleTestCase

from services.accounts_portfolio import book, rows
from services.accounts_portfolio.fields import FIELDS
from services.accounts_portfolio.params import parse_params
from services.accounts_portfolio.tests.fixtures import AccountPortfolioFixture
from services.customers.models import Account

#: Every Account field, and the export column that carries it. The spec's
#: promise is that none is lost; a new model field fails this until it has a
#: column (and a place in the row or panels).
COLUMN_FOR_MODEL_FIELD = {
    "id": "revenactId",
    "customers": "organizations",
    "name": "account",
    "domain": "domain",
    "industry": "industry",
    "address": "address",
    "email": "email",
    "phone": "phone",
    "owner": "owner",
    "created_at": "createdDate",
    "updated_at": "modifiedDate",
    "lifecycle_stage": "lifecycleStage",
    "health_score": "health",
    "pulse": "pulse",
    "ai_pulse_value": "aiPulseValue",
    "ai_pulse_reason": "aiPulseReason",
    "csm_pulse_score": "csmPulseScore",
    "csm_pulse_modified_at": "csmPulseModifiedAt",
    "pulse_recorded_on": "pulseRecordedOn",
    "nps_score": "nps",
    "csat_score": "csatScore",
    "renewal_date": "renewalDate",
    "arr": "arr",
}


class FieldCoverageTests(SimpleTestCase):
    def test_every_account_field_has_a_column(self):
        model_fields = {f.name for f in Account._meta.concrete_fields} | {
            f.name for f in Account._meta.many_to_many
        }
        self.assertEqual(model_fields, set(COLUMN_FOR_MODEL_FIELD))
        ids = [field.id for field in FIELDS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertLessEqual(set(COLUMN_FOR_MODEL_FIELD.values()), set(ids))
        # The derived AI pulse label is the one column with no field of its own.
        self.assertEqual(set(ids) - set(COLUMN_FOR_MODEL_FIELD.values()), {"aiPulseScore"})


class RowTests(AccountPortfolioFixture):
    def setUp(self):
        super().setUp()
        self.emea = self.account(
            "Pizza Hut EMEA",
            customers=[self.pizza, self.taco],
            domain="emea.pizzahut.example",
            industry="Food",
            address="1 Main St, London",
            email="emea@pizzahut.example",
            phone="+44 20 0000",
            lifecycle_stage="live",
            health_score=Decimal("4.9"),
            pulse=[1, 2, 2],
            ai_pulse_value=1,
            ai_pulse_reason="Quiet since the outage.",
            csm_pulse_score=3,
            nps_score=-80,
            csat_score=Decimal("72.50"),
            renewal_date=self.today - timedelta(days=47),
            arr=Decimal("69600"),
            pulse_recorded_on=self.today,
        )

    def row(self, account=None, user=None):
        account = account or self.emea
        portfolio = book.load_portfolio(user or self.admin, parse_params({}), today=self.today)
        entry = next(e for e in portfolio.entries if e.account.pk == account.pk)
        return rows.row_payload(entry)

    def test_the_header(self):
        row = self.row()
        self.assertEqual(
            set(row),
            {
                "id", "name", "initials", "owner", "lifecycle", "health", "renewal", "arr",
                "risk", "pulse", "last_touch_days", "urgent_tickets", "signal",
                "organisation", "extra_organisations", "details",
            },
        )  # fmt: skip
        self.assertEqual(
            (row["id"], row["name"], row["initials"]), (self.emea.pk, "Pizza Hut EMEA", "PH")
        )
        self.assertEqual(row["owner"], {"id": self.csm.pk, "name": "Carl CSM"})
        self.assertEqual(row["lifecycle"], {"value": "live", "label": "Live"})
        self.assertEqual(row["health"], {"score": 4.9, "category": "average", "trend": [4.9]})
        self.assertEqual(
            row["renewal"],
            {"date": (self.today - timedelta(days=47)).isoformat(), "days": -47},
        )
        self.assertEqual(row["arr"], 69600.0)
        self.assertEqual(set(row["risk"]), {"score", "direction"})
        self.assertEqual(
            row["pulse"],
            {
                "csm": 3,
                "ai": 1,
                "ai_category": "high_risk",
                "ai_label": "High Risk",
                "reason": "Quiet since the outage.",
                "history": [1, 2, 2],
                "disagree": True,
            },
        )
        self.assertIsNone(row["last_touch_days"])
        self.assertEqual(row["urgent_tickets"], 0)
        self.assertEqual(row["signal"], {"kind": "renewal_overdue", "label": "Renewal overdue"})
        self.assertEqual(row["organisation"], {"id": self.pizza.pk, "name": "Pizza Hut"})
        self.assertEqual(row["extra_organisations"], 1)

    def test_the_panels_hold_the_other_fields(self):
        details = self.row()["details"]
        self.assertEqual(set(details), {"commercial", "voice", "profile", "history"})
        self.assertEqual(
            details["commercial"],
            {"arr": 69600.0, "renewal_date": (self.today - timedelta(days=47)).isoformat()},
        )
        self.assertEqual(
            details["voice"],
            {"nps_score": -80, "csat_score": 72.5, "ai_pulse_reason": "Quiet since the outage."},
        )
        self.assertEqual(
            details["profile"],
            {
                "revenact_id": self.emea.pk,
                "domain": "emea.pizzahut.example",
                "industry": "Food",
                "email": "emea@pizzahut.example",
                "phone": "+44 20 0000",
                "address": "1 Main St, London",
                "organisations": [
                    {"id": self.pizza.pk, "name": "Pizza Hut"},
                    {"id": self.taco.pk, "name": "Taco Bell"},
                ],
            },
        )
        self.emea.refresh_from_db()
        self.assertEqual(
            details["history"],
            {
                "created_at": self.emea.created_at.isoformat(),
                "updated_at": self.emea.updated_at.isoformat(),
                "pulse_recorded_on": self.today.isoformat(),
                "csm_pulse_modified_at": None,
            },
        )

    def test_an_account_whose_organisations_are_all_closed_to_the_viewer(self):
        pool = self.account("Pool", customers=[self.taco], owner=None)
        row = self.row(pool, user=self.csm)
        self.assertIsNone(row["organisation"])
        self.assertEqual(row["extra_organisations"], 0)
        self.assertEqual(row["details"]["profile"]["organisations"], [])
        self.assertIsNone(row["owner"])

    def test_the_export_fields_read_the_row(self):
        values = {field.id: field.value(self.row()) for field in FIELDS}
        self.assertEqual(values["account"], "Pizza Hut EMEA")
        self.assertEqual(values["organizations"], "Pizza Hut; Taco Bell")
        self.assertEqual(values["owner"], "Carl CSM")
        self.assertEqual(values["pulse"], "1 2 2")
        self.assertEqual(values["aiPulseScore"], "High Risk")
        self.assertEqual(values["arr"], 69600.0)
        self.assertEqual(values["nps"], -80)
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `venv/bin/python manage.py test services.accounts_portfolio.tests.test_rows --noinput`
Expected: `ImportError: cannot import name 'rows' from 'services.accounts_portfolio'`.

- [ ] **Step 3: Write `rows.py` and `fields.py`**

`services/accounts_portfolio/rows.py`:

```python
"""One Accounts portfolio row as the page reads it: the header fields plus the
opened row's four panels (`details`). Every Account field is in one of them —
`fields.FIELDS` names where, and tests/test_rows.py pins it."""

from services.organizations.rows import initials, iso, number, person, pulse_payload


def details_payload(account, organisations):
    """The opened row. `organisations` are the linked ones the viewer may
    open, `(id, name)` pairs, lowest id first. `csm_pulse_modified_at` sits in
    History: the spec's panels do not name it, and every field must appear
    once. Profile values are the account's own; the frontend's fallback to
    the parent's contact details (see `Account`) is not applied here."""
    return {
        "commercial": {
            "arr": number(account.arr),
            "renewal_date": iso(account.renewal_date),
        },
        "voice": {
            "nps_score": account.nps_score,
            "csat_score": number(account.csat_score),
            "ai_pulse_reason": account.ai_pulse_reason,
        },
        "profile": {
            "revenact_id": account.pk,
            "domain": account.domain,
            "industry": account.industry,
            "email": account.email,
            "phone": account.phone,
            "address": account.address,
            "organisations": [{"id": pk, "name": name} for pk, name in organisations],
        },
        "history": {
            "created_at": iso(account.created_at),
            "updated_at": iso(account.updated_at),
            "pulse_recorded_on": iso(account.pulse_recorded_on),
            "csm_pulse_modified_at": iso(account.csm_pulse_modified_at),
        },
    }


def row_payload(entry):
    account = entry.account
    first = entry.organisations[0] if entry.organisations else None
    return {
        "id": account.pk,
        "name": account.name,
        "initials": initials(account.name),
        "owner": person(account.owner),
        "lifecycle": {
            "value": account.lifecycle_stage,
            "label": account.get_lifecycle_stage_display(),
        },
        "health": {
            "score": float(account.health_score),
            "category": account.health_category,
            "trend": entry.trend,
        },
        "renewal": {"date": iso(account.renewal_date), "days": entry.renewal_days},
        "arr": entry.arr,
        "risk": {"score": entry.triage.score, "direction": entry.triage.direction},
        "pulse": pulse_payload(account),
        "last_touch_days": entry.last_touch_days,
        "urgent_tickets": entry.urgent_tickets,
        "signal": entry.signal,
        # "Organisation · owner · …": the first the viewer may open, then "+N".
        "organisation": None if first is None else {"id": first[0], "name": first[1]},
        "extra_organisations": max(len(entry.organisations) - 1, 0),
        "details": details_payload(account, entry.organisations),
    }
```

`services/accounts_portfolio/fields.py`:

```python
"""Every Account field, named once: the export's columns and the tests' proof
that the portfolio lost none of them (tests/test_rows.py maps each model field
to one of these). `id` is the column's stable key; `label` its CSV header.
ARR is in the workspace's currency, which the export prints beside it."""

from services.organizations.fields import Field


def _detail(group, key):
    return lambda row: row["details"][group][key]


def _organisations(row):
    return "; ".join(item["name"] for item in row["details"]["profile"]["organisations"])


FIELDS = (
    Field("account", "Account", lambda row: row["name"]),
    Field("revenactId", "Revenact ID", _detail("profile", "revenact_id")),
    Field("organizations", "Organizations", _organisations),
    Field("owner", "Owner", lambda row: row["owner"]["name"] if row["owner"] else "Unassigned"),
    Field("lifecycleStage", "Lifecycle Stage", lambda row: row["lifecycle"]["label"]),
    Field("health", "Health", lambda row: row["health"]["score"]),
    Field("pulse", "Pulse", lambda row: " ".join(str(p) for p in row["pulse"]["history"])),
    Field("aiPulseScore", "AI Pulse Score", lambda row: row["pulse"]["ai_label"]),
    Field("aiPulseValue", "AI Pulse Value", lambda row: row["pulse"]["ai"]),
    Field("csmPulseScore", "CSM Pulse Score", lambda row: row["pulse"]["csm"]),
    Field(
        "csmPulseModifiedAt",
        "CSM Pulse Modified At",
        _detail("history", "csm_pulse_modified_at"),
    ),
    Field("aiPulseReason", "AI Pulse Reason", _detail("voice", "ai_pulse_reason")),
    Field("nps", "NPS", _detail("voice", "nps_score")),
    Field("csatScore", "CSAT Score", _detail("voice", "csat_score")),
    Field("renewalDate", "Renewal Date", _detail("commercial", "renewal_date")),
    Field("arr", "ARR", _detail("commercial", "arr")),
    Field("domain", "Domain", _detail("profile", "domain")),
    Field("industry", "Industry", _detail("profile", "industry")),
    Field("email", "Email", _detail("profile", "email")),
    Field("phone", "Phone", _detail("profile", "phone")),
    Field("address", "Address", _detail("profile", "address")),
    Field("createdDate", "Created Date", _detail("history", "created_at")),
    Field("modifiedDate", "Modified Date", _detail("history", "updated_at")),
    Field("pulseRecordedOn", "Pulse Recorded On", _detail("history", "pulse_recorded_on")),
)
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `venv/bin/python manage.py test services.accounts_portfolio.tests.test_rows --noinput`
Expected: `OK` (5 tests).

- [ ] **Step 5: Commit**

```bash
git add services/accounts_portfolio
git commit -m "$(cat <<'EOF'
feat(accounts): the portfolio row, its four panels and every field

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Sort, group, `group_value` and the cursor

**Files:**
- Create: `services/accounts_portfolio/shape.py`
- Test: `services/accounts_portfolio/tests/test_shape.py`

**Interfaces:**
- Consumes:
  - `load_portfolio` and `AccountEntry` (Task 4).
  - `parse_params` and `AccountPortfolioParams` (Task 2).
  - From Task 1: `keyset_page`, `wrap_desc` and `group_rank`, and the existing `renewal_window` and `RENEWAL_WINDOWS`, all from `services.organizations.shape`.
- Produces:
  - `SORT_GETTERS: dict[str, Callable[[AccountEntry], object]]`.
  - `group_key(entry, group) -> tuple[str, str]`.
  - `order_entries(entries, sort_key, descending, group="") -> list[AccountEntry]`.
  - `build_groups(entries, group) -> list[{"key", "label", "count", "arr"}]`.
  - `select(portfolio, params) -> tuple[list[AccountEntry], list[dict]]`.
  - `filter_fingerprint(params) -> str`.
  - `paginate(entries, *, params) -> tuple[list[AccountEntry], str | None]`.

- [ ] **Step 1: Write the failing tests**

`services/accounts_portfolio/tests/test_shape.py`:

```python
from datetime import timedelta
from decimal import Decimal

from services.accounts_portfolio import book, shape
from services.accounts_portfolio.params import parse_params
from services.accounts_portfolio.tests.fixtures import AccountPortfolioFixture


class ShapeFixture(AccountPortfolioFixture):
    """Four accounts under Pizza Hut, read by Alice (who sees every owner):

    | name    | owner | ARR    | health | renewal | stage      |
    | Alpha   | Carl  | 30,000 | 8.0    | +100    | live       |
    | Bravo   | Dana  | 20,000 | 5.0    | +10     | onboarding |
    | Charlie | —     | 10,000 | 3.0    | —       | renewal    |
    | Delta   | Carl  | 0      | 8.0    | −3      | live       |

    Triage: Alpha 8 (renews in 91–180 days), Bravo 40 (Average 22 + renews
    within 90 days 18), Charlie 66 (Poor), Delta 18 (overdue counts as urgent).
    """

    def setUp(self):
        super().setUp()
        self.account(
            "Alpha",
            arr=Decimal("30000"),
            renewal_date=self.today + timedelta(days=100),
            lifecycle_stage="live",
        )
        self.account(
            "Bravo",
            owner=self.other,
            arr=Decimal("20000"),
            health_score=Decimal("5.0"),
            renewal_date=self.today + timedelta(days=10),
            lifecycle_stage="onboarding",
        )
        self.account(
            "Charlie",
            owner=None,
            arr=Decimal("10000"),
            health_score=Decimal("3.0"),
            lifecycle_stage="renewal",
        )
        self.account(
            "Delta",
            arr=Decimal("0"),
            renewal_date=self.today - timedelta(days=3),
            lifecycle_stage="live",
        )

    def portfolio(self, **query):
        return book.load_portfolio(self.admin, parse_params(query), today=self.today)

    def names(self, entries):
        return [entry.account.name for entry in entries]


class OrderTests(ShapeFixture):
    def test_every_sort_both_ways_missing_values_last(self):
        expected = {
            "-arr": ["Alpha", "Bravo", "Charlie", "Delta"],
            "arr": ["Delta", "Charlie", "Bravo", "Alpha"],
            "-risk": ["Charlie", "Bravo", "Delta", "Alpha"],
            "risk": ["Alpha", "Delta", "Bravo", "Charlie"],
            "renewal": ["Delta", "Bravo", "Alpha", "Charlie"],
            "-renewal": ["Alpha", "Bravo", "Delta", "Charlie"],
            "health": ["Charlie", "Bravo", "Alpha", "Delta"],
            "-health": ["Alpha", "Delta", "Bravo", "Charlie"],
            "name": ["Alpha", "Bravo", "Charlie", "Delta"],
            "-name": ["Delta", "Charlie", "Bravo", "Alpha"],
        }
        portfolio = self.portfolio()
        for sort, names in expected.items():
            with self.subTest(sort=sort):
                params = parse_params({"sort": sort})
                ordered = shape.order_entries(portfolio.entries, params.sort_key, params.descending)
                self.assertEqual(self.names(ordered), names)


class GroupTests(ShapeFixture):
    def groups(self, group):
        entries, groups = shape.select(self.portfolio(), parse_params({"group": group}))
        return [(g["key"], g["label"], g["count"], g["arr"]) for g in groups], entries

    def test_health_sections_poor_first(self):
        groups, entries = self.groups("health")
        self.assertEqual(
            groups,
            [
                ("poor", "Poor", 1, 10000.0),
                ("average", "Average", 1, 20000.0),
                ("good", "Good", 2, 30000.0),
            ],
        )
        self.assertEqual(self.names(entries), ["Charlie", "Bravo", "Alpha", "Delta"])

    def test_owner_sections_by_name_unassigned_last(self):
        groups, _entries = self.groups("owner")
        self.assertEqual(
            groups,
            [
                (str(self.csm.pk), "Carl CSM", 2, 30000.0),
                (str(self.other.pk), "Dana CSM", 1, 20000.0),
                ("unassigned", "Unassigned", 1, 10000.0),
            ],
        )

    def test_lifecycle_sections_in_stage_order(self):
        groups, _entries = self.groups("lifecycle")
        self.assertEqual(
            groups,
            [
                ("onboarding", "Onboarding", 1, 20000.0),
                ("live", "Live", 2, 30000.0),
                ("renewal", "Renewal", 1, 10000.0),
            ],
        )

    def test_renewal_windows(self):
        groups, _entries = self.groups("renewal")
        self.assertEqual(
            groups,
            [
                ("overdue", "Overdue", 1, 0.0),
                ("30", "Within 30 days", 1, 20000.0),
                ("180", "91–180 days", 1, 30000.0),
                ("none", "No renewal date", 1, 10000.0),
            ],
        )

    def test_group_value_is_one_board_column_with_every_header(self):
        params = parse_params({"group": "lifecycle", "group_value": "live"})
        entries, groups = shape.select(self.portfolio(), params)
        self.assertEqual(self.names(entries), ["Alpha", "Delta"])
        self.assertEqual(len(groups), 3)
        params = parse_params({"group": "lifecycle", "group_value": "expansion"})
        self.assertEqual(shape.select(self.portfolio(), params)[0], [])


class PagingTests(ShapeFixture):
    def follow(self, **query):
        seen, cursor = [], ""
        for _ in range(10):
            params = parse_params({**query, "cursor": cursor})
            entries, _groups = shape.select(self.portfolio(**query), params)
            page, cursor = shape.paginate(entries, params=params)
            seen += self.names(page)
            if cursor is None:
                return seen
        self.fail(f"the cursor never ended: {seen}")

    def test_following_the_cursor_reads_every_row_once(self):
        self.assertEqual(self.follow(limit="1"), ["Alpha", "Bravo", "Charlie", "Delta"])
        self.assertEqual(
            self.follow(limit="1", sort="renewal"), ["Delta", "Bravo", "Alpha", "Charlie"]
        )

    def test_following_the_cursor_over_a_grouped_list(self):
        self.assertEqual(
            self.follow(limit="1", group="health"), ["Charlie", "Bravo", "Alpha", "Delta"]
        )

    def test_a_cursor_from_another_list_is_the_first_page(self):
        first = parse_params({"limit": "2"})
        entries, _groups = shape.select(self.portfolio(), first)
        _page, cursor = shape.paginate(entries, params=first)
        changed = parse_params({"limit": "2", "health": "good", "cursor": cursor})
        entries, _groups = shape.select(self.portfolio(health="good"), changed)
        page, _next = shape.paginate(entries, params=changed)
        self.assertEqual(self.names(page), ["Alpha", "Delta"])

    def test_the_fingerprint_reads_multi_value_filters_as_sets(self):
        self.assertEqual(
            shape.filter_fingerprint(parse_params({"health": "poor,good"})),
            shape.filter_fingerprint(parse_params({"health": "good,poor"})),
        )
        self.assertNotEqual(
            shape.filter_fingerprint(parse_params({"organisation": "1"})),
            shape.filter_fingerprint(parse_params({"organisation": "2"})),
        )
        self.assertEqual(
            shape.filter_fingerprint(parse_params({"cursor": "abc", "limit": "5"})),
            shape.filter_fingerprint(parse_params({})),
        )
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `venv/bin/python manage.py test services.accounts_portfolio.tests.test_shape --noinput`
Expected: `ImportError: cannot import name 'shape' from 'services.accounts_portfolio'`.

- [ ] **Step 3: Write `shape.py` (order, groups, paging)**

`services/accounts_portfolio/shape.py`:

```python
"""Ordering, grouping, paging and totals for the Accounts portfolio — pure
functions over the entries `book.load_portfolio` returns. No queries.

The cursor (`keyset_page`), the section order (`group_rank`) and the renewal
windows are Organizations' own. The sort values, group keys and fingerprint
are the account's: they read `entry.account` and the account's filters.
Everything runs over the whole filtered set, so a section header or a tile
never counts only the page.
"""

import hashlib
import json

from services.customers.models import Customer
from services.organizations.shape import (
    RENEWAL_WINDOWS,
    group_rank,
    keyset_page,
    renewal_window,
    wrap_desc,
)

SORT_GETTERS = {
    "risk": lambda entry: entry.triage.score,
    "arr": lambda entry: entry.arr,
    "renewal": lambda entry: entry.account.renewal_date,
    "health": lambda entry: float(entry.account.health_score),
    "name": lambda entry: entry.account.name.casefold(),
}


def _tiebreak(entry):
    return (entry.account.name.casefold(), entry.account.pk)


def group_key(entry, group):
    account = entry.account
    if group == "health":
        category = account.health_category
        return category, Customer.HealthCategory(category).label
    if group == "owner":
        if account.owner_id is None:
            return "unassigned", "Unassigned"
        return str(account.owner_id), account.owner.name
    if group == "lifecycle":
        return account.lifecycle_stage, account.get_lifecycle_stage_display()
    key = renewal_window(entry.renewal_days)
    return key, dict(RENEWAL_WINDOWS)[key]


def _rank(entry, sort_key, descending, group=""):
    """The tuple `order_entries` and the cursor both sort by — Organizations'
    `_rank` shape: section, then missing values last in either direction,
    then the (direction-wrapped) value, then name and id ascending."""
    section = group_rank(*group_key(entry, group), group) if group else ()
    value = SORT_GETTERS[sort_key](entry)
    if value is None:
        return (section, 1, None, _tiebreak(entry))
    return (section, 0, wrap_desc(value, descending), _tiebreak(entry))


def order_entries(entries, sort_key, descending, group=""):
    return sorted(entries, key=lambda entry: _rank(entry, sort_key, descending, group))


def build_groups(entries, group):
    groups = {}
    for entry in entries:
        key, label = group_key(entry, group)
        bucket = groups.setdefault(key, {"key": key, "label": label, "count": 0, "arr": 0.0})
        bucket["count"] += 1
        bucket["arr"] += entry.arr
    ordered = sorted(groups.values(), key=lambda g: group_rank(g["key"], g["label"], group))
    for bucket in ordered:
        bucket["arr"] = round(bucket["arr"], 2)
    return ordered


def select(portfolio, params):
    """The rows in list order — sections first, the chosen sort inside each —
    and the section totals over every row, before `group_value` narrows."""
    entries = order_entries(portfolio.entries, params.sort_key, params.descending, params.group)
    if not params.group:
        return entries, []
    groups = build_groups(entries, params.group)
    if params.group_value is not None:
        entries = [e for e in entries if group_key(e, params.group)[0] == params.group_value]
    return entries, groups


def filter_fingerprint(params):
    """A short hash of everything that decides which rows a list holds and in
    what order, but not `cursor` or `limit`, so a cursor from a list with
    other filters reads the new list from its first page. Multi-value filters
    compare as sets."""
    state = [
        params.search,
        sorted(set(params.organisations)),
        params.owner,
        sorted(set(params.lifecycles)),
        sorted(set(params.health)),
        params.renews_within,
        params.nps,
        None if params.ids is None else sorted(set(params.ids)),
        params.sort,
        params.group,
        params.group_value,
    ]
    digest = hashlib.sha256(json.dumps(state, separators=(",", ":")).encode())
    return digest.hexdigest()[:16]


def paginate(entries, *, params):
    """Organizations' keyset cursor over `select`'s order."""
    sort_key, descending, group = params.sort_key, params.descending, params.group
    return keyset_page(
        entries,
        rank=lambda entry: _rank(entry, sort_key, descending, group),
        cursor=params.cursor,
        limit=params.limit,
        descending=descending,
        fingerprint=filter_fingerprint(params),
        grouped=bool(group),
    )
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `venv/bin/python manage.py test services.accounts_portfolio.tests.test_shape --noinput`
Expected: `OK` (10 tests).

- [ ] **Step 5: Commit**

```bash
git add services/accounts_portfolio
git commit -m "$(cat <<'EOF'
feat(accounts): portfolio sort, groups, board columns and cursor

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: The five tiles and the listing body

**Files:**
- Modify: `services/accounts_portfolio/shape.py`
- Test: `services/accounts_portfolio/tests/test_summary.py` (new)

**Interfaces:**
- Consumes:
  - `select` and `paginate` (Task 6).
  - `row_payload` (Task 5).
  - `RENEWING_WINDOWS` (`(30, 90)`) from `services.organizations.book`.
  - `NPS_BANDS` from `services.organizations.params`.
- Produces:
  - `nps_band(score: int | None) -> str | None`.
  - `build_summary(entries) -> {"health", "nps", "lifecycle", "accounts", "arr", "unconverted_count", "renewing"}`, the Organizations summary shape.
  - `build_listing(portfolio, params, *, filters) -> {"results", "next_cursor", "count", "groups", "summary", "filters", "currency"}`.

- [ ] **Step 1: Write the failing tests**

`services/accounts_portfolio/tests/test_summary.py`:

```python
from datetime import timedelta
from decimal import Decimal

from django.test import SimpleTestCase
from rest_framework.test import APIClient

from services.accounts_portfolio import book, shape
from services.accounts_portfolio.params import parse_params
from services.accounts_portfolio.tests.fixtures import AccountPortfolioFixture


class NpsBandTests(SimpleTestCase):
    def test_by_sign_like_the_filter(self):
        self.assertEqual(shape.nps_band(50), "promoter")
        self.assertEqual(shape.nps_band(0), "passive")
        self.assertEqual(shape.nps_band(-1), "detractor")
        self.assertIsNone(shape.nps_band(None))


class SummaryTests(AccountPortfolioFixture):
    """Four accounts. The tiles must reproduce `/accounts/stats/` (today's
    MetricsPanel) and list exactly their N when clicked."""

    def setUp(self):
        super().setUp()
        self.account(
            "Good",
            lifecycle_stage="live",
            nps_score=50,
            renewal_date=self.today + timedelta(days=20),
        )
        self.account(
            "Average",
            health_score=Decimal("5.0"),
            lifecycle_stage="adoption",
            nps_score=0,
            arr=Decimal("24000"),
            renewal_date=self.today + timedelta(days=60),
        )
        self.account(
            "Poor",
            health_score=Decimal("2.0"),
            lifecycle_stage="renewal",
            nps_score=-40,
            arr=Decimal("6000"),
            renewal_date=self.today - timedelta(days=3),
        )
        self.account(
            "Unscored",
            owner=None,
            lifecycle_stage="churn",
            arr=Decimal("1000"),
            renewal_date=self.today + timedelta(days=120),
        )

    def portfolio(self, user, **query):
        return book.load_portfolio(user, parse_params(query), today=self.today)

    def summary(self, user, **query):
        return shape.build_summary(self.portfolio(user, **query).entries)

    def test_the_numbers(self):
        summary = self.summary(self.admin)
        self.assertEqual(summary["accounts"], 4)
        self.assertEqual(summary["arr"], 43000.0)
        self.assertEqual(summary["unconverted_count"], 0)
        self.assertEqual(summary["renewing"], {"30": 2, "90": 3})
        self.assertEqual(
            summary["nps"], {"promoters": 1, "passives": 1, "detractors": 1, "score": 0}
        )
        self.assertEqual(
            (summary["health"]["good"], summary["health"]["average"], summary["health"]["poor"]),
            (2, 1, 1),
        )
        self.assertEqual(summary["health"]["arr"]["good"], 13000.0)
        self.assertEqual(summary["health"]["mrr"]["average"], 2000.0)
        stages = {row["value"]: row for row in summary["lifecycle"]}
        self.assertEqual(stages["churn"]["count"], 1)
        self.assertEqual(
            stages["kickoff"], {"value": "kickoff", "label": "Kickoff", "count": 0, "arr": 0.0}
        )

    def test_the_tiles_reproduce_account_stats(self):
        for user in (self.admin, self.csm):
            with self.subTest(user=user.name):
                api = APIClient()
                api.force_authenticate(user)
                stats = api.get("/api/v1/accounts/stats/").data
                summary = self.summary(user)
                for category in ("good", "average", "poor"):
                    self.assertEqual(
                        summary["health"][category], stats["health"][category]["count"]
                    )
                    self.assertEqual(
                        summary["health"]["arr"][category], stats["health"][category]["arr"]
                    )
                    self.assertEqual(
                        summary["health"]["mrr"][category], stats["health"][category]["mrr"]
                    )
                self.assertEqual(summary["nps"], stats["nps"])
                self.assertEqual(
                    {
                        row["value"]: {"count": row["count"], "arr": row["arr"]}
                        for row in summary["lifecycle"]
                    },
                    {
                        stage: {"count": bucket["count"], "arr": bucket["arr"]}
                        for stage, bucket in stats["lifecycle"].items()
                    },
                )

    def test_a_clicked_tile_lists_exactly_its_number(self):
        summary = self.summary(self.admin)

        def count(**query):
            return len(self.portfolio(self.admin, **query).entries)

        for days in ("30", "90"):
            self.assertEqual(count(renews_within=days), summary["renewing"][days])
        for band, key in (
            ("promoter", "promoters"),
            ("passive", "passives"),
            ("detractor", "detractors"),
        ):
            self.assertEqual(count(nps=band), summary["nps"][key])
        for category in ("good", "average", "poor"):
            self.assertEqual(count(health=category), summary["health"][category])
        for row in summary["lifecycle"]:
            self.assertEqual(count(lifecycle=row["value"]), row["count"])

    def test_the_listing_body(self):
        params = parse_params({"group": "health", "group_value": "good", "limit": "1"})
        body = shape.build_listing(self.portfolio(self.admin), params, filters={"owners": []})
        self.assertEqual(
            set(body),
            {"results", "next_cursor", "count", "groups", "summary", "filters", "currency"},
        )
        self.assertEqual(body["count"], 2)
        self.assertEqual(len(body["results"]), 1)
        self.assertIsNotNone(body["next_cursor"])
        self.assertEqual(sum(g["count"] for g in body["groups"]), 4)
        self.assertEqual(body["summary"]["accounts"], 4)
        self.assertEqual(body["filters"], {"owners": []})
        self.assertEqual(body["currency"], "USD")
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `venv/bin/python manage.py test services.accounts_portfolio.tests.test_summary --noinput`
Expected: `AttributeError: module 'services.accounts_portfolio.shape' has no attribute 'nps_band'`.

- [ ] **Step 3: Add the summary and the listing to `shape.py`**

Add these imports to `services/accounts_portfolio/shape.py`. Put the `services.organizations.book` and `params` lines in the `services` import block, keeping it alphabetical, and put the relative import last:

```python
from services.organizations.book import RENEWING_WINDOWS
from services.organizations.params import NPS_BANDS

from .rows import row_payload
```

Append to the module:

```python
def nps_band(score):
    """`NPS_Q`'s rule in Python, by sign (`/accounts/stats/`' buckets), so the
    NPS tile and the `nps` filter can never disagree."""
    if score is None:
        return None
    if score > 0:
        return "promoter"
    if score == 0:
        return "passive"
    return "detractor"


def build_summary(entries):
    """The five tiles, over every filtered row, in the Organizations summary's
    shape. Health, NPS and lifecycle are `AccountStatsView`'s arithmetic (ARR
    as stored, MRR = ARR / 12, NPS by sign); renewals are
    `book.account_renewing_q`'s rule over `renewal_days` (overdue in), so a
    clicked tile lists exactly its N. Account ARR is always in the
    workspace's currency, so `unconverted_count` is always 0 — kept so both
    lists' tiles read one shape."""
    categories = Customer.HealthCategory.values
    health = dict.fromkeys(categories, 0)
    health_arr = dict.fromkeys(categories, 0.0)
    health_mrr = dict.fromkeys(categories, 0.0)
    stages = {stage: {"count": 0, "arr": 0.0} for stage in Customer.LifecycleStage.values}
    bands = dict.fromkeys(NPS_BANDS, 0)
    renewing = {str(days): 0 for days in RENEWING_WINDOWS}
    total = 0.0

    for entry in entries:
        account = entry.account
        category = account.health_category
        health[category] += 1
        health_arr[category] += entry.arr
        health_mrr[category] += entry.arr / 12
        stages[account.lifecycle_stage]["count"] += 1
        stages[account.lifecycle_stage]["arr"] += entry.arr
        total += entry.arr
        band = nps_band(account.nps_score)
        if band is not None:
            bands[band] += 1
        for days in RENEWING_WINDOWS:
            if entry.renewal_days is not None and entry.renewal_days <= days:
                renewing[str(days)] += 1

    promoters, detractors = bands["promoter"], bands["detractor"]
    scored = sum(bands.values())
    return {
        "health": {
            **health,
            "arr": {category: round(value, 2) for category, value in health_arr.items()},
            "mrr": {category: round(value, 2) for category, value in health_mrr.items()},
        },
        "nps": {
            "promoters": promoters,
            "passives": bands["passive"],
            "detractors": detractors,
            "score": round((promoters - detractors) / scored * 100) if scored else 0,
        },
        "lifecycle": [
            {
                "value": value,
                "label": label,
                "count": stages[value]["count"],
                "arr": round(stages[value]["arr"], 2),
            }
            for value, label in Customer.LifecycleStage.choices
        ],
        "accounts": len(entries),
        "arr": round(total, 2),
        "unconverted_count": 0,
        "renewing": renewing,
    }


def build_listing(portfolio, params, *, filters):
    """The endpoint's body. `count` is the rows this query pages through
    (after `group_value`); `groups` and `summary` are over the whole filtered
    set, so a Board column's header and the tiles never shrink to a page."""
    entries, groups = select(portfolio, params)
    page, next_cursor = paginate(entries, params=params)
    return {
        "results": [row_payload(entry) for entry in page],
        "next_cursor": next_cursor,
        "count": len(entries),
        "groups": groups,
        "summary": build_summary(portfolio.entries),
        "filters": filters,
        "currency": portfolio.organisation.currency,
    }
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `venv/bin/python manage.py test services.accounts_portfolio.tests.test_summary services.accounts_portfolio.tests.test_shape --noinput`
Expected: `OK` (15 tests).

- [ ] **Step 5: Commit**

```bash
git add services/accounts_portfolio
git commit -m "$(cat <<'EOF'
feat(accounts): the five summary tiles and the listing body

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: `GET /api/v1/accounts/portfolio/`

**Files:**
- Create: `services/accounts_portfolio/views.py`, `services/accounts_portfolio/urls.py`
- Modify: `config/urls.py` (above `path("api/v1/accounts/stats/", …)`)
- Test: `services/accounts_portfolio/tests/test_views.py`

**Interfaces:**
- Consumes:
  - `parse_params` (Task 2).
  - `load_portfolio` and `filter_options` (Tasks 3 and 4).
  - `build_listing` (Task 7).
  - `blind_to_one_account(customer) -> (viewer, seen, hidden)` from `services.customers.tests.test_views`.
- Produces:
  - `AccountPortfolioView` at `/api/v1/accounts/portfolio/`, URL name `accounts-portfolio`.
  - `services/accounts_portfolio/urls.py` with `urlpatterns`, mounted at `api/v1/accounts/`. Tasks 9 and 10 add paths to it.

- [ ] **Step 1: Write the failing tests**

`services/accounts_portfolio/tests/test_views.py`:

```python
from datetime import timedelta
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import resolve
from rest_framework.test import APIClient

from services.accounts.models import User
from services.accounts_portfolio.tests.fixtures import AccountPortfolioFixture
from services.accounts_portfolio.views import AccountPortfolioView
from services.customers.models import Activity, HealthSnapshot, Ticket
from services.customers.tests.test_views import blind_to_one_account
from services.customers.views import AccountListView, AccountStatsView

URL = "/api/v1/accounts/portfolio/"


class PortfolioEndpointTests(AccountPortfolioFixture):
    def get(self, user=None, **query):
        api = APIClient()
        api.force_authenticate(user or self.csm)
        response = api.get(URL, query)
        self.assertEqual(response.status_code, 200, response.content)
        return response.data

    def test_requires_authentication(self):
        self.assertEqual(APIClient().get(URL).status_code, 401)

    def test_the_existing_account_routes_still_resolve(self):
        self.assertIs(resolve(URL).func.view_class, AccountPortfolioView)
        self.assertIs(resolve("/api/v1/accounts/").func.view_class, AccountListView)
        self.assertIs(resolve("/api/v1/accounts/stats/").func.view_class, AccountStatsView)

    def test_response_shape(self):
        self.account("Pizza EMEA")
        body = self.get()
        self.assertEqual(
            set(body),
            {"results", "next_cursor", "count", "groups", "summary", "filters", "currency"},
        )
        self.assertEqual((body["currency"], body["count"], body["next_cursor"]), ("USD", 1, None))
        self.assertEqual(
            set(body["results"][0]),
            {
                "id", "name", "initials", "owner", "lifecycle", "health", "renewal", "arr",
                "risk", "pulse", "last_touch_days", "urgent_tickets", "signal",
                "organisation", "extra_organisations", "details",
            },
        )  # fmt: skip
        self.assertEqual(set(body["filters"]), {"organisations", "owners", "lifecycles"})
        self.assertEqual(
            set(body["summary"]),
            {"health", "nps", "lifecycle", "accounts", "arr", "unconverted_count", "renewing"},
        )

    def test_blind_to_one_account(self):
        viewer, seen, hidden = blind_to_one_account(self.pizza)
        Ticket.objects.create(
            account=hidden,
            ticket_number="T-H",
            title="Down",
            status=Ticket.Status.OPEN,
            priority=Ticket.Priority.HIGH,
            opened_at=self.today,
        )
        body = self.get(viewer)
        self.assertEqual([row["name"] for row in body["results"]], ["Seen"])
        self.assertEqual(body["results"][0]["urgent_tickets"], 0)
        self.assertEqual(body["summary"]["accounts"], 1)
        self.assertEqual(body["filters"]["owners"], [{"value": str(viewer.pk), "name": "Viewer"}])
        grouped = self.get(viewer, group="lifecycle")
        self.assertEqual(sum(group["count"] for group in grouped["groups"]), 1)
        by_id = self.get(viewer, ids=f"{hidden.pk}")
        self.assertEqual(
            (by_id["count"], by_id["results"], by_id["summary"]["accounts"]), (0, [], 0)
        )
        self.assertEqual(self.get(self.admin)["summary"]["accounts"], 2)

    def test_another_tenants_accounts_never_appear(self):
        globex = self.account("Globex EMEA", customers=[self.globex], owner=None)
        self.assertEqual(self.get(self.admin, ids=str(globex.pk))["count"], 0)
        self.assertEqual(self.get(self.admin)["count"], 0)

    def test_unknown_values_are_ignored_not_rejected(self):
        self.account("A")
        self.account("B")
        body = self.get(
            sort="colour",
            group="product",
            health="purple",
            limit="lots",
            renews_within="7",
            nps="happy",
            cursor="%%%",
            owner="someone",
            organisation="x",
            include_churned="1",
        )
        self.assertEqual(body["count"], 2)

    def test_group_value_serves_one_board_column_with_every_header(self):
        self.account("Live one", lifecycle_stage="live")
        self.account("Live two", lifecycle_stage="live")
        self.account("Onboarding", lifecycle_stage="onboarding")
        body = self.get(group="lifecycle", group_value="live")
        self.assertEqual({row["name"] for row in body["results"]}, {"Live one", "Live two"})
        self.assertEqual(body["count"], 2)
        self.assertEqual([g["key"] for g in body["groups"]], ["onboarding", "live"])
        self.assertEqual(body["summary"]["accounts"], 3)

    def test_following_the_cursor_reads_every_row_once(self):
        for i in range(5):
            self.account(f"Div {i}", arr=Decimal(1000 * (i + 1)))
        seen, cursor = [], None
        for _ in range(10):
            body = self.get(limit="2", **({"cursor": cursor} if cursor else {}))
            seen += [row["name"] for row in body["results"]]
            cursor = body["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(seen, ["Div 4", "Div 3", "Div 2", "Div 1", "Div 0"])


class PortfolioQueryCountTests(AccountPortfolioFixture):
    """Every figure is computed over the whole filtered set in a fixed number
    of queries; a per-row query anywhere would make the count grow with the
    book. Ten for an admin, who sees everything (no org-chart walk):
      1. `user.organisation` (`book.load_portfolio`)
      2. the caller's active membership, joined to its organisation and role
         (`services.identity.context.active_membership`, memoised)
      3. `user.role` (`services.identity.context.capabilities_for`)
      4. the accounts — last-touch subquery, owner joined
      5. their health snapshots (`snapshot_history(..., parent="account")`)
      6. the urgent tickets, counted per account
      7. the linked organisations the viewer may open (the M2M table)
      8-10. the filter options: organisations, owners, stages
    If the pinned number is ever wrong, print `[q["sql"] for q in
    ctx.captured_queries]`: every query must be one of these ten kinds; an
    extra one is a bug to fix, not a number to bump."""

    EXPECTED = 10

    def book(self, size):
        for i in range(size):
            account = self.account(
                f"Div {size}-{i}",
                customers=[self.pizza, self.taco],
                renewal_date=self.today + timedelta(days=i),
            )
            HealthSnapshot.objects.create(
                account=account,
                captured_on=self.today - timedelta(days=30),
                health_score=Decimal("6.0"),
            )
            Activity.objects.create(
                account=account, type=Activity.ActivityType.OTHER, occurred_at=self.today
            )
            Ticket.objects.create(
                account=account,
                ticket_number=f"T-{size}-{i}",
                title="Down",
                status=Ticket.Status.OPEN,
                priority=Ticket.Priority.HIGH,
                opened_at=self.today,
            )

    def count(self, user, **query):
        # A fresh user each time, as a real request loads one: memoised lookups
        # cached on a reused instance would otherwise flatter the second call.
        api = APIClient()
        api.force_authenticate(User.objects.get(pk=user.pk))
        with CaptureQueriesContext(connection) as ctx:
            response = api.get(URL, query)
        self.assertEqual(response.status_code, 200)
        return len(ctx.captured_queries)

    def test_the_count_is_pinned_and_does_not_grow_with_the_book(self):
        self.book(3)
        small, small_csm = self.count(self.admin), self.count(self.csm)
        self.book(12)
        self.assertEqual(self.count(self.admin), small)
        self.assertEqual(self.count(self.csm), small_csm)
        self.assertEqual(small, self.EXPECTED)

    def test_sorting_grouping_filtering_and_paging_add_no_queries(self):
        self.book(5)
        plain = self.count(self.admin)
        self.assertEqual(self.count(self.admin, sort="-risk", group="owner", limit="2"), plain)
        self.assertEqual(self.count(self.admin, group="lifecycle", group_value="live"), plain)
        self.assertEqual(
            self.count(
                self.admin,
                search="Div",
                health="average,good",
                renews_within="30",
                organisation=str(self.pizza.pk),
            ),
            plain,
        )
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `venv/bin/python manage.py test services.accounts_portfolio.tests.test_views --noinput`
Expected: `ModuleNotFoundError: No module named 'services.accounts_portfolio.views'`.

- [ ] **Step 3: Write the view, the URLs and the mount**

`services/accounts_portfolio/views.py`:

```python
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .book import filter_options, load_portfolio
from .params import parse_params
from .shape import build_listing


class AccountPortfolioView(APIView):
    """GET /api/v1/accounts/portfolio/ — the Accounts list and Board: the
    viewer's visible accounts (no archive or churn to hide), filtered,
    sorted, optionally grouped, and paged by an opaque cursor, with the
    summary tiles and group totals over the whole filtered set. Row signals
    come from the dashboard's own code. Unknown parameter values are ignored.
    See docs/API_CONTRACTS.md."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        params = parse_params(request.query_params)
        portfolio = load_portfolio(request.user, params, today=timezone.localdate())
        return Response(build_listing(portfolio, params, filters=filter_options(request.user)))
```

`services/accounts_portfolio/urls.py`:

```python
from django.urls import path

from . import views

urlpatterns = [
    path("portfolio/", views.AccountPortfolioView.as_view(), name="accounts-portfolio"),
]
```

In `config/urls.py`, replace:

```python
(path("api/v1/accounts/stats/", AccountStatsView.as_view(), name="account-stats"),)
```

with:

```python
# The Accounts page's own endpoints (portfolio, export, bulk). /accounts/
# and /accounts/stats/ stay as they are for their other consumers — see
# services/accounts_portfolio. None of its routes is "" or "stats/", so
# both exact paths still resolve.
(path("api/v1/accounts/", include("services.accounts_portfolio.urls")),)
(path("api/v1/accounts/stats/", AccountStatsView.as_view(), name="account-stats"),)
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `venv/bin/python manage.py test services.accounts_portfolio services.customers.tests.test_views --parallel --noinput`
Expected: `OK`. If `test_the_count_is_pinned…` fails on the number alone, print the captured SQL as its docstring says and fix the extra query. Do not change `EXPECTED`.

- [ ] **Step 5: Commit**

```bash
git add services/accounts_portfolio config/urls.py
git commit -m "$(cat <<'EOF'
feat(accounts): GET /accounts/portfolio/

Rows, panels, groups, the five tiles and filter options over the
viewer's visible accounts, cursor-paged with group_value for a Board
column, in a pinned ten queries.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: The CSV export, audited

**Files:**
- Create: `services/accounts_portfolio/export.py`
- Modify: `services/accounts_portfolio/views.py`, `services/accounts_portfolio/urls.py`, `docs/audit-events.md`, `docs/data-classification.md`
- Test: `services/accounts_portfolio/tests/test_export.py`

**Interfaces:**
- Consumes:
  - `FIELDS` (Task 5).
  - `row_payload` (Task 5).
  - `select` (Task 6).
  - `cell(value)` and `CSVRenderer` from `services.organizations.export`.
  - `audit.record(action, *, request, metadata)` from `core.audit`.
- Produces:
  - `table(rows, *, currency) -> list[list]`.
  - `AccountPortfolioExportView` at `/api/v1/accounts/portfolio/export.csv`, URL name `accounts-portfolio-export`.
  - The audit event `accounts.exported`.

- [ ] **Step 1: Write the failing tests**

`services/accounts_portfolio/tests/test_export.py`:

```python
import csv
import io
from decimal import Decimal

from rest_framework.test import APIClient

from core.models import AuditEvent
from services.accounts_portfolio.fields import FIELDS
from services.accounts_portfolio.tests.fixtures import AccountPortfolioFixture
from services.customers.models import Customer
from services.customers.tests.test_views import blind_to_one_account

URL = "/api/v1/accounts/portfolio/export.csv"


class ExportEndpointTests(AccountPortfolioFixture):
    def download(self, user=None, **query):
        api = APIClient()
        api.force_authenticate(user or self.csm)
        response = api.get(URL, query)
        return response, list(csv.reader(io.StringIO(response.content.decode())))

    def test_requires_authentication(self):
        response = APIClient().get(URL)
        self.assertEqual(response.status_code, 401)
        self.assertTrue(response["Content-Type"].startswith("text/csv"))

    def test_every_field_every_row_no_pagination(self):
        for i in range(60):
            self.account(f"Div {i:02d}")
        response, table = self.download()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/csv"))
        self.assertIn(
            f'filename="accounts-{self.today.isoformat()}.csv"', response["Content-Disposition"]
        )
        self.assertIn("attachment;", response["Content-Disposition"])
        header, rows = table[0], table[1:]
        self.assertEqual(header, [field.label for field in FIELDS] + ["Currency"])
        self.assertEqual(len(header), 25)
        self.assertEqual(len(rows), 60)

    def test_same_params_same_rows_and_visibility(self):
        self.account("Poor one", health_score=Decimal("2.0"))
        self.account("Good one")
        self.account("Dana's", customers=[self.taco], owner=self.other, health_score=Decimal("2.0"))
        _response, table = self.download(health="poor", sort="name")
        self.assertEqual([row[0] for row in table[1:]], ["Poor one"])

    def test_blind_to_one_account(self):
        viewer, _seen, _hidden = blind_to_one_account(self.pizza)
        _response, table = self.download(viewer)
        self.assertEqual([row[0] for row in table[1:]], ["Seen"])

    def test_values_and_formula_injection(self):
        evil = Customer.objects.create(organisation=self.org, name="+SUM(A1)", owner=self.csm)
        self.account(
            '=HYPERLINK("http://evil.example")',
            customers=[self.pizza, evil],
            owner=None,
            nps_score=-80,
            email="@ops.example",
        )
        _response, table = self.download(self.admin)
        row = dict(zip(table[0], table[1], strict=True))
        self.assertTrue(row["Account"].startswith("'="))
        self.assertEqual(row["Organizations"], "Pizza Hut; +SUM(A1)")
        self.assertEqual(row["Email"], "'@ops.example")
        self.assertEqual(row["Owner"], "Unassigned")
        self.assertEqual(row["NPS"], "-80")
        self.assertEqual(row["ARR"], "12000.0")
        self.assertEqual(row["Currency"], "USD")

    def test_the_export_is_audited(self):
        self.account("A")
        self.account("B")
        self.download(health="good", sort="name")
        event = AuditEvent.objects.get(action="accounts.exported")
        self.assertEqual(event.actor, self.csm)
        self.assertEqual(event.organisation, self.org)
        self.assertEqual(event.metadata, {"count": 2, "params": ["health", "sort"]})
```

The joined Organizations cell starts with `P`, so only a cell that itself begins with a formula character is prefixed. `"Pizza Hut; +SUM(A1)"` is not prefixed, and that is correct: a spreadsheet runs only a cell that starts with one.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `venv/bin/python manage.py test services.accounts_portfolio.tests.test_export --noinput`
Expected: FAIL. The tests expect status 200 and get 404, because the route does not exist yet.

- [ ] **Step 3: Write the export and its view**

`services/accounts_portfolio/export.py`:

```python
"""The Accounts portfolio as a CSV: every filtered row, every Account field
(`fields.FIELDS`), and the currency ARR is in. The cell rule and the renderer
are Organizations' own: a text cell that starts with `=`, `+`, `-`, `@`, a
tab or a carriage return is prefixed with `'`, so an account named
`=HYPERLINK(...)` is data, not an instruction."""

from services.organizations.export import cell

from .fields import FIELDS


def table(rows, *, currency):
    header = [field.label for field in FIELDS] + ["Currency"]
    body = [[cell(field.value(row)) for field in FIELDS] + [currency] for row in rows]
    return [header, *body]
```

Replace `services/accounts_portfolio/views.py` with:

```python
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core import audit
from services.organizations.export import CSVRenderer

from .book import filter_options, load_portfolio
from .export import table
from .params import parse_params
from .rows import row_payload
from .shape import build_listing, select


class AccountPortfolioView(APIView):
    """GET /api/v1/accounts/portfolio/ — the Accounts list and Board: the
    viewer's visible accounts (no archive or churn to hide), filtered,
    sorted, optionally grouped, and paged by an opaque cursor, with the
    summary tiles and group totals over the whole filtered set. Row signals
    come from the dashboard's own code. Unknown parameter values are ignored.
    See docs/API_CONTRACTS.md."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        params = parse_params(request.query_params)
        portfolio = load_portfolio(request.user, params, today=timezone.localdate())
        return Response(build_listing(portfolio, params, filters=filter_options(request.user)))


class AccountPortfolioExportView(APIView):
    """GET /api/v1/accounts/portfolio/export.csv — the same query as the
    list, every row (no pagination), in list order, with every Account field.
    Audited: this is confidential data leaving the app."""

    permission_classes = [IsAuthenticated]
    renderer_classes = [CSVRenderer]

    def get(self, request):
        params = parse_params(request.query_params)
        today = timezone.localdate()
        portfolio = load_portfolio(request.user, params, today=today)
        entries, _groups = select(portfolio, params)
        audit.record(  # SOC2:LOG-01
            "accounts.exported",
            request=request,
            # Parameter names only: a search term is the user's own words.
            metadata={"count": len(entries), "params": sorted(request.query_params.keys())},
        )
        response = Response(
            table(
                [row_payload(entry) for entry in entries],
                currency=portfolio.organisation.currency,
            )
        )
        response["Content-Disposition"] = f'attachment; filename="accounts-{today.isoformat()}.csv"'
        return response
```

Replace `services/accounts_portfolio/urls.py` with:

```python
from django.urls import path

from . import views

urlpatterns = [
    path(
        "portfolio/export.csv",
        views.AccountPortfolioExportView.as_view(),
        name="accounts-portfolio-export",
    ),
    path("portfolio/", views.AccountPortfolioView.as_view(), name="accounts-portfolio"),
]
```

In `docs/audit-events.md`, add this row directly after the `organizations.bulk_updated` row:

```text
| `accounts.exported` | `services.accounts_portfolio.views.AccountPortfolioExportView` | user | — | `count` of rows exported, `params`: the query-parameter names used (never their values — a search term is the user's own words) |
```

In `docs/data-classification.md`, add this row directly after the "Organizations CSV export" row:

```text
| Accounts CSV export (`services/accounts_portfolio`, `GET /accounts/portfolio/export.csv`) → the requester's device | every account field (ARR, renewal, NPS/CSAT, pulses and the AI pulse reason, owner, contact details, the linked organisations the requester may open) for the requester's own visible, filtered accounts | confidential | visibility-scoped exactly like the list (AUTH-02); audited `accounts.exported` (LOG-01); formula cells neutralised against CSV injection |
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `venv/bin/python manage.py test services.accounts_portfolio.tests.test_export services.accounts_portfolio.tests.test_views --noinput`
Expected: `OK` (16 tests).

- [ ] **Step 5: Commit**

```bash
git add services/accounts_portfolio docs/audit-events.md docs/data-classification.md
git commit -m "$(cat <<'EOF'
feat(accounts): GET /accounts/portfolio/export.csv, audited

Every account field for the viewer's filtered accounts, formula cells
neutralised, recorded as accounts.exported.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: `POST /api/v1/accounts/bulk/`

**Files:**
- Create: `services/accounts_portfolio/serializers.py`, `services/accounts_portfolio/bulk.py`
- Modify:
  - `services/customers/views.py`: add `after_account_update` below `after_customer_update`, and make `AccountDetailView.perform_update` use it.
  - `services/customers/serializers.py`: `AccountSerializer.validate_owner_id` refuses an inactive owner.
  - `services/accounts_portfolio/views.py` and `services/accounts_portfolio/urls.py`.
  - `docs/audit-events.md`.
- Test: `services/accounts_portfolio/tests/test_bulk.py`

**Interfaces:**
- Consumes:
  - `AccountSerializer` (partial, `context={"request": request}`), whose `validate_owner_id` enforces the same organisation, an active owner and `may_change_owner`.
  - `visible_accounts(user)`.
  - From Task 1: `first_reason`, `NOT_FOUND` and `NOT_UPDATED` from `services.organizations.bulk`.
  - `MAX_IDS` from `services.organizations.params`.
- Produces:
  - `services.customers.views.after_account_update(account, *, actor, previous_owner, handover_note="") -> None`.
  - `BulkRequestSerializer`, with actions `("set_owner", "set_lifecycle")`.
  - `bulk.apply(request, *, ids, action, value, result=None) -> {"updated": [int], "failed": [{"id", "reason"}]}`.
  - `AccountBulkUpdateView` at `/api/v1/accounts/bulk/`, URL name `accounts-bulk`.
  - The audit event `accounts.bulk_updated`.

- [ ] **Step 1: Write the failing tests**

`services/accounts_portfolio/tests/test_bulk.py`:

```python
from unittest.mock import patch

from django.db import DatabaseError, connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from core.models import AuditEvent
from services.accounts.models import User
from services.accounts_portfolio import bulk as bulk_module
from services.accounts_portfolio.serializers import BulkRequestSerializer
from services.accounts_portfolio.tests.fixtures import AccountPortfolioFixture
from services.customers.models import Account
from services.customers.tests.test_views import blind_to_one_account
from services.knowledge.models import Contribution
from services.notifications.models import Notification

URL = "/api/v1/accounts/bulk/"
NOT_YOURS = "can reassign this account"


class BulkRequestSerializerTests(SimpleTestCase):
    def validated(self, body):
        serializer = BulkRequestSerializer(data=body)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        return serializer.validated_data

    def errors(self, body):
        serializer = BulkRequestSerializer(data=body)
        self.assertFalse(serializer.is_valid())
        return serializer.errors

    def test_ids_are_deduplicated_in_order(self):
        data = self.validated({"ids": [3, 1, 3, 2, 1], "action": "set_lifecycle", "value": "live"})
        self.assertEqual(data["ids"], [3, 1, 2])

    def test_owner_is_an_id_or_an_explicit_null(self):
        self.assertEqual(
            self.validated({"ids": [1], "action": "set_owner", "value": 7})["value"], 7
        )
        self.assertIsNone(
            self.validated({"ids": [1], "action": "set_owner", "value": None})["value"]
        )
        for bad in (True, 1.5, "7"):
            self.assertIn("value", self.errors({"ids": [1], "action": "set_owner", "value": bad}))

    def test_a_missing_owner_does_not_mean_unassign(self):
        self.assertIn("value", self.errors({"ids": [1], "action": "set_owner"}))

    def test_every_stage_is_a_stage_churn_included(self):
        self.assertEqual(
            self.validated({"ids": [1], "action": "set_lifecycle", "value": "churn"})["value"],
            "churn",
        )
        self.assertIn(
            "value", self.errors({"ids": [1], "action": "set_lifecycle", "value": "bogus"})
        )

    def test_there_is_no_archive_or_churn_action(self):
        self.assertIn("action", self.errors({"ids": [1], "action": "archive"}))
        self.assertIn("action", self.errors({"ids": [1], "action": "churn"}))

    def test_ids_are_bounded(self):
        body = {"action": "set_lifecycle", "value": "live"}
        self.assertIn("ids", self.errors({**body, "ids": []}))
        self.assertIn("ids", self.errors({**body, "ids": list(range(1, 502))}))
        self.assertIn("ids", self.errors({**body, "ids": [0]}))


class BulkTests(AccountPortfolioFixture):
    def post(self, body, user=None):
        api = APIClient()
        api.force_authenticate(user or self.csm)
        return api.post(URL, body, format="json")

    def test_requires_authentication(self):
        self.assertEqual(APIClient().post(URL, {}, format="json").status_code, 401)

    def test_set_lifecycle(self):
        a, b = self.account("A"), self.account("B")
        response = self.post({"ids": [a.pk, b.pk], "action": "set_lifecycle", "value": "expansion"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"updated": [a.pk, b.pk], "failed": []})
        stages = set(
            Account.objects.filter(pk__in=[a.pk, b.pk]).values_list("lifecycle_stage", flat=True)
        )
        self.assertEqual(stages, {"expansion"})

    def test_request_validation(self):
        self.assertEqual(
            self.post({"ids": [], "action": "set_lifecycle", "value": "live"}).status_code, 400
        )
        self.assertEqual(self.post({"ids": [1], "action": "archive"}).status_code, 400)
        self.assertEqual(
            self.post({"ids": [1], "action": "set_owner", "value": "carl"}).status_code, 400
        )

    def test_set_owner_hands_over_like_the_detail_view(self):
        a = self.account("A", customers=[self.pizza, self.taco])
        response = self.post({"ids": [a.pk], "action": "set_owner", "value": self.other.pk})
        self.assertEqual(response.data, {"updated": [a.pk], "failed": []})
        self.assertEqual(Account.objects.get(pk=a.pk).owner, self.other)
        # The handover is written on every organisation the account is on.
        for customer in (self.pizza, self.taco):
            self.assertTrue(
                Contribution.objects.filter(customer=customer, author=self.csm).exists()
            )
        notice = Notification.objects.get(
            recipient=self.other, kind=Notification.Kind.ACCOUNT_ASSIGNED
        )
        self.assertEqual(notice.link, f"/accounts/{a.pk}")

    def test_unassigning(self):
        a = self.account("A")
        response = self.post({"ids": [a.pk], "action": "set_owner", "value": None})
        self.assertEqual(response.data["updated"], [a.pk])
        self.assertIsNone(Account.objects.get(pk=a.pk).owner)

    def test_partial_failures_are_reported_per_account(self):
        mine = self.account("Mine")
        danas = self.account("Dana's", customers=[self.taco], owner=self.other)
        globex = self.account("Globex's", customers=[self.globex], owner=None)
        # Visible to Carl through Pizza Hut, which he owns, but only Dana, her
        # managers or a settings manager may reassign it.
        shared = self.account("Shared", owner=self.other)
        outsider = User.objects.create_user(
            email="olga@globex.io",
            password="supersecret1",
            name="Olga",
            organisation=self.other_org,
            role=User.Role.CSM,
        )
        response = self.post(
            {
                "ids": [mine.pk, danas.pk, globex.pk, 999999, shared.pk],
                "action": "set_owner",
                "value": self.csm.pk,
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["updated"], [mine.pk])
        failed = {row["id"]: row["reason"] for row in response.data["failed"]}
        self.assertEqual(failed[danas.pk], "Not found.")
        self.assertEqual(failed[globex.pk], "Not found.")
        self.assertEqual(failed[999999], "Not found.")
        self.assertIn(NOT_YOURS, failed[shared.pk])
        self.assertEqual(Account.objects.get(pk=shared.pk).owner, self.other)

        response = self.post({"ids": [mine.pk], "action": "set_owner", "value": outsider.pk})
        self.assertEqual(
            response.data["failed"],
            [{"id": mine.pk, "reason": "Owner must be a member of your own organisation."}],
        )

    def test_blind_to_one_account(self):
        viewer, seen, hidden = blind_to_one_account(self.pizza)
        response = self.post(
            {"ids": [seen.pk, hidden.pk], "action": "set_lifecycle", "value": "live"}, user=viewer
        )
        self.assertEqual(response.data["updated"], [seen.pk])
        self.assertEqual(response.data["failed"], [{"id": hidden.pk, "reason": "Not found."}])
        self.assertNotEqual(Account.objects.get(pk=hidden.pk).lifecycle_stage, "live")

    def test_admin_may_reassign_anyones(self):
        danas = self.account("Dana's", customers=[self.taco], owner=self.other)
        response = self.post(
            {"ids": [danas.pk], "action": "set_owner", "value": self.csm.pk}, user=self.admin
        )
        self.assertEqual(response.data["updated"], [danas.pk])

    def test_an_inactive_owner_is_refused(self):
        a = self.account("A")
        self.other.is_active = False
        self.other.save(update_fields=["is_active"])
        response = self.post({"ids": [a.pk], "action": "set_owner", "value": self.other.pk})
        self.assertEqual(
            response.data["failed"],
            [{"id": a.pk, "reason": "Owner must be an active member of your organisation."}],
        )
        self.assertEqual(Account.objects.get(pk=a.pk).owner, self.csm)

    def test_the_single_edit_refuses_an_inactive_owner_too(self):
        a = self.account("A")
        self.other.is_active = False
        self.other.save(update_fields=["is_active"])
        api = APIClient()
        api.force_authenticate(self.csm)
        response = api.patch(
            f"/api/v1/customers/{self.pizza.pk}/accounts/{a.pk}/",
            {"owner_id": self.other.pk},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["owner_id"], ["Owner must be an active member of your organisation."]
        )

    def test_duplicate_ids_apply_once(self):
        a = self.account("A")
        response = self.post({"ids": [a.pk, a.pk], "action": "set_lifecycle", "value": "live"})
        self.assertEqual(response.data["updated"], [a.pk])

    def test_audited_with_ids_and_action(self):
        a = self.account("A")
        danas = self.account("Dana's", customers=[self.taco], owner=self.other)
        self.post({"ids": [a.pk, danas.pk], "action": "set_lifecycle", "value": "live"})
        event = AuditEvent.objects.get(action="accounts.bulk_updated")
        self.assertEqual(event.outcome, AuditEvent.Outcome.SUCCESS)
        self.assertEqual(
            event.metadata,
            {"action": "set_lifecycle", "value": "live", "ids": [a.pk], "failed_ids": [danas.pk]},
        )

    def test_nothing_updated_is_a_failure_outcome(self):
        danas = self.account("Dana's", customers=[self.taco], owner=self.other)
        self.post({"ids": [danas.pk], "action": "set_lifecycle", "value": "live"})
        event = AuditEvent.objects.get(action="accounts.bulk_updated")
        self.assertEqual(event.outcome, AuditEvent.Outcome.FAILURE)

    def test_a_database_error_fails_only_that_id(self):
        a, b, c = self.account("A"), self.account("B"), self.account("C")
        real_save = Account.save

        def save(instance, *args, **kwargs):
            if instance.pk == b.pk:
                raise DatabaseError("disk on fire")
            return real_save(instance, *args, **kwargs)

        with (
            patch.object(Account, "save", save),
            self.assertLogs("services.accounts_portfolio.bulk", level="WARNING") as logs,
        ):
            response = self.post(
                {"ids": [a.pk, b.pk, c.pk], "action": "set_lifecycle", "value": "live"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["updated"], [a.pk, c.pk])
        self.assertEqual(response.data["failed"], [{"id": b.pk, "reason": "Could not be updated."}])
        self.assertNotEqual(Account.objects.get(pk=b.pk).lifecycle_stage, "live")
        self.assertNotIn("disk on fire", "\n".join(logs.output))
        event = AuditEvent.objects.get(action="accounts.bulk_updated")
        self.assertEqual(
            (event.metadata["ids"], event.metadata["failed_ids"]), ([a.pk, c.pk], [b.pk])
        )

    def test_an_unexpected_error_still_audits_what_was_done(self):
        a, b = self.account("A"), self.account("B")
        api = APIClient(raise_request_exception=False)
        api.force_authenticate(self.csm)
        with patch(
            "services.accounts_portfolio.bulk.after_account_update",
            side_effect=[None, RuntimeError("boom")],
        ):
            response = api.post(
                URL,
                {"ids": [a.pk, b.pk], "action": "set_lifecycle", "value": "live"},
                format="json",
            )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(Account.objects.get(pk=a.pk).lifecycle_stage, "live")
        self.assertNotEqual(Account.objects.get(pk=b.pk).lifecycle_stage, "live")
        event = AuditEvent.objects.get(action="accounts.bulk_updated")
        self.assertEqual(event.outcome, AuditEvent.Outcome.FAILURE)
        self.assertEqual(event.metadata["ids"], [a.pk])

    def test_each_row_is_locked_and_read_fresh(self):
        a = self.account("A")
        with CaptureQueriesContext(connection) as queries:
            self.post({"ids": [a.pk], "action": "set_lifecycle", "value": "live"})
        locked = [q["sql"] for q in queries if "FOR UPDATE" in q["sql"]]
        self.assertEqual(len(locked), 1)
        self.assertIn('"customers_account"', locked[0])

    def test_authorisation_sees_the_current_owner(self):
        # B is Carl's to see through Pizza Hut. While the batch is on A, Dana
        # takes B over; by B's turn only Dana's chain or a settings manager
        # may reassign it, so a snapshot from the start must not do.
        a, b = self.account("A"), self.account("B")
        real_after = bulk_module.after_account_update

        def after(account, **kwargs):
            if account.pk == a.pk:
                Account.objects.filter(pk=b.pk).update(owner=self.other)
            return real_after(account, **kwargs)

        with patch("services.accounts_portfolio.bulk.after_account_update", after):
            response = self.post({"ids": [a.pk, b.pk], "action": "set_owner", "value": None})
        self.assertEqual(response.data["updated"], [a.pk])
        self.assertIn(NOT_YOURS, response.data["failed"][0]["reason"])
        self.assertEqual(Account.objects.get(pk=b.pk).owner, self.other)
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `venv/bin/python manage.py test services.accounts_portfolio.tests.test_bulk --noinput`
Expected: `ModuleNotFoundError: No module named 'services.accounts_portfolio.serializers'`.

- [ ] **Step 3: Share the after-save rule and refuse inactive owners**

In `services/customers/views.py`, add this directly below `after_customer_update`:

```python
def after_account_update(account, *, actor, previous_owner, handover_note=""):
    """What a saved Account edit sets off when its owner changed: the handover
    written down on every organisation the account belongs to, and the new
    owner told. Shared by the detail view's PATCH and the Accounts bulk edit,
    so a reassign means the same thing from either. The live push waits for
    the commit (a bulk edit saves each account in its own transaction)."""
    if account.owner_id == (previous_owner.id if previous_owner else None):
        return
    from services.knowledge.ownership import record_account_handover

    record_account_handover(account, actor, previous_owner, handover_note)
    _notify_owner_assigned(
        instance=account,
        actor=actor,
        kind=Notification.Kind.ACCOUNT_ASSIGNED,
        noun="the account",
        link=f"/accounts/{account.id}",
        push_on_commit=True,
    )
```

Replace `AccountDetailView.perform_update` with:

```python
    def perform_update(self, serializer):
        previous_owner = serializer.instance.owner
        account = serializer.save()
        after_account_update(
            account,
            actor=self.request.user,
            previous_owner=previous_owner,
            handover_note=serializer.context.get("handover_note", ""),
        )
```

In `services/customers/serializers.py`, inside `AccountSerializer.validate_owner_id`, insert this directly after the "Owner must be a member of your own organisation." check:

```python
# A deactivated user can't sign in to act on the account, so it would
# sit with nobody while looking owned — CustomerSerializer's rule.
if owner is not None and not owner.is_active:
    raise serializers.ValidationError("Owner must be an active member of your organisation.")
```

- [ ] **Step 4: Write the serializer, the bulk module and the view**

`services/accounts_portfolio/serializers.py`:

```python
from rest_framework import serializers

from services.customers.models import Customer
from services.organizations.params import MAX_IDS

#: Owner and lifecycle: the spec's two bulk edits. Accounts have no archive or
#: churn, so there is nothing else to apply in bulk.
ACTIONS = ("set_owner", "set_lifecycle")
OWNER_VALUE = "An owner is a user id, or null to unassign."


class BulkRequestSerializer(serializers.Serializer):
    ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), min_length=1, max_length=MAX_IDS
    )
    action = serializers.ChoiceField(choices=ACTIONS)
    value = serializers.JSONField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        action, value = attrs["action"], attrs.get("value")
        if action == "set_owner":
            # Unassigning is `null`, said out loud: a forgotten key must not
            # strip the owner from every selected account.
            if "value" not in self.initial_data:
                raise serializers.ValidationError({"value": OWNER_VALUE})
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise serializers.ValidationError({"value": OWNER_VALUE})
        if action == "set_lifecycle":
            # Every stage the single edit accepts, Churn included: on an
            # account it is only a stage, with no churn flow behind it.
            if not isinstance(value, str) or value not in Customer.LifecycleStage.values:
                raise serializers.ValidationError({"value": "Not a lifecycle stage."})
        attrs["ids"] = list(dict.fromkeys(attrs["ids"]))
        return attrs
```

`services/accounts_portfolio/bulk.py`:

```python
"""Bulk edits from the Accounts selection bar, applied one account at a time
with exactly the rules a single edit follows: the record must be visible to
the editor, `AccountSerializer` validates the change (an active owner in the
same organisation; a reassign only by the current owner, their chain or a
settings manager), and an owner change is handed over and notified
(`after_account_update`).

Each id is its own transaction: the row is re-read under a lock, so the
permission check judges the owner it has now, not when the batch began. One
failure never stops the rest (a database error included), and each is
reported by id. An id the editor cannot see reads "Not found." — the same
answer as one that does not exist, so the endpoint cannot be used to discover
records. Organizations' bulk edit, for accounts.
"""

import logging

from django.db import DatabaseError, transaction

from services.customers.models import Account
from services.customers.scoping import visible_accounts
from services.customers.serializers import AccountSerializer
from services.customers.views import after_account_update
from services.organizations.bulk import NOT_FOUND, NOT_UPDATED, first_reason

#: Each action is one writable field of the ordinary account update.
FIELD_FOR_ACTION = {
    "set_owner": "owner_id",
    "set_lifecycle": "lifecycle_stage",
}

logger = logging.getLogger(__name__)


def _locked(user, account_id):
    """The row as it is now, locked until this id's transaction ends, and
    only if the editor can still see it. Visibility is a subquery because
    Postgres will not lock a DISTINCT query (`visible_accounts` is one)."""
    # SOC2:AUTH-02 each record is read through the editor's own visibility
    visible = visible_accounts(user).filter(pk=account_id).values("pk")
    return Account.objects.select_for_update(of=("self",)).filter(pk__in=visible).first()


def _apply_one(request, account_id, field, value):
    """Returns None when the id was updated, else the reason it was not."""
    with transaction.atomic():
        account = _locked(request.user, account_id)
        if account is None:
            return NOT_FOUND
        previous_owner = account.owner
        serializer = AccountSerializer(
            account, data={field: value}, partial=True, context={"request": request}
        )
        if not serializer.is_valid():
            return first_reason(serializer.errors)
        saved = serializer.save()
        after_account_update(saved, actor=request.user, previous_owner=previous_owner)
    return None


def apply(request, *, ids, action, value, result=None):
    """`result`, when given, is filled in place as each id finishes, so a
    caller still knows what was done if something unexpected escapes."""
    field = FIELD_FOR_ACTION[action]
    result = result if result is not None else {"updated": [], "failed": []}
    for account_id in ids:
        try:
            reason = _apply_one(request, account_id, field, value)
        except DatabaseError as exc:
            # The id and the error's class only: the message can quote row data.
            logger.warning("Accounts bulk: id %s not updated (%s)", account_id, type(exc).__name__)
            reason = NOT_UPDATED
        if reason is None:
            result["updated"].append(account_id)
        else:
            result["failed"].append({"id": account_id, "reason": reason})
    return result
```

In `services/accounts_portfolio/views.py`, add these imports:

```python
from core.models import AuditEvent

from . import bulk
from .serializers import BulkRequestSerializer
```

Put `from core.models import AuditEvent` directly under `from core import audit`. Put `from . import bulk` first among the relative imports and `from .serializers import BulkRequestSerializer` after `from .rows import row_payload`. Then append this view:

```python
class AccountBulkUpdateView(APIView):
    """POST /api/v1/accounts/bulk/ — `{ids, action, value}` from the
    selection bar: `set_owner` or `set_lifecycle`. Applied per id under the
    single-edit rules; returns `{updated, failed: [{id, reason}]}` with a 200
    even when some failed."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = BulkRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = {"updated": [], "failed": []}
        finished = False
        try:
            bulk.apply(
                request,
                ids=data["ids"],
                action=data["action"],
                value=data["value"],
                result=result,
            )
            finished = True
        finally:
            # Recorded even if something escaped mid-batch: the ids already
            # saved stay saved (each is its own transaction).
            audit.record(  # SOC2:LOG-01
                "accounts.bulk_updated",
                request=request,
                outcome=(
                    AuditEvent.Outcome.SUCCESS
                    if finished and result["updated"]
                    else AuditEvent.Outcome.FAILURE
                ),
                metadata={
                    "action": data["action"],
                    "value": data["value"],
                    "ids": result["updated"],
                    "failed_ids": [row["id"] for row in result["failed"]],
                },
            )
        return Response(result)
```

In `services/accounts_portfolio/urls.py`, add this as the last entry of `urlpatterns`:

```python
(path("bulk/", views.AccountBulkUpdateView.as_view(), name="accounts-bulk"),)
```

In `docs/audit-events.md`, add this row after the `accounts.exported` row:

```text
| `accounts.bulk_updated` | `services.accounts_portfolio.views.AccountBulkUpdateView` | user | — | `action`, `value` (owner id or null, or stage), `ids` updated, `failed_ids`; written in a `finally`, outcome `failure` when nothing was updated or the batch did not finish |
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `venv/bin/python manage.py test services.accounts_portfolio services.customers services.knowledge --parallel --noinput`
Expected: `OK`. The existing account-detail PATCH tests (handover and notification) pass through `after_account_update`.

- [ ] **Step 6: Commit**

```bash
git add services/accounts_portfolio services/customers/views.py services/customers/serializers.py docs/audit-events.md
git commit -m "$(cat <<'EOF'
feat(accounts): POST /accounts/bulk/ for owner and lifecycle

Each id is locked, re-read through visible_accounts and saved through
AccountSerializer, so only whoever may reassign changes an owner;
failures are reported per id and the batch is audited as
accounts.bulk_updated. The detail PATCH and bulk share
after_account_update, and both now refuse an inactive owner.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: End-to-end flow

**Files:**
- Create: `e2e/test_accounts_flow.py`

**Interfaces:**
- Consumes:
  - The three endpoints from Tasks 8–10.
  - `POST /auth/signup/`, `/auth/users/`, `/auth/login/`, `/customers/` and `/customers/<id>/accounts/`.
  - `http_get` and `http_post` from `e2e.http`.
- Produces: `AccountsPortfolioFlowTests.test_full_flow`.

- [ ] **Step 1: Write the test**

`e2e/test_accounts_flow.py`:

```python
"""End-to-end tier: real server, real HTTP, real test DB. A CSM reads their
accounts portfolio, never sees the admin's account (not even by id), groups
and filters it, moves stages in bulk (the admin's id fails, theirs
succeed), unassigns one, and exports what they see as CSV."""

import csv
import io
import urllib.request

from django.test import LiveServerTestCase

from e2e.http import http_get, http_post


def http_get_text(url, token):
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request) as response:
        return response.status, response.headers.get("Content-Type"), response.read().decode()


class AccountsPortfolioFlowTests(LiveServerTestCase):
    def api(self, path):
        return f"{self.live_server_url}/api/v1{path}"

    def test_full_flow(self):
        # 1. An organisation signs up; its admin adds a CSM, who logs in.
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
        status, body = http_post(
            self.api("/auth/users/"),
            {"name": "Carl CSM", "email": "carl@acme.io", "password": "csmpassword1"},
            token=admin,
        )
        self.assertEqual(status, 201, body)
        status, body = http_post(
            self.api("/auth/login/"), {"email": "carl@acme.io", "password": "csmpassword1"}
        )
        self.assertEqual(status, 200, body)
        csm = body["access"]

        # 2. The admin's organisation and account; Carl's organisation and two accounts.
        status, body = http_post(self.api("/customers/"), {"name": "Admin Co"}, token=admin)
        self.assertEqual(status, 201, body)
        status, body = http_post(
            self.api(f"/customers/{body['id']}/accounts/"),
            {"name": "Admin Division", "arr": "90000"},
            token=admin,
        )
        self.assertEqual(status, 201, body)
        admin_division = body["id"]
        status, body = http_post(self.api("/customers/"), {"name": "Pizza Hut"}, token=csm)
        self.assertEqual(status, 201, body)
        pizza = body["id"]
        ids = {}
        for name, score, arr in (("Pizza EMEA", "2.0", "50000"), ("Pizza APAC", "8.0", "20000")):
            status, body = http_post(
                self.api(f"/customers/{pizza}/accounts/"),
                {"name": name, "health_score": score, "arr": arr, "lifecycle_stage": "live"},
                token=csm,
            )
            self.assertEqual(status, 201, body)
            ids[name] = body["id"]

        # 3. Carl's portfolio: his two, largest ARR first, each naming Pizza Hut;
        #    the admin's account is absent, even when named.
        status, body = http_get(self.api("/accounts/portfolio/"), token=csm)
        self.assertEqual(status, 200, body)
        self.assertEqual([row["name"] for row in body["results"]], ["Pizza EMEA", "Pizza APAC"])
        self.assertEqual(body["results"][0]["organisation"], {"id": pizza, "name": "Pizza Hut"})
        self.assertEqual(body["summary"]["accounts"], 2)
        self.assertEqual(body["summary"]["arr"], 70000.0)
        self.assertEqual(body["currency"], "USD")
        status, body = http_get(self.api(f"/accounts/portfolio/?ids={admin_division}"), token=csm)
        self.assertEqual((status, body["count"]), (200, 0))

        # 4. Grouped by health: Poor first; filtered to Poor, the one risky row.
        status, body = http_get(self.api("/accounts/portfolio/?group=health"), token=csm)
        self.assertEqual([g["key"] for g in body["groups"]], ["poor", "good"])
        status, body = http_get(self.api("/accounts/portfolio/?health=poor"), token=csm)
        self.assertEqual([row["name"] for row in body["results"]], ["Pizza EMEA"])
        self.assertEqual(body["results"][0]["signal"], {"kind": "risk", "label": "Risk 66"})

        # 5. Bulk stage change: Carl's two move, the admin's reports Not found.
        status, body = http_post(
            self.api("/accounts/bulk/"),
            {
                "ids": [ids["Pizza EMEA"], ids["Pizza APAC"], admin_division],
                "action": "set_lifecycle",
                "value": "expansion",
            },
            token=csm,
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["updated"], [ids["Pizza EMEA"], ids["Pizza APAC"]])
        self.assertEqual(body["failed"], [{"id": admin_division, "reason": "Not found."}])

        # 6. Unassign APAC; the Unassigned filter finds it, in its new stage.
        status, body = http_post(
            self.api("/accounts/bulk/"),
            {"ids": [ids["Pizza APAC"]], "action": "set_owner", "value": None},
            token=csm,
        )
        self.assertEqual((status, body["updated"]), (200, [ids["Pizza APAC"]]))
        status, body = http_get(self.api("/accounts/portfolio/?owner=unassigned"), token=csm)
        self.assertEqual([row["name"] for row in body["results"]], ["Pizza APAC"])
        self.assertEqual(body["results"][0]["lifecycle"]["value"], "expansion")

        # 7. Export: a CSV of every field for what Carl sees.
        status, content_type, text = http_get_text(self.api("/accounts/portfolio/export.csv"), csm)
        self.assertEqual(status, 200)
        self.assertTrue(content_type.startswith("text/csv"))
        table = list(csv.reader(io.StringIO(text)))
        self.assertEqual(len(table[0]), 25)
        self.assertEqual((table[0][0], table[0][-1]), ("Account", "Currency"))
        self.assertEqual([row[0] for row in table[1:]], ["Pizza EMEA", "Pizza APAC"])
```

- [ ] **Step 2: Run it**

Run: `venv/bin/python manage.py test e2e.test_accounts_flow --noinput`
Expected: `OK` (1 test). If step 4's label is not `Risk 66`, check that the account POST kept `health_score` (the account has no rubric recompute) and that it has no renewal date or pulses. Triage is Poor alone: 3 × 22.

- [ ] **Step 3: Commit**

```bash
git add e2e/test_accounts_flow.py
git commit -m "$(cat <<'EOF'
test(accounts): end-to-end accounts portfolio flow

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: Documentation

**Files:**
- Modify: `docs/API_CONTRACTS.md`, `docs/product/01-prd.md`, `docs/product/02-trd.md`, `docs/product/05-backend-schema.md`

**Interfaces:**
- Consumes: the shapes from Tasks 7–10, exactly as the tests pin them.
- Produces: docs only.

- [ ] **Step 1: `docs/API_CONTRACTS.md`, the status row**

In the Status table, insert this row directly after the row that begins `| Organizations portfolio (`/organizations` list redesign)`:

```text
| Accounts portfolio (`/accounts` list and Board redesign) | `accounts_portfolio` | ✅ Built — `GET /accounts/portfolio/` (rows, details, groups, summary, filters, cursor pages, `group_value` for a Board column), `GET /accounts/portfolio/export.csv`, `POST /accounts/bulk/` (owner, lifecycle). `/accounts/` and `/accounts/stats/` are unchanged |
```

- [ ] **Step 2: `docs/API_CONTRACTS.md`, the section**

Insert this section directly above the line `## Files — the Files tab on organisations and accounts`:

````text
## `accounts_portfolio` — Accounts portfolio (`/accounts`)

Built for the redesigned list and Board (spec: react-ts-app `docs/superpowers/specs/2026-09-29-accounts-redesign-design.md` §1).
`/api/v1/accounts/` and `/api/v1/accounts/stats/` are unchanged. No model: `services/accounts_portfolio/book.py` loads
the viewer's accounts and computes every row signal with the code Organizations and the dashboard use
(`health_q`, `signal_for`, `snapshot_history`, `triage`, the keyset cursor), so the lists agree.

### `GET /api/v1/accounts/portfolio/`

Auth: `IsAuthenticated`. Scope: `visible_accounts(user)`. Accounts have no archive or churn, so nothing is hidden by
default and there is no `include_churned`; the Churn lifecycle stage is an ordinary stage. A parent organisation's
archive or churn does not hide its accounts. Unknown parameter values are ignored, never a 400.

| Param | Meaning |
|---|---|
| `search` | name or Revenact ID (the row id as text), case-insensitive contains |
| `organisation` | comma list of organisation (`Customer`) ids; an account linked to any of them. An id the viewer cannot open matches nothing |
| `owner` | user id or `unassigned` |
| `lifecycle` | comma list of `LifecycleStage` values (`churn` included) |
| `health` | comma list of `good,average,poor` (score bands ≥ 7, 4–6.9, < 4) |
| `renews_within` | `30`, `90` or `180`: renewal on or before today + N, overdue included — the Renewing tile's own rule |
| `nps` | `promoter` (> 0), `passive` (= 0) or `detractor` (< 0) — `/accounts/stats/`' rule |
| `ids` | comma list, first 500 read; present with no usable id → no rows (the selection bar's "Export selected") |
| `sort` | `risk`, `arr`, `renewal`, `health`, `name`; `-` prefix descending; default `-arr`. Missing values sort last either way; ties by name, then id |
| `group` | `health` (Poor, Average, Good), `lifecycle` (stage order), `owner` (by name, Unassigned last), `renewal` (`overdue`, `30`, `90`, `180`, `later`, `none`), or empty |
| `group_value` | with `group` only: restrict `results` and `count` to that group key (a Board column). `groups` and `summary` stay whole |
| `cursor` | opaque, from `next_cursor`; valid only for the same filters, search, `ids`, sort, `group` and `group_value` |
| `limit` | default 50, max 100 |

```json
{
  "results": [{
    "id": 12, "name": "Pizza Hut EMEA", "initials": "PH",
    "owner": {"id": 2, "name": "Carl CSM"},
    "lifecycle": {"value": "live", "label": "Live"},
    "health": {"score": 4.9, "category": "average", "trend": [6.2, 5.8, 5.5, 5.1, 5.0, 4.9]},
    "renewal": {"date": "2026-08-13", "days": -47},
    "arr": 69600.0,
    "risk": {"score": 73, "direction": "declining"},
    "pulse": {"csm": 3, "ai": 1, "ai_category": "high_risk", "ai_label": "High Risk",
              "reason": "Quiet since the outage.", "history": [1, 2, 2], "disagree": true},
    "last_touch_days": 12,
    "urgent_tickets": 0,
    "signal": {"kind": "renewal_overdue", "label": "Renewal overdue"},
    "organisation": {"id": 7, "name": "Pizza Hut"},
    "extra_organisations": 1,
    "details": {
      "commercial": {"arr": 69600.0, "renewal_date": "2026-08-13"},
      "voice": {"nps_score": -80, "csat_score": 72.5, "ai_pulse_reason": "Quiet since the outage."},
      "profile": {"revenact_id": 12, "domain": "emea.pizzahut.example", "industry": "Food",
                  "email": "emea@pizzahut.example", "phone": "+44 20 0000", "address": "1 Main St, London",
                  "organisations": [{"id": 7, "name": "Pizza Hut"}, {"id": 9, "name": "Taco Bell"}]},
      "history": {"created_at": "2026-01-02T10:00:00+00:00", "updated_at": "2026-09-20T08:00:00+00:00",
                  "pulse_recorded_on": "2026-09-29", "csm_pulse_modified_at": null}
    }
  }],
  "next_cursor": "<opaque string; pass back as ?cursor=>",
  "count": 12,
  "groups": [{"key": "average", "label": "Average", "count": 4, "arr": 244000.0}],
  "summary": {
    "health": {"good": 5, "average": 4, "poor": 3,
               "arr": {"good": 444000.0, "average": 244000.0, "poor": 0.0},
               "mrr": {"good": 37000.0, "average": 20333.33, "poor": 0.0}},
    "nps": {"score": 44, "promoters": 6, "passives": 1, "detractors": 2},
    "lifecycle": [{"value": "onboarding", "label": "Onboarding", "count": 0, "arr": 0.0}],
    "accounts": 12, "arr": 688000.0, "unconverted_count": 0,
    "renewing": {"30": 1, "90": 3}
  },
  "filters": {"organisations": [{"value": "7", "name": "Pizza Hut"}],
              "owners": [{"value": "2", "name": "Carl CSM"}, {"value": "unassigned", "name": "Unassigned"}],
              "lifecycles": [{"value": "live", "name": "Live"}]},
  "currency": "USD"
}
```

- **Differences from `/organizations/portfolio/`.** No `is_archived` or `churned` on a row, and `signal` is never
  muted by churn. `organisation` + `extra_organisations` replace nothing: they are the "Organisation · …" line and
  its "+N". Four panels (`commercial`, `voice`, `profile`, `history`) instead of six. No product filter or group, no
  `touch` or numeric-field sorts.
- **Organisations.** `organisation` is the lowest-id linked organisation the viewer may open (`null` when they may
  open none); `extra_organisations` counts the other openable ones; `details.profile.organisations` lists them all.
  An organisation the viewer cannot open is never named or counted.
- **Money.** `Account.arr` is stored in the workspace's currency (as `/accounts/stats/` reads it), so `arr`, groups
  and the summary are that currency (top-level `currency`) with no conversion; `summary.unconverted_count` is always
  `0`, kept so both lists' tiles read one shape.
- **Signals.** `health.trend` is the account's last five month-end snapshots within six months plus today's score;
  `risk` is the Triage score over its 12-month snapshot history; `last_touch_days` is contact logged on the account
  itself (`contact.last_contact_by_account`; `null` = never); `urgent_tickets` counts open High/Critical tickets filed
  on the account that the viewer's department may read (`visible_tickets`) — a ticket on the organisation is not the
  account's. `signal` is at most one of `renewal_overdue`, `risk` (≥ 40), `tickets`, in that priority.
  `pulse.disagree` is `|csm − ai| ≥ 2`.
- **Totals.** `groups` and `summary` cover every filtered row, not the page. The summary's health, NPS and
  lifecycle reproduce `/accounts/stats/` for the same viewer; `summary.renewing` uses the `renews_within` rule, so
  clicking the tile lists exactly its N.
- **Pages.** As on Organizations: `next_cursor` is `null` on the last page; following it serves every row once, in
  list order; a cursor from a list with any of `search`, `organisation`, `owner`, `lifecycle`, `health`,
  `renews_within`, `nps`, `ids`, `sort`, `group`, `group_value` changed, or a malformed one, returns the first page.
- **Filters** are the viewer's own: organisations they may open that hold one of their visible accounts, the owners
  of those accounts (people in the viewer's organisation only; `unassigned` when an account has no owner), and the
  stages present.
- 10 constant queries per request for an admin, whatever the book size (pinned by `PortfolioQueryCountTests`).

### `GET /api/v1/accounts/portfolio/export.csv`

Auth: `IsAuthenticated`. Same parameters (`cursor`/`limit` ignored); every row in list order. `text/csv`,
`Content-Disposition: attachment; filename="accounts-<date>.csv"`. Columns: Account, Revenact ID, Organizations
(the openable ones, `; `-joined), Owner, Lifecycle Stage, Health, Pulse, AI Pulse Score, AI Pulse Value, CSM Pulse
Score, CSM Pulse Modified At, AI Pulse Reason, NPS, CSAT Score, Renewal Date, ARR, Domain, Industry, Email, Phone,
Address, Created Date, Modified Date, Pulse Recorded On, then `Currency` — every Account field. Text cells beginning
with `= + - @`, tab or CR are prefixed with `'`. An error response renders as a two-row CSV (`detail`, then the
message). Audited as `accounts.exported`, with `count` and `params` (the query-parameter names, never their values).

### `POST /api/v1/accounts/bulk/`

Auth: `IsAuthenticated`. Body `{"ids": [1, 2], "action": "set_owner" | "set_lifecycle", "value": …}`: `set_owner`
takes a user id, or `null` to unassign — the `value` key must be present (omitting it is a 400); `set_lifecycle` takes
any stage, `churn` included (an account has no churn flow). There is no archive action. 1–500 ids, duplicates applied
once.

Each id is locked and re-read (`select_for_update`) through `visible_accounts`, then runs the ordinary account update
(`AccountSerializer`, partial) in its own transaction: an owner must be an active member of the caller's organisation,
and only the current owner, their management chain or an organisation-settings manager may reassign. An owner change
is handed over (a contribution on each linked organisation) and the new owner notified, the push firing on commit.
`200`:

```json
{"updated": [1], "failed": [{"id": 2, "reason": "Not found."}]}
```

An id the caller cannot see reads `"Not found."`, like one that does not exist. A database error on one id puts it in
`failed` with `"Could not be updated."` and the batch continues. Audited as `accounts.bulk_updated`, written in a
`finally`; outcome `failure` when nothing was updated or the batch did not finish.

`PATCH /api/v1/customers/<customer_id>/accounts/<id>/` (the Board's move and the single edit) now also refuses an
inactive owner (`"Owner must be an active member of your organisation."`), as `PATCH /customers/<id>/` does, and its
assignment push waits for the commit.
````

- [ ] **Step 3: The product docs**

In `docs/product/01-prd.md` §5.2 Records, insert directly after the row `| Accounts: list, board, detail, create, edit | Built | …`:

```text
| Accounts portfolio (list and Board redesign) | Built (backend) | One endpoint for the page: rows with health trend, renewal runway, pulse, last touch, one signal from the dashboard's own code and the organisations the viewer may open; the four detail panels; group (health, lifecycle, owner, renewal), five sorts, filters (organisation, owner, lifecycle, health, renews within, NPS) and cursor pages with Board columns; the five tiles over the filtered set; CSV export of every field; bulk owner and lifecycle under the single-edit rules |
```

In §9 Release history, append:

```text
| 2026-09-29 | Accounts portfolio (backend): portfolio endpoint, export, bulk edit |
```

In `docs/product/02-trd.md` §2.4, append this row to the module table:

```text
| `services/organizations/`, `services/accounts_portfolio/` | The two portfolios (Organizations, Accounts): each loads the viewer's visible book in a fixed number of queries and computes row signals with the dashboard's code; the account one imports the organisation one's generic helpers (filters, signal, snapshots, sparkline, keyset cursor, CSV cell) and keeps the account rules its own |
```

In `docs/product/05-backend-schema.md`, directly after the `### `organizations`` section's two paragraphs (before the `---` that follows them), add:

```text
### `accounts_portfolio`

No model. `services.accounts_portfolio.book.load_portfolio` reads `Account` (with the account last-touch
annotation), `HealthSnapshot` (account-level), `Ticket` and the `Account.customers` link table for the viewer's
visible, filtered accounts on every request, and `shape.py` orders, groups and totals it in Python. Bulk edits write
`Account` through `AccountSerializer`.
```

- [ ] **Step 4: Check the docs**

Run: `venv/bin/ruff format --check docs/API_CONTRACTS.md docs/product docs/audit-events.md docs/data-classification.md`
Expected: every file already formatted. The new blocks use only `json` and `text` fences.

Run: `grep -c "accounts.exported\|accounts.bulk_updated" docs/audit-events.md docs/API_CONTRACTS.md`
Expected: `docs/audit-events.md:2` and a non-zero count for `docs/API_CONTRACTS.md`.

- [ ] **Step 5: Commit**

```bash
git add docs
git commit -m "$(cat <<'EOF'
docs(accounts): portfolio, export and bulk contracts; product docs

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: Full verification

**Files:** none changed. If a check fails, fix it and commit the fix in the task it belongs to.

- [ ] **Step 1: The whole suite**

Run: `venv/bin/python manage.py test --parallel --noinput`
Expected: `OK`. No test is skipped that was not skipped on `main`.

- [ ] **Step 2: Lint and format**

Run: `venv/bin/ruff check . && venv/bin/ruff format --check .`
Expected: `All checks passed!` and `… files already formatted`.

- [ ] **Step 3: No migrations, system checks clean**

Run: `venv/bin/python manage.py makemigrations --check --dry-run && venv/bin/python manage.py check`
Expected: `No changes detected` and `System check identified no issues (0 silenced).`

- [ ] **Step 4: The house markers**

Run: `grep -n "SOC2:AUTH-02" services/accounts_portfolio/*.py && grep -n "SOC2:LOG-01" services/accounts_portfolio/views.py`
Expected: `AUTH-02` appears in `book.py` (the scope, the organisation filter, tickets, the linked organisations, and the options) and in `bulk.py`; `LOG-01` appears twice in `views.py`.

- [ ] **Step 5: The schema still builds**

Run: `venv/bin/python manage.py spectacular --file /dev/null --validate`
Expected: exit 0. APIView warnings like the Organizations endpoints' are acceptable; errors are not.

- [ ] **Step 6: A live look**

Run `venv/bin/python manage.py runserver`. Log in as a seeded CSM, then run `curl -s -H "Authorization: Bearer <token>" "http://localhost:8000/api/v1/accounts/portfolio/?group=lifecycle&limit=5"`.
Expected: a 200 whose `groups` list every lifecycle stage present with counts and ARR, `results` hold at most 5 rows, each with `organisation`, and `summary.accounts` equals the sum of the group counts.

- [ ] **Step 7: Finish the branch**

Use superpowers:finishing-a-development-branch. The PR body lists the three endpoints, notes the `AccountSerializer` inactive-owner change and the Organizations helper refactor, and ends with the attribution line from the session's instructions.

---

## Decisions this plan makes where the spec is silent

1. **Organisations on a row.**
   - `organisation` is the lowest-id linked organisation the viewer may open.
   - `extra_organisations` ("+N") counts only the *other openable* ones, and Profile lists only openable ones.
   - An account whose organisations are all closed to the viewer shows `organisation: null`. This can happen with an unowned account under another CSM's organisation.
   - Reason: naming or counting an organisation the viewer cannot open would disclose it.
2. **The `organisation` filter** is a comma list (like Organizations' `product`). An id the viewer cannot open matches nothing, rather than being ignored or listing its accounts.
3. **Filter options.** Organisations are the openable ones that hold at least one visible account, archived or churned organisations included, because their accounts are listed. Options carry no counts.
4. **Parent state does not hide accounts.** An account under an archived or churned organisation is still listed, as `/accounts/` lists it today.
5. **The Churn lifecycle stage is an ordinary stage.** It is listed, filterable and a Board column, and bulk `set_lifecycle` accepts it. Renewal filters, tiles and signals apply to it. Accounts have no churn flow for it to bypass.
6. **ARR.**
   - `Account.arr` is taken as already in the workspace currency. This is `AccountStatsView`'s documented rule. There is no FX.
   - `summary.unconverted_count` is always `0`, kept only so the kind-agnostic tiles read one shape.
7. **Urgent tickets** are only tickets filed on the account itself. The organisation's tickets are not fanned down, matching delivery 2's "only records with `account_id` = this account".
8. **Last touch** is contact logged on the account itself (`last_account_contact_annotation`), with no fall-back to the organisation.
9. **Sort and group.**
   - The default sort is `-arr`, as on Organizations. The frontend's default group (health on the List, lifecycle on the Board) lives in its URL, not the API.
   - There are no `touch` or numeric-field sorts.
10. **`ids`** is supported (max 500) for "Export selected", with Organizations' semantics.
11. **Row shape.**
    - Organizations' keys are kept where they mean the same thing. `is_archived` and `churned` are dropped; `organisation` and `extra_organisations` are added.
    - `details` has four panels. `csm_pulse_modified_at` goes in History, so every field appears exactly once.
    - Profile values are the account's own. The parent-fallback the old frontend applied for display is not reproduced.
12. **Export columns.** There are 24 fields plus `Currency`, 25 columns in all. "Organizations" joins the openable names with `; `. The AI Pulse Value and the CSM pulse columns are included because the spec says "every field".
13. **Tile windows.** `summary.renewing` carries both `30` and `90`, as on Organizations. The spec's tile names 90, and the same shape keeps the shared tile component.
14. **Bulk.**
    - The actions are `set_owner` and `set_lifecycle` only.
    - `AccountSerializer.validate_owner_id` now refuses an inactive owner, as `CustomerSerializer` does. This also tightens the single PATCH and the Board move, to mirror Organizations' gating.
    - `after_account_update` is extracted, and the detail PATCH's assignment push now waits for the commit, as the customer PATCH's does.
15. **Refactoring Organizations (Task 1).**
    - Private helpers become public and three inline pieces become functions (`keyset_page`, `health_trend`, `pulse_payload`), plus `snapshot_history(parent=)`, so the account app imports rather than copies them.
    - The account app writes its own `renewing_q`, ticket count, group key, sort getters, fingerprint and summary, because Organizations' versions read Customer-only fields.
16. **Mounting.** The app includes at `api/v1/accounts/` beside the two existing exact paths, which still resolve (pinned by a test). The audit actions are `accounts.exported` and `accounts.bulk_updated`.

## Self-review

**Spec coverage** (§1 Backend, the §1 data needs, §5 Backend):

- Portfolio rows, details, groups, summary and filter options: Tasks 3–8. Cursor and `group_value`: Tasks 6 and 8.
- Export (every field, formula neutralising, `accounts.exported`): Tasks 5 and 9.
- Bulk (owner, lifecycle, `visible_accounts` per id, reassign gating, per-id results): Task 10.
- Access (visible accounts, then `visible_tickets`; the summary is the viewer's own): Tasks 3, 4 and 8.
- Currency: Tasks 4 and 7.
- The row's nine elements: ring, name line with organisation and "+N", trend, runway, ARR, pulse with disagree, signal priority. Tasks 4 and 5.
- The panels: Task 5. The tiles: Task 7. The filters: Task 3. Groups and sorts: Task 6.
- Board columns, including empty ones: the frontend draws every stage from `summary.lifecycle`, which lists every stage with its count and ARR. `groups` lists the non-empty ones, and each column pages with `group_value`.
- Tests:
  - Unit: Tasks 1, 2, 5, 7 and 10.
  - Integration: Tasks 3–10.
  - E2E: Task 11.
  - `blind_to_one_account` across rows, summary, groups and filters (Task 8), export (Task 9) and bulk (Task 10).
  - Query counts pinned flat for admin and CSM (Task 8).
  - Bulk gating (Task 10). CSV neutralising (Task 9).
- The story and Ask are deliveries 2 and 3, and not in scope.

**Placeholder scan.** No TBD, TODO, "similar to Task N" or undescribed steps. Every code step carries its code, and every test step its test code and the command with the expected result.

**Type consistency.**

| Name | Defined | Consumed |
|---|---|---|
| `AccountPortfolioParams.organisations` | Task 2 | Tasks 3 and 6 |
| `AccountEntry.account/arr/trend/triage/last_touch_days/renewal_days/urgent_tickets/signal/organisations` | Task 4 | Tasks 5–7 |
| `AccountPortfolio.entries/organisation` | Task 4 | Tasks 7 and 9 |
| `keyset_page(entries, *, rank, cursor, limit, descending, fingerprint, grouped)` | Task 1 | Task 6 |
| `snapshot_history(ids, *, since, parent=)` | Task 1 | Task 4 |
| `health_trend(snapshots, current, *, today)` | Task 1 | Task 4 |
| `pulse_payload(record)` | Task 1 | Task 5 |
| `first_reason(errors)` | Task 1 | Task 10 |
| `paginate(entries, *, params)` | Task 6 | Task 7 |
| `table(rows, *, currency)` | Task 9 | Task 9 |
| `after_account_update(account, *, actor, previous_owner, handover_note="")` | Task 10 | Task 10 |

The fixture names `self.pizza`, `self.taco`, `self.globex` and `account(...)` are used the same way throughout.
