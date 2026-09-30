# Pipelines, backend (delivery 1: one book of opportunities and risks) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve the redesigned `/pipelines` list and Board from `GET /api/v1/pipelines/opportunities/` and `GET /api/v1/pipelines/risks/` (rows, groups, tiles, filter options, cursor pages, `group_value` for a Board column), plus `…/export.csv` and `POST /api/v1/pipelines/{kind}/bulk/`. Along the way, give opportunities and risks their dates, a Closed Lost stage and a stage clock, audit their writes, and trim `companies` to the organisations the viewer may open on every endpoint.

**Architecture:** Model changes and the privacy and audit fixes land in `services/customers` (one migration, a `StageClockMixin`, a shared `PipelineItemSerializer`, a `pipeline_audit` module). The book is a new model-less app, `services/pipelines_portfolio/`, laid out like `services/accounts_portfolio/` (`kinds`, `params`, `book`, `rows`, `fields`, `shape`, `export`, `serializers`, `bulk`, `views`, `urls`). One `Kind` object carries everything that differs between opportunities and risks, so one set of modules serves both routes. It imports Organizations' generic helpers (keyset cursor, direction wrapper, section order, CSV cell and renderer, bulk reasons) and Accounts' `linked_organisations`. The forecast and the Copilot digest learn that Closed Lost is not pipeline.

**Tech Stack:** Django 5.2, DRF, PostgreSQL, `SimpleTestCase`/`TestCase`/`APITestCase`/`APIClient`, `LiveServerTestCase` for e2e.

**Spec:** `react-ts-app/docs/superpowers/specs/2026-09-30-pipelines-redesign-design.md` (branch `feat/pipelines-redesign`). This plan covers §2 "Backend (delivery 1)", the parts of §1 the server must supply (tiles, grouping including close month, sort, filters including owner, export, bulk set stage/priority/department/date) and §5 Testing (backend). §3 (Ask) is delivery 2 and out of scope.

**Branch:** `feat/pipelines-portfolio` (already checked out, from `main`). Stay on it.

## Global Constraints

Copied from the spec (quoted text is verbatim):

- Shape: "One book of opportunities or risks across organisations **and** accounts. Each item names its organisation or account."
- Approach: "Mirror the Organizations and Accounts portfolios: a server endpoint filters, groups, totals and pages".
- Dates: "Opportunities gain an optional **expected close** (`expected_close`); risks gain an optional **due by** (`due_by`). Existing rows start empty and read "No date"".
- Lost: "Opportunities gain a **Closed Lost** stage. *Open* = not Closed Won or Closed Lost. For risks, *open* = stage Open".
- Overdue: "Open, with its date in the past".
- Owner: "No owner field. The Owner filter and grouping use the owner of the item's organisation or account".
- Audit: "Create, edit, stage change and delete of an opportunity or risk are audited"; "`opportunity.created|updated|deleted`, `risk.…`, and the bulk batch; no field values in the event beyond ids and the changed field names."
- Privacy: "The department rule and the parent-visibility rule are applied once, in one place. The "Part of" name and `companies` list only organisations the viewer may open, everywhere opportunities and risks are served".
- Privacy (§2): "a row exists only if the viewer may open its organisation or account (`visible_children_q`) **and** may read it by department (`pipeline_visible_q`); filters only narrow. An organisation or account filter the viewer may not open narrows to nothing." "Owner options come only from the viewer's own organisation, as on Accounts."
- Model: "`Opportunity.expected_close` and `Risk.due_by` (nullable dates); `Opportunity.Stage.CLOSED_LOST`; `stage_changed_at` (set on create and whenever the stage changes) for "this quarter" tiles. One migration; no data backfill beyond `stage_changed_at = created_at`."
- Endpoints: "`GET /api/v1/pipelines/opportunities/` and `/risks/` — rows, groups, summary (tiles), filter options, cursor pages; the same response conventions as `/accounts/portfolio/` (unknown parameter values are ignored, never a 400; `ids` narrows, never widens). `…/export.csv` and `POST /api/v1/pipelines/{kind}/bulk/` (set stage, priority, department, date; each id re-read and checked like the single PATCH; one audited batch)."
- Existing endpoints: "stay for the Deals & risks tabs and the forms, and accept the new fields."
- Tiles: "**Opportunities:** Open pipeline (count · MRR) · Closing in 30 / 90 days · Overdue · Won this quarter (count · MRR) · a stage strip (count per open stage)." "**Risks:** MRR at risk (open count · MRR) · Due in 30 / 90 days · Overdue · Mitigated this quarter · a stage strip." "“This quarter” uses the date the stage last changed". "A tile sets its filter; tapping it again clears it. Totals cover every filtered row, not the page."
- Search: "title, and the organisation or account name."
- Group: "stage (Board and List default), close month (`2026-10`, …, *Overdue*, *No date*), organisation or account, owner (with Unassigned), department, priority, or none."
- Sort: "MRR, close date, priority, stage, title; missing values last."
- Filters: "organisation, account, owner (with Unassigned), stage (open stages by default; closed stages opt-in), priority, department, closes or is due within 30/90/180 days, overdue, no date." "Everything lives in the URL."
- List item: "MRR in the workspace currency, the stage tag, priority, department; the date … a signal tag at most: *Overdue* first, then *High priority* on an open item."
- Board: "Columns by stage (Closed Lost shown for opportunities, collapsed by default) … dragging a card sets its stage."
- "**Query counts** pinned and flat as the book grows."
- Testing (backend): "unit tests for the filters, grouping, dates, overdue and summary; integration tests through the endpoints; privacy tests (blind to one account, another tenant, department rule, an organisation filter the viewer cannot open, `companies` trimmed on every endpoint); the summary equals the rows it covers; pinned query counts; an e2e flow; the migration on existing rows."
- Out of scope: "A people-level opportunity owner", "Weighted pipeline or probability per stage", "Moving the dashboard forecast onto the new dates".
- Order: "Backend delivery 1 … then frontend delivery 1 … Backend merges and deploys first." Nothing here removes a field from an existing response, so this PR merges first.

House rules (every task):

- `docs/API_CONTRACTS.md` changes in the same PR (skill `api-contracts`), and so do the product docs (`docs/product/01-prd.md` feature row and release history, `02-trd.md`, `05-backend-schema.md`), `docs/audit-events.md` and `docs/data-classification.md`. Task 14 does them all.
- Tests come in three tiers (skill `backend-testing`): unit (`SimpleTestCase`), integration (`TestCase`/`APITestCase` + `APIClient`) and e2e (`LiveServerTestCase` in `e2e/`). Privacy uses `blind_to_one_account` from `services/customers/tests/test_views.py`. Focused runs: `venv/bin/python manage.py test <label> --noinput`. The full suite: `venv/bin/python manage.py test --parallel --noinput`.
- `venv/bin/ruff check .` and `venv/bin/ruff format --check .` must pass (line length 100, rules E, F, I). Ruff also formats Python fences inside Markdown: run `venv/bin/ruff format <file.md>` on any doc you edit that has a `python` fence.
- Put `# SOC2:AUTH-02 <why>` on every access check and `# SOC2:LOG-01` on every `audit.record` call.
- Semgrep runs in CI: return an opaque cursor string, never a built URL; let a DRF renderer write the CSV (no hand-built `HttpResponse`).
- Commits are conventional (`feat(pipelines): …`, `fix(customers): …`) and end with a blank line and `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Reuse map: what is imported, what is the pipeline's own

Each helper below was read in the current code before the plan relied on it.

| Need | Source | Use | Why |
|---|---|---|---|
| Department rule | `customers.scoping.pipeline_visible_q(user)` | import | The one rule every pipeline endpoint applies |
| Parent rule | `customers.scoping.visible_children_q(user)` | import | `customer__in` / `account__in` subqueries, so no row repeats and no `.distinct()` is needed |
| Parent filters | `visible_customers`, `visible_accounts` | import | An organisation or account filter narrows through them, so one the viewer cannot open names nothing |
| Linked organisations | `accounts_portfolio.book.linked_organisations(user, ids)` | import | One query through `Account.customers`' own table, openable organisations only, lowest id first |
| Param helpers | `organizations.params.int_or_none`, `comma_list`, `DEFAULT_LIMIT`, `MAX_LIMIT`, `MAX_IDS`, `RENEWS_WITHIN_DAYS` | import | Same parsing rules; the date windows are the renewal windows' days (30, 90, 180) |
| Tile windows | `organizations.book.RENEWING_WINDOWS` (`(30, 90)`) | import | The 30/90 tiles |
| Cursor | `organizations.shape.keyset_page(entries, *, rank, cursor, limit, descending, fingerprint, grouped)`, `wrap_desc` | import | Kind-free keyset paging; a section must be `()` or an `(int, str, str)` triple, which every section here is |
| Section order (by name, empty last) | `organizations.shape.group_rank(key, label, group)` | import for parent, owner, department | Its fixed-order branches only fire for `health`, `lifecycle`, `renewal`, none of which is a pipeline group |
| Row helpers | `organizations.rows.iso`, `person` | import | Same shapes |
| CSV | `organizations.export.cell`, `CSVRenderer`; `organizations.fields.Field` | import | Formula neutralising and the error-as-CSV renderer |
| Bulk reasons | `organizations.bulk.NOT_FOUND`, `NOT_UPDATED`, `first_reason` | import | Same per-id reporting |
| Visible-ids memo | `AccountSerializer._visible_customer_ids` | **Task 3** lifts it into `VisibleCustomersMixin` | So Account, Opportunity and Risk serializers share one fail-closed read |
| Group key, sort values, fingerprint, summary | Accounts/Organizations versions | **not reused** | They read `entry.account`/`entry.customer`, lifecycle and health |

## File Structure

| File | Responsibility |
|---|---|
| `services/customers/models.py` (modify) | `StageClockMixin`; `Opportunity.expected_close`, `Risk.due_by`, `stage_changed_at` on both; `Opportunity.Stage.CLOSED_LOST`; `OPEN_STAGES` (both) and `Opportunity.CLOSED_STAGES` |
| `services/customers/migrations/0049_pipeline_dates.py` (new) | The one migration, with the `stage_changed_at = created_at` backfill |
| `services/customers/forecast.py`, `services/copilot/context.py` (modify) | Closed Lost is not counted as pipeline or as open |
| `services/customers/serializers.py` (modify) | `VisibleCustomersMixin`; `PipelineItemSerializer` (shared fields, trimmed `companies`); `OpportunitySerializer`/`RiskSerializer` gain their date and `stage_changed_at` |
| `core/audit.py` (modify) | `record(..., target_repr=None)` so an event can name a record without quoting it |
| `services/customers/pipeline_audit.py` (new) | `create`, `update`, `delete`, `changed_fields`: the audited writes |
| `services/customers/views.py` (modify) | The eight creates go through `pipeline_audit.create`; `PipelineItemWriteMixin` on both detail views |
| `services/pipelines_portfolio/__init__.py`, `apps.py` (new) | App config (`PipelinesPortfolioConfig`) |
| `services/pipelines_portfolio/kinds.py` (new) | `Kind`, `OPPORTUNITIES`, `RISKS`, `KINDS` |
| `services/pipelines_portfolio/params.py` (new) | `PipelineParams`, `parse_params(query, kind)` and the allowed values |
| `services/pipelines_portfolio/book.py` (new) | `quarter_bounds`, `scope`, `date_q`, `filtered_queryset`, `PipelineEntry`, `PipelineBook`, `item_signal`, `load_book`, `filter_options` |
| `services/pipelines_portfolio/rows.py` (new) | `department_label`, `row_payload(entry, kind)` |
| `services/pipelines_portfolio/fields.py` (new) | `fields_for(kind)`: every model field as an export column |
| `services/pipelines_portfolio/shape.py` (new) | Sort values, group keys, section order, `select`, `filter_fingerprint`, `paginate`, `build_summary`, `build_listing` |
| `services/pipelines_portfolio/export.py` (new) | `table(rows, *, kind, currency)` |
| `services/pipelines_portfolio/serializers.py` (new) | `BulkRequestSerializer` |
| `services/pipelines_portfolio/bulk.py` (new) | `field_for`, `apply(request, kind, *, ids, action, value, result=None)` |
| `services/pipelines_portfolio/views.py`, `urls.py` (new) | `PipelineView`, `PipelineExportView`, `PipelineBulkUpdateView`; one route set per kind |
| `services/pipelines_portfolio/tests/` (new) | `__init__.py`, `fixtures.py`, `test_params.py`, `test_book.py`, `test_rows.py`, `test_shape.py`, `test_summary.py`, `test_views.py`, `test_export.py`, `test_bulk.py` |
| `services/customers/tests/test_pipeline_dates.py`, `test_pipeline_serializers.py`, `test_pipeline_audit.py` (new) | Model, serializer and audit tests |
| `services/customers/tests/test_forecast.py`, `services/copilot/tests/test_context.py` (modify) | Closed Lost tests |
| `config/settings.py`, `config/urls.py` (modify) | Register the app; mount `api/v1/pipelines/` |
| `e2e/test_pipelines_flow.py` (new) | Create, read, group, drag, bulk, export over real HTTP |
| docs (modify) | `API_CONTRACTS.md`, `audit-events.md`, `data-classification.md`, `product/01-prd.md`, `product/02-trd.md`, `product/05-backend-schema.md` |

---

### Task 1: Dates, Closed Lost and the stage clock (model and migration)

**Files:**
- Modify: `services/customers/models.py` (imports; new `StageClockMixin` directly above `class Opportunity`; `Opportunity` and `Risk`)
- Create: `services/customers/migrations/0049_pipeline_dates.py`
- Test: `services/customers/tests/test_pipeline_dates.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Opportunity.expected_close` and `Risk.due_by` (`DateField`, null); `stage_changed_at` on both (`DateTimeField`, default `timezone.now`, `editable=False`); `Opportunity.Stage.CLOSED_LOST == "closed_lost"`; `Opportunity.OPEN_STAGES` (5 values), `Opportunity.CLOSED_STAGES == ("closed_won", "closed_lost")`, `Risk.OPEN_STAGES == ("open",)`; `StageClockMixin`; the migration module `services.customers.migrations.0049_pipeline_dates` with `backfill_stage_changed_at(apps, schema_editor)`.

- [ ] **Step 1: Write the failing tests**

Create `services/customers/tests/test_pipeline_dates.py`:

```python
"""Opportunity and risk dates, the Closed Lost stage, and the stage clock
(`stage_changed_at`) the Pipelines "…this quarter" tiles read."""

import importlib
from datetime import timedelta

from django.apps import apps
from django.test import TestCase
from django.utils import timezone

from services.accounts.models import Organisation
from services.customers.models import Customer, Opportunity, Risk


