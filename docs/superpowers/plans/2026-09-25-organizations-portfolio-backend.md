# Organizations portfolio, backend (delivery 1: portfolio data) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve the redesigned `/organizations` list from one purpose-built endpoint, `GET /api/v1/organizations/portfolio/` (rows with their signals, the opened-row `details`, groups, summary tiles, filter options, cursor pages), plus a CSV export of the same query and a bulk-edit endpoint.

**Architecture:** A new app, `services/organizations/`, with no models. `params.py` parses the query once. `book.py` loads the viewer's filtered book in a fixed number of queries and computes every row signal with the code the dashboard already uses (`with_health_inputs`, the health snapshots, `triage.triage`, the last-touch annotation, `visible_tickets`, `convert_to_org_currency`). `shape.py` orders, groups, pages and totals in Python over the whole filtered set. `rows.py` builds the JSON row, `fields.py` names the 34 table fields once for the export and the tests, `export.py` writes the CSV, and `bulk.py` applies the existing `CustomerSerializer` update rules one id at a time. `/customers/` is not touched.

**Tech Stack:** Django 5, DRF, PostgreSQL, `TestCase`/`SimpleTestCase`/`APIClient`, `LiveServerTestCase` for the e2e test.

**Spec:** `react-ts-app/docs/superpowers/specs/2026-09-25-organizations-portfolio-design.md`: §2 is the scope; §1 sets the data needs (all 34 fields, the six `details` panels, the row's eight elements, the tiles, the filters); §4 item 1 is this delivery; §5 "Backend" lists the tests. §3 (Ask Revenact) and the board (§4 item 2) are later deliveries. This plan only has to keep `group_value` good enough for the board.

## Global Constraints

- **Auth**: `IsAuthenticated`.
- **Scope**: `visible_customers(user)`, excluding archived. Churned customers are included only when `lifecycle` includes `churn` or `include_churned=1`. `ids` names records explicitly: "Archived customers named in the list are included, as on `/customers/?ids=`."
- **Unknown values are ignored**, as the dashboard endpoints do: never a 400 on the GET endpoints.
- **Params**: `search`, `owner` (user id or `unassigned`), `lifecycle` (comma list), `health` (comma list of `good,average,poor`), `product` (comma list of product ids), `renews_within` (`30`, `90`, `180`, overdue included), `nps` (`promoter`, `passive` or `detractor`), `ids` (comma list, max 500), `include_churned` (`1`), `sort` (`arr`, `health`, `renewal`, `touch`, `risk`, `name`, or a numeric field key; `-` prefix for descending; default `-arr`), `group` (`health`, `owner`, `lifecycle`, `product`, `renewal`, or empty), `group_value`, `cursor`, `limit` ("`limit` defaults to 50, max 100").
- **Health group order**: "sections Poor, Average, Good in that order".
- **Signal**: "at most one. Priority is renewal overdue, then risk ≥ 40 ("Risk NN"), then open High/Critical tickets ("N open tickets"), then none."
- **Row signals reuse the dashboard code, so the numbers agree**: `with_health_inputs`; the health snapshot history (6 months, one query); `services/customers/triage.py` (risk); the last-touch rule from `activity_tracking`; open High/Critical tickets through `visible_tickets`; ARR converted like the dashboard figures.
- **Groups and summary are computed over the whole filtered set, not the page.**
- **The query count is constant per page**, pinned by a test.
- **Export**: `GET /api/v1/organizations/portfolio/export.csv`, "same params and returns every row (no pagination) with all 34 fields". Audit event: `organizations.exported`.
- **Bulk**: `POST /api/v1/organizations/bulk/` with `{ids, action: "set_owner"|"set_lifecycle"|"archive", value}`. It "applies per id using the existing update rules and permissions" and returns `{updated: [...], failed: [{id, reason}]}`. Audit: `organizations.bulk_updated` with the ids and action. "Churn keeps its existing modal and endpoint, run per account."
- `/customers/` stays unchanged for its other consumers.
- Every endpoint change updates `docs/API_CONTRACTS.md` in the same PR (`.claude/skills/api-contracts`). The product docs (PRD, backend schema), `docs/audit-events.md` and `docs/data-classification.md` change in the same PR too.
- Tests come in three tiers per `.claude/skills/testing`: unit (`SimpleTestCase`), integration (`TestCase` with `APIClient`), and e2e (`LiveServerTestCase` in the top-level `e2e/`). Run them with `venv/bin/python manage.py test <label> --noinput`.
- `venv/bin/ruff check .` and `venv/bin/ruff format --check .` must pass. **ruff formats Python inside Markdown fences**, so run `venv/bin/ruff format` on every doc you edit that contains a `python` fence. The doc edits in this plan use only `json`/`text` fences.
- Semgrep runs in CI (`p/django`, `p/security-audit`, `p/owasp-top-ten`). Return opaque strings, not formatted URLs, and do not hand-build `HttpResponse` bodies. The export uses a DRF renderer for this reason.
- Mark code with the SOC 2 tags the repo uses: `# SOC2:LOG-01` on each `audit.record` call and `# SOC2:AUTH-02` on object-level visibility rules.
- Commits are conventional (`feat(organizations): …`) and end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Pre-flight: where the spec meets the code

| # | Spec says | Code has | Resolution |
|---|---|---|---|
| 1 | Search covers "name / Revenact ID / external ID" | `Customer` has no external-id field. Only `Ticket.external_id` exists, and `CustomerListCreateView`'s docstring says the placeholder "isn't wired to anything real". "Revenact ID" is the row's own `id`. | Search matches `name` (icontains) and the `id` cast to text (icontains), the same as `/customers/?search=`. External ID is **not searched**, and no field is added. The frontend placeholder must say "Search name or ID". This is flagged for the frontend plan. |
| 2 | "Churned … included only when `lifecycle` includes `churn` or `include_churned=1`, **matching today's list**" | Today's `/customers/` excludes only archived and **shows churned**. The dashboards' `live_customers` treats "churned" as `churn_date` being set. The churn modal sets `churn_date` and the stage together. | The spec's rule is adopted, as a deliberate change from today's list. "Churned" means `churn_date` is set **or** `lifecycle_stage == "churn"` (`book.CHURNED`), so a seeded stage-only churn row also hides. Consequence: the summary equals `/customers/stats/` (which uses `live_customers`) whenever the two markers agree, and the equality test builds its data that way. |
| 3 | A new module for the portfolio | `services/customers/portfolio.py` already exists: it is the **Customer Overview** dashboard's rollups. `services/customers/views.py` is 2,966 lines. The API convention is "one Django app per domain, mounted at `/api/v1/<domain>/`". | A new app, `services/organizations/`, mounted at `/api/v1/organizations/`. It has no models. It depends on `services.customers` one way only, and delivery 3's `organizations_grounding` imports `book.load_portfolio` from it. |
| 4 | "`cursor`, `limit`: cursor pagination" | There is no `CursorPagination` anywhere. The default is `PageNumberPagination`. `risk`, `touch` and converted-ARR sorts are computed in Python, so DRF's `CursorPagination` cannot order by them. Groups and summary need the whole set loaded anyway. | The cursor is opaque. It is base64 JSON `{"id": <last row id>, "offset": <rows served>}` over the Python-ordered list. The next page starts after that id. If that row has left the set, it starts at `offset - 1`. A malformed cursor gives the first page. `next_cursor` is a string, not a URL (semgrep trap). |
| 5 | "6-month sparkline of health score from snapshots" | Snapshots are **month-end**, one per customer per month (`run_health_maintenance.last_completed_month_end`). The ring shows today's `health_score`. | `trend` is the scores of the snapshots captured in the last `31 × 6` days, oldest first, with the last five kept, then the current score appended. That gives 6 points, and the line ends at the ring's number, as in the spec example. It is one query: the same prefetch triage reads. |
| 6 | Risk from `triage.py` | `CustomerHealthRowSerializer._triage` feeds triage the categories of the snapshots in a 12-month window (`CustomerHealthView.DEFAULT_HISTORY_MONTHS`). `attention.rules.filtered_customers` loads the same window. | The snapshot prefetch uses that same 12-month window, so the triage score equals `/customers/health/`. The row gains `risk: {score, direction}`, which is not in the spec example. It is needed for `sort=risk`, the signal, and the equality test. |
| 7 | "the last-touch rule from `activity_tracking`" | `activity_tracking` uses `contact.last_contact_by_customer` (every touch source, `None` when never). `with_health_inputs` annotates `_last_touch_on` with the SQL rendering of the same rule (pinned equal by `test_contact.py`). `health_inputs()["days_since_touch"]` falls back to `joined_date`, which is a different measure. | `last_touch_days = (today - _last_touch_on).days`, or `null` when there has never been contact. No joined-date fallback. It equals `activity_tracking.gone_quiet_values`. `sort=-touch` puts never-contacted first, as `dark_accounts` does. |
| 8 | Row `pulse: {csm, ai, reason}`; the old table has "Pulse" and "AI Pulse Score" columns | The "Pulse" column renders the stored `Customer.pulse` dots (`OrganizationsTable.renderPulse(r.pulse)`). "AI Pulse Score" is the `ai_pulse_score` category label. | `pulse` gains `history` (the stored dots), `ai_category`, `ai_label` and `disagree` (`abs(csm - ai) >= 2`, the spec's "pulses disagree" marker), so the header carries both old columns. |
| 9 | `product` filter and group; "Products Utilized" | There is one FK, `Customer.primary_product` (a `Product` row), plus `additional_products_count`, which has no names. | The filter and the group read `primary_product_id`. `details.adoption.products = {primary: {id, name} or null, additional_count}`. The export prints "Core (+2)". |
| 10 | `nps` "promoter/passive/detractor band" | `Customer.nps_score` is a −100…100 NPS. `CustomerStatsView` buckets by sign: `> 0` promoter, `== 0` passive, `< 0` detractor. | The same sign rule, for both the filter and the summary, so the tile reproduces today's MetricsPanel. |
| 11 | Summary "renewals" tile, 30/90 days | `/customers/?renewal_within=N` counts renewals on or before today+N, overdue included, and excludes `lifecycle_stage=churn`. | `summary.renewing` uses the same rule. It is equality-tested against the list endpoint's `count`. |
| 12 | "ARR: … in the organisation currency" | `CustomerHealthRowSerializer.get_arr` converts with `rates_for` and gives `null` when no rate exists. The old table prints every money column in the **row's own** currency. | The row's `arr`, the groups, sorting on money fields, and the summary are in the organisation's currency (top-level `currency`, with `summary.unconverted_count`). The `details.commercial` amounts stay in the customer's own currency, with a `currency` key, as the old table showed them. |
| 13 | Bulk "existing update rules and permissions" | `CustomerDetailView` means: visible customers only (404 otherwise), `CustomerSerializer` partial update, `validate_owner_id` (same org, `may_change_owner`), and after a save a handover `Contribution` plus an assignment notification when the owner changed. Archive is `PATCH is_archived`. | Each id runs `CustomerSerializer(partial=True)` with the request context. The after-save owner handling is extracted into `services.customers.views.after_customer_update(...)`, which the detail view and bulk share. An invisible or unknown id fails with the reason `"Not found."`, which does not reveal whether the record exists. |
| 14 | Churn via bulk | The spec's bulk actions are `set_owner`, `set_lifecycle` and `archive`. Churn keeps its modal. | `set_lifecycle` with `value: "churn"` is a **400**, so churn can only arrive through the modal, which records `churn_date`, reason and comment. There is no un-archive action, because the spec lists none. |
| 15 | Export "all 34 fields" | No CSV endpoint exists in the repo. Its JSON default renderer would 406 an `Accept: text/csv`. | A DRF `CSVRenderer` renders the export view, including its 401/403. The columns are the 34 table fields in `ALL_COLUMNS` order, with the frontend labels minus "($)", plus a trailing `Currency` column for the money columns. Cells that start with `= + - @` or a tab/CR are prefixed with `'` (CSV injection). |
| 16 | `filters: {owners, lifecycles, products}` | `forecast.filter_options` returns `{"value", "name"}` lists over `live_customers` and runs one `EXISTS` per stage. | The same `{"value", "name"}` shape, over visible **non-archived** customers (churned included, so the churned filter has options). There are three queries: owners, stages and products. |
| 17 | `group_value` "restrict to one group (used by board columns)" | — | It applies only with `group`. It narrows `results` and `count`. `groups` and `summary` stay over the whole filtered set, so every column header keeps its totals. A value naming no group returns zero rows. |
| 18 | "renewal overdue" signal | A churned account has a renewal date in the past by definition. | Churned rows (reachable with `include_churned` or `ids`) carry `signal: null`. |
| 19 | "open High/Critical tickets through `visible_tickets`" | `attention.rules._support_items`: `visible_tickets(user, Ticket.objects.filter(visible_children_q(user)).distinct())`, with `priority in SUPPORT_PRIORITIES`, unresolved, and an account ticket counting for each of that account's companies. | The same query and counting rule (`book.urgent_ticket_counts`), equality-tested against the attention list's `support` item. |

## File Structure

| File | Responsibility |
|---|---|
| `services/organizations/__init__.py`, `apps.py` (new) | App config, registered in `INSTALLED_APPS` |
| `services/organizations/params.py` (new) | `PortfolioParams`, `parse_params(query)`: every param parsed once, bad values dropped |
| `services/organizations/book.py` (new) | `filtered_queryset`, `load_portfolio` → `Portfolio(entries, organisation, rates, today)`, `urgent_ticket_counts`, `signal_for`, `filter_options` |
| `services/organizations/shape.py` (new) | `order_entries`, `group_key`, `build_groups`, `select`, `paginate` with the cursor, `build_summary`, `build_listing` |
| `services/organizations/rows.py` (new) | `row_payload(entry)`, `details_payload(customer)`, `initials(name)` |
| `services/organizations/fields.py` (new) | `FIELDS`: the 34 table fields `(id, label, value(row))`, in the frontend's `ALL_COLUMNS` order |
| `services/organizations/export.py` (new) | `table(rows)`, `cell(value)`, `CSVRenderer` |
| `services/organizations/serializers.py` (new) | `BulkRequestSerializer` |
| `services/organizations/bulk.py` (new) | `apply(request, *, ids, action, value)` → `{updated, failed}` |
| `services/organizations/views.py`, `urls.py` (new) | `PortfolioView`, `PortfolioExportView`, `BulkUpdateView` |
| `services/organizations/tests/` (new) | `fixtures.py` (`PortfolioFixture`), `test_params.py`, `test_book.py`, `test_signals.py`, `test_shape.py`, `test_summary.py`, `test_rows.py`, `test_views.py`, `test_export.py`, `test_bulk.py` |
| `config/settings.py`, `config/urls.py` | Register the app and mount `api/v1/organizations/` |
| `services/customers/views.py` | Extract `after_customer_update` from `CustomerDetailView.perform_update` |
| `e2e/test_organizations_flow.py` (new) | End to end: read, filter, group, bulk, archive, export |
| docs | `API_CONTRACTS.md`, `audit-events.md`, `data-classification.md`, `product/01-prd.md`, `product/05-backend-schema.md` |

---

### Task 1: The app and its parameters

**Files:**
- Create: `services/organizations/__init__.py` (empty), `services/organizations/apps.py`, `services/organizations/params.py`, `services/organizations/tests/__init__.py` (empty), `services/organizations/tests/test_params.py`
- Modify: `config/settings.py` (`INSTALLED_APPS`)

**Interfaces:**
- Produces:
  - `PortfolioParams` (frozen dataclass) with the fields `search: str`, `owner: int | str | None` (an int id, `"unassigned"`, or None), `lifecycles: tuple[str, ...]`, `health: tuple[str, ...]`, `products: tuple[int, ...]`, `renews_within: int | None`, `nps: str | None`, `ids: tuple[int, ...] | None` (None when not given, `()` when given with no usable id), `include_churned: bool`, `sort: str`, `group: str`, `group_value: str | None`, `cursor: str`, `limit: int`, and the properties `sort_key: str` and `descending: bool`.
  - `parse_params(query: Mapping) -> PortfolioParams`, which accepts a DRF `QueryDict` or a plain `dict`.
  - The constants `SORT_KEYS`, `NUMERIC_SORT_KEYS`, `GROUPS`, `RENEWS_WITHIN_DAYS`, `NPS_BANDS`, `DEFAULT_SORT = "-arr"`, `DEFAULT_LIMIT = 50`, `MAX_LIMIT = 100` and `MAX_IDS = 500`.

- [ ] **Step 1: Write the failing tests**

`services/organizations/tests/test_params.py`:

```python
from django.http import QueryDict
from django.test import SimpleTestCase

from services.organizations.params import (
    DEFAULT_LIMIT,
    DEFAULT_SORT,
    MAX_LIMIT,
    PortfolioParams,
    parse_params,
)


class ParseParamsTests(SimpleTestCase):
    def test_nothing_given_is_the_defaults(self):
        self.assertEqual(parse_params({}), PortfolioParams())
        params = parse_params({})
        self.assertEqual(params.sort, "-arr")
        self.assertEqual(params.limit, 50)
        self.assertIsNone(params.ids)

    def test_a_query_dict_works_like_a_dict(self):
        query = QueryDict("lifecycle=live,renewal&health=poor&owner=unassigned")
        params = parse_params(query)
        self.assertEqual(params.lifecycles, ("live", "renewal"))
        self.assertEqual(params.health, ("poor",))
        self.assertEqual(params.owner, "unassigned")

    def test_owner_is_an_id_or_unassigned(self):
        self.assertEqual(parse_params({"owner": "7"}).owner, 7)
        self.assertEqual(parse_params({"owner": "unassigned"}).owner, "unassigned")
        self.assertIsNone(parse_params({"owner": "carl"}).owner)

    def test_lists_drop_what_they_do_not_know(self):
        params = parse_params(
            {"lifecycle": "live, bogus,,churn", "health": "good,purple", "product": "3,x,4"}
        )
        self.assertEqual(params.lifecycles, ("live", "churn"))
        self.assertEqual(params.health, ("good",))
        self.assertEqual(params.products, (3, 4))

    def test_renews_within_is_one_of_three_windows(self):
        self.assertEqual(parse_params({"renews_within": "90"}).renews_within, 90)
        self.assertIsNone(parse_params({"renews_within": "45"}).renews_within)
        self.assertIsNone(parse_params({"renews_within": "soon"}).renews_within)

    def test_nps_is_one_band(self):
        self.assertEqual(parse_params({"nps": "detractor"}).nps, "detractor")
        self.assertIsNone(parse_params({"nps": "happy"}).nps)

    def test_ids_present_but_unusable_names_nothing(self):
        self.assertEqual(parse_params({"ids": ""}).ids, ())
        self.assertEqual(parse_params({"ids": "x,y"}).ids, ())
        self.assertEqual(parse_params({"ids": "5,zz,6"}).ids, (5, 6))

    def test_at_most_five_hundred_ids_are_read(self):
        raw = ",".join(["1"] * 500 + ["2"])
        self.assertNotIn(2, parse_params({"ids": raw}).ids)

    def test_include_churned_is_only_the_literal_one(self):
        self.assertTrue(parse_params({"include_churned": "1"}).include_churned)
        self.assertFalse(parse_params({"include_churned": "true"}).include_churned)

    def test_sort_accepts_known_keys_with_an_optional_minus(self):
        self.assertEqual(parse_params({"sort": "risk"}).sort, "risk")
        self.assertEqual(parse_params({"sort": "-total_hires"}).sort, "-total_hires")
        self.assertEqual(parse_params({"sort": "--arr"}).sort, DEFAULT_SORT)
        self.assertEqual(parse_params({"sort": "email"}).sort, DEFAULT_SORT)
        params = parse_params({"sort": "-touch"})
        self.assertEqual((params.sort_key, params.descending), ("touch", True))
        params = parse_params({"sort": "name"})
        self.assertEqual((params.sort_key, params.descending), ("name", False))

    def test_group_value_only_counts_with_a_group(self):
        self.assertEqual(parse_params({"group": "owner"}).group, "owner")
        self.assertEqual(parse_params({"group": "colour"}).group, "")
        self.assertIsNone(parse_params({"group_value": "live"}).group_value)
        params = parse_params({"group": "lifecycle", "group_value": "live"})
        self.assertEqual(params.group_value, "live")

    def test_limit_defaults_and_is_capped(self):
        self.assertEqual(parse_params({"limit": "20"}).limit, 20)
        self.assertEqual(parse_params({"limit": "1000"}).limit, MAX_LIMIT)
        self.assertEqual(parse_params({"limit": "0"}).limit, DEFAULT_LIMIT)
        self.assertEqual(parse_params({"limit": "lots"}).limit, DEFAULT_LIMIT)

    def test_search_is_trimmed(self):
        self.assertEqual(parse_params({"search": "  pizza "}).search, "pizza")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.organizations.tests.test_params --noinput`
Expected: an ERROR, `ModuleNotFoundError: No module named 'services.organizations'`

- [ ] **Step 3: Create the app and implement `params.py`**

`services/organizations/apps.py`:

```python
from django.apps import AppConfig


class OrganizationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "services.organizations"
    verbose_name = "Organizations portfolio"
```

`config/settings.py`: add `"services.organizations",` to `INSTALLED_APPS` directly after `"services.mcp",`.

`services/organizations/params.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.organizations.tests.test_params --noinput`
Expected: all 13 tests pass.

- [ ] **Step 5: Lint and commit**

```bash
venv/bin/ruff check --fix services/organizations config && venv/bin/ruff format services/organizations config
git add services/organizations config/settings.py
git commit -m "feat(organizations): the portfolio app and its query parameters

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: The filtered book — visibility, scope and every filter

**Files:**
- Create: `services/organizations/book.py`, `services/organizations/tests/fixtures.py`, `services/organizations/tests/test_book.py`

**Interfaces:**
- Consumes: `PortfolioParams` and `parse_params` from Task 1.
- Produces:
  - `book.filtered_queryset(user, params: PortfolioParams, *, today: date) -> QuerySet[Customer]`: visibility first, then scope, then filters. It carries no annotations beyond the search cast.
  - `book.CHURNED: Q`.
  - `book.health_q(category: str) -> Q`.
  - `book.NPS_Q: dict[str, Q]`.
  - `tests.fixtures.PortfolioFixture(TestCase)`. It has `self.today`, `self.org` (USD), `self.admin` (Alice Admin, admin role, Leadership), `self.csm` (Carl CSM), `self.other` (Dana CSM) and `self.other_org` (Globex). `customer(name, *, owner=<Carl>, organisation=None, **fields)` creates a Good (8.0), USD, 12,000-ARR customer. Pass `owner=None` for an unowned one.

- [ ] **Step 1: Write the fixture and the failing tests**

`services/organizations/tests/fixtures.py`:

```python
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from services.accounts.models import Organisation, User
from services.customers.models import Customer

_CARL = object()


class PortfolioFixture(TestCase):
    """Carl and Dana are CSMs in Acme, Alice is its admin, and Globex is
    another tenant. `customer()` makes a Good, USD, 12,000-ARR customer owned by
    Carl unless a test says otherwise, so each test's differences are its own."""

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

    def customer(self, name, *, owner=_CARL, organisation=None, **fields):
        values = {
            "health_score": Decimal("8.0"),
            "arr_billed_at_account": Decimal("12000"),
            "currency": "USD",
            **fields,
        }
        return Customer.objects.create(
            organisation=organisation or self.org,
            name=name,
            owner=self.csm if owner is _CARL else owner,
            **values,
        )
```

`services/organizations/tests/test_book.py`:

```python
from datetime import timedelta
from decimal import Decimal

from services.customers.models import Customer, Product
from services.organizations import book
from services.organizations.params import parse_params
from services.organizations.tests.fixtures import PortfolioFixture


class FilteredQuerysetTests(PortfolioFixture):
    def names(self, user=None, **query):
        queryset = book.filtered_queryset(user or self.csm, parse_params(query), today=self.today)
        return set(queryset.values_list("name", flat=True))

    def test_archived_are_hidden(self):
        self.customer("Active")
        self.customer("Archived", is_archived=True)
        self.assertEqual(self.names(), {"Active"})

    def test_churned_are_hidden_unless_asked_for(self):
        self.customer("Live")
        self.customer("Left", lifecycle_stage="churn", churn_date=self.today - timedelta(days=10))
        self.customer("Dated", lifecycle_stage="live", churn_date=self.today - timedelta(days=3))
        self.customer("Staged", lifecycle_stage="churn")
        self.assertEqual(self.names(), {"Live"})
        self.assertEqual(self.names(include_churned="1"), {"Live", "Left", "Dated", "Staged"})
        self.assertEqual(self.names(include_churned="yes"), {"Live"})
        self.assertEqual(self.names(lifecycle="churn"), {"Left", "Staged"})
        self.assertEqual(self.names(lifecycle="churn,live"), {"Live", "Left", "Dated", "Staged"})

    def test_ids_name_exactly_those_records_archived_and_churned_included(self):
        live = self.customer("Live")
        archived = self.customer("Archived", is_archived=True)
        left = self.customer(
            "Left", lifecycle_stage="churn", churn_date=self.today - timedelta(days=10)
        )
        self.customer("Unnamed")
        self.assertEqual(
            self.names(ids=f"{live.pk},{archived.pk},{left.pk}"), {"Live", "Archived", "Left"}
        )
        self.assertEqual(self.names(ids=""), set())
        self.assertEqual(self.names(ids="x,y"), set())
        self.assertEqual(self.names(ids=f"{live.pk},zz"), {"Live"})
        self.assertEqual(self.names(ids=",".join(["0"] * 500 + [str(live.pk)])), set())
        # The other filters still apply on top of an id list.
        self.assertEqual(self.names(ids=f"{live.pk}", health="poor"), set())

    def test_other_owners_and_other_tenants_never_appear(self):
        self.customer("Mine")
        self.customer("Nobody's", owner=None)
        danas = self.customer("Dana's", owner=self.other)
        globex = self.customer("Globex's", owner=None, organisation=self.other_org)
        self.assertEqual(self.names(), {"Mine", "Nobody's"})
        self.assertEqual(self.names(ids=f"{danas.pk},{globex.pk}"), set())
        self.assertEqual(self.names(search="Dana"), set())
        self.assertEqual(self.names(user=self.admin), {"Mine", "Nobody's", "Dana's"})

    def test_search_matches_name_or_revenact_id(self):
        pizza = self.customer("Pizza Hut")
        self.customer("Globex")
        self.assertEqual(self.names(search="pIZ"), {"Pizza Hut"})
        self.assertIn("Pizza Hut", self.names(search=str(pizza.pk)))

    def test_owner(self):
        self.customer("Mine")
        self.customer("Nobody's", owner=None)
        self.customer("Dana's", owner=self.other)
        self.assertEqual(self.names(user=self.admin, owner=str(self.other.pk)), {"Dana's"})
        self.assertEqual(self.names(user=self.admin, owner="unassigned"), {"Nobody's"})
        self.assertEqual(
            self.names(user=self.admin, owner="someone"), {"Mine", "Nobody's", "Dana's"}
        )

    def test_lifecycle_is_a_list_and_unknown_stages_are_ignored(self):
        self.customer("Live", lifecycle_stage="live")
        self.customer("Renewing", lifecycle_stage="renewal")
        self.customer("Onboarding", lifecycle_stage="onboarding")
        self.assertEqual(self.names(lifecycle="live,renewal"), {"Live", "Renewing"})
        self.assertEqual(self.names(lifecycle="live,bogus"), {"Live"})
        self.assertEqual(self.names(lifecycle="bogus"), {"Live", "Renewing", "Onboarding"})

    def test_health_bands_match_the_derived_category_at_every_boundary(self):
        for name, score in (("7.0", "7.0"), ("6.9", "6.9"), ("4.0", "4.0"), ("3.9", "3.9")):
            self.customer(name, health_score=Decimal(score))
        self.assertEqual(self.names(health="good"), {"7.0"})
        self.assertEqual(self.names(health="average"), {"6.9", "4.0"})
        self.assertEqual(self.names(health="poor"), {"3.9"})
        self.assertEqual(self.names(health="good,poor"), {"7.0", "3.9"})
        for category in ("good", "average", "poor"):
            for customer in Customer.objects.filter(name__in=self.names(health=category)):
                self.assertEqual(customer.health_category, category)

    def test_product(self):
        core = Product.objects.create(organisation=self.org, name="Core")
        extra = Product.objects.create(organisation=self.org, name="Extra")
        self.customer("On core", primary_product=core)
        self.customer("On extra", primary_product=extra)
        self.customer("On nothing")
        self.assertEqual(self.names(product=str(core.pk)), {"On core"})
        self.assertEqual(self.names(product=f"{core.pk},{extra.pk},x"), {"On core", "On extra"})

    def test_renews_within_includes_overdue(self):
        self.customer("In 30", renewal_date=self.today + timedelta(days=30))
        self.customer("In 31", renewal_date=self.today + timedelta(days=31))
        self.customer("Overdue", renewal_date=self.today - timedelta(days=5))
        self.customer("Undated")
        self.assertEqual(self.names(renews_within="30"), {"In 30", "Overdue"})
        self.assertEqual(self.names(renews_within="90"), {"In 30", "In 31", "Overdue"})
        self.assertEqual(self.names(renews_within="45"), {"In 30", "In 31", "Overdue", "Undated"})

    def test_nps_bands_follow_the_stats_rule(self):
        self.customer("Promoter", nps_score=10)
        self.customer("Passive", nps_score=0)
        self.customer("Detractor", nps_score=-20)
        self.customer("Unscored")
        self.assertEqual(self.names(nps="promoter"), {"Promoter"})
        self.assertEqual(self.names(nps="passive"), {"Passive"})
        self.assertEqual(self.names(nps="detractor"), {"Detractor"})
        self.assertEqual(len(self.names(nps="happy")), 4)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.organizations.tests.test_book --noinput`
Expected: an ERROR, `ImportError: cannot import name 'book' from 'services.organizations'`

- [ ] **Step 3: Implement `book.py` (filters only)**

`services/organizations/book.py`:

```python
"""The Organizations portfolio's book: the viewer's customers, narrowed by the
page's filters, with every signal a row shows.

**Visibility first.** Everything starts from `visible_customers(user)`; a raw
`?ids=` or `?owner=` can only narrow that, never widen it.

**Scope.** Archived customers are hidden unless named by `ids`, the way
`/customers/?ids=` treats them. Churned customers — a `churn_date`, or the
Churn stage — are hidden unless the viewer asks (`include_churned=1`, or
`churn` among the lifecycle filters) or names them by id: the working list is
the book you hold.
"""

from datetime import timedelta

from django.db.models import CharField, Q
from django.db.models.functions import Cast

from services.customers.models import Customer
from services.customers.scoping import visible_customers

from .params import PortfolioParams

#: Either marker means the customer has left. The churn modal sets both; a
#: seeded or imported row may carry only one, and either should hide it.
CHURNED = Q(churn_date__isnull=False) | Q(lifecycle_stage=Customer.LifecycleStage.CHURN)

#: `CustomerStatsView`'s buckets: NPS is stored per customer as −100…100, so
#: the band is its sign.
NPS_Q = {
    "promoter": Q(nps_score__gt=0),
    "passive": Q(nps_score=0),
    "detractor": Q(nps_score__lt=0),
}


def health_q(category):
    """`Customer.health_category` as SQL, read off the same thresholds, so the
    filter and the ring can never disagree about a boundary score."""
    upper = None
    for threshold, name in Customer.HEALTH_THRESHOLDS:
        if name == category:
            lower = Q(health_score__gte=threshold)
            return lower if upper is None else lower & Q(health_score__lt=upper)
        upper = threshold
    return Q(health_score__lt=upper)


def filtered_queryset(user, params: PortfolioParams, *, today):
    # SOC2:AUTH-02 record visibility comes first; filters only narrow it
    customers = visible_customers(user)

    if params.ids is None:
        customers = customers.filter(is_archived=False)
        asked_for_churned = (
            params.include_churned or Customer.LifecycleStage.CHURN in params.lifecycles
        )
        if not asked_for_churned:
            customers = customers.exclude(CHURNED)
    elif params.ids:
        customers = customers.filter(pk__in=params.ids)
    else:
        return customers.none()

    if params.search:
        customers = customers.annotate(id_as_text=Cast("id", CharField())).filter(
            Q(name__icontains=params.search) | Q(id_as_text__icontains=params.search)
        )

    if params.owner == "unassigned":
        customers = customers.filter(owner__isnull=True)
    elif params.owner is not None:
        customers = customers.filter(owner_id=params.owner)

    if params.lifecycles:
        customers = customers.filter(lifecycle_stage__in=params.lifecycles)

    if params.health:
        bands = Q()
        for category in params.health:
            bands |= health_q(category)
        customers = customers.filter(bands)

    if params.products:
        customers = customers.filter(primary_product_id__in=params.products)

    if params.renews_within is not None:
        deadline = today + timedelta(days=params.renews_within)
        customers = customers.filter(renewal_date__isnull=False, renewal_date__lte=deadline)

    if params.nps:
        customers = customers.filter(NPS_Q[params.nps])

    return customers
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.organizations.tests.test_book --noinput`
Expected: all 11 tests pass.

- [ ] **Step 5: Lint and commit**

```bash
venv/bin/ruff check --fix services/organizations && venv/bin/ruff format services/organizations
git add services/organizations
git commit -m "feat(organizations): the filtered book, visibility first

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Row signals computed by the dashboard's own code

**Files:**
- Modify: `services/organizations/book.py`
- Create: `services/organizations/tests/test_signals.py`

**Interfaces:**
- Consumes: `filtered_queryset`, `CHURNED` and `PortfolioFixture` from Task 2.
- Produces:
  - `book.Entry` (dataclass) with the fields `customer: Customer`, `arr: float | None` (in the organisation's currency, `None` when there is no FX rate), `trend: list[float]`, `triage: Triage`, `last_touch_days: int | None`, `renewal_days: int | None`, `urgent_tickets: int`, `churned: bool` and `signal: dict | None`.
  - `book.Portfolio` (dataclass) with the fields `entries: list[Entry]`, `organisation: Organisation`, `rates: dict[str, Decimal]` and `today: date`.
  - `book.load_portfolio(user, params, *, today) -> Portfolio`. Its entries come back in the queryset's default order (by name).
  - `book.urgent_ticket_counts(user, ids: list[int]) -> Counter[int]`.
  - `book.signal_for(*, churned: bool, renewal_days: int | None, risk: int, urgent_tickets: int) -> dict | None`, which returns `{"kind": "renewal_overdue" | "risk" | "tickets", "label": str}` or None.
  - `book.TREND_MONTHS = 6`.

- [ ] **Step 1: Write the failing tests**

`services/organizations/tests/test_signals.py`:

```python
from datetime import timedelta
from decimal import Decimal

from django.test import SimpleTestCase
from rest_framework.test import APIClient

from services.attention import rules as attention_rules
from services.customers.activity_tracking import gone_quiet_values
from services.customers.contact import last_contact_by_customer
from services.customers.models import Account, Activity, HealthSnapshot, Ticket
from services.fx_rates.models import FxRate
from services.organizations import book
from services.organizations.params import parse_params
from services.organizations.tests.fixtures import PortfolioFixture


class SignalPriorityTests(SimpleTestCase):
    def test_overdue_beats_risk_beats_tickets(self):
        signal = book.signal_for
        self.assertEqual(
            signal(churned=False, renewal_days=-1, risk=90, urgent_tickets=3),
            {"kind": "renewal_overdue", "label": "Renewal overdue"},
        )
        self.assertEqual(
            signal(churned=False, renewal_days=0, risk=40, urgent_tickets=3),
            {"kind": "risk", "label": "Risk 40"},
        )
        self.assertEqual(
            signal(churned=False, renewal_days=None, risk=39, urgent_tickets=1),
            {"kind": "tickets", "label": "1 open ticket"},
        )
        self.assertEqual(
            signal(churned=False, renewal_days=None, risk=0, urgent_tickets=2)["label"],
            "2 open tickets",
        )

    def test_nothing_or_churned_is_no_signal(self):
        self.assertIsNone(book.signal_for(churned=False, renewal_days=5, risk=39, urgent_tickets=0))
        self.assertIsNone(
            book.signal_for(churned=True, renewal_days=-30, risk=90, urgent_tickets=4)
        )


class DashboardEqualityTests(PortfolioFixture):
    """Each row signal equals what the dashboard shows for the same account."""

    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.csm)

    def entry(self, customer, user=None, **query):
        portfolio = book.load_portfolio(user or self.csm, parse_params(query), today=self.today)
        return next(e for e in portfolio.entries if e.customer.pk == customer.pk)

    def health_row(self, customer, **query):
        response = self.api.get("/api/v1/customers/health/", query)
        self.assertEqual(response.status_code, 200)
        return next(row for row in response.data["results"] if row["id"] == customer.pk)

    def test_health_trend_and_triage_equal_the_health_overview(self):
        pizza = self.customer(
            "Pizza Hut",
            health_score=Decimal("4.9"),
            csm_pulse_score=4,
            ai_pulse_value=1,
            renewal_date=self.today + timedelta(days=40),
        )
        for months_ago, score in ((8, "7.5"), (5, "6.2"), (4, "5.8"), (3, "5.5"), (2, "5.1")):
            HealthSnapshot.objects.create(
                customer=pizza,
                captured_on=self.today - timedelta(days=30 * months_ago),
                health_score=Decimal(score),
            )
        HealthSnapshot.objects.create(
            customer=pizza, captured_on=self.today - timedelta(days=30), health_score=Decimal("5.0")
        )

        entry = self.entry(pizza)
        dashboard = self.health_row(pizza)
        six_months = self.health_row(pizza, history_months="6")

        self.assertEqual(float(entry.customer.health_score), float(dashboard["health_score"]))
        self.assertEqual(entry.customer.health_category, dashboard["health_category"])
        # The trend is the last five month-end snapshots in the six-month window,
        # then today's score, so the line ends at the ring's number.
        history = [float(point["health_score"]) for point in six_months["history"]]
        self.assertEqual(entry.trend, history[-5:] + [float(dashboard["health_score"])])
        self.assertEqual(entry.trend, [6.2, 5.8, 5.5, 5.1, 5.0, 4.9])
        # Triage reads the Health view's own (12-month) history.
        self.assertEqual(entry.triage.score, dashboard["triage_score"])
        self.assertEqual(entry.triage.direction, dashboard["triage_direction"])
        # Average 22 + AI three below the CSM 33 + renews in 40 days 18.
        self.assertEqual(entry.triage.score, 73)

    def test_a_customer_with_no_snapshots_has_a_one_point_trend(self):
        fresh = self.customer("Fresh", health_score=Decimal("6.0"))
        self.assertEqual(self.entry(fresh).trend, [6.0])

    def test_arr_is_converted_like_the_dashboard(self):
        FxRate.objects.create(
            organisation=self.org, currency="EUR", rate_to_org_currency=Decimal("1.1")
        )
        euro = self.customer("Euro", currency="EUR", arr_billed_at_account=Decimal("60000"))
        pound = self.customer("Pound", currency="GBP", arr_billed_at_account=Decimal("10000"))
        dollar = self.customer("Dollar", arr_billed_at_account=Decimal("12345.67"))
        for customer in (euro, pound, dollar):
            self.assertEqual(self.entry(customer).arr, self.health_row(customer)["arr"])
        self.assertEqual(self.entry(euro).arr, 66000.0)
        self.assertIsNone(self.entry(pound).arr)

    def test_last_touch_is_activity_trackings_rule(self):
        quiet = self.customer("Quiet")
        never = self.customer("Never")
        recent = self.customer("Recent")
        Activity.objects.create(
            customer=quiet,
            type=Activity.ActivityType.OTHER,
            occurred_at=self.today - timedelta(days=70),
        )
        Activity.objects.create(
            customer=recent,
            type=Activity.ActivityType.OTHER,
            occurred_at=self.today - timedelta(days=3),
        )
        dark = gone_quiet_values(self.csm, {})
        self.assertEqual(self.entry(quiet).last_touch_days, dark[quiet.pk])
        self.assertEqual(self.entry(quiet).last_touch_days, 70)
        self.assertIsNone(self.entry(never).last_touch_days)
        self.assertIsNone(dark[never.pk])
        latest = last_contact_by_customer([recent.pk])[recent.pk]
        self.assertEqual(self.entry(recent).last_touch_days, (self.today - latest).days)

    def ticket(self, number, **fields):
        values = {
            "ticket_number": f"TKT-{number}",
            "title": "Down",
            "status": Ticket.Status.OPEN,
            "priority": Ticket.Priority.HIGH,
            "opened_at": self.today,
            **fields,
        }
        return Ticket.objects.create(**values)

    def test_urgent_tickets_equal_the_attention_list(self):
        acme = self.customer("Acme")
        division = Account.objects.create(name="Acme EMEA")
        division.customers.add(acme)
        self.ticket(1, customer=acme)
        self.ticket(2, customer=acme, priority=Ticket.Priority.CRITICAL)
        self.ticket(3, account=division)
        self.ticket(4, customer=acme, priority=Ticket.Priority.MEDIUM)
        self.ticket(5, customer=acme, status=Ticket.Status.RESOLVED)
        # Another department's ticket: Carl (Customer Success) cannot read it.
        self.ticket(6, customer=acme, department="engineering")

        entry = self.entry(acme)
        items = attention_rules.build_items(self.csm, {}, today=self.today)
        support = next(item for item in items if item["key"] == f"support:{acme.pk}")
        self.assertEqual(entry.urgent_tickets, support["fingerprint"]["count"])
        self.assertEqual(entry.urgent_tickets, 3)
        self.assertEqual(entry.signal, {"kind": "tickets", "label": "3 open tickets"})

    def test_signals_on_real_rows(self):
        overdue = self.customer(
            "Overdue", health_score=Decimal("2.0"), renewal_date=self.today - timedelta(days=5)
        )
        risky = self.customer("Risky", health_score=Decimal("2.0"))
        calm = self.customer("Calm")
        left = self.customer(
            "Left",
            lifecycle_stage="churn",
            churn_date=self.today - timedelta(days=9),
            renewal_date=self.today - timedelta(days=30),
        )
        self.assertEqual(self.entry(overdue).signal["kind"], "renewal_overdue")
        self.assertEqual(self.entry(overdue).renewal_days, -5)
        self.assertEqual(self.entry(risky).signal, {"kind": "risk", "label": "Risk 66"})
        self.assertIsNone(self.entry(calm).signal)
        churned = self.entry(left, include_churned="1")
        self.assertTrue(churned.churned)
        self.assertIsNone(churned.signal)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.organizations.tests.test_signals --noinput`
Expected: an ERROR, `AttributeError: module 'services.organizations.book' has no attribute 'signal_for'`

- [ ] **Step 3: Add the loading code to `book.py`**

Replace the import block at the top of `services/organizations/book.py` with:

```python
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

from django.db.models import CharField, Prefetch, Q
from django.db.models.functions import Cast

from services.attention.rules import SUPPORT_PRIORITIES
from services.customers.models import Customer, HealthSnapshot, Ticket, with_health_inputs
from services.customers.personal import visible_tickets
from services.customers.scoping import visible_children_q, visible_customers
from services.customers.triage import ACTION_THRESHOLD, Triage, triage
from services.customers.views import CustomerHealthView
from services.fx_rates.conversion import convert_to_org_currency, rates_for

from .params import PortfolioParams
```

Append to the end of `services/organizations/book.py`:

```python
#: How far back the row's sparkline reads. Snapshots are month-end, so six
#: months is five of them plus today's score.
TREND_MONTHS = 6


@dataclass
class Entry:
    customer: Customer
    arr: float | None
    trend: list[float]
    triage: Triage
    last_touch_days: int | None
    renewal_days: int | None
    urgent_tickets: int
    churned: bool
    signal: dict | None


@dataclass
class Portfolio:
    entries: list[Entry]
    organisation: object
    rates: dict
    today: date


def signal_for(*, churned, renewal_days, risk, urgent_tickets):
    """At most one tag per row: overdue renewal, then risk at or above the
    Triage action threshold, then open High/Critical tickets. A churned
    account has already left, so nothing about it is urgent."""
    if churned:
        return None
    if renewal_days is not None and renewal_days < 0:
        return {"kind": "renewal_overdue", "label": "Renewal overdue"}
    if risk >= ACTION_THRESHOLD:
        return {"kind": "risk", "label": f"Risk {risk}"}
    if urgent_tickets:
        noun = "ticket" if urgent_tickets == 1 else "tickets"
        return {"kind": "tickets", "label": f"{urgent_tickets} open {noun}"}
    return None


def urgent_ticket_counts(user, ids):
    """Open High/Critical tickets per company — the attention list's support
    rule (`attention.rules._support_items`): read under the department rule,
    and a ticket on an account counts for each of its companies in `ids`."""
    if not ids:
        return Counter()
    # SOC2:AUTH-02 tickets are read department-wise, on visible parents only
    tickets = (
        visible_tickets(user, Ticket.objects.filter(visible_children_q(user)).distinct())
        .filter(priority__in=SUPPORT_PRIORITIES)
        .exclude(status__in=Ticket.RESOLVED_STATUSES)
        .filter(Q(customer_id__in=ids) | Q(account__customers__id__in=ids))
        .distinct()
        .prefetch_related("account__customers")
    )
    wanted = set(ids)
    counts = Counter()
    for ticket in tickets:
        targets = {ticket.customer_id} if ticket.customer_id else set()
        if ticket.account_id:
            targets |= {customer.pk for customer in ticket.account.customers.all()}
        for customer_id in targets & wanted:
            counts[customer_id] += 1
    return counts


def _entry(customer, *, today, organisation, rates, urgent):
    converted = convert_to_org_currency(
        customer.arr_billed_at_account, customer.currency, organisation, rates=rates
    )
    snapshots = list(customer.health_snapshots.all())
    # CustomerHealthRowSerializer._triage's own call, over the same window.
    result = triage(
        health_category=customer.health_category,
        csm_pulse=customer.csm_pulse_score,
        ai_pulse=customer.ai_pulse_value,
        renewal_date=customer.renewal_date,
        history=[snapshot.health_category for snapshot in snapshots],
        today=today,
    )
    since = today - timedelta(days=31 * TREND_MONTHS)
    recent = [float(s.health_score) for s in snapshots if s.captured_on >= since]
    trend = recent[-(TREND_MONTHS - 1) :] + [float(customer.health_score)]
    last_touch = customer._last_touch_on
    renewal_days = None if customer.renewal_date is None else (customer.renewal_date - today).days
    churned = (
        customer.churn_date is not None or customer.lifecycle_stage == Customer.LifecycleStage.CHURN
    )
    return Entry(
        customer=customer,
        arr=None if converted is None else float(converted),
        trend=trend,
        triage=result,
        last_touch_days=None if last_touch is None else (today - last_touch).days,
        renewal_days=renewal_days,
        urgent_tickets=urgent,
        churned=churned,
        signal=signal_for(
            churned=churned, renewal_days=renewal_days, risk=result.score, urgent_tickets=urgent
        ),
    )


def load_portfolio(user, params: PortfolioParams, *, today):
    """The whole filtered book with its signals, in a fixed number of queries:
    the customers (touch and ticket subqueries from `with_health_inputs`, the
    people and product joined), their snapshots, the FX table, and the urgent
    tickets. `with_health_inputs`' CSAT prefetch is dropped: no row reads it."""
    organisation = user.organisation
    earliest = today - timedelta(days=31 * CustomerHealthView.DEFAULT_HISTORY_MONTHS)
    snapshots = (
        HealthSnapshot.objects.filter(captured_on__gte=earliest)
        .only("id", "customer_id", "captured_on", "health_score")
        .order_by("captured_on")
    )
    queryset = (
        with_health_inputs(filtered_queryset(user, params, today=today))
        .prefetch_related(None)
        .select_related("owner", "primary_product", "created_by", "modified_by")
        .prefetch_related(Prefetch("health_snapshots", queryset=snapshots))
    )
    customers = list(queryset)
    rates = rates_for(organisation)
    urgent = urgent_ticket_counts(user, [customer.pk for customer in customers])
    entries = [
        _entry(
            customer,
            today=today,
            organisation=organisation,
            rates=rates,
            urgent=urgent.get(customer.pk, 0),
        )
        for customer in customers
    ]
    return Portfolio(entries=entries, organisation=organisation, rates=rates, today=today)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.organizations.tests.test_signals services.organizations.tests.test_book --noinput`
Expected: all tests pass.

- [ ] **Step 5: Lint and commit**

```bash
venv/bin/ruff check --fix services/organizations && venv/bin/ruff format services/organizations
git add services/organizations
git commit -m "feat(organizations): row signals from the dashboard's own code

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Sort, group, `group_value` and the cursor

**Files:**
- Create: `services/organizations/shape.py`, `services/organizations/tests/test_shape.py`

**Interfaces:**
- Consumes: `Portfolio`, `Entry` and `load_portfolio` from Task 3; `PortfolioParams`, `parse_params` and `NUMERIC_SORT_KEYS` from Task 1.
- Produces:
  - `shape.order_entries(portfolio, sort_key: str, descending: bool) -> list[Entry]`. Nulls always sort last, and ties break by name then id.
  - `shape.group_key(entry, group: str) -> tuple[str, str]` (key, label).
  - `shape.build_groups(entries, group) -> list[dict]`, where each dict is `{"key", "label", "count", "arr"}`, in section order.
  - `shape.select(portfolio, params) -> tuple[list[Entry], list[dict]]`, which returns the ordered (and `group_value`-narrowed) entries and the groups.
  - `shape.paginate(entries, *, cursor: str, limit: int) -> tuple[list[Entry], str | None]`.
  - `shape.encode_cursor(entry_id: int, offset: int) -> str` and `shape.decode_cursor(cursor: str) -> tuple[int, int] | None`.
  - The constants `shape.HEALTH_GROUP_ORDER`, `shape.RENEWAL_WINDOWS` and `shape.MONEY_FIELDS`.

- [ ] **Step 1: Write the failing tests**

`services/organizations/tests/test_shape.py`:

```python
from datetime import timedelta
from decimal import Decimal

from services.customers.models import Activity, Customer, Product
from services.organizations import book, shape
from services.organizations.params import parse_params
from services.organizations.tests.fixtures import PortfolioFixture


class ShapeFixture(PortfolioFixture):
    """Four customers, read by Alice (who sees every owner):

    | name    | owner | ARR (USD)   | health | renewal | last touch | product | stage      |
    | Alpha   | Carl  | 30,000      | 8.0    | +100    | 5 days     | Zeta    | live       |
    | Bravo   | Dana  | 20,000      | 5.0    | +10     | never      | Zeta    | onboarding |
    | Charlie | —     | 10,000      | 3.0    | —       | 20 days    | Acme    | renewal    |
    | Delta   | Carl  | GBP, no rate| 8.0    | −3      | 1 day      | —       | live       |

    Triage: Alpha 8 (renews 91-180d), Bravo 40 (Average 22 + renews ≤ 90d 18),
    Charlie 66 (Poor), Delta 18 (overdue counts as urgent).
    """

    def setUp(self):
        super().setUp()
        zeta = Product.objects.create(organisation=self.org, name="Zeta")
        acme = Product.objects.create(organisation=self.org, name="Acme")
        self.alpha = self.customer(
            "Alpha",
            arr_billed_at_account=Decimal("30000"),
            renewal_date=self.today + timedelta(days=100),
            primary_product=zeta,
            lifecycle_stage="live",
            total_hires=5,
        )
        self.bravo = self.customer(
            "Bravo",
            owner=self.other,
            arr_billed_at_account=Decimal("20000"),
            health_score=Decimal("5.0"),
            renewal_date=self.today + timedelta(days=10),
            primary_product=zeta,
            lifecycle_stage="onboarding",
        )
        self.charlie = self.customer(
            "Charlie",
            owner=None,
            arr_billed_at_account=Decimal("10000"),
            health_score=Decimal("3.0"),
            primary_product=acme,
            lifecycle_stage="renewal",
            total_hires=50,
        )
        self.delta = self.customer(
            "Delta",
            currency="GBP",
            arr_billed_at_account=Decimal("99999"),
            renewal_date=self.today - timedelta(days=3),
            lifecycle_stage="live",
            total_hires=7,
        )
        for customer, days in ((self.alpha, 5), (self.charlie, 20), (self.delta, 1)):
            Activity.objects.create(
                customer=customer,
                type=Activity.ActivityType.OTHER,
                occurred_at=self.today - timedelta(days=days),
            )

    def portfolio(self, **query):
        return book.load_portfolio(self.admin, parse_params(query), today=self.today)

    def names(self, entries):
        return [entry.customer.name for entry in entries]

    def ordered(self, sort):
        params = parse_params({"sort": sort})
        return self.names(shape.order_entries(self.portfolio(), params.sort_key, params.descending))


class OrderTests(ShapeFixture):
    def test_default_is_arr_descending_with_unconvertible_last(self):
        entries, groups = shape.select(self.portfolio(), parse_params({}))
        self.assertEqual(self.names(entries), ["Alpha", "Bravo", "Charlie", "Delta"])
        self.assertEqual(groups, [])

    def test_nulls_are_last_in_both_directions(self):
        self.assertEqual(self.ordered("arr"), ["Charlie", "Bravo", "Alpha", "Delta"])
        self.assertEqual(self.ordered("renewal"), ["Delta", "Bravo", "Alpha", "Charlie"])
        self.assertEqual(self.ordered("-renewal"), ["Alpha", "Bravo", "Delta", "Charlie"])

    def test_name(self):
        self.assertEqual(self.ordered("name"), ["Alpha", "Bravo", "Charlie", "Delta"])
        self.assertEqual(self.ordered("-name"), ["Delta", "Charlie", "Bravo", "Alpha"])

    def test_never_touched_is_the_longest_silence(self):
        self.assertEqual(self.ordered("-touch"), ["Bravo", "Charlie", "Alpha", "Delta"])
        self.assertEqual(self.ordered("touch"), ["Delta", "Alpha", "Charlie", "Bravo"])

    def test_risk(self):
        self.assertEqual(self.ordered("-risk"), ["Charlie", "Bravo", "Delta", "Alpha"])

    def test_ties_break_by_name_in_both_directions(self):
        self.assertEqual(self.ordered("health"), ["Charlie", "Bravo", "Alpha", "Delta"])
        self.assertEqual(self.ordered("-health"), ["Alpha", "Delta", "Bravo", "Charlie"])

    def test_numeric_field(self):
        self.assertEqual(self.ordered("-total_hires"), ["Charlie", "Delta", "Alpha", "Bravo"])

    def test_money_fields_sort_converted(self):
        Customer.objects.filter(pk=self.alpha.pk).update(arr_billed_at_hq=Decimal("100"))
        Customer.objects.filter(pk=self.delta.pk).update(arr_billed_at_hq=Decimal("5000"))
        # Delta's GBP has no rate, so it cannot be compared and goes last.
        self.assertEqual(self.ordered("-arr_billed_at_hq")[0], "Alpha")
        self.assertEqual(self.ordered("-arr_billed_at_hq")[-1], "Delta")


class GroupTests(ShapeFixture):
    def select(self, **query):
        return shape.select(self.portfolio(**query), parse_params(query))

    def test_health_sections_are_poor_average_good(self):
        entries, groups = self.select(group="health")
        self.assertEqual(self.names(entries), ["Charlie", "Bravo", "Alpha", "Delta"])
        self.assertEqual(
            groups,
            [
                {"key": "poor", "label": "Poor", "count": 1, "arr": 10000.0},
                {"key": "average", "label": "Average", "count": 1, "arr": 20000.0},
                {"key": "good", "label": "Good", "count": 2, "arr": 30000.0},
            ],
        )

    def test_owner_sections_name_the_unassigned_last(self):
        _entries, groups = self.select(group="owner")
        self.assertEqual(
            [(g["key"], g["label"], g["count"]) for g in groups],
            [
                (str(self.csm.pk), "Carl CSM", 2),
                (str(self.other.pk), "Dana CSM", 1),
                ("unassigned", "Unassigned", 1),
            ],
        )

    def test_lifecycle_sections_follow_the_stage_order(self):
        _entries, groups = self.select(group="lifecycle")
        self.assertEqual([g["key"] for g in groups], ["onboarding", "live", "renewal"])
        self.assertEqual(groups[1]["label"], "Live")

    def test_product_sections_put_no_product_last(self):
        _entries, groups = self.select(group="product")
        self.assertEqual([g["label"] for g in groups], ["Acme", "Zeta", "No product"])
        self.assertEqual(groups[-1]["key"], "none")

    def test_renewal_windows(self):
        _entries, groups = self.select(group="renewal")
        self.assertEqual([g["key"] for g in groups], ["overdue", "30", "180", "none"])
        self.assertEqual(
            [g["label"] for g in groups],
            ["Overdue", "Within 30 days", "91–180 days", "No renewal date"],
        )

    def test_group_value_narrows_rows_but_not_groups(self):
        entries, groups = self.select(group="health", group_value="good")
        self.assertEqual(self.names(entries), ["Alpha", "Delta"])
        self.assertEqual(sum(g["count"] for g in groups), 4)
        entries, _groups = self.select(group="health", group_value="purple")
        self.assertEqual(entries, [])


class CursorTests(ShapeFixture):
    def page(self, cursor="", **query):
        entries, _groups = shape.select(self.portfolio(**query), parse_params(query))
        return shape.paginate(entries, cursor=cursor, limit=2)

    def test_pages_follow_on_without_overlap(self):
        first, cursor = self.page(sort="name")
        self.assertEqual(self.names(first), ["Alpha", "Bravo"])
        self.assertIsNotNone(cursor)
        second, last = self.page(cursor, sort="name")
        self.assertEqual(self.names(second), ["Charlie", "Delta"])
        self.assertIsNone(last)

    def test_a_bad_cursor_is_the_first_page(self):
        for cursor in ("%%%", "bm90IGpzb24", shape.encode_cursor(0, -4)[:-2]):
            page, _next = self.page(cursor, sort="name")
            self.assertEqual(self.names(page), ["Alpha", "Bravo"])

    def test_when_the_cursor_row_leaves_the_set_nothing_is_skipped(self):
        _first, cursor = self.page(sort="name")
        Customer.objects.filter(pk=self.bravo.pk).update(is_archived=True)
        second, _last = self.page(cursor, sort="name")
        self.assertEqual(self.names(second), ["Charlie", "Delta"])

    def test_cursor_round_trip(self):
        self.assertEqual(shape.decode_cursor(shape.encode_cursor(7, 50)), (7, 50))
        self.assertIsNone(shape.decode_cursor(""))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.organizations.tests.test_shape --noinput`
Expected: an ERROR, `ImportError: cannot import name 'shape'`

- [ ] **Step 3: Implement `shape.py`**

`services/organizations/shape.py`:

```python
"""Ordering, grouping, paging and totals for the portfolio — pure functions
over the entries `book.load_portfolio` returns. No queries.

Everything here runs over the whole filtered set: a section header or a tile
that counted only the page would count nothing useful.
"""

import base64
import binascii
import json
import math

from services.customers.models import Customer
from services.fx_rates.conversion import convert_to_org_currency

from .params import NUMERIC_SORT_KEYS

#: Money fields sort in the organisation's currency — a list mixing EUR and
#: USD contracts cannot be ranked on their raw numbers.
MONEY_FIELDS = frozenset(
    {
        "arr_billed_at_hq",
        "implementation_fee",
        "total_contract_value",
        "total_forecasted_renewal_revenue",
    }
)

HEALTH_GROUP_ORDER = ("poor", "average", "good")

RENEWAL_WINDOWS = (
    ("overdue", "Overdue"),
    ("30", "Within 30 days"),
    ("90", "31–90 days"),
    ("180", "91–180 days"),
    ("later", "Later"),
    ("none", "No renewal date"),
)

#: The bucket for "nobody" / "nothing", which always closes the list.
EMPTY_KEYS = frozenset({"unassigned", "none"})


def _numeric(field):
    def get(entry, portfolio):
        raw = getattr(entry.customer, field)
        if raw is None:
            return None
        if field in MONEY_FIELDS:
            converted = convert_to_org_currency(
                raw, entry.customer.currency, portfolio.organisation, rates=portfolio.rates
            )
            return None if converted is None else float(converted)
        return float(raw)

    return get


SORT_GETTERS = {
    "arr": lambda entry, portfolio: entry.arr,
    "health": lambda entry, portfolio: float(entry.customer.health_score),
    "renewal": lambda entry, portfolio: entry.customer.renewal_date,
    # Never contacted is the longest silence there is, as on Activity Tracking.
    "touch": lambda entry, portfolio: (
        math.inf if entry.last_touch_days is None else entry.last_touch_days
    ),
    "risk": lambda entry, portfolio: entry.triage.score,
    "name": lambda entry, portfolio: entry.customer.name.casefold(),
    **{field: _numeric(field) for field in NUMERIC_SORT_KEYS},
}


def _tiebreak(entry):
    return (entry.customer.name.casefold(), entry.customer.pk)


def order_entries(portfolio, sort_key, descending):
    """Missing values last in either direction; ties by name, then id. Python's
    sort is stable with `reverse=True` too, so the tiebreak survives it."""
    getter = SORT_GETTERS[sort_key]
    ranked = sorted(portfolio.entries, key=_tiebreak)
    present = [entry for entry in ranked if getter(entry, portfolio) is not None]
    missing = [entry for entry in ranked if getter(entry, portfolio) is None]
    present.sort(key=lambda entry: getter(entry, portfolio), reverse=descending)
    return present + missing


def renewal_window(days):
    if days is None:
        return "none"
    if days < 0:
        return "overdue"
    if days <= 30:
        return "30"
    if days <= 90:
        return "90"
    if days <= 180:
        return "180"
    return "later"


def group_key(entry, group):
    customer = entry.customer
    if group == "health":
        category = customer.health_category
        return category, Customer.HealthCategory(category).label
    if group == "owner":
        if customer.owner_id is None:
            return "unassigned", "Unassigned"
        return str(customer.owner_id), customer.owner.name
    if group == "lifecycle":
        return customer.lifecycle_stage, customer.get_lifecycle_stage_display()
    if group == "product":
        if customer.primary_product_id is None:
            return "none", "No product"
        return str(customer.primary_product_id), customer.primary_product.name
    key = renewal_window(entry.renewal_days)
    return key, dict(RENEWAL_WINDOWS)[key]


def _group_rank(key, label, group):
    if group == "health":
        return (HEALTH_GROUP_ORDER.index(key), "", "")
    if group == "lifecycle":
        return (Customer.LifecycleStage.values.index(key), "", "")
    if group == "renewal":
        return ([window for window, _label in RENEWAL_WINDOWS].index(key), "", "")
    # Owners and products by name; the key splits two people who share one.
    return (1 if key in EMPTY_KEYS else 0, label.casefold(), key)


def build_groups(entries, group):
    groups = {}
    for entry in entries:
        key, label = group_key(entry, group)
        bucket = groups.setdefault(key, {"key": key, "label": label, "count": 0, "arr": 0.0})
        bucket["count"] += 1
        if entry.arr is not None:
            bucket["arr"] += entry.arr
    ordered = sorted(groups.values(), key=lambda g: _group_rank(g["key"], g["label"], group))
    for bucket in ordered:
        bucket["arr"] = round(bucket["arr"], 2)
    return ordered


def select(portfolio, params):
    """The rows in list order — sections first, the chosen sort inside each —
    and the section totals over every row, before `group_value` narrows."""
    entries = order_entries(portfolio, params.sort_key, params.descending)
    if not params.group:
        return entries, []
    entries = sorted(
        entries, key=lambda entry: _group_rank(*group_key(entry, params.group), params.group)
    )
    groups = build_groups(entries, params.group)
    if params.group_value is not None:
        entries = [e for e in entries if group_key(e, params.group)[0] == params.group_value]
    return entries, groups


def encode_cursor(entry_id, offset):
    raw = json.dumps({"id": entry_id, "offset": offset}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor):
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (binascii.Error, ValueError, UnicodeError):
        return None
    if not isinstance(data, dict):
        return None
    entry_id, offset = data.get("id"), data.get("offset")
    if not isinstance(entry_id, int) or not isinstance(offset, int) or offset < 0:
        return None
    return entry_id, offset


def paginate(entries, *, cursor, limit):
    """The cursor names the last row served and how many rows that was. The
    next page starts after that row; if the row has since left the set (just
    archived, say), the rows after it moved up by one, so start one earlier."""
    start = 0
    decoded = decode_cursor(cursor)
    if decoded is not None:
        entry_id, offset = decoded
        position = next(
            (i for i, entry in enumerate(entries) if entry.customer.pk == entry_id), None
        )
        start = position + 1 if position is not None else min(max(offset - 1, 0), len(entries))
    page = entries[start : start + limit]
    end = start + len(page)
    next_cursor = encode_cursor(page[-1].customer.pk, end) if page and end < len(entries) else None
    return page, next_cursor
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.organizations.tests.test_shape --noinput`
Expected: all tests pass.

- [ ] **Step 5: Lint and commit**

```bash
venv/bin/ruff check --fix services/organizations && venv/bin/ruff format services/organizations
git add services/organizations
git commit -m "feat(organizations): sort, group and cursor pages over the whole book

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Summary tiles and filter options

**Files:**
- Modify: `services/organizations/shape.py`, `services/organizations/book.py`
- Create: `services/organizations/tests/test_summary.py`

**Interfaces:**
- Consumes: `Entry` and `load_portfolio` from Task 3; `PortfolioFixture`.
- Produces:
  - `shape.build_summary(entries) -> dict`, shaped as `{"health": {"good", "average", "poor", "arr": {cat: float}, "mrr": {cat: float}}, "nps": {"score", "promoters", "passives", "detractors"}, "lifecycle": [{"value", "label", "count", "arr"}] (every stage in order), "accounts", "arr", "unconverted_count", "renewing": {"30", "90"}}`.
  - `shape.RENEWING_WINDOWS = (30, 90)`.
  - `book.filter_options(user) -> {"owners": [{"value", "name"}], "lifecycles": [{"value", "name"}], "products": [{"value", "name"}]}`.

- [ ] **Step 1: Write the failing tests**

`services/organizations/tests/test_summary.py`:

```python
from datetime import timedelta
from decimal import Decimal

from rest_framework.test import APIClient

from services.customers.models import Customer, Product
from services.fx_rates.models import FxRate
from services.organizations import book, shape
from services.organizations.params import parse_params
from services.organizations.tests.fixtures import PortfolioFixture


class SummaryEqualsTodaysPanelTests(PortfolioFixture):
    """The tiles reproduce `/customers/stats/` and the list's `renewal_within`
    count on the same book. Churned rows carry both markers here, the way the
    churn modal writes them (see the plan's pre-flight row 2)."""

    def setUp(self):
        super().setUp()
        FxRate.objects.create(
            organisation=self.org, currency="EUR", rate_to_org_currency=Decimal("1.1")
        )
        self.customer(
            "Good",
            health_score=Decimal("8.0"),
            lifecycle_stage="live",
            nps_score=50,
            renewal_date=self.today + timedelta(days=20),
        )
        self.customer(
            "Average",
            health_score=Decimal("5.0"),
            lifecycle_stage="adoption",
            nps_score=0,
            arr_billed_at_account=Decimal("24000"),
            renewal_date=self.today + timedelta(days=60),
        )
        self.customer(
            "Poor euro",
            health_score=Decimal("2.0"),
            lifecycle_stage="live",
            nps_score=-10,
            currency="EUR",
            arr_billed_at_account=Decimal("10000"),
            renewal_date=self.today - timedelta(days=5),
        )
        self.customer(
            "Poor pound",
            health_score=Decimal("3.0"),
            lifecycle_stage="renewal",
            currency="GBP",
            renewal_date=self.today + timedelta(days=200),
        )
        self.customer("Archived", is_archived=True, arr_billed_at_account=Decimal("99999"))
        self.customer(
            "Churned",
            lifecycle_stage="churn",
            churn_date=self.today - timedelta(days=30),
            renewal_date=self.today + timedelta(days=5),
        )
        self.api = APIClient()
        self.api.force_authenticate(self.csm)

    def summary(self, **query):
        portfolio = book.load_portfolio(self.csm, parse_params(query), today=self.today)
        return shape.build_summary(portfolio.entries)

    def test_health_nps_and_lifecycle_equal_customer_stats(self):
        stats = self.api.get("/api/v1/customers/stats/").data
        summary = self.summary()
        for category in ("good", "average", "poor"):
            self.assertEqual(summary["health"][category], stats["health"][category]["count"])
            self.assertEqual(summary["health"]["arr"][category], stats["health"][category]["arr"])
            self.assertEqual(summary["health"]["mrr"][category], stats["health"][category]["mrr"])
        self.assertEqual(summary["nps"], stats["nps"])
        self.assertEqual(
            {row["value"]: row["count"] for row in summary["lifecycle"]},
            {stage: bucket["count"] for stage, bucket in stats["lifecycle"].items()},
        )
        self.assertEqual(summary["unconverted_count"], stats["unconverted_count"])

    def test_accounts_and_arr(self):
        summary = self.summary()
        self.assertEqual(summary["accounts"], 4)
        self.assertEqual(summary["arr"], 12000.0 + 24000.0 + 11000.0)
        self.assertEqual(summary["unconverted_count"], 1)
        live = next(row for row in summary["lifecycle"] if row["value"] == "live")
        self.assertEqual(live, {"value": "live", "label": "Live", "count": 2, "arr": 23000.0})

    def test_renewing_equals_the_lists_renewal_within(self):
        summary = self.summary()
        for days in (30, 90):
            listed = self.api.get("/api/v1/customers/", {"renewal_within": days}).data["count"]
            self.assertEqual(summary["renewing"][str(days)], listed)
        self.assertEqual(summary["renewing"], {"30": 2, "90": 3})

    def test_totals_follow_the_filters(self):
        summary = self.summary(health="poor")
        self.assertEqual(summary["accounts"], 2)
        self.assertEqual(summary["health"]["good"], 0)
        # A churned row counted when asked for, never towards renewals.
        with_churned = self.summary(include_churned="1")
        self.assertEqual(with_churned["accounts"], 5)
        self.assertEqual(with_churned["renewing"]["30"], 2)

    def test_empty_book_is_all_zeros(self):
        summary = self.summary(search="nothing matches this")
        self.assertEqual(summary["accounts"], 0)
        self.assertEqual(summary["nps"]["score"], 0)
        self.assertEqual(len(summary["lifecycle"]), len(Customer.LifecycleStage.values))


class FilterOptionsTests(PortfolioFixture):
    def test_options_are_scoped_like_the_rows(self):
        core = Product.objects.create(organisation=self.org, name="Core")
        Product.objects.create(organisation=self.org, name="Unused")
        self.customer("Mine", lifecycle_stage="live", primary_product=core)
        self.customer("Nobody's", owner=None, lifecycle_stage="renewal")
        self.customer("Left", lifecycle_stage="churn", churn_date=self.today)
        self.customer("Hidden", is_archived=True, lifecycle_stage="expansion")
        self.customer("Dana's", owner=self.other, lifecycle_stage="kickoff")

        options = book.filter_options(self.csm)
        self.assertEqual(
            options["owners"],
            [
                {"value": str(self.csm.pk), "name": "Carl CSM"},
                {"value": "unassigned", "name": "Unassigned"},
            ],
        )
        self.assertEqual(
            [row["value"] for row in options["lifecycles"]], ["live", "renewal", "churn"]
        )
        self.assertEqual(options["products"], [{"value": str(core.pk), "name": "Core"}])

        admin_owners = [row["name"] for row in book.filter_options(self.admin)["owners"]]
        self.assertEqual(admin_owners, ["Carl CSM", "Dana CSM", "Unassigned"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.organizations.tests.test_summary --noinput`
Expected: an ERROR, `AttributeError: module 'services.organizations.shape' has no attribute 'build_summary'`

- [ ] **Step 3: Implement**

Append to `services/organizations/shape.py`:

```python
#: The Renewing tile's two windows — `/customers/?renewal_within=` counts.
RENEWING_WINDOWS = (30, 90)


def build_summary(entries):
    """The five tiles, over every filtered row. Health, NPS and lifecycle are
    `CustomerStatsView`'s arithmetic (ARR converted, MRR = ARR / 12 per
    customer, unconvertible money counted but not summed, NPS by sign);
    renewals are the list's `renewal_within` rule (overdue in, Churn stage
    out)."""
    categories = Customer.HealthCategory.values
    health = {category: 0 for category in categories}
    health_arr = {category: 0.0 for category in categories}
    health_mrr = {category: 0.0 for category in categories}
    stages = {stage: {"count": 0, "arr": 0.0} for stage in Customer.LifecycleStage.values}
    promoters = passives = detractors = 0
    renewing = {str(days): 0 for days in RENEWING_WINDOWS}
    total = 0.0
    unconverted = 0

    for entry in entries:
        customer = entry.customer
        category = customer.health_category
        health[category] += 1
        stages[customer.lifecycle_stage]["count"] += 1
        if entry.arr is None:
            unconverted += 1
        else:
            health_arr[category] += entry.arr
            health_mrr[category] += entry.arr / 12
            stages[customer.lifecycle_stage]["arr"] += entry.arr
            total += entry.arr
        if customer.nps_score is not None:
            if customer.nps_score > 0:
                promoters += 1
            elif customer.nps_score == 0:
                passives += 1
            else:
                detractors += 1
        if (
            customer.lifecycle_stage != Customer.LifecycleStage.CHURN
            and entry.renewal_days is not None
        ):
            for days in RENEWING_WINDOWS:
                if entry.renewal_days <= days:
                    renewing[str(days)] += 1

    scored = promoters + passives + detractors
    return {
        "health": {
            **health,
            "arr": {category: round(value, 2) for category, value in health_arr.items()},
            "mrr": {category: round(value, 2) for category, value in health_mrr.items()},
        },
        "nps": {
            "promoters": promoters,
            "passives": passives,
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
        "unconverted_count": unconverted,
        "renewing": renewing,
    }
```

In `services/organizations/book.py`, change the models import to `from services.customers.models import Customer, HealthSnapshot, Product, Ticket, with_health_inputs`. Then append:

```python
def filter_options(user):
    """The filter sheet's choices, scoped exactly as the rows are: the viewer's
    visible, non-archived customers — churned included, so "includes churned"
    has something to narrow. Three queries, whatever the book's size."""
    customers = visible_customers(user).filter(is_archived=False).order_by()
    owners = list(customers.values_list("owner_id", "owner__name").distinct())
    named = sorted(
        ((pk, name) for pk, name in owners if pk is not None),
        key=lambda row: (row[1] or "").casefold(),
    )
    stages = set(customers.values_list("lifecycle_stage", flat=True).distinct())
    products = (
        Product.objects.filter(
            organisation=user.organisation, pk__in=customers.values("primary_product_id")
        )
        .order_by("name")
        .values_list("id", "name")
    )
    return {
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
        "products": [{"value": str(pk), "name": name} for pk, name in products],
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.organizations.tests.test_summary --noinput`
Expected: all 6 tests pass.

- [ ] **Step 5: Lint and commit**

```bash
venv/bin/ruff check --fix services/organizations && venv/bin/ruff format services/organizations
git add services/organizations
git commit -m "feat(organizations): summary tiles and filter options over the filtered book

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: The row, its `details`, and the 34 fields

**Files:**
- Create: `services/organizations/rows.py`, `services/organizations/fields.py`, `services/organizations/tests/test_rows.py`

**Interfaces:**
- Consumes: `Entry` and `load_portfolio` from Task 3.
- Produces:
  - `rows.row_payload(entry) -> dict` (the shape is in Step 3).
  - `rows.details_payload(customer) -> dict` with six groups: `commercial`, `contract`, `adoption`, `voice`, `profile` and `history`.
  - `rows.initials(name) -> str`.
  - `rows.PULSE_DISAGREE_GAP = 2`.
  - `fields.Field(id: str, label: str, value: Callable[[dict], object])`.
  - `fields.FIELDS: tuple[Field, ...]`: 34 entries, in the frontend `ALL_COLUMNS` order.

- [ ] **Step 1: Write the failing tests**

`services/organizations/tests/test_rows.py`:

```python
from datetime import date, timedelta
from decimal import Decimal

from django.test import SimpleTestCase

from services.customers.models import Product
from services.organizations import book, rows
from services.organizations.fields import FIELDS
from services.organizations.params import parse_params
from services.organizations.tests.fixtures import PortfolioFixture

#: `ColumnId` in react-ts-app `src/components/organizations/tableData.ts`, in
#: `ALL_COLUMNS` order. The page's promise is that none of these is lost.
FRONTEND_COLUMN_IDS = [
    "organization", "revenactId", "owner", "lifecycleStage", "health", "pulse",
    "aiPulseScore", "aiPulseReason", "nps", "csatScore", "joinedDate", "renewalDate",
    "arrAccount", "arrHQ", "implFee", "tcv", "tcvRenewal", "contractStart", "contractEnd",
    "productsUtilized", "topSourceChannel", "totalContractedSeats", "totalActiveSeats",
    "totalSeatUtilization", "totalHires", "scopeWebApp", "cesPercentage", "churnDate",
    "churnReason", "churnComment", "domain", "createdBy", "modifiedBy", "nameAddress",
]  # fmt: skip


class InitialsTests(SimpleTestCase):
    def test_the_frontend_rule(self):
        self.assertEqual(rows.initials("Pizza Hut"), "PH")
        self.assertEqual(rows.initials("acme"), "A")
        self.assertEqual(rows.initials("Bank of the West"), "BO")
        self.assertEqual(rows.initials("   "), "?")


class RowFixture(PortfolioFixture):
    """A fully filled-in customer, and `row()` to read one customer's row."""

    def setUp(self):
        super().setUp()
        core = Product.objects.create(organisation=self.org, name="Core")
        self.pizza = self.customer(
            "Pizza Hut",
            address="1 Main St, Dallas",
            domain="pizzahut.example",
            created_by=self.admin,
            modified_by=self.csm,
            lifecycle_stage="live",
            health_score=Decimal("4.9"),
            pulse=[1, 2, 2],
            ai_pulse_value=1,
            ai_pulse_reason="Quiet since the outage.",
            csm_pulse_score=3,
            nps_score=-80,
            csat_score=Decimal("72.50"),
            joined_date=date(2024, 1, 15),
            renewal_date=self.today - timedelta(days=47),
            contract_start_date=date(2024, 2, 1),
            contract_end_date=date(2027, 1, 31),
            arr_billed_at_account=Decimal("69600"),
            arr_billed_at_hq=Decimal("10000"),
            implementation_fee=Decimal("5000"),
            total_contract_value=Decimal("208800"),
            total_forecasted_renewal_revenue=Decimal("70000"),
            primary_product=core,
            additional_products_count=2,
            top_source_channel="Referral",
            total_contracted_seats=100,
            total_active_seats=16,
            total_hires=40,
            scope_web_app="Full",
            ces_percentage=Decimal("61.00"),
        )
        self.core = core

    def row(self, customer, **query):
        portfolio = book.load_portfolio(self.csm, parse_params(query), today=self.today)
        entry = next(e for e in portfolio.entries if e.customer.pk == customer.pk)
        return rows.row_payload(entry)


class RowPayloadTests(RowFixture):
    def test_header(self):
        row = self.row(self.pizza)
        self.assertEqual(row["id"], self.pizza.pk)
        self.assertEqual(row["name"], "Pizza Hut")
        self.assertEqual(row["initials"], "PH")
        self.assertEqual(row["owner"], {"id": self.csm.pk, "name": "Carl CSM"})
        self.assertEqual(row["lifecycle"], {"value": "live", "label": "Live"})
        self.assertEqual(row["health"], {"score": 4.9, "category": "average", "trend": [4.9]})
        self.assertEqual(
            row["renewal"],
            {"date": (self.today - timedelta(days=47)).isoformat(), "days": -47},
        )
        self.assertEqual(row["arr"], 69600.0)
        self.assertEqual(row["pulse"], {
            "csm": 3,
            "ai": 1,
            "ai_category": "high_risk",
            "ai_label": "High Risk",
            "reason": "Quiet since the outage.",
            "history": [1, 2, 2],
            "disagree": True,
        })  # fmt: skip
        self.assertIsNone(row["last_touch_days"])
        self.assertEqual(row["urgent_tickets"], 0)
        self.assertEqual(row["signal"], {"kind": "renewal_overdue", "label": "Renewal overdue"})
        self.assertEqual(set(row["risk"]), {"score", "direction"})
        self.assertFalse(row["is_archived"])
        self.assertFalse(row["churned"])

    def test_details_groups(self):
        details = self.row(self.pizza)["details"]
        self.assertEqual(
            list(details), ["commercial", "contract", "adoption", "voice", "profile", "history"]
        )
        self.assertEqual(details["commercial"], {
            "currency": "USD",
            "arr_billed_at_account": 69600.0,
            "arr_billed_at_hq": 10000.0,
            "total_contract_value": 208800.0,
            "total_forecasted_renewal_revenue": 70000.0,
            "implementation_fee": 5000.0,
        })  # fmt: skip
        self.assertEqual(details["contract"], {
            "joined_date": "2024-01-15",
            "contract_start_date": "2024-02-01",
            "renewal_date": (self.today - timedelta(days=47)).isoformat(),
            "contract_end_date": "2027-01-31",
        })  # fmt: skip
        self.assertEqual(details["adoption"], {
            "total_contracted_seats": 100,
            "total_active_seats": 16,
            "seat_utilization_percentage": 16.0,
            "total_hires": 40,
            "products": {
                "primary": {"id": self.core.pk, "name": "Core"},
                "additional_count": 2,
            },
            "scope_web_app": "Full",
        })  # fmt: skip
        self.assertEqual(details["voice"], {
            "nps_score": -80,
            "csat_score": 72.5,
            "ces_percentage": 61.0,
            "ai_pulse_reason": "Quiet since the outage.",
        })  # fmt: skip
        self.assertEqual(details["profile"], {
            "revenact_id": self.pizza.pk,
            "domain": "pizzahut.example",
            "address": "1 Main St, Dallas",
            "top_source_channel": "Referral",
        })  # fmt: skip
        history = details["history"]
        self.assertEqual(history["created_by"], {"id": self.admin.pk, "name": "Alice Admin"})
        self.assertEqual(history["modified_by"], {"id": self.csm.pk, "name": "Carl CSM"})
        self.assertEqual(history["created_at"], self.pizza.created_at.isoformat())
        self.assertEqual(history["churn_date"], None)
        self.assertEqual(history["churn_reason"], "")

    def test_churn_fields_and_an_empty_row(self):
        left = self.customer(
            "Left Co",
            owner=None,
            lifecycle_stage="churn",
            churn_date=date(2026, 8, 1),
            churn_reason="price",
            churn_comment="Moved to a cheaper tool.",
        )
        row = self.row(left, include_churned="1")
        self.assertIsNone(row["owner"])
        self.assertTrue(row["churned"])
        self.assertEqual(row["details"]["history"]["churn_date"], "2026-08-01")
        self.assertEqual(row["details"]["history"]["churn_reason_label"], "Price")
        self.assertEqual(row["details"]["history"]["churn_comment"], "Moved to a cheaper tool.")
        self.assertIsNone(row["details"]["adoption"]["products"]["primary"])
        self.assertIsNone(row["details"]["adoption"]["seat_utilization_percentage"])
        self.assertFalse(row["pulse"]["disagree"])


class FieldCatalogueTests(RowFixture):
    def test_every_table_column_is_named_once_in_order(self):
        self.assertEqual([field.id for field in FIELDS], FRONTEND_COLUMN_IDS)
        self.assertEqual(len({field.label for field in FIELDS}), 34)

    def test_every_field_reads_from_a_full_and_an_empty_row(self):
        full = self.row(self.pizza)
        empty = self.row(self.customer("Blank", owner=None))
        values = {field.id: field.value(full) for field in FIELDS}
        for field in FIELDS:
            field.value(empty)
        self.assertEqual(values["organization"], "Pizza Hut")
        self.assertEqual(values["revenactId"], self.pizza.pk)
        self.assertEqual(values["owner"], "Carl CSM")
        self.assertEqual(values["lifecycleStage"], "Live")
        self.assertEqual(values["health"], 4.9)
        self.assertEqual(values["pulse"], "1 2 2")
        self.assertEqual(values["aiPulseScore"], "High Risk")
        self.assertEqual(values["productsUtilized"], "Core (+2)")
        self.assertEqual(values["totalSeatUtilization"], 16.0)
        self.assertEqual(
            values["createdBy"], f"Alice Admin / {self.pizza.created_at.date().isoformat()}"
        )
        self.assertEqual(values["nameAddress"], "1 Main St, Dallas")
        self.assertEqual(FIELDS[4].value(empty), 8.0)
        self.assertEqual(next(f for f in FIELDS if f.id == "owner").value(empty), "Unassigned")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.organizations.tests.test_rows --noinput`
Expected: an ERROR, `ImportError: cannot import name 'rows'`

- [ ] **Step 3: Implement `rows.py` and `fields.py`**

`services/organizations/rows.py`:

```python
"""One portfolio row as the page reads it: the header's eight elements plus
the opened row's six panels (`details`). Every one of the old table's 34
fields is here — `fields.FIELDS` names where."""

from services.customers.models import Customer

#: "When they differ by 2 or more, a 'pulses disagree' marker shows" (spec §1).
PULSE_DISAGREE_GAP = 2


def initials(name):
    """react-ts-app `formatters.initials`: the first letters of the first two
    words, upper-cased, or "?"."""
    letters = "".join(part[0] for part in name.split()[:2])
    return letters.upper() or "?"


def _iso(value):
    return None if value is None else value.isoformat()


def _number(value):
    return None if value is None else float(value)


def _person(user):
    return None if user is None else {"id": user.pk, "name": user.name}


def details_payload(customer):
    """The opened row. Money here is in the customer's own contract currency
    (`commercial.currency`), as the old table printed it; the row's `arr` is
    the converted figure."""
    product = customer.primary_product
    return {
        "commercial": {
            "currency": customer.currency,
            "arr_billed_at_account": _number(customer.arr_billed_at_account),
            "arr_billed_at_hq": _number(customer.arr_billed_at_hq),
            "total_contract_value": _number(customer.total_contract_value),
            "total_forecasted_renewal_revenue": _number(customer.total_forecasted_renewal_revenue),
            "implementation_fee": _number(customer.implementation_fee),
        },
        "contract": {
            "joined_date": _iso(customer.joined_date),
            "contract_start_date": _iso(customer.contract_start_date),
            "renewal_date": _iso(customer.renewal_date),
            "contract_end_date": _iso(customer.contract_end_date),
        },
        "adoption": {
            "total_contracted_seats": customer.total_contracted_seats,
            "total_active_seats": customer.total_active_seats,
            "seat_utilization_percentage": customer.seat_utilization_percentage,
            "total_hires": customer.total_hires,
            "products": {
                "primary": None if product is None else {"id": product.pk, "name": product.name},
                "additional_count": customer.additional_products_count,
            },
            "scope_web_app": customer.scope_web_app,
        },
        "voice": {
            "nps_score": customer.nps_score,
            "csat_score": _number(customer.csat_score),
            "ces_percentage": _number(customer.ces_percentage),
            "ai_pulse_reason": customer.ai_pulse_reason,
        },
        "profile": {
            "revenact_id": customer.pk,
            "domain": customer.domain,
            "address": customer.address,
            "top_source_channel": customer.top_source_channel,
        },
        "history": {
            "created_by": _person(customer.created_by),
            "created_at": _iso(customer.created_at),
            "modified_by": _person(customer.modified_by),
            "updated_at": _iso(customer.updated_at),
            "churn_date": _iso(customer.churn_date),
            "churn_reason": customer.churn_reason,
            "churn_reason_label": (
                customer.get_churn_reason_display() if customer.churn_reason else ""
            ),
            "churn_comment": customer.churn_comment,
        },
    }


def row_payload(entry):
    customer = entry.customer
    csm, ai = customer.csm_pulse_score, customer.ai_pulse_value
    ai_category = customer.ai_pulse_score
    return {
        "id": customer.pk,
        "name": customer.name,
        "initials": initials(customer.name),
        "owner": _person(customer.owner),
        "lifecycle": {
            "value": customer.lifecycle_stage,
            "label": customer.get_lifecycle_stage_display(),
        },
        "health": {
            "score": float(customer.health_score),
            "category": customer.health_category,
            "trend": entry.trend,
        },
        "renewal": {"date": _iso(customer.renewal_date), "days": entry.renewal_days},
        "arr": entry.arr,
        "risk": {"score": entry.triage.score, "direction": entry.triage.direction},
        "pulse": {
            "csm": csm,
            "ai": ai,
            "ai_category": ai_category,
            "ai_label": Customer.AIPulseScore(ai_category).label if ai_category else "",
            "reason": customer.ai_pulse_reason,
            # The old table's "Pulse" column: the stored history dots.
            "history": list(customer.pulse or []),
            "disagree": csm is not None and ai is not None and abs(csm - ai) >= PULSE_DISAGREE_GAP,
        },
        "last_touch_days": entry.last_touch_days,
        "urgent_tickets": entry.urgent_tickets,
        "signal": entry.signal,
        "is_archived": customer.is_archived,
        "churned": entry.churned,
        "details": details_payload(customer),
    }
```

`services/organizations/fields.py`:

```python
"""The Organizations table's 34 fields, named once: the export's columns and
the tests' proof that the redesign lost none of them. `id` is the frontend's
`ColumnId`; `label` is its `ALL_COLUMNS` label without the "($)" (amounts are
in the row's own currency, which the export prints beside them)."""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    id: str
    label: str
    value: Callable[[dict], object]


def _detail(group, key):
    return lambda row: row["details"][group][key]


def _by(person_key, date_key):
    def value(row):
        history = row["details"]["history"]
        person = history[person_key]
        name = person["name"] if person else "System"
        return f"{name} / {history[date_key][:10]}"

    return value


def _products(row):
    products = row["details"]["adoption"]["products"]
    if products["primary"] is None:
        return ""
    extra = products["additional_count"]
    name = products["primary"]["name"]
    return f"{name} (+{extra})" if extra else name


FIELDS = (
    Field("organization", "Organization", lambda row: row["name"]),
    Field("revenactId", "Revenact ID", _detail("profile", "revenact_id")),
    Field("owner", "Owner", lambda row: row["owner"]["name"] if row["owner"] else "Unassigned"),
    Field("lifecycleStage", "Lifecycle Stage", lambda row: row["lifecycle"]["label"]),
    Field("health", "Health", lambda row: row["health"]["score"]),
    Field("pulse", "Pulse", lambda row: " ".join(str(p) for p in row["pulse"]["history"])),
    Field("aiPulseScore", "AI Pulse Score", lambda row: row["pulse"]["ai_label"]),
    Field("aiPulseReason", "AI Pulse Reason", _detail("voice", "ai_pulse_reason")),
    Field("nps", "NPS", _detail("voice", "nps_score")),
    Field("csatScore", "CSAT Score", _detail("voice", "csat_score")),
    Field("joinedDate", "Joined Date", _detail("contract", "joined_date")),
    Field("renewalDate", "Renewal Date", _detail("contract", "renewal_date")),
    Field(
        "arrAccount",
        "Total ARR Billed At Account",
        _detail("commercial", "arr_billed_at_account"),
    ),
    Field("arrHQ", "Total ARR Billed At HQ", _detail("commercial", "arr_billed_at_hq")),
    Field("implFee", "Implementation Fee (One Time)", _detail("commercial", "implementation_fee")),
    Field("tcv", "Total Contract Value", _detail("commercial", "total_contract_value")),
    Field(
        "tcvRenewal",
        "Total Forecasted Renewal Revenue",
        _detail("commercial", "total_forecasted_renewal_revenue"),
    ),
    Field("contractStart", "Contract Start Date", _detail("contract", "contract_start_date")),
    Field("contractEnd", "Contract End Date", _detail("contract", "contract_end_date")),
    Field("productsUtilized", "Products Utilized", _products),
    Field("topSourceChannel", "Top Source Channel", _detail("profile", "top_source_channel")),
    Field(
        "totalContractedSeats",
        "Total Contracted Seats",
        _detail("adoption", "total_contracted_seats"),
    ),
    Field("totalActiveSeats", "Total Active Seats", _detail("adoption", "total_active_seats")),
    Field(
        "totalSeatUtilization",
        "Total Seat Usage Utilization %",
        _detail("adoption", "seat_utilization_percentage"),
    ),
    Field("totalHires", "Total Hires", _detail("adoption", "total_hires")),
    Field("scopeWebApp", "Scope WebApp", _detail("adoption", "scope_web_app")),
    Field("cesPercentage", "CES Percentage", _detail("voice", "ces_percentage")),
    Field("churnDate", "Churn Date", _detail("history", "churn_date")),
    Field("churnReason", "Churn Reason", _detail("history", "churn_reason_label")),
    Field("churnComment", "Churn Comment", _detail("history", "churn_comment")),
    Field("domain", "Domain", _detail("profile", "domain")),
    Field("createdBy", "Created By / Created Date", _by("created_by", "created_at")),
    Field("modifiedBy", "Modified By / Modified Date", _by("modified_by", "updated_at")),
    Field("nameAddress", "Name / Address", _detail("profile", "address")),
)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.organizations.tests.test_rows --noinput`
Expected: all tests pass.

- [ ] **Step 5: Lint and commit**

```bash
venv/bin/ruff check --fix services/organizations && venv/bin/ruff format services/organizations
git add services/organizations
git commit -m "feat(organizations): the portfolio row, its details and the 34 fields

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: `GET /api/v1/organizations/portfolio/`

**Files:**
- Modify: `services/organizations/shape.py`, `config/urls.py`
- Create: `services/organizations/views.py`, `services/organizations/urls.py`, `services/organizations/tests/test_views.py`

**Interfaces:**
- Consumes: `parse_params` (Task 1), `load_portfolio` and `filter_options` (Tasks 3 and 5), `select`, `paginate` and `build_summary` (Tasks 4 and 5), and `row_payload` (Task 6).
- Produces:
  - `shape.build_listing(portfolio, params, *, filters) -> dict`, whose keys are `{"results", "next_cursor", "count", "groups", "summary", "filters", "currency"}`.
  - `views.PortfolioView`, mounted at `api/v1/organizations/portfolio/` with the URL name `organizations-portfolio`.

- [ ] **Step 1: Write the failing tests**

`services/organizations/tests/test_views.py`:

```python
from datetime import timedelta
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from services.accounts.models import User
from services.customers.models import Account, Activity, HealthSnapshot, Product, Ticket
from services.organizations.tests.fixtures import PortfolioFixture

URL = "/api/v1/organizations/portfolio/"


class PortfolioEndpointTests(PortfolioFixture):
    def get(self, user=None, **query):
        api = APIClient()
        api.force_authenticate(user or self.csm)
        response = api.get(URL, query)
        self.assertEqual(response.status_code, 200, response.content)
        return response.data

    def test_requires_authentication(self):
        self.assertEqual(APIClient().get(URL).status_code, 401)

    def test_response_shape(self):
        self.customer("Pizza Hut")
        body = self.get()
        self.assertEqual(
            set(body),
            {"results", "next_cursor", "count", "groups", "summary", "filters", "currency"},
        )
        self.assertEqual(body["currency"], "USD")
        self.assertEqual(body["count"], 1)
        self.assertIsNone(body["next_cursor"])
        self.assertEqual(
            set(body["results"][0]),
            {
                "id", "name", "initials", "owner", "lifecycle", "health", "renewal", "arr",
                "risk", "pulse", "last_touch_days", "urgent_tickets", "signal",
                "is_archived", "churned", "details",
            },
        )  # fmt: skip
        self.assertEqual(set(body["filters"]), {"owners", "lifecycles", "products"})

    def test_customers_outside_visibility_never_appear_even_by_id(self):
        self.customer("Mine")
        self.customer("Nobody's", owner=None)
        danas = self.customer("Dana's", owner=self.other)
        globex = self.customer("Globex's", owner=None, organisation=self.other_org)
        body = self.get()
        self.assertEqual({row["name"] for row in body["results"]}, {"Mine", "Nobody's"})
        self.assertEqual(body["summary"]["accounts"], 2)
        self.assertNotIn(str(self.other.pk), [o["value"] for o in body["filters"]["owners"]])
        by_id = self.get(ids=f"{danas.pk},{globex.pk}")
        self.assertEqual((by_id["count"], by_id["results"]), (0, []))
        self.assertEqual(by_id["summary"]["accounts"], 0)

    def test_unknown_values_are_ignored_not_rejected(self):
        self.customer("A")
        self.customer("B")
        body = self.get(
            sort="colour",
            group="mood",
            health="purple",
            limit="lots",
            renews_within="7",
            nps="happy",
            cursor="%%%",
            owner="someone",
            product="x",
        )
        self.assertEqual(body["count"], 2)

    def test_groups_and_summary_cover_the_whole_filtered_set(self):
        self.customer("Poor", health_score=Decimal("2.0"))
        self.customer("Average", health_score=Decimal("5.0"))
        self.customer("Good")
        body = self.get(group="health", limit="1")
        self.assertEqual(len(body["results"]), 1)
        self.assertEqual(body["results"][0]["name"], "Poor")
        self.assertEqual(body["count"], 3)
        self.assertEqual(sum(group["count"] for group in body["groups"]), 3)
        self.assertEqual(body["summary"]["accounts"], 3)
        self.assertIsNotNone(body["next_cursor"])

        narrowed = self.get(health="poor,average")
        self.assertEqual(narrowed["summary"]["accounts"], 2)

    def test_group_value_serves_one_board_column_with_all_headers(self):
        self.customer("Live one", lifecycle_stage="live")
        self.customer("Live two", lifecycle_stage="live")
        self.customer("Onboarding", lifecycle_stage="onboarding")
        body = self.get(group="lifecycle", group_value="live")
        self.assertEqual({row["name"] for row in body["results"]}, {"Live one", "Live two"})
        self.assertEqual(body["count"], 2)
        self.assertEqual([g["key"] for g in body["groups"]], ["onboarding", "live"])
        self.assertEqual(body["summary"]["accounts"], 3)

    def test_following_the_cursor_reads_every_row_once(self):
        for i in range(5):
            self.customer(f"Co {i}", arr_billed_at_account=Decimal(1000 * (i + 1)))
        seen, cursor = [], None
        while True:
            query = {"limit": "2", **({"cursor": cursor} if cursor else {})}
            body = self.get(**query)
            seen += [row["name"] for row in body["results"]]
            cursor = body["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(seen, ["Co 4", "Co 3", "Co 2", "Co 1", "Co 0"])


class PortfolioQueryCountTests(PortfolioFixture):
    """Every figure is computed over the whole filtered set in a fixed number
    of queries. A per-row query anywhere would make the count grow with the
    book, which is what the second assertion catches.

    Ten for an admin, who sees everything (so no org-chart lookups):
      1. the caller's membership (services.identity.context, memoised per request)
      2. the customers — touch and open-ticket subqueries, owner / product /
         created_by / modified_by joined
      3. their health snapshots (one prefetch)
      4. the FX rate table
      5. the open High/Critical tickets
      6. those tickets' accounts (prefetch)
      7. those accounts' customers (prefetch)
      8-10. the filter options: owners, stages, products
    If the pinned number is ever wrong, print `[q["sql"] for q in
    ctx.captured_queries]`: every query must be one of these ten kinds; an
    extra one is a bug to fix, not a number to bump.
    """

    EXPECTED = 10

    def book(self, size):
        product = Product.objects.create(organisation=self.org, name=f"Core {size}")
        for i in range(size):
            customer = self.customer(
                f"Co {size}-{i}",
                primary_product=product,
                renewal_date=self.today + timedelta(days=i),
            )
            HealthSnapshot.objects.create(
                customer=customer,
                captured_on=self.today - timedelta(days=30),
                health_score=Decimal("6.0"),
            )
            Activity.objects.create(
                customer=customer, type=Activity.ActivityType.OTHER, occurred_at=self.today
            )
            account = Account.objects.create(name=f"Div {size}-{i}")
            account.customers.add(customer)
            Ticket.objects.create(
                account=account,
                ticket_number=f"T-{size}-{i}",
                title="Down",
                status=Ticket.Status.OPEN,
                priority=Ticket.Priority.HIGH,
                opened_at=self.today,
            )

    def count(self, **query):
        # A fresh user each time, as a real request loads one: memoised lookups
        # cached on a reused instance would otherwise flatter the second call.
        api = APIClient()
        api.force_authenticate(User.objects.get(pk=self.admin.pk))
        with CaptureQueriesContext(connection) as ctx:
            response = api.get(URL, query)
        self.assertEqual(response.status_code, 200)
        return len(ctx.captured_queries)

    def test_the_count_is_pinned_and_does_not_grow_with_the_book(self):
        self.book(3)
        small = self.count()
        self.book(12)
        self.assertEqual(self.count(), small)
        self.assertEqual(small, self.EXPECTED)

    def test_sorting_grouping_filtering_and_paging_add_no_queries(self):
        self.book(5)
        plain = self.count()
        self.assertEqual(self.count(sort="-risk", group="owner", limit="2"), plain)
        self.assertEqual(self.count(group="lifecycle", group_value="live"), plain)
        self.assertEqual(self.count(search="Co", health="average,good", renews_within="30"), plain)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.organizations.tests.test_views --noinput`
Expected: FAIL. The authenticated tests get a 404 status and `test_requires_authentication` gets 404 != 401, because no URL is mounted yet.

- [ ] **Step 3: Implement the listing, the view and the URLs**

Add `from .rows import row_payload` to the imports of `services/organizations/shape.py` (after `from .params import NUMERIC_SORT_KEYS`). Then append:

```python
def build_listing(portfolio, params, *, filters):
    """The endpoint's body. `count` is the rows this query pages through
    (after `group_value`); `groups` and `summary` are over the whole filtered
    set, so a board column's header and the tiles never shrink to a page."""
    entries, groups = select(portfolio, params)
    page, next_cursor = paginate(entries, cursor=params.cursor, limit=params.limit)
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

`services/organizations/views.py`:

```python
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .book import filter_options, load_portfolio
from .params import parse_params
from .shape import build_listing


class PortfolioView(APIView):
    """GET /api/v1/organizations/portfolio/ — the Organizations list: the
    viewer's visible book (archived out, churned out unless asked for),
    filtered, sorted, optionally grouped, and paged by an opaque cursor, with
    the summary tiles and group totals over the whole filtered set. Every row
    signal comes from the code the dashboard uses, so the two cannot disagree.
    Unknown parameter values are ignored. See docs/API_CONTRACTS.md."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        params = parse_params(request.query_params)
        portfolio = load_portfolio(request.user, params, today=timezone.localdate())
        return Response(build_listing(portfolio, params, filters=filter_options(request.user)))
```

`services/organizations/urls.py`:

```python
from django.urls import path

from . import views

urlpatterns = [
    path("portfolio/", views.PortfolioView.as_view(), name="organizations-portfolio"),
]
```

`config/urls.py`: add this directly after the `path("api/v1/mcp/", include("services.mcp.urls")),` line:

```text
    # The Organizations page's own endpoint (list, export, bulk). /customers/
    # stays as it is for its other consumers — see services/organizations.
    path("api/v1/organizations/", include("services.organizations.urls")),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.organizations --noinput`
Expected: all tests pass, including `EXPECTED = 10`. If the pinned count differs, follow the class docstring: list the captured SQL and reconcile each query against the ten kinds before changing anything.

- [ ] **Step 5: Lint and commit**

```bash
venv/bin/ruff check --fix services/organizations config && venv/bin/ruff format services/organizations config
git add services/organizations config/urls.py
git commit -m "feat(organizations): GET /organizations/portfolio/

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: The CSV export, audited

**Files:**
- Create: `services/organizations/export.py`, `services/organizations/tests/test_export.py`
- Modify: `services/organizations/views.py`, `services/organizations/urls.py`

**Interfaces:**
- Consumes: `FIELDS` (Task 6), `select` (Task 4), `row_payload` (Task 6), `load_portfolio` and `parse_params`.
- Produces:
  - `export.cell(value) -> str | int | float`.
  - `export.table(rows: list[dict]) -> list[list]`. Its header is the 34 `FIELDS` labels plus `"Currency"`.
  - `export.CSVRenderer` (DRF renderer).
  - `views.PortfolioExportView`, at `api/v1/organizations/portfolio/export.csv` with the URL name `organizations-portfolio-export`.
  - The audit action `organizations.exported`, with the metadata `{"count": int, "params": [sorted query-param names]}`.

- [ ] **Step 1: Write the failing tests**

`services/organizations/tests/test_export.py`:

```python
import csv
import io
from decimal import Decimal

from django.test import SimpleTestCase
from rest_framework.test import APIClient

from core.models import AuditEvent
from services.organizations import export
from services.organizations.fields import FIELDS
from services.organizations.tests.fixtures import PortfolioFixture

URL = "/api/v1/organizations/portfolio/export.csv"


class CellTests(SimpleTestCase):
    def test_formula_cells_are_neutralised(self):
        self.assertEqual(export.cell("=HYPERLINK(1)"), "'=HYPERLINK(1)")
        for prefix in ("+", "-", "@", "\t", "\r"):
            self.assertEqual(export.cell(f"{prefix}x"), f"'{prefix}x")
        self.assertEqual(export.cell("Pizza Hut"), "Pizza Hut")

    def test_numbers_stay_numbers_and_blanks_are_empty(self):
        self.assertEqual(export.cell(-80), -80)
        self.assertEqual(export.cell(4.9), 4.9)
        self.assertEqual(export.cell(None), "")
        self.assertEqual(export.cell(True), "Yes")


class ExportEndpointTests(PortfolioFixture):
    def download(self, user=None, **query):
        api = APIClient()
        api.force_authenticate(user or self.csm)
        response = api.get(URL, query)
        return response, list(csv.reader(io.StringIO(response.content.decode())))

    def test_requires_authentication(self):
        self.assertEqual(APIClient().get(URL).status_code, 401)

    def test_every_field_every_row_no_pagination(self):
        for i in range(60):
            self.customer(f"Co {i:02d}")
        response, table = self.download()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/csv"))
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertIn(
            f"organizations-{self.today.isoformat()}.csv", response["Content-Disposition"]
        )
        header, rows = table[0], table[1:]
        self.assertEqual(header, [field.label for field in FIELDS] + ["Currency"])
        self.assertEqual(len(header), 35)
        self.assertEqual(len(rows), 60)

    def test_same_params_same_rows_and_visibility(self):
        self.customer("Poor one", health_score=Decimal("2.0"))
        self.customer("Good one")
        self.customer("Dana's", owner=self.other, health_score=Decimal("2.0"))
        _response, table = self.download(health="poor", sort="name")
        self.assertEqual([row[0] for row in table[1:]], ["Poor one"])

    def test_values_and_injection(self):
        self.customer(
            '=HYPERLINK("http://evil.example")', currency="EUR", nps_score=-80, owner=None
        )
        _response, table = self.download()
        row = dict(zip(table[0], table[1], strict=True))
        self.assertTrue(row["Organization"].startswith("'="))
        self.assertEqual(row["Owner"], "Unassigned")
        self.assertEqual(row["NPS"], "-80")
        self.assertEqual(row["Currency"], "EUR")
        self.assertEqual(row["Total ARR Billed At Account"], "12000.0")

    def test_the_export_is_audited(self):
        self.customer("A")
        self.customer("B")
        self.download(health="good", sort="name")
        event = AuditEvent.objects.get(action="organizations.exported")
        self.assertEqual(event.actor, self.csm)
        self.assertEqual(event.organisation, self.org)
        self.assertEqual(event.metadata, {"count": 2, "params": ["health", "sort"]})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.organizations.tests.test_export --noinput`
Expected: an ERROR, `ImportError: cannot import name 'export'`

- [ ] **Step 3: Implement**

`services/organizations/export.py`:

```python
"""The portfolio as a CSV: every filtered row, the 34 table fields, and the
currency the money columns are in.

A spreadsheet runs a cell that starts with `=`, `+`, `-`, `@`, a tab or a
carriage return as a formula, so any text cell that does is prefixed with `'`.
A company named `=HYPERLINK(...)` is data, not an instruction. Numbers are
written as numbers (an NPS of -80 stays -80).
"""

import csv
import io

from rest_framework.renderers import BaseRenderer

from .fields import FIELDS

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    return "'" + text if text.startswith(FORMULA_PREFIXES) else text


def table(rows):
    header = [field.label for field in FIELDS] + ["Currency"]
    body = [
        [cell(field.value(row)) for field in FIELDS] + [row["details"]["commercial"]["currency"]]
        for row in rows
    ]
    return [header, *body]


class CSVRenderer(BaseRenderer):
    """The export view's only renderer, so it answers `Accept: text/csv` and
    `*/*` alike. An error (401, 403) is written as a one-cell CSV rather than
    switching format mid-download."""

    media_type = "text/csv"
    format = "csv"
    charset = "utf-8"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        if isinstance(data, list):
            writer.writerows(data)
        else:
            detail = data.get("detail", "") if isinstance(data, dict) else data
            writer.writerows([["detail"], [str(detail)]])
        return buffer.getvalue().encode(self.charset)
```

Replace the import block of `services/organizations/views.py` with:

```python
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core import audit

from .book import filter_options, load_portfolio
from .export import CSVRenderer, table
from .params import parse_params
from .rows import row_payload
from .shape import build_listing, select
```

Then append this to `services/organizations/views.py`:

```python
class PortfolioExportView(APIView):
    """GET /api/v1/organizations/portfolio/export.csv — the same query as the
    list, every row (no pagination), in list order, with all 34 fields.
    Audited: this is confidential data leaving the app."""

    permission_classes = [IsAuthenticated]
    renderer_classes = [CSVRenderer]

    def get(self, request):
        params = parse_params(request.query_params)
        today = timezone.localdate()
        portfolio = load_portfolio(request.user, params, today=today)
        entries, _groups = select(portfolio, params)
        audit.record(  # SOC2:LOG-01
            "organizations.exported",
            request=request,
            # Parameter names only: a search term is the user's own words.
            metadata={"count": len(entries), "params": sorted(request.query_params.keys())},
        )
        response = Response(table([row_payload(entry) for entry in entries]))
        response["Content-Disposition"] = (
            f'attachment; filename="organizations-{today.isoformat()}.csv"'
        )
        return response
```

`services/organizations/urls.py`: add this to `urlpatterns`:

```text
    path(
        "portfolio/export.csv",
        views.PortfolioExportView.as_view(),
        name="organizations-portfolio-export",
    ),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.organizations.tests.test_export --noinput`
Expected: all tests pass.

- [ ] **Step 5: Lint and commit**

```bash
venv/bin/ruff check --fix services/organizations && venv/bin/ruff format services/organizations
git add services/organizations
git commit -m "feat(organizations): export the portfolio as CSV, audited

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: `POST /api/v1/organizations/bulk/`

**Files:**
- Modify: `services/customers/views.py` (extract `after_customer_update`), `services/organizations/views.py`, `services/organizations/urls.py`
- Create: `services/organizations/serializers.py`, `services/organizations/bulk.py`, `services/organizations/tests/test_bulk.py`

**Interfaces:**
- Consumes: `CustomerSerializer` and `visible_customers` from `services.customers`, and `PortfolioFixture`.
- Produces:
  - `services.customers.views.after_customer_update(customer, *, actor, previous_owner, handover_note="") -> None`.
  - `serializers.BulkRequestSerializer`, whose validated data is `{"ids": list[int] (deduplicated, 1..500), "action": str, "value": int | str | bool | None}`.
  - `bulk.apply(request, *, ids, action, value) -> {"updated": list[int], "failed": list[{"id": int, "reason": str}]}`.
  - `views.BulkUpdateView`, at `api/v1/organizations/bulk/` with the URL name `organizations-bulk`.
  - The audit action `organizations.bulk_updated`, with the metadata `{"action", "value", "ids", "failed_ids"}`. Its outcome is `failure` when nothing was updated.

- [ ] **Step 1: Write the failing tests**

`services/organizations/tests/test_bulk.py`:

```python
from rest_framework.test import APIClient

from core.models import AuditEvent
from services.accounts.models import User
from services.customers.models import Account, Customer
from services.knowledge.models import Contribution
from services.organizations.tests.fixtures import PortfolioFixture

URL = "/api/v1/organizations/bulk/"


class BulkTests(PortfolioFixture):
    def post(self, body, user=None):
        api = APIClient()
        api.force_authenticate(user or self.csm)
        return api.post(URL, body, format="json")

    def test_requires_authentication(self):
        self.assertEqual(APIClient().post(URL, {}, format="json").status_code, 401)

    def test_set_lifecycle(self):
        a, b = self.customer("A"), self.customer("B")
        response = self.post({"ids": [a.pk, b.pk], "action": "set_lifecycle", "value": "expansion"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"updated": [a.pk, b.pk], "failed": []})
        stages = set(
            Customer.objects.filter(pk__in=[a.pk, b.pk]).values_list("lifecycle_stage", flat=True)
        )
        self.assertEqual(stages, {"expansion"})
        self.assertEqual(Customer.objects.get(pk=a.pk).modified_by, self.csm)

    def test_churn_is_not_a_bulk_stage(self):
        a = self.customer("A")
        response = self.post({"ids": [a.pk], "action": "set_lifecycle", "value": "churn"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("value", response.data)
        response = self.post({"ids": [a.pk], "action": "set_lifecycle", "value": "bogus"})
        self.assertEqual(response.status_code, 400)

    def test_request_validation(self):
        self.assertEqual(self.post({"ids": [], "action": "archive"}).status_code, 400)
        self.assertEqual(
            self.post({"ids": list(range(1, 502)), "action": "archive"}).status_code, 400
        )
        self.assertEqual(self.post({"ids": [1], "action": "delete"}).status_code, 400)
        self.assertEqual(
            self.post({"ids": [1], "action": "set_owner", "value": "carl"}).status_code, 400
        )

    def test_archive_hides_from_the_portfolio(self):
        a = self.customer("A")
        response = self.post({"ids": [a.pk], "action": "archive"})
        self.assertEqual(response.data, {"updated": [a.pk], "failed": []})
        self.assertTrue(Customer.objects.get(pk=a.pk).is_archived)
        api = APIClient()
        api.force_authenticate(self.csm)
        self.assertEqual(api.get("/api/v1/organizations/portfolio/").data["count"], 0)

    def test_set_owner_hands_over_like_the_detail_view(self):
        a = self.customer("A")
        response = self.post({"ids": [a.pk], "action": "set_owner", "value": self.other.pk})
        self.assertEqual(response.data, {"updated": [a.pk], "failed": []})
        self.assertEqual(Customer.objects.get(pk=a.pk).owner, self.other)
        self.assertTrue(Contribution.objects.filter(customer=a, author=self.csm).exists())

    def test_unassigning(self):
        a = self.customer("A")
        response = self.post({"ids": [a.pk], "action": "set_owner", "value": None})
        self.assertEqual(response.data["updated"], [a.pk])
        self.assertIsNone(Customer.objects.get(pk=a.pk).owner)

    def test_partial_failures_are_reported_per_account(self):
        mine = self.customer("Mine")
        danas = self.customer("Dana's", owner=self.other)
        globex = self.customer("Globex's", owner=None, organisation=self.other_org)
        # Visible to Carl through an account he owns, but only Dana, her
        # managers or a settings manager may reassign it.
        shared = self.customer("Shared", owner=self.other)
        division = Account.objects.create(name="Shared EMEA", owner=self.csm)
        division.customers.add(shared)
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
        self.assertIn("can reassign this account", failed[shared.pk])
        self.assertEqual(Customer.objects.get(pk=shared.pk).owner, self.other)

        response = self.post({"ids": [mine.pk], "action": "set_owner", "value": outsider.pk})
        self.assertEqual(
            response.data["failed"],
            [{"id": mine.pk, "reason": "Owner must be a member of your own organisation."}],
        )

    def test_admin_may_reassign_anyones(self):
        danas = self.customer("Dana's", owner=self.other)
        response = self.post(
            {"ids": [danas.pk], "action": "set_owner", "value": self.csm.pk}, user=self.admin
        )
        self.assertEqual(response.data["updated"], [danas.pk])

    def test_duplicate_ids_apply_once(self):
        a = self.customer("A")
        response = self.post({"ids": [a.pk, a.pk], "action": "archive"})
        self.assertEqual(response.data["updated"], [a.pk])

    def test_audited_with_ids_and_action(self):
        a = self.customer("A")
        danas = self.customer("Dana's", owner=self.other)
        self.post({"ids": [a.pk, danas.pk], "action": "set_lifecycle", "value": "live"})
        event = AuditEvent.objects.get(action="organizations.bulk_updated")
        self.assertEqual(event.outcome, AuditEvent.Outcome.SUCCESS)
        self.assertEqual(
            event.metadata,
            {"action": "set_lifecycle", "value": "live", "ids": [a.pk], "failed_ids": [danas.pk]},
        )

    def test_nothing_updated_is_a_failure_outcome(self):
        danas = self.customer("Dana's", owner=self.other)
        self.post({"ids": [danas.pk], "action": "archive"})
        event = AuditEvent.objects.get(action="organizations.bulk_updated")
        self.assertEqual(event.outcome, AuditEvent.Outcome.FAILURE)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.organizations.tests.test_bulk --noinput`
Expected: FAIL with 404s, because `/api/v1/organizations/bulk/` is not mounted.

- [ ] **Step 3: Extract `after_customer_update` from the detail view**

In `services/customers/views.py`, add this directly after `_notify_owner_assigned`:

```python
def after_customer_update(customer, *, actor, previous_owner, handover_note=""):
    """What a saved Customer edit sets off when its owner changed: the
    handover written down as a contribution, and the new owner told. Shared
    by the detail view's PATCH and the Organizations bulk edit, so a reassign
    means the same thing from either."""
    if customer.owner_id == (previous_owner.id if previous_owner else None):
        return
    from services.knowledge.ownership import record_handover

    record_handover(customer, actor, previous_owner, handover_note)
    _notify_owner_assigned(
        instance=customer,
        actor=actor,
        kind=Notification.Kind.CUSTOMER_ASSIGNED,
        noun="the organization",
        link=f"/organizations/{customer.id}",
    )
```

Replace `CustomerDetailView.perform_update` with:

```text
    def perform_update(self, serializer):
        previous_owner = serializer.instance.owner
        customer = serializer.save()
        after_customer_update(
            customer,
            actor=self.request.user,
            previous_owner=previous_owner,
            handover_note=serializer.context.get("handover_note", ""),
        )
```

Run: `venv/bin/python manage.py test services.customers.tests.test_views services.knowledge --noinput`
Expected: all tests pass. The refactor does not change behaviour.

- [ ] **Step 4: Implement the serializer, `bulk.py` and the view**

`services/organizations/serializers.py`:

```python
from rest_framework import serializers

from services.customers.models import Customer

from .params import MAX_IDS

ACTIONS = ("set_owner", "set_lifecycle", "archive")


class BulkRequestSerializer(serializers.Serializer):
    ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), min_length=1, max_length=MAX_IDS
    )
    action = serializers.ChoiceField(choices=ACTIONS)
    value = serializers.JSONField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        action, value = attrs["action"], attrs.get("value")
        if action == "set_owner" and value is not None:
            if isinstance(value, bool) or not isinstance(value, int):
                raise serializers.ValidationError(
                    {"value": "An owner is a user id, or null to unassign."}
                )
        if action == "set_lifecycle":
            if not isinstance(value, str) or value not in Customer.LifecycleStage.values:
                raise serializers.ValidationError({"value": "Not a lifecycle stage."})
            if value == Customer.LifecycleStage.CHURN:
                raise serializers.ValidationError(
                    {
                        "value": "Churn has its own flow, which records the date and reason: "
                        "churn each organization from its churn action."
                    }
                )
        if action == "archive":
            attrs["value"] = True
        attrs["ids"] = list(dict.fromkeys(attrs["ids"]))
        return attrs
```

`services/organizations/bulk.py`:

```python
"""Bulk edits from the Organizations selection bar, applied one organization
at a time with exactly the rules a single edit follows: the record must be
visible to the editor, `CustomerSerializer` validates the change (an owner in
the same organisation; a reassign only by the current owner, their chain or a
settings manager), and an owner change is handed over and notified.

One failure never stops the rest, and each is reported by id. An id the editor
cannot see reads "Not found." — the same answer as one that does not exist,
so the endpoint cannot be used to discover records.
"""

from django.db import transaction

from services.customers.scoping import visible_customers
from services.customers.serializers import CustomerSerializer
from services.customers.views import after_customer_update

#: Each action is one writable field of the ordinary customer update.
FIELD_FOR_ACTION = {
    "set_owner": "owner_id",
    "set_lifecycle": "lifecycle_stage",
    "archive": "is_archived",
}

NOT_FOUND = "Not found."


def _reason(errors):
    for messages in errors.values():
        if isinstance(messages, (list, tuple)) and messages:
            return str(messages[0])
        return str(messages)
    return "Could not be updated."


def apply(request, *, ids, action, value):
    field = FIELD_FOR_ACTION[action]
    # SOC2:AUTH-02 each record is read through the editor's own visibility
    visible = {
        customer.pk: customer
        for customer in visible_customers(request.user).filter(pk__in=ids).select_related("owner")
    }
    updated, failed = [], []
    for customer_id in ids:
        customer = visible.get(customer_id)
        if customer is None:
            failed.append({"id": customer_id, "reason": NOT_FOUND})
            continue
        previous_owner = customer.owner
        serializer = CustomerSerializer(
            customer, data={field: value}, partial=True, context={"request": request}
        )
        if not serializer.is_valid():
            failed.append({"id": customer_id, "reason": _reason(serializer.errors)})
            continue
        with transaction.atomic():
            saved = serializer.save()
            after_customer_update(saved, actor=request.user, previous_owner=previous_owner)
        updated.append(customer_id)
    return {"updated": updated, "failed": failed}
```

In `services/organizations/views.py`, add `from core.models import AuditEvent`, `from . import bulk`, and `from .serializers import BulkRequestSerializer` to the imports. Then append:

```python
class BulkUpdateView(APIView):
    """POST /api/v1/organizations/bulk/ — `{ids, action, value}` from the
    selection bar. Applied per id under the single-edit rules; returns
    `{updated, failed: [{id, reason}]}` with a 200 even when some failed.
    Churn is not an action here: it keeps its own modal and endpoint."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = BulkRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = bulk.apply(request, ids=data["ids"], action=data["action"], value=data["value"])
        audit.record(  # SOC2:LOG-01
            "organizations.bulk_updated",
            request=request,
            outcome=(
                AuditEvent.Outcome.SUCCESS if result["updated"] else AuditEvent.Outcome.FAILURE
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

`services/organizations/urls.py`: add this to `urlpatterns`:

```text
    path("bulk/", views.BulkUpdateView.as_view(), name="organizations-bulk"),
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.organizations services.customers.tests.test_views --noinput`
Expected: all tests pass.

- [ ] **Step 6: Lint and commit**

```bash
venv/bin/ruff check --fix services && venv/bin/ruff format services/organizations services/customers/views.py
git add services/organizations services/customers/views.py
git commit -m "feat(organizations): bulk owner, stage and archive under the single-edit rules

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Documentation

**Files:**
- Modify: `docs/API_CONTRACTS.md`, `docs/audit-events.md`, `docs/data-classification.md`, `docs/product/01-prd.md`, `docs/product/05-backend-schema.md`

**Interfaces:**
- Consumes: the endpoints from Tasks 7, 8 and 9. Everything written here must match their behaviour and the pre-flight table.

- [ ] **Step 1: `docs/API_CONTRACTS.md` — Status table**

Add this row directly under the `| Organizations (list/board/detail) | customers | … |` row:

```text
| Organizations portfolio (`/organizations` list redesign) | `organizations` | ✅ Built — `GET /organizations/portfolio/` (rows, details, groups, summary, filters, cursor pages), `GET /organizations/portfolio/export.csv`, `POST /organizations/bulk/` |
```

- [ ] **Step 2: `docs/API_CONTRACTS.md` — new section**

Insert the following section immediately above the line `## Files — the Files tab on organisations and accounts`:

````text
## `organizations` — Organizations portfolio (`/organizations`)

Built for the redesigned list (spec: react-ts-app `docs/superpowers/specs/2026-09-25-organizations-portfolio-design.md`).
`/api/v1/customers/` is unchanged and still serves its other consumers. No model: `services/organizations/book.py`
loads the viewer's book and computes every row signal with the code the dashboard uses, so the two agree.

### `GET /api/v1/organizations/portfolio/`

Auth: `IsAuthenticated`. Scope: `visible_customers(user)`, archived excluded; **churned** (a `churn_date` or the
Churn stage) excluded unless `lifecycle` includes `churn` or `include_churned=1`. This differs from
`/customers/`, which lists churned rows. `ids` names records explicitly: archived and churned ones named there
are included; ids outside visibility are silently absent. Unknown parameter values are ignored, never a 400.

| Param | Meaning |
|---|---|
| `search` | name or Revenact ID (the row id as text), case-insensitive contains. There is no external-ID field on Customer, so none is searched |
| `owner` | user id or `unassigned` |
| `lifecycle` | comma list of `Customer.LifecycleStage` values |
| `health` | comma list of `good,average,poor` (score bands ≥ 7, 4–6.9, < 4) |
| `product` | comma list of `Product` ids (the customer's `primary_product`) |
| `renews_within` | `30`, `90` or `180`: renewal on or before today + N, overdue included |
| `nps` | `promoter` (> 0), `passive` (= 0) or `detractor` (< 0) — `/customers/stats/`'s rule |
| `ids` | comma list, first 500 read; present with no usable id → no rows |
| `include_churned` | `1` |
| `sort` | `arr`, `health`, `renewal`, `touch`, `risk`, `name`, or a numeric field (`arr_billed_at_hq`, `implementation_fee`, `total_contract_value`, `total_forecasted_renewal_revenue`, `total_contracted_seats`, `total_active_seats`, `seat_utilization_percentage`, `total_hires`, `nps_score`, `csat_score`, `ces_percentage`, `ai_pulse_value`, `csm_pulse_score`); `-` prefix descending; default `-arr`. Missing values sort last either way; ties by name. `touch` treats never-contacted as the longest silence. Money sorts converted |
| `group` | `health` (Poor, Average, Good), `owner` (by name, Unassigned last), `lifecycle` (stage order), `product` (by name, No product last), `renewal` (`overdue`, `30`, `90`, `180`, `later`, `none`), or empty |
| `group_value` | with `group` only: restrict `results` and `count` to that group key (a board column). `groups` and `summary` stay whole |
| `cursor` | opaque, from `next_cursor` |
| `limit` | default 50, max 100 |

```json
{
  "results": [{
    "id": 7, "name": "Pizza Hut", "initials": "PH",
    "owner": {"id": 2, "name": "Carl CSM"},
    "lifecycle": {"value": "live", "label": "Live"},
    "health": {"score": 4.9, "category": "average", "trend": [6.2, 5.8, 5.5, 5.1, 5.0, 4.9]},
    "renewal": {"date": "2026-08-09", "days": -47},
    "arr": 69600.0,
    "risk": {"score": 73, "direction": "flat"},
    "pulse": {"csm": 3, "ai": 1, "ai_category": "high_risk", "ai_label": "High Risk",
              "reason": "Quiet since the outage.", "history": [1, 2, 2], "disagree": true},
    "last_touch_days": 33,
    "urgent_tickets": 0,
    "signal": {"kind": "renewal_overdue", "label": "Renewal overdue"},
    "is_archived": false, "churned": false,
    "details": {
      "commercial": {"currency": "USD", "arr_billed_at_account": 69600.0, "arr_billed_at_hq": 10000.0,
                     "total_contract_value": 208800.0, "total_forecasted_renewal_revenue": 70000.0,
                     "implementation_fee": 5000.0},
      "contract": {"joined_date": "2024-01-15", "contract_start_date": "2024-02-01",
                   "renewal_date": "2026-08-09", "contract_end_date": "2027-01-31"},
      "adoption": {"total_contracted_seats": 100, "total_active_seats": 16, "seat_utilization_percentage": 16.0,
                   "total_hires": 40, "products": {"primary": {"id": 3, "name": "Core"}, "additional_count": 2},
                   "scope_web_app": "Full"},
      "voice": {"nps_score": -80, "csat_score": 72.5, "ces_percentage": 61.0,
                "ai_pulse_reason": "Quiet since the outage."},
      "profile": {"revenact_id": 7, "domain": "pizzahut.example", "address": "1 Main St, Dallas",
                  "top_source_channel": "Referral"},
      "history": {"created_by": {"id": 1, "name": "Alice Admin"}, "created_at": "2026-01-02T10:00:00+00:00",
                  "modified_by": {"id": 2, "name": "Carl CSM"}, "updated_at": "2026-09-20T08:00:00+00:00",
                  "churn_date": null, "churn_reason": "", "churn_reason_label": "", "churn_comment": ""}
    }
  }],
  "next_cursor": "eyJpZCI6Nywib2Zmc2V0Ijo1MH0",
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
  "filters": {"owners": [{"value": "2", "name": "Carl CSM"}, {"value": "unassigned", "name": "Unassigned"}],
              "lifecycles": [{"value": "live", "name": "Live"}],
              "products": [{"value": "3", "name": "Core"}]},
  "currency": "USD"
}
```

- **Money.** `arr`, `groups[].arr`, the summary's money and money sorts are in the organisation's `currency`,
  converted like `/customers/health/`; `null` / left out of sums when the contract currency has no FX rate
  (`summary.unconverted_count` says how many). `details.commercial` stays in the customer's own currency.
- **Signals** come from the dashboard's code: `health.trend` is the last five month-end snapshots within six
  months plus today's score; `risk` is the Triage score over the Health view's 12-month history (equal to
  `/customers/health/` `triage_score`); `last_touch_days` is Activity Tracking's last-contact rule (`null` =
  never); `urgent_tickets` counts open High/Critical tickets the viewer's department may read, an account's
  ticket counting for each of its companies (the attention list's support rule). `signal` is at most one of
  `renewal_overdue`, `risk` (score ≥ 40, "Risk NN"), `tickets` ("N open tickets"), in that priority; always
  `null` for a churned row. `pulse.disagree` is `|csm − ai| ≥ 2`.
- **Totals.** `groups` and `summary` cover every filtered row, not the page. The summary reproduces
  `/customers/stats/` (health counts and ARR/MRR, NPS by sign, lifecycle) and the list's `?renewal_within=`
  counts on the same set.
- **Pages.** `next_cursor` is `null` on the last page. It names the last row served; if that row leaves the set
  before the next request, the next page starts where it was. A malformed cursor returns the first page.
- **Filters** are the viewer's visible, non-archived customers' owners, stages and products (churned included).
- Constant query count per request, whatever the book size (pinned by `PortfolioQueryCountTests`).

### `GET /api/v1/organizations/portfolio/export.csv`

Auth: `IsAuthenticated`. Same parameters (`cursor`/`limit` ignored); every row in list order. `text/csv`,
`Content-Disposition: attachment; filename="organizations-<date>.csv"`. Columns: the 34 Organizations table
fields in the old table's order (labels without "($)") plus `Currency`. Text cells beginning with `= + - @`,
tab or CR are prefixed with `'`. Errors are a one-cell CSV. Audited as `organizations.exported`.

### `POST /api/v1/organizations/bulk/`

Auth: `IsAuthenticated`. Body `{"ids": [1, 2], "action": "set_owner" | "set_lifecycle" | "archive", "value": …}`:
`set_owner` takes a user id or `null`; `set_lifecycle` a stage other than `churn` (400 — churn keeps its own
modal, which records date and reason); `archive` ignores `value`. 1–500 ids, duplicates applied once.

Each id runs the ordinary customer update (`CustomerSerializer`, partial): the record must be visible to the
caller, an owner must be in the caller's organisation, and only the current owner, their management chain or an
organisation-settings manager may reassign; an owner change is handed over (a contribution) and the new owner
notified, exactly as `PATCH /customers/<id>/`. `200`:

```json
{"updated": [1], "failed": [{"id": 2, "reason": "Not found."}]}
```

An id the caller cannot see reads `"Not found."`, like one that does not exist. Audited as
`organizations.bulk_updated` (outcome `failure` when nothing was updated).
````

- [ ] **Step 3: `docs/audit-events.md`**

Append these rows to the catalogue table:

```text
| `organizations.exported` | `services.organizations.views.PortfolioExportView` | user | — | `count` of rows exported, `params`: the query-parameter names used (never their values — a search term is the user's own words) |
| `organizations.bulk_updated` | `services.organizations.views.BulkUpdateView` | user | — | `action`, `value` (owner id, stage, or true for archive), `ids` updated, `failed_ids`; outcome `failure` when nothing was updated |
```

- [ ] **Step 4: `docs/data-classification.md`**

Append this row to the "Flows that leave the tenant boundary" table:

```text
| Organizations CSV export (`services/organizations`, `GET /organizations/portfolio/export.csv`) → the requester's device | the 34 organisation fields (commercial terms, owners, churn reasons) for the requester's own visible, filtered book | confidential | visibility-scoped exactly like the list (AUTH-02); audited `organizations.exported` (LOG-01); formula cells neutralised against CSV injection |
```

- [ ] **Step 5: `docs/product/01-prd.md`**

In §5.2 Records, add this row directly under the "Organisations (customers): list, board, detail, …" row:

```text
| Organizations portfolio (list redesign) | Built (backend) | One endpoint for the page: rows with health trend, renewal runway, pulse, last touch and one signal computed by the dashboard's own code; the six detail panels; group, sort, filters and cursor pages; summary tiles over the filtered set; CSV export of all 34 fields; bulk owner, stage and archive under the single-edit rules |
```

In the milestones table, add this row after the `2026-09-24` row:

```text
| 2026-09-25 | Organizations portfolio (backend): portfolio endpoint, export, bulk edit |
```

- [ ] **Step 6: `docs/product/05-backend-schema.md`**

Add this subsection directly after the `### attention` subsection, before its closing `---`. Put it after that subsection's final paragraph:

```text
### `organizations`

No model. `services.organizations.book.load_portfolio` reads `Customer` (with `with_health_inputs`),
`HealthSnapshot`, `FxRate` and `Ticket` for the viewer's visible, filtered book on every request, and
`shape.py` orders, groups and totals it in Python — the same reuse-the-existing-rule shape as the
dashboard rollups. Bulk edits write `Customer` through `CustomerSerializer`.
```

- [ ] **Step 7: Verify the docs do not break the build, then commit**

Run: `venv/bin/ruff format --check . && venv/bin/ruff check .`
Expected: no changes needed and no lint errors. The inserted blocks contain no `python` fences. If ruff reformats a doc, keep ruff's version.

```bash
git add docs
git commit -m "docs(organizations): portfolio, export and bulk contracts, audit events and data flow

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: End-to-end flow

**Files:**
- Create: `e2e/test_organizations_flow.py`

**Interfaces:**
- Consumes: `e2e.http.http_get` and `http_post`, and the three endpoints.

- [ ] **Step 1: Write the test**

`e2e/test_organizations_flow.py`:

```python
"""End-to-end tier: real server, real HTTP, real test DB. A CSM reads their
portfolio, never sees the admin's organization (not even by id), groups and
filters it, moves stages in bulk (the admin's id fails, theirs succeed),
archives one, and exports what is left as CSV."""

import csv
import io
import urllib.request

from django.test import LiveServerTestCase

from e2e.http import http_get, http_post


def http_get_text(url, token):
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request) as response:
        return response.status, response.headers.get("Content-Type"), response.read().decode()


class OrganizationsPortfolioFlowTests(LiveServerTestCase):
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

        # 2. The admin's own organization (owned by the admin) and two of Carl's.
        status, body = http_post(
            self.api("/customers/"),
            {"name": "Admin Co", "arr_billed_at_account": "90000"},
            token=admin,
        )
        self.assertEqual(status, 201, body)
        admin_co = body["id"]
        ids = {}
        for name, score, arr in (("Globex", "2.0", "50000"), ("Initech", "8.0", "20000")):
            status, body = http_post(
                self.api("/customers/"),
                {
                    "name": name,
                    "health_score": score,
                    "arr_billed_at_account": arr,
                    "lifecycle_stage": "live",
                },
                token=csm,
            )
            self.assertEqual(status, 201, body)
            ids[name] = body["id"]

        # 3. Carl's portfolio: his two, largest ARR first; the admin's is absent,
        #    even when named.
        status, body = http_get(self.api("/organizations/portfolio/"), token=csm)
        self.assertEqual(status, 200, body)
        self.assertEqual([row["name"] for row in body["results"]], ["Globex", "Initech"])
        self.assertEqual(body["summary"]["accounts"], 2)
        self.assertEqual(body["summary"]["arr"], 70000.0)
        status, body = http_get(self.api(f"/organizations/portfolio/?ids={admin_co}"), token=csm)
        self.assertEqual((status, body["count"]), (200, 0))

        # 4. Grouped by health: Poor first; filtered to Poor, the one risky row.
        status, body = http_get(self.api("/organizations/portfolio/?group=health"), token=csm)
        self.assertEqual([g["key"] for g in body["groups"]], ["poor", "good"])
        status, body = http_get(self.api("/organizations/portfolio/?health=poor"), token=csm)
        self.assertEqual([row["name"] for row in body["results"]], ["Globex"])
        self.assertEqual(body["results"][0]["signal"], {"kind": "risk", "label": "Risk 66"})

        # 5. Bulk stage change: Carl's two move, the admin's reports Not found.
        status, body = http_post(
            self.api("/organizations/bulk/"),
            {
                "ids": [ids["Globex"], ids["Initech"], admin_co],
                "action": "set_lifecycle",
                "value": "expansion",
            },
            token=csm,
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["updated"], [ids["Globex"], ids["Initech"]])
        self.assertEqual(body["failed"], [{"id": admin_co, "reason": "Not found."}])

        # 6. Archive Initech; it leaves the portfolio.
        status, body = http_post(
            self.api("/organizations/bulk/"),
            {"ids": [ids["Initech"]], "action": "archive"},
            token=csm,
        )
        self.assertEqual((status, body["updated"]), (200, [ids["Initech"]]))
        status, body = http_get(self.api("/organizations/portfolio/"), token=csm)
        self.assertEqual([row["name"] for row in body["results"]], ["Globex"])
        self.assertEqual(body["results"][0]["lifecycle"]["value"], "expansion")

        # 7. Export: a CSV of every field for what is left.
        status, content_type, text = http_get_text(
            self.api("/organizations/portfolio/export.csv"), csm
        )
        self.assertEqual(status, 200)
        self.assertTrue(content_type.startswith("text/csv"))
        table = list(csv.reader(io.StringIO(text)))
        self.assertEqual(len(table[0]), 35)
        self.assertEqual(table[0][0], "Organization")
        self.assertEqual(table[0][-1], "Currency")
        self.assertEqual([row[0] for row in table[1:]], ["Globex"])
```

- [ ] **Step 2: Run it**

Run: `venv/bin/python manage.py test e2e.test_organizations_flow --noinput`
Expected: PASS. It exercises the real server, JWT auth and every endpoint from Tasks 7–9. If it fails, the bug is in the implementation: debug with superpowers:systematic-debugging, and do not weaken the test.

- [ ] **Step 3: Lint and commit**

```bash
venv/bin/ruff check --fix e2e && venv/bin/ruff format e2e
git add e2e/test_organizations_flow.py
git commit -m "test(organizations): end-to-end portfolio, bulk and export flow

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Full verification

**Files:** none are created. Fix whatever a step below uncovers in the task that owns it, then re-run from Step 1.

- [ ] **Step 1: Run the whole suite**

Run: `venv/bin/python manage.py test --noinput`
Expected: every test passes. Record the final `Ran N tests … OK` line.

- [ ] **Step 2: Lint, format and migrations**

Run:

```bash
venv/bin/ruff check . && venv/bin/ruff format --check .
venv/bin/python manage.py makemigrations --check --dry-run
```

Expected: no lint errors, no files to reformat, and "No changes detected". The app has no models.

- [ ] **Step 3: Run the security gates as CI runs them**

Run:

```bash
semgrep scan --error --metrics=off --config p/security-audit --config p/secrets --config p/owasp-top-ten --config p/django --exclude .claude --exclude venv --exclude '**/tests/**' --exclude '**/migrations/**' --exclude e2e services/organizations services/customers/views.py
python3 .claude/skills/soc2-dev/scripts/soc2_scan.py . --format md --fail-on critical
```

Expected: semgrep reports 0 findings, and the soc2 scan has no critical findings. If `semgrep` is not installed, run it as `pipx run semgrep scan …`. If semgrep flags the export, keep the `CSVRenderer` design. Add a `# nosemgrep: <rule-id>` with a one-line reason only if the finding is a false positive on text/csv output, and say so in the PR.

- [ ] **Step 4: Check the schema**

Run: `venv/bin/python manage.py spectacular --file /tmp/revenact-schema.yml`
Expected: it exits 0 and `/api/v1/organizations/portfolio/`, `/api/v1/organizations/portfolio/export.csv` and `/api/v1/organizations/bulk/` appear in the file (`grep organizations /tmp/revenact-schema.yml`). "Unable to guess serializer" warnings are expected on `APIView`s, as on the existing dashboard views. Errors are not.

- [ ] **Step 5: Smoke test against the seeded dev database**

Run:

```bash
venv/bin/python manage.py runserver 8011 &
TOKEN=$(curl -s -X POST localhost:8011/api/v1/auth/login/ -H 'Content-Type: application/json' \
  -d '{"email":"<a seeded admin email>","password":"<its password>"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access"])')
curl -s "localhost:8011/api/v1/organizations/portfolio/?group=health&limit=3" -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | head -60
curl -s "localhost:8011/api/v1/organizations/portfolio/export.csv" -H "Authorization: Bearer $TOKEN" | head -3
kill %1
```

Take the seeded admin's email and password from `README.md` ("Seed data"). Expected: the JSON has rows with `trend`, `signal` and `details`, the groups are in the order Poor, Average, Good, and the summary numbers match the Organizations page's current MetricsPanel for the same login. The CSV starts with the 35-column header.

- [ ] **Step 6: Report**

Use superpowers:verification-before-completion. Report the test count, the ruff/semgrep/soc2 results, and the smoke output. Then hand off to superpowers:finishing-a-development-branch. The backend PR merges and deploys before the frontend PR (spec §4).

---

## Self-review

**Spec coverage.**

| Spec requirement | Where it is covered |
|---|---|
| §2 auth and scope | Task 2 (`filtered_queryset`) and Task 7 (401 test) |
| Every param | Task 1 (parsing) and Task 2 (search, owner, lifecycle, health, product, renews_within, nps, ids, include_churned) |
| sort / group / group_value / cursor / limit | Task 1 and Task 4 |
| Row shape: the eight elements, the 6-month trend, renewal days, signal priority | Task 3 and Task 6 |
| `details`, six groups, and all 34 fields | Task 6 (`FIELDS` asserted against the frontend's `ColumnId` list) |
| groups and summary over the whole filtered set | Task 4, Task 5, and Task 7 (`limit=1` test) |
| filters and currency | Task 5 and Task 7 |
| Constant query count, pinned | Task 7 |
| Export: every row, 34 fields, audited | Task 8 |
| Bulk: three actions, per-id rules, partial failures, audit, churn excluded | Task 9 |
| §5 equality tests: health, trend, triage risk, last touch, ARR | Task 3 (plus urgent tickets). Summary against stats and list is in Task 5 |
| §5 visibility test, including via `ids` | Task 2 (queryset), Task 7 (API), Task 8 (export) and Task 9 (bulk) |
| Docs: API_CONTRACTS, audit events, data classification, PRD, schema | Task 10 |
| E2E | Task 11 |
| Verification | Task 12 |
| §3 Ask Revenact and the board UI | Out of scope (deliveries 2 and 3). `group_value` and `book.load_portfolio` are what they will reuse |

**Placeholder scan.** Every code step has complete code. The only value that must be read at run time is the pinned query count, and Task 7 lists every query it covers.

**Type consistency.** `load_portfolio(user, params, *, today)` returns a `Portfolio(entries, organisation, rates, today)`. `Entry.triage` is the `Triage` dataclass, and code reads `.score` and `.direction`. `select(portfolio, params)` returns `(entries, groups)` and is used the same way by `build_listing` (Task 7) and the export (Task 8). `row_payload(entry)` takes an `Entry` everywhere. `after_customer_update(customer, *, actor, previous_owner, handover_note="")` has the same signature in the detail view and in `bulk.apply`.