class Fixture(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.pizza = Customer.objects.create(organisation=self.org, name="Pizza Hut")
        self.long_ago = timezone.now() - timedelta(days=400)

    def backdate(self, item):
        """Moves the clock into the past and reloads, as a request would."""
        type(item).objects.filter(pk=item.pk).update(stage_changed_at=self.long_ago)
        return type(item).objects.get(pk=item.pk)


class StagesAndDatesTests(Fixture):
    def test_closed_lost_is_an_opportunity_stage_after_closed_won(self):
        self.assertEqual(Opportunity.Stage.values[-2:], ["closed_won", "closed_lost"])
        self.assertEqual(Opportunity.Stage.CLOSED_LOST.label, "Closed Lost")

    def test_open_and_closed_stages_partition_each_kind(self):
        self.assertEqual(Opportunity.CLOSED_STAGES, ("closed_won", "closed_lost"))
        self.assertEqual(
            set(Opportunity.OPEN_STAGES) | set(Opportunity.CLOSED_STAGES),
            set(Opportunity.Stage.values),
        )
        self.assertFalse(set(Opportunity.OPEN_STAGES) & set(Opportunity.CLOSED_STAGES))
        self.assertEqual(Risk.OPEN_STAGES, ("open",))

    def test_dates_are_optional(self):
        opportunity = Opportunity.objects.create(customer=self.pizza, title="Upsell")
        risk = Risk.objects.create(customer=self.pizza, title="Budget")
        self.assertIsNone(opportunity.expected_close)
        self.assertIsNone(risk.due_by)
        opportunity.expected_close = timezone.localdate()
        opportunity.save()
        opportunity.refresh_from_db()
        self.assertEqual(opportunity.expected_close, timezone.localdate())


class StageClockTests(Fixture):
    def test_the_clock_starts_at_creation(self):
        before = timezone.now()
        opportunity = Opportunity.objects.create(customer=self.pizza, title="Upsell")
        risk = Risk.objects.create(customer=self.pizza, title="Budget")
        self.assertGreaterEqual(opportunity.stage_changed_at, before)
        self.assertGreaterEqual(risk.stage_changed_at, before)

    def test_a_stage_change_moves_the_clock(self):
        opportunity = self.backdate(Opportunity.objects.create(customer=self.pizza, title="Upsell"))
        opportunity.stage = Opportunity.Stage.CLOSED_WON
        opportunity.save()
        opportunity.refresh_from_db()
        self.assertGreater(opportunity.stage_changed_at, self.long_ago)

    def test_saving_without_a_stage_change_leaves_the_clock(self):
        opportunity = self.backdate(Opportunity.objects.create(customer=self.pizza, title="Upsell"))
        opportunity.title = "Bigger upsell"
        opportunity.mrr = 500
        opportunity.save()
        opportunity.refresh_from_db()
        self.assertEqual(opportunity.stage_changed_at, self.long_ago)

    def test_setting_the_same_stage_again_is_not_a_change(self):
        opportunity = self.backdate(Opportunity.objects.create(customer=self.pizza, title="Upsell"))
        opportunity.stage = Opportunity.Stage.DISCOVERY
        opportunity.save()
        opportunity.refresh_from_db()
        self.assertEqual(opportunity.stage_changed_at, self.long_ago)

    def test_update_fields_with_the_stage_also_writes_the_clock(self):
        risk = self.backdate(Risk.objects.create(customer=self.pizza, title="Budget"))
        risk.stage = Risk.Stage.MITIGATED
        risk.save(update_fields=["stage"])
        risk.refresh_from_db()
        self.assertEqual(risk.stage, "mitigated")
        self.assertGreater(risk.stage_changed_at, self.long_ago)

    def test_update_fields_without_the_stage_leaves_the_clock(self):
        risk = self.backdate(Risk.objects.create(customer=self.pizza, title="Budget"))
        risk.stage = Risk.Stage.MITIGATED
        risk.title = "Budget freeze"
        risk.save(update_fields=["title"])
        risk.refresh_from_db()
        self.assertEqual((risk.stage, risk.stage_changed_at), ("open", self.long_ago))

    def test_a_refreshed_row_judges_the_stage_it_has_now(self):
        opportunity = Opportunity.objects.create(customer=self.pizza, title="Upsell")
        Opportunity.objects.filter(pk=opportunity.pk).update(
            stage=Opportunity.Stage.NEGOTIATION, stage_changed_at=self.long_ago
        )
        opportunity.refresh_from_db()
        opportunity.title = "Renamed"
        opportunity.save()
        opportunity.refresh_from_db()
        self.assertEqual(opportunity.stage_changed_at, self.long_ago)


class StageClockBackfillTests(Fixture):
    """The migration on existing rows: their clock starts at creation."""

    def test_existing_rows_start_their_clock_at_creation(self):
        migration = importlib.import_module("services.customers.migrations.0049_pipeline_dates")
        opportunity = Opportunity.objects.create(customer=self.pizza, title="Old deal")
        risk = Risk.objects.create(customer=self.pizza, title="Old risk")
        created = timezone.now() - timedelta(days=90)
        Opportunity.objects.filter(pk=opportunity.pk).update(created_at=created)
        Risk.objects.filter(pk=risk.pk).update(created_at=created)

        migration.backfill_stage_changed_at(apps, None)

        self.assertEqual(Opportunity.objects.get(pk=opportunity.pk).stage_changed_at, created)
        self.assertEqual(Risk.objects.get(pk=risk.pk).stage_changed_at, created)
        self.assertIsNone(Opportunity.objects.get(pk=opportunity.pk).expected_close)
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `venv/bin/python manage.py test services.customers.tests.test_pipeline_dates --noinput`
Expected: errors — `AttributeError: … has no attribute 'CLOSED_LOST'` / `OPEN_STAGES`, and `ModuleNotFoundError` for the migration.

- [ ] **Step 3: Add the mixin and the fields**

In `services/customers/models.py`, add `from django.utils import timezone` to the Django imports at the top (the file only imports it inside functions today; the local imports keep working).

Directly above `class Opportunity(models.Model):`, add:

```python
class StageClockMixin:
    """Keeps `stage_changed_at` true on Opportunity and Risk. The field's
    default stamps creation; `save()` stamps every later stage change,
    whichever path saves it (a form, a Board drag, a bulk edit, the admin,
    a seed). The Pipelines "won / mitigated this quarter" tiles read it.

    The stage a row was loaded with is remembered in `from_db`, and again
    after each save or refresh, so setting the same stage again is not a
    change. A row loaded without its stage (`.only()`) cannot tell, so its
    clock is left alone. `QuerySet.update(stage=…)` bypasses `save()`:
    nothing may move a stage that way."""

    _STAGE_UNKNOWN = object()

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._loaded_stage = instance.__dict__.get("stage", cls._STAGE_UNKNOWN)
        return instance

    def refresh_from_db(self, using=None, fields=None, **kwargs):
        super().refresh_from_db(using=using, fields=fields, **kwargs)
        if fields is None or "stage" in fields:
            self._loaded_stage = self.stage

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        saves_stage = update_fields is None or "stage" in update_fields
        loaded = getattr(self, "_loaded_stage", self._STAGE_UNKNOWN)
        moved = (
            saves_stage
            and not self._state.adding
            and loaded is not self._STAGE_UNKNOWN
            and self.stage != loaded
        )
        if moved:
            self.stage_changed_at = timezone.now()
            if update_fields is not None:
                kwargs["update_fields"] = {*update_fields, "stage_changed_at"}
        super().save(*args, **kwargs)
        if saves_stage:
            self._loaded_stage = self.stage
```

Change `class Opportunity(models.Model):` to `class Opportunity(StageClockMixin, models.Model):`. In its docstring, replace "`stage` is the closed set of 6 columns the board already has" with "`stage` is the closed set of 7 columns (Closed Lost joined Closed Won on 2026-09-30, the Pipelines redesign)". Add the stage after `CLOSED_WON`:

```python
        CLOSED_WON = "closed_won", "Closed Won"
        CLOSED_LOST = "closed_lost", "Closed Lost"
```

Directly after the `Priority` class inside `Opportunity`, add:

```python
    #: *Open* is anything not closed (spec: "not Closed Won or Closed Lost").
    OPEN_STAGES = (
        Stage.DISCOVERY,
        Stage.QUALIFICATION,
        Stage.SOLUTION_VALIDATION,
        Stage.PROPOSAL_PRICE_REVIEW,
        Stage.NEGOTIATION,
    )
    CLOSED_STAGES = (Stage.CLOSED_WON, Stage.CLOSED_LOST)
```

Directly after `Opportunity.department`, add:

```python
    expected_close = models.DateField(
        null=True,
        blank=True,
        help_text="When the deal is expected to close. Optional: an empty date "
        "reads 'No date' on the Pipelines page.",
    )
    stage_changed_at = models.DateTimeField(
        default=timezone.now,
        editable=False,
        help_text="When `stage` last changed, creation included. Kept by "
        "StageClockMixin; the Pipelines 'this quarter' tiles read it.",
    )
```

Change `class Risk(models.Model):` to `class Risk(StageClockMixin, models.Model):`. Directly after the `Priority` class inside `Risk`, add:

```python
    #: Mitigated, realised and abandoned are closed (the forecast's rule).
    OPEN_STAGES = (Stage.OPEN,)
```

Directly after `Risk.department`, add:

```python
    due_by = models.DateField(
        null=True,
        blank=True,
        help_text="When the risk must be dealt with by. Optional: an empty date "
        "reads 'No date' on the Pipelines page.",
    )
    stage_changed_at = models.DateTimeField(
        default=timezone.now,
        editable=False,
        help_text="When `stage` last changed, creation included. Kept by "
        "StageClockMixin; the Pipelines 'this quarter' tiles read it.",
    )
```

- [ ] **Step 4: Generate the migration, then add the backfill**

Run: `venv/bin/python manage.py showmigrations customers | tail -2`
Expected: the last line is `[X] 0048_call_not_analysable`. If it is not, use the latest name in the next step's `dependencies`.

Run: `venv/bin/python manage.py makemigrations customers --name pipeline_dates`
Expected: `services/customers/migrations/0049_pipeline_dates.py` with four `AddField`s and one `AlterField` on `opportunity.stage`.

Open the file. Add this function above `class Migration`, and append the `RunPython` operation as the **last** item of `operations` (keep Django's generated operations as they are):

```python
def backfill_stage_changed_at(apps, schema_editor):
    """Rows from before the stage clock have no stage history, so their clock
    starts when they were created (spec §2: "no data backfill beyond
    `stage_changed_at = created_at`"). Their dates stay empty."""
    for name in ("Opportunity", "Risk"):
        model = apps.get_model("customers", name)
        model.objects.update(stage_changed_at=models.F("created_at"))
```

```python
(migrations.RunPython(backfill_stage_changed_at, migrations.RunPython.noop),)
```

The finished file reads like this (Django's header comment and argument order may differ; that is fine):

```python
import django.utils.timezone
from django.db import migrations, models


def backfill_stage_changed_at(apps, schema_editor):
    """Rows from before the stage clock have no stage history, so their clock
    starts when they were created (spec §2: "no data backfill beyond
    `stage_changed_at = created_at`"). Their dates stay empty."""
    for name in ("Opportunity", "Risk"):
        model = apps.get_model("customers", name)
        model.objects.update(stage_changed_at=models.F("created_at"))


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0048_call_not_analysable"),
    ]

    operations = [
        migrations.AddField(
            model_name="opportunity",
            name="expected_close",
            field=models.DateField(
                blank=True,
                help_text="When the deal is expected to close. Optional: an empty date "
                "reads 'No date' on the Pipelines page.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="opportunity",
            name="stage_changed_at",
            field=models.DateTimeField(
                default=django.utils.timezone.now,
                editable=False,
                help_text="When `stage` last changed, creation included. Kept by "
                "StageClockMixin; the Pipelines 'this quarter' tiles read it.",
            ),
        ),
        migrations.AddField(
            model_name="risk",
            name="due_by",
            field=models.DateField(
                blank=True,
                help_text="When the risk must be dealt with by. Optional: an empty date "
                "reads 'No date' on the Pipelines page.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="risk",
            name="stage_changed_at",
            field=models.DateTimeField(
                default=django.utils.timezone.now,
                editable=False,
                help_text="When `stage` last changed, creation included. Kept by "
                "StageClockMixin; the Pipelines 'this quarter' tiles read it.",
            ),
        ),
        migrations.AlterField(
            model_name="opportunity",
            name="stage",
            field=models.CharField(
                choices=[
                    ("discovery", "Discovery"),
                    ("qualification", "Qualification"),
                    ("solution_validation", "Solution Validation"),
                    ("proposal_price_review", "Proposal / Price Review"),
                    ("negotiation", "Negotiation"),
                    ("closed_won", "Closed Won"),
                    ("closed_lost", "Closed Lost"),
                ],
                default="discovery",
                max_length=32,
            ),
        ),
        migrations.RunPython(backfill_stage_changed_at, migrations.RunPython.noop),
    ]
```

Run: `venv/bin/ruff format services/customers/migrations/0049_pipeline_dates.py`

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `venv/bin/python manage.py test services.customers.tests.test_pipeline_dates services.customers.tests.test_models services.customers.tests.test_pipeline_departments --noinput`
Expected: `OK`.

Run: `venv/bin/python manage.py makemigrations --check --dry-run`
Expected: `No changes detected`.

- [ ] **Step 6: Commit**

```bash
git add services/customers/models.py services/customers/migrations/0049_pipeline_dates.py services/customers/tests/test_pipeline_dates.py
git commit -m "$(cat <<'EOF'
feat(pipelines): expected close, due by, Closed Lost and the stage clock

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: A lost opportunity is not pipeline (forecast and Copilot)

**Files:**
- Modify: `services/customers/forecast.py` (`STAGE_PROBABILITY` comment, `counted_opportunities`, `pipeline_by_stage`)
- Modify: `services/copilot/context.py` (the `open_opportunities` count)
- Test: `services/customers/tests/test_forecast.py`, `services/copilot/tests/test_context.py`

**Interfaces:**
- Consumes: `Opportunity.Stage.CLOSED_LOST`, `Opportunity.CLOSED_STAGES` (Task 1).
- Produces: `counted_opportunities(ids, viewer)` excludes Closed Lost, so expansion, the scenarios, `pipeline`, `counted_pipeline` and Copilot snapshots never count one. `pipeline_by_stage` still returns exactly the six old stage rows.

- [ ] **Step 1: Write the failing tests**

In `services/customers/tests/test_forecast.py`, change the imports to:

```python
from services.customers import churn, forecast
from services.customers.models import Account, Activity, Customer, Opportunity, Risk
```

Inside `class ForecastViewTests`, directly after `test_pipeline_reports_weighted_and_open_side_by_side`, add:

```python
def test_a_lost_opportunity_is_neither_expansion_nor_pipeline(self):
    customer = self._customer("Lost deal", 100_000, renews_in_days=800)
    Opportunity.objects.create(
        customer=customer,
        title="Gone",
        mrr=Decimal("5000"),
        stage=Opportunity.Stage.CLOSED_LOST,
    )

    data = self.client.get(self.url).data

    self.assertEqual(data["bridge"]["expansion"], 0.0)
    self.assertEqual(data["scenarios"]["best"], 100_000.0)
    self.assertEqual(sum(row["count"] for row in data["pipeline"]), 0)


def test_the_stage_breakdown_keeps_its_six_stages(self):
    # Closed Lost is not pipeline, so it gets no row: the response is
    # what it was before the stage existed.
    self.assertEqual(
        [row["key"] for row in self.client.get(self.url).data["pipeline"]],
        [
            "discovery",
            "qualification",
            "solution_validation",
            "proposal_price_review",
            "negotiation",
            "closed_won",
        ],
    )
```

At the end of the file, add:

```python
class CountedPipelineClosedLostTests(TestCase):
    def test_a_lost_opportunitys_account_is_not_in_the_snapshot(self):
        org = Organisation.objects.create(name="Acme Inc")
        admin = User.objects.create_user(
            email="alice@acme.io",
            password="supersecret1",
            name="Alice",
            organisation=org,
            role=User.Role.ADMIN,
        )
        customer = Customer.objects.create(organisation=org, name="Pizza Hut")
        account = Account.objects.create(name="Pizza EMEA")
        account.customers.add(customer)
        Opportunity.objects.create(
            account=account, title="Gone", stage=Opportunity.Stage.CLOSED_LOST
        )

        self.assertEqual(
            forecast.counted_pipeline([customer], admin),
            {"account_ids": [], "departments": []},
        )
```

In `services/copilot/tests/test_context.py`, inside `class BuildOrgContextSummaryTests`, directly after `test_the_pipeline_counts_follow_the_viewers_department`, add:

```python
def test_a_lost_opportunity_is_not_open(self):
    customer = Customer.objects.create(organisation=self.org, name="Globex", owner=self.user)
    Opportunity.objects.create(
        customer=customer, title="Upsell", stage=Opportunity.Stage.NEGOTIATION
    )
    Opportunity.objects.create(customer=customer, title="Gone", stage=Opportunity.Stage.CLOSED_LOST)

    summary = build_org_context_summary(self.org, self.user)

    self.assertIn("Your pipeline: 1 open opportunities", summary)
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `venv/bin/python manage.py test services.customers.tests.test_forecast services.copilot.tests.test_context --noinput`
Expected: 4 failures — expansion is 60000.0, `best` is 160000.0, the stage list has a seventh `closed_lost` row, the snapshot names the account, and the Copilot line says 2 open opportunities.

- [ ] **Step 3: Leave Closed Lost out of the forecast**

In `services/customers/forecast.py`, extend the comment above `STAGE_PROBABILITY`:

```python
#: Probability an open opportunity closes, by sales stage. The ordinary ladder
#: — see the module docstring on why these are an assumption rather than a
#: measurement. Closed Won is certain by definition and carries no weighting.
#: Closed Lost is not here: `counted_opportunities` leaves it out entirely.
```

Replace `counted_opportunities` with:

```python
def counted_opportunities(ids, viewer):
    """Every opportunity the forecast counts for these customer ids — the one
    queryset its expansion, its stage breakdown and a Copilot reply's
    pipeline snapshot all read, so they cannot disagree. A Closed Lost
    opportunity is not counted anywhere: it is neither expansion nor
    pipeline."""
    return (
        Opportunity.objects.filter(Q(customer_id__in=ids) | Q(account__customers__id__in=ids))
        .exclude(stage=Opportunity.Stage.CLOSED_LOST)
        .filter(readable_pipeline_q(viewer))
        .distinct()
    )
```

In `pipeline_by_stage`, the two loops over `Opportunity.Stage.choices` become loops over the counted stages only, so the response keeps its six rows:

```python
    totals = {
        stage: {"key": stage, "name": label, "open": 0.0, "weighted": 0.0, "count": 0}
        for stage, label in Opportunity.Stage.choices
        if stage in STAGE_PROBABILITY
    }
```

```python
    return [
        {
            **totals[stage],
            "open": round(totals[stage]["open"], 2),
            "weighted": round(totals[stage]["weighted"], 2),
        }
        for stage in totals
    ]
```

In `services/copilot/context.py`, change `.exclude(stage=Opportunity.Stage.CLOSED_WON)` in `open_opportunities` to:

```python
            .exclude(stage__in=Opportunity.CLOSED_STAGES)
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `venv/bin/python manage.py test services.customers.tests.test_forecast services.copilot --noinput`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add services/customers/forecast.py services/copilot/context.py services/customers/tests/test_forecast.py services/copilot/tests/test_context.py
git commit -m "$(cat <<'EOF'
fix(forecast): a Closed Lost opportunity is not expansion or open pipeline

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: The serializers — new fields, and `companies` only names openable organisations

**Files:**
- Modify: `services/customers/serializers.py` (new `VisibleCustomersMixin` above `AccountSerializer`; `AccountSerializer` uses it; `OpportunitySerializer`/`RiskSerializer` rebuilt on `PipelineItemSerializer`)
- Test: `services/customers/tests/test_pipeline_serializers.py`

**Interfaces:**
- Consumes: Task 1's fields.
- Produces: `VisibleCustomersMixin._visible_customer_ids() -> set[int]` (memoised on `self.context["visible_customer_ids"]`, fail-closed); `PipelineItemSerializer` with `COMMON_FIELDS`; `OpportunitySerializer` fields `COMMON_FIELDS + ["expected_close"]`, `RiskSerializer` fields `COMMON_FIELDS + ["due_by"]`; `stage_changed_at` read-only on both. Task 12's bulk saves through these serializers.

- [ ] **Step 1: Write the failing tests**

Create `services/customers/tests/test_pipeline_serializers.py`:

```python
"""The pipeline serializers on every existing endpoint: the new dates and the
Closed Lost stage are read and written, and `companies` names only the
organisations the caller may open (the twice-filter, closing "Known
consequences #2" for opportunities and risks)."""

from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers.models import Account, Customer, Opportunity, Risk
from services.customers.serializers import OpportunitySerializer


class Fixture(APITestCase):
    """Carl owns Pizza Hut. Dana owns Taco Bell and the account Shared, which
    is linked to both: Carl may open Shared (it sits under his organisation)
    but not Taco Bell, so Taco Bell must never be named to him."""

    def setUp(self):
        self.today = timezone.localdate()
        self.org = Organisation.objects.create(name="Acme Inc")
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="supersecret1",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.other = User.objects.create_user(
            email="dana@acme.io",
            password="supersecret1",
            name="Dana",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.pizza = Customer.objects.create(
            organisation=self.org, name="Pizza Hut", owner=self.csm
        )
        self.taco = Customer.objects.create(
            organisation=self.org, name="Taco Bell", owner=self.other
        )
        self.shared = Account.objects.create(name="Shared", owner=self.other)
        self.shared.customers.add(self.pizza, self.taco)
        self.client.force_authenticate(self.csm)


class NewFieldsTests(Fixture):
    def test_an_opportunity_takes_an_expected_close_and_a_risk_a_due_by(self):
        close = (self.today + timedelta(days=30)).isoformat()
        response = self.client.post(
            "/api/v1/opportunities/",
            {
                "customer_id": self.pizza.pk,
                "title": "Upsell",
                "mrr": "100",
                "expected_close": close,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["expected_close"], close)
        self.assertIsNotNone(response.data["stage_changed_at"])
        response = self.client.post(
            f"/api/v1/customers/{self.pizza.pk}/risks/",
            {"title": "Budget", "mrr": "50", "due_by": close},
            format="json",
        )
        self.assertEqual((response.status_code, response.data["due_by"]), (201, close))

    def test_a_date_clears_with_null_and_rejects_nonsense(self):
        opportunity = Opportunity.objects.create(
            customer=self.pizza, title="Upsell", expected_close=self.today
        )
        url = f"/api/v1/opportunities/{opportunity.pk}/"
        response = self.client.patch(url, {"expected_close": None}, format="json")
        self.assertEqual((response.status_code, response.data["expected_close"]), (200, None))
        self.assertEqual(
            self.client.patch(url, {"expected_close": "soon"}, format="json").status_code, 400
        )

    def test_closed_lost_is_accepted_and_a_drag_moves_the_clock(self):
        opportunity = Opportunity.objects.create(customer=self.pizza, title="Upsell")
        long_ago = timezone.now() - timedelta(days=200)
        Opportunity.objects.filter(pk=opportunity.pk).update(stage_changed_at=long_ago)
        response = self.client.patch(
            f"/api/v1/opportunities/{opportunity.pk}/", {"stage": "closed_lost"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            (response.data["stage"], response.data["stage_display"]),
            ("closed_lost", "Closed Lost"),
        )
        opportunity.refresh_from_db()
        self.assertGreater(opportunity.stage_changed_at, long_ago)

    def test_the_clock_is_read_only(self):
        opportunity = Opportunity.objects.create(customer=self.pizza, title="Upsell")
        before = opportunity.stage_changed_at
        self.client.patch(
            f"/api/v1/opportunities/{opportunity.pk}/",
            {"stage_changed_at": "2020-01-01T00:00:00Z"},
            format="json",
        )
        opportunity.refresh_from_db()
        self.assertEqual(opportunity.stage_changed_at, before)


class CompaniesTrimTests(Fixture):
    """Every endpoint that serves an opportunity or a risk names only the
    organisations the caller may open."""

    def companies_everywhere(self, model, plural):
        item = model.objects.create(account=self.shared, title="On shared", mrr=10)
        seen = []
        for url in (
            f"/api/v1/{plural}/",
            f"/api/v1/customers/{self.pizza.pk}/{plural}/",
            f"/api/v1/customers/{self.pizza.pk}/accounts/{self.shared.pk}/{plural}/",
            f"/api/v1/accounts/{self.shared.pk}/{plural}/",
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, (url, response.data))
            seen.append(next(r for r in response.data if r["id"] == item.pk)["companies"])
        seen.append(self.client.get(f"/api/v1/{plural}/{item.pk}/").data["companies"])
        response = self.client.patch(
            f"/api/v1/{plural}/{item.pk}/", {"title": "Renamed"}, format="json"
        )
        seen.append(response.data["companies"])
        response = self.client.post(
            f"/api/v1/{plural}/",
            {"account_id": self.shared.pk, "title": "New", "mrr": "5"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        seen.append(response.data["companies"])
        return seen

    def test_opportunities_never_name_an_organisation_the_caller_cannot_open(self):
        for companies in self.companies_everywhere(Opportunity, "opportunities"):
            self.assertEqual(companies, [{"id": self.pizza.pk, "name": "Pizza Hut"}])

    def test_risks_never_name_one_either(self):
        for companies in self.companies_everywhere(Risk, "risks"):
            self.assertEqual(companies, [{"id": self.pizza.pk, "name": "Pizza Hut"}])

    def test_someone_who_may_open_both_sees_both(self):
        # Dana owns Taco Bell and the account under Pizza Hut, so she opens both.
        item = Opportunity.objects.create(account=self.shared, title="On shared", mrr=10)
        self.client.force_authenticate(self.other)
        companies = self.client.get(f"/api/v1/opportunities/{item.pk}/").data["companies"]
        self.assertCountEqual(
            companies,
            [{"id": self.pizza.pk, "name": "Pizza Hut"}, {"id": self.taco.pk, "name": "Taco Bell"}],
        )

    def test_with_no_request_nothing_is_named(self):
        item = Opportunity.objects.create(account=self.shared, title="On shared", mrr=10)
        self.assertEqual(OpportunitySerializer(item).data["companies"], [])
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `venv/bin/python manage.py test services.customers.tests.test_pipeline_serializers --noinput`
Expected: failures — `KeyError: 'expected_close'` / `'stage_changed_at'`, and `companies` including Taco Bell.

- [ ] **Step 3: Share the visible-ids read**

In `services/customers/serializers.py`, directly above `class AccountSerializer`, add:

```python
class VisibleCustomersMixin:
    """`_visible_customer_ids()`: the ids of the organisations the request's
    user may open. Read once per response and memoised on the serializer's
    context — DRF shares one context across a list's rows, so a page costs
    one query, not one per row. The key is the one
    `ContactCompanyVisibilityMixin` fills, so a view that already read it is
    not read twice. Fails closed: with no request in context nothing is
    provably visible, so nothing is."""

    def _visible_customer_ids(self):
        cache = self.context
        if "visible_customer_ids" not in cache:
            request = cache.get("request")
            # SOC2:AUTH-02 only organisations the caller may open are named
            cache["visible_customer_ids"] = (
                set(visible_customers(request.user).values_list("id", flat=True))
                if request is not None
                else set()
            )
        return cache["visible_customer_ids"]
```

Change `class AccountSerializer(PulseWritesMixin, serializers.ModelSerializer):` to `class AccountSerializer(VisibleCustomersMixin, PulseWritesMixin, serializers.ModelSerializer):` and delete `AccountSerializer._visible_customer_ids` (the whole method, including its comment); its three callers now reach the mixin's.

- [ ] **Step 4: Rebuild the two pipeline serializers on one base**

Replace the whole of `class OpportunitySerializer` and `class RiskSerializer` with:

```python
class PipelineItemSerializer(VisibleCustomersMixin, serializers.ModelSerializer):
    """What OpportunitySerializer and RiskSerializer share, field for field.
    `companies`/`account_id`/`account_name` mirror ContactSerializer's own
    fields (the standalone Pipelines board spans every Customer, so it can't
    assume which parent FK is set). `companies` is every parent organisation
    the caller may open — an account-level item's account can belong to
    organisations the caller cannot see, and those are never named (the
    twice-filter). `stage_display`/`priority_display` are the human labels;
    the raw `stage`/`priority` key drag-and-drop and filtering.
    `stage_changed_at` is read-only (`StageClockMixin` keeps it)."""

    COMMON_FIELDS = [
        "id",
        "title",
        "mrr",
        "stage",
        "stage_display",
        "priority",
        "priority_display",
        "department",
        "department_display",
        "companies",
        "account_id",
        "account_name",
        "stage_changed_at",
    ]

    stage_display = serializers.CharField(source="get_stage_display", read_only=True)
    priority_display = serializers.CharField(source="get_priority_display", read_only=True)
    companies = serializers.SerializerMethodField()
    account_name = serializers.SerializerMethodField()

    department = serializers.ChoiceField(
        choices=User.Function.choices, required=False, allow_blank=True
    )
    department_display = serializers.SerializerMethodField()

    def get_department_display(self, obj):
        return dict(User.Function.choices).get(obj.department, "") if obj.department else ""

    def get_companies(self, obj):
        # SOC2:AUTH-02 a parent organisation is named only if the caller may open it
        visible = self._visible_customer_ids()
        return [{"id": c.id, "name": c.name} for c in obj.companies if c.id in visible]

    def get_account_name(self, obj):
        return obj.account.name if obj.account_id else None


class OpportunitySerializer(PipelineItemSerializer):
    """See Opportunity model's docstring and PipelineItemSerializer.
    `expected_close` is optional; null reads "No date"."""

    class Meta:
        model = Opportunity
        fields = [*PipelineItemSerializer.COMMON_FIELDS, "expected_close"]


class RiskSerializer(PipelineItemSerializer):
    """See Risk model's docstring and PipelineItemSerializer. `due_by` is
    optional; null reads "No date"."""

    class Meta:
        model = Risk
        fields = [*PipelineItemSerializer.COMMON_FIELDS, "due_by"]
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `venv/bin/python manage.py test services.customers services.account_story --noinput`
Expected: `OK` (the new tests, and every existing opportunity, risk and account test).

- [ ] **Step 6: Commit**

```bash
git add services/customers/serializers.py services/customers/tests/test_pipeline_serializers.py
git commit -m "$(cat <<'EOF'
fix(customers): opportunity and risk companies name only openable organisations

The serializers also read and write expected close / due by and return the
stage clock.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Audited opportunity and risk writes

**Files:**
- Modify: `core/audit.py` (`record` gains `target_repr`)
- Create: `services/customers/pipeline_audit.py`
- Modify: `services/customers/views.py` (import; eight creates; `PipelineItemWriteMixin` on `OpportunityDetailView` and `RiskDetailView`)
- Test: `services/customers/tests/test_pipeline_audit.py`

**Interfaces:**
- Consumes: Task 3's serializers.
- Produces: `audit.record(action, *, request=None, actor=None, organisation=None, target=None, target_repr=None, outcome=…, metadata=None)`; `pipeline_audit.create(request, serializer, **save_kwargs) -> instance`, `pipeline_audit.update(request, serializer) -> instance`, `pipeline_audit.delete(request, instance)`, `pipeline_audit.changed_fields(instance, validated_data) -> list[str]`. Events `opportunity.created|updated|deleted`, `risk.created|updated|deleted`.

- [ ] **Step 1: Write the failing tests**

Create `services/customers/tests/test_pipeline_audit.py`:

```python
"""Opportunity and risk writes are audited: create, edit (a drag included)
and delete, from every endpoint that writes them, naming ids and changed
field names only — never a value, never the title."""

from rest_framework.test import APITestCase

from core.models import AuditEvent
from services.accounts.models import Organisation, User
from services.customers.models import Account, Customer, Opportunity, Risk


class PipelineAuditTests(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="supersecret1",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.pizza = Customer.objects.create(
            organisation=self.org, name="Pizza Hut", owner=self.csm
        )
        self.emea = Account.objects.create(name="Pizza EMEA", owner=self.csm)
        self.emea.customers.add(self.pizza)
        self.client.force_authenticate(self.csm)

    def events(self, action):
        return list(AuditEvent.objects.filter(action=action).order_by("id"))

    def test_every_create_path_records_one_event(self):
        paths = [
            ("/api/v1/opportunities/", {"customer_id": self.pizza.pk}),
            ("/api/v1/opportunities/", {"account_id": self.emea.pk}),
            (f"/api/v1/customers/{self.pizza.pk}/opportunities/", {}),
            (f"/api/v1/customers/{self.pizza.pk}/accounts/{self.emea.pk}/opportunities/", {}),
            (f"/api/v1/accounts/{self.emea.pk}/opportunities/", {}),
        ]
        ids = []
        for url, parent in paths:
            response = self.client.post(
                url, {"title": "Secret upsell", "mrr": "10", **parent}, format="json"
            )
            self.assertEqual(response.status_code, 201, (url, response.data))
            ids.append(str(response.data["id"]))
        events = self.events("opportunity.created")
        self.assertEqual([event.target_id for event in events], ids)
        self.assertEqual({event.target_type for event in events}, {"customers.opportunity"})
        self.assertEqual(events[0].actor, self.csm)
        self.assertEqual(events[0].metadata, {"customer_id": self.pizza.pk, "account_id": None})
        self.assertEqual(events[1].metadata, {"customer_id": None, "account_id": self.emea.pk})
        for event in events:
            self.assertEqual(event.target_repr, f"opportunity {event.target_id}")
            self.assertNotIn("Secret", str(event.metadata))

    def test_a_risk_create_is_recorded_as_a_risk(self):
        response = self.client.post(
            f"/api/v1/customers/{self.pizza.pk}/risks/",
            {"title": "Budget", "mrr": "5"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        [event] = self.events("risk.created")
        pk = response.data["id"]
        self.assertEqual(
            (event.target_type, event.target_id, event.target_repr),
            ("customers.risk", str(pk), f"risk {pk}"),
        )

    def test_an_edit_names_the_changed_fields_and_not_their_values(self):
        item = Opportunity.objects.create(customer=self.pizza, title="Upsell", mrr=100)
        response = self.client.patch(
            f"/api/v1/opportunities/{item.pk}/",
            {"title": "Secret new title", "mrr": "100", "expected_close": "2026-12-01"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        [event] = self.events("opportunity.updated")
        self.assertEqual(event.metadata, {"fields": ["expected_close", "title"]})
        self.assertNotIn("Secret", event.target_repr)

    def test_a_drag_is_an_edit_of_the_stage(self):
        item = Risk.objects.create(customer=self.pizza, title="Budget")
        response = self.client.patch(
            f"/api/v1/risks/{item.pk}/", {"stage": "mitigated"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        [event] = self.events("risk.updated")
        self.assertEqual(event.metadata, {"fields": ["stage"]})

    def test_a_patch_that_changes_nothing_or_fails_records_nothing(self):
        item = Opportunity.objects.create(customer=self.pizza, title="Upsell", mrr=100)
        url = f"/api/v1/opportunities/{item.pk}/"
        same = self.client.patch(url, {"title": "Upsell", "mrr": "100.00"}, format="json")
        self.assertEqual(same.status_code, 200)
        self.assertEqual(self.client.patch(url, {"stage": "won"}, format="json").status_code, 400)
        self.assertEqual(self.events("opportunity.updated"), [])

    def test_a_delete_is_recorded_with_its_parent(self):
        item = Opportunity.objects.create(account=self.emea, title="Upsell")
        pk = item.pk
        self.assertEqual(self.client.delete(f"/api/v1/opportunities/{pk}/").status_code, 204)
        self.assertFalse(Opportunity.objects.filter(pk=pk).exists())
        [event] = self.events("opportunity.deleted")
        self.assertEqual(
            (event.target_id, event.metadata),
            (str(pk), {"customer_id": None, "account_id": self.emea.pk}),
        )

    def test_a_delete_the_caller_may_not_make_records_nothing(self):
        dana = User.objects.create_user(
            email="dana@acme.io",
            password="supersecret1",
            name="Dana",
            organisation=self.org,
            role=User.Role.CSM,
        )
        theirs = Customer.objects.create(organisation=self.org, name="Not Carl's", owner=dana)
        item = Opportunity.objects.create(customer=theirs, title="Theirs")
        self.assertEqual(self.client.delete(f"/api/v1/opportunities/{item.pk}/").status_code, 404)
        self.assertEqual(self.events("opportunity.deleted"), [])
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `venv/bin/python manage.py test services.customers.tests.test_pipeline_audit --noinput`
Expected: failures — no `opportunity.*`/`risk.*` events exist.

- [ ] **Step 3: Let an event name its target without quoting it**

In `core/audit.py`, add `target_repr=None,` to `record`'s keyword arguments directly after `target=None,`, and change the `target_repr=` line in `AuditEvent.objects.create(...)` to:

```python
target_repr = (
    ((str(target) if target_repr is None else target_repr)[:255] if target is not None else ""),
)
```

In the module docstring, after the `audit.record("user.deactivate", …)` example paragraph, add: "`target_repr` overrides `str(target)` when the record's own string quotes content that must not reach the log (an opportunity's title)."

- [ ] **Step 4: Write `pipeline_audit.py`**

Create `services/customers/pipeline_audit.py`:

```python
"""Opportunity and risk writes, audited (SOC2:LOG-01). Every create, edit (a
Board drag included) and delete made through the pipeline endpoints goes
through here: `opportunity.created|updated|deleted`, `risk.…`.

An event names the record by id, its parent by id, and for an edit the
fields that changed — never their values, and never the title (a record's
`str()` quotes it, so `target_repr` is just the kind and id). A PATCH that
changes nothing records nothing. Bulk edits are one batch event of their
own (`pipelines.bulk_updated`, services/pipelines_portfolio/views.py)."""

from django.db import transaction

from core import audit


def _record(request, instance, verb, metadata):
    name = instance._meta.model_name
    audit.record(  # SOC2:LOG-01
        f"{name}.{verb}",
        request=request,
        target=instance,
        target_repr=f"{name} {instance.pk}",
        metadata=metadata,
    )


def _parents(instance):
    return {"customer_id": instance.customer_id, "account_id": instance.account_id}


def changed_fields(instance, validated_data):
    """The validated fields whose value differs from the row's, sorted."""
    return sorted(
        name for name, value in validated_data.items() if getattr(instance, name) != value
    )


def create(request, serializer, **save_kwargs):
    instance = serializer.save(**save_kwargs)
    _record(request, instance, "created", _parents(instance))
    return instance


def update(request, serializer):
    changed = changed_fields(serializer.instance, serializer.validated_data)
    instance = serializer.save()
    if changed:
        _record(request, instance, "updated", {"fields": changed})
    return instance


def delete(request, instance):
    # One transaction: the event and the delete land together or not at all.
    with transaction.atomic():
        _record(request, instance, "deleted", _parents(instance))
        instance.delete()
```

- [ ] **Step 5: Route the views' writes through it**

In `services/customers/views.py`, add `pipeline_audit` to the `from . import …` line (keep it alphabetical: `… interactions, pipeline_audit, portfolio, …`).

Replace every `serializer.save(**_pipeline_defaults(self.request, serializer), ` with `pipeline_audit.create(self.request, serializer, **_pipeline_defaults(self.request, serializer), ` — eight places: `CustomerOpportunityListView`, `AccountOpportunityListView`, `OpportunityListView` (two), `CustomerRiskListView`, `AccountRiskListView`, `RiskListView` (two). For example, `OpportunityListView.perform_create` becomes:

```python
    def perform_create(self, serializer):
        account_id = self.request.data.get("account_id")
        customer_id = self.request.data.get("customer_id")
        if account_id:
            # `.distinct()` before `get_object_or_404` — an Account
            # linked to two+ Customers in this organisation would
            # otherwise fan out into more than one row for the *same*
            # pk, which `.get()` (what get_object_or_404 calls) treats
            # as MultipleObjectsReturned rather than a single match.
            account = get_object_or_404(visible_accounts(self.request.user), pk=account_id)
            pipeline_audit.create(
                self.request,
                serializer,
                **_pipeline_defaults(self.request, serializer),
                account=account,
            )
        elif customer_id:
            customer = get_object_or_404(visible_customers(self.request.user), pk=customer_id)
            pipeline_audit.create(
                self.request,
                serializer,
                **_pipeline_defaults(self.request, serializer),
                customer=customer,
            )
        else:
            raise ValidationError("Provide either customer_id or account_id.")
```

Directly above `class OpportunityDetailView`, add:

```python
class PipelineItemWriteMixin:
    """The opportunity and risk detail views' PATCH (a Board drag included)
    and DELETE, audited (`pipeline_audit`)."""

    def perform_update(self, serializer):
        pipeline_audit.update(self.request, serializer)

    def perform_destroy(self, instance):
        pipeline_audit.delete(self.request, instance)
```

Change `class OpportunityDetailView(generics.RetrieveUpdateDestroyAPIView):` to `class OpportunityDetailView(PipelineItemWriteMixin, generics.RetrieveUpdateDestroyAPIView):`, and the same for `RiskDetailView`.

Run: `grep -c "serializer.save(\*\*_pipeline_defaults" services/customers/views.py; grep -c "pipeline_audit.create(" services/customers/views.py`
Expected: `0`, then `8`.

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `venv/bin/python manage.py test services.customers core services.account_story --noinput`
Expected: `OK`.

- [ ] **Step 7: Commit**

```bash
git add core/audit.py services/customers/pipeline_audit.py services/customers/views.py services/customers/tests/test_pipeline_audit.py
git commit -m "$(cat <<'EOF'
feat(customers): audit opportunity and risk creates, edits and deletes

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: The app, its two kinds and its parameters

**Files:**
- Create: `services/pipelines_portfolio/__init__.py` (empty), `apps.py`, `kinds.py`, `params.py`
- Create: `services/pipelines_portfolio/tests/__init__.py` (empty), `tests/test_params.py`
- Modify: `config/settings.py` (`INSTALLED_APPS`)

**Interfaces:**
- Consumes: Task 1's `OPEN_STAGES`; Task 3's serializers.
- Produces: `Kind(key, item, model, serializer, date_field, date_label, open_stages, done_stage)` with `.stages -> tuple[str, ...]`; `OPPORTUNITIES`, `RISKS`, `KINDS: dict[str, Kind]`; `PipelineParams` (fields `stages, search, organisations, accounts, owner, priorities, departments, date, changed, ids, sort, group, group_value, cursor, limit`; properties `sort_key`, `descending`); `parse_params(query, kind) -> PipelineParams`; constants `SORT_KEYS`, `GROUPS`, `DEFAULT_SORT`, `DATE_WINDOWS`, `DATE_FILTERS`, `NO_DEPARTMENT`, `DEPARTMENTS`.

- [ ] **Step 1: Write the failing tests**

Create `services/pipelines_portfolio/tests/__init__.py` (empty) and `services/pipelines_portfolio/tests/test_params.py`:

```python
from django.http import QueryDict
from django.test import SimpleTestCase

from services.pipelines_portfolio.kinds import KINDS, OPPORTUNITIES, RISKS
from services.pipelines_portfolio.params import parse_params

OPEN_DEALS = (
    "discovery",
    "qualification",
    "solution_validation",
    "proposal_price_review",
    "negotiation",
)


def parse(query="", kind=OPPORTUNITIES):
    return parse_params(QueryDict(query), kind)


class KindTests(SimpleTestCase):
    def test_the_two_kinds(self):
        self.assertEqual(set(KINDS), {"opportunities", "risks"})
        self.assertEqual((OPPORTUNITIES.item, RISKS.item), ("opportunity", "risk"))
        self.assertEqual((OPPORTUNITIES.date_field, RISKS.date_field), ("expected_close", "due_by"))
        self.assertEqual((OPPORTUNITIES.date_label, RISKS.date_label), ("Expected Close", "Due By"))
        self.assertEqual((OPPORTUNITIES.done_stage, RISKS.done_stage), ("closed_won", "mitigated"))
        self.assertEqual(OPPORTUNITIES.open_stages, OPEN_DEALS)
        self.assertEqual(RISKS.open_stages, ("open",))
        self.assertEqual(OPPORTUNITIES.stages[-2:], ("closed_won", "closed_lost"))
        self.assertEqual(RISKS.stages, ("open", "mitigated", "realised", "abandoned"))


class ParamsTests(SimpleTestCase):
    def test_defaults(self):
        params = parse()
        self.assertEqual(params.stages, OPEN_DEALS)
        self.assertEqual((params.sort, params.sort_key, params.descending), ("-mrr", "mrr", True))
        self.assertEqual(
            (params.group, params.group_value, params.limit, params.ids), ("", None, 50, None)
        )
        self.assertEqual(parse(kind=RISKS).stages, ("open",))

    def test_closed_stages_are_opt_in(self):
        self.assertEqual(
            parse("stage=closed_won,closed_lost").stages, ("closed_won", "closed_lost")
        )
        self.assertEqual(parse("stage=negotiation,bogus,negotiation").stages, ("negotiation",))
        self.assertEqual(parse("stage=bogus").stages, OPEN_DEALS)
        self.assertEqual(parse("stage=mitigated", kind=RISKS).stages, ("mitigated",))
        # Another kind's stage is not this kind's.
        self.assertEqual(parse("stage=mitigated").stages, OPEN_DEALS)

    def test_ids_reach_every_stage_and_empty_ids_name_nothing(self):
        params = parse("ids=3,x,4")
        self.assertEqual((params.ids, params.stages), ((3, 4), OPPORTUNITIES.stages))
        self.assertEqual(parse("ids=").ids, ())
        self.assertEqual(parse("ids=3&stage=negotiation").stages, ("negotiation",))

    def test_parents_owner_and_choices(self):
        params = parse(
            "organisation=1,x,2&account=5&owner=7"
            "&priority=high,urgent&department=sales,none,pirates"
        )
        self.assertEqual((params.organisations, params.accounts, params.owner), ((1, 2), (5,), 7))
        self.assertEqual(params.priorities, ("high",))
        self.assertEqual(params.departments, ("sales", "none"))
        self.assertEqual(parse("owner=unassigned").owner, "unassigned")
        self.assertIsNone(parse("owner=someone").owner)

    def test_date_and_changed(self):
        for value in ("30", "90", "180", "overdue", "none"):
            self.assertEqual(parse(f"date={value}").date, value)
        self.assertIsNone(parse("date=7").date)
        self.assertEqual(parse("changed=quarter").changed, "quarter")
        self.assertIsNone(parse("changed=year").changed)

    def test_sort_group_search_and_paging(self):
        for sort in ("mrr", "-date", "priority", "-stage", "title"):
            self.assertEqual(parse(f"sort={sort}").sort, sort)
        self.assertEqual(parse("sort=colour").sort, "-mrr")
        for group in ("stage", "month", "parent", "owner", "department", "priority"):
            self.assertEqual(parse(f"group={group}").group, group)
        self.assertEqual(parse("group=health").group, "")
        self.assertEqual(parse("group=stage&group_value=negotiation").group_value, "negotiation")
        self.assertIsNone(parse("group_value=negotiation").group_value)
        self.assertEqual(
            (parse("limit=500").limit, parse("limit=0").limit, parse("limit=x").limit),
            (100, 50, 50),
        )
        self.assertEqual(parse("search=%20pizza%20").search, "pizza")
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `venv/bin/python manage.py test services.pipelines_portfolio.tests.test_params --noinput`
Expected: `ModuleNotFoundError: No module named 'services.pipelines_portfolio.kinds'`.

- [ ] **Step 3: Write the app, the kinds and the parser**

Create `services/pipelines_portfolio/__init__.py` (empty) and `services/pipelines_portfolio/apps.py`:

```python
from django.apps import AppConfig


class PipelinesPortfolioConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "services.pipelines_portfolio"
    verbose_name = "Pipelines portfolio"
```

In `config/settings.py`, add `"services.pipelines_portfolio",` to `INSTALLED_APPS` directly after `"services.account_story",`.

Create `services/pipelines_portfolio/kinds.py`:

```python
"""The two kinds the Pipelines book serves, and everything that differs
between them, named once: the model and its single-edit serializer, the date
field ("expected close" / "due by"), which stages are open, and the stage
the "…this quarter" tile counts (Closed Won / Mitigated). Every other module
takes a `Kind` and never branches on which one it is."""

from dataclasses import dataclass

from services.customers.models import Opportunity, Risk
from services.customers.serializers import OpportunitySerializer, RiskSerializer


@dataclass(frozen=True)
class Kind:
    #: The route segment and the `kind` in a response: "opportunities".
    key: str
    #: One item, as a row's `kind` and an audit prefix: "opportunity".
    item: str
    model: type
    serializer: type
    date_field: str
    date_label: str
    open_stages: tuple[str, ...]
    done_stage: str

    @property
    def stages(self) -> tuple[str, ...]:
        return tuple(self.model.Stage.values)


OPPORTUNITIES = Kind(
    key="opportunities",
    item="opportunity",
    model=Opportunity,
    serializer=OpportunitySerializer,
    date_field="expected_close",
    date_label="Expected Close",
    open_stages=tuple(stage.value for stage in Opportunity.OPEN_STAGES),
    done_stage=Opportunity.Stage.CLOSED_WON.value,
)
RISKS = Kind(
    key="risks",
    item="risk",
    model=Risk,
    serializer=RiskSerializer,
    date_field="due_by",
    date_label="Due By",
    open_stages=tuple(stage.value for stage in Risk.OPEN_STAGES),
    done_stage=Risk.Stage.MITIGATED.value,
)
KINDS = {kind.key: kind for kind in (OPPORTUNITIES, RISKS)}
```

Create `services/pipelines_portfolio/params.py`:

```python
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
    owner = "unassigned" if owner_raw == "unassigned" else int_or_none(owner_raw)

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
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `venv/bin/python manage.py test services.pipelines_portfolio.tests.test_params --noinput`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add config/settings.py services/pipelines_portfolio
git commit -m "$(cat <<'EOF'
feat(pipelines): the pipelines book app, its two kinds and its parameters

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: The book — scope, filters, entries and filter options

**Files:**
- Create: `services/pipelines_portfolio/book.py`
- Create: `services/pipelines_portfolio/tests/fixtures.py`, `tests/test_book.py`

**Interfaces:**
- Consumes: `Kind`, `PipelineParams`, `parse_params`, `NO_DEPARTMENT` (Task 5); `linked_organisations(user, ids) -> dict[int, list[tuple[int, str]]]` from `services.accounts_portfolio.book`.
- Produces: `quarter_bounds(today) -> (date, date)`; `scope(user, kind) -> QuerySet`; `date_q(kind, value, *, today) -> Q`; `filtered_queryset(user, kind, params, *, today)`; `PipelineEntry(item, mrr: float, when: date | None, days: int | None, is_open: bool, overdue: bool, owner: User | None, parent: dict, organisations: list[tuple[int, str]], signal: dict | None)`; `PipelineBook(kind, entries, rows, organisation)` (`entries` every stage, `rows` the chosen stages); `item_signal(*, overdue, is_open, priority)`; `load_book(user, kind, params, *, today) -> PipelineBook`; `filter_options(user, kind) -> dict`. The fixture `PipelineFixture` with `opportunity()`, `risk()`, `account()`, `days()`.

- [ ] **Step 1: Write the fixture**

Create `services/pipelines_portfolio/tests/fixtures.py`:

```python
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from services.accounts.models import Organisation, User
from services.customers.models import Account, Customer, Opportunity, Risk

_CARL = object()


class PipelineFixture(TestCase):
    """Carl and Dana are CSMs (Customer Success) in Acme, Sid is in Sales and
    Alice is its admin (Leadership); Globex is another tenant. Carl owns Pizza
    Hut and Dana owns Taco Bell, so Carl opens Pizza Hut and not Taco Bell.
    `opportunity()`/`risk()` make an organisation-level item on Pizza Hut in
    Customer Success, 1,000 MRR, unless a test says otherwise; `account()`
    makes an account owned by Carl under Pizza Hut."""

    def setUp(self):
        self.today = timezone.localdate()
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")
        self.admin = self.user("alice@acme.io", "Alice Admin", User.Role.ADMIN, "leadership")
        self.csm = self.user("carl@acme.io", "Carl CSM", User.Role.CSM, "cs")
        self.other = self.user("dana@acme.io", "Dana CSM", User.Role.CSM, "cs")
        self.sales = self.user("sid@acme.io", "Sid Sales", User.Role.CSM, "sales")
        self.other_org = Organisation.objects.create(name="Globex", currency="USD")
        self.pizza = Customer.objects.create(
            organisation=self.org, name="Pizza Hut", owner=self.csm
        )
        self.taco = Customer.objects.create(
            organisation=self.org, name="Taco Bell", owner=self.other
        )
        self.globex = Customer.objects.create(organisation=self.other_org, name="Globex Corp")

    def user(self, email, name, role, function):
        return User.objects.create_user(
            email=email,
            password="supersecret1",
            name=name,
            organisation=self.org,
            role=role,
            function=function,
        )

    def account(self, name, *, customers=None, owner=_CARL):
        account = Account.objects.create(name=name, owner=self.csm if owner is _CARL else owner)
        account.customers.add(*(customers if customers is not None else [self.pizza]))
        return account

    def _item(self, model, title, *, customer, account, fields):
        values = {"mrr": Decimal("1000"), "department": User.Function.CS, **fields}
        if account is not None:
            return model.objects.create(account=account, title=title, **values)
        return model.objects.create(customer=customer or self.pizza, title=title, **values)

    def opportunity(self, title, *, customer=None, account=None, **fields):
        return self._item(Opportunity, title, customer=customer, account=account, fields=fields)

    def risk(self, title, *, customer=None, account=None, **fields):
        return self._item(Risk, title, customer=customer, account=account, fields=fields)

    def days(self, n):
        return self.today + timedelta(days=n)
```

- [ ] **Step 2: Write the failing tests**

Create `services/pipelines_portfolio/tests/test_book.py`:

```python
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.http import QueryDict
from django.test import SimpleTestCase
from django.utils import timezone

from services.accounts.models import User
from services.customers.models import Opportunity
from services.customers.tests.test_views import blind_to_one_account
from services.pipelines_portfolio.book import filter_options, load_book, quarter_bounds
from services.pipelines_portfolio.kinds import OPPORTUNITIES, RISKS
from services.pipelines_portfolio.params import parse_params
from services.pipelines_portfolio.tests.fixtures import PipelineFixture

EVERY_STAGE = "stage=" + ",".join(OPPORTUNITIES.stages)


class QuarterTests(SimpleTestCase):
    def test_calendar_quarters(self):
        self.assertEqual(quarter_bounds(date(2026, 9, 30)), (date(2026, 7, 1), date(2026, 9, 30)))
        self.assertEqual(quarter_bounds(date(2026, 10, 1)), (date(2026, 10, 1), date(2026, 12, 31)))
        self.assertEqual(quarter_bounds(date(2026, 2, 14)), (date(2026, 1, 1), date(2026, 3, 31)))
        self.assertEqual(quarter_bounds(date(2026, 5, 31)), (date(2026, 4, 1), date(2026, 6, 30)))


class BookTests(PipelineFixture):
    def titles(self, user=None, query="", kind=OPPORTUNITIES, rows=True):
        params = parse_params(QueryDict(query), kind)
        book = load_book(user or self.csm, kind, params, today=self.today)
        return sorted(entry.item.title for entry in (book.rows if rows else book.entries))

    def test_the_viewer_reads_only_what_they_may_open(self):
        self.opportunity("Mine")
        self.opportunity("Taco's", customer=self.taco)
        self.opportunity("Globex's", customer=self.globex)
        self.assertEqual(self.titles(), ["Mine"])
        self.assertEqual(self.titles(self.admin), ["Mine", "Taco's"])

    def test_the_department_rule_applies(self):
        self.opportunity("Ours")
        self.opportunity("Everyone's", department="")
        self.opportunity("Sales'", department=User.Function.SALES)
        self.assertEqual(self.titles(), ["Everyone's", "Ours"])
        self.assertEqual(self.titles(self.admin), ["Everyone's", "Ours", "Sales'"])

    def test_blind_to_one_account(self):
        viewer, seen, hidden = blind_to_one_account(self.pizza)
        self.opportunity("On seen", account=seen)
        on_hidden = self.opportunity("On hidden", account=hidden)
        self.opportunity("On the organisation")
        self.assertEqual(self.titles(viewer), ["On seen", "On the organisation"])
        self.assertEqual(self.titles(viewer, f"ids={on_hidden.pk}"), [])

    def test_the_stage_filter_shapes_rows_not_the_summary_set(self):
        self.opportunity("Open")
        self.opportunity("Won", stage="closed_won")
        self.opportunity("Lost", stage="closed_lost")
        self.assertEqual(self.titles(), ["Open"])
        self.assertEqual(self.titles(rows=False), ["Lost", "Open", "Won"])
        self.assertEqual(self.titles(query="stage=closed_won,closed_lost"), ["Lost", "Won"])

    def test_an_organisation_filter_rolls_up_accounts_and_hides_what_it_cannot_open(self):
        emea = self.account("Pizza EMEA")
        self.opportunity("On Pizza Hut")
        self.opportunity("On EMEA", account=emea)
        self.opportunity("On Taco Bell", customer=self.taco)
        self.assertEqual(
            self.titles(query=f"organisation={self.pizza.pk}"), ["On EMEA", "On Pizza Hut"]
        )
        # Carl cannot open Taco Bell: naming it narrows to nothing, tiles included.
        self.assertEqual(self.titles(query=f"organisation={self.taco.pk}"), [])
        self.assertEqual(self.titles(query=f"organisation={self.taco.pk}", rows=False), [])
        self.assertEqual(self.titles(self.admin, f"organisation={self.taco.pk}"), ["On Taco Bell"])

    def test_an_account_filter_the_viewer_cannot_open_narrows_to_nothing(self):
        emea = self.account("Pizza EMEA")
        tacos = self.account("Taco EMEA", customers=[self.taco], owner=self.other)
        self.opportunity("On EMEA", account=emea)
        self.opportunity("On Taco EMEA", account=tacos)
        self.assertEqual(self.titles(query=f"account={emea.pk}"), ["On EMEA"])
        self.assertEqual(self.titles(query=f"account={tacos.pk}"), [])
        self.assertEqual(self.titles(query=f"account={emea.pk},{tacos.pk}"), ["On EMEA"])

    def test_owner_is_the_organisations_or_the_accounts(self):
        unowned = self.account("Unowned", owner=None)
        danas = self.account("Dana's division", owner=self.other)
        self.opportunity("On Pizza Hut")
        self.opportunity("On unowned", account=unowned)
        self.opportunity("On Dana's", account=danas)
        self.assertEqual(self.titles(query=f"owner={self.csm.pk}"), ["On Pizza Hut"])
        self.assertEqual(self.titles(query=f"owner={self.other.pk}"), ["On Dana's"])
        self.assertEqual(self.titles(query="owner=unassigned"), ["On unowned"])

    def test_search_reads_the_title_and_the_parent_name(self):
        emea = self.account("Pizza EMEA")
        self.opportunity("Upsell")
        self.opportunity("Seats", account=emea)
        self.assertEqual(self.titles(query="search=upS"), ["Upsell"])
        self.assertEqual(self.titles(query="search=emea"), ["Seats"])
        self.assertEqual(self.titles(query="search=pizza"), ["Seats", "Upsell"])

    def test_date_filters(self):
        self.opportunity("Next week", expected_close=self.days(7))
        self.opportunity("In two months", expected_close=self.days(60))
        self.opportunity("Late", expected_close=self.days(-3))
        self.opportunity("Won late", expected_close=self.days(-3), stage="closed_won")
        self.opportunity("Undated")
        self.assertEqual(self.titles(query=f"date=30&{EVERY_STAGE}"), ["Next week"])
        self.assertEqual(
            self.titles(query=f"date=90&{EVERY_STAGE}"), ["In two months", "Next week"]
        )
        # Overdue is open by definition: a won deal past its date is not late.
        self.assertEqual(self.titles(query=f"date=overdue&{EVERY_STAGE}"), ["Late"])
        self.assertEqual(self.titles(query=f"date=none&{EVERY_STAGE}"), ["Undated"])

    def test_risks_use_due_by(self):
        self.risk("Due soon", due_by=self.days(10))
        self.risk("Due later", due_by=self.days(100))
        self.assertEqual(self.titles(query="date=30", kind=RISKS), ["Due soon"])

    def test_changed_this_quarter(self):
        start, _end = quarter_bounds(self.today)
        self.opportunity("Won now", stage="closed_won")
        old = self.opportunity("Won long ago", stage="closed_won")
        before = timezone.make_aware(datetime.combine(start - timedelta(days=1), time(12)))
        Opportunity.objects.filter(pk=old.pk).update(stage_changed_at=before)
        self.assertEqual(self.titles(query="stage=closed_won&changed=quarter"), ["Won now"])

    def test_priority_and_department_filters(self):
        self.opportunity("High", priority="high")
        self.opportunity("Low", priority="low")
        self.opportunity("Everyone's", department="")
        self.assertEqual(self.titles(query="priority=high"), ["High"])
        self.assertEqual(self.titles(query="department=none"), ["Everyone's"])
        self.assertEqual(self.titles(query="department=cs"), ["High", "Low"])

    def test_ids_narrow_and_never_widen(self):
        mine = self.opportunity("Mine")
        theirs = self.opportunity("Theirs", customer=self.taco)
        self.assertEqual(self.titles(query=f"ids={mine.pk},{theirs.pk}"), ["Mine"])
        self.assertEqual(self.titles(query="ids="), [])


class EntryTests(PipelineFixture):
    def entry(self, item, kind=OPPORTUNITIES, user=None):
        params = parse_params(QueryDict(f"ids={item.pk}"), kind)
        [entry] = load_book(user or self.csm, kind, params, today=self.today).entries
        return entry

    def test_an_organisation_item(self):
        item = self.opportunity("Upsell", mrr=Decimal("2500.50"), expected_close=self.days(12))
        entry = self.entry(item)
        self.assertEqual(
            entry.parent, {"type": "organisation", "id": self.pizza.pk, "name": "Pizza Hut"}
        )
        self.assertEqual(entry.organisations, [(self.pizza.pk, "Pizza Hut")])
        self.assertEqual(
            (entry.mrr, entry.days, entry.is_open, entry.overdue, entry.signal),
            (2500.5, 12, True, False, None),
        )
        self.assertEqual(entry.owner, self.csm)

    def test_an_account_item_names_only_organisations_the_viewer_may_open(self):
        shared = self.account("Shared", customers=[self.pizza, self.taco], owner=self.other)
        item = self.opportunity("Seats", account=shared)
        entry = self.entry(item)
        self.assertEqual(entry.parent, {"type": "account", "id": shared.pk, "name": "Shared"})
        self.assertEqual(entry.organisations, [(self.pizza.pk, "Pizza Hut")])
        self.assertEqual(entry.owner, self.other)
        self.assertEqual(
            self.entry(item, user=self.admin).organisations,
            [(self.pizza.pk, "Pizza Hut"), (self.taco.pk, "Taco Bell")],
        )

    def test_overdue_comes_before_high_priority(self):
        late = self.entry(self.opportunity("Late", expected_close=self.days(-5), priority="high"))
        self.assertEqual(
            (late.days, late.overdue, late.signal),
            (-5, True, {"kind": "overdue", "label": "Overdue"}),
        )
        urgent = self.entry(self.opportunity("Urgent", priority="high"))
        self.assertEqual(urgent.signal, {"kind": "high_priority", "label": "High priority"})
        won = self.entry(
            self.opportunity(
                "Won", priority="high", expected_close=self.days(-5), stage="closed_won"
            )
        )
        self.assertEqual((won.is_open, won.overdue, won.signal), (False, False, None))

    def test_due_today_is_not_overdue(self):
        entry = self.entry(self.risk("Today", due_by=self.today), kind=RISKS)
        self.assertEqual((entry.days, entry.overdue), (0, False))


class FilterOptionTests(PipelineFixture):
    def test_options_are_scoped_as_the_rows_are(self):
        emea = self.account("Pizza EMEA")
        unowned = self.account("Unowned", owner=None)
        tacos = self.account("Taco EMEA", customers=[self.taco], owner=self.other)
        self.opportunity("On Pizza Hut", department="")
        self.opportunity("On EMEA", account=emea)
        self.opportunity("On unowned", account=unowned)
        self.opportunity("On Taco Bell", customer=self.taco)
        self.opportunity("On Taco EMEA", account=tacos)

        options = filter_options(self.csm, OPPORTUNITIES)

        self.assertEqual(
            options["organisations"], [{"value": str(self.pizza.pk), "name": "Pizza Hut"}]
        )
        self.assertEqual(
            options["accounts"],
            [
                {"value": str(emea.pk), "name": "Pizza EMEA"},
                {"value": str(unowned.pk), "name": "Unowned"},
            ],
        )
        self.assertEqual(
            options["owners"],
            [
                {"value": str(self.csm.pk), "name": "Carl CSM"},
                {"value": "unassigned", "name": "Unassigned"},
            ],
        )
        self.assertEqual(
            options["departments"],
            [
                {"value": "cs", "name": "Customer Success"},
                {"value": "none", "name": "No department"},
            ],
        )
        self.assertEqual([s["value"] for s in options["stages"]], list(OPPORTUNITIES.stages))
        self.assertEqual([p["value"] for p in options["priorities"]], ["high", "medium", "low"])

    def test_an_organisation_is_offered_through_its_accounts_items(self):
        emea = self.account("Pizza EMEA")
        self.opportunity("On EMEA", account=emea)
        names = [o["name"] for o in filter_options(self.csm, OPPORTUNITIES)["organisations"]]
        self.assertEqual(names, ["Pizza Hut"])

    def test_an_owner_from_another_tenant_is_never_offered(self):
        stranger = User.objects.create_user(
            email="gus@globex.io",
            password="supersecret1",
            name="Gus Globex",
            organisation=self.other_org,
            role=User.Role.CSM,
        )
        odd = self.account("Odd import", owner=stranger)
        self.opportunity("On odd", account=odd)
        owners = filter_options(self.admin, OPPORTUNITIES)["owners"]
        self.assertNotIn("Gus Globex", [owner["name"] for owner in owners])
```

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `venv/bin/python manage.py test services.pipelines_portfolio.tests.test_book --noinput`
Expected: `ModuleNotFoundError: No module named 'services.pipelines_portfolio.book'`.

- [ ] **Step 4: Write `book.py`**

Create `services/pipelines_portfolio/book.py`:

```python
"""The Pipelines book: the opportunities or risks the viewer may read,
narrowed by the page's filters, with what each row shows.

**Twice filtered, once, here.** An item exists only if the viewer may open
its organisation or account (`visible_children_q`) and may read it by
department (`pipeline_visible_q`) — the single-item endpoints' own rule.
Every filter narrows that scope; none widens it. An organisation or account
filter the viewer cannot open narrows to nothing.

**Stage last, in Python.** The query carries every other filter. The rows
keep the chosen stages (the open ones by default), while the summary reads
every stage of the same set (`PipelineBook.entries`), so "Won this quarter"
and the stage strip survive the default open-only view.

**Money.** `mrr` is in the workspace's currency, as the Pipelines page and
the Deals & risks tabs have always shown it. No FX.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from django.db.models import Q

from services.accounts.models import User
from services.accounts_portfolio.book import linked_organisations
from services.customers.scoping import (
    pipeline_visible_q,
    visible_accounts,
    visible_children_q,
    visible_customers,
)

from .kinds import Kind
from .params import NO_DEPARTMENT, PipelineParams


def quarter_bounds(today):
    """The calendar quarter holding `today`, as (first day, last day)."""
    first_month = 3 * ((today.month - 1) // 3) + 1
    start = date(today.year, first_month, 1)
    if first_month == 10:
        return start, date(today.year, 12, 31)
    return start, date(today.year, first_month + 3, 1) - timedelta(days=1)


def scope(user, kind: Kind):
    """Every item of this kind the viewer may read: the detail endpoints' own
    queryset (`OpportunityDetailView`), without their `.distinct()` — both
    rules are `IN` subqueries, so no row repeats."""
    # SOC2:AUTH-02 twice filtered: the item's organisation or account must be
    # one the viewer may open, and its department one they may read
    return kind.model.objects.filter(visible_children_q(user)).filter(pipeline_visible_q(user))


def date_q(kind: Kind, value, *, today):
    """The `date` filter. A window is today to today+N inclusive (overdue
    has its own value); overdue is open by definition."""
    field = kind.date_field
    if value == "overdue":
        return Q(**{f"{field}__lt": today, "stage__in": kind.open_stages})
    if value == "none":
        return Q(**{f"{field}__isnull": True})
    return Q(**{f"{field}__gte": today, f"{field}__lte": today + timedelta(days=int(value))})


def filtered_queryset(user, kind: Kind, params: PipelineParams, *, today):
    items = scope(user, kind)

    if params.ids is not None:
        if not params.ids:
            return items.none()
        items = items.filter(pk__in=params.ids)

    if params.search:
        text = params.search
        items = items.filter(
            Q(title__icontains=text)
            | Q(customer__name__icontains=text)
            | Q(account__name__icontains=text)
        )

    if params.organisations:
        # SOC2:AUTH-02 an organisation the viewer cannot open narrows to
        # nothing; its accounts' items count only on accounts they may open
        openable = visible_customers(user).filter(pk__in=params.organisations)
        items = items.filter(
            Q(customer__in=openable)
            | Q(account__in=visible_accounts(user).filter(customers__in=openable))
        )

    if params.accounts:
        # SOC2:AUTH-02 an account the viewer cannot open narrows to nothing
        items = items.filter(account__in=visible_accounts(user).filter(pk__in=params.accounts))

    if params.owner == "unassigned":
        items = items.filter(
            Q(customer__isnull=False, customer__owner__isnull=True)
            | Q(account__isnull=False, account__owner__isnull=True)
        )
    elif params.owner is not None:
        items = items.filter(Q(customer__owner_id=params.owner) | Q(account__owner_id=params.owner))

    if params.priorities:
        items = items.filter(priority__in=params.priorities)

    if params.departments:
        wanted = Q(department__in=[d for d in params.departments if d != NO_DEPARTMENT])
        if NO_DEPARTMENT in params.departments:
            wanted |= Q(department="")
        items = items.filter(wanted)

    if params.date:
        items = items.filter(date_q(kind, params.date, today=today))

    if params.changed == "quarter":
        start, end = quarter_bounds(today)
        items = items.filter(stage_changed_at__date__gte=start, stage_changed_at__date__lte=end)

    return items


@dataclass
class PipelineEntry:
    item: object
    #: `mrr` as stored: the workspace's currency.
    mrr: float
    #: The kind's date (expected close / due by), or None.
    when: date | None
    #: Days from today to `when`; negative once it has passed.
    days: int | None
    is_open: bool
    #: Open, with its date in the past.
    overdue: bool
    #: The owner of the item's organisation or account (spec: no owner field).
    owner: object
    #: "Part of": {"type": "organisation" | "account", "id", "name"}.
    parent: dict
    #: The parent organisations the viewer may open, lowest id first.
    organisations: list[tuple[int, str]]
    signal: dict | None


@dataclass
class PipelineBook:
    kind: Kind
    #: Every stage of the filtered set: the summary's.
    entries: list[PipelineEntry]
    #: The chosen stages: the list's and the Board's.
    rows: list[PipelineEntry]
    organisation: object


def item_signal(*, overdue, is_open, priority):
    """At most one tag: Overdue first, then High priority on an open item."""
    if overdue:
        return {"kind": "overdue", "label": "Overdue"}
    if is_open and priority == "high":
        return {"kind": "high_priority", "label": "High priority"}
    return None


def _entry(item, kind: Kind, *, today, linked):
    when = getattr(item, kind.date_field)
    days = None if when is None else (when - today).days
    is_open = item.stage in kind.open_stages
    overdue = is_open and days is not None and days < 0
    if item.customer_id is not None:
        parent = {"type": "organisation", "id": item.customer_id, "name": item.customer.name}
        owner = item.customer.owner
        # `scope` let the item in through this organisation, so it is openable.
        organisations = [(item.customer_id, item.customer.name)]
    else:
        parent = {"type": "account", "id": item.account_id, "name": item.account.name}
        owner = item.account.owner
        organisations = linked.get(item.account_id, [])
    return PipelineEntry(
        item=item,
        mrr=float(item.mrr),
        when=when,
        days=days,
        is_open=is_open,
        overdue=overdue,
        owner=owner,
        parent=parent,
        organisations=organisations,
        signal=item_signal(overdue=overdue, is_open=is_open, priority=item.priority),
    )


def load_book(user, kind: Kind, params: PipelineParams, *, today):
    """The whole filtered book in a fixed number of queries: the items (both
    parents and their owners joined), then the openable organisations of the
    account-level ones (skipped when there are none)."""
    organisation = user.organisation
    items = list(
        filtered_queryset(user, kind, params, today=today).select_related(
            "customer__owner", "account__owner"
        )
    )
    linked = linked_organisations(
        user, sorted({item.account_id for item in items if item.account_id})
    )
    entries = [_entry(item, kind, today=today, linked=linked) for item in items]
    rows = [entry for entry in entries if entry.item.stage in params.stages]
    return PipelineBook(kind=kind, entries=entries, rows=rows, organisation=organisation)


def filter_options(user, kind: Kind):
    """The filter sheet's choices, scoped exactly as the rows are: the
    organisations and accounts the viewer may open that hold one of their
    readable items (an organisation also through its accounts' items), the
    owners of those items' parents, and the departments present. Stages and
    priorities are the kind's fixed lists. Three queries."""
    items = scope(user, kind).order_by()
    # SOC2:AUTH-02 an organisation is offered only if the viewer may open it
    organisations = (
        visible_customers(user)
        .filter(Q(pk__in=items.values("customer_id")) | Q(accounts__in=items.values("account_id")))
        .order_by("name", "id")
        .values_list("id", "name")
        .distinct()
    )
    # SOC2:AUTH-02 an account is offered only if the viewer may open it
    accounts = (
        visible_accounts(user)
        .filter(pk__in=items.values("account_id"))
        .order_by("name", "id")
        .values_list("id", "name")
    )
    parents = items.values_list(
        "customer_id",
        "customer__owner_id",
        "customer__owner__name",
        "customer__owner__organisation_id",
        "account__owner_id",
        "account__owner__name",
        "account__owner__organisation_id",
        "department",
    ).distinct()
    owners, unassigned, departments = {}, False, set()
    for customer_id, *owner_columns, department in parents:
        departments.add(department)
        on_account = customer_id is None
        pk, name, organisation_id = owner_columns[3:] if on_account else owner_columns[:3]
        if pk is None:
            unassigned = True
        elif organisation_id == user.organisation_id:
            # SOC2:AUTH-02 owners only from the viewer's own organisation: a
            # bad import must not put another tenant's name in this menu
            owners[pk] = name
    named = sorted(owners.items(), key=lambda row: (row[1] or "").casefold())
    return {
        "organisations": [{"value": str(pk), "name": name} for pk, name in organisations],
        "accounts": [{"value": str(pk), "name": name} for pk, name in accounts],
        "owners": [{"value": str(pk), "name": name} for pk, name in named]
        + ([{"value": "unassigned", "name": "Unassigned"}] if unassigned else []),
        "stages": [{"value": value, "name": label} for value, label in kind.model.Stage.choices],
        "priorities": [
            {"value": value, "name": label} for value, label in kind.model.Priority.choices
        ],
        "departments": [
            {"value": value, "name": label}
            for value, label in User.Function.choices
            if value in departments
        ]
        + ([{"value": NO_DEPARTMENT, "name": "No department"}] if "" in departments else []),
    }
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `venv/bin/python manage.py test services.pipelines_portfolio --noinput`
Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add services/pipelines_portfolio
git commit -m "$(cat <<'EOF'
feat(pipelines): the twice-filtered book, its filters and filter options

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: The row, and every field in the export

**Files:**
- Create: `services/pipelines_portfolio/rows.py`, `fields.py`
- Test: `services/pipelines_portfolio/tests/test_rows.py`

**Interfaces:**
- Consumes: `PipelineEntry`, `load_book` (Task 6); `Kind` (Task 5).
- Produces: `department_label(value) -> str`; `row_payload(entry, kind) -> dict` with keys `id, kind, title, parent, companies, owner, mrr, stage, priority, department, date, open, overdue, signal, stage_changed_at, created_at`; `fields_for(kind) -> tuple[Field, ...]` (12 columns).

- [ ] **Step 1: Write the failing tests**

Create `services/pipelines_portfolio/tests/test_rows.py`:

```python
from decimal import Decimal

from django.http import QueryDict
from django.test import SimpleTestCase

from services.pipelines_portfolio.book import load_book
from services.pipelines_portfolio.fields import fields_for
from services.pipelines_portfolio.kinds import KINDS, OPPORTUNITIES, RISKS
from services.pipelines_portfolio.params import parse_params
from services.pipelines_portfolio.rows import row_payload
from services.pipelines_portfolio.tests.fixtures import PipelineFixture

#: Every model field of both kinds, and the export column that carries it.
#: The kind's own date maps to "date". A new field fails this until it has a
#: column (and a place in the row).
COLUMN_FOR_MODEL_FIELD = {
    "id": "revenactId",
    "customer": "organizations",
    "account": "account",
    "title": "title",
    "mrr": "mrr",
    "stage": "stage",
    "priority": "priority",
    "department": "department",
    "created_at": "createdDate",
    "stage_changed_at": "stageChangedAt",
}


class FieldCoverageTests(SimpleTestCase):
    def test_every_field_of_both_kinds_has_a_column(self):
        for kind in KINDS.values():
            with self.subTest(kind=kind.key):
                expected = {**COLUMN_FOR_MODEL_FIELD, kind.date_field: "date"}
                self.assertEqual(
                    {field.name for field in kind.model._meta.concrete_fields}, set(expected)
                )
                ids = [field.id for field in fields_for(kind)]
                self.assertEqual(len(ids), len(set(ids)))
                self.assertLessEqual(set(expected.values()), set(ids))
                # The owner is the parent's: the one column with no field of its own.
                self.assertEqual(set(ids) - set(expected.values()), {"owner"})


class RowTests(PipelineFixture):
    def row(self, item, kind=OPPORTUNITIES, user=None):
        params = parse_params(QueryDict(f"ids={item.pk}"), kind)
        [entry] = load_book(user or self.csm, kind, params, today=self.today).entries
        return row_payload(entry, kind)

    def test_an_organisation_opportunity(self):
        item = self.opportunity(
            "Upsell", mrr=Decimal("2500.50"), expected_close=self.days(12), priority="high"
        )
        self.assertEqual(
            self.row(item),
            {
                "id": item.pk,
                "kind": "opportunity",
                "title": "Upsell",
                "parent": {"type": "organisation", "id": self.pizza.pk, "name": "Pizza Hut"},
                "companies": [{"id": self.pizza.pk, "name": "Pizza Hut"}],
                "owner": {"id": self.csm.pk, "name": "Carl CSM"},
                "mrr": 2500.5,
                "stage": {"value": "discovery", "label": "Discovery"},
                "priority": {"value": "high", "label": "High"},
                "department": {"value": "cs", "label": "Customer Success"},
                "date": {"value": self.days(12).isoformat(), "days": 12},
                "open": True,
                "overdue": False,
                "signal": {"kind": "high_priority", "label": "High priority"},
                "stage_changed_at": item.stage_changed_at.isoformat(),
                "created_at": item.created_at.isoformat(),
            },
        )

    def test_an_account_risk_with_a_hidden_organisation(self):
        shared = self.account("Shared", customers=[self.pizza, self.taco], owner=None)
        item = self.risk("Budget", account=shared, due_by=self.days(-2), department="")
        row = self.row(item, kind=RISKS)
        self.assertEqual(row["kind"], "risk")
        self.assertEqual(row["parent"], {"type": "account", "id": shared.pk, "name": "Shared"})
        self.assertEqual(row["companies"], [{"id": self.pizza.pk, "name": "Pizza Hut"}])
        self.assertIsNone(row["owner"])
        self.assertEqual(row["department"], {"value": "", "label": ""})
        self.assertEqual((row["date"]["days"], row["overdue"]), (-2, True))
        self.assertEqual(row["signal"], {"kind": "overdue", "label": "Overdue"})

    def test_closed_lost_reads_as_a_closed_stage(self):
        row = self.row(self.opportunity("Gone", stage="closed_lost", priority="high"))
        self.assertEqual(
            (row["stage"], row["open"], row["signal"]),
            ({"value": "closed_lost", "label": "Closed Lost"}, False, None),
        )

    def test_the_export_columns_read_the_row(self):
        shared = self.account("Shared", customers=[self.pizza, self.taco], owner=None)
        item = self.opportunity("Seats", account=shared, expected_close=self.days(3))
        row = self.row(item, user=self.admin)
        values = {field.id: field.value(row) for field in fields_for(OPPORTUNITIES)}
        self.assertEqual(values["organizations"], "Pizza Hut; Taco Bell")
        self.assertEqual(
            (values["account"], values["owner"], values["date"]),
            ("Shared", "Unassigned", self.days(3).isoformat()),
        )
        self.assertIn("Due By", [field.label for field in fields_for(RISKS)])
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `venv/bin/python manage.py test services.pipelines_portfolio.tests.test_rows --noinput`
Expected: `ModuleNotFoundError` for `fields` / `rows`.

- [ ] **Step 3: Write `rows.py` and `fields.py`**

Create `services/pipelines_portfolio/rows.py`:

```python
"""One Pipelines row as the page reads it, the list item and the Board card
alike: the title, "Part of" its organisation or account, MRR, stage,
priority, department, the date line and at most one signal. Every model
field is in the row, and in the export (`fields.fields_for`);
tests/test_rows.py pins both."""

from services.accounts.models import User
from services.organizations.rows import iso, person

DEPARTMENT_LABELS = dict(User.Function.choices)


def department_label(value):
    return DEPARTMENT_LABELS.get(value, "") if value else ""


def row_payload(entry, kind):
    item = entry.item
    return {
        "id": item.pk,
        "kind": kind.item,
        "title": item.title,
        "parent": entry.parent,
        "companies": [{"id": pk, "name": name} for pk, name in entry.organisations],
        "owner": person(entry.owner),
        "mrr": entry.mrr,
        "stage": {"value": item.stage, "label": item.get_stage_display()},
        "priority": {"value": item.priority, "label": item.get_priority_display()},
        "department": {"value": item.department, "label": department_label(item.department)},
        "date": {"value": iso(entry.when), "days": entry.days},
        "open": entry.is_open,
        "overdue": entry.overdue,
        "signal": entry.signal,
        "stage_changed_at": iso(item.stage_changed_at),
        "created_at": iso(item.created_at),
    }
```

Create `services/pipelines_portfolio/fields.py`:

```python
"""Every Opportunity or Risk field, named once: the export's columns and the
tests' proof that none is lost (tests/test_rows.py maps each model field to
one of these). MRR is in the workspace's currency, which the export prints
beside it."""

from services.organizations.fields import Field


def _organisations(row):
    return "; ".join(company["name"] for company in row["companies"])


def _account(row):
    return row["parent"]["name"] if row["parent"]["type"] == "account" else ""


def _owner(row):
    return row["owner"]["name"] if row["owner"] else "Unassigned"


def fields_for(kind):
    return (
        Field("title", "Title", lambda row: row["title"]),
        Field("revenactId", "Revenact ID", lambda row: row["id"]),
        Field("organizations", "Organizations", _organisations),
        Field("account", "Account", _account),
        Field("owner", "Owner", _owner),
        Field("stage", "Stage", lambda row: row["stage"]["label"]),
        Field("priority", "Priority", lambda row: row["priority"]["label"]),
        Field("department", "Department", lambda row: row["department"]["label"]),
        Field("mrr", "MRR", lambda row: row["mrr"]),
        Field("date", kind.date_label, lambda row: row["date"]["value"]),
        Field("stageChangedAt", "Stage Changed At", lambda row: row["stage_changed_at"]),
        Field("createdDate", "Created Date", lambda row: row["created_at"]),
    )
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `venv/bin/python manage.py test services.pipelines_portfolio --noinput`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add services/pipelines_portfolio
git commit -m "$(cat <<'EOF'
feat(pipelines): the row and every field as an export column

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Sort, group, `group_value` and the cursor

**Files:**
- Create: `services/pipelines_portfolio/shape.py`
- Test: `services/pipelines_portfolio/tests/test_shape.py`

**Interfaces:**
- Consumes: `PipelineBook`, `load_book` (Task 6); `department_label` (Task 7); `keyset_page`, `wrap_desc`, `group_rank` from `services.organizations.shape`.
- Produces: `PRIORITY_ORDER`, `PRIORITY_RANK`; `sort_value(entry, sort_key, kind)`; `month_bucket(entry) -> (key, label)`; `group_key(entry, group, kind) -> (key, label)`; `section_rank(key, label, group, kind) -> tuple`; `order_entries(entries, sort_key, descending, kind, group="")`; `build_groups(entries, group, kind) -> list[{"key","label","count","mrr"}]`; `select(book, params) -> (entries, groups)`; `filter_fingerprint(params, kind) -> str`; `paginate(entries, *, params, kind) -> (page, next_cursor)`.

- [ ] **Step 1: Write the failing tests**

Create `services/pipelines_portfolio/tests/test_shape.py`:

```python
import calendar
from decimal import Decimal

from django.http import QueryDict

from services.pipelines_portfolio.book import load_book
from services.pipelines_portfolio.kinds import OPPORTUNITIES
from services.pipelines_portfolio.params import parse_params
from services.pipelines_portfolio.shape import paginate, select
from services.pipelines_portfolio.tests.fixtures import PipelineFixture

EVERY_STAGE = "stage=" + ",".join(OPPORTUNITIES.stages)


class ShapeTests(PipelineFixture):
    def book(self, query=""):
        params = parse_params(QueryDict(query), OPPORTUNITIES)
        return load_book(self.csm, OPPORTUNITIES, params, today=self.today), params

    def listed(self, query=""):
        book, params = self.book(query)
        entries, groups = select(book, params)
        return [entry.item.title for entry in entries], groups

    def board(self):
        self.opportunity("N1", stage="negotiation", mrr=Decimal("100"))
        self.opportunity("N2", stage="negotiation", mrr=Decimal("50.25"))
        self.opportunity("D1", mrr=Decimal("10"))
        self.opportunity("Lost", stage="closed_lost", mrr=Decimal("5"))

    def test_default_sort_is_largest_mrr_first_ties_by_title(self):
        self.opportunity("B", mrr=Decimal("100"))
        self.opportunity("a", mrr=Decimal("100"))
        self.opportunity("Big", mrr=Decimal("900"))
        self.assertEqual(self.listed()[0], ["Big", "a", "B"])

    def test_missing_dates_sort_last_either_way(self):
        self.opportunity("Soon", expected_close=self.days(2))
        self.opportunity("Later", expected_close=self.days(40))
        self.opportunity("Undated")
        self.assertEqual(self.listed("sort=date")[0], ["Soon", "Later", "Undated"])
        self.assertEqual(self.listed("sort=-date")[0], ["Later", "Soon", "Undated"])

    def test_priority_stage_and_title_sorts(self):
        self.opportunity("Low", priority="low")
        self.opportunity("High", priority="high", stage="negotiation")
        self.opportunity("Medium", stage="qualification")
        self.assertEqual(self.listed("sort=-priority")[0], ["High", "Medium", "Low"])
        self.assertEqual(self.listed("sort=stage")[0], ["Low", "Medium", "High"])
        self.assertEqual(self.listed("sort=title")[0], ["High", "Low", "Medium"])

    def test_stage_groups_follow_the_board_and_carry_totals(self):
        self.board()
        titles, groups = self.listed(f"group=stage&{EVERY_STAGE}")
        self.assertEqual(
            groups,
            [
                {"key": "discovery", "label": "Discovery", "count": 1, "mrr": 10.0},
                {"key": "negotiation", "label": "Negotiation", "count": 2, "mrr": 150.25},
                {"key": "closed_lost", "label": "Closed Lost", "count": 1, "mrr": 5.0},
            ],
        )
        self.assertEqual(titles, ["D1", "N1", "N2", "Lost"])

    def test_group_value_serves_one_board_column_with_every_header(self):
        self.board()
        titles, groups = self.listed("group=stage&group_value=negotiation")
        self.assertEqual(titles, ["N1", "N2"])
        self.assertEqual([group["key"] for group in groups], ["discovery", "negotiation"])

    def test_close_month_sections(self):
        self.opportunity("Late", expected_close=self.days(-3))
        self.opportunity("Won in the past", expected_close=self.days(-3), stage="closed_won")
        self.opportunity("Undated")
        self.opportunity("Soon", expected_close=self.days(1))
        self.opportunity("Far", expected_close=self.days(70))
        _titles, groups = self.listed(f"group=month&{EVERY_STAGE}")
        months = sorted({self.days(n).strftime("%Y-%m") for n in (-3, 1, 70)})
        self.assertEqual([group["key"] for group in groups], ["overdue", *months, "none"])
        self.assertEqual((groups[0]["label"], groups[-1]["label"]), ("Overdue", "No date"))
        far = self.days(70)
        self.assertEqual(groups[-2]["label"], f"{calendar.month_name[far.month]} {far.year}")
        # A closed deal past its date is not overdue: it sits in its month.
        self.assertEqual(groups[0]["count"], 1)

    def test_owner_department_priority_and_parent_sections(self):
        unowned = self.account("Unowned", owner=None)
        self.opportunity("Carl's")
        self.opportunity("Nobody's", account=unowned, department="", priority="high")
        _t, owners = self.listed("group=owner")
        self.assertEqual([g["key"] for g in owners], [str(self.csm.pk), "unassigned"])
        _t, departments = self.listed("group=department")
        self.assertEqual(
            [(g["key"], g["label"]) for g in departments],
            [("cs", "Customer Success"), ("none", "No department")],
        )
        _t, priorities = self.listed("group=priority")
        self.assertEqual([g["key"] for g in priorities], ["high", "medium"])
        _t, parents = self.listed("group=parent")
        self.assertEqual(
            [(g["key"], g["label"]) for g in parents],
            [(f"organisation:{self.pizza.pk}", "Pizza Hut"), (f"account:{unowned.pk}", "Unowned")],
        )

    def test_following_the_cursor_reads_every_row_once(self):
        for i in range(7):
            self.opportunity(
                f"Deal {i}",
                mrr=Decimal(100 * (i % 3)),
                stage=OPPORTUNITIES.open_stages[i % 5],
                expected_close=None if i % 2 else self.days(i),
            )
        for query in ("", "sort=date", "group=stage&sort=-priority", "group=month&sort=title"):
            with self.subTest(query=query):
                book, params = self.book(f"{query}&limit=2")
                expected = [entry.item.pk for entry in select(book, params)[0]]
                seen, cursor = [], ""
                for _ in range(10):
                    params = parse_params(
                        QueryDict(f"{query}&limit=2&cursor={cursor}"), OPPORTUNITIES
                    )
                    page, cursor = paginate(
                        select(book, params)[0], params=params, kind=OPPORTUNITIES
                    )
                    seen += [entry.item.pk for entry in page]
                    if cursor is None:
                        break
                self.assertEqual(seen, expected)
                self.assertEqual(len(seen), 7)

    def test_a_cursor_from_other_filters_reads_the_first_page(self):
        for i in range(4):
            self.opportunity(f"Deal {i}", mrr=Decimal(i))
        book, params = self.book("limit=2")
        _page, cursor = paginate(select(book, params)[0], params=params, kind=OPPORTUNITIES)
        other = parse_params(QueryDict(f"limit=2&priority=medium&cursor={cursor}"), OPPORTUNITIES)
        page, _next = paginate(select(book, other)[0], params=other, kind=OPPORTUNITIES)
        self.assertEqual([entry.item.title for entry in page], ["Deal 3", "Deal 2"])
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `venv/bin/python manage.py test services.pipelines_portfolio.tests.test_shape --noinput`
Expected: `ModuleNotFoundError: No module named 'services.pipelines_portfolio.shape'`.

- [ ] **Step 3: Write `shape.py` (order, groups, paging)**

Create `services/pipelines_portfolio/shape.py`:

```python
"""Ordering, grouping, paging and totals for the Pipelines book — pure
functions over `book.load_book`'s entries. No queries.

The cursor (`keyset_page`), the direction wrapper and the by-name section
order (`group_rank`: by label, the empty bucket last) are Organizations'
own. The sort values, the fixed section orders (stage, priority, close
month) and the fingerprint are the pipeline's. Everything runs over the
whole filtered set, so a section header or a tile never counts only the
page.
"""

import calendar
import hashlib
import json

from services.organizations.shape import group_rank, keyset_page, wrap_desc

from .rows import department_label

PRIORITY_ORDER = ("high", "medium", "low")
#: So `-priority` puts High first.
PRIORITY_RANK = {"high": 3, "medium": 2, "low": 1}


def sort_value(entry, sort_key, kind):
    item = entry.item
    if sort_key == "mrr":
        return entry.mrr
    if sort_key == "date":
        return entry.when
    if sort_key == "priority":
        return PRIORITY_RANK[item.priority]
    if sort_key == "stage":
        return kind.stages.index(item.stage)
    return item.title.casefold()


def _tiebreak(entry):
    return (entry.item.title.casefold(), entry.item.pk)


def month_bucket(entry):
    """Overdue first (open and past its date), then the date's month, then
    No date. A closed item past its date sits in its month."""
    if entry.overdue:
        return "overdue", "Overdue"
    if entry.when is None:
        return "none", "No date"
    return (
        entry.when.strftime("%Y-%m"),
        f"{calendar.month_name[entry.when.month]} {entry.when.year}",
    )


def group_key(entry, group, kind):
    item = entry.item
    if group == "stage":
        return item.stage, item.get_stage_display()
    if group == "month":
        return month_bucket(entry)
    if group == "parent":
        parent = entry.parent
        return f"{parent['type']}:{parent['id']}", parent["name"]
    if group == "owner":
        if entry.owner is None:
            return "unassigned", "Unassigned"
        return str(entry.owner.pk), entry.owner.name
    if group == "department":
        if not item.department:
            return "none", "No department"
        return item.department, department_label(item.department) or item.department
    return item.priority, item.get_priority_display()


def section_rank(key, label, group, kind):
    """A section's position, as the `(int, str, str)` triple the cursor
    carries: stage and priority in their fixed order, close month
    chronologically between Overdue and No date, anything else by name with
    the empty bucket last (Organizations' `group_rank`)."""
    if group == "stage":
        return (kind.stages.index(key), "", "")
    if group == "priority":
        return (PRIORITY_ORDER.index(key), "", "")
    if group == "month":
        if key == "overdue":
            return (0, "", "")
        if key == "none":
            return (2, "", "")
        return (1, key, key)
    return group_rank(key, label, group)


def _rank(entry, sort_key, descending, kind, group=""):
    """The tuple `order_entries` and the cursor both sort by: section, then
    missing values last in either direction, then the (direction-wrapped)
    value, then title and id ascending."""
    section = section_rank(*group_key(entry, group, kind), group, kind) if group else ()
    value = sort_value(entry, sort_key, kind)
    if value is None:
        return (section, 1, None, _tiebreak(entry))
    return (section, 0, wrap_desc(value, descending), _tiebreak(entry))


def order_entries(entries, sort_key, descending, kind, group=""):
    return sorted(entries, key=lambda entry: _rank(entry, sort_key, descending, kind, group))


def build_groups(entries, group, kind):
    groups = {}
    for entry in entries:
        key, label = group_key(entry, group, kind)
        bucket = groups.setdefault(key, {"key": key, "label": label, "count": 0, "mrr": 0.0})
        bucket["count"] += 1
        bucket["mrr"] += entry.mrr
    ordered = sorted(
        groups.values(),
        key=lambda bucket: section_rank(bucket["key"], bucket["label"], group, kind),
    )
    for bucket in ordered:
        bucket["mrr"] = round(bucket["mrr"], 2)
    return ordered


def select(book, params):
    """The rows in list order — sections first, the chosen sort inside each —
    and the section totals over every row of the chosen stages, before
    `group_value` narrows to one Board column."""
    kind = book.kind
    entries = order_entries(book.rows, params.sort_key, params.descending, kind, params.group)
    if not params.group:
        return entries, []
    groups = build_groups(entries, params.group, kind)
    if params.group_value is not None:
        entries = [e for e in entries if group_key(e, params.group, kind)[0] == params.group_value]
    return entries, groups


def filter_fingerprint(params, kind):
    """A short hash of everything that decides which rows a list holds and in
    what order, but not `cursor` or `limit`, so a cursor from a list with
    other filters (or the other kind) reads the new list from its first
    page. Multi-value filters compare as sets."""
    state = [
        kind.key,
        params.search,
        sorted(set(params.organisations)),
        sorted(set(params.accounts)),
        params.owner,
        sorted(set(params.stages)),
        sorted(set(params.priorities)),
        sorted(set(params.departments)),
        params.date,
        params.changed,
        None if params.ids is None else sorted(set(params.ids)),
        params.sort,
        params.group,
        params.group_value,
    ]
    digest = hashlib.sha256(json.dumps(state, separators=(",", ":")).encode())
    return digest.hexdigest()[:16]


def paginate(entries, *, params, kind):
    """Organizations' keyset cursor over `select`'s order."""
    sort_key, descending, group = params.sort_key, params.descending, params.group
    return keyset_page(
        entries,
        rank=lambda entry: _rank(entry, sort_key, descending, kind, group),
        cursor=params.cursor,
        limit=params.limit,
        descending=descending,
        fingerprint=filter_fingerprint(params, kind),
        grouped=bool(group),
    )
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `venv/bin/python manage.py test services.pipelines_portfolio --noinput`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add services/pipelines_portfolio
git commit -m "$(cat <<'EOF'
feat(pipelines): sort, group by stage, month, parent, owner, department or priority, and page

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: The tiles and the listing body

**Files:**
- Modify: `services/pipelines_portfolio/shape.py` (imports; `TILE_WINDOWS`, `_total`, `build_summary`, `build_listing`)
- Test: `services/pipelines_portfolio/tests/test_summary.py`

**Interfaces:**
- Consumes: `select`, `paginate` (Task 8); `row_payload` (Task 7); `quarter_bounds` (Task 6); `RENEWING_WINDOWS` from `services.organizations.book`.
- Produces: `build_summary(book, *, today) -> {"items", "mrr", "open", "within": {"30", "90"}, "overdue", "done_this_quarter": {"stage", "count", "mrr"}, "stages": [{"value", "label", "count", "mrr"}]}` (each money bucket `{"count": int, "mrr": float}`); `build_listing(book, params, *, filters, today) -> {"kind", "results", "next_cursor", "count", "groups", "summary", "filters", "currency"}`.

- [ ] **Step 1: Write the failing tests**

Create `services/pipelines_portfolio/tests/test_summary.py`:

```python
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.http import QueryDict
from django.utils import timezone

from services.accounts.models import User
from services.customers.models import Opportunity
from services.pipelines_portfolio.book import load_book, quarter_bounds
from services.pipelines_portfolio.kinds import OPPORTUNITIES, RISKS
from services.pipelines_portfolio.params import parse_params
from services.pipelines_portfolio.shape import build_listing, build_summary, select
from services.pipelines_portfolio.tests.fixtures import PipelineFixture

EVERY_STAGE = "stage=" + ",".join(OPPORTUNITIES.stages)


class SummaryTests(PipelineFixture):
    def summary(self, query="", kind=OPPORTUNITIES, user=None):
        params = parse_params(QueryDict(query), kind)
        book = load_book(user or self.csm, kind, params, today=self.today)
        return build_summary(book, today=self.today)

    def count(self, query, kind=OPPORTUNITIES):
        params = parse_params(QueryDict(query), kind)
        book = load_book(self.csm, kind, params, today=self.today)
        return len(select(book, params)[0])

    def seed(self):
        self.opportunity("Soon", mrr=Decimal("100"), expected_close=self.days(10))
        self.opportunity(
            "Quarter away", mrr=Decimal("200"), expected_close=self.days(60), stage="negotiation"
        )
        self.opportunity("Late", mrr=Decimal("300"), expected_close=self.days(-4))
        self.opportunity("Undated", mrr=Decimal("400"))
        self.opportunity("Won", mrr=Decimal("500"), stage="closed_won")
        self.opportunity(
            "Lost", mrr=Decimal("600"), stage="closed_lost", expected_close=self.days(-9)
        )
        old = self.opportunity("Won before", mrr=Decimal("700"), stage="closed_won")
        start, _end = quarter_bounds(self.today)
        before = timezone.make_aware(datetime.combine(start - timedelta(days=1), time(12)))
        Opportunity.objects.filter(pk=old.pk).update(stage_changed_at=before)

    def test_the_opportunity_tiles(self):
        self.seed()
        summary = self.summary()
        self.assertEqual((summary["items"], summary["mrr"]), (7, 2800.0))
        self.assertEqual(summary["open"], {"count": 4, "mrr": 1000.0})
        self.assertEqual(
            summary["within"],
            {"30": {"count": 1, "mrr": 100.0}, "90": {"count": 2, "mrr": 300.0}},
        )
        self.assertEqual(summary["overdue"], {"count": 1, "mrr": 300.0})
        self.assertEqual(
            summary["done_this_quarter"], {"stage": "closed_won", "count": 1, "mrr": 500.0}
        )
        stages = {stage["value"]: (stage["count"], stage["mrr"]) for stage in summary["stages"]}
        self.assertEqual(list(stages), list(OPPORTUNITIES.stages))
        self.assertEqual(stages["discovery"], (3, 800.0))
        self.assertEqual(stages["qualification"], (0, 0.0))
        self.assertEqual(stages["closed_won"], (2, 1200.0))
        self.assertEqual(stages["closed_lost"], (1, 600.0))

    def test_each_tile_lists_exactly_its_n(self):
        self.seed()
        summary = self.summary()
        self.assertEqual(self.count(""), summary["open"]["count"])
        self.assertEqual(self.count("date=30"), summary["within"]["30"]["count"])
        self.assertEqual(self.count("date=90"), summary["within"]["90"]["count"])
        self.assertEqual(self.count("date=overdue"), summary["overdue"]["count"])
        self.assertEqual(
            self.count("stage=closed_won&changed=quarter"), summary["done_this_quarter"]["count"]
        )
        for stage in summary["stages"]:
            self.assertEqual(self.count(f"stage={stage['value']}"), stage["count"])

    def test_the_summary_equals_the_rows_it_covers(self):
        self.seed()
        params = parse_params(QueryDict(EVERY_STAGE), OPPORTUNITIES)
        book = load_book(self.csm, OPPORTUNITIES, params, today=self.today)
        entries, _groups = select(book, params)
        summary = build_summary(book, today=self.today)
        self.assertEqual(summary["items"], len(entries))
        self.assertEqual(summary["mrr"], round(sum(entry.mrr for entry in entries), 2))
        self.assertEqual(sum(stage["count"] for stage in summary["stages"]), len(entries))

    def test_filters_narrow_the_tiles_but_the_stage_filter_does_not(self):
        self.seed()
        self.opportunity("High one", priority="high", mrr=Decimal("50"))
        self.assertEqual(self.summary("priority=high")["items"], 1)
        self.assertEqual(self.summary("stage=closed_lost")["items"], 8)

    def test_the_risk_tiles(self):
        self.risk("Open soon", mrr=Decimal("10"), due_by=self.days(5))
        self.risk("Open late", mrr=Decimal("20"), due_by=self.days(-1))
        self.risk("Mitigated", mrr=Decimal("30"), stage="mitigated")
        self.risk("Realised", mrr=Decimal("40"), stage="realised")
        summary = self.summary(kind=RISKS)
        self.assertEqual(summary["open"], {"count": 2, "mrr": 30.0})
        self.assertEqual(summary["within"]["30"], {"count": 1, "mrr": 10.0})
        self.assertEqual(summary["overdue"], {"count": 1, "mrr": 20.0})
        self.assertEqual(
            summary["done_this_quarter"], {"stage": "mitigated", "count": 1, "mrr": 30.0}
        )
        self.assertEqual(
            [stage["value"] for stage in summary["stages"]],
            ["open", "mitigated", "realised", "abandoned"],
        )

    def test_the_summary_is_the_viewers_own(self):
        self.opportunity("Mine", mrr=Decimal("1"))
        self.opportunity("Taco's", customer=self.taco, mrr=Decimal("2"))
        self.opportunity("Sales'", department=User.Function.SALES, mrr=Decimal("4"))
        self.assertEqual(self.summary()["mrr"], 1.0)
        self.assertEqual(self.summary(user=self.admin)["mrr"], 7.0)


class ListingTests(PipelineFixture):
    def test_the_body(self):
        self.opportunity("Upsell")
        params = parse_params(QueryDict("group=stage"), OPPORTUNITIES)
        book = load_book(self.csm, OPPORTUNITIES, params, today=self.today)
        body = build_listing(book, params, filters={"x": []}, today=self.today)
        self.assertEqual(
            set(body),
            {"kind", "results", "next_cursor", "count", "groups", "summary", "filters", "currency"},
        )
        self.assertEqual(
            (body["kind"], body["count"], body["currency"], body["filters"]),
            ("opportunities", 1, "USD", {"x": []}),
        )
        self.assertEqual(body["results"][0]["title"], "Upsell")
        self.assertIsNone(body["next_cursor"])
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `venv/bin/python manage.py test services.pipelines_portfolio.tests.test_summary --noinput`
Expected: `ImportError: cannot import name 'build_listing'`.

- [ ] **Step 3: Add the summary and the listing to `shape.py`**

In `services/pipelines_portfolio/shape.py`, change the import block to:

```python
import calendar
import hashlib
import json

from django.utils import timezone

from services.organizations.book import RENEWING_WINDOWS
from services.organizations.shape import group_rank, keyset_page, wrap_desc

from .book import quarter_bounds
from .rows import department_label, row_payload

#: The "closing / due in 30 / 90 days" tiles: Organizations' renewing windows.
TILE_WINDOWS = RENEWING_WINDOWS
```

Append to the end of the file:

```python
def _total(entries):
    return {"count": len(entries), "mrr": round(sum(entry.mrr for entry in entries), 2)}


def build_summary(book, *, today):
    """The tiles, over every filtered row of every stage: the list's filters
    apply, its stage filter does not, so "Won this quarter" and the stage
    strip survive the default open-only view. Each tile is the rule of the
    filter it sets, so clicking it (with the default stages) lists exactly
    its N:
      open — the kind's open stages (the default `stage`);
      within 30/90 — open, dated today to today+N (`date=30|90`);
      overdue — open, dated before today (`date=overdue`);
      done_this_quarter — Closed Won (risks: Mitigated) whose stage last
        changed this calendar quarter (`stage=<stage>&changed=quarter`);
      stages — every stage, empty ones included (`stage=<value>`): the strip
        and the Board's column headers."""
    kind = book.kind
    entries = book.entries
    start, end = quarter_bounds(today)
    open_entries = [entry for entry in entries if entry.is_open]
    done = [
        entry
        for entry in entries
        if entry.item.stage == kind.done_stage
        and start <= timezone.localdate(entry.item.stage_changed_at) <= end
    ]
    return {
        "items": len(entries),
        "mrr": round(sum(entry.mrr for entry in entries), 2),
        "open": _total(open_entries),
        "within": {
            str(days): _total(
                [e for e in open_entries if e.days is not None and 0 <= e.days <= days]
            )
            for days in TILE_WINDOWS
        },
        "overdue": _total([entry for entry in open_entries if entry.overdue]),
        "done_this_quarter": {"stage": kind.done_stage, **_total(done)},
        "stages": [
            {
                "value": value,
                "label": label,
                **_total([entry for entry in entries if entry.item.stage == value]),
            }
            for value, label in kind.model.Stage.choices
        ],
    }


def build_listing(book, params, *, filters, today):
    """The endpoint's body. `count` is the rows this query pages through
    (after `stage` and `group_value`); `groups` cover the chosen stages
    before `group_value`, so a Board column's header never shrinks to a
    page; `summary` covers every stage of the filtered set."""
    entries, groups = select(book, params)
    page, next_cursor = paginate(entries, params=params, kind=book.kind)
    return {
        "kind": book.kind.key,
        "results": [row_payload(entry, book.kind) for entry in page],
        "next_cursor": next_cursor,
        "count": len(entries),
        "groups": groups,
        "summary": build_summary(book, today=today),
        "filters": filters,
        "currency": book.organisation.currency,
    }
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `venv/bin/python manage.py test services.pipelines_portfolio --noinput`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add services/pipelines_portfolio
git commit -m "$(cat <<'EOF'
feat(pipelines): the tiles over every stage of the filtered set, and the listing

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: `GET /api/v1/pipelines/opportunities/` and `/risks/`

**Files:**
- Create: `services/pipelines_portfolio/views.py`, `urls.py`
- Modify: `config/urls.py` (mount `api/v1/pipelines/`)
- Test: `services/pipelines_portfolio/tests/test_views.py`

**Interfaces:**
- Consumes: `KINDS` (Task 5); `load_book`, `filter_options` (Task 6); `build_listing` (Task 9).
- Produces: `PipelineView` (`get(request, kind_key)`); routes named `pipelines-opportunities`, `pipelines-risks`; URL kwarg `kind_key`. Tasks 11 and 12 add views to the same files.

- [ ] **Step 1: Write the failing tests**

Create `services/pipelines_portfolio/tests/test_views.py`:

```python
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import resolve
from rest_framework.test import APIClient

from services.accounts.models import User
from services.customers.tests.test_views import blind_to_one_account
from services.customers.views import OpportunityListView, RiskListView
from services.pipelines_portfolio.kinds import OPPORTUNITIES
from services.pipelines_portfolio.tests.fixtures import PipelineFixture
from services.pipelines_portfolio.views import PipelineView

URL = "/api/v1/pipelines/{}/"
EVERY_STAGE = ",".join(OPPORTUNITIES.stages)


class PipelineEndpointTests(PipelineFixture):
    def get(self, user=None, kind="opportunities", **query):
        api = APIClient()
        api.force_authenticate(user or self.csm)
        response = api.get(URL.format(kind), query)
        self.assertEqual(response.status_code, 200, response.content)
        return response.data

    def titles(self, body):
        return [row["title"] for row in body["results"]]

    def test_requires_authentication(self):
        for kind in ("opportunities", "risks"):
            self.assertEqual(APIClient().get(URL.format(kind)).status_code, 401)

    def test_routes(self):
        self.assertIs(resolve(URL.format("opportunities")).func.view_class, PipelineView)
        self.assertIs(resolve(URL.format("risks")).func.view_class, PipelineView)
        self.assertIs(resolve("/api/v1/opportunities/").func.view_class, OpportunityListView)
        self.assertIs(resolve("/api/v1/risks/").func.view_class, RiskListView)
        api = APIClient()
        api.force_authenticate(self.csm)
        self.assertEqual(api.get(URL.format("deals")).status_code, 404)

    def test_response_shape(self):
        self.opportunity("Upsell")
        body = self.get()
        self.assertEqual(
            set(body),
            {"kind", "results", "next_cursor", "count", "groups", "summary", "filters", "currency"},
        )
        self.assertEqual(
            set(body["results"][0]),
            {
                "id", "kind", "title", "parent", "companies", "owner", "mrr", "stage",
                "priority", "department", "date", "open", "overdue", "signal",
                "stage_changed_at", "created_at",
            },
        )  # fmt: skip
        self.assertEqual(
            set(body["summary"]),
            {"items", "mrr", "open", "within", "overdue", "done_this_quarter", "stages"},
        )
        self.assertEqual(
            set(body["filters"]),
            {"organisations", "accounts", "owners", "stages", "priorities", "departments"},
        )

    def test_risks_are_their_own_book(self):
        self.opportunity("Upsell")
        self.risk("Budget")
        self.risk("Handled", stage="mitigated")
        body = self.get(kind="risks")
        self.assertEqual((body["kind"], self.titles(body)), ("risks", ["Budget"]))
        self.assertEqual(body["summary"]["items"], 2)

    def test_blind_to_one_account(self):
        viewer, seen, hidden = blind_to_one_account(self.pizza)
        self.opportunity("On seen", account=seen)
        on_hidden = self.opportunity("On hidden", account=hidden, mrr=Decimal("9000"))
        body = self.get(viewer, group="parent")
        self.assertEqual(self.titles(body), ["On seen"])
        self.assertEqual((body["summary"]["items"], body["summary"]["mrr"]), (1, 1000.0))
        self.assertEqual([group["label"] for group in body["groups"]], ["Seen"])
        self.assertEqual([option["name"] for option in body["filters"]["accounts"]], ["Seen"])
        self.assertEqual(self.get(viewer, ids=str(on_hidden.pk))["count"], 0)
        self.assertEqual(self.get(viewer, account=str(hidden.pk))["summary"]["items"], 0)
        self.assertEqual(self.get(self.admin)["summary"]["items"], 2)

    def test_another_tenant_never_appears(self):
        theirs = self.opportunity("Globex deal", customer=self.globex, department="")
        self.assertEqual(self.get(self.admin, ids=str(theirs.pk))["count"], 0)
        self.assertEqual(self.get(self.admin)["summary"]["items"], 0)
        self.assertEqual(self.get(self.admin, organisation=str(self.globex.pk))["count"], 0)

    def test_the_department_rule(self):
        self.opportunity("Sales'", department=User.Function.SALES)
        self.opportunity("Ours")
        self.assertEqual(self.titles(self.get()), ["Ours"])
        self.assertEqual(sorted(self.titles(self.get(self.admin))), ["Ours", "Sales'"])

    def test_an_organisation_filter_the_viewer_cannot_open_names_nothing(self):
        self.opportunity("On Taco Bell", customer=self.taco)
        self.opportunity("Mine")
        body = self.get(organisation=str(self.taco.pk), group="stage")
        self.assertEqual((body["count"], body["summary"]["items"], body["groups"]), (0, 0, []))

    def test_companies_are_trimmed_on_rows(self):
        shared = self.account("Shared", customers=[self.pizza, self.taco], owner=self.other)
        self.opportunity("Seats", account=shared)
        [row] = self.get()["results"]
        self.assertEqual(row["companies"], [{"id": self.pizza.pk, "name": "Pizza Hut"}])

    def test_unknown_values_are_ignored_not_rejected(self):
        self.opportunity("A")
        self.opportunity("B")
        body = self.get(
            sort="colour",
            group="health",
            stage="won",
            priority="urgent",
            department="pirates",
            date="7",
            changed="year",
            limit="lots",
            cursor="%%%",
            owner="someone",
            organisation="x",
            account="y",
        )
        self.assertEqual(body["count"], 2)

    def test_the_board_asks_for_every_stage_one_column_at_a_time(self):
        self.opportunity("N1", stage="negotiation")
        self.opportunity("Won", stage="closed_won")
        body = self.get(group="stage", stage=EVERY_STAGE, group_value="closed_won")
        self.assertEqual(self.titles(body), ["Won"])
        self.assertEqual([group["key"] for group in body["groups"]], ["negotiation", "closed_won"])

    def test_following_the_cursor_reads_every_row_once(self):
        for i in range(5):
            self.opportunity(f"Deal {i}", mrr=Decimal(1000 * (i + 1)))
        seen, cursor = [], None
        for _ in range(10):
            body = self.get(limit="2", **({"cursor": cursor} if cursor else {}))
            seen += self.titles(body)
            cursor = body["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(seen, ["Deal 4", "Deal 3", "Deal 2", "Deal 1", "Deal 0"])


class PipelineQueryCountTests(PipelineFixture):
    """Every figure is computed over the whole filtered set in a fixed number
    of queries; a per-row query anywhere would make the count grow with the
    book. Eight for an admin, who sees everything (no org-chart walk):
      1. `user.organisation` (`book.load_book`)
      2. the caller's active membership, joined to its organisation and role
         (`services.identity.context.active_membership`, memoised)
      3. `user.role` (`services.identity.context.capabilities_for`)
      4. the items, both parents and their owners joined
      5. the openable organisations of the account-level items (skipped when
         the filtered set has none — every book below has some)
      6-8. the filter options: organisations, accounts, owners and departments
    If the pinned number is ever wrong, print `[q["sql"] for q in
    ctx.captured_queries]`: every query must be one of these kinds. Only a
    miscount of the fixed identity lookups (1-3) may change `EXPECTED`; any
    other extra query is a bug to fix, not a number to bump."""

    EXPECTED = 8

    def book(self, size):
        for i in range(size):
            account = self.account(f"Div {size}-{i}", customers=[self.pizza, self.taco])
            self.opportunity(f"Org deal {size}-{i}", expected_close=self.days(i))
            self.opportunity(f"Account deal {size}-{i}", account=account, stage="closed_won")
            self.risk(f"Risk {size}-{i}", account=account)

    def count(self, user, kind="opportunities", **query):
        # A fresh user each time, as a real request loads one: memoised lookups
        # cached on a reused instance would otherwise flatter the second call.
        api = APIClient()
        api.force_authenticate(User.objects.get(pk=user.pk))
        with CaptureQueriesContext(connection) as ctx:
            response = api.get(URL.format(kind), query)
        self.assertEqual(response.status_code, 200)
        return len(ctx.captured_queries)

    def test_the_count_is_pinned_and_does_not_grow_with_the_book(self):
        self.book(3)
        small = self.count(self.admin)
        small_csm = self.count(self.csm)
        small_risks = self.count(self.admin, "risks")
        self.book(12)
        self.assertEqual(self.count(self.admin), small)
        self.assertEqual(self.count(self.csm), small_csm)
        self.assertEqual(self.count(self.admin, "risks"), small_risks)
        self.assertEqual(small, self.EXPECTED)

    def test_sorting_grouping_filtering_and_paging_add_no_queries(self):
        self.book(5)
        plain = self.count(self.admin)
        self.assertEqual(self.count(self.admin, sort="-date", group="month", limit="2"), plain)
        self.assertEqual(
            self.count(self.admin, group="stage", stage=EVERY_STAGE, group_value="closed_won"),
            plain,
        )
        self.assertEqual(
            self.count(
                self.admin,
                search="Account",
                organisation=str(self.pizza.pk),
                owner=str(self.csm.pk),
                priority="medium",
                department="cs,none",
                changed="quarter",
            ),
            plain,
        )
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `venv/bin/python manage.py test services.pipelines_portfolio.tests.test_views --noinput`
Expected: `ImportError: cannot import name 'PipelineView'` (or `ModuleNotFoundError` for `views`).

- [ ] **Step 3: Write the view, the URLs and the mount**

Create `services/pipelines_portfolio/views.py`:

```python
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .book import filter_options, load_book
from .kinds import KINDS
from .params import parse_params
from .shape import build_listing


class PipelineView(APIView):
    """GET /api/v1/pipelines/opportunities/ and /api/v1/pipelines/risks/ —
    the Pipelines list and Board: every opportunity or risk the viewer may
    read (twice filtered, `book.scope`), filtered, sorted, optionally
    grouped and paged by an opaque cursor, with the tiles over every stage
    of the filtered set and the group totals. Unknown parameter values are
    ignored. See docs/API_CONTRACTS.md."""

    # SOC2:AUTH-02 authentication only; `load_book`/`filter_options`
    # (services/pipelines_portfolio/book.py) do the record-visibility checks
    # through `visible_children_q`, `pipeline_visible_q`, `visible_customers`
    # and `visible_accounts`.
    permission_classes = [IsAuthenticated]

    def get(self, request, kind_key):
        kind = KINDS[kind_key]
        params = parse_params(request.query_params, kind)
        today = timezone.localdate()
        book = load_book(request.user, kind, params, today=today)
        filters = filter_options(request.user, kind)
        return Response(build_listing(book, params, filters=filters, today=today))
```

Create `services/pipelines_portfolio/urls.py`:

```python
"""The Pipelines book's routes, one set per kind. The kind comes from the
route, never from the client, so a view can index `KINDS` directly."""

from django.urls import path

from . import views
from .kinds import KINDS

urlpatterns = [
    path(f"{key}/", views.PipelineView.as_view(), {"kind_key": key}, name=f"pipelines-{key}")
    for key in KINDS
]
```

In `config/urls.py`, directly after the `path("api/v1/risks/", RiskListView.as_view(), name="risk-list"),` line, add:

```python
# The Pipelines page's own book (list, Board, tiles, export, bulk) for
# both kinds — services/pipelines_portfolio. /opportunities/ and /risks/
# above stay for the Deals & risks tabs and the forms.
(path("api/v1/pipelines/", include("services.pipelines_portfolio.urls")),)
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `venv/bin/python manage.py test services.pipelines_portfolio --noinput`
Expected: `OK`. If `test_the_count_is_pinned…` fails on the number, follow the class docstring before touching `EXPECTED`.

- [ ] **Step 5: Commit**

```bash
git add config/urls.py services/pipelines_portfolio
git commit -m "$(cat <<'EOF'
feat(pipelines): GET /api/v1/pipelines/opportunities/ and /risks/

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: The CSV export, audited

**Files:**
- Create: `services/pipelines_portfolio/export.py`
- Modify: `services/pipelines_portfolio/views.py` (add `PipelineExportView`), `urls.py`
- Test: `services/pipelines_portfolio/tests/test_export.py`

**Interfaces:**
- Consumes: `fields_for` (Task 7); `select` (Task 8); `row_payload` (Task 7); `cell`, `CSVRenderer` from `services.organizations.export`.
- Produces: `table(rows, *, kind, currency) -> list[list]`; `PipelineExportView`; routes `pipelines-<kind>-export`; audit action `pipelines.exported`.

- [ ] **Step 1: Write the failing tests**

Create `services/pipelines_portfolio/tests/test_export.py`:

```python
import csv
import io
from decimal import Decimal

from rest_framework.test import APIClient

from core.models import AuditEvent
from services.accounts.models import User
from services.customers.tests.test_views import blind_to_one_account
from services.pipelines_portfolio.fields import fields_for
from services.pipelines_portfolio.kinds import OPPORTUNITIES
from services.pipelines_portfolio.tests.fixtures import PipelineFixture

URL = "/api/v1/pipelines/{}/export.csv"


class ExportEndpointTests(PipelineFixture):
    def download(self, user=None, kind="opportunities", **query):
        api = APIClient()
        api.force_authenticate(user or self.csm)
        response = api.get(URL.format(kind), query)
        return response, list(csv.reader(io.StringIO(response.content.decode())))

    def test_requires_authentication(self):
        response = APIClient().get(URL.format("risks"))
        self.assertEqual(response.status_code, 401)
        self.assertTrue(response["Content-Type"].startswith("text/csv"))

    def test_every_field_every_row_no_pagination(self):
        for i in range(60):
            self.opportunity(f"Deal {i:02d}", mrr=Decimal(i))
        response, table = self.download()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/csv"))
        self.assertIn(
            f'filename="opportunities-{self.today.isoformat()}.csv"',
            response["Content-Disposition"],
        )
        self.assertIn("attachment;", response["Content-Disposition"])
        header, rows = table[0], table[1:]
        self.assertEqual(
            header, [field.label for field in fields_for(OPPORTUNITIES)] + ["Currency"]
        )
        self.assertEqual(len(header), 13)
        self.assertEqual(len(rows), 60)
        self.assertEqual((rows[0][0], rows[0][-1]), ("Deal 59", "USD"))

    def test_risks_carry_their_due_by(self):
        self.risk("Budget", due_by=self.days(3))
        _response, table = self.download(kind="risks")
        column = table[0].index("Due By")
        self.assertEqual(table[1][column], self.days(3).isoformat())

    def test_formula_cells_are_neutralised(self):
        self.opportunity('=HYPERLINK("http://evil")')
        _response, table = self.download()
        self.assertEqual(table[1][0], '\'=HYPERLINK("http://evil")')

    def test_the_export_is_the_viewers_own(self):
        viewer, seen, hidden = blind_to_one_account(self.pizza)
        self.opportunity("On seen", account=seen)
        self.opportunity("On hidden", account=hidden)
        self.opportunity("Sales'", department=User.Function.SALES)
        _response, table = self.download(viewer)
        self.assertEqual([row[0] for row in table[1:]], ["On seen"])

    def test_the_export_is_audited_without_the_search_term(self):
        self.opportunity("Upsell")
        self.download(search="secret words", stage=",".join(OPPORTUNITIES.stages))
        event = AuditEvent.objects.get(action="pipelines.exported")
        self.assertEqual(event.actor, self.csm)
        self.assertEqual(
            event.metadata, {"kind": "opportunities", "count": 0, "params": ["search", "stage"]}
        )
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `venv/bin/python manage.py test services.pipelines_portfolio.tests.test_export --noinput`
Expected: failures — the route does not exist yet, so every download is a 404 (no `pipelines.exported` event, no CSV header).

- [ ] **Step 3: Write the export and its view**

Create `services/pipelines_portfolio/export.py`:

```python
"""The Pipelines book as a CSV: every row the list would page through (the
chosen stages, every page), every field (`fields.fields_for`), and the
currency MRR is in. Organizations' cell rule and renderer: a text cell that
starts with `=`, `+`, `-`, `@`, a tab or a carriage return is prefixed with
`'`, so a deal titled `=HYPERLINK(...)` is data, not an instruction."""

from services.organizations.export import cell

from .fields import fields_for


def table(rows, *, kind, currency):
    fields = fields_for(kind)
    header = [field.label for field in fields] + ["Currency"]
    body = [[cell(field.value(row)) for field in fields] + [currency] for row in rows]
    return [header, *body]
```

In `services/pipelines_portfolio/views.py`, change the imports to:

```python
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core import audit
from services.organizations.export import CSVRenderer

from .book import filter_options, load_book
from .export import table
from .kinds import KINDS
from .params import parse_params
from .rows import row_payload
from .shape import build_listing, select
```

and append:

```python
class PipelineExportView(APIView):
    """GET /api/v1/pipelines/<kind>/export.csv — the same query as the list,
    every row (no pagination; the chosen stages and `group_value` apply), in
    list order, with every field. Audited: this is confidential data leaving
    the app."""

    # SOC2:AUTH-02 authentication only; `load_book` (book.py) does the
    # record-visibility checks — the export is the same twice-filtered set
    # the list endpoint returns.
    permission_classes = [IsAuthenticated]
    renderer_classes = [CSVRenderer]

    def get(self, request, kind_key):
        kind = KINDS[kind_key]
        params = parse_params(request.query_params, kind)
        today = timezone.localdate()
        book = load_book(request.user, kind, params, today=today)
        entries, _groups = select(book, params)
        audit.record(  # SOC2:LOG-01
            "pipelines.exported",
            request=request,
            # Parameter names only: a search term is the user's own words.
            metadata={
                "kind": kind.key,
                "count": len(entries),
                "params": sorted(request.query_params.keys()),
            },
        )
        rows = [row_payload(entry, kind) for entry in entries]
        response = Response(table(rows, kind=kind, currency=book.organisation.currency))
        response["Content-Disposition"] = (
            f'attachment; filename="{kind.key}-{today.isoformat()}.csv"'
        )
        return response
```

Replace `urlpatterns` in `services/pipelines_portfolio/urls.py` with:

```python
urlpatterns = [
    route
    for key in KINDS
    for route in (
        path(
            f"{key}/export.csv",
            views.PipelineExportView.as_view(),
            {"kind_key": key},
            name=f"pipelines-{key}-export",
        ),
        path(f"{key}/", views.PipelineView.as_view(), {"kind_key": key}, name=f"pipelines-{key}"),
    )
]
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `venv/bin/python manage.py test services.pipelines_portfolio --noinput`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add services/pipelines_portfolio
git commit -m "$(cat <<'EOF'
feat(pipelines): CSV export of the filtered book, audited

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: `POST /api/v1/pipelines/{kind}/bulk/`

**Files:**
- Create: `services/pipelines_portfolio/serializers.py`, `bulk.py`
- Modify: `services/pipelines_portfolio/views.py` (add `PipelineBulkUpdateView`), `urls.py`
- Test: `services/pipelines_portfolio/tests/test_bulk.py`

**Interfaces:**
- Consumes: `scope` (Task 6); `kind.serializer` (Task 3's serializers, so the stage clock moves on save); `NOT_FOUND`, `NOT_UPDATED`, `first_reason` from `services.organizations.bulk`.
- Produces: `ACTIONS`, `DATE_VALUE`, `BulkRequestSerializer` (needs `context={"kind": kind}`); `bulk.field_for(kind, action) -> str`; `bulk.apply(request, kind, *, ids, action, value, result=None) -> {"updated": [...], "failed": [{"id", "reason"}]}`; `PipelineBulkUpdateView`; routes `pipelines-<kind>-bulk`; audit action `pipelines.bulk_updated`.

- [ ] **Step 1: Write the failing tests**

Create `services/pipelines_portfolio/tests/test_bulk.py`:

```python
from datetime import timedelta
from unittest.mock import patch

from django.db import DatabaseError
from django.test import SimpleTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import AuditEvent
from services.accounts.models import User
from services.customers.models import Opportunity
from services.customers.tests.test_views import blind_to_one_account
from services.pipelines_portfolio import bulk as bulk_module
from services.pipelines_portfolio.kinds import OPPORTUNITIES, RISKS
from services.pipelines_portfolio.serializers import BulkRequestSerializer
from services.pipelines_portfolio.tests.fixtures import PipelineFixture

URL = "/api/v1/pipelines/{}/bulk/"
MISSING = 999_999


class BulkRequestSerializerTests(SimpleTestCase):
    def validated(self, body, kind=OPPORTUNITIES):
        serializer = BulkRequestSerializer(data=body, context={"kind": kind})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        return serializer.validated_data

    def errors(self, body, kind=OPPORTUNITIES):
        serializer = BulkRequestSerializer(data=body, context={"kind": kind})
        self.assertFalse(serializer.is_valid())
        return serializer.errors

    def test_ids_are_deduplicated_in_order(self):
        data = self.validated({"ids": [3, 1, 3, 2], "action": "set_priority", "value": "high"})
        self.assertEqual(data["ids"], [3, 1, 2])

    def test_a_stage_is_the_kinds_own(self):
        body = {"ids": [1], "action": "set_stage"}
        self.assertEqual(self.validated({**body, "value": "closed_lost"})["value"], "closed_lost")
        self.assertIn("value", self.errors({**body, "value": "mitigated"}))
        self.assertEqual(
            self.validated({**body, "value": "mitigated"}, RISKS)["value"], "mitigated"
        )
        self.assertIn("value", self.errors({**body, "value": ["open"]}, RISKS))

    def test_priority_and_department(self):
        self.validated({"ids": [1], "action": "set_priority", "value": "high"})
        self.assertIn(
            "value", self.errors({"ids": [1], "action": "set_priority", "value": "urgent"})
        )
        for department in ("", "sales"):
            self.validated({"ids": [1], "action": "set_department", "value": department})
        for bad in ("pirates", None):
            self.assertIn(
                "value", self.errors({"ids": [1], "action": "set_department", "value": bad})
            )

    def test_a_date_is_iso_or_an_explicit_null(self):
        body = {"ids": [1], "action": "set_date"}
        self.assertEqual(self.validated({**body, "value": "2026-12-01"})["value"], "2026-12-01")
        self.assertIsNone(self.validated({**body, "value": None})["value"])
        for bad in ("soon", 20261201):
            self.assertIn("value", self.errors({**body, "value": bad}))
        # A forgotten key must not clear every selected date.
        self.assertIn("value", self.errors(body))

    def test_there_is_no_owner_or_delete_action(self):
        for action in ("set_owner", "delete"):
            self.assertIn("action", self.errors({"ids": [1], "action": action, "value": None}))


class BulkEndpointTests(PipelineFixture):
    def post(self, body, user=None, kind="opportunities"):
        api = APIClient()
        api.force_authenticate(user or self.csm)
        return api.post(URL.format(kind), body, format="json")

    def test_set_stage_moves_each_item_and_its_clock(self):
        first, second = self.opportunity("A"), self.opportunity("B")
        long_ago = timezone.now() - timedelta(days=300)
        Opportunity.objects.filter(pk__in=[first.pk, second.pk]).update(stage_changed_at=long_ago)
        response = self.post(
            {"ids": [first.pk, second.pk], "action": "set_stage", "value": "closed_lost"}
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, {"updated": [first.pk, second.pk], "failed": []})
        for item in Opportunity.objects.filter(pk__in=[first.pk, second.pk]):
            self.assertEqual(item.stage, "closed_lost")
            self.assertGreater(item.stage_changed_at, long_ago)

    def test_set_date_sets_and_clears_the_kinds_date(self):
        deal = self.opportunity("A")
        self.post({"ids": [deal.pk], "action": "set_date", "value": "2026-12-01"})
        deal.refresh_from_db()
        self.assertEqual(deal.expected_close.isoformat(), "2026-12-01")
        self.post({"ids": [deal.pk], "action": "set_date", "value": None})
        deal.refresh_from_db()
        self.assertIsNone(deal.expected_close)
        risk = self.risk("R")
        self.post({"ids": [risk.pk], "action": "set_date", "value": "2026-11-15"}, kind="risks")
        risk.refresh_from_db()
        self.assertEqual(risk.due_by.isoformat(), "2026-11-15")

    def test_priority_and_department(self):
        deal = self.opportunity("A")
        response = self.post({"ids": [deal.pk], "action": "set_priority", "value": "high"})
        self.assertEqual(response.data["updated"], [deal.pk])
        response = self.post({"ids": [deal.pk], "action": "set_department", "value": ""})
        self.assertEqual(response.data["updated"], [deal.pk])
        deal.refresh_from_db()
        self.assertEqual((deal.priority, deal.department), ("high", ""))

    def test_an_item_the_editor_cannot_read_is_not_found(self):
        viewer, seen, hidden = blind_to_one_account(self.pizza)
        mine = self.opportunity("On seen", account=seen)
        on_hidden = self.opportunity("On hidden", account=hidden)
        sales = self.opportunity("Sales'", department=User.Function.SALES)
        globex = self.opportunity("Globex's", customer=self.globex, department="")
        ids = [mine.pk, on_hidden.pk, sales.pk, globex.pk, MISSING]
        response = self.post({"ids": ids, "action": "set_priority", "value": "low"}, user=viewer)
        self.assertEqual(response.data["updated"], [mine.pk])
        self.assertEqual(
            response.data["failed"],
            [{"id": pk, "reason": "Not found."} for pk in ids[1:]],
        )
        for item in (on_hidden, sales, globex):
            item.refresh_from_db()
            self.assertEqual(item.priority, "medium")

    def test_a_risk_is_not_found_on_the_opportunities_route(self):
        risk = self.risk("R")
        self.assertFalse(Opportunity.objects.exists())
        response = self.post({"ids": [risk.pk], "action": "set_priority", "value": "low"})
        self.assertEqual(response.data["failed"], [{"id": risk.pk, "reason": "Not found."}])

    def test_a_database_error_on_one_id_does_not_stop_the_rest(self):
        first, second = self.opportunity("A"), self.opportunity("B")
        real = bulk_module._apply_one

        def flaky(request, kind, item_id, field, value):
            if item_id == first.pk:
                raise DatabaseError("boom")
            return real(request, kind, item_id, field, value)

        with patch.object(bulk_module, "_apply_one", side_effect=flaky):
            response = self.post(
                {"ids": [first.pk, second.pk], "action": "set_priority", "value": "high"}
            )
        self.assertEqual(
            response.data,
            {
                "updated": [second.pk],
                "failed": [{"id": first.pk, "reason": "Could not be updated."}],
            },
        )

    def test_the_batch_is_audited_once_without_values(self):
        deal = self.opportunity("A")
        self.post({"ids": [deal.pk, MISSING], "action": "set_date", "value": "2026-12-01"})
        [event] = AuditEvent.objects.filter(action="pipelines.bulk_updated")
        self.assertEqual(event.outcome, AuditEvent.Outcome.SUCCESS)
        self.assertEqual(
            event.metadata,
            {
                "kind": "opportunities",
                "action": "set_date",
                "field": "expected_close",
                "ids": [deal.pk],
                "failed_ids": [MISSING],
            },
        )
        self.assertFalse(AuditEvent.objects.filter(action="opportunity.updated").exists())

    def test_a_batch_that_updates_nothing_is_a_failure(self):
        self.post({"ids": [MISSING], "action": "set_priority", "value": "high"})
        event = AuditEvent.objects.get(action="pipelines.bulk_updated")
        self.assertEqual(event.outcome, AuditEvent.Outcome.FAILURE)

    def test_bad_requests(self):
        self.assertEqual(
            self.post({"ids": [], "action": "set_stage", "value": "negotiation"}).status_code, 400
        )
        self.assertEqual(
            self.post({"ids": [1], "action": "set_stage", "value": "open"}).status_code, 400
        )
        self.assertEqual(APIClient().post(URL.format("risks"), {}, format="json").status_code, 401)
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `venv/bin/python manage.py test services.pipelines_portfolio.tests.test_bulk --noinput`
Expected: `ModuleNotFoundError: No module named 'services.pipelines_portfolio.bulk'`.

- [ ] **Step 3: Write the serializer, the bulk module and the view**

Create `services/pipelines_portfolio/serializers.py`:

```python
from datetime import date

from rest_framework import serializers

from services.accounts.models import User
from services.organizations.params import MAX_IDS

#: Stage, priority, department and date: the spec's four bulk edits.
ACTIONS = ("set_stage", "set_priority", "set_department", "set_date")
DATE_VALUE = "A date is YYYY-MM-DD, or null to clear it."


class BulkRequestSerializer(serializers.Serializer):
    """`context["kind"]` is the route's kind: a stage is checked against that
    kind's own stages (Closed Lost is an opportunity's, Mitigated a risk's).
    Each id is then saved through the kind's single-edit serializer, which
    checks the value again."""

    ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), min_length=1, max_length=MAX_IDS
    )
    action = serializers.ChoiceField(choices=ACTIONS)
    value = serializers.JSONField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        kind = self.context["kind"]
        action, value = attrs["action"], attrs.get("value")
        allowed = {
            "set_stage": (kind.stages, "Not a stage."),
            "set_priority": (tuple(kind.model.Priority.values), "Not a priority."),
            # Blank is "everyone's", as on the single edit.
            "set_department": (("", *User.Function.values), "Not a department."),
        }
        if action in allowed:
            values, message = allowed[action]
            if not isinstance(value, str) or value not in values:
                raise serializers.ValidationError({"value": message})
        if action == "set_date":
            # Clearing is `null`, said out loud: a forgotten key must not
            # strip the date from every selected item.
            if "value" not in self.initial_data:
                raise serializers.ValidationError({"value": DATE_VALUE})
            if value is not None:
                try:
                    date.fromisoformat(value)
                except (TypeError, ValueError):
                    raise serializers.ValidationError({"value": DATE_VALUE}) from None
        attrs["ids"] = list(dict.fromkeys(attrs["ids"]))
        return attrs
```

Create `services/pipelines_portfolio/bulk.py`:

```python
"""Bulk edits from the Pipelines selection bar — set stage, priority,
department or date — applied one item at a time with exactly the rules the
single PATCH follows: the item is read through the editor's own twice
filter (`book.scope`, the detail endpoints' queryset), and the kind's own
serializer validates and saves the change, so a stage change moves the stage
clock just as a Board drag does.

Each id is its own transaction: the row is re-read under a lock, so the
check judges the item as it is now. One failure never stops the rest (a
database error included), and each is reported by id. An id the editor
cannot read reads "Not found." — the same answer as one that does not
exist, so the endpoint cannot be used to discover records. The batch is
audited once by the view (`pipelines.bulk_updated`); its items are not also
audited one by one."""

import logging

from django.db import DatabaseError, transaction

from services.organizations.bulk import NOT_FOUND, NOT_UPDATED, first_reason

from .book import scope

logger = logging.getLogger(__name__)


def field_for(kind, action):
    """Each action is one writable field of the ordinary single edit."""
    return {
        "set_stage": "stage",
        "set_priority": "priority",
        "set_department": "department",
        "set_date": kind.date_field,
    }[action]


def _locked(user, kind, item_id):
    """The row as it is now, locked until this id's transaction ends, and
    only if the editor can still read it."""
    # SOC2:AUTH-02 each item is re-read through the editor's own twice
    # filter, exactly as the single PATCH reads it
    visible = scope(user, kind).filter(pk=item_id).values("pk")
    return kind.model.objects.select_for_update(of=("self",)).filter(pk__in=visible).first()


def _apply_one(request, kind, item_id, field, value):
    """Returns None when the id was updated, else the reason it was not."""
    with transaction.atomic():
        item = _locked(request.user, kind, item_id)
        if item is None:
            return NOT_FOUND
        serializer = kind.serializer(
            item, data={field: value}, partial=True, context={"request": request}
        )
        if not serializer.is_valid():
            return first_reason(serializer.errors)
        serializer.save()
    return None


def apply(request, kind, *, ids, action, value, result=None):
    """`result`, when given, is filled in place as each id finishes, so a
    caller still knows what was done if something unexpected escapes."""
    field = field_for(kind, action)
    result = result if result is not None else {"updated": [], "failed": []}
    for item_id in ids:
        try:
            reason = _apply_one(request, kind, item_id, field, value)
        except DatabaseError as exc:
            # The id and the error's class only: the message can quote row data.
            logger.warning(
                "Pipelines bulk: %s %s not updated (%s)", kind.item, item_id, type(exc).__name__
            )
            reason = NOT_UPDATED
        if reason is None:
            result["updated"].append(item_id)
        else:
            result["failed"].append({"id": item_id, "reason": reason})
    return result
```

In `services/pipelines_portfolio/views.py`, add to the imports:

```python
from core.models import AuditEvent

from . import bulk
from .serializers import BulkRequestSerializer
```

(keep isort order: `from core import audit`, `from core.models import AuditEvent`, `from services.organizations.export import CSVRenderer`, then `from . import bulk`, `from .book import …`, …, `from .serializers import BulkRequestSerializer`, `from .shape import …`), and append:

```python
class PipelineBulkUpdateView(APIView):
    """POST /api/v1/pipelines/<kind>/bulk/ — `{ids, action, value}` from the
    selection bar: `set_stage`, `set_priority`, `set_department` or
    `set_date`. Applied per id under the single-edit rules; returns
    `{updated, failed: [{id, reason}]}` with a 200 even when some failed."""

    # SOC2:AUTH-02 authentication only; the per-id visibility checks are in
    # `bulk.apply` (services/pipelines_portfolio/bulk.py).
    permission_classes = [IsAuthenticated]

    def post(self, request, kind_key):
        kind = KINDS[kind_key]
        serializer = BulkRequestSerializer(data=request.data, context={"kind": kind})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = {"updated": [], "failed": []}
        finished = False
        try:
            bulk.apply(
                request,
                kind,
                ids=data["ids"],
                action=data["action"],
                value=data["value"],
                result=result,
            )
            finished = True
        finally:
            # Recorded even if something escaped mid-batch: the ids already
            # saved stay saved (each is its own transaction). Ids and the
            # field only — never the value (spec §2).
            audit.record(  # SOC2:LOG-01
                "pipelines.bulk_updated",
                request=request,
                outcome=(
                    AuditEvent.Outcome.SUCCESS
                    if finished and result["updated"]
                    else AuditEvent.Outcome.FAILURE
                ),
                metadata={
                    "kind": kind.key,
                    "action": data["action"],
                    "field": bulk.field_for(kind, data["action"]),
                    "ids": result["updated"],
                    "failed_ids": [row["id"] for row in result["failed"]],
                },
            )
        return Response(result)
```

Replace `urlpatterns` in `services/pipelines_portfolio/urls.py` with the final set:

```python
urlpatterns = [
    route
    for key in KINDS
    for route in (
        path(
            f"{key}/export.csv",
            views.PipelineExportView.as_view(),
            {"kind_key": key},
            name=f"pipelines-{key}-export",
        ),
        path(
            f"{key}/bulk/",
            views.PipelineBulkUpdateView.as_view(),
            {"kind_key": key},
            name=f"pipelines-{key}-bulk",
        ),
        path(f"{key}/", views.PipelineView.as_view(), {"kind_key": key}, name=f"pipelines-{key}"),
    )
]
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `venv/bin/python manage.py test services.pipelines_portfolio --noinput`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add services/pipelines_portfolio
git commit -m "$(cat <<'EOF'
feat(pipelines): bulk set stage, priority, department or date, one audited batch

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: End-to-end flow

**Files:**
- Create: `e2e/test_pipelines_flow.py`

**Interfaces:**
- Consumes: every endpoint above, over real HTTP; `e2e.http.http_get`, `http_post`, `http_patch`.
- Produces: nothing new.

- [ ] **Step 1: Write the test**

Create `e2e/test_pipelines_flow.py`:

```python
"""End-to-end tier: real server, real HTTP, real test DB. A CSM adds deals
and a risk with dates on their organisation and account, reads their
Pipelines book (never the admin's deal, not even by id), groups it by close
month, wins a deal by dragging it (the won-this-quarter tile counts it),
dates the rest in bulk (the admin's id fails), and exports what they see."""

import csv
import io
import urllib.request
from datetime import timedelta

from django.test import LiveServerTestCase
from django.utils import timezone

from e2e.http import http_get, http_patch, http_post

EVERY_STAGE = (
    "discovery,qualification,solution_validation,proposal_price_review,"
    "negotiation,closed_won,closed_lost"
)


def http_get_text(url, token):
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request) as response:
        return response.status, response.headers.get("Content-Type"), response.read().decode()


class PipelinesFlowTests(LiveServerTestCase):
    def api(self, path):
        return f"{self.live_server_url}/api/v1{path}"

    def test_full_flow(self):
        today = timezone.localdate()

        def day(n):
            return (today + timedelta(days=n)).isoformat()

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

        # 2. The admin's organisation and deal — undeparted, so only the
        #    parent rule keeps it from Carl.
        status, body = http_post(self.api("/customers/"), {"name": "Admin Co"}, token=admin)
        self.assertEqual(status, 201, body)
        status, body = http_post(
            self.api("/opportunities/"),
            {"customer_id": body["id"], "title": "Admin deal", "mrr": "9000", "department": ""},
            token=admin,
        )
        self.assertEqual(status, 201, body)
        admin_deal = body["id"]

        # 3. Carl's organisation and account, three deals and a risk.
        status, body = http_post(self.api("/customers/"), {"name": "Pizza Hut"}, token=csm)
        self.assertEqual(status, 201, body)
        pizza = body["id"]
        status, body = http_post(
            self.api(f"/customers/{pizza}/accounts/"), {"name": "Pizza EMEA"}, token=csm
        )
        self.assertEqual(status, 201, body)
        emea = body["id"]
        deals = {}
        for payload in (
            {"customer_id": pizza, "title": "Upsell", "mrr": "3000", "expected_close": day(-5)},
            {
                "account_id": emea,
                "title": "EMEA seats",
                "mrr": "2000",
                "expected_close": day(7),
                "priority": "high",
            },
            {"customer_id": pizza, "title": "Someday", "mrr": "1000"},
        ):
            status, body = http_post(self.api("/opportunities/"), payload, token=csm)
            self.assertEqual(status, 201, body)
            deals[body["title"]] = body["id"]
        status, body = http_post(
            self.api("/risks/"),
            {"customer_id": pizza, "title": "Budget cut", "mrr": "500", "due_by": day(20)},
            token=csm,
        )
        self.assertEqual((status, body["due_by"]), (201, day(20)))

        # 4. Carl's book: his three, largest MRR first; the tiles; the admin's
        #    deal is absent even when named.
        status, body = http_get(self.api("/pipelines/opportunities/"), token=csm)
        self.assertEqual(status, 200, body)
        self.assertEqual(
            [row["title"] for row in body["results"]], ["Upsell", "EMEA seats", "Someday"]
        )
        self.assertEqual(body["results"][0]["signal"], {"kind": "overdue", "label": "Overdue"})
        self.assertEqual(
            body["results"][1]["parent"], {"type": "account", "id": emea, "name": "Pizza EMEA"}
        )
        self.assertEqual(body["results"][1]["companies"], [{"id": pizza, "name": "Pizza Hut"}])
        self.assertEqual(body["summary"]["open"], {"count": 3, "mrr": 6000.0})
        self.assertEqual(body["summary"]["overdue"], {"count": 1, "mrr": 3000.0})
        self.assertEqual(body["summary"]["within"]["30"], {"count": 1, "mrr": 2000.0})
        status, body = http_get(self.api(f"/pipelines/opportunities/?ids={admin_deal}"), token=csm)
        self.assertEqual((status, body["count"]), (200, 0))
        status, body = http_get(self.api("/pipelines/risks/"), token=csm)
        self.assertEqual([row["title"] for row in body["results"]], ["Budget cut"])

        # 5. Grouped by close month: Overdue first, No date last.
        status, body = http_get(self.api("/pipelines/opportunities/?group=month"), token=csm)
        keys = [group["key"] for group in body["groups"]]
        self.assertEqual((keys[0], keys[-1]), ("overdue", "none"))

        # 6. Drag Upsell to Closed Won: it leaves the open list and the won
        #    tile counts it.
        status, body = http_patch(
            self.api(f"/opportunities/{deals['Upsell']}/"), {"stage": "closed_won"}, token=csm
        )
        self.assertEqual(status, 200, body)
        status, body = http_get(self.api("/pipelines/opportunities/"), token=csm)
        self.assertEqual(body["count"], 2)
        self.assertEqual(
            body["summary"]["done_this_quarter"],
            {"stage": "closed_won", "count": 1, "mrr": 3000.0},
        )
        self.assertEqual(body["summary"]["overdue"]["count"], 0)

        # 7. Bulk date: Carl's two are dated, the admin's reports Not found.
        status, body = http_post(
            self.api("/pipelines/opportunities/bulk/"),
            {
                "ids": [deals["Someday"], deals["EMEA seats"], admin_deal],
                "action": "set_date",
                "value": day(45),
            },
            token=csm,
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["updated"], [deals["Someday"], deals["EMEA seats"]])
        self.assertEqual(body["failed"], [{"id": admin_deal, "reason": "Not found."}])
        status, body = http_get(self.api("/pipelines/opportunities/?date=90"), token=csm)
        self.assertEqual(sorted(row["title"] for row in body["results"]), ["EMEA seats", "Someday"])

        # 8. Export: every field for what Carl sees, closed stages opted in.
        status, content_type, text = http_get_text(
            self.api(f"/pipelines/opportunities/export.csv?stage={EVERY_STAGE}"), csm
        )
        self.assertEqual(status, 200)
        self.assertTrue(content_type.startswith("text/csv"))
        table = list(csv.reader(io.StringIO(text)))
        self.assertEqual(len(table[0]), 13)
        self.assertEqual((table[0][0], table[0][-1]), ("Title", "Currency"))
        self.assertEqual(sorted(row[0] for row in table[1:]), ["EMEA seats", "Someday", "Upsell"])
```

- [ ] **Step 2: Run it**

Run: `venv/bin/python manage.py test e2e.test_pipelines_flow --noinput`
Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add e2e/test_pipelines_flow.py
git commit -m "$(cat <<'EOF'
test(pipelines): end-to-end flow — dates, book, drag, bulk, export

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: Documentation

**Files:**
- Modify: `docs/API_CONTRACTS.md`, `docs/audit-events.md`, `docs/data-classification.md`, `docs/product/01-prd.md`, `docs/product/02-trd.md`, `docs/product/05-backend-schema.md`

**Interfaces:**
- Consumes: the shapes from Tasks 1–12, exactly as the tests pin them.
- Produces: docs only.

- [ ] **Step 1: `docs/API_CONTRACTS.md`, the status row**

Replace the Status table row that begins `| Pipelines (standalone board — "Opportunities" and "Risks" tabs)` with:

```text
| Pipelines (`/pipelines` list and Board — opportunities and risks) | `customers` (`Opportunity`, `Risk` models), `pipelines_portfolio` | ✅ Built — the page's book is `GET /pipelines/opportunities/` and `GET /pipelines/risks/` (rows, groups, tiles, filters, cursor pages, `group_value` for a Board column), `…/export.csv` and `POST /pipelines/{opportunities,risks}/bulk/` (stage, priority, department, date). The per-item endpoints stay for the Deals & risks tabs and the forms — global unpaginated lists (`OpportunityListView`/`RiskListView`), per-Customer and per-Account list-create endpoints, flat detail views (GET/PATCH/DELETE) — and now read and write `expected_close`/`due_by`, return `stage_changed_at`, name only openable organisations in `companies`, and are audited |
```

- [ ] **Step 2: `docs/API_CONTRACTS.md`, the Pipelines book section**

Insert this section directly above the line `## \`account_story\` — the account page (\`/accounts/:id\`)`:

````text
## `pipelines_portfolio` — Pipelines (`/pipelines`)

Built for the redesigned list and Board (spec: react-ts-app `docs/superpowers/specs/2026-09-30-pipelines-redesign-design.md`
§1–§2). One book of opportunities or risks across organisations **and** accounts. No model:
`services/pipelines_portfolio/book.py` loads the viewer's items and `shape.py` orders, groups and totals them in
Python, with Organizations' keyset cursor. `/opportunities/`, `/risks/` and their nested routes are unchanged apart
from the new fields.

### `GET /api/v1/pipelines/opportunities/` and `GET /api/v1/pipelines/risks/`

Auth: `IsAuthenticated`. Scope (`book.scope`, the twice-filter, applied once): an item exists only if the viewer may
open its organisation or account (`visible_children_q`) **and** may read it by department (`pipeline_visible_q`) —
the detail endpoints' own rule. Every filter narrows that scope. Unknown parameter values are ignored, never a 400.

| Param | Meaning |
|---|---|
| `search` | title, or the item's organisation or account name, case-insensitive contains |
| `organisation` | comma list of organisation ids: items on it, plus items on its accounts the viewer may open. An id the viewer cannot open matches nothing |
| `account` | comma list of account ids: items on it. An id the viewer cannot open matches nothing |
| `owner` | user id or `unassigned`: the owner of the item's organisation (organisation-level) or account (account-level). There is no owner on the item |
| `stage` | comma list of the kind's stages. **Default: the open stages** (opportunities: not `closed_won`/`closed_lost`; risks: `open`). Closed stages are opt-in; the Board sends every stage. With `ids` and no `stage`, every stage |
| `priority` | comma list of `high,medium,low` |
| `department` | comma list of `User.Function` values; `none` = undeparted |
| `date` | `30`, `90`, `180` (dated today to today+N, inclusive), `overdue` (open and dated before today) or `none` (no date). Opportunities read `expected_close`, risks `due_by` |
| `changed` | `quarter`: the stage last changed this calendar quarter (`stage_changed_at`) |
| `ids` | comma list, first 500 read; present with no usable id → no rows |
| `sort` | `mrr`, `date`, `priority` (`-priority` = High first), `stage` (board order), `title`; `-` prefix descending; default `-mrr`. Missing values sort last either way; ties by title, then id |
| `group` | `stage` (board order), `month` (`overdue`, then `YYYY-MM` ascending, then `none`; a closed item past its date sits in its month), `parent` (`organisation:<id>`/`account:<id>`, by name), `owner` (by name, `unassigned` last), `department` (by name, `none` last), `priority` (High, Medium, Low), or empty |
| `group_value` | with `group` only: restrict `results` and `count` to that group key (a Board column). `groups` and `summary` stay whole |
| `cursor` | opaque, from `next_cursor`; valid only for the same kind and the same filters, search, `ids`, sort, `group` and `group_value` |
| `limit` | default 50, max 100 |

```json
{
  "kind": "opportunities",
  "results": [{
    "id": 41, "kind": "opportunity", "title": "EMEA seats",
    "parent": {"type": "account", "id": 12, "name": "Pizza Hut EMEA"},
    "companies": [{"id": 7, "name": "Pizza Hut"}],
    "owner": {"id": 2, "name": "Carl CSM"},
    "mrr": 2000.0,
    "stage": {"value": "negotiation", "label": "Negotiation"},
    "priority": {"value": "high", "label": "High"},
    "department": {"value": "cs", "label": "Customer Success"},
    "date": {"value": "2026-10-07", "days": 7},
    "open": true, "overdue": false,
    "signal": {"kind": "high_priority", "label": "High priority"},
    "stage_changed_at": "2026-09-30T10:00:00+00:00",
    "created_at": "2026-09-01T09:00:00+00:00"
  }],
  "next_cursor": "<opaque string; pass back as ?cursor=>",
  "count": 12,
  "groups": [{"key": "negotiation", "label": "Negotiation", "count": 3, "mrr": 6400.0}],
  "summary": {
    "items": 20, "mrr": 41000.0,
    "open": {"count": 12, "mrr": 26000.0},
    "within": {"30": {"count": 2, "mrr": 4000.0}, "90": {"count": 5, "mrr": 9000.0}},
    "overdue": {"count": 1, "mrr": 3000.0},
    "done_this_quarter": {"stage": "closed_won", "count": 3, "mrr": 7000.0},
    "stages": [{"value": "discovery", "label": "Discovery", "count": 4, "mrr": 5000.0}]
  },
  "filters": {
    "organisations": [{"value": "7", "name": "Pizza Hut"}],
    "accounts": [{"value": "12", "name": "Pizza Hut EMEA"}],
    "owners": [{"value": "2", "name": "Carl CSM"}, {"value": "unassigned", "name": "Unassigned"}],
    "stages": [{"value": "discovery", "name": "Discovery"}],
    "priorities": [{"value": "high", "name": "High"}],
    "departments": [{"value": "cs", "name": "Customer Success"}, {"value": "none", "name": "No department"}]
  },
  "currency": "USD"
}
```

- **Rows.** `parent` is the "Part of" link: the organisation or account the item hangs off, always one the viewer may
  open (otherwise the row does not exist). `companies` lists the parent organisations the viewer may open (for an
  account-level item, its account's openable organisations, lowest id first; possibly none). `date.days` is days from
  today (negative once passed). `open` follows the kind's open stages; `overdue` is open with a past date (due today is
  not overdue). `signal` is at most one of `overdue`, then `high_priority` on an open item.
- **Money.** `mrr` is in the workspace's currency (top-level `currency`), as the Pipelines page and the Deals & risks
  tabs have always shown it; no FX.
- **Totals.** `count` is the rows this query pages through (after `stage` and `group_value`). `groups` cover the chosen
  stages before `group_value`. `summary` covers **every stage** of the filtered set — every other filter applies, the
  stage filter does not — so the won/mitigated tile and the stage strip survive the default open-only view. Each tile is
  its filter's rule, so clicking it with the default stages lists exactly its N: `open` (default `stage`), `within.30`/
  `within.90` (open; `date=30|90`), `overdue` (`date=overdue`), `done_this_quarter` (Closed Won for opportunities,
  Mitigated for risks, whose stage last changed this calendar quarter; `stage=<done_this_quarter.stage>&changed=quarter`),
  `stages` (every stage, empty ones included: the strip and the Board's column headers; `stage=<value>`).
- **Quarter.** Calendar quarters (Jan–Mar, …) in the server's timezone, the same "today" every portfolio uses.
- **Filters** are the viewer's own: organisations they may open that hold one of their readable items (directly or
  through an account), the accounts they may open that hold one, the owners of those items' parents (people in the
  viewer's organisation only; `unassigned` when a parent has no owner), the departments present, and the kind's fixed
  stages and priorities.
- 8 constant queries per request for an admin, whatever the book size (pinned by `PipelineQueryCountTests`).

### `GET /api/v1/pipelines/{opportunities,risks}/export.csv`

Auth: `IsAuthenticated`. Same parameters (`cursor`/`limit` ignored); every row the list would page through, in list
order. `text/csv`, `Content-Disposition: attachment; filename="<kind>-<date>.csv"`. Columns: Title, Revenact ID,
Organizations (the openable ones, `; `-joined), Account (blank for an organisation-level item), Owner, Stage, Priority,
Department, MRR, Expected Close (risks: Due By), Stage Changed At, Created Date, then `Currency` — every field. Text
cells beginning with `= + - @`, tab or CR are prefixed with `'`. An error response renders as a two-row CSV. Audited
as `pipelines.exported` with `kind`, `count` and `params` (the query-parameter names, never their values).

### `POST /api/v1/pipelines/{opportunities,risks}/bulk/`

Auth: `IsAuthenticated`. Body `{"ids": [1, 2], "action": "set_stage" | "set_priority" | "set_department" | "set_date",
"value": …}`: `set_stage` takes one of the route kind's stages; `set_priority` `high|medium|low`; `set_department` a
`User.Function` value or `""` (everyone's); `set_date` `YYYY-MM-DD`, or `null` to clear — the `value` key must be present
(omitting it is a 400). 1–500 ids, duplicates applied once.

Each id is locked and re-read (`select_for_update`) through the editor's own twice-filter, then saved through the kind's
single-edit serializer (partial) in its own transaction — the single PATCH's rules, so a stage change moves
`stage_changed_at` as a Board drag does. `200`:

```json
{"updated": [1], "failed": [{"id": 2, "reason": "Not found."}]}
```

An id the caller cannot read (another department, an account they cannot open, another tenant, the other kind) reads
`"Not found."`, like one that does not exist. A database error on one id puts it in `failed` with
`"Could not be updated."` and the batch continues. Audited once as `pipelines.bulk_updated` (`kind`, `action`, `field`,
`ids`, `failed_ids`; never the value), written in a `finally`; outcome `failure` when nothing was updated or the batch
did not finish. The items are not also audited one by one.
````

- [ ] **Step 3: `docs/API_CONTRACTS.md`, the existing sections**

1. Under `### Models — \`Opportunity\``, directly after the paragraph ending "same shape as Account's own ARR-family fields.", insert:

```text
**Since 2026-09-30 (Pipelines redesign):** `stage` also has `closed_lost` (Closed Lost), after `closed_won`; *open*
means neither. `expected_close` is an optional date (null reads "No date"). `stage_changed_at` (read-only) is when
the stage last changed, creation included (`StageClockMixin`; rows from before it start at `created_at`). Every
opportunity endpoint below reads and writes `expected_close` and returns `stage_changed_at`. `companies` names only
the organisations the caller may open, on every endpoint.
```

2. Under `### Models — \`Risk\``, directly after the paragraph ending "`priority`/`mrr` are the same shape as Opportunity's.", insert:

```text
**Since 2026-09-30:** `due_by` is an optional date (null reads "No date"); *open* is stage `open`. `stage_changed_at`
as on Opportunity. Every risk endpoint below reads and writes `due_by` and returns `stage_changed_at`; `companies` names
only the organisations the caller may open.
```

3. In both JSON examples (`GET/POST /api/v1/opportunities/` and `GET/POST /api/v1/risks/`), directly after the line `    "department_display": "",` insert `    "stage_changed_at": "2026-09-30T10:00:00+00:00",` and then `    "expected_close": "2026-11-15",` (opportunities) or `    "due_by": null,` (risks).

4. In `#### Known consequences (accepted, not oversights)`, item 2, replace the sentences from "Contact/Opportunity/Risk/Survey/Canvas rows still carry their own `companies` list unfiltered by default" through "rather than a reason those four are left as they are." with:

```text
Opportunity and Risk rows name only organisations the viewer may open, on every endpoint
(`PipelineItemSerializer.get_companies` over `VisibleCustomersMixin`, which fails closed without a request).
Contact's list is narrowed the same way when the view passes `visible_customer_ids` (`ContactCompanyVisibilityMixin`,
e.g. the Contacts page). Survey and Canvas rows still carry theirs unfiltered — the known follow-up is giving those
two the same treatment.
```

5. In `### Opportunities and risks are read department-wise`, replace the last sentence ("The Pipelines page's filter narrows further by department, priority and stage on the client.") with:

```text
The Pipelines page's book (`/pipelines/{opportunities,risks}/`, see `pipelines_portfolio`) applies the same rule once,
together with the parent rule, and filters by department, priority and stage on the server. Creates, edits (a Board
drag included) and deletes on every endpoint here are audited (`opportunity.created|updated|deleted`, `risk.…`: ids
and changed field names only; a PATCH that changes nothing is not recorded).
```

6. In the forecast section's rules list, directly after the bullet that begins `* **Expansion is weighted by sales stage**`, add:

```text
* **A Closed Lost opportunity is not counted anywhere** — not expansion, not `pipeline`, not the scenarios, not a
  Copilot pipeline snapshot. `pipeline` keeps its six stages.
```

- [ ] **Step 4: `docs/audit-events.md`**

Directly after the `accounts.bulk_updated` row, add:

```text
| `opportunity.created` / `opportunity.updated` / `opportunity.deleted` | `services.customers.pipeline_audit` (every opportunity list-create and detail endpoint) | user | Opportunity (`target_repr` is `opportunity <id>`, never the title) | created/deleted: `customer_id`, `account_id`; updated: `fields` changed (names only, a Board drag is `["stage"]`); a PATCH that changes nothing is not recorded |
| `risk.created` / `risk.updated` / `risk.deleted` | `services.customers.pipeline_audit` (every risk list-create and detail endpoint) | user | Risk (`risk <id>`) | as for opportunities |
| `pipelines.exported` | `services.pipelines_portfolio.views.PipelineExportView` | user | — | `kind`, `count` of rows exported, `params`: the query-parameter names used (never their values) |
| `pipelines.bulk_updated` | `services.pipelines_portfolio.views.PipelineBulkUpdateView` | user | — | `kind`, `action`, `field`, `ids` updated, `failed_ids`; never the value; written in a `finally`, outcome `failure` when nothing was updated or the batch did not finish; the items are not also recorded one by one |
```

- [ ] **Step 5: `docs/data-classification.md`**

Directly after the `Accounts CSV export` row, add:

```text
| Pipelines CSV export (`services/pipelines_portfolio`, `GET /pipelines/{opportunities,risks}/export.csv`) → the requester's device | every opportunity or risk field (title, MRR, stage, priority, department, date, stage clock, the parent organisation or account and its owner, the linked organisations the requester may open) for the requester's own readable, filtered book | confidential | twice-filtered exactly like the list (AUTH-02: the parent must be openable and the department readable); audited `pipelines.exported` (LOG-01); formula cells neutralised against CSV injection |
```

- [ ] **Step 6: The product docs**

In `docs/product/01-prd.md` §5.2, replace the row `| Pipelines (opportunities and risks) | Built | Kanban with drag to stage; department and role scoped |` with:

```text
| Pipelines (opportunities and risks) | Built | Kanban with drag to stage; department and role scoped. One book of opportunities or risks across organisations and accounts (backend, 2026-09-30): tiles (open, closing or due in 30/90 days, overdue, won or mitigated this quarter, a stage strip), group (stage, close month, organisation or account, owner, department, priority), five sorts, filters and cursor pages with Board columns; CSV export and bulk stage, priority, department and date, all twice filtered (openable parent, readable department). Expected close / due by dates, a Closed Lost stage, audited writes; `companies` names only organisations the viewer may open |
```

In §9 Release history, append:

```text
| 2026-09-30 | Pipelines, delivery 1 (backend): dates, Closed Lost, stage clock, the pipelines book, export, bulk edit, audited writes, `companies` trimmed |
```

In `docs/product/02-trd.md` §2.4, replace the row that begins `` | `services/organizations/`, `services/accounts_portfolio/` `` with:

```text
| `services/organizations/`, `services/accounts_portfolio/`, `services/pipelines_portfolio/` | The three portfolios (Organizations, Accounts, Pipelines): each loads the viewer's visible book in a fixed number of queries and computes row signals in Python; the account and pipeline ones import the organisation one's generic helpers (keyset cursor, section order, CSV cell, bulk reasons) and keep their own rules. The pipeline one serves both kinds through one `Kind` object and applies the twice-filter (openable parent, readable department) once, in `book.scope` |
```

In §2.5, change "Board-style endpoints (opportunities, risks, health) are deliberately unpaginated." to "Board-style endpoints (opportunities, risks, health) are deliberately unpaginated; the Pipelines page itself reads the cursor-paged `/pipelines/{kind}/` book, and the unpaginated lists remain for the Deals & risks tabs."

In `docs/product/05-backend-schema.md`:

- Replace the `Opportunity` row with: ``| `Opportunity` | `title`, `mrr` (workspace currency), `stage` (discovery, qualification, solution_validation, proposal_price_review, negotiation, closed_won, closed_lost), `priority`, `department`, `expected_close` (optional), `stage_changed_at` (kept by `StageClockMixin`, backfilled to `created_at`) |``
- Replace the `Risk` row with: ``| `Risk` | Same shape; `stage` is open, mitigated, realised, abandoned; `due_by` instead of `expected_close` |``
- Directly after the `### \`accounts_portfolio\`` section's paragraph, add:

```text
### `pipelines_portfolio`

No model. `services.pipelines_portfolio.book.load_book` reads `Opportunity` or `Risk` (both parents and their owners
joined) and the `Account.customers` link table for the viewer's readable, filtered items on every request, and
`shape.py` orders, groups and totals them in Python. Bulk edits write through `OpportunitySerializer`/`RiskSerializer`.
```

- Replace the access-table row `| Opportunities and risks | Own department plus undeparted; Leadership and \`view_all_accounts\` see all | \`scoping.pipeline_visible_q\` |` with:

```text
| Opportunities and risks | The parent organisation or account must be openable (`visible_children_q`; an organisation roll-up follows `customer_rollup_q`), then own department plus undeparted; Leadership and `view_all_accounts` see every department. The Pipelines book applies both once (`pipelines_portfolio.book.scope`); `companies` names only openable organisations | `scoping.pipeline_visible_q` |
```

- [ ] **Step 7: Check the docs**

Run: `venv/bin/ruff format docs/API_CONTRACTS.md docs/product/01-prd.md docs/product/02-trd.md docs/product/05-backend-schema.md docs/audit-events.md docs/data-classification.md && venv/bin/ruff format --check docs`
Expected: every file formatted (the new blocks use only `json` and `text` fences).

Run: `grep -c "pipelines.exported\|pipelines.bulk_updated\|opportunity.created" docs/audit-events.md`
Expected: `3`.

- [ ] **Step 8: Commit**

```bash
git add docs
git commit -m "$(cat <<'EOF'
docs(pipelines): the pipelines book, new fields, audit events and product docs

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: Full verification

**Files:** none changed. If a check fails, fix it and commit the fix in the task it belongs to.

- [ ] **Step 1: The whole suite**

Run: `venv/bin/python manage.py test --parallel --noinput`
Expected: `OK`. No test is skipped that was not skipped on `main`.

- [ ] **Step 2: Lint and format**

Run: `venv/bin/ruff check . && venv/bin/ruff format --check .`
Expected: `All checks passed!` and `… files already formatted`.

- [ ] **Step 3: One migration, system checks clean**

Run: `venv/bin/python manage.py makemigrations --check --dry-run && venv/bin/python manage.py check`
Expected: `No changes detected` and `System check identified no issues (0 silenced).`

Run: `git diff --name-only main -- '*/migrations/*'`
Expected: exactly `services/customers/migrations/0049_pipeline_dates.py`.

- [ ] **Step 4: The house markers**

Run: `grep -n "SOC2:AUTH-02" services/pipelines_portfolio/*.py services/customers/serializers.py | wc -l && grep -n "SOC2:LOG-01" services/pipelines_portfolio/views.py services/customers/pipeline_audit.py`
Expected: `AUTH-02` in `book.py` (scope, organisation filter, account filter, two option reads, owners), `bulk.py`, the three views and the serializers; `LOG-01` twice in `views.py` and once in `pipeline_audit.py`.

- [ ] **Step 5: The schema still builds**

Run: `venv/bin/python manage.py spectacular --file /dev/null --validate`
Expected: exit 0. APIView warnings like the Accounts endpoints' are acceptable; errors are not.

- [ ] **Step 6: A live look**

Run `venv/bin/python manage.py migrate` then `venv/bin/python manage.py runserver`. Log in as a seeded CSM and run `curl -s -H "Authorization: Bearer <token>" "http://localhost:8000/api/v1/pipelines/opportunities/?group=stage&limit=5"`.
Expected: a 200 whose `groups` list the open stages present with counts and MRR, at most 5 `results` each with `parent` and `companies`, and `summary.stages` listing all seven stages. Existing rows read `date.value: null` ("No date").

- [ ] **Step 7: Finish the branch**

Use superpowers:finishing-a-development-branch. The PR body lists the three endpoints, the migration, the `companies` privacy fix on every opportunity/risk endpoint, the audit events, the forecast's Closed Lost handling and the `AccountSerializer` refactor onto `VisibleCustomersMixin`, and ends with the attribution line from the session's instructions. This PR merges and deploys before the frontend's.

---

## Decisions this plan makes where the spec is silent

1. **The summary ignores the stage filter, and only that filter.** The spec asks for tiles whose totals "cover every filtered row". It also makes open stages the default filter. If the tiles honoured the default, "Won this quarter" would always read 0 and the stage strip could never show a closed stage. So every other filter applies to the tiles and `stage` does not. `count` and `groups` do honour `stage`. The test "summary equals the rows it covers" is run with every stage selected.
2. **Clicking a tile applies that tile's rule as a filter.** `within` means open items dated from today to today+N; it does not include overdue items, which have their own tile. `overdue` means open with a date before today, so an item due today is not overdue. "Done this quarter" is `stage=<done>&changed=quarter`. The `date` filter does not itself restrict to open items. The default stage filter does that, so with default stages a tile click lists exactly its N (tested).
3. **Quarters are calendar quarters** (Jan–Mar and so on). They use the server's `timezone.localdate()`, which is UTC, the same "today" every portfolio uses. Fiscal quarters are not configurable.
4. **Closed stages.**
   - Opportunities: Closed Won and Closed Lost are closed.
   - Risks: Mitigated, Realised and Abandoned are closed, which matches the forecast's `OPEN_RISK_STAGES`.
   - The risks' "done this quarter" tile counts Mitigated only.
   - Unchanged, and worth knowing: the Copilot digest still counts Mitigated and Realised risks as "open".
5. **`ids` without `stage` reaches every stage.** This is so "Export selected" never silently drops a closed item. It mirrors how Organizations' `ids` reaches churned rows.
6. **Currency: `mrr` is read as the workspace currency, with no FX.** This is what the page and the Deals & risks tabs have always shown, and it is what the spec says. The forecast, by contrast, converts opportunity MRR from the parent organisation's own currency. For a customer billed in another currency the two will differ. This plan leaves the forecast alone, as the spec puts it out of scope. **Owner may want one rule.**
7. **Owner means the parent's owner.** An account-level item uses the account's owner, not the organisation's. `owner=unassigned` matches an item whose own parent has no owner.
8. **The organisation filter rolls up.** It returns items on the organisation, plus items on its accounts that the viewer may open, as the organisation page's Deals & risks tab does. The account filter is exact. Both use comma lists, and an id the viewer cannot open matches nothing.
9. **Search matches the item's own parent name.** For an account-level item that is the account name. It does not match the account's linked organisations' names.
10. **Close-month grouping.** The order is Overdue, then months ascending, then No date. Overdue first matches the renewal windows. A closed item past its date sits in its month, not under Overdue. Month labels are English ("October 2026").
11. **Defaults.**
    - The default sort is `-mrr`.
    - There is no default group in the API. The frontend puts stage in the URL, as on Accounts.
    - The Board requests every stage explicitly, one column at a time, with `group_value`. Every column header, empty ones included, comes from `summary.stages`.
12. **The stage clock lives in the model** (`StageClockMixin.save`). This catches every path: forms, drag, bulk, admin and seeds.
    - `QuerySet.update(stage=…)` bypasses it. No code does that today.
    - A no-op stage write is not a change.
    - The backfill sets `stage_changed_at = created_at`, as the spec requires. Dates stay empty, and seeds are unchanged.
13. **Audit.**
    - `target_repr` is `"<kind> <id>"`, never `str()`, because `str()` quotes the title. This needs a small `target_repr` override in `core.audit.record`.
    - Create and delete events carry parent ids only.
    - Edit events carry the names of the changed fields only. A PATCH that changes nothing is not recorded.
    - Bulk writes one batch event and no per-item events. The event records the field name but never the value. That is stricter than `accounts.bulk_updated`, which records its value.
14. **`companies` privacy is fail-closed.** `VisibleCustomersMixin` names nothing when there is no request. That is stricter than ContactSerializer, which trims only when the view asks. `AccountSerializer` now shares the mixin; its behaviour is unchanged.
15. **Forecast.** A Closed Lost opportunity is left out of `counted_opportunities`, so it drops out of expansion, the scenarios, `pipeline` and the Copilot snapshots. `pipeline` keeps exactly its six rows, so the dashboard response is unchanged. The Copilot "open opportunities" count also excludes Closed Lost.
16. **Bulk `set_department` accepts `""` (everyone's)** and may move an item to a department the editor cannot read. The single PATCH allows both today.
17. **Export columns.** There are 12 fields plus Currency. "Organizations" joins the openable names with `; `, and "Account" is blank for an organisation-level item. Owner is the only column with no field of its own.
18. **Routes.** `kind` comes from the route (`{"kind_key": …}` URL kwargs), never from the client, so an unknown kind is a 404 with no route. The app mounts at `api/v1/pipelines/`.

## Self-review

**Spec coverage** (§2, the server side of §1, §5 Backend):

| Spec item | Where it is covered |
|---|---|
| Model: dates, Closed Lost, `stage_changed_at` with backfill, one migration | Task 1. Migration on existing rows is tested |
| Stage clock set on create, stage change, drag PATCH and bulk | Tasks 1, 3 and 12 |
| Existing endpoints accept the new fields | Task 3 |
| `companies` trimmed on every opportunity/risk endpoint (Known consequences #2) | Task 3: flat list, nested organisation, nested account, flat account, detail GET/PATCH, POST. Task 10: book rows |
| Audit: create, edit and stage change, delete; bulk batch; no values | Tasks 4 and 12 |
| Endpoints: rows, groups, summary, filter options, cursor, `group_value` | Tasks 6–10 |
| Endpoints: `export.csv` | Task 11 |
| Endpoints: bulk (stage, priority, department, date) | Task 12 |
| Conventions: unknown values ignored, `ids` narrows | Tasks 5 and 10 |
| Privacy: twice filtered once, filters only narrow, unopenable organisation or account filter → nothing | Task 6 (`scope`, filters); tested in Tasks 6, 10, 11 and 12 |
| Privacy: owner options from own organisation | Task 6 |
| Privacy tests: blind to one account | Tasks 6, 10, 11, 12 |
| Privacy tests: another tenant | Tasks 6, 10, 12 |
| Privacy tests: department rule | Tasks 6, 10, 11, 12 |
| Privacy tests: unopenable organisation filter | Tasks 6, 10 |
| Privacy tests: `companies` everywhere | Tasks 3, 10 |
| Tiles per kind, "this quarter" by `stage_changed_at`, totals over the filtered set | Task 9 |
| Tiles: summary equals its rows; each tile lists its N | Task 9 |
| Group: stage, close month, parent, owner with Unassigned, department, priority, none | Task 8 |
| Sort: five keys, missing last | Task 8 |
| Filters: organisation, account, owner, stage default open, priority, department, 30/90/180, overdue, no date | Tasks 5, 6 |
| Forecast: Closed Lost not expansion, behaviour otherwise unchanged | Task 2 |
| Query counts pinned and flat | Task 10 |
| E2E | Task 13 |
| Docs | Task 14 |

**Placeholder scan.** There is no TBD, TODO, "similar to Task N" or undescribed step. Every code step carries its code. Every test step carries its test code, the command and the expected result. Task 1's migration is generated by Django and then extended; the plan shows the finished file.

**Type consistency.**

| Name | Defined | Consumed |
|---|---|---|
| `Opportunity.OPEN_STAGES` / `CLOSED_STAGES`, `Risk.OPEN_STAGES` | Task 1 | Tasks 2 and 5 |
| `VisibleCustomersMixin._visible_customer_ids()` | Task 3 | Task 3 (`AccountSerializer`, `PipelineItemSerializer`) |
| `pipeline_audit.create/update/delete` | Task 4 | Task 4 (views) |
| `audit.record(..., target_repr=)` | Task 4 | Task 4 |
| `Kind.key/item/model/serializer/date_field/date_label/open_stages/done_stage/stages` | Task 5 | Tasks 6–12 |
| `PipelineParams.stages/.../limit`, `parse_params(query, kind)` | Task 5 | Tasks 6–12 |
| `scope(user, kind)` | Task 6 | Tasks 6 and 12 |
| `quarter_bounds(today)` | Task 6 | Task 9 |
| `PipelineEntry.item/mrr/when/days/is_open/overdue/owner/parent/organisations/signal` | Task 6 | Tasks 7–9 |
| `PipelineBook.kind/entries/rows/organisation` | Task 6 | Tasks 8, 9, 11 |
| `load_book(user, kind, params, *, today)`, `filter_options(user, kind)` | Task 6 | Tasks 7–11 |
| `row_payload(entry, kind)`, `department_label(value)` | Task 7 | Tasks 8, 9, 11 |
| `fields_for(kind)` | Task 7 | Task 11 |
| `select(book, params)`, `paginate(entries, *, params, kind)` | Task 8 | Tasks 9, 11 |
| `build_summary(book, *, today)`, `build_listing(book, params, *, filters, today)` | Task 9 | Task 10 |
| `table(rows, *, kind, currency)` | Task 11 | Task 11 |
| `bulk.field_for(kind, action)`, `bulk.apply(request, kind, *, ids, action, value, result=None)` | Task 12 | Task 12 |

The URL kwarg is `kind_key` in every view and route. The fixture names `self.pizza`, `self.taco`, `self.globex`, `self.csm`, `self.other`, `self.sales`, `self.admin`, `opportunity()`, `risk()`, `account()` and `days()` are used the same way throughout.
