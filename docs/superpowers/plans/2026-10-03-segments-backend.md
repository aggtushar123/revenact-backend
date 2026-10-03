# Segments, backend (delivery 1 of the tools section) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Saved, rule-based segments of organisations, accounts or contacts: a new `services/segments` app with `Segment` and `SegmentChange`, a fixed field registry and a rule compiler, the `/api/v1/segments/` endpoints (list, create, read, edit, delete, duplicate, members, CSV export, changes, preview, pin/keep out), a nightly step in `run_health_maintenance` that records who entered and who left, and a `segment_changes` notification kind for the owner's daily alert.

**Architecture:**
- A segment stores rules, never members. `compiler.compile_rules` turns the stored JSON into one `Q` (plus the annotations it reads). `evaluate.members_queryset` runs it over the records the viewer may open, adds pins, removes exclusions and applies visibility last. Every read is computed for the person looking.
- The members page reuses each kind's own list:
  - organisations: `organizations.book.load_portfolio`, `shape`, `rows`;
  - accounts: `accounts_portfolio`;
  - contacts: `contact_list.filtered_contacts` and `ContactSerializer`.
  The two portfolio books gain a `scope=` keyword, which can only narrow the visible book.
- The nightly step (`nightly.evaluate_nightly`) evaluates each segment as its owner under a row lock. It diffs the result against `last_members`, writes `SegmentChange` rows and sends at most one in-app alert per segment per day.

**Tech Stack:** Django 5.2, DRF, PostgreSQL (`jsonb_typeof`, `jsonb → double precision`), `TestCase`/`APITestCase`/`APIClient`, `LiveServerTestCase` for e2e.

**Spec:** `react-ts-app/docs/superpowers/specs/2026-10-03-segments-design.md`, agreed with the owner on 2026-10-03. This plan covers its backend half:
- §1 (model and rules);
- §2 (privacy, endpoints, nightly step, audit, performance);
- §4 item 1 (the backend PR);
- the backend part of §5 (testing).

§3 (pages) and §4 item 2 belong to the frontend plan.

**Branch:** `feat/segments`, freshly cut from `main`. Stay on it. **Merge order:** this backend PR merges and deploys **before** the frontend PR. Nothing here removes or changes a field in an existing response: the two portfolio books only gain an optional keyword, and the notification kinds only gain one.

## Global Constraints

Copied from the spec. Quoted text is verbatim.

- Who: "Anyone. A segment only ever contains records its viewer may open, so a manager's segment covers their team's book through the existing visibility rules."
- Kind: "One kind per segment: organisations, accounts or contacts."
- Membership: "Rules, plus records the owner pins in or keeps out. A contacts segment may also use fields of the contact's organisation or account."
- Sharing: "Private, the workspace, or chosen teammates. Viewers see the same rules but only the members they may open, plus a count of the rest that names nobody. Only the owner edits or deletes. Others can duplicate a segment into their own."
- Rule logic: "Match all or any of a list of conditions. A condition may be one group with its own all/any, and groups do not nest further."
- Changes: "Membership is live whenever viewed. The nightly job records who entered and who left. Each segment has an optional daily in-app alert to its owner."
- Naming: `services/customers/segments.py` (revenue brackets, the "Size band" metrics dimension) is unrelated and untouched. The new app is `services/segments`, with models `Segment` and `SegmentChange` under the URL prefix `/segments/`.
- Model fields:
  - `Segment`: `organisation`, `owner`, `name`, `description`, `kind` (`customer`/`account`/`contact`), `rules` (JSON), `pinned_ids`, `excluded_ids`, `sharing` (`private`/`workspace`/`people`), `shared_with`, `alert_on_changes`, `paused`, `last_members`, `last_evaluated_on`, `created_at`, `updated_at`.
  - `SegmentChange`: segment, record id, change (`entered`/`left`), date, and `reason`. The reason "holds the field names that changed the match, never their values. A daily copy of every member is never stored."
- Rule shape: `{"match": "all", "conditions": [{"field", "op", "value"}, {"group": {"match", "conditions"}}]}`.
- Operators: `is`, `is_not`, `in`, `gt`, `lt`, `between`, `within_next`, `within_last` (days), `is_empty`, `is_not_empty`. "Each field accepts only the operators that make sense for its type."
- Fields: "a fixed registry; anything else is refused with a clear 400". The fields per kind are §1's list. Contacts' organisation and account fields are written as `parent.<field>`.
- Defaults: "churned and archived organisations are left out unless a rule names `churned` or `archived`, as on the Organizations list."
- Compilation:
  - "Rules compile to one `Q` over the visible queryset of the kind."
  - It reuses `health_q`, `renewing_q`, `NPS_Q` and `CHURNED`, the health-input annotations, the last-contact annotations, and a latest-value subquery for AI attributes.
  - "After the rules: pins are added, exclusions are removed, and visibility is applied last."
- Limits: "each person may own up to 50 segments, and each segment may have up to 20 conditions."
- Privacy (§2, "Twice filtered, always"):
  - Shared viewers get `hidden_count`, which "is a count only and names nobody".
  - "a pinned record the viewer cannot open is neither shown nor named".
  - Rule values that name a hidden organisation, account or owner read "an organisation you can't open", and so on. Owner names come "only from the viewer's own workspace".
  - "a rule on an AI attribute never shows a value the viewer could not otherwise see".
  - A segment the viewer may not see "reads the same as one that does not exist (404)".
- Endpoints: exactly §2's nine routes (Task 7, Task 8, Task 9).
- Nightly:
  - The step runs "after the health recalculation and the pulses".
  - It evaluates "**as the owner**".
  - It sends "one in-app notification per segment per day", which "needs a new `Notification.Kind`".
  - "A segment whose owner is inactive is paused." "The step is idempotent: one run per date." "A failure in one segment is logged and skipped."
- Audit: `segment.created`, `segment.updated`, `segment.shared`, `segment.duplicated`, `segment.deleted`, `segment.member_pinned`, `segment.member_excluded`, `segment.exported`. Each records "ids and changed field names only".
- Performance: "Query counts are pinned and stay flat as members grow." "The preview is limited to the first ten members plus the totals."
- Testing (§5): every case in §5 "Backend" has a test in this plan. The self-review maps each one.
- Out of scope: custom-object fields; per-message sentiment rules; segment-based reporting dashboards; real-time (on-save) entry detection; Ask on Segments; segments as survey or campaign audiences; scenario triggers on entry and exit.

House rules (every task):

- `docs/API_CONTRACTS.md` changes in the same PR (skill `api-contracts`). So do the product docs (`docs/product/01-prd.md` feature row and change log, `02-trd.md`, `05-backend-schema.md`), `docs/audit-events.md` and `docs/data-classification.md`. Task 10 does them all.
- Tests come in three tiers (skill `backend-testing`):
  - unit (`SimpleTestCase`/`TestCase`);
  - integration (`APITestCase`/`APIClient`);
  - e2e (`LiveServerTestCase` in `e2e/`).

  Privacy uses `blind_to_one_account` from `services/customers/tests/test_views.py`.
- Commands:
  - focused runs: `venv/bin/python manage.py test <label> --noinput`;
  - the full suite: `venv/bin/python manage.py test --parallel --noinput`;
  - migrations: `venv/bin/python manage.py makemigrations --check --dry-run`.
- **CI budget is tight** (about 27 minutes on GitHub's runners). Every new test class builds its data once with `setUpTestData`, uses a handful of rows, and never sleeps. Dates are passed in (`today=`), never waited for.
- `venv/bin/ruff check .` and `venv/bin/ruff format --check .` must pass (line length 100, rules E, F, I). Ruff also formats the Python fences inside Markdown: run `venv/bin/ruff format <file.md>` on any doc you edit.
- Put `# SOC2:AUTH-02 <why>` on every access check and `# SOC2:LOG-01` on every `audit.record` call.
- Semgrep runs in CI. Return an opaque cursor string, never a built URL. Let a DRF renderer write the CSV, never a hand-built `HttpResponse`.
- Commits are conventional (`feat(segments): …`, `test(e2e): …`, `docs(segments): …`). Each ends with a blank line and `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Do not push.

## Decisions (left open by the spec)

The owner may overrule any of these. Each one is applied in the tasks below.

1. **Field registry shape.** `registry.Field(key, label, type, column, choices, record)`. There is one static dict per kind: `CUSTOMER_FIELDS`, `ACCOUNT_FIELDS` and `CONTACT_FIELDS`.
   - An AI attribute is named `attr:<api_name>`, the scenario engine's own spelling. It resolves per workspace to a `Field` of its value type (number → `number`, boolean → `boolean`, picklist → `choice` with its options, text → `text`). It applies only to the kinds it is marked for.
   - Keys are snake_case. The registry's own names:
     - `health_category`, `nps_band`, `arr`, `owner`, `product`, `seat_use`, `open_tickets`, `last_touch`, `ai_pulse`, `csm_pulse`, `churned`, `archived`, `created`;
     - column names where a column exists (`lifecycle_stage`, `health_score`, `csat_score`, `nps_score`, `ces_percentage`, `renewal_date`);
     - contacts: `role`, `sentiment`, `status`, `language`, `last_contacted`, `organisation`, `account`.
2. **Operators per value type.**
   - `number`, `percent`: `gt lt between is_empty is_not_empty`.
   - `days` (days since): `gt lt between is_empty`. `is_empty` means "never". A record never touched counts as more than any number of days, as on the Organizations list.
   - `date`: `within_next within_last gt lt between is_empty is_not_empty`.
   - `choice`: `is is_not in`.
   - `text`: `is is_not in is_empty is_not_empty`.
   - `boolean`: `is`.
   - `owner` and `record` (an organisation, account or product id): `is is_not in`.
   - An AI attribute takes its type's operators, plus `is_empty`/`is_not_empty` ("not answered yet") when the type lacks them (`Field.optional`).

   Values:
   - JSON numbers;
   - dates as `YYYY-MM-DD`;
   - days as whole numbers, 1–3650 for the windows;
   - `between` takes two values, inclusive, low first;
   - `in` takes 1–100 values;
   - an owner value is a person's id or `"unassigned"`.

   `is_not` also matches an empty value ("CSAT is not 60" includes "no CSAT").
3. **An empty condition list matches nothing.** The segment is then its pins only. A new segment is not "everyone" by accident.
4. **`parent.` for contacts.**
   - `parent.<key>` is any organisation or account field except `organisation`, which the contact has itself.
   - It is applied to the contact's own parent: the organisation for an organisation-level contact, the account for an account-level one.
   - A field the account does not have never matches an account-level contact, even `is_empty`. This covers `ces_percentage`, `product`, `seat_use`, `churned` and `archived`.
   - A contact is visible only through a parent its viewer may open, so a parent field never reads anything the viewer could not.
   - The churned/archived default applies to organisations segments only. A contacts rule can name `parent.churned`.
5. **Organisation and account pickers.** The registry adds `organisation` to accounts (a linked organisation) and `organisation` and `account` to contacts. They are not in §1's list, but the builder's "server-searched organisation or account picker" and §2's "an organisation, account … the viewer cannot open" need them.
6. **Ids in rule values, and how a shared viewer reads them.**
   - On write, every organisation and account id must be one the writer may open. People and products must be in the writer's workspace. Otherwise the 400 reads `Not an organisation you can open.` (`… account you can open.`, `… product in your workspace.`, `… person in your workspace.`), the same for a missing id and a hidden one.
   - On read, every reader (the owner included) gets `rules` with each id they cannot open replaced by `null`, plus a `labels` map that names only what they may open:
     ```json
     {
       "organisations": {"12": "Pizza Hut"},
       "accounts": {},
       "people": {"4": "Carl CSM"},
       "products": {}
     }
     ```
     The frontend renders a `null` (or an id with no label) as "an organisation you can't open".
   - When rules are evaluated for a viewer, ids are intersected with what that viewer may open. So `null` and a hidden id both match nothing in their book, and the rule a viewer reads is exactly the rule they are evaluated by.
   - Duplicate copies the rules as the duplicator reads them.
7. **ARR.**
   - Organisations: the column the workspace's global-attribute mapping names (`effective_global_attributes()["arr"]`), converted to the workspace currency by one SQL `CASE` over the workspace's `FxRate` rows (`arr_expression`).
     - Cost: one `FxRate` query per evaluation, no join, no per-row query.
     - A currency with no rate is `NULL`. It never meets an ARR condition, and the tile counts it in `unconverted_count`, as `convert_to_org_currency` says.
   - Accounts: `Account.arr` as stored, already in the workspace currency.
   - The "ARR covered" tile uses the same rule. The Organizations rows keep the list's own `arr` (`arr_billed_at_account`, converted). The two agree under the default mapping. See the ambiguities at the end.
8. **AI attributes.**
   - The latest value comes from a correlated subquery: the newest `AIAttributeValue` on the record by `(-computed_at, -id)`, the row the attribute panel shows. It uses the existing `(attribute, customer|account, -computed_at)` index. Cost: one index probe per row per attribute condition, plus one `AIAttribute` query per evaluation.
   - A number attribute is read through `jsonb_typeof(...) = 'number'` before the cast. An attribute whose type changed keeps its old answers and must not 500 the page.
   - The value is only ever read on records the viewer may open, which is the attribute panel's own rule.
9. **Open tickets** count the unresolved tickets filed on the record itself, the health rubric's scope, that the *viewer* may read under the department rule (`personal.visible_tickets`). The rubric's own annotation counts every department, so reusing it would break "twice filtered".
   - Last touch reuses `contact.last_contact_annotation` (organisations, the rubric's own) and `last_account_contact_annotation` (accounts).
   - Contacts' "days since last contacted" reads `Contact.last_contacted_at`, which contact sentiment keeps current.
10. **`hidden_count`** is live: one `COUNT`. It is the owner's members (as the owner may open them) minus the viewer's visible records. It is 0 for the owner, and never an id. `/changes/` reports its own `hidden_count`: the rows in the window naming records the viewer cannot open, deleted ones included.
11. **Baseline.** Creating a segment, editing its rules, pinning or keeping out, and duplicating each re-baseline it (`baseline.rebaseline`): `last_members` becomes today's members as the owner sees them, and no change rows are written. The history records the data moving, never an edit. This also stops delivery 3's scenarios from firing on every record an edit brings in.
12. **`last_members`** is a sorted JSON list of ids. A denormalised `member_count` column lets the list skip it, and the list `defer`s it. Above `MAX_TRACKED_MEMBERS = 100_000`, the step keeps the count and sets `last_members` to `null` (no history), so a 50k-member segment (about 0.35 MB of JSON) is still tracked.
13. **Nightly step.**
    - It runs as the owner, under `select_for_update` on the segment.
    - It is idempotent per date in three ways:
      - it skips a segment whose `last_evaluated_on >= today`;
      - the change rows are unique on `(segment, record_id, changed_on)`;
      - the alert is sent only inside that one evaluation.
    - The first run, with no baseline, records nothing.
    - An inactive owner pauses the segment. An active owner again resumes it from a fresh baseline, so the gap is not reported as one burst.
    - One segment's failure is logged (`logger.exception`) and skipped.
    - The step sits after the pulse dots and before the metric month-end. `--dry-run` skips it, as it skips the other writing steps.
14. **Reasons** are field keys:
    - Entered: the top-level conditions that now hold.
    - Left: the conditions that no longer hold.
    - A group contributes all its field keys.
    - Special reasons: `pinned`, `deleted` (the record is gone), `access` (the owner can no longer open it), and `churned`/`archived` (the organisations default removed it).
15. **Members response.**
    - Organisations and accounts return the kind's own portfolio rows (`row_payload`), groups and keyset cursor, with the same `sort`, `group`, `group_value`, `search`, `cursor` and `limit` parameters.
    - Contacts return the Contacts list's `ContactSerializer` rows, with its filters and a keyset cursor by name.
    - No `filters` options: the members tab does not filter.
    - Body keys: `results`, `next_cursor`, `count`, `groups`, `kind`, `currency`, `summary` and `hidden_count`.
16. **Tiles** cover every member the viewer may open, not the search or the page. Search narrows rows, not the segment.
    - Organisations and accounts: `members`, `arr`, `unconverted_count`, `avg_health`, `avg_csat`, `entered_7d`, `left_7d` (records the viewer may open only) and `currency`.
    - Contacts: `members`, `contacts` (the Contacts list's own `contacts_summary`) and the 7-day counts. `arr`, `avg_health` and `avg_csat` are `null`.
17. **CSV columns:**
    - organisations: the Organizations export (34 fields + Currency);
    - accounts: the Accounts export (+ Currency);
    - contacts: Name, Email, Phone, Role, Status, Sentiment, Language, Last Contacted, Organisation, Account.

    The rows are every member the viewer may open, in list order, honouring `search` and `sort` as the portfolio exports do.
18. **Writes.**
    - A reader who is not the owner gets `403 {"detail": "Only the segment's owner can change it."}`.
    - Anyone who may not read the segment gets the same 404 as a missing id.
    - `kind` cannot change.
    - `shared_with` holds 1–50 active teammates in the owner's workspace, never the owner. It is cleared whenever `sharing` is not `people`.
19. **Limits:**
    - 50 owned segments, checked on create and duplicate;
    - 20 conditions, counting each one inside a group;
    - 500 pinned and 500 kept out;
    - `name` 120 characters, `description` 500.
20. **List rows** (`GET /segments/`): an unpaginated list (own ≤ 50, plus what is shared).
    - Each row carries the owner's nightly `member_count`, today's `{entered, left}` and a 30-point `sparkline`. The sparkline is rebuilt backwards from `member_count` and each day's changes, in one query for every row.
    - These are counts only, the same class of figure as `hidden_count`.
21. **Pin audit:** `segment.member_pinned` carries `{record_id, pinned: true|false}`, and `segment.member_excluded` carries `{record_id, excluded: true|false}`. Clearing a state records the event of the list it left.
22. **Query pins:**
    - `GET /segments/`: 2.
    - `GET /segments/<id>/`: 2.
    - `GET /segments/<id>/members/`: 12 for an organisations segment read by its admin owner, flat as the book grows for an admin and a CSM.
    - `POST /segments/preview/`: 6.
    - `GET /segments/<id>/changes/`: 5.

    Each pin lists its queries in the test docstring.
23. Deleting a user deletes the segments they own (`CASCADE`). Users are normally deactivated, which pauses their segments (decision 13).

## Reuse map: what is imported, what is the segment's own

Each helper below was read in the current code before the plan relied on it.

| Need | Source | Use |
|---|---|---|
| Visibility | `customers.scoping.visible_customers`, `visible_accounts`, `visible_children_q` | `evaluate.visible_records`, id checks, labels |
| Health bands, NPS bands, churn, renewal | `organizations.book.health_q`, `NPS_Q`, `CHURNED`, `renewing_q`; `accounts_portfolio.book.account_renewing_q` | compiler |
| NPS band values | `organizations.params.NPS_BANDS` | registry |
| Last touch | `customers.contact.last_contact_annotation`, `last_account_contact_annotation` | compiler |
| Tickets by department | `customers.personal.visible_tickets`, `Ticket.RESOLVED_STATUSES` | compiler (`open_tickets_expression`) |
| ARR mapping and FX | `Organisation.effective_global_attributes`, `fx_rates.conversion.rates_for` | `compiler.arr_expression` (the SQL form of `convert_to_org_currency`'s rule) |
| AI attributes | `attributes.models.AIAttribute`, `AIAttributeValue` | registry, compiler |
| Organisations members | `organizations.params.parse_params`, `book.load_portfolio` (gains `scope=`), `shape.select`, `shape.paginate`, `rows.row_payload`, `export.table` | `members.py` |
| Accounts members | the same five from `accounts_portfolio` (`load_portfolio` gains `scope=`) | `members.py` |
| Contacts members | `contact_list.filtered_contacts`, `parse_contact_filters`, `contacts_summary`; `customers.serializers.ContactSerializer` | `members.py`, `summary.py` |
| Cursor | `portfolio_core.shape.keyset_page`, `rank_of`, `fingerprint` | contacts members |
| Params | `organizations.params.int_or_none`, `DEFAULT_LIMIT`, `MAX_LIMIT` | contacts members, changes |
| CSV | `organizations.export.CSVRenderer`, `cell`; `organizations.fields.Field` | export view, `export.contacts_table` |
| Row helpers | `organizations.rows.person`, `iso` | payloads, preview |
| Notifications | `notifications.realtime.notify(…, push_on_commit=True)`, `Notification.Kind` (+ `SEGMENT_CHANGES`) | nightly |
| Audit | `core.audit.record` | views |
| Nightly pattern | `run_health_maintenance`'s `if not options["dry_run"]: try … except Exception: WARNING` steps | Task 9 |
| Scenario condition shape | `scenarios.engine`: `field`/`op`/`value` and `attr:<api_name>` | rule JSON, registry |

## File Structure

| File | Responsibility |
|---|---|
| `services/segments/__init__.py`, `apps.py` (new) | App config (`SegmentsConfig`) |
| `services/segments/models.py` (new) | `Segment`, `SegmentChange`, `default_rules`, limits (`MAX_OWNED`, `MAX_CONDITIONS`, `MAX_PINNED`, `MAX_TRACKED_MEMBERS`) |
| `services/segments/migrations/0001_initial.py` (new, generated) | The two tables |
| `services/notifications/models.py` (modify), `migrations/0003_segment_changes_kind.py` (new, generated) | `Notification.Kind.SEGMENT_CHANGES` |
| `services/segments/registry.py` (new) | Value types, `OPERATORS`, `Field`, the per-kind field dicts, AI attribute and `parent.` resolution |
| `services/segments/rules.py` (new) | `RuleError`, `leaves`, `validate_rules` (the 400s), `present_rules` (redaction + labels) |
| `services/organizations/book.py`, `services/accounts_portfolio/book.py` (modify) | `filtered_queryset`/`load_portfolio` gain `scope=None` |
| `services/segments/compiler.py` (new) | `Compiled`, `Compiler`, `compile_rules`, `arr_expression`, `seat_use_expression`, `open_tickets_expression`, `latest_value`, `latest_number`, `JsonbTypeof` |
| `services/segments/evaluate.py` (new) | `MODELS`, `Draft`, `visible_records`, `members_queryset`, `member_ids`, `hidden_count`, `openable_ids` |
| `services/segments/baseline.py` (new) | `rebaseline` |
| `services/segments/access.py` (new) | `readable_segments`, `get_readable`, `get_owned`, `NOT_OWNER` |
| `services/segments/serializers.py` (new) | `SegmentWriteSerializer`, `PreviewSerializer`, `MemberStateSerializer` |
| `services/segments/payloads.py` (new) | `segment_payload`, `list_rows`, `daily_changes`, `sparkline` |
| `services/segments/summary.py` (new) | `segment_summary`, `recent_changes` |
| `services/segments/members.py` (new) | `members_listing`, `members_table`, `preview` |
| `services/segments/export.py` (new) | `CONTACT_FIELDS`, `contacts_table` |
| `services/segments/history.py` (new) | `change_history`, `DEFAULT_DAYS`, `MAX_DAYS` |
| `services/segments/nightly.py` (new) | `evaluate_nightly`, `evaluate_segment`, `change_reasons`, `alert_message`, `NightlyResult`, `Outcome` |
| `services/segments/views.py`, `urls.py` (new) | The nine routes |
| `services/segments/tests/` (new) | `__init__.py`, `fixtures.py`, `test_models.py`, `test_rules.py`, `test_compiler_customers.py`, `test_compiler_accounts_contacts.py`, `test_evaluate.py`, `test_views.py`, `test_members.py`, `test_nightly.py` |
| `services/organizations/tests/test_scope.py`, `services/accounts_portfolio/tests/test_scope.py` (new) | The `scope=` keyword |
| `services/customers/management/commands/run_health_maintenance.py` (modify) | The nightly step |
| `config/settings.py`, `config/urls.py` (modify) | Register the app; mount `api/v1/segments/` |
| `e2e/test_segments_flow.py` (new) | Preview, create, share, pin, duplicate, nightly, changes, export, delete over real HTTP |
| docs (modify) | `API_CONTRACTS.md`, `audit-events.md`, `data-classification.md`, `product/01-prd.md`, `product/02-trd.md`, `product/05-backend-schema.md` |

---

### Task 1: The app, `Segment`, `SegmentChange` and the notification kind

**Files:**
- Create: `services/segments/__init__.py` (empty), `services/segments/apps.py`, `services/segments/models.py`, `services/segments/migrations/__init__.py` (empty), `services/segments/migrations/0001_initial.py` (generated)
- Modify: `services/notifications/models.py` (`Notification.Kind`); create `services/notifications/migrations/0003_segment_changes_kind.py` (generated)
- Modify: `config/settings.py` (`INSTALLED_APPS`, after `"services.pipelines_portfolio",`)
- Test: `services/segments/tests/__init__.py` (empty), `services/segments/tests/fixtures.py`, `services/segments/tests/test_models.py`

**Interfaces:**
- Consumes: nothing new.
- Produces (every later task):
  - `Segment` (fields as the Global Constraints list, plus `member_count`):
    - `Segment.Kind` (`CUSTOMER="customer"`, `ACCOUNT="account"`, `CONTACT="contact"`);
    - `Segment.Sharing` (`PRIVATE`, `WORKSPACE`, `PEOPLE`);
    - `str(segment) == f"Segment {pk}"`.
  - `SegmentChange(segment, record_id, change, changed_on, reason)`, with `SegmentChange.Change` (`ENTERED`, `LEFT`) and unique `(segment, record_id, changed_on)`.
  - `default_rules() -> {"match": "all", "conditions": []}`.
  - `MAX_OWNED = 50`, `MAX_CONDITIONS = 20`, `MAX_PINNED = 500`, `MAX_TRACKED_MEMBERS = 100_000`.
  - `Notification.Kind.SEGMENT_CHANGES == "segment_changes"`.
  - In the tests, `SegmentFixture`, `rule(field, op, value=None, *, match="all")`, `person(email, name, organisation, **extra)` and `SegmentFixture.make_account(name, customer, owner, **fields)`.

- [ ] **Step 1: Write the fixture and the failing tests**

Create `services/segments/tests/fixtures.py`:

```python
"""The segments tests' shared world, built once per test class
(`setUpTestData`), so a class of thirty tests creates it once: the CI budget
is tight."""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from services.accounts.models import Organisation, User
from services.customers.models import Account, Customer
from services.segments.models import Segment


def rule(field, op, value=None, *, match="all"):
    """One condition as a whole rule: `{"match", "conditions": [...]}`."""
    condition = {"field": field, "op": op}
    if value is not None:
        condition["value"] = value
    return {"match": match, "conditions": [condition]}


def person(email, name, organisation, **extra):
    values = {"role": User.Role.CSM, "function": User.Function.CS, **extra}
    return User.objects.create_user(
        email=email, password="supersecret1", name=name, organisation=organisation, **values
    )


class SegmentFixture(TestCase):
    """Acme has Alice (admin, Leadership) and two CSMs, Carl and Dana. Globex
    is another tenant, with Gus. Carl owns Pizza Hut and its account Pizza
    EMEA; Dana owns Taco Bell and its account Taco West. Carl cannot open
    Taco Bell or Taco West, and Dana cannot open Pizza Hut or Pizza EMEA."""

    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.org = Organisation.objects.create(name="Acme Inc", currency="USD")
        cls.admin = person(
            "alice@acme.io",
            "Alice Admin",
            cls.org,
            role=User.Role.ADMIN,
            function=User.Function.LEADERSHIP,
        )
        cls.csm = person("carl@acme.io", "Carl CSM", cls.org)
        cls.other = person("dana@acme.io", "Dana CSM", cls.org)
        cls.other_org = Organisation.objects.create(name="Globex", currency="USD")
        cls.stranger = person("gus@globex.io", "Gus Globex", cls.other_org)
        cls.pizza = Customer.objects.create(
            organisation=cls.org, name="Pizza Hut", owner=cls.csm, health_score=Decimal("8.0")
        )
        cls.taco = Customer.objects.create(
            organisation=cls.org, name="Taco Bell", owner=cls.other, health_score=Decimal("3.0")
        )
        cls.globex = Customer.objects.create(
            organisation=cls.other_org, name="Globex Corp", owner=cls.stranger
        )
        cls.emea = cls.make_account("Pizza EMEA", cls.pizza, cls.csm)
        cls.west = cls.make_account("Taco West", cls.taco, cls.other)

    @staticmethod
    def make_account(name, customer, owner, **fields):
        account = Account.objects.create(name=name, owner=owner, **fields)
        account.customers.add(customer)
        return account

    def segment(self, *, owner=None, kind="customer", rules=None, **fields):
        return Segment.objects.create(
            organisation=self.org,
            owner=owner or self.csm,
            name=fields.pop("name", "Renewal risk"),
            kind=kind,
            rules=rules or {"match": "all", "conditions": []},
            **fields,
        )
```

Create `services/segments/tests/test_models.py`:

```python
"""The two segment tables and the alert's notification kind."""

from django.db import IntegrityError, transaction

from services.notifications.models import Notification
from services.segments.models import Segment, SegmentChange, default_rules
from services.segments.tests.fixtures import SegmentFixture


class SegmentModelTests(SegmentFixture):
    def test_a_new_segment_is_private_with_no_rules_and_no_history(self):
        segment = self.segment()
        self.assertEqual(segment.rules, {"match": "all", "conditions": []})
        self.assertEqual(
            (segment.sharing, segment.pinned_ids, segment.excluded_ids), ("private", [], [])
        )
        self.assertEqual((segment.alert_on_changes, segment.paused), (False, False))
        self.assertEqual(
            (segment.last_members, segment.member_count, segment.last_evaluated_on),
            (None, None, None),
        )

    def test_default_rules_are_a_fresh_dict_each_time(self):
        self.assertIsNot(default_rules(), default_rules())

    def test_its_string_never_quotes_the_owners_words(self):
        segment = self.segment(name="Accounts Carl is about to lose")
        self.assertEqual(str(segment), f"Segment {segment.pk}")

    def test_one_change_per_record_per_day(self):
        segment = self.segment()
        SegmentChange.objects.create(
            segment=segment, record_id=1, change="entered", changed_on=self.today
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            SegmentChange.objects.create(
                segment=segment, record_id=1, change="left", changed_on=self.today
            )

    def test_deleting_the_owner_deletes_their_segments(self):
        segment = self.segment(owner=self.other)
        self.other.delete()
        self.assertFalse(Segment.objects.filter(pk=segment.pk).exists())

    def test_the_alert_has_its_own_notification_kind(self):
        self.assertEqual(Notification.Kind.SEGMENT_CHANGES, "segment_changes")
        Notification.objects.create(
            recipient=self.csm, kind=Notification.Kind.SEGMENT_CHANGES, message="x: 1 entered"
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.segments --noinput`
Expected: an import error, because there is no module named `services.segments.models`.

- [ ] **Step 3: Write the app and the models**

`services/segments/apps.py`:

```python
from django.apps import AppConfig


class SegmentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "services.segments"
    verbose_name = "Segments"
```

`services/segments/models.py`:

```python
"""Segments: saved, rule-based groups of organisations, accounts or contacts.

A segment stores rules, never members. Membership is computed for whoever
is looking (`evaluate.members_queryset`), so a segment only ever holds
records its viewer may open. The nightly step (`nightly.py`) evaluates each
segment as its owner and records who entered and who left in
`SegmentChange`; `last_members` is what the next run compares against.

Unrelated to `services/customers/segments.py`, the revenue brackets the
metrics call "Size band".
"""

from django.conf import settings
from django.db import models

from services.accounts.models import Organisation

#: How many segments one person may own.
MAX_OWNED = 50
#: How many conditions one segment may hold, counting each inside a group.
MAX_CONDITIONS = 20
#: How many records one segment may pin in, and how many it may keep out.
MAX_PINNED = 500
#: Beyond this many members the nightly step keeps the count but records no
#: entries or exits: the member list would outgrow a row.
MAX_TRACKED_MEMBERS = 100_000


def default_rules():
    return {"match": "all", "conditions": []}


class Segment(models.Model):
    class Kind(models.TextChoices):
        CUSTOMER = "customer", "Organisations"
        ACCOUNT = "account", "Accounts"
        CONTACT = "contact", "Contacts"

    class Sharing(models.TextChoices):
        PRIVATE = "private", "Only me"
        WORKSPACE = "workspace", "Everyone in the workspace"
        PEOPLE = "people", "Chosen teammates"

    organisation = models.ForeignKey(
        Organisation, related_name="segments", on_delete=models.CASCADE
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="segments", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=500, blank=True, default="")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    rules = models.JSONField(
        default=default_rules,
        help_text='{"match": "all"|"any", "conditions": [...]} — see services/segments/registry.py.',
    )
    pinned_ids = models.JSONField(
        default=list, blank=True, help_text="Records kept in, whatever the rules say."
    )
    excluded_ids = models.JSONField(
        default=list, blank=True, help_text="Records kept out, whatever the rules say."
    )
    sharing = models.CharField(max_length=16, choices=Sharing.choices, default=Sharing.PRIVATE)
    shared_with = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name="shared_segments", blank=True
    )
    alert_on_changes = models.BooleanField(default=False)
    paused = models.BooleanField(
        default=False,
        help_text="Set by the nightly step while the owner is inactive; evaluation stops.",
    )
    last_members = models.JSONField(
        null=True,
        blank=True,
        help_text="Sorted member ids at the last nightly evaluation, as the owner saw them. "
        "Null before the first one, and for a segment too large to track.",
    )
    member_count = models.PositiveIntegerField(null=True, blank=True)
    last_evaluated_on = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        indexes = [models.Index(fields=["organisation", "sharing"])]

    def __str__(self):
        # The name is the owner's own words; an audit row carries the id only.
        return f"Segment {self.pk}"


class SegmentChange(models.Model):
    """One record entering or leaving one segment on one day. `reason` holds
    the field keys that changed the match, never their values."""

    class Change(models.TextChoices):
        ENTERED = "entered", "Entered"
        LEFT = "left", "Left"

    segment = models.ForeignKey(Segment, related_name="changes", on_delete=models.CASCADE)
    record_id = models.PositiveBigIntegerField()
    change = models.CharField(max_length=8, choices=Change.choices)
    changed_on = models.DateField()
    reason = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-changed_on", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["segment", "record_id", "changed_on"],
                name="one_segment_change_per_record_per_day",
            )
        ]
        indexes = [models.Index(fields=["segment", "changed_on"])]

    def __str__(self):
        return f"Segment {self.segment_id}: {self.record_id} {self.change} on {self.changed_on}"
```

In `services/notifications/models.py`, add one line to `Notification.Kind`, after `QUESTION_ANSWERED`:

```python
        SEGMENT_CHANGES = "segment_changes", "Segment changes"
```

In `config/settings.py`'s `INSTALLED_APPS`, add `"services.segments",` on the line after `"services.pipelines_portfolio",`.

- [ ] **Step 4: Generate the migrations**

Run:
```bash
venv/bin/python manage.py makemigrations segments
venv/bin/python manage.py makemigrations notifications --name segment_changes_kind
```
Expected: `services/segments/migrations/0001_initial.py` (two models, the M2M, the constraint and both indexes) and `services/notifications/migrations/0003_segment_changes_kind.py` (an `AlterField` on `kind`). Read both files before going on.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.segments services.notifications --noinput`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/segments config/settings.py services/notifications
git commit -m "feat(segments): Segment and SegmentChange models, segment_changes notification kind

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: The field registry and rule validation

**Files:**
- Create: `services/segments/registry.py`, `services/segments/rules.py`
- Test: `services/segments/tests/test_rules.py`

**Interfaces:**
- Consumes: `models.MAX_CONDITIONS`, `SegmentFixture`, `rule`.
- Produces:
  - `registry` constants:
    - value types `NUMBER PERCENT DAYS DATE CHOICE TEXT BOOLEAN OWNER RECORD`;
    - `OPERATORS: dict[str, tuple[str, ...]]`;
    - `ATTRIBUTE_PREFIX = "attr:"`, `PARENT_PREFIX = "parent."`, `UNASSIGNED = "unassigned"`;
    - `KIND_NOUNS`, `PARENT_KINDS = ("customer", "account")`, `PARENT_EXCLUDED`;
    - `FIELDS[kind] -> dict[str, Field]`.
  - `registry.Field(key, label, type, column="", choices=(), record="", optional=False)`, with `.operators` (an `optional` field also takes `is_empty`/`is_not_empty`).
  - `registry` functions:
    - `attribute_field(attribute) -> Field`;
    - `applies(attribute, kind) -> bool`;
    - `attribute_name(key) -> str | None`;
    - `attributes_for(organisation_id, keys) -> dict[str, AIAttribute]` (no query when no key names one);
    - `resolve(kind, key, attributes) -> Field | None`;
    - `resolve_parent(key, attributes) -> Field | None`;
    - `resolve_any(kind, key, attributes) -> Field | None`;
    - `static_field(kind, key) -> Field | None` (no attributes, no query).
  - `rules`:
    - `RuleError(ValueError)`;
    - `leaves(rules)`, which yields every leaf condition with groups flattened;
    - `validate_rules(rules, kind, *, user) -> dict`, which returns a clean deep copy or raises `RuleError` with the 400's text;
    - `present_rules(rules, kind, *, user) -> (rules, labels)`;
    - `NOT_OPEN` (messages by record type).

- [ ] **Step 1: Write the failing tests**

Create `services/segments/tests/test_rules.py`:

```python
"""The registry and the 400s: every field a rule may name, the operators its
type allows, the values each takes, and ids the writer must be able to open.
Then how a stored rule reads to someone who cannot open what it names."""

from services.attributes.models import AIAttribute
from services.customers.models import Product
from services.customers.tests.test_views import blind_to_one_account
from services.segments import registry
from services.segments.rules import NOT_OPEN, RuleError, present_rules, validate_rules
from services.segments.tests.fixtures import SegmentFixture, rule


class RuleFixture(SegmentFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.tier = AIAttribute.objects.create(
            organisation=cls.org,
            name="Tier",
            api_name="tier",
            prompt="Which tier?",
            value_type=AIAttribute.ValueType.PICKLIST,
            picklist_options=["gold", "silver"],
        )
        cls.seats = AIAttribute.objects.create(
            organisation=cls.org,
            name="Seats",
            api_name="seats",
            prompt="How many seats?",
            value_type=AIAttribute.ValueType.NUMBER,
            applies_to_account=True,
        )
        AIAttribute.objects.create(
            organisation=cls.other_org,
            name="Globex only",
            api_name="globex_only",
            prompt="?",
            value_type=AIAttribute.ValueType.TEXT,
        )
        cls.core = Product.objects.create(organisation=cls.org, name="Core")
        cls.their_product = Product.objects.create(organisation=cls.other_org, name="Theirs")

    def check(self, rules, kind="customer", user=None):
        return validate_rules(rules, kind, user=user or self.csm)

    def refused(self, rules, message, kind="customer", user=None):
        with self.assertRaisesMessage(RuleError, message):
            self.check(rules, kind, user)


class ShapeTests(RuleFixture):
    def test_an_empty_rule_list_is_valid(self):
        empty = {"match": "all", "conditions": []}
        self.assertEqual(self.check(empty), empty)

    def test_the_clean_copy_is_not_the_input(self):
        rules = rule("health_score", "gt", 5)
        self.assertIsNot(self.check(rules), rules)

    def test_match_must_be_all_or_any(self):
        self.refused({"match": "most", "conditions": []}, '"match" is "all" or "any".')

    def test_unexpected_keys_and_shapes_are_refused(self):
        self.refused({"match": "all"}, "Rules are")
        self.refused({"match": "all", "conditions": [], "extra": 1}, "Rules are")
        self.refused({"match": "all", "conditions": {}}, '"conditions" is a list.')
        self.refused({"match": "all", "conditions": ["csat"]}, "names a field and an operator")
        self.refused(
            {"match": "all", "conditions": [{"field": "csat_score", "op": "gt", "x": 1}]},
            "names a field and an operator",
        )

    def test_groups_hold_conditions_and_do_not_nest(self):
        group = {
            "group": {"match": "any", "conditions": [{"field": "csat_score", "op": "is_empty"}]}
        }
        self.check({"match": "all", "conditions": [group]})
        self.refused(
            {"match": "all", "conditions": [{"group": {"match": "any", "conditions": []}}]},
            "A group needs at least one condition.",
        )
        nested = {"group": {"match": "any", "conditions": [group]}}
        self.refused({"match": "all", "conditions": [nested]}, "Groups do not nest.")

    def test_at_most_twenty_conditions_counting_inside_groups(self):
        leaf = {"field": "csat_score", "op": "is_empty"}
        group = {"group": {"match": "any", "conditions": [leaf] * 10}}
        self.check({"match": "all", "conditions": [group, *[leaf] * 10]})
        self.refused(
            {"match": "all", "conditions": [group, *[leaf] * 11]},
            "A segment can have at most 20 conditions.",
        )


class FieldTests(RuleFixture):
    def test_unknown_fields_are_refused_per_kind(self):
        self.refused(rule("password", "is", "x"), 'Unknown field "password" for organisations.')
        self.refused(
            rule("ces_percentage", "gt", 1),
            'Unknown field "ces_percentage" for accounts.',
            "account",
        )
        self.refused(
            rule("churned", "is", True), 'Unknown field "churned" for accounts.', "account"
        )
        self.refused(rule("role", "is", "champion"), 'Unknown field "role" for organisations.')
        self.refused(
            rule("parent.health_score", "gt", 1),
            'Unknown field "parent.health_score" for organisations.',
        )

    def test_every_registry_field_accepts_exactly_its_types_operators(self):
        for kind, fields in registry.FIELDS.items():
            for key, field in fields.items():
                self.assertEqual(field.operators, registry.OPERATORS[field.type], (kind, key))
        self.assertEqual(
            set(registry.CUSTOMER_FIELDS),
            {
                "lifecycle_stage", "health_score", "health_category", "csat_score", "nps_score",
                "nps_band", "ces_percentage", "arr", "renewal_date", "owner", "product",
                "seat_use", "open_tickets", "last_touch", "ai_pulse", "csm_pulse", "churned",
                "archived", "created",
            },
        )  # fmt: skip
        self.assertEqual(
            set(registry.ACCOUNT_FIELDS),
            set(registry.CUSTOMER_FIELDS)
            - {"ces_percentage", "product", "seat_use", "churned", "archived"}
            | {"organisation"},
        )
        self.assertEqual(
            set(registry.CONTACT_FIELDS),
            {
                "role",
                "sentiment",
                "status",
                "language",
                "last_contacted",
                "organisation",
                "account",
            },
        )

    def test_an_operator_its_type_does_not_take_is_refused(self):
        self.refused(rule("health_score", "is", 5), '"is" cannot be used with Health score.')
        self.refused(rule("churned", "gt", 1), '"gt" cannot be used with Churned.')
        self.refused(
            rule("lifecycle_stage", "lt", "live"), '"lt" cannot be used with Lifecycle stage.'
        )
        self.refused(rule("owner", "between", [1, 2]), '"between" cannot be used with Owner.')
        self.refused(
            rule("csat_score", "sounds_like", 1), '"sounds_like" cannot be used with CSAT %.'
        )

    def test_values_are_checked_by_type(self):
        cases = (
            (rule("health_score", "gt", "7"), "Health score: a number."),
            (rule("health_score", "gt", True), "Health score: a number."),
            (rule("health_score", "between", [8, 2]), "must not be above the second"),
            (rule("health_score", "between", [2]), "between takes two values."),
            (rule("renewal_date", "within_next", 0), "a number of days from 1 to 3650."),
            (rule("renewal_date", "within_last", 4000), "a number of days from 1 to 3650."),
            (rule("renewal_date", "gt", "next week"), "Renewal date: a date as YYYY-MM-DD."),
            (rule("last_touch", "gt", -1), "a whole number of days."),
            (rule("lifecycle_stage", "is", "asleep"), '"asleep" is not one of its values.'),
            (rule("lifecycle_stage", "in", []), "choose from 1 to 100 values."),
            (rule("churned", "is", "yes"), "Churned: true or false."),
            (rule("owner", "is", "carl"), 'a person\'s id or "unassigned".'),
            (rule("product", "is", "core"), "Product: an id."),
            (rule("csat_score", "is_empty", 1), "CSAT % is empty takes no value."),
        )
        for rules, message in cases:
            with self.subTest(rules=rules):
                self.refused(rules, message)
        self.refused(rule("language", "is", ""), "text of up to 100 characters.", "contact")
        self.check(rule("renewal_date", "between", ["2026-01-01", "2026-12-31"]))
        self.check(rule("csat_score", "between", [50, 50.5]))

    def test_contacts_name_parent_fields(self):
        self.check(rule("parent.health_score", "gt", 6), "contact")
        self.check(rule("parent.ces_percentage", "gt", 50), "contact")
        self.check(rule("parent.attr:tier", "is", "gold"), "contact")
        self.refused(
            rule("parent.organisation", "is", self.pizza.pk),
            'Unknown field "parent.organisation" for contacts.',
            "contact",
        )
        self.refused(
            rule("parent.role", "is", "champion"), 'Unknown field "parent.role"', "contact"
        )


class AttributeTests(RuleFixture):
    def test_an_ai_attribute_is_named_by_api_name_and_typed_by_its_value_type(self):
        self.check(rule("attr:seats", "gt", 10))
        self.check(rule("attr:tier", "in", ["gold", "silver"]))
        self.check(rule("attr:tier", "is_empty"))
        self.refused(rule("attr:seats", "is", 10), '"is" cannot be used with Seats.')
        self.refused(rule("attr:tier", "is", "bronze"), '"bronze" is not one of its values.')

    def test_an_attribute_of_another_workspace_or_kind_reads_as_unknown(self):
        self.refused(rule("attr:globex_only", "is", "x"), 'Unknown field "attr:globex_only"')
        self.refused(
            rule("attr:tier", "is", "gold"), 'Unknown field "attr:tier" for accounts.', "account"
        )
        self.check(rule("attr:seats", "gt", 1), "account")

    def test_attributes_are_read_in_one_query_and_not_at_all_when_unnamed(self):
        with self.assertNumQueries(0):
            self.assertEqual(registry.attributes_for(self.org.pk, ["csat_score"]), {})
        with self.assertNumQueries(1):
            found = registry.attributes_for(self.org.pk, ["attr:tier", "parent.attr:seats"])
        self.assertEqual(set(found), {"tier", "seats"})


class IdTests(RuleFixture):
    def test_an_organisation_must_be_one_the_writer_can_open(self):
        self.check(rule("organisation", "is", self.pizza.pk), "account")
        for missing_or_hidden in (self.taco.pk, self.globex.pk, 999999):
            with self.subTest(id=missing_or_hidden):
                self.refused(
                    rule("organisation", "in", [self.pizza.pk, missing_or_hidden]),
                    NOT_OPEN["customer"],
                    "account",
                )

    def test_blind_to_one_account(self):
        viewer, seen, hidden = blind_to_one_account(self.pizza)
        self.check(rule("account", "is", seen.pk), "contact", viewer)
        self.refused(rule("account", "is", hidden.pk), NOT_OPEN["account"], "contact", viewer)
        self.refused(rule("account", "is", 999999), NOT_OPEN["account"], "contact", viewer)

    def test_people_and_products_come_from_the_writers_workspace(self):
        self.check(rule("owner", "in", [self.other.pk, "unassigned"]))
        self.refused(rule("owner", "is", self.stranger.pk), NOT_OPEN["user"])
        self.check(rule("product", "is", self.core.pk))
        self.refused(rule("product", "is", self.their_product.pk), NOT_OPEN["product"])
        self.check(rule("parent.owner", "is", self.csm.pk), "contact")

    def test_a_null_id_names_nothing_and_is_accepted(self):
        self.check(rule("organisation", "in", [None, self.pizza.pk]), "account")


class PresentTests(RuleFixture):
    def test_the_owner_reads_the_names_of_what_they_can_open(self):
        stored = rule("organisation", "in", [self.pizza.pk])
        rules, labels = present_rules(stored, "account", user=self.csm)
        self.assertEqual(rules, stored)
        self.assertEqual(labels["organisations"], {str(self.pizza.pk): "Pizza Hut"})

    def test_an_id_the_reader_cannot_open_reads_null_and_is_never_named(self):
        stored = {
            "match": "all",
            "conditions": [
                {"field": "organisation", "op": "in", "value": [self.pizza.pk, self.taco.pk]},
                {"field": "owner", "op": "is", "value": self.stranger.pk},
            ],
        }
        rules, labels = present_rules(stored, "account", user=self.other)
        self.assertEqual(rules["conditions"][0]["value"], [None, self.taco.pk])
        self.assertIsNone(rules["conditions"][1]["value"])
        self.assertEqual(labels["organisations"], {str(self.taco.pk): "Taco Bell"})
        self.assertEqual(labels["people"], {})
        self.assertEqual(stored["conditions"][0]["value"], [self.pizza.pk, self.taco.pk])

    def test_people_are_named_from_the_readers_workspace(self):
        _rules, labels = present_rules(
            rule("parent.owner", "in", [self.csm.pk, "unassigned"]), "contact", user=self.other
        )
        self.assertEqual(labels["people"], {str(self.csm.pk): "Carl CSM"})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.segments.tests.test_rules --noinput`
Expected: an import error, because there is no module named `services.segments.registry`.

- [ ] **Step 3: Write the registry**

`services/segments/registry.py`:

```python
"""Which fields a segment rule may name, per kind, and which operators each
takes.

A fixed list, because a saved rule is JSON and a field name in it is a
claim, not a permission (the scenario engine's `CONDITION_ATTRIBUTES` rule).
An AI attribute is named `attr:<api_name>`, as in a scenario condition. A
contacts rule names a field of the contact's own organisation or account as
`parent.<key>`. How each field compiles lives in `compiler.py`; this module
only says what exists and what it accepts.
"""

from dataclasses import dataclass

from services.attributes.models import AIAttribute
from services.customers.models import Contact, Customer
from services.organizations.params import NPS_BANDS

NUMBER = "number"
PERCENT = "percent"
#: Days since a date: "never" is more days than any number.
DAYS = "days"
DATE = "date"
CHOICE = "choice"
TEXT = "text"
BOOLEAN = "boolean"
OWNER = "owner"
#: An organisation, account or product id.
RECORD = "record"

OPERATORS = {
    NUMBER: ("gt", "lt", "between", "is_empty", "is_not_empty"),
    PERCENT: ("gt", "lt", "between", "is_empty", "is_not_empty"),
    DAYS: ("gt", "lt", "between", "is_empty"),
    DATE: ("within_next", "within_last", "gt", "lt", "between", "is_empty", "is_not_empty"),
    CHOICE: ("is", "is_not", "in"),
    TEXT: ("is", "is_not", "in", "is_empty", "is_not_empty"),
    BOOLEAN: ("is",),
    OWNER: ("is", "is_not", "in"),
    RECORD: ("is", "is_not", "in"),
}

ATTRIBUTE_PREFIX = "attr:"
PARENT_PREFIX = "parent."
UNASSIGNED = "unassigned"
KIND_NOUNS = {"customer": "organisations", "account": "accounts", "contact": "contacts"}


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    type: str
    #: The ORM path a plain comparison reads. Empty for the fields
    #: `compiler.py` builds itself (health band, NPS band, ARR, seat use,
    #: tickets, touch, churn, the organisation and account pickers).
    column: str = ""
    choices: tuple = ()
    #: For OWNER and RECORD: what an id in the value names — "user",
    #: "customer", "account" or "product".
    record: str = ""
    #: True for an AI attribute: "not answered yet" is a value worth asking
    #: about, so its type also takes `is_empty` and `is_not_empty`.
    optional: bool = False

    @property
    def operators(self):
        operators = OPERATORS[self.type]
        if self.optional:
            operators += tuple(op for op in ("is_empty", "is_not_empty") if op not in operators)
        return operators


def _fields(*fields):
    return {field.key: field for field in fields}


#: What an organisation and an account both have.
_SHARED = (
    Field(
        "lifecycle_stage",
        "Lifecycle stage",
        CHOICE,
        "lifecycle_stage",
        tuple(Customer.LifecycleStage.values),
    ),
    Field("health_score", "Health score", NUMBER, "health_score"),
    Field("health_category", "Health", CHOICE, choices=tuple(Customer.HealthCategory.values)),
    Field("csat_score", "CSAT %", PERCENT, "csat_score"),
    Field("nps_score", "NPS", NUMBER, "nps_score"),
    Field("nps_band", "NPS band", CHOICE, choices=NPS_BANDS),
    # An organisation's ARR is built in SQL (the mapping, converted); an
    # account's is its own `arr` column, already in the workspace currency.
    Field("arr", "ARR", NUMBER, "arr"),
    Field("renewal_date", "Renewal date", DATE, "renewal_date"),
    Field("owner", "Owner", OWNER, "owner", record="user"),
    Field("open_tickets", "Open tickets", NUMBER),
    Field("last_touch", "Days since last touch", DAYS),
    Field("ai_pulse", "AI pulse", NUMBER, "ai_pulse_value"),
    Field("csm_pulse", "CSM pulse", NUMBER, "csm_pulse_score"),
    Field("created", "Created date", DATE, "created_at__date"),
)

CUSTOMER_FIELDS = _fields(
    *_SHARED,
    Field("ces_percentage", "CES %", PERCENT, "ces_percentage"),
    Field("product", "Product", RECORD, "primary_product_id", record="product"),
    Field("seat_use", "Seat use %", PERCENT),
    Field("churned", "Churned", BOOLEAN),
    Field("archived", "Archived", BOOLEAN, "is_archived"),
)
ACCOUNT_FIELDS = _fields(
    *_SHARED,
    Field("organisation", "Organisation", RECORD, record="customer"),
)
CONTACT_FIELDS = _fields(
    Field("role", "Role", CHOICE, "role", tuple(Contact.Role.values)),
    Field("sentiment", "Sentiment", CHOICE, "sentiment", tuple(Contact.Sentiment.values)),
    Field("status", "Status", CHOICE, "status", tuple(Contact.Status.values)),
    Field("language", "Language", TEXT, "language"),
    Field("last_contacted", "Days since last contacted", DAYS, "last_contacted_at__date"),
    Field("organisation", "Organisation", RECORD, record="customer"),
    Field("account", "Account", RECORD, record="account"),
)
FIELDS = {"customer": CUSTOMER_FIELDS, "account": ACCOUNT_FIELDS, "contact": CONTACT_FIELDS}

#: Where a contact's `parent.` field is read, in this order.
PARENT_KINDS = ("customer", "account")
#: Parent fields a contacts rule may not name: the contact has its own.
PARENT_EXCLUDED = frozenset({"organisation"})

_ATTRIBUTE_TYPES = {
    AIAttribute.ValueType.NUMBER: NUMBER,
    AIAttribute.ValueType.BOOLEAN: BOOLEAN,
    AIAttribute.ValueType.PICKLIST: CHOICE,
    AIAttribute.ValueType.TEXT: TEXT,
}


def attribute_field(attribute):
    return Field(
        key=f"{ATTRIBUTE_PREFIX}{attribute.api_name}",
        label=attribute.name,
        type=_ATTRIBUTE_TYPES[attribute.value_type],
        choices=tuple(attribute.picklist_options or ()),
        optional=True,
    )


def applies(attribute, kind):
    return attribute.applies_to_account if kind == "account" else attribute.applies_to_customer


def attribute_name(key):
    """`attr:tier` and `parent.attr:tier` → "tier"; any other key → None."""
    key = key.removeprefix(PARENT_PREFIX)
    return key.removeprefix(ATTRIBUTE_PREFIX) if key.startswith(ATTRIBUTE_PREFIX) else None


def attributes_for(organisation_id, keys):
    """The workspace's AI attributes the keys name, by api name: one query,
    none when no key names one."""
    names = {name for name in map(attribute_name, keys) if name}
    if not names:
        return {}
    rows = AIAttribute.objects.filter(organisation_id=organisation_id, api_name__in=names)
    return {attribute.api_name: attribute for attribute in rows}


def resolve(kind, key, attributes):
    """The Field `key` names for `kind`, or None. `attributes` is
    `attributes_for`'s answer; an attribute that does not apply to the kind
    reads as unknown."""
    if key.startswith(ATTRIBUTE_PREFIX):
        attribute = attributes.get(key.removeprefix(ATTRIBUTE_PREFIX))
        if attribute is None or not applies(attribute, kind):
            return None
        return attribute_field(attribute)
    return FIELDS[kind].get(key)


def resolve_parent(key, attributes):
    """A contacts rule's `parent.<key>`: the field as an organisation reads
    it, else as an account does."""
    if key in PARENT_EXCLUDED:
        return None
    for parent_kind in PARENT_KINDS:
        field = resolve(parent_kind, key, attributes)
        if field is not None:
            return field
    return None


def resolve_any(kind, key, attributes):
    if kind == "contact" and key.startswith(PARENT_PREFIX):
        return resolve_parent(key.removeprefix(PARENT_PREFIX), attributes)
    return resolve(kind, key, attributes)


def static_field(kind, key):
    """`resolve_any` without the workspace's attributes, which never hold
    ids: enough to find the owner and record fields in stored rules."""
    return resolve_any(kind, key, {})
```

- [ ] **Step 4: Write the validation and presentation**

`services/segments/rules.py`:

```python
"""A segment's rules: checked on the way in, presented on the way out.

`validate_rules` is every 400 the builder can meet. It is written as the
person saving, so an organisation or account id must be one they may open,
and a person or product must be in their workspace. A missing id and a hidden
one read the same, so the error never confirms that a record exists.

`present_rules` is how stored rules read to someone: each id they cannot
open becomes `null`, and `labels` names only what they may see. The
compiler intersects ids with the viewer's own book the same way, so the
rules a viewer reads are exactly the rules they are evaluated by.
"""

import copy
from datetime import date

from services.accounts.models import User
from services.customers.models import Product
from services.customers.scoping import visible_accounts, visible_customers

from . import registry
from .models import MAX_CONDITIONS
from .registry import BOOLEAN, CHOICE, DATE, DAYS, OWNER, RECORD, TEXT, UNASSIGNED

MATCHES = ("all", "any")
MAX_DAYS = 3650
MAX_VALUES = 100
MAX_TEXT = 100
LEAF_KEYS = frozenset({"field", "op", "value"})
SHAPE = 'Rules are {"match": "all" | "any", "conditions": [...]}.'
LEAF = "A condition names a field and an operator."

NOT_OPEN = {
    "customer": "Not an organisation you can open.",
    "account": "Not an account you can open.",
    "product": "Not a product in your workspace.",
    "user": "Not a person in your workspace.",
}
LABEL_GROUPS = {
    "customer": "organisations",
    "account": "accounts",
    "product": "products",
    "user": "people",
}


class RuleError(ValueError):
    """A rule the registry refuses; its message is the 400's text."""


def leaves(rules):
    """Every condition, groups flattened, in order."""
    for condition in (rules or {}).get("conditions") or []:
        if isinstance(condition, dict) and "group" in condition:
            group = condition["group"] if isinstance(condition["group"], dict) else {}
            yield from group.get("conditions") or []
        else:
            yield condition


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _ids_in(value):
    values = value if isinstance(value, list) else [value]
    return {part for part in values if _is_int(part)}


def _check_block(block, *, top):
    if not isinstance(block, dict) or set(block) != {"match", "conditions"}:
        raise RuleError(SHAPE)
    if block["match"] not in MATCHES:
        raise RuleError('"match" is "all" or "any".')
    if not isinstance(block["conditions"], list):
        raise RuleError('"conditions" is a list.')
    if not top and not block["conditions"]:
        raise RuleError("A group needs at least one condition.")
    for condition in block["conditions"]:
        if isinstance(condition, dict) and set(condition) == {"group"}:
            if not top:
                raise RuleError("Groups do not nest.")
            _check_block(condition["group"], top=False)
            continue
        if not (
            isinstance(condition, dict)
            and {"field", "op"} <= set(condition) <= LEAF_KEYS
            and isinstance(condition["field"], str)
            and isinstance(condition["op"], str)
        ):
            raise RuleError(LEAF)


def _scalar(field, value):
    if field.type == DATE:
        try:
            return date.fromisoformat(value)
        except (TypeError, ValueError):
            raise RuleError(f"{field.label}: a date as YYYY-MM-DD.") from None
    if field.type == DAYS:
        if not _is_int(value) or value < 0:
            raise RuleError(f"{field.label}: a whole number of days.")
        return value
    if not _is_number(value):
        raise RuleError(f"{field.label}: a number.")
    return value


def _member(field, value):
    if field.type == CHOICE and value not in field.choices:
        raise RuleError(f'{field.label}: "{value}" is not one of its values.')
    if field.type == TEXT and not (isinstance(value, str) and 0 < len(value) <= MAX_TEXT):
        raise RuleError(f"{field.label}: text of up to {MAX_TEXT} characters.")
    if field.type == BOOLEAN and not isinstance(value, bool):
        raise RuleError(f"{field.label}: true or false.")
    if field.type == OWNER and not (value in (UNASSIGNED, None) or _is_int(value)):
        raise RuleError(f'{field.label}: a person\'s id or "unassigned".')
    if field.type == RECORD and not (value is None or _is_int(value)):
        raise RuleError(f"{field.label}: an id.")


def _check_value(field, op, value):
    if op in ("is_empty", "is_not_empty"):
        if value is not None:
            raise RuleError(f"{field.label} {op.replace('_', ' ')} takes no value.")
        return
    if op in ("within_next", "within_last"):
        if not _is_int(value) or not 1 <= value <= MAX_DAYS:
            raise RuleError(f"{field.label}: a number of days from 1 to {MAX_DAYS}.")
        return
    if op == "between":
        if not isinstance(value, list) or len(value) != 2:
            raise RuleError(f"{field.label}: between takes two values.")
        low, high = (_scalar(field, part) for part in value)
        if low > high:
            raise RuleError(f"{field.label}: the first value must not be above the second.")
        return
    if op in ("gt", "lt"):
        _scalar(field, value)
        return
    if op == "in":
        if not isinstance(value, list) or not 1 <= len(value) <= MAX_VALUES:
            raise RuleError(f"{field.label}: choose from 1 to {MAX_VALUES} values.")
        for part in value:
            _member(field, part)
        return
    _member(field, value)


def _openable(record, ids, user):
    """`{id: name}` for the ids `user` may see named: organisations and
    accounts they may open, products and people in their own workspace."""
    if not ids:
        return {}
    if record == "customer":
        rows = visible_customers(user).filter(pk__in=ids)
    elif record == "account":
        rows = visible_accounts(user).filter(pk__in=ids)
    elif record == "product":
        rows = Product.objects.filter(organisation_id=user.organisation_id, pk__in=ids)
    else:
        rows = User.objects.filter(organisation_id=user.organisation_id, pk__in=ids)
    # SOC2:AUTH-02 named only when the reader may open it, or it is in their workspace
    return dict(rows.order_by().values_list("pk", "name").distinct())


def validate_rules(rules, kind, *, user):
    """`rules` checked against the registry for `kind`, as `user` writes
    them. Returns a clean copy; raises RuleError with the 400's text."""
    _check_block(rules, top=True)
    conditions = list(leaves(rules))
    if len(conditions) > MAX_CONDITIONS:
        raise RuleError(f"A segment can have at most {MAX_CONDITIONS} conditions.")
    attributes = registry.attributes_for(user.organisation_id, [c["field"] for c in conditions])
    wanted = {record: set() for record in NOT_OPEN}
    for condition in conditions:
        field = registry.resolve_any(kind, condition["field"], attributes)
        if field is None:
            raise RuleError(
                f'Unknown field "{condition["field"]}" for {registry.KIND_NOUNS[kind]}.'
            )
        if condition["op"] not in field.operators:
            raise RuleError(f'"{condition["op"]}" cannot be used with {field.label}.')
        _check_value(field, condition["op"], condition.get("value"))
        if field.record:
            wanted[field.record] |= _ids_in(condition.get("value"))
    for record, ids in wanted.items():
        # SOC2:AUTH-02 a missing id and one the writer cannot open read the same
        if ids and len(_openable(record, ids, user)) != len(ids):
            raise RuleError(NOT_OPEN[record])
    return copy.deepcopy(rules)


def _redact(value, openable):
    if isinstance(value, list):
        return [_redact(part, openable) for part in value]
    return None if _is_int(value) and value not in openable else value


def present_rules(rules, kind, *, user):
    """`(rules, labels)` as `user` may read them. Every id they cannot open
    (a person or product outside their workspace included) reads `null` and
    has no label; `labels` is `{"organisations", "accounts", "products",
    "people"}`, each `{"<id>": name}`. At most one query per record type the
    rules name."""
    rules = copy.deepcopy(rules or {})
    refs = {record: set() for record in LABEL_GROUPS}
    targets = []
    for condition in leaves(rules):
        field = registry.static_field(kind, condition.get("field", ""))
        if field is not None and field.record:
            refs[field.record] |= _ids_in(condition.get("value"))
            targets.append((condition, field.record))
    names = {record: _openable(record, ids, user) for record, ids in refs.items()}
    for condition, record in targets:
        condition["value"] = _redact(condition.get("value"), names[record])
    labels = {
        LABEL_GROUPS[record]: {str(pk): name for pk, name in found.items()}
        for record, found in names.items()
    }
    return rules, labels
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.segments.tests.test_rules --noinput`
Expected: PASS. If `test_an_id_the_reader_cannot_open_reads_null_and_is_never_named` fails on `labels["people"]`, check that `_openable("user", …)` filters by the *reader's* `organisation_id`.

- [ ] **Step 6: Commit**

```bash
git add services/segments/registry.py services/segments/rules.py services/segments/tests/test_rules.py
git commit -m "feat(segments): field registry and rule validation with same-reading id refusals

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: The portfolio books accept a segment's members as a scope

**Files:**
- Modify: `services/organizations/book.py` (`filtered_queryset` ~lines 87–105, `load_portfolio` ~lines 265–273)
- Modify: `services/accounts_portfolio/book.py` (`filtered_queryset` ~lines 56–58, `load_portfolio` ~lines 192–199)
- Test: `services/organizations/tests/test_scope.py`, `services/accounts_portfolio/tests/test_scope.py`

**Interfaces:**
- Consumes: nothing new.
- Produces (Task 8):
  - `organizations.book.filtered_queryset(user, params, *, today, scope=None)` and `load_portfolio(user, params, *, today, scope=None)`;
  - `accounts_portfolio.book.filtered_queryset(user, params, *, today, scope=None)` and `load_portfolio(user, params, *, today, scope=None)`.

  `scope` is a queryset of the kind. The book keeps only its rows, and only those the viewer may open. With a scope, the Organizations book does not hide archived or churned rows: the segment decided that already. Every existing caller passes no scope and is unchanged.

- [ ] **Step 1: Write the failing tests**

`services/organizations/tests/test_scope.py`:

```python
"""`scope=`: a saved segment's members as the book. It narrows the visible
book and never widens it; archive and churn are the segment's to decide."""

from services.customers.models import Customer
from services.organizations.book import load_portfolio
from services.organizations.params import parse_params
from services.organizations.tests.fixtures import PortfolioFixture


class SegmentScopeTests(PortfolioFixture):
    def names(self, scope, user=None, **query):
        portfolio = load_portfolio(
            user or self.csm, parse_params(query), today=self.today, scope=scope
        )
        return sorted(entry.customer.name for entry in portfolio.entries)

    def test_a_scope_narrows_the_book(self):
        alpha = self.customer("Alpha")
        self.customer("Beta")
        self.assertEqual(self.names(Customer.objects.filter(pk=alpha.pk)), ["Alpha"])

    def test_a_scope_never_widens_what_the_viewer_may_open(self):
        alpha = self.customer("Alpha")
        hidden = self.customer("Hidden", owner=self.other)
        theirs = self.customer("Theirs", organisation=self.other_org, owner=None)
        scope = Customer.objects.filter(pk__in=[alpha.pk, hidden.pk, theirs.pk])
        self.assertEqual(self.names(scope), ["Alpha"])

    def test_a_scope_keeps_archived_and_churned_rows_it_holds(self):
        self.customer("Old", is_archived=True)
        self.customer("Gone", churn_date=self.today)
        everyone = Customer.objects.filter(organisation=self.org)
        self.assertEqual(self.names(everyone), ["Gone", "Old"])
        self.assertEqual(self.names(None), [])

    def test_the_page_filters_still_narrow_a_scope(self):
        self.customer("Alpha")
        self.customer("Beta")
        everyone = Customer.objects.filter(organisation=self.org)
        self.assertEqual(self.names(everyone, search="alp"), ["Alpha"])
```

`services/accounts_portfolio/tests/test_scope.py`:

```python
"""`scope=` on the Accounts book: narrows, never widens."""

from services.accounts_portfolio.book import load_portfolio
from services.accounts_portfolio.params import parse_params
from services.accounts_portfolio.tests.fixtures import AccountPortfolioFixture
from services.customers.models import Account


class SegmentScopeTests(AccountPortfolioFixture):
    def names(self, scope, **query):
        portfolio = load_portfolio(self.csm, parse_params(query), today=self.today, scope=scope)
        return sorted(entry.account.name for entry in portfolio.entries)

    def test_a_scope_narrows_the_book(self):
        north = self.account("North")
        self.account("South")
        self.assertEqual(self.names(Account.objects.filter(pk=north.pk)), ["North"])

    def test_a_scope_never_widens_what_the_viewer_may_open(self):
        north = self.account("North")
        hidden = self.account("Hidden", customers=[self.taco], owner=self.other)
        self.assertEqual(
            self.names(Account.objects.filter(pk__in=[north.pk, hidden.pk])), ["North"]
        )

    def test_the_page_filters_still_narrow_a_scope(self):
        self.account("North")
        self.account("South")
        self.assertEqual(self.names(Account.objects.all(), search="sou"), ["South"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.organizations.tests.test_scope services.accounts_portfolio.tests.test_scope --noinput`
Expected: FAIL with `TypeError: load_portfolio() got an unexpected keyword argument 'scope'`.

- [ ] **Step 3: Add the keyword**

In `services/organizations/book.py`, replace the start of `filtered_queryset` through the end of the `ids` branch:

```python
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
```

with:

```python
def filtered_queryset(user, params: PortfolioParams, *, today, scope=None):
    """`scope` is a saved segment's members (services.segments): it can only
    narrow the visible book, and it has decided archive and churn already."""
    # SOC2:AUTH-02 record visibility comes first; filters only narrow it
    customers = visible_customers(user)
    if scope is not None:
        customers = customers.filter(pk__in=scope.values("pk"))

    if params.ids is None:
        if scope is None:
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
```

In the same file, change `load_portfolio`'s signature and its call:

```python
def load_portfolio(user, params: PortfolioParams, *, today, scope=None):
```

```python
        with_health_inputs(filtered_queryset(user, params, today=today, scope=scope))
```

In `services/accounts_portfolio/book.py`, replace the first lines of `filtered_queryset`:

```python
def filtered_queryset(user, params: AccountPortfolioParams, *, today):
    # SOC2:AUTH-02 record visibility comes first; filters only narrow it
    accounts = visible_accounts(user)
```

with:

```python
def filtered_queryset(user, params: AccountPortfolioParams, *, today, scope=None):
    """`scope` is a saved segment's members (services.segments): it can only
    narrow the visible book."""
    # SOC2:AUTH-02 record visibility comes first; filters only narrow it
    accounts = visible_accounts(user)
    if scope is not None:
        accounts = accounts.filter(pk__in=scope.values("pk"))
```

and `load_portfolio`:

```python
def load_portfolio(user, params: AccountPortfolioParams, *, today, scope=None):
```

```python
        filtered_queryset(user, params, today=today, scope=scope)
```

- [ ] **Step 4: Run the new tests and both apps' suites**

Run: `venv/bin/python manage.py test services.organizations services.accounts_portfolio services.copilot --noinput`
Expected: PASS. The pinned query counts in both `test_views.py` files are unchanged, because no scope means no new query.

- [ ] **Step 5: Commit**

```bash
git add services/organizations/book.py services/accounts_portfolio/book.py services/organizations/tests/test_scope.py services/accounts_portfolio/tests/test_scope.py
git commit -m "feat(portfolios): load_portfolio takes a scope that only narrows the visible book

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: The compiler, and every organisation field and operator

**Files:**
- Create: `services/segments/compiler.py`
- Test: `services/segments/tests/test_compiler_customers.py`

**Interfaces:**
- Consumes:
  - `registry` (Task 2): `resolve`, `attributes_for`, the type constants, `ATTRIBUTE_PREFIX`, `UNASSIGNED`;
  - `rules.leaves`;
  - `organizations.book.CHURNED`, `NPS_Q`, `health_q`, `renewing_q`; `accounts_portfolio.book.account_renewing_q`;
  - `customers.contact.last_contact_annotation`, `last_account_contact_annotation`;
  - `customers.personal.visible_tickets`;
  - `fx_rates.conversion.rates_for`.
- Produces (Tasks 5, 6, 8, 9):
  - `compile_rules(rules, kind, *, user, today) -> Compiled`;
  - `Compiled(kind, q, annotations, named, conditions)`, with:
    - `.annotate(queryset)`;
    - `.apply(queryset)`, which annotates, filters and adds the organisations' churned/archived defaults;
    - `.named`: a frozenset of field keys;
    - `.conditions`: a tuple of `(keys: tuple[str, ...], q: Q)`, one per top-level condition.
  - `Compiler(kind, *, user, today, cache=None)`, with `.compile(rules)`, `.leaf_q(condition)`, `.annotated(queryset)` and `.cache`;
  - `arr_expression(organisation, rates)`, which the summary tile reuses;
  - `seat_use_expression()`, `open_tickets_expression(user, kind)`, `latest_value(attribute, kind)`, `latest_number(attribute, kind)`, `JsonbTypeof`, `nothing()`.

- [ ] **Step 1: Write the failing tests**

Create `services/segments/tests/test_compiler_customers.py`:

```python
"""Every organisation field with every operator its type takes, against five
organisations whose values differ field by field. One query per case, all in
one test, over data built once.

Alpha, Beta and Gamma are the live book. Delta has churned and Echo is
archived, so they appear only when a rule names `churned` or `archived`."""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from services.attributes.models import AIAttribute, AIAttributeValue
from services.customers.models import Activity, Customer, Product, Ticket
from services.customers.scoping import visible_customers
from services.fx_rates.models import FxRate
from services.segments import registry
from services.segments.compiler import compile_rules
from services.segments.tests.fixtures import SegmentFixture, rule

NAMES = ("Alpha", "Beta", "Gamma", "Delta", "Echo", "Foxtrot")


class CustomerFieldTests(SegmentFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        day = cls.today
        FxRate.objects.create(
            organisation=cls.org, currency="EUR", rate_to_org_currency=Decimal("1.1")
        )
        cls.core = Product.objects.create(organisation=cls.org, name="Core")

        def make(name, **fields):
            return Customer.objects.create(organisation=cls.org, name=name, **fields)

        cls.alpha = make(
            "Alpha", owner=cls.csm, lifecycle_stage="live", health_score=Decimal("8.0"),
            csat_score=Decimal("90"), nps_score=40, ces_percentage=Decimal("80"),
            currency="USD", arr_billed_at_account=Decimal("120000"),
            renewal_date=day + timedelta(days=20), primary_product=cls.core,
            total_contracted_seats=100, total_active_seats=80, ai_pulse_value=5,
            csm_pulse_score=4,
        )  # fmt: skip
        cls.beta = make(
            "Beta", owner=cls.other, lifecycle_stage="onboarding", health_score=Decimal("5.0"),
            csat_score=Decimal("55"), nps_score=0, ces_percentage=Decimal("40"),
            currency="EUR", arr_billed_at_account=Decimal("30000"),
            renewal_date=day - timedelta(days=5), total_contracted_seats=100,
            total_active_seats=10, ai_pulse_value=2, csm_pulse_score=2,
        )  # fmt: skip
        cls.gamma = make(
            "Gamma", owner=None, lifecycle_stage="adoption", health_score=Decimal("2.0"),
            nps_score=-20, currency="USD", arr_billed_at_account=Decimal("5000"),
        )  # fmt: skip
        Customer.objects.filter(pk=cls.gamma.pk).update(
            created_at=timezone.now() - timedelta(days=100)
        )
        make(
            "Delta", owner=cls.csm, lifecycle_stage="churn", churn_date=day - timedelta(days=10),
            health_score=Decimal("9.0"),
        )  # fmt: skip
        make("Echo", owner=cls.csm, is_archived=True, health_score=Decimal("9.0"))

        for title, department, status in (
            ("Down", "", Ticket.Status.OPEN),
            ("Pricing", "sales", Ticket.Status.OPEN),
        ):
            Ticket.objects.create(
                customer=cls.alpha, ticket_number=title, title=title, status=status,
                priority=Ticket.Priority.LOW, opened_at=day, department=department,
            )  # fmt: skip
        Ticket.objects.create(
            customer=cls.beta, ticket_number="Old", title="Old", status=Ticket.Status.RESOLVED,
            priority=Ticket.Priority.LOW, opened_at=day,
        )  # fmt: skip
        for customer, days_ago in ((cls.alpha, 3), (cls.beta, 40)):
            Activity.objects.create(
                customer=customer,
                type=Activity.ActivityType.OTHER,
                occurred_at=day - timedelta(days=days_ago),
            )

        def attribute(api_name, value_type, **fields):
            return AIAttribute.objects.create(
                organisation=cls.org, name=api_name.title(), api_name=api_name,
                prompt="?", value_type=value_type, **fields,
            )  # fmt: skip

        tier = attribute("tier", "picklist", picklist_options=["gold", "silver"])
        seats = attribute("seats", "number")
        sso = attribute("sso", "boolean")
        # Alpha's older answer is silver; the newest row is the value.
        for attr, customer, value in (
            (tier, cls.alpha, "silver"), (tier, cls.alpha, "gold"), (tier, cls.beta, "silver"),
            (seats, cls.alpha, 50), (seats, cls.beta, 5), (sso, cls.alpha, True),
            (sso, cls.beta, False),
            # Gamma's answer was given when "seats" was a text attribute.
            (seats, cls.gamma, "many"),
        ):  # fmt: skip
            AIAttributeValue.objects.create(attribute=attr, customer=customer, value=value)
        AIAttributeValue.objects.create(
            attribute=tier, customer=cls.gamma, value=None, status="insufficient"
        )

    def matched(self, rules, user=None):
        user = user or self.admin
        compiled = compile_rules(rules, "customer", user=user, today=self.today)
        records = visible_customers(user).filter(name__in=NAMES)
        return set(compiled.apply(records).values_list("name", flat=True))

    def cases(self):
        day = self.today

        def iso(days):
            return (day + timedelta(days=days)).isoformat()

        alpha_beta, all3 = {"Alpha", "Beta"}, {"Alpha", "Beta", "Gamma"}
        carl, dana = self.csm.pk, self.other.pk
        return [
            ("lifecycle_stage", "is", "live", {"Alpha"}),
            ("lifecycle_stage", "is_not", "live", {"Beta", "Gamma"}),
            ("lifecycle_stage", "in", ["live", "adoption"], {"Alpha", "Gamma"}),
            ("health_score", "gt", 6, {"Alpha"}),
            ("health_score", "lt", 6, {"Beta", "Gamma"}),
            ("health_score", "between", [4, 8], alpha_beta),
            ("health_score", "is_empty", None, set()),
            ("health_score", "is_not_empty", None, all3),
            ("health_category", "is", "good", {"Alpha"}),
            ("health_category", "is_not", "good", {"Beta", "Gamma"}),
            ("health_category", "in", ["average", "poor"], {"Beta", "Gamma"}),
            ("csat_score", "gt", 60, {"Alpha"}),
            ("csat_score", "lt", 60, {"Beta"}),
            ("csat_score", "between", [50, 95], alpha_beta),
            ("csat_score", "is_empty", None, {"Gamma"}),
            ("csat_score", "is_not_empty", None, alpha_beta),
            ("nps_score", "gt", 0, {"Alpha"}),
            ("nps_score", "lt", 0, {"Gamma"}),
            ("nps_score", "between", [-10, 10], {"Beta"}),
            ("nps_score", "is_empty", None, set()),
            ("nps_score", "is_not_empty", None, all3),
            ("nps_band", "is", "promoter", {"Alpha"}),
            ("nps_band", "is_not", "promoter", {"Beta", "Gamma"}),
            ("nps_band", "in", ["passive", "detractor"], {"Beta", "Gamma"}),
            ("ces_percentage", "gt", 50, {"Alpha"}),
            ("ces_percentage", "lt", 50, {"Beta"}),
            ("ces_percentage", "between", [30, 90], alpha_beta),
            ("ces_percentage", "is_empty", None, {"Gamma"}),
            ("ces_percentage", "is_not_empty", None, alpha_beta),
            # Beta bills 30,000 EUR = 33,000 USD: over 30,000 and not under
            # 31,000, which its raw number would be.
            ("arr", "gt", 30000, alpha_beta),
            ("arr", "lt", 31000, {"Gamma"}),
            ("arr", "between", [4000, 40000], {"Beta", "Gamma"}),
            ("arr", "is_empty", None, set()),
            ("arr", "is_not_empty", None, all3),
            # Overdue counts as within.
            ("renewal_date", "within_next", 30, alpha_beta),
            ("renewal_date", "within_last", 10, {"Beta"}),
            ("renewal_date", "gt", iso(10), {"Alpha"}),
            ("renewal_date", "lt", iso(0), {"Beta"}),
            ("renewal_date", "between", [iso(-10), iso(30)], alpha_beta),
            ("renewal_date", "is_empty", None, {"Gamma"}),
            ("renewal_date", "is_not_empty", None, alpha_beta),
            ("owner", "is", carl, {"Alpha"}),
            ("owner", "is", "unassigned", {"Gamma"}),
            ("owner", "is_not", carl, {"Beta", "Gamma"}),
            ("owner", "in", [dana, "unassigned"], {"Beta", "Gamma"}),
            ("product", "is", self.core.pk, {"Alpha"}),
            ("product", "is_not", self.core.pk, {"Beta", "Gamma"}),
            ("product", "in", [self.core.pk], {"Alpha"}),
            ("seat_use", "gt", 50, {"Alpha"}),
            ("seat_use", "lt", 50, {"Beta"}),
            ("seat_use", "between", [5, 85], alpha_beta),
            ("seat_use", "is_empty", None, {"Gamma"}),
            ("seat_use", "is_not_empty", None, alpha_beta),
            # Alice is Leadership: she reads both of Alpha's open tickets.
            ("open_tickets", "gt", 1, {"Alpha"}),
            ("open_tickets", "lt", 1, {"Beta", "Gamma"}),
            ("open_tickets", "between", [2, 2], {"Alpha"}),
            ("open_tickets", "is_empty", None, set()),
            ("open_tickets", "is_not_empty", None, all3),
            # Gamma was never touched: more days than any number.
            ("last_touch", "gt", 30, {"Beta", "Gamma"}),
            ("last_touch", "lt", 30, {"Alpha"}),
            ("last_touch", "between", [1, 5], {"Alpha"}),
            ("last_touch", "is_empty", None, {"Gamma"}),
            ("ai_pulse", "gt", 3, {"Alpha"}),
            ("ai_pulse", "lt", 3, {"Beta"}),
            ("ai_pulse", "between", [2, 5], alpha_beta),
            ("ai_pulse", "is_empty", None, {"Gamma"}),
            ("ai_pulse", "is_not_empty", None, alpha_beta),
            ("csm_pulse", "gt", 3, {"Alpha"}),
            ("csm_pulse", "lt", 3, {"Beta"}),
            ("csm_pulse", "between", [2, 4], alpha_beta),
            ("csm_pulse", "is_empty", None, {"Gamma"}),
            ("csm_pulse", "is_not_empty", None, alpha_beta),
            ("churned", "is", True, {"Delta"}),
            ("churned", "is", False, all3),
            ("archived", "is", True, {"Echo"}),
            ("archived", "is", False, all3),
            ("created", "within_next", 30, alpha_beta),
            ("created", "within_last", 30, alpha_beta),
            ("created", "gt", iso(-50), alpha_beta),
            ("created", "lt", iso(-50), {"Gamma"}),
            ("created", "between", [iso(-200), iso(-50)], {"Gamma"}),
            ("created", "is_empty", None, set()),
            ("created", "is_not_empty", None, all3),
            ("attr:tier", "is", "gold", {"Alpha"}),
            ("attr:tier", "is_not", "gold", {"Beta", "Gamma"}),
            ("attr:tier", "in", ["gold", "silver"], alpha_beta),
            ("attr:tier", "is_empty", None, {"Gamma"}),
            ("attr:tier", "is_not_empty", None, alpha_beta),
            # Gamma's "many" is not a number, and must not break the cast.
            ("attr:seats", "gt", 10, {"Alpha"}),
            ("attr:seats", "lt", 10, {"Beta"}),
            ("attr:seats", "between", [1, 100], alpha_beta),
            ("attr:seats", "is_empty", None, {"Gamma"}),
            ("attr:seats", "is_not_empty", None, alpha_beta),
            ("attr:sso", "is", True, {"Alpha"}),
            ("attr:sso", "is", False, {"Beta"}),
            ("attr:sso", "is_empty", None, {"Gamma"}),
            ("attr:sso", "is_not_empty", None, alpha_beta),
        ]

    def test_every_field_and_operator(self):
        for field, op, value, expected in self.cases():
            with self.subTest(field=field, op=op, value=value):
                self.assertEqual(self.matched(rule(field, op, value)), expected)

    def test_the_cases_cover_every_field_and_operator(self):
        covered = {(field, op) for field, op, _value, _expected in self.cases()}
        wanted = {
            (key, op) for key, field in registry.CUSTOMER_FIELDS.items() for op in field.operators
        }
        for attribute in AIAttribute.objects.filter(organisation=self.org):
            field = registry.attribute_field(attribute)
            wanted |= {(field.key, op) for op in field.operators}
        self.assertEqual(covered, wanted)

    def test_open_tickets_count_only_what_the_viewer_may_read(self):
        # Carl is Customer Success: Alpha's Sales ticket is not his to count.
        self.assertEqual(self.matched(rule("open_tickets", "between", [1, 1]), self.csm), {"Alpha"})
        self.assertEqual(self.matched(rule("open_tickets", "between", [1, 1])), set())

    def test_arr_follows_the_workspace_mapping(self):
        Customer.objects.filter(pk=self.alpha.pk).update(arr_billed_at_hq=Decimal("200000"))
        organisation = self.admin.organisation
        organisation.global_attributes = {"arr": "arr_billed_at_hq"}
        organisation.save(update_fields=["global_attributes"])
        self.assertEqual(self.matched(rule("arr", "gt", 150000)), {"Alpha"})

    def test_arr_in_a_currency_with_no_rate_never_matches(self):
        Customer.objects.create(
            organisation=self.org, name="Foxtrot", currency="JPY",
            arr_billed_at_account=Decimal("9000000"), owner=self.csm,
        )  # fmt: skip
        self.assertNotIn("Foxtrot", self.matched(rule("arr", "gt", 0)))
        self.assertEqual(self.matched(rule("arr", "is_empty")), {"Foxtrot"})

    def test_groups_and_match_any(self):
        rules = {
            "match": "any",
            "conditions": [
                {"field": "lifecycle_stage", "op": "is", "value": "live"},
                {
                    "group": {
                        "match": "all",
                        "conditions": [
                            {"field": "csat_score", "op": "is_empty"},
                            {"field": "nps_band", "op": "is", "value": "detractor"},
                        ],
                    }
                },
            ],
        }
        self.assertEqual(self.matched(rules), {"Alpha", "Gamma"})
        rules["match"] = "all"
        self.assertEqual(self.matched(rules), set())

    def test_an_empty_rule_list_matches_nothing(self):
        self.assertEqual(self.matched({"match": "all", "conditions": []}), set())

    def test_a_field_or_attribute_that_no_longer_resolves_matches_nothing(self):
        self.assertEqual(self.matched(rule("retired_field", "is", "x")), set())
        self.assertEqual(self.matched(rule("attr:deleted", "is", "x")), set())

    def test_named_and_conditions_are_reported(self):
        rules = {
            "match": "all",
            "conditions": [
                {"field": "churned", "op": "is", "value": True},
                {
                    "group": {
                        "match": "any",
                        "conditions": [
                            {"field": "csat_score", "op": "is_empty"},
                            {"field": "attr:tier", "op": "is", "value": "gold"},
                        ],
                    }
                },
            ],
        }
        compiled = compile_rules(rules, "customer", user=self.admin, today=self.today)
        self.assertEqual(compiled.named, {"churned", "csat_score", "attr:tier"})
        self.assertEqual(
            [keys for keys, _q in compiled.conditions],
            [("churned",), ("csat_score", "attr:tier")],
        )

    def test_compiling_reads_the_rates_and_attributes_once(self):
        rules = {
            "match": "all",
            "conditions": [
                {"field": "arr", "op": "gt", "value": 1},
                {"field": "arr", "op": "lt", "value": 10**9},
                {"field": "attr:tier", "op": "is", "value": "gold"},
            ],
        }
        with self.assertNumQueries(2):
            compile_rules(rules, "customer", user=self.admin, today=self.today)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.segments.tests.test_compiler_customers --noinput`
Expected: an import error, because there is no module named `services.segments.compiler`.

- [ ] **Step 3: Write the compiler**

`services/segments/compiler.py`:

```python
"""A segment's rules as one `Q` over the kind's visible queryset.

`compile_rules` turns the stored JSON into a `Compiled`: the `Q`, the
annotations it reads, and which fields it names. `Compiled.apply` runs over a
queryset the caller has already scoped to what its viewer may open
(`evaluate.visible_records`), so everything a rule reads is the viewer's
own:
- an organisation or account id narrows through `visible_customers` and
  `visible_accounts`;
- tickets are counted under the department rule;
- an AI attribute is read only on records the viewer may open.

Nothing here is a copy:
- the health bands (`health_q`), NPS bands (`NPS_Q`), churn (`CHURNED`) and
  renewal windows (`renewing_q`, `account_renewing_q`) are the Organizations
  and Accounts lists' own;
- last touch is the rubric's `last_contact_annotation`, or the account's
  `last_account_contact_annotation`.

A stored rule naming a field, attribute or id that no longer resolves
matches nothing rather than failing. Rules are checked when written
(`rules.validate_rules`), and the data moves on after that.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from functools import reduce
from operator import and_, or_

from django.db.models import (
    Case,
    CharField,
    Count,
    DecimalField,
    F,
    FloatField,
    Func,
    IntegerField,
    JSONField,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Cast, Coalesce
from django.db.models.lookups import Exact

from services.accounts_portfolio.book import account_renewing_q
from services.attributes.models import AIAttribute, AIAttributeValue
from services.customers.contact import last_account_contact_annotation, last_contact_annotation
from services.customers.models import Ticket
from services.customers.personal import visible_tickets
from services.fx_rates.conversion import rates_for
from services.organizations.book import CHURNED, NPS_Q, health_q, renewing_q

from . import registry
from .registry import DATE, DAYS, OWNER, RECORD, TEXT, UNASSIGNED
from .rules import leaves


def nothing():
    """A condition no record meets."""
    return Q(pk__in=[])


class JsonbTypeof(Func):
    function = "jsonb_typeof"
    output_field = CharField()


def arr_expression(organisation, rates):
    """ARR as the workspace counts it, in the workspace's currency. The
    column is the one its global-attribute mapping names, times the
    admin-maintained rate, as one CASE over the workspace's few currencies:
    no join and no query per row. A currency with no rate is NULL, so it
    never meets an ARR condition and the tile counts it as unconverted. That
    is `convert_to_org_currency`'s rule, in SQL."""
    column = organisation.effective_global_attributes()["arr"]
    output = DecimalField(max_digits=24, decimal_places=6)
    whens = [When(currency=organisation.currency, then=Cast(F(column), output))]
    whens += [
        When(currency=currency, then=Cast(F(column) * Value(rate), output))
        for currency, rate in sorted(rates.items())
        if currency != organisation.currency
    ]
    return Case(*whens, default=None, output_field=output)


def seat_use_expression():
    """`Customer.seat_utilization_percentage` in SQL: active over contracted
    seats, as a percentage. NULL with no contracted seats or no active count."""
    return Case(
        When(
            total_contracted_seats__gt=0,
            total_active_seats__isnull=False,
            then=Cast(F("total_active_seats"), FloatField())
            * Value(100.0)
            / Cast(F("total_contracted_seats"), FloatField()),
        ),
        default=None,
        output_field=FloatField(),
    )


def open_tickets_expression(user, kind):
    """Unresolved tickets filed on the record itself (the rubric's scope)
    that `user` may read under the department rule."""
    parent = "account" if kind == "account" else "customer"
    # SOC2:AUTH-02 counted under the reader's own department rule
    tickets = visible_tickets(user, Ticket.objects.filter(**{parent: OuterRef("pk")}))
    counted = (
        tickets.exclude(status__in=Ticket.RESOLVED_STATUSES)
        .order_by()
        .values(parent)
        .annotate(n=Count("id"))
        .values("n")[:1]
    )
    return Coalesce(Subquery(counted, output_field=IntegerField()), 0)


def latest_value(attribute, kind):
    """The newest answer to `attribute` on the outer record: the row the
    attribute panel shows (newest `computed_at`, then id)."""
    parent = "account" if kind == "account" else "customer"
    rows = (
        AIAttributeValue.objects.filter(attribute=attribute, **{parent: OuterRef("pk")})
        .order_by("-computed_at", "-id")
        .values("value")[:1]
    )
    return Subquery(rows, output_field=JSONField())


def latest_number(attribute, kind):
    """`latest_value` as a number, or NULL when the newest answer is not a
    JSON number. An attribute whose type changed keeps its old answers, and
    casting one of them must not fail the page."""
    return Case(
        When(
            Exact(JsonbTypeof(latest_value(attribute, kind)), Value("number")),
            then=Cast(latest_value(attribute, kind), FloatField()),
        ),
        default=None,
        output_field=FloatField(),
    )


@dataclass
class Compiled:
    kind: str
    q: Q
    annotations: dict
    #: Every field key the rules name, `parent.` prefixes kept.
    named: frozenset
    #: Each top-level condition as `(field keys, Q)`, for a change's reason.
    conditions: tuple

    def annotate(self, queryset):
        return queryset.annotate(**self.annotations) if self.annotations else queryset

    def apply(self, queryset):
        """The records of `queryset` the rules match. Churned and archived
        organisations are left out unless a rule names them, as on the
        Organizations list."""
        queryset = self.annotate(queryset).filter(self.q)
        if self.kind == "customer":
            if "churned" not in self.named:
                queryset = queryset.exclude(CHURNED)
            if "archived" not in self.named:
                queryset = queryset.filter(is_archived=False)
        return queryset


def _combine(match, parts):
    if not parts:
        return nothing()
    return reduce(or_ if match == "any" else and_, parts)


def _choice(op, value, q_for):
    if op == "is":
        return q_for(value)
    if op == "is_not":
        return ~q_for(value)
    if op == "in":
        return _combine("any", [q_for(part) for part in value])
    return nothing()


def _owner(value):
    if value == UNASSIGNED:
        return Q(owner__isnull=True)
    if value is None:
        return nothing()
    return Q(owner_id=value)


def _ids(column, op, value):
    ids = [part for part in (value if op == "in" else [value]) if part is not None]
    hit = Q(**{f"{column}__in": ids})
    return ~hit if op == "is_not" else hit


class Compiler:
    def __init__(self, kind, *, user, today, cache=None):
        self.kind = kind
        self.user = user
        self.today = today
        #: Shared with `parent.` sub-compilers: one FX read, one attribute read.
        self.cache = cache if cache is not None else {}
        self.annotations = {}

    @property
    def organisation(self):
        return self.user.organisation

    @property
    def rates(self):
        if "rates" not in self.cache:
            self.cache["rates"] = rates_for(self.organisation)
        return self.cache["rates"]

    @property
    def attributes(self):
        return self.cache.get("attributes", {})

    def annotated(self, queryset):
        return queryset.annotate(**self.annotations) if self.annotations else queryset

    def compile(self, rules):
        rules = rules or {}
        if "attributes" not in self.cache:
            keys = [condition.get("field", "") for condition in leaves(rules)]
            self.cache["attributes"] = registry.attributes_for(self.user.organisation_id, keys)
        conditions = []
        for condition in rules.get("conditions") or []:
            if "group" in condition:
                group = condition["group"]
                members = group.get("conditions") or []
                q = _combine(group.get("match"), [self.leaf_q(leaf) for leaf in members])
                keys = tuple(leaf.get("field", "") for leaf in members)
            else:
                q = self.leaf_q(condition)
                keys = (condition.get("field", ""),)
            conditions.append((keys, q))
        return Compiled(
            kind=self.kind,
            q=_combine(rules.get("match"), [q for _keys, q in conditions]),
            annotations=dict(self.annotations),
            named=frozenset(key for keys, _q in conditions for key in keys),
            conditions=tuple(conditions),
        )

    def leaf_q(self, condition):
        key, op, value = condition.get("field", ""), condition.get("op"), condition.get("value")
        field = registry.resolve(self.kind, key, self.attributes)
        if field is None or op not in field.operators:
            return nothing()
        return self._field_q(field, op, value)

    def _field_q(self, field, op, value):
        key = field.key
        if key == "health_category":
            return _choice(op, value, health_q)
        if key == "nps_band":
            return _choice(op, value, NPS_Q.__getitem__)
        if key == "churned":
            return CHURNED if value else ~CHURNED
        if key == "renewal_date" and op == "within_next":
            # Overdue counts as within: the lists' own renewal rule.
            renewing = renewing_q if self.kind == "customer" else account_renewing_q
            return renewing(value, today=self.today)
        if field.type == OWNER:
            return _choice(op, value, _owner)
        if field.type == RECORD:
            return _ids(field.column, op, value)
        json = key.startswith(registry.ATTRIBUTE_PREFIX)
        return self._typed(field.type, self._column(field), op, value, json=json)

    def _annotate(self, name, build):
        if name not in self.annotations:
            self.annotations[name] = build()
        return name

    def _column(self, field):
        key = field.key
        if key == "arr" and self.kind == "customer":
            return self._annotate("_seg_arr", lambda: arr_expression(self.organisation, self.rates))
        if key == "seat_use":
            return self._annotate("_seg_seat_use", seat_use_expression)
        if key == "open_tickets":
            return self._annotate(
                "_seg_open_tickets", lambda: open_tickets_expression(self.user, self.kind)
            )
        if key == "last_touch":
            touch = (
                last_contact_annotation
                if self.kind == "customer"
                else last_account_contact_annotation
            )
            return self._annotate("_seg_touch", touch)
        if key.startswith(registry.ATTRIBUTE_PREFIX):
            attribute = self.attributes[key.removeprefix(registry.ATTRIBUTE_PREFIX)]
            if attribute.value_type == AIAttribute.ValueType.NUMBER:
                return self._annotate(
                    f"_seg_attr_number_{attribute.pk}",
                    lambda: latest_number(attribute, self.kind),
                )
            return self._annotate(
                f"_seg_attr_{attribute.pk}", lambda: latest_value(attribute, self.kind)
            )
        return field.column

    def _typed(self, type_, column, op, value, *, json=False):
        if op == "is_empty":
            empty = Q(**{f"{column}__isnull": True})
            return empty | Q(**{column: ""}) if type_ == TEXT else empty
        if op == "is_not_empty":
            present = Q(**{f"{column}__isnull": False})
            return present & ~Q(**{column: ""}) if type_ == TEXT else present
        if type_ == DAYS:
            return self._days(column, op, value)
        if type_ == DATE and op in ("gt", "lt"):
            value = date.fromisoformat(value)
        if type_ == DATE and op == "between":
            value = [date.fromisoformat(part) for part in value]
        if op == "gt":
            return Q(**{f"{column}__gt": value})
        if op == "lt":
            return Q(**{f"{column}__lt": value})
        if op == "between":
            low, high = value
            return Q(**{f"{column}__gte": low, f"{column}__lte": high})
        if op == "within_next":
            end = self.today + timedelta(days=value)
            return Q(**{f"{column}__gte": self.today, f"{column}__lte": end})
        if op == "within_last":
            start = self.today - timedelta(days=value)
            return Q(**{f"{column}__gte": start, f"{column}__lte": self.today})
        if op == "is":
            return Q(**{column: value})
        if op == "is_not":
            # "Is not X" includes "has no value", as a person reads it.
            return ~Q(**{column: value}) | Q(**{f"{column}__isnull": True})
        if op == "in":
            if json:
                return _combine("any", [Q(**{column: part}) for part in value])
            return Q(**{f"{column}__in": list(value)})
        return nothing()

    def _days(self, column, op, value):
        """Days since `column`. Never touched is more days than any number."""

        def ago(days):
            return self.today - timedelta(days=days)

        if op == "gt":
            return Q(**{f"{column}__lt": ago(value)}) | Q(**{f"{column}__isnull": True})
        if op == "lt":
            return Q(**{f"{column}__gt": ago(value)})
        if op == "between":
            low, high = value
            return Q(**{f"{column}__gte": ago(high), f"{column}__lte": ago(low)})
        return nothing()


def compile_rules(rules, kind, *, user, today):
    return Compiler(kind, user=user, today=today).compile(rules)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.segments.tests.test_compiler_customers --noinput`
Expected: PASS. Each failing `subTest` names its field and operator. If one fails:
- For `attr:` cases, print `str(compile_rules(...).apply(...).query)` and check that the subquery orders by `-computed_at, -id`.
- For an `is_not` case, check the `| isnull` arm.

- [ ] **Step 5: Commit**

```bash
git add services/segments/compiler.py services/segments/tests/test_compiler_customers.py
git commit -m "feat(segments): rule compiler over the visible book, every organisation field

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Accounts and contacts: the organisation and account pickers, and `parent.` fields

**Files:**
- Modify: `services/segments/compiler.py` (imports, `Compiler.leaf_q`, `Compiler._field_q`, four new methods)
- Test: `services/segments/tests/test_compiler_accounts_contacts.py`

**Interfaces:**
- Consumes: `Compiler` and `compile_rules` (Task 4); `registry.PARENT_PREFIX`, `PARENT_EXCLUDED` and `resolve` (Task 2); `visible_customers`, `visible_accounts`.
- Produces: `compile_rules(rules, "account" | "contact", …)` handles every field in `ACCOUNT_FIELDS` and `CONTACT_FIELDS`, plus `parent.<key>` for contacts. No new public names.

- [ ] **Step 1: Write the failing tests**

Create `services/segments/tests/test_compiler_accounts_contacts.py`:

```python
"""Every account field and every contact field with every operator its type
takes, plus contacts' `parent.` fields on both kinds of parent. Then the
pickers: an organisation or account id the viewer cannot open names nothing
in their book."""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from services.attributes.models import AIAttribute, AIAttributeValue
from services.customers.models import Account, Activity, Contact, Customer, Ticket
from services.customers.scoping import visible_accounts, visible_children_q
from services.segments import registry
from services.segments.compiler import compile_rules
from services.segments.tests.fixtures import SegmentFixture, rule

ACCOUNTS = ("North", "South", "Lone")
PEOPLE = ("Sam", "Uma", "Tom", "Wes")


class AccountsAndContactsFixture(SegmentFixture):
    """North is under Pizza Hut (Carl's), South under Taco Bell (Dana's), and
    Lone is unowned and linked to both. Sam is a Pizza Hut contact, Uma a
    Pizza EMEA one, Tom a Taco Bell one and Wes a Taco West one."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        day, now = cls.today, timezone.now()
        cls.north = cls.make_account(
            "North", cls.pizza, cls.csm, lifecycle_stage="live", health_score=Decimal("8.0"),
            csat_score=Decimal("90"), nps_score=40, arr=Decimal("50000"),
            renewal_date=day + timedelta(days=20), ai_pulse_value=5, csm_pulse_score=4,
        )  # fmt: skip
        cls.south = cls.make_account(
            "South", cls.taco, cls.other, lifecycle_stage="onboarding",
            health_score=Decimal("5.0"), csat_score=Decimal("55"), nps_score=0,
            arr=Decimal("10000"), renewal_date=day - timedelta(days=5), ai_pulse_value=2,
            csm_pulse_score=2,
        )  # fmt: skip
        cls.lone = cls.make_account(
            "Lone", cls.pizza, None, lifecycle_stage="adoption", health_score=Decimal("2.0"),
            nps_score=-20, arr=Decimal("0"),
        )  # fmt: skip
        cls.lone.customers.add(cls.taco)
        Account.objects.filter(pk=cls.lone.pk).update(created_at=now - timedelta(days=100))
        Ticket.objects.create(
            account=cls.north, ticket_number="T-1", title="Down", status=Ticket.Status.OPEN,
            priority=Ticket.Priority.LOW, opened_at=day,
        )  # fmt: skip
        for account, days_ago in ((cls.north, 3), (cls.south, 40)):
            Activity.objects.create(
                account=account,
                type=Activity.ActivityType.OTHER,
                occurred_at=day - timedelta(days=days_ago),
            )
        plan = AIAttribute.objects.create(
            organisation=cls.org, name="Plan", api_name="plan", prompt="?",
            value_type="picklist", picklist_options=["pro", "free"], applies_to_account=True,
        )  # fmt: skip
        for parent, value in (
            ({"account": cls.north}, "pro"),
            ({"account": cls.south}, "free"),
            ({"customer": cls.pizza}, "pro"),
            ({"account": cls.west}, "pro"),
        ):
            AIAttributeValue.objects.create(attribute=plan, value=value, **parent)

        Customer.objects.filter(pk=cls.pizza.pk).update(ces_percentage=Decimal("80"))
        Customer.objects.filter(pk=cls.taco.pk).update(renewal_date=day + timedelta(days=10))
        Account.objects.filter(pk=cls.emea.pk).update(health_score=Decimal("9.0"))
        Account.objects.filter(pk=cls.west.pk).update(health_score=Decimal("2.0"))

        def contact(name, **fields):
            return Contact.objects.create(name=name, email=f"{name.lower()}@example.com", **fields)

        contact(
            "Sam", customer=cls.pizza, role="champion", sentiment="positive", language="fr",
            last_contacted_at=now - timedelta(days=2),
        )  # fmt: skip
        contact(
            "Uma", account=cls.emea, role="economic_buyer", sentiment="negative",
            status="inactive",
        )  # fmt: skip
        contact(
            "Tom", customer=cls.taco, role="other", sentiment="neutral", language="en",
            last_contacted_at=now - timedelta(days=40),
        )  # fmt: skip
        contact("Wes", account=cls.west, role="champion", sentiment="neutral")

    def accounts(self, rules, user=None):
        user = user or self.admin
        compiled = compile_rules(rules, "account", user=user, today=self.today)
        records = visible_accounts(user).filter(name__in=ACCOUNTS)
        return set(compiled.apply(records).values_list("name", flat=True))

    def contacts(self, rules, user=None):
        user = user or self.admin
        compiled = compile_rules(rules, "contact", user=user, today=self.today)
        records = Contact.objects.filter(visible_children_q(user), name__in=PEOPLE)
        return set(compiled.apply(records).values_list("name", flat=True))

    def iso(self, days):
        return (self.today + timedelta(days=days)).isoformat()


class AccountFieldTests(AccountsAndContactsFixture):
    def cases(self):
        ns, all3 = {"North", "South"}, set(ACCOUNTS)
        sl = {"South", "Lone"}
        carl, dana, pizza, taco = self.csm.pk, self.other.pk, self.pizza.pk, self.taco.pk
        return [
            ("lifecycle_stage", "is", "live", {"North"}),
            ("lifecycle_stage", "is_not", "live", sl),
            ("lifecycle_stage", "in", ["live", "adoption"], {"North", "Lone"}),
            ("health_score", "gt", 6, {"North"}),
            ("health_score", "lt", 6, sl),
            ("health_score", "between", [4, 8], ns),
            ("health_score", "is_empty", None, set()),
            ("health_score", "is_not_empty", None, all3),
            ("health_category", "is", "good", {"North"}),
            ("health_category", "is_not", "good", sl),
            ("health_category", "in", ["average", "poor"], sl),
            ("csat_score", "gt", 60, {"North"}),
            ("csat_score", "lt", 60, {"South"}),
            ("csat_score", "between", [50, 95], ns),
            ("csat_score", "is_empty", None, {"Lone"}),
            ("csat_score", "is_not_empty", None, ns),
            ("nps_score", "gt", 0, {"North"}),
            ("nps_score", "lt", 0, {"Lone"}),
            ("nps_score", "between", [-10, 10], {"South"}),
            ("nps_score", "is_empty", None, set()),
            ("nps_score", "is_not_empty", None, all3),
            ("nps_band", "is", "promoter", {"North"}),
            ("nps_band", "is_not", "promoter", sl),
            ("nps_band", "in", ["passive", "detractor"], sl),
            ("arr", "gt", 20000, {"North"}),
            ("arr", "lt", 20000, sl),
            ("arr", "between", [5000, 60000], ns),
            ("arr", "is_empty", None, set()),
            ("arr", "is_not_empty", None, all3),
            ("renewal_date", "within_next", 30, ns),
            ("renewal_date", "within_last", 10, {"South"}),
            ("renewal_date", "gt", self.iso(10), {"North"}),
            ("renewal_date", "lt", self.iso(0), {"South"}),
            ("renewal_date", "between", [self.iso(-10), self.iso(30)], ns),
            ("renewal_date", "is_empty", None, {"Lone"}),
            ("renewal_date", "is_not_empty", None, ns),
            ("owner", "is", carl, {"North"}),
            ("owner", "is", "unassigned", {"Lone"}),
            ("owner", "is_not", carl, sl),
            ("owner", "in", [dana, "unassigned"], sl),
            ("open_tickets", "gt", 0, {"North"}),
            ("open_tickets", "lt", 1, sl),
            ("open_tickets", "between", [1, 1], {"North"}),
            ("open_tickets", "is_empty", None, set()),
            ("open_tickets", "is_not_empty", None, all3),
            ("last_touch", "gt", 30, sl),
            ("last_touch", "lt", 30, {"North"}),
            ("last_touch", "between", [1, 5], {"North"}),
            ("last_touch", "is_empty", None, {"Lone"}),
            ("ai_pulse", "gt", 3, {"North"}),
            ("ai_pulse", "lt", 3, {"South"}),
            ("ai_pulse", "between", [2, 5], ns),
            ("ai_pulse", "is_empty", None, {"Lone"}),
            ("ai_pulse", "is_not_empty", None, ns),
            ("csm_pulse", "gt", 3, {"North"}),
            ("csm_pulse", "lt", 3, {"South"}),
            ("csm_pulse", "between", [2, 4], ns),
            ("csm_pulse", "is_empty", None, {"Lone"}),
            ("csm_pulse", "is_not_empty", None, ns),
            ("created", "within_next", 30, ns),
            ("created", "within_last", 30, ns),
            ("created", "gt", self.iso(-50), ns),
            ("created", "lt", self.iso(-50), {"Lone"}),
            ("created", "between", [self.iso(-200), self.iso(-50)], {"Lone"}),
            ("created", "is_empty", None, set()),
            ("created", "is_not_empty", None, all3),
            ("organisation", "is", pizza, {"North", "Lone"}),
            ("organisation", "is_not", pizza, {"South"}),
            ("organisation", "in", [taco], sl),
            ("attr:plan", "is", "pro", {"North"}),
            ("attr:plan", "is_not", "pro", sl),
            ("attr:plan", "in", ["pro", "free"], ns),
            ("attr:plan", "is_empty", None, {"Lone"}),
            ("attr:plan", "is_not_empty", None, ns),
        ]

    def test_every_field_and_operator(self):
        for field, op, value, expected in self.cases():
            with self.subTest(field=field, op=op, value=value):
                self.assertEqual(self.accounts(rule(field, op, value)), expected)

    def test_the_cases_cover_every_field_and_operator(self):
        covered = {(field, op) for field, op, _value, _expected in self.cases()}
        wanted = {
            (key, op) for key, field in registry.ACCOUNT_FIELDS.items() for op in field.operators
        }
        plan = registry.attribute_field(AIAttribute.objects.get(api_name="plan"))
        wanted |= {(plan.key, op) for op in plan.operators}
        self.assertEqual(covered, wanted)

    def test_an_organisation_the_viewer_cannot_open_names_nothing(self):
        # Lone is linked to Taco Bell, which Carl cannot open: the picker
        # must not tell him so.
        self.assertEqual(self.accounts(rule("organisation", "in", [self.taco.pk]), self.csm), set())
        self.assertEqual(
            self.accounts(rule("organisation", "is", self.pizza.pk), self.csm), {"North", "Lone"}
        )


class ContactFieldTests(AccountsAndContactsFixture):
    def cases(self):
        pizza, taco, emea, west = self.pizza.pk, self.taco.pk, self.emea.pk, self.west.pk
        return [
            ("role", "is", "champion", {"Sam", "Wes"}),
            ("role", "is_not", "champion", {"Uma", "Tom"}),
            ("role", "in", ["economic_buyer", "other"], {"Uma", "Tom"}),
            ("sentiment", "is", "negative", {"Uma"}),
            ("sentiment", "is_not", "neutral", {"Sam", "Uma"}),
            ("sentiment", "in", ["positive", "negative"], {"Sam", "Uma"}),
            ("status", "is", "inactive", {"Uma"}),
            ("status", "is_not", "inactive", {"Sam", "Tom", "Wes"}),
            ("status", "in", ["active"], {"Sam", "Tom", "Wes"}),
            ("language", "is", "fr", {"Sam"}),
            ("language", "is_not", "fr", {"Uma", "Tom", "Wes"}),
            ("language", "in", ["fr", "en"], {"Sam", "Tom"}),
            ("language", "is_empty", None, {"Uma", "Wes"}),
            ("language", "is_not_empty", None, {"Sam", "Tom"}),
            ("last_contacted", "gt", 30, {"Uma", "Tom", "Wes"}),
            ("last_contacted", "lt", 30, {"Sam"}),
            ("last_contacted", "between", [1, 5], {"Sam"}),
            ("last_contacted", "is_empty", None, {"Uma", "Wes"}),
            ("organisation", "is", pizza, {"Sam", "Uma"}),
            ("organisation", "is_not", pizza, {"Tom", "Wes"}),
            ("organisation", "in", [taco], {"Tom", "Wes"}),
            ("account", "is", emea, {"Uma"}),
            ("account", "is_not", emea, {"Sam", "Tom", "Wes"}),
            ("account", "in", [west], {"Wes"}),
        ]

    def test_every_field_and_operator(self):
        for field, op, value, expected in self.cases():
            with self.subTest(field=field, op=op, value=value):
                self.assertEqual(self.contacts(rule(field, op, value)), expected)

    def test_the_cases_cover_every_field_and_operator(self):
        covered = {(field, op) for field, op, _value, _expected in self.cases()}
        wanted = {
            (key, op) for key, field in registry.CONTACT_FIELDS.items() for op in field.operators
        }
        self.assertEqual(covered, wanted)

    def test_parent_fields_read_the_contacts_own_organisation_or_account(self):
        """`parent.` compiles each field with the organisation and account
        code the tests above cover field by field. These cases pin the
        mechanism: both parents, a field only an organisation has, the
        owner, an AI attribute, a date window and the churn flag."""
        cases = (
            ("parent.health_score", "gt", 6, {"Sam", "Uma"}),
            ("parent.ces_percentage", "gt", 50, {"Sam"}),
            ("parent.owner", "is", self.csm.pk, {"Sam", "Uma"}),
            ("parent.attr:plan", "is", "pro", {"Sam", "Wes"}),
            ("parent.renewal_date", "within_next", 30, {"Tom"}),
            ("parent.churned", "is", False, {"Sam", "Tom"}),
        )
        for field, op, value, expected in cases:
            with self.subTest(field=field, op=op):
                self.assertEqual(self.contacts(rule(field, op, value)), expected)

    def test_a_parent_field_that_does_not_resolve_matches_nothing(self):
        self.assertEqual(self.contacts(rule("parent.organisation", "is", self.pizza.pk)), set())
        self.assertEqual(self.contacts(rule("parent.nonsense", "is", "x")), set())

    def test_ids_the_viewer_cannot_open_name_nothing(self):
        self.assertEqual(self.contacts(rule("organisation", "is", self.taco.pk), self.csm), set())
        self.assertEqual(self.contacts(rule("account", "is", self.west.pk), self.csm), set())
        self.assertEqual(
            self.contacts(rule("organisation", "in", [self.pizza.pk]), self.csm), {"Sam", "Uma"}
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.segments.tests.test_compiler_accounts_contacts --noinput`
Expected: ERROR in the `organisation` and `account` cases (Task 4's generic RECORD path has no column for them, so Django raises `FieldError`), FAIL in the `parent.` cases (they match nothing yet), and both outcomes in the two picker tests. Every other account and contact case already passes on Task 4's generic paths.

- [ ] **Step 3: Add the pickers and `parent.`**

In `services/segments/compiler.py`, change the models import and add the scoping import:

```python
from services.customers.models import Account, Customer, Ticket
from services.customers.personal import visible_tickets
from services.customers.scoping import visible_accounts, visible_customers
```

In `Compiler.leaf_q`, put the parent branch first:

```python
    def leaf_q(self, condition):
        key, op, value = condition.get("field", ""), condition.get("op"), condition.get("value")
        if self.kind == "contact" and key.startswith(registry.PARENT_PREFIX):
            return self._parent(key.removeprefix(registry.PARENT_PREFIX), op, value)
        field = registry.resolve(self.kind, key, self.attributes)
        if field is None or op not in field.operators:
            return nothing()
        return self._field_q(field, op, value)
```

In `Compiler._field_q`, replace:

```python
        if field.type == RECORD:
            return _ids(field.column, op, value)
```

with:

```python
        if key == "organisation":
            return self._organisation(op, value)
        if key == "account":
            return self._account(op, value)
        if field.type == RECORD:
            return _ids(field.column, op, value)
```

Then add these methods at the end of `Compiler`, after `_days` (indented as shown, inside the class):

```text
    def _openable(self, record, op, value):
        ids = [part for part in (value if op == "in" else [value]) if part is not None]
        # SOC2:AUTH-02 an id the viewer cannot open names nothing in their book
        records = visible_customers(self.user) if record == "customer" else visible_accounts(self.user)
        return records.filter(pk__in=ids).values("pk")


    def _organisation(self, op, value):
        """An account linked to the organisation; a contact on it or on one of
        its accounts."""
        openable = self._openable("customer", op, value)
        if self.kind == "account":
            hit = Q(customers__in=openable)
        else:
            hit = Q(customer__in=openable) | Q(account__customers__in=openable)
        return ~hit if op == "is_not" else hit


    def _account(self, op, value):
        hit = Q(account__in=self._openable("account", op, value))
        return ~hit if op == "is_not" else hit


    def _parent(self, key, op, value):
        """A contacts rule on the contact's own organisation or account. The
        condition is compiled for each parent kind that has the field, over
        that kind's records in the workspace, and a contact matches when its
        own parent does. A contact is visible only through a parent its viewer
        may open, so this reads nothing the viewer could not."""
        if key in registry.PARENT_EXCLUDED:
            return nothing()
        organisation_id = self.user.organisation_id
        parents = (
            ("customer", "customer__in", Customer.objects.filter(organisation_id=organisation_id)),
            (
                "account",
                "account__in",
                Account.objects.filter(customers__organisation_id=organisation_id),
            ),
        )
        sides = []
        for parent_kind, relation, records in parents:
            if registry.resolve(parent_kind, key, self.attributes) is None:
                continue
            sub = Compiler(parent_kind, user=self.user, today=self.today, cache=self.cache)
            condition = sub.leaf_q({"field": key, "op": op, "value": value})
            sides.append(Q(**{relation: sub.annotated(records).filter(condition).values("pk")}))
        return _combine("any", sides)
```

- [ ] **Step 4: Run the compiler tests to verify they pass**

Run: `venv/bin/python manage.py test services.segments --noinput`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/segments/compiler.py services/segments/tests/test_compiler_accounts_contacts.py
git commit -m "feat(segments): account and contact fields, pickers scoped to the viewer, parent fields

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Members for the viewer: pins, exclusions, visibility last, `hidden_count`

**Files:**
- Create: `services/segments/evaluate.py`
- Test: `services/segments/tests/test_evaluate.py`

**Interfaces:**
- Consumes: `compile_rules` (Tasks 4–5); `visible_customers`, `visible_accounts`, `visible_children_q`.
- Produces (Tasks 7–9):
  - `MODELS: dict[str, Model]`;
  - `Draft(kind, rules, pinned_ids=[], excluded_ids=[])`, an unsaved segment with `pk = None` and `owner_id = None`;
  - `visible_records(kind, user) -> QuerySet`;
  - `members_queryset(segment, user, *, today) -> QuerySet` (the kind's model);
  - `member_ids(segment, user, *, today) -> list[int]` (sorted);
  - `hidden_count(segment, viewer, *, today) -> int`;
  - `openable_ids(kind, user, ids) -> list[int]` (sorted).

  `segment` is anything with `kind`, `rules`, `pinned_ids` and `excluded_ids` (and `owner`/`owner_id` for `hidden_count`).

- [ ] **Step 1: Write the failing tests**

Create `services/segments/tests/test_evaluate.py`:

```python
"""Members are computed for the viewer: the rules over their own book, pins
added, exclusions removed, visibility last. A shared viewer learns how many
of the owner's members they cannot open, and nothing else about them."""

from datetime import timedelta
from decimal import Decimal

from services.accounts.models import User
from services.customers.models import Account, Contact, Customer
from services.customers.tests.test_views import blind_to_one_account
from services.segments.evaluate import (
    Draft,
    hidden_count,
    member_ids,
    members_queryset,
    openable_ids,
    visible_records,
)
from services.segments.tests.fixtures import SegmentFixture, rule

HEALTHY = rule("health_score", "gt", 0)
NOBODY = rule("health_score", "gt", 100)


class MembershipTests(SegmentFixture):
    def ids(self, draft, user=None):
        return member_ids(draft, user or self.admin, today=self.today)

    def test_the_rules_run_over_the_viewers_own_book(self):
        draft = Draft("customer", HEALTHY)
        self.assertEqual(self.ids(draft), sorted([self.pizza.pk, self.taco.pk]))
        self.assertEqual(self.ids(draft, self.csm), [self.pizza.pk])
        self.assertEqual(self.ids(draft, self.stranger), [self.globex.pk])

    def test_pins_are_added_whatever_the_rules_say(self):
        self.assertEqual(self.ids(Draft("customer", NOBODY, [self.taco.pk])), [self.taco.pk])

    def test_exclusions_win_over_rules_and_pins(self):
        draft = Draft("customer", HEALTHY, [self.taco.pk], [self.taco.pk])
        self.assertEqual(self.ids(draft), [self.pizza.pk])

    def test_a_pinned_churned_organisation_stays_in(self):
        gone = Customer.objects.create(
            organisation=self.org, name="Gone", churn_date=self.today - timedelta(days=1),
            health_score=Decimal("5.0"), owner=self.csm,
        )  # fmt: skip
        self.assertNotIn(gone.pk, self.ids(Draft("customer", HEALTHY)))
        self.assertIn(gone.pk, self.ids(Draft("customer", HEALTHY, [gone.pk])))

    def test_visibility_is_applied_last_so_a_hidden_pin_is_not_the_viewers(self):
        draft = Draft("customer", NOBODY, [self.taco.pk, self.globex.pk])
        self.assertEqual(self.ids(draft, self.csm), [])
        self.assertEqual(self.ids(draft, self.other), [self.taco.pk])

    def test_blind_to_one_account(self):
        viewer, seen, hidden = blind_to_one_account(self.pizza)
        draft = Draft("account", HEALTHY, [hidden.pk])
        self.assertEqual(self.ids(draft, viewer), [seen.pk])

    def test_another_tenant_never_appears(self):
        draft = Draft("customer", HEALTHY, [self.globex.pk])
        self.assertNotIn(self.globex.pk, self.ids(draft))

    def test_contacts_follow_their_parents_visibility(self):
        sam = Contact.objects.create(customer=self.pizza, name="Sam", email="sam@pizza.io")
        tom = Contact.objects.create(account=self.west, name="Tom", email="tom@taco.io")
        draft = Draft("contact", rule("status", "is", "active"))
        self.assertEqual(self.ids(draft, self.csm), [sam.pk])
        self.assertEqual(self.ids(draft, self.other), [tom.pk])

    def test_members_queryset_is_the_kinds_own_model(self):
        members = members_queryset(Draft("account", HEALTHY), self.admin, today=self.today)
        self.assertIs(members.model, Account)


class HiddenCountTests(SegmentFixture):
    def test_a_shared_viewer_counts_the_owners_members_they_cannot_open(self):
        segment = self.segment(owner=self.admin, rules=HEALTHY, sharing="workspace")
        self.assertEqual(member_ids(segment, self.csm, today=self.today), [self.pizza.pk])
        self.assertEqual(hidden_count(segment, self.csm, today=self.today), 1)
        self.assertEqual(hidden_count(segment, self.other, today=self.today), 1)

    def test_hidden_pins_are_counted_not_named(self):
        segment = self.segment(owner=self.csm, rules=NOBODY, pinned_ids=[self.pizza.pk])
        self.assertEqual(member_ids(segment, self.other, today=self.today), [])
        self.assertEqual(hidden_count(segment, self.other, today=self.today), 1)

    def test_the_owner_hides_nothing_from_themselves(self):
        segment = self.segment(owner=self.csm, rules=HEALTHY)
        self.assertEqual(hidden_count(segment, self.csm, today=self.today), 0)

    def test_a_record_the_viewer_sees_but_the_owner_does_not_is_the_viewers(self):
        segment = self.segment(owner=self.csm, rules=HEALTHY, sharing="workspace")
        self.assertEqual(
            member_ids(segment, self.admin, today=self.today), sorted([self.pizza.pk, self.taco.pk])
        )
        self.assertEqual(hidden_count(segment, self.admin, today=self.today), 0)

    def test_it_is_one_query(self):
        segment = self.segment(owner=self.admin, rules=HEALTHY, sharing="workspace")
        viewer = User.objects.get(pk=self.csm.pk)
        # A request has already loaded both people's capabilities and org chart
        # (memoised on each instance); what is left is the count itself.
        visible_records("customer", viewer)
        visible_records("customer", segment.owner)
        with self.assertNumQueries(1):
            self.assertEqual(hidden_count(segment, viewer, today=self.today), 1)


class OpenableIdsTests(SegmentFixture):
    def test_only_what_the_reader_may_open_sorted(self):
        ids = [self.taco.pk, self.pizza.pk, self.globex.pk, 999999]
        self.assertEqual(openable_ids("customer", self.csm, ids), [self.pizza.pk])
        self.assertEqual(
            openable_ids("customer", self.admin, ids), sorted([self.pizza.pk, self.taco.pk])
        )
        with self.assertNumQueries(0):
            self.assertEqual(openable_ids("customer", self.admin, []), [])

    def test_visible_records_per_kind(self):
        self.assertEqual(
            set(visible_records("account", self.csm).values_list("pk", flat=True)), {self.emea.pk}
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.segments.tests.test_evaluate --noinput`
Expected: an import error, because there is no module named `services.segments.evaluate`.

- [ ] **Step 3: Write the evaluation**

`services/segments/evaluate.py`:

```python
"""A segment's members, computed for whoever is looking.

Twice filtered, always: the rules run over the records the viewer may open
(`visible_records`), pins are added, exclusions removed, and visibility is
applied last. So a pin of a record the viewer cannot open is neither shown
nor named. `hidden_count` is the one thing a shared viewer learns about the
rest of the owner's members: how many there are.
"""

from dataclasses import dataclass, field

from django.db.models import Q

from services.customers.models import Account, Contact, Customer
from services.customers.scoping import visible_accounts, visible_children_q, visible_customers

from .compiler import compile_rules

MODELS = {"customer": Customer, "account": Account, "contact": Contact}


@dataclass(frozen=True)
class Draft:
    """An unsaved segment: what the builder's live preview evaluates."""

    kind: str
    rules: dict
    pinned_ids: list = field(default_factory=list)
    excluded_ids: list = field(default_factory=list)
    pk = None
    owner_id = None


def visible_records(kind, user):
    # SOC2:AUTH-02 a segment only ever holds records its viewer may open
    if kind == "customer":
        return visible_customers(user)
    if kind == "account":
        return visible_accounts(user)
    return Contact.objects.filter(visible_children_q(user))


def members_queryset(segment, user, *, today):
    """The segment's members as `user` may see them: the rules over their
    book, pins added, exclusions removed, visibility applied last."""
    compiled = compile_rules(segment.rules, segment.kind, user=user, today=today)
    matched = compiled.apply(visible_records(segment.kind, user))
    chosen = Q(pk__in=matched.values("pk")) | Q(pk__in=list(segment.pinned_ids or []))
    # SOC2:AUTH-02 visibility last: a pin the viewer cannot open is not theirs to see
    return (
        visible_records(segment.kind, user)
        .filter(chosen)
        .exclude(pk__in=list(segment.excluded_ids or []))
    )


def member_ids(segment, user, *, today):
    members = members_queryset(segment, user, today=today)
    return sorted(set(members.order_by().values_list("pk", flat=True)))


def hidden_count(segment, viewer, *, today):
    """How many of the owner's members `viewer` cannot open: one COUNT,
    never an id. Zero for the owner."""
    if segment.owner_id is None or segment.owner_id == viewer.pk:
        return 0
    owners = members_queryset(segment, segment.owner, today=today)
    # SOC2:AUTH-02 counted, never named: the owner's members the viewer cannot open
    return owners.exclude(pk__in=visible_records(segment.kind, viewer).values("pk")).count()


def openable_ids(kind, user, ids):
    """The ids among `ids` that `user` may open, sorted; no query for none."""
    if not ids:
        return []
    # SOC2:AUTH-02 only pins and exclusions the reader may open are shown
    rows = visible_records(kind, user).filter(pk__in=list(ids)).order_by()
    return sorted(set(rows.values_list("pk", flat=True)))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.segments.tests.test_evaluate --noinput`
Expected: PASS. If `test_it_is_one_query` counts more than one, print `connection.queries`: anything besides the single `SELECT COUNT(*)` is a capability or org-chart read that the two warm-up calls should have memoised. Fix the warm-up, never the pin.

- [ ] **Step 5: Commit**

```bash
git add services/segments/evaluate.py services/segments/tests/test_evaluate.py
git commit -m "feat(segments): members for the viewer, pins and exclusions, visibility last, hidden count

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: List, create, read, edit, delete and duplicate

**Files:**
- Create: `services/segments/access.py`, `services/segments/baseline.py`, `services/segments/serializers.py`, `services/segments/payloads.py`, `services/segments/views.py`, `services/segments/urls.py`
- Modify: `config/urls.py` (mount `api/v1/segments/` on the line after `path("api/v1/scenarios/", include("services.scenarios.urls")),`)
- Test: `services/segments/tests/test_views.py`

**Interfaces:**
- Consumes: `member_ids`, `openable_ids` (Task 6); `present_rules`, `validate_rules`, `RuleError` (Task 2); `organizations.rows.person`, `iso`; `core.audit.record`.
- Produces (Tasks 8–10):
  - `access`:
    - `readable_segments(user) -> QuerySet[Segment]`;
    - `get_readable(user, pk) -> Segment` (404 alike for missing and hidden);
    - `get_owned(user, pk) -> Segment` (403 `NOT_OWNER` for a reader who is not the owner);
    - `NOT_OWNER`.
  - `baseline.rebaseline(segment, *, today) -> None`.
  - `serializers.SegmentWriteSerializer` (context `{"request"}`) and `MAX_SHARED = 50`.
  - `payloads`:
    - `segment_payload(segment, viewer) -> dict`;
    - `list_rows(segments, viewer, *, today) -> list[dict]`;
    - `daily_changes(segment_ids, *, since)`;
    - `sparkline(count, end, daily) -> list[int]`;
    - `SPARKLINE_DAYS = 30`.
  - `views`: `SegmentListCreateView`, `SegmentDetailView`, `SegmentDuplicateView` and `LIMIT_REACHED`.
  - The routes `/api/v1/segments/`, `/api/v1/segments/<pk>/` and `/api/v1/segments/<pk>/duplicate/`.

- [ ] **Step 1: Write the failing tests**

Create `services/segments/tests/test_views.py`:

```python
"""The segment endpoints a person reads and writes: list, create, read, edit,
delete and duplicate. Missing and hidden read the same; only the owner
writes; a shared viewer reads the same rules with what they cannot open
redacted."""

import json
from datetime import timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from core.models import AuditEvent
from services.accounts.models import User
from services.segments.models import MAX_OWNED, Segment, SegmentChange
from services.segments.tests.fixtures import SegmentFixture, person, rule

URL = "/api/v1/segments/"
LOW_HEALTH = rule("health_score", "lt", 5)
HEALTHY = rule("health_score", "gt", 0)
NOT_OWNER = {"detail": "Only the segment's owner can change it."}
DETAIL_KEYS = {
    "id", "name", "description", "kind", "rules", "labels", "pinned_ids", "excluded_ids",
    "sharing", "shared_with", "owner", "is_owner", "alert_on_changes", "paused",
    "member_count", "last_evaluated_on", "created_at", "updated_at",
}  # fmt: skip


class Endpoint(SegmentFixture):
    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def events(self, action):
        rows = AuditEvent.objects.filter(action=action).order_by("pk")
        return list(rows.values_list("target_id", "metadata"))

    def count_queries(self, user, path):
        client = APIClient()
        # A fresh user, as a real request loads one: memoised lookups on a
        # reused instance would flatter the count.
        client.force_authenticate(User.objects.get(pk=user.pk))
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(path)
        self.assertEqual(response.status_code, 200, response.content)
        return len(ctx.captured_queries)


class CreateTests(Endpoint):
    def post(self, user, **body):
        return self.api(user).post(
            URL, {"name": "Unwell", "kind": "customer", **body}, format="json"
        )

    def test_requires_authentication(self):
        self.assertEqual(APIClient().get(URL).status_code, 401)
        self.assertEqual(APIClient().post(URL, {}, format="json").status_code, 401)

    def test_create_sets_the_owner_and_the_baseline(self):
        response = self.post(self.admin, rules=LOW_HEALTH, alert_on_changes=True)
        self.assertEqual(response.status_code, 201, response.content)
        body = response.data
        segment = Segment.objects.get(pk=body["id"])
        self.assertEqual((segment.owner, segment.organisation), (self.admin, self.org))
        self.assertEqual(
            (segment.last_members, segment.member_count, segment.last_evaluated_on),
            ([self.taco.pk], 1, self.today),
        )
        self.assertEqual(set(body), DETAIL_KEYS)
        self.assertEqual(
            (body["is_owner"], body["sharing"], body["member_count"], body["alert_on_changes"]),
            (True, "private", 1, True),
        )
        self.assertEqual(self.events("segment.created"), [(str(segment.pk), {"kind": "customer"})])
        self.assertEqual(self.events("segment.shared"), [])
        self.assertFalse(SegmentChange.objects.exists())

    def test_sharing_on_create_is_audited_with_ids_only(self):
        response = self.post(self.csm, sharing="people", shared_with=[self.other.pk])
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(
            self.events("segment.shared"),
            [(str(response.data["id"]), {"sharing": "people", "shared_with": [self.other.pk]})],
        )
        self.assertEqual(response.data["shared_with"], [{"id": self.other.pk, "name": "Dana CSM"}])

    def test_bad_rules_are_a_400_naming_the_problem(self):
        response = self.post(self.csm, rules=rule("password", "is", "x"))
        self.assertEqual(
            (response.status_code, response.data),
            (400, {"rules": ['Unknown field "password" for organisations.']}),
        )
        response = self.post(
            self.csm, kind="account", rules=rule("organisation", "is", self.taco.pk)
        )
        self.assertEqual(response.data, {"rules": ["Not an organisation you can open."]})

    def test_a_kind_is_required(self):
        response = self.api(self.csm).post(URL, {"name": "No kind"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("kind", response.data)

    def test_at_most_fifty_owned(self):
        Segment.objects.bulk_create(
            Segment(organisation=self.org, owner=self.csm, name=f"S{i}", kind="customer")
            for i in range(MAX_OWNED)
        )
        response = self.post(self.csm)
        self.assertEqual(
            (response.status_code, response.data),
            (400, {"detail": "You can own at most 50 segments."}),
        )
        self.assertEqual(self.post(self.admin).status_code, 201)

    def test_sharing_with_people_needs_active_teammates_from_the_workspace(self):
        self.assertEqual(
            self.post(self.csm, sharing="people", shared_with=[]).data,
            {"shared_with": ["Choose at least one teammate."]},
        )
        inactive = person("ivy@acme.io", "Ivy", self.org, is_active=False)
        for outsider in (self.stranger.pk, self.csm.pk, inactive.pk, 999999):
            with self.subTest(outsider=outsider):
                response = self.post(self.csm, sharing="people", shared_with=[outsider])
                self.assertEqual(response.status_code, 400)
                self.assertIn("shared_with", response.data)


class ListTests(Endpoint):
    def names(self, user, **query):
        return [row["name"] for row in self.api(user).get(URL, query).data]

    def test_mine_shared_and_all(self):
        self.segment(owner=self.csm, name="Mine")
        self.segment(owner=self.admin, name="Everyone's", sharing="workspace")
        self.segment(owner=self.other, name="For Carl", sharing="people").shared_with.add(self.csm)
        self.segment(owner=self.other, name="Dana's own")
        self.segment(owner=self.other, name="For Alice", sharing="people").shared_with.add(
            self.admin
        )
        Segment.objects.create(
            organisation=self.other_org, owner=self.stranger, name="Globex", kind="customer",
            sharing="workspace",
        )  # fmt: skip
        self.assertEqual(self.names(self.csm), ["Everyone's", "For Carl", "Mine"])
        self.assertEqual(self.names(self.csm, scope="mine"), ["Mine"])
        self.assertEqual(self.names(self.csm, scope="shared"), ["Everyone's", "For Carl"])
        self.assertEqual(self.names(self.csm, search="carl"), ["For Carl"])
        self.assertEqual(self.names(self.stranger), ["Globex"])

    def test_a_row_counts_today_and_draws_thirty_days(self):
        segment = self.segment(owner=self.csm, member_count=5, last_evaluated_on=self.today)
        record = iter(range(1, 100))
        for days_ago, change, count in ((0, "entered", 2), (0, "left", 1), (3, "entered", 1)):
            for _ in range(count):
                SegmentChange.objects.create(
                    segment=segment, record_id=next(record), change=change,
                    changed_on=self.today - timedelta(days=days_ago),
                )  # fmt: skip
        row = self.api(self.csm).get(URL).data[0]
        self.assertEqual(
            set(row),
            {
                "id", "name", "kind", "owner", "is_owner", "sharing", "paused",
                "member_count", "today", "sparkline", "updated_at",
            },
        )  # fmt: skip
        self.assertEqual(row["today"], {"entered": 2, "left": 1})
        # 5 today, 4 before today's moves, 3 before the entry three days ago.
        self.assertEqual(row["sparkline"], [3] * 26 + [4, 4, 4, 5])

    def test_a_segment_never_evaluated_has_no_sparkline(self):
        self.segment(owner=self.csm)
        row = self.api(self.csm).get(URL).data[0]
        self.assertEqual((row["sparkline"], row["today"]), ([], {"entered": 0, "left": 0}))

    def test_the_query_count_is_pinned(self):
        """Two queries, whatever the number of segments: the segments (owner
        joined, member lists deferred), and every row's last 30 days of
        changes, counted per day."""
        for name in ("A", "B", "C"):
            segment = self.segment(
                owner=self.csm, name=name, member_count=1, last_evaluated_on=self.today
            )
            SegmentChange.objects.create(
                segment=segment, record_id=1, change="entered", changed_on=self.today
            )
        self.assertEqual(self.count_queries(self.csm, URL), 2)


class DetailTests(Endpoint):
    def get(self, user, segment):
        return self.api(user).get(f"{URL}{segment.pk}/")

    def test_the_owner_reads_their_segment(self):
        segment = self.segment(owner=self.csm, rules=HEALTHY)
        body = self.get(self.csm, segment).data
        self.assertEqual(set(body), DETAIL_KEYS)
        self.assertEqual(body["owner"], {"id": self.csm.pk, "name": "Carl CSM"})
        self.assertEqual(body["rules"], HEALTHY)

    def test_a_hidden_segment_reads_like_a_missing_one(self):
        private = self.segment(owner=self.csm)
        theirs = Segment.objects.create(
            organisation=self.other_org, owner=self.stranger, name="G", kind="customer",
            sharing="workspace",
        )  # fmt: skip
        missing = self.api(self.other).get(f"{URL}999999/")
        self.assertEqual(missing.status_code, 404)
        for segment in (private, theirs):
            response = self.get(self.other, segment)
            self.assertEqual((response.status_code, response.data), (404, missing.data))

    def test_rule_values_naming_hidden_records_read_null_and_unnamed(self):
        segment = self.segment(
            owner=self.csm, kind="account", sharing="workspace",
            rules=rule("organisation", "in", [self.pizza.pk]),
        )  # fmt: skip
        carl = self.get(self.csm, segment).data
        self.assertEqual(carl["rules"]["conditions"][0]["value"], [self.pizza.pk])
        self.assertEqual(carl["labels"]["organisations"], {str(self.pizza.pk): "Pizza Hut"})
        dana = self.get(self.other, segment).data
        self.assertEqual(dana["rules"]["conditions"][0]["value"], [None])
        self.assertEqual(dana["labels"]["organisations"], {})
        self.assertNotIn("Pizza Hut", json.dumps(dana))
        self.assertNotIn(str(self.pizza.pk), json.dumps(dana["rules"]))

    def test_pins_of_hidden_records_are_neither_shown_nor_named(self):
        segment = self.segment(
            owner=self.csm, sharing="workspace", pinned_ids=[self.pizza.pk],
            excluded_ids=[self.emea.pk],
        )  # fmt: skip
        self.assertEqual(self.get(self.csm, segment).data["pinned_ids"], [self.pizza.pk])
        self.assertEqual(self.get(self.other, segment).data["pinned_ids"], [])

    def test_the_query_count_is_pinned(self):
        """Two queries for a segment with no ids in its rules and no pins:
        the segment (owner joined) and its teammates. Each record type its
        rules name adds one, and so do pins and keep-outs."""
        segment = self.segment(owner=self.csm, rules=HEALTHY)
        self.assertEqual(self.count_queries(self.csm, f"{URL}{segment.pk}/"), 2)


class WriteTests(Endpoint):
    def patch(self, user, segment, **body):
        return self.api(user).patch(f"{URL}{segment.pk}/", body, format="json")

    def test_the_owner_edits_and_each_change_is_audited_by_field_name(self):
        segment = self.segment(owner=self.csm)
        response = self.patch(self.csm, segment, name="Renamed", description="Why")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["name"], "Renamed")
        self.assertEqual(
            self.events("segment.updated"), [(str(segment.pk), {"fields": ["description", "name"]})]
        )

    def test_a_patch_that_changes_nothing_records_nothing(self):
        segment = self.segment(owner=self.csm, name="Same")
        self.assertEqual(self.patch(self.csm, segment, name="Same").status_code, 200)
        self.assertEqual(self.events("segment.updated"), [])

    def test_changing_the_rules_starts_the_history_again(self):
        segment = self.segment(
            owner=self.admin, rules=LOW_HEALTH, last_members=[self.taco.pk], member_count=1,
            last_evaluated_on=self.today - timedelta(days=3),
        )  # fmt: skip
        self.assertEqual(self.patch(self.admin, segment, rules=HEALTHY).status_code, 200)
        segment.refresh_from_db()
        self.assertEqual(segment.last_members, sorted([self.pizza.pk, self.taco.pk]))
        self.assertEqual(segment.last_evaluated_on, self.today)
        self.assertFalse(SegmentChange.objects.exists())

    def test_sharing_changes_are_audited_as_shared(self):
        segment = self.segment(owner=self.csm)
        self.patch(self.csm, segment, sharing="workspace")
        self.patch(self.csm, segment, sharing="people", shared_with=[self.other.pk])
        self.patch(self.csm, segment, sharing="private")
        self.assertEqual(
            [metadata for _target, metadata in self.events("segment.shared")],
            [
                {"sharing": "workspace", "shared_with": []},
                {"sharing": "people", "shared_with": [self.other.pk]},
                {"sharing": "private", "shared_with": []},
            ],
        )
        self.assertFalse(segment.shared_with.exists())

    def test_the_kind_cannot_change(self):
        segment = self.segment(owner=self.csm)
        response = self.patch(self.csm, segment, kind="account")
        self.assertEqual(
            (response.status_code, response.data),
            (400, {"kind": ["A segment's kind cannot change."]}),
        )

    def test_non_owners_cannot_write(self):
        shared = self.segment(owner=self.csm, name="Shared", sharing="workspace")
        private = self.segment(owner=self.csm, name="Private")
        dana = self.api(self.other)
        for target, expected in ((shared, (403, NOT_OWNER)), (private, (404, None))):
            path = f"{URL}{target.pk}/"
            for response in (dana.patch(path, {"name": "x"}, format="json"), dana.delete(path)):
                with self.subTest(target=target.name, method=response.request["REQUEST_METHOD"]):
                    self.assertEqual(response.status_code, expected[0])
                    if expected[1] is not None:
                        self.assertEqual(response.data, expected[1])
        self.assertEqual(
            sorted(Segment.objects.values_list("name", flat=True)), ["Private", "Shared"]
        )

    def test_the_owner_deletes(self):
        segment = self.segment(owner=self.csm)
        response = self.api(self.csm).delete(f"{URL}{segment.pk}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Segment.objects.exists())
        self.assertEqual(self.events("segment.deleted"), [(str(segment.pk), {"kind": "customer"})])


class DuplicateTests(Endpoint):
    def duplicate(self, user, segment):
        return self.api(user).post(f"{URL}{segment.pk}/duplicate/")

    def test_a_reader_duplicates_into_their_own_without_what_they_cannot_open(self):
        source = self.segment(
            owner=self.csm, kind="account", sharing="workspace", alert_on_changes=True,
            description="Carl's", rules=rule("organisation", "in", [self.pizza.pk]),
            pinned_ids=[self.emea.pk],
        )  # fmt: skip
        response = self.duplicate(self.other, source)
        self.assertEqual(response.status_code, 201, response.content)
        copy = Segment.objects.get(pk=response.data["id"])
        self.assertEqual(copy.owner, self.other)
        self.assertEqual((copy.name, copy.description), ("Renewal risk (copy)", "Carl's"))
        self.assertEqual((copy.sharing, copy.alert_on_changes), ("private", False))
        self.assertEqual(copy.rules["conditions"][0]["value"], [None])
        self.assertEqual(copy.pinned_ids, [])
        self.assertEqual(copy.last_evaluated_on, self.today)
        self.assertEqual(self.events("segment.duplicated"), [(str(copy.pk), {"source": source.pk})])

    def test_a_segment_the_caller_cannot_read_cannot_be_duplicated(self):
        private = self.segment(owner=self.csm)
        self.assertEqual(self.duplicate(self.other, private).status_code, 404)

    def test_duplicating_counts_towards_the_limit(self):
        source = self.segment(owner=self.csm, sharing="workspace")
        Segment.objects.bulk_create(
            Segment(organisation=self.org, owner=self.other, name=f"S{i}", kind="customer")
            for i in range(MAX_OWNED)
        )
        response = self.duplicate(self.other, source)
        self.assertEqual(
            (response.status_code, response.data),
            (400, {"detail": "You can own at most 50 segments."}),
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.segments.tests.test_views --noinput`
Expected: FAIL. Every request answers 404, because the URLs are not mounted.

- [ ] **Step 3: Write access, baseline, serializer and payloads**

`services/segments/access.py`:

```python
"""Who may read a segment, and who may change it."""

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied

from .models import Segment

NOT_OWNER = "Only the segment's owner can change it."


def readable_segments(user):
    """Segments `user` may read: their own, those shared with the workspace,
    and those shared with them by name. Inside their own workspace only."""
    # SOC2:AUTH-02 a segment is read by its owner and whoever it is shared with
    return (
        Segment.objects.filter(organisation_id=user.organisation_id)
        .filter(
            Q(owner=user)
            | Q(sharing=Segment.Sharing.WORKSPACE)
            | Q(sharing=Segment.Sharing.PEOPLE, shared_with=user)
        )
        .distinct()
    )


def get_readable(user, pk):
    """The segment, or a 404 that reads the same for a missing segment and
    for one `user` may not read."""
    return get_object_or_404(readable_segments(user).select_related("owner"), pk=pk)


def get_owned(user, pk):
    segment = get_readable(user, pk)
    # SOC2:AUTH-02 only the owner edits, deletes, pins or keeps out
    if segment.owner_id != user.pk:
        raise PermissionDenied(NOT_OWNER)
    return segment
```

`services/segments/baseline.py`:

```python
"""Where a segment's change history starts."""

from .evaluate import member_ids
from .models import MAX_TRACKED_MEMBERS


def rebaseline(segment, *, today):
    """Start the history afresh from today's members, as the owner sees them.
    Called when the owner changes what the segment means (create, rules,
    pins, keep-outs, duplicate) and when a paused segment resumes, so the
    history records the data moving, never an edit."""
    ids = member_ids(segment, segment.owner, today=today)
    segment.member_count = len(ids)
    segment.last_members = ids if len(ids) <= MAX_TRACKED_MEMBERS else None
    segment.last_evaluated_on = today
    segment.save(update_fields=["member_count", "last_members", "last_evaluated_on"])
```

`services/segments/serializers.py`:

```python
from rest_framework import serializers

from services.accounts.models import User

from .models import Segment, default_rules
from .rules import RuleError, validate_rules

#: How many teammates one segment may be shared with by name.
MAX_SHARED = 50
NO_TEAMMATES = "Choose at least one teammate."


class SegmentWriteSerializer(serializers.ModelSerializer):
    """Create and edit. Pins and keep-outs have their own endpoint; the
    owner and workspace come from the request, never the body."""

    shared_with = serializers.PrimaryKeyRelatedField(
        many=True, queryset=User.objects.none(), required=False
    )

    class Meta:
        model = Segment
        fields = [
            "name",
            "description",
            "kind",
            "rules",
            "sharing",
            "shared_with",
            "alert_on_changes",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = self.context["request"].user
        # SOC2:AUTH-02 shared only with active teammates in the owner's workspace
        self.fields["shared_with"].child_relation.queryset = User.objects.filter(
            organisation_id=user.organisation_id, is_active=True
        ).exclude(pk=user.pk)

    def validate(self, attrs):
        instance = self.instance
        if instance is not None and attrs.get("kind", instance.kind) != instance.kind:
            raise serializers.ValidationError({"kind": ["A segment's kind cannot change."]})
        kind = instance.kind if instance is not None else attrs["kind"]
        if instance is None or "rules" in attrs:
            try:
                attrs["rules"] = validate_rules(
                    attrs.get("rules", default_rules()), kind, user=self.context["request"].user
                )
            except RuleError as exc:
                raise serializers.ValidationError({"rules": [str(exc)]}) from exc
        default_sharing = instance.sharing if instance is not None else Segment.Sharing.PRIVATE
        if attrs.get("sharing", default_sharing) == Segment.Sharing.PEOPLE:
            if "shared_with" in attrs:
                people = attrs["shared_with"]
            else:
                people = list(instance.shared_with.all()) if instance is not None else []
            if not people:
                raise serializers.ValidationError({"shared_with": [NO_TEAMMATES]})
            if len(people) > MAX_SHARED:
                raise serializers.ValidationError(
                    {"shared_with": [f"Share with at most {MAX_SHARED} teammates."]}
                )
        elif "sharing" in attrs or "shared_with" in attrs:
            attrs["shared_with"] = []
        return attrs
```

`services/segments/payloads.py`:

```python
"""A segment as the API returns it: the full record, and the list's row."""

from collections import defaultdict
from datetime import timedelta

from django.db.models import Count

from services.organizations.rows import iso, person

from .evaluate import openable_ids
from .models import SegmentChange
from .rules import present_rules

#: The list row's size sparkline, in days.
SPARKLINE_DAYS = 30


def segment_payload(segment, viewer):
    """The segment as `viewer` may read it: rules with what they cannot open
    redacted, labels for what they can, and only the pins and keep-outs
    they may open."""
    rules, labels = present_rules(segment.rules, segment.kind, user=viewer)
    # SOC2:AUTH-02 teammates are named only from the viewer's own workspace
    teammates = segment.shared_with.filter(organisation_id=viewer.organisation_id).order_by(
        "name", "pk"
    )
    return {
        "id": segment.pk,
        "name": segment.name,
        "description": segment.description,
        "kind": segment.kind,
        "rules": rules,
        "labels": labels,
        "pinned_ids": openable_ids(segment.kind, viewer, segment.pinned_ids),
        "excluded_ids": openable_ids(segment.kind, viewer, segment.excluded_ids),
        "sharing": segment.sharing,
        "shared_with": [{"id": user.pk, "name": user.name} for user in teammates],
        "owner": person(segment.owner),
        "is_owner": segment.owner_id == viewer.pk,
        "alert_on_changes": segment.alert_on_changes,
        "paused": segment.paused,
        "member_count": segment.member_count,
        "last_evaluated_on": iso(segment.last_evaluated_on),
        "created_at": iso(segment.created_at),
        "updated_at": iso(segment.updated_at),
    }


def daily_changes(segment_ids, *, since):
    """`{segment_id: {date: (entered, left)}}` from `since` on, in one query."""
    daily = defaultdict(dict)
    rows = (
        SegmentChange.objects.filter(segment_id__in=segment_ids, changed_on__gte=since)
        .order_by()
        .values("segment_id", "changed_on", "change")
        .annotate(n=Count("id"))
    )
    for row in rows:
        entered, left = daily[row["segment_id"]].get(row["changed_on"], (0, 0))
        if row["change"] == SegmentChange.Change.ENTERED:
            entered = row["n"]
        else:
            left = row["n"]
        daily[row["segment_id"]][row["changed_on"]] = (entered, left)
    return daily


def sparkline(count, end, daily):
    """The size on each of the `SPARKLINE_DAYS` days ending `end`, oldest
    first. It is rebuilt backwards from `count` (the size on `end`) and each
    day's entries and exits, so no daily copy of the members is ever kept."""
    if count is None or end is None:
        return []
    sizes = [count]
    for offset in range(SPARKLINE_DAYS - 1):
        entered, left = daily.get(end - timedelta(days=offset), (0, 0))
        sizes.append(sizes[-1] - entered + left)
    return sizes[::-1]


def list_rows(segments, viewer, *, today):
    """The list's rows. Counts are the owner's nightly figures and name
    nobody: the same kind of number as `hidden_count`."""
    segments = list(segments)
    daily = daily_changes(
        [segment.pk for segment in segments], since=today - timedelta(days=SPARKLINE_DAYS)
    )
    rows = []
    for segment in segments:
        days = daily.get(segment.pk, {})
        entered, left = days.get(today, (0, 0))
        rows.append(
            {
                "id": segment.pk,
                "name": segment.name,
                "kind": segment.kind,
                "owner": person(segment.owner),
                "is_owner": segment.owner_id == viewer.pk,
                "sharing": segment.sharing,
                "paused": segment.paused,
                "member_count": segment.member_count,
                "today": {"entered": entered, "left": left},
                "sparkline": sparkline(segment.member_count, segment.last_evaluated_on, days),
                "updated_at": iso(segment.updated_at),
            }
        )
    return rows
```

- [ ] **Step 4: Write the views and routes**

`services/segments/views.py`:

```python
"""The segment endpoints. Every read is computed for the person asking;
only the owner writes. See docs/API_CONTRACTS.md, `segments`."""

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core import audit

from .access import get_owned, get_readable, readable_segments
from .baseline import rebaseline
from .evaluate import openable_ids
from .models import MAX_OWNED, Segment
from .payloads import list_rows, segment_payload
from .rules import present_rules
from .serializers import SegmentWriteSerializer

LIMIT_REACHED = f"You can own at most {MAX_OWNED} segments."
SHARING_FIELDS = frozenset({"sharing", "shared_with"})


def _limit_reached(user):
    return Segment.objects.filter(owner=user).count() >= MAX_OWNED


def _changed(segment, data):
    """The names of the fields `data` would change, never their values."""
    changed = []
    for name, value in data.items():
        if name == "shared_with":
            before = set(segment.shared_with.values_list("pk", flat=True))
            if {user.pk for user in value} != before:
                changed.append(name)
        elif getattr(segment, name) != value:
            changed.append(name)
    return sorted(changed)


def _record_shared(request, segment):
    audit.record(  # SOC2:LOG-01
        "segment.shared",
        request=request,
        target=segment,
        metadata={
            "sharing": segment.sharing,
            "shared_with": sorted(segment.shared_with.values_list("pk", flat=True)),
        },
    )


class SegmentListCreateView(APIView):
    """GET /api/v1/segments/?scope=mine|shared|all&search= — the segments the
    caller may read, by name. POST — a new segment, owned by the caller."""

    # SOC2:AUTH-02 authentication here; `readable_segments` scopes every read
    permission_classes = [IsAuthenticated]

    def get(self, request):
        segments = readable_segments(request.user)
        scope = request.query_params.get("scope")
        if scope == "mine":
            segments = segments.filter(owner=request.user)
        elif scope == "shared":
            segments = segments.exclude(owner=request.user)
        search = (request.query_params.get("search") or "").strip()
        if search:
            segments = segments.filter(name__icontains=search)
        segments = (
            segments.select_related("owner")
            .defer("rules", "pinned_ids", "excluded_ids", "last_members")
            .order_by("name", "pk")
        )
        return Response(list_rows(segments, request.user, today=timezone.localdate()))

    def post(self, request):
        if _limit_reached(request.user):
            return Response({"detail": LIMIT_REACHED}, status=status.HTTP_400_BAD_REQUEST)
        serializer = SegmentWriteSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            segment = serializer.save(
                owner=request.user, organisation_id=request.user.organisation_id
            )
            rebaseline(segment, today=timezone.localdate())
            audit.record(  # SOC2:LOG-01
                "segment.created", request=request, target=segment, metadata={"kind": segment.kind}
            )
            if segment.sharing != Segment.Sharing.PRIVATE:
                _record_shared(request, segment)
        return Response(segment_payload(segment, request.user), status=status.HTTP_201_CREATED)


class SegmentDetailView(APIView):
    """GET/PATCH/DELETE /api/v1/segments/<id>/. Readers read; only the owner
    writes (403 for another reader, 404 for anyone who may not read it)."""

    # SOC2:AUTH-02 authentication here; `get_readable`/`get_owned` check the segment
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        return Response(segment_payload(get_readable(request.user, pk), request.user))

    def patch(self, request, pk):
        segment = get_owned(request.user, pk)
        serializer = SegmentWriteSerializer(
            segment, data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        changed = _changed(segment, serializer.validated_data)
        with transaction.atomic():
            segment = serializer.save()
            if "rules" in changed:
                rebaseline(segment, today=timezone.localdate())
            if changed:
                audit.record(  # SOC2:LOG-01
                    "segment.updated", request=request, target=segment, metadata={"fields": changed}
                )
            if SHARING_FIELDS & set(changed):
                _record_shared(request, segment)
        return Response(segment_payload(segment, request.user))

    def delete(self, request, pk):
        segment = get_owned(request.user, pk)
        with transaction.atomic():
            audit.record(  # SOC2:LOG-01
                "segment.deleted", request=request, target=segment, metadata={"kind": segment.kind}
            )
            segment.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SegmentDuplicateView(APIView):
    """POST /api/v1/segments/<id>/duplicate/ — a private copy owned by the
    caller. Its rules are as the caller reads them (ids they cannot open are
    null), and it keeps only the pins and keep-outs they may open."""

    # SOC2:AUTH-02 authentication here; `get_readable` checks the source
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        source = get_readable(request.user, pk)
        if _limit_reached(request.user):
            return Response({"detail": LIMIT_REACHED}, status=status.HTTP_400_BAD_REQUEST)
        rules, _labels = present_rules(source.rules, source.kind, user=request.user)
        with transaction.atomic():
            duplicate = Segment.objects.create(
                organisation_id=request.user.organisation_id,
                owner=request.user,
                name=f"{source.name} (copy)"[:120],
                description=source.description,
                kind=source.kind,
                rules=rules,
                pinned_ids=openable_ids(source.kind, request.user, source.pinned_ids),
                excluded_ids=openable_ids(source.kind, request.user, source.excluded_ids),
            )
            rebaseline(duplicate, today=timezone.localdate())
            audit.record(  # SOC2:LOG-01
                "segment.duplicated",
                request=request,
                target=duplicate,
                metadata={"source": source.pk},
            )
        return Response(segment_payload(duplicate, request.user), status=status.HTTP_201_CREATED)
```

`services/segments/urls.py`:

```python
from django.urls import path

from . import views

urlpatterns = [
    path("", views.SegmentListCreateView.as_view(), name="segment-list"),
    path("<int:pk>/", views.SegmentDetailView.as_view(), name="segment-detail"),
    path("<int:pk>/duplicate/", views.SegmentDuplicateView.as_view(), name="segment-duplicate"),
]
```

In `config/urls.py`, on the line after `path("api/v1/scenarios/", include("services.scenarios.urls")),`:

```text
    path("api/v1/segments/", include("services.segments.urls")),
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.segments.tests.test_views --noinput`
Expected: PASS. If a pinned count is off, print `[q["sql"] for q in ctx.captured_queries]`:
- The list must be exactly the segments query and the changes query.
- The detail must be exactly the segment and its teammates.

An extra query is a bug to fix, not a number to bump.

- [ ] **Step 6: Commit**

```bash
git add services/segments config/urls.py
git commit -m "feat(segments): list, create, read, edit, delete and duplicate, owner-only writes, audited

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Members, tiles, CSV export, the builder's preview, and pin or keep out

**Files:**
- Create: `services/segments/summary.py`, `services/segments/export.py`, `services/segments/members.py`
- Modify: `services/segments/serializers.py` (append two serializers), `services/segments/views.py` (imports; four views appended), `services/segments/urls.py` (four routes)
- Test: `services/segments/tests/test_members.py`

**Interfaces:**
- Consumes:
  - `members_queryset`, `hidden_count`, `visible_records`, `openable_ids` and `Draft` (Task 6);
  - `get_readable`, `get_owned` and `rebaseline` (Task 7);
  - `arr_expression` (Task 4);
  - the scoped `load_portfolio` (Task 3);
  - `contact_list.filtered_contacts`, `parse_contact_filters` and `contacts_summary`;
  - `ContactSerializer`;
  - `portfolio_core.shape.keyset_page`, `rank_of` and `fingerprint`;
  - `organizations.export.CSVRenderer` and `cell`.
- Produces:
  - `summary.segment_summary(members, segment, viewer, *, today, rates=None) -> dict`, with keys:
    - `members`, `arr`, `unconverted_count`, `avg_health`, `avg_csat`;
    - `entered_7d`, `left_7d`, `currency`;
    - plus `contacts` on a contacts segment.
  - `summary.recent_changes(segment, viewer, *, today, days=7)`.
  - `members`:
    - `members_listing(segment, viewer, query, *, today) -> dict`;
    - `members_table(segment, viewer, query, *, today) -> (rows, count)`;
    - `preview(draft, viewer, *, today) -> dict`;
    - `PREVIEW_SIZE = 10`.
  - `export.CONTACT_FIELDS` and `contacts_table(rows)`.
  - `serializers.PreviewSerializer` and `MemberStateSerializer`.
  - The routes:
    - `GET /segments/<pk>/members/`;
    - `GET /segments/<pk>/members/export.csv`;
    - `PATCH /segments/<pk>/members/<record_id>/`;
    - `POST /segments/preview/`.

- [ ] **Step 1: Write the failing tests**

Create `services/segments/tests/test_members.py`:

```python
"""A segment's members as the kind's own list reads them, the tiles over
them, the CSV, the builder's preview, and pinning or keeping out. Twice
filtered throughout: a shared viewer gets their own members and a count."""

import csv
import io
import json
from datetime import timedelta
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from core.models import AuditEvent
from services.accounts.models import User
from services.customers.models import (
    Account,
    Activity,
    Contact,
    Customer,
    HealthSnapshot,
    Ticket,
)
from services.customers.tests.test_views import blind_to_one_account
from services.fx_rates.models import FxRate
from services.segments.export import CONTACT_FIELDS
from services.segments.models import MAX_PINNED, SegmentChange
from services.segments.tests.fixtures import SegmentFixture, rule

URL = "/api/v1/segments/"
HEALTHY = rule("health_score", "gt", 0)
ACTIVE = rule("status", "is", "active")
LISTING_KEYS = {
    "results", "next_cursor", "count", "groups", "kind", "currency", "summary", "hidden_count",
}  # fmt: skip


class MembersFixture(SegmentFixture):
    """Pizza Hut bills 12,000 USD with CSAT 80; Taco Bell bills 10,000 EUR
    (15,000 USD) with CSAT 40. Pizza EMEA and Taco West carry 5,000 and 7,000
    ARR. Sam and Uma are Pizza Hut's people, Tom is Taco Bell's."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        FxRate.objects.create(
            organisation=cls.org, currency="EUR", rate_to_org_currency=Decimal("1.5")
        )
        Customer.objects.filter(pk=cls.pizza.pk).update(
            arr_billed_at_account=Decimal("12000"), csat_score=Decimal("80")
        )
        Customer.objects.filter(pk=cls.taco.pk).update(
            arr_billed_at_account=Decimal("10000"), currency="EUR", csat_score=Decimal("40")
        )
        Account.objects.filter(pk=cls.emea.pk).update(
            arr=Decimal("5000"), health_score=Decimal("6.0"), csat_score=Decimal("70")
        )
        Account.objects.filter(pk=cls.west.pk).update(
            arr=Decimal("7000"), health_score=Decimal("4.0")
        )
        cls.sam = Contact.objects.create(customer=cls.pizza, name="Sam", email="sam@pizza.io")
        cls.uma = Contact.objects.create(account=cls.emea, name="Uma", email="uma@pizza.io")
        cls.tom = Contact.objects.create(customer=cls.taco, name="Tom", email="tom@taco.io")

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def members(self, user, segment, **query):
        response = self.api(user).get(f"{URL}{segment.pk}/members/", query)
        self.assertEqual(response.status_code, 200, response.content)
        return response.data

    def ids(self, body):
        return sorted(row["id"] for row in body["results"])


class RowTests(MembersFixture):
    def test_organisation_members_are_the_organizations_lists_own_rows(self):
        segment = self.segment(owner=self.admin, rules=HEALTHY)
        body = self.members(self.admin, segment)
        self.assertEqual(set(body), LISTING_KEYS)
        self.assertEqual(
            (body["kind"], body["currency"], body["hidden_count"]), ("customer", "USD", 0)
        )
        portfolio = self.api(self.admin).get("/api/v1/organizations/portfolio/").data["results"]
        self.assertEqual(body["results"], [row for row in portfolio if row["id"] in self.ids(body)])

    def test_account_members_are_the_accounts_lists_own_rows(self):
        segment = self.segment(owner=self.admin, kind="account", rules=HEALTHY)
        body = self.members(self.admin, segment)
        portfolio = self.api(self.admin).get("/api/v1/accounts/portfolio/").data["results"]
        self.assertEqual(self.ids(body), sorted([self.emea.pk, self.west.pk]))
        self.assertEqual(body["results"], [row for row in portfolio if row["id"] in self.ids(body)])

    def test_contact_members_are_the_contacts_lists_own_rows(self):
        segment = self.segment(owner=self.admin, kind="contact", rules=ACTIVE)
        body = self.members(self.admin, segment)
        listed = self.api(self.admin).get("/api/v1/contacts/").data["results"]
        self.assertEqual([row["name"] for row in body["results"]], ["Sam", "Tom", "Uma"])
        self.assertEqual(
            body["results"], sorted(listed, key=lambda row: (row["name"].casefold(), row["id"]))
        )

    def test_sort_group_and_cursor_are_the_lists_own(self):
        segment = self.segment(owner=self.admin, rules=HEALTHY)
        first = self.members(self.admin, segment, sort="name", limit="1")
        self.assertEqual([row["name"] for row in first["results"]], ["Pizza Hut"])
        self.assertIsInstance(first["next_cursor"], str)
        second = self.members(
            self.admin, segment, sort="name", limit="1", cursor=first["next_cursor"]
        )
        self.assertEqual(
            ([r["name"] for r in second["results"]], second["next_cursor"]), (["Taco Bell"], None)
        )
        grouped = self.members(self.admin, segment, group="health")
        self.assertEqual([group["key"] for group in grouped["groups"]], ["poor", "good"])

    def test_contacts_page_by_a_cursor_too(self):
        segment = self.segment(owner=self.admin, kind="contact", rules=ACTIVE)
        first = self.members(self.admin, segment, limit="2")
        second = self.members(self.admin, segment, limit="2", cursor=first["next_cursor"])
        self.assertEqual([row["name"] for row in first["results"]], ["Sam", "Tom"])
        self.assertEqual(
            ([row["name"] for row in second["results"]], second["next_cursor"]), (["Uma"], None)
        )

    def test_search_narrows_the_rows_not_the_tiles(self):
        segment = self.segment(owner=self.admin, rules=HEALTHY)
        body = self.members(self.admin, segment, search="pizza")
        self.assertEqual(
            ([row["name"] for row in body["results"]], body["count"]), (["Pizza Hut"], 1)
        )
        self.assertEqual(body["summary"]["members"], 2)


class SummaryTests(MembersFixture):
    def test_the_tiles_equal_the_members_they_cover(self):
        segment = self.segment(owner=self.admin, rules=HEALTHY)
        body = self.members(self.admin, segment, limit="100")
        rows, summary = body["results"], body["summary"]
        self.assertEqual(summary["members"], body["count"])
        self.assertEqual(summary["arr"], sum(row["arr"] for row in rows))
        self.assertEqual(summary["arr"], 27000.0)
        self.assertEqual(summary["avg_health"], 5.5)
        self.assertEqual(summary["avg_csat"], 60.0)
        self.assertEqual((summary["unconverted_count"], summary["currency"]), (0, "USD"))

    def test_accounts_tiles(self):
        segment = self.segment(owner=self.admin, kind="account", rules=HEALTHY)
        summary = self.members(self.admin, segment)["summary"]
        self.assertEqual(
            (summary["members"], summary["arr"], summary["avg_health"], summary["avg_csat"]),
            (2, 12000.0, 5.0, 70.0),
        )

    def test_contacts_tiles(self):
        segment = self.segment(owner=self.admin, kind="contact", rules=ACTIVE)
        summary = self.members(self.admin, segment)["summary"]
        self.assertEqual((summary["members"], summary["contacts"]["total"]), (3, 3))
        self.assertEqual(
            (summary["arr"], summary["avg_health"], summary["avg_csat"]), (None, None, None)
        )

    def test_money_with_no_rate_is_counted_not_summed(self):
        Customer.objects.create(
            organisation=self.org, name="Yen Co", currency="JPY", owner=self.admin,
            arr_billed_at_account=Decimal("1000000"), health_score=Decimal("5.0"),
        )  # fmt: skip
        segment = self.segment(owner=self.admin, rules=HEALTHY)
        summary = self.members(self.admin, segment)["summary"]
        self.assertEqual(
            (summary["members"], summary["arr"], summary["unconverted_count"]), (3, 27000.0, 1)
        )

    def test_entries_and_exits_count_only_what_the_viewer_may_open(self):
        segment = self.segment(owner=self.admin, rules=HEALTHY, sharing="workspace")
        for record, change, days_ago in (
            (self.pizza.pk, "entered", 0),
            (self.taco.pk, "left", 2),
            (self.pizza.pk, "left", 10),
        ):
            SegmentChange.objects.create(
                segment=segment, record_id=record, change=change,
                changed_on=self.today - timedelta(days=days_ago),
            )  # fmt: skip
        admin = self.members(self.admin, segment)["summary"]
        carl = self.members(self.csm, segment)["summary"]
        self.assertEqual((admin["entered_7d"], admin["left_7d"]), (1, 1))
        self.assertEqual((carl["entered_7d"], carl["left_7d"]), (1, 0))


class PrivacyTests(MembersFixture):
    def test_a_shared_viewer_gets_their_own_members_and_a_count(self):
        segment = self.segment(owner=self.admin, rules=HEALTHY, sharing="workspace")
        body = self.members(self.csm, segment)
        self.assertEqual((self.ids(body), body["hidden_count"]), ([self.pizza.pk], 1))
        self.assertEqual(body["summary"]["members"], 1)
        self.assertNotIn("Taco Bell", json.dumps(body, default=str))

    def test_blind_to_one_account(self):
        viewer, seen, hidden = blind_to_one_account(self.pizza)
        segment = self.segment(owner=self.admin, kind="account", rules=HEALTHY, sharing="workspace")
        body = self.members(viewer, segment)
        self.assertEqual(self.ids(body), [seen.pk])
        self.assertEqual(body["hidden_count"], 3)
        response = self.api(viewer).get(f"{URL}{segment.pk}/members/export.csv")
        names = [row[0] for row in csv.reader(io.StringIO(response.content.decode()))][1:]
        self.assertEqual(names, ["Seen"])

    def test_another_tenant_and_a_missing_segment_read_the_same(self):
        segment = self.segment(owner=self.admin, rules=HEALTHY, sharing="workspace")
        stranger = self.api(self.stranger)
        for path in ("members/", "members/export.csv"):
            hidden = stranger.get(f"{URL}{segment.pk}/{path}")
            missing = stranger.get(f"{URL}999999/{path}")
            self.assertEqual((hidden.status_code, hidden.content), (404, missing.content))


class ExportTests(MembersFixture):
    def export(self, user, segment, **query):
        response = self.api(user).get(f"{URL}{segment.pk}/members/export.csv", query)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response["Content-Type"].startswith("text/csv"))
        return list(csv.reader(io.StringIO(response.content.decode()))), response

    def test_organisations_export_the_organizations_columns_and_is_audited(self):
        segment = self.segment(owner=self.admin, rules=HEALTHY)
        rows, response = self.export(self.admin, segment, sort="name")
        self.assertEqual((rows[0][0], rows[0][-1]), ("Organization", "Currency"))
        self.assertEqual([row[0] for row in rows[1:]], ["Pizza Hut", "Taco Bell"])
        self.assertIn(f'filename="segment-{segment.pk}-', response["Content-Disposition"])
        event = AuditEvent.objects.get(action="segment.exported")
        self.assertEqual(
            (event.target_id, event.metadata), (str(segment.pk), {"count": 2, "params": ["sort"]})
        )

    def test_accounts_export_the_accounts_columns(self):
        segment = self.segment(owner=self.admin, kind="account", rules=HEALTHY)
        rows, _response = self.export(self.admin, segment)
        self.assertEqual((rows[0][0], rows[0][-1]), ("Account", "Currency"))
        self.assertEqual(len(rows), 3)

    def test_contacts_export_their_own_columns(self):
        segment = self.segment(owner=self.admin, kind="contact", rules=ACTIVE)
        rows, _response = self.export(self.admin, segment)
        self.assertEqual(rows[0], [field.label for field in CONTACT_FIELDS])
        uma = next(row for row in rows if row[0] == "Uma")
        self.assertEqual((uma[-2], uma[-1]), ("Pizza Hut", "Pizza EMEA"))

    def test_a_shared_viewer_exports_only_what_they_may_open(self):
        segment = self.segment(owner=self.admin, rules=HEALTHY, sharing="workspace")
        rows, _response = self.export(self.other, segment)
        self.assertEqual([row[0] for row in rows[1:]], ["Taco Bell"])


class PinTests(MembersFixture):
    def mark(self, user, segment, record_id, state):
        return self.api(user).patch(
            f"{URL}{segment.pk}/members/{record_id}/", {"state": state}, format="json"
        )

    def events(self):
        rows = AuditEvent.objects.filter(action__startswith="segment.member_").order_by("pk")
        return [(row.action, row.metadata) for row in rows]

    def test_the_owner_pins_keeps_out_and_clears(self):
        segment = self.segment(owner=self.admin, rules=rule("health_score", "gt", 5))
        self.assertEqual(self.ids(self.members(self.admin, segment)), [self.pizza.pk])

        response = self.mark(self.admin, segment, self.taco.pk, "pinned")
        self.assertEqual(response.data, {"pinned_ids": [self.taco.pk], "excluded_ids": []})
        self.assertEqual(
            self.ids(self.members(self.admin, segment)), sorted([self.pizza.pk, self.taco.pk])
        )
        segment.refresh_from_db()
        self.assertEqual(segment.last_members, sorted([self.pizza.pk, self.taco.pk]))

        response = self.mark(self.admin, segment, self.taco.pk, "excluded")
        self.assertEqual(response.data, {"pinned_ids": [], "excluded_ids": [self.taco.pk]})
        self.mark(self.admin, segment, self.taco.pk, "none")
        self.mark(self.admin, segment, self.taco.pk, "none")
        record = self.taco.pk
        self.assertEqual(
            self.events(),
            [
                ("segment.member_pinned", {"record_id": record, "pinned": True}),
                ("segment.member_pinned", {"record_id": record, "pinned": False}),
                ("segment.member_excluded", {"record_id": record, "excluded": True}),
                ("segment.member_excluded", {"record_id": record, "excluded": False}),
            ],
        )

    def test_only_a_record_the_owner_may_open_and_missing_reads_the_same(self):
        segment = self.segment(owner=self.csm, rules=HEALTHY)
        hidden = self.mark(self.csm, segment, self.taco.pk, "pinned")
        missing = self.mark(self.csm, segment, 999999, "pinned")
        self.assertEqual((hidden.status_code, hidden.data), (404, missing.data))
        segment.refresh_from_db()
        self.assertEqual(segment.pinned_ids, [])

    def test_non_owners_cannot_pin(self):
        shared = self.segment(owner=self.csm, sharing="workspace")
        private = self.segment(owner=self.csm, name="Private")
        self.assertEqual(self.mark(self.other, shared, self.taco.pk, "pinned").status_code, 403)
        self.assertEqual(self.mark(self.other, private, self.taco.pk, "pinned").status_code, 404)

    def test_a_bad_state_and_the_limit(self):
        segment = self.segment(owner=self.admin, pinned_ids=list(range(10**6, 10**6 + MAX_PINNED)))
        self.assertEqual(self.mark(self.admin, segment, self.pizza.pk, "starred").status_code, 400)
        response = self.mark(self.admin, segment, self.pizza.pk, "pinned")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data,
            {"detail": "A segment can pin at most 500 records, and keep out as many."},
        )


class PreviewTests(MembersFixture):
    def preview(self, user, **body):
        return self.api(user).post(f"{URL}preview/", body, format="json")

    def test_the_count_the_first_ten_and_the_totals(self):
        Customer.objects.bulk_create(
            Customer(organisation=self.org, name=f"Extra {i:02}", owner=self.admin)
            for i in range(11)
        )
        response = self.preview(self.admin, kind="customer", rules=HEALTHY)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.data
        self.assertEqual(set(body), {"kind", "count", "results", "summary"})
        self.assertEqual(body["count"], 13)
        self.assertEqual(
            [row["name"] for row in body["results"]], [f"Extra {i:02}" for i in range(10)]
        )
        self.assertEqual(set(body["results"][0]), {"id", "name", "owner", "health"})
        self.assertEqual((body["summary"]["members"], body["summary"]["entered_7d"]), (13, None))

    def test_contacts_preview_names_their_parent(self):
        body = self.preview(self.csm, kind="contact", rules=ACTIVE).data
        self.assertEqual(
            body["results"],
            [
                {
                    "id": self.sam.pk,
                    "name": "Sam",
                    "role": "Other",
                    "parent": {"kind": "customer", "id": self.pizza.pk, "name": "Pizza Hut"},
                },
                {
                    "id": self.uma.pk,
                    "name": "Uma",
                    "role": "Other",
                    "parent": {"kind": "account", "id": self.emea.pk, "name": "Pizza EMEA"},
                },
            ],
        )

    def test_a_rule_naming_a_hidden_record_is_refused_like_a_missing_one(self):
        hidden = self.preview(
            self.csm, kind="account", rules=rule("organisation", "is", self.taco.pk)
        )
        missing = self.preview(self.csm, kind="account", rules=rule("organisation", "is", 999999))
        self.assertEqual((hidden.status_code, hidden.data), (400, missing.data))

    def test_a_pin_the_caller_cannot_open_is_not_shown(self):
        body = self.preview(
            self.csm,
            kind="customer",
            rules=rule("health_score", "gt", 100),
            pinned_ids=[self.taco.pk],
        ).data
        self.assertEqual((body["count"], body["results"]), (0, []))

    def test_the_query_count_is_pinned(self):
        """Six queries for an organisations preview by an admin, whatever the
        book's size: the caller's active membership and role (capabilities),
        the first ten members (owner joined), `user.organisation` (the ARR
        tile's mapping and currency), the FX rates and the tiles (one
        aggregate)."""
        client = APIClient()
        client.force_authenticate(User.objects.get(pk=self.admin.pk))
        with CaptureQueriesContext(connection) as ctx:
            response = client.post(
                f"{URL}preview/", {"kind": "customer", "rules": HEALTHY}, format="json"
            )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(ctx.captured_queries), 6)


class MembersQueryCountTests(MembersFixture):
    """`GET /segments/<id>/members/` for an organisations segment runs a fixed
    number of queries, whatever its size. Twelve for its admin owner:
      1. `user.organisation` (`organizations.book.load_portfolio`)
      2. the caller's active membership (`identity.context.active_membership`)
      3. `user.role` (`identity.context.capabilities_for`)
      4. the segment (owner joined)
      5. the customers: the members as a subquery, touch and ticket
         subqueries, people and product joined
      6. their health snapshots
      7. the FX rate table
      8. the open High/Critical tickets
      9. those tickets' accounts (prefetch)
      10. those accounts' customers (prefetch)
      11. the tiles (one aggregate, reusing the FX rates already read)
      12. the last 7 days' entries and exits the caller may open
    A reader who is not the owner adds the hidden count plus the owner's own
    capability and org-chart reads (the owner is another person); that is
    flat too. If the pinned number is ever
    wrong, print `[q["sql"] for q in ctx.captured_queries]`: every query must
    be one of these kinds; an extra one is a bug to fix, not a number to bump."""

    EXPECTED = 12

    def book(self, size):
        for i in range(size):
            customer = Customer.objects.create(
                organisation=self.org, name=f"Co {size}-{i}", owner=self.csm,
                health_score=Decimal("6.0"), renewal_date=self.today + timedelta(days=i),
            )  # fmt: skip
            HealthSnapshot.objects.create(
                customer=customer,
                captured_on=self.today - timedelta(days=30),
                health_score=Decimal("5.0"),
            )
            Activity.objects.create(
                customer=customer, type=Activity.ActivityType.OTHER, occurred_at=self.today
            )
            account = Account.objects.create(name=f"Div {size}-{i}", owner=self.csm)
            account.customers.add(customer)
            Ticket.objects.create(
                account=account, ticket_number=f"T-{size}-{i}", title="Down",
                status=Ticket.Status.OPEN, priority=Ticket.Priority.HIGH, opened_at=self.today,
            )  # fmt: skip

    def count(self, user, segment):
        client = APIClient()
        client.force_authenticate(User.objects.get(pk=user.pk))
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(f"{URL}{segment.pk}/members/")
        self.assertEqual(response.status_code, 200, response.content)
        return len(ctx.captured_queries)

    def test_pinned_and_flat_as_the_segment_grows(self):
        admins = self.segment(owner=self.admin, rules=HEALTHY)
        carls = self.segment(owner=self.csm, rules=HEALTHY, name="Carl's")
        self.book(3)
        small, small_csm = self.count(self.admin, admins), self.count(self.csm, carls)
        self.book(12)
        self.assertEqual(self.count(self.admin, admins), small)
        self.assertEqual(self.count(self.csm, carls), small_csm)
        self.assertEqual(small, self.EXPECTED)

    def test_a_shared_viewer_is_flat_too(self):
        segment = self.segment(owner=self.csm, rules=HEALTHY, sharing="workspace")
        self.book(3)
        small = self.count(self.admin, segment)
        self.book(12)
        self.assertEqual(self.count(self.admin, segment), small)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.segments.tests.test_members --noinput`
Expected: an import error, because there is no module named `services.segments.export`.

- [ ] **Step 3: Write the tiles, the contacts CSV and the members**

`services/segments/summary.py`:

```python
"""The segment page's tiles and the builder's totals.

They cover every member the viewer may open, not the page and not the
search, so a tile always equals the members it covers. Entered and left
count only records the viewer may open.
"""

from datetime import timedelta

from django.db.models import Avg, Count, Q, Sum

from services.customers.contact_list import contacts_summary
from services.fx_rates.conversion import rates_for

from .compiler import arr_expression
from .evaluate import visible_records
from .models import SegmentChange

#: The "entered" and "left" tiles' window, today included.
WINDOW_DAYS = 7


def _tenth(value):
    return None if value is None else round(float(value), 1)


def recent_changes(segment, viewer, *, today, days=WINDOW_DAYS):
    """Entries and exits over the last `days` on records `viewer` may open.
    None for an unsaved segment (the builder's preview)."""
    if segment.pk is None:
        return {"entered": None, "left": None}
    # SOC2:AUTH-02 only records the viewer may open are counted
    rows = SegmentChange.objects.filter(
        segment_id=segment.pk,
        changed_on__gt=today - timedelta(days=days),
        record_id__in=visible_records(segment.kind, viewer).values("pk"),
    )
    return rows.aggregate(
        entered=Count("pk", filter=Q(change=SegmentChange.Change.ENTERED)),
        left=Count("pk", filter=Q(change=SegmentChange.Change.LEFT)),
    )


def segment_summary(members, segment, viewer, *, today, rates=None):
    """The tiles over `members`, a queryset of the viewer's members. An
    organisation's ARR is `arr_expression`'s (the workspace mapping,
    converted, unconvertible counted not summed); an account's is its own
    `arr`. Pass `rates` when the caller has read them already."""
    organisation = viewer.organisation
    tiles = {
        "members": 0,
        "arr": None,
        "unconverted_count": 0,
        "avg_health": None,
        "avg_csat": None,
    }
    if segment.kind == "contact":
        people = contacts_summary(members)
        tiles.update(members=people["total"], contacts=people)
    else:
        aggregates = {
            "members": Count("pk"),
            "avg_health": Avg("health_score"),
            "avg_csat": Avg("csat_score"),
        }
        if segment.kind == "customer":
            rates = rates if rates is not None else rates_for(organisation)
            members = members.annotate(_seg_arr=arr_expression(organisation, rates))
            aggregates["arr"] = Sum("_seg_arr")
            aggregates["unconverted_count"] = Count("pk", filter=Q(_seg_arr__isnull=True))
        else:
            aggregates["arr"] = Sum("arr")
        row = members.aggregate(**aggregates)
        tiles.update(
            members=row["members"],
            arr=round(float(row["arr"] or 0), 2),
            unconverted_count=row.get("unconverted_count", 0),
            avg_health=_tenth(row["avg_health"]),
            avg_csat=_tenth(row["avg_csat"]),
        )
    changes = recent_changes(segment, viewer, today=today)
    tiles.update(
        entered_7d=changes["entered"], left_7d=changes["left"], currency=organisation.currency
    )
    return tiles
```

`services/segments/export.py`:

```python
"""A contacts segment as a CSV: the person, how to reach them, and their
organisation and account (only ones the reader may open, as on the Contacts
list). Organisations and accounts segments use their lists' own exports.
Cells go through Organizations' `cell`, so a name starting with `=` is data,
not a formula."""

from services.organizations.export import cell
from services.organizations.fields import Field


def _parent(key):
    return lambda row: row[key]["name"] if row[key] else ""


CONTACT_FIELDS = (
    Field("name", "Name", lambda row: row["name"]),
    Field("email", "Email", lambda row: row["email"]),
    Field("phone", "Phone", lambda row: row["phone"]),
    Field("role", "Role", lambda row: row["role_display"]),
    Field("status", "Status", lambda row: row["status"]),
    Field("sentiment", "Sentiment", lambda row: row["sentiment"]),
    Field("language", "Language", lambda row: row["language"]),
    Field("lastContacted", "Last Contacted", lambda row: row["last_contacted_at"]),
    Field("organisation", "Organisation", _parent("organisation")),
    Field("account", "Account", _parent("account")),
)


def contacts_table(rows):
    """`rows` are `ContactSerializer` rows."""
    header = [field.label for field in CONTACT_FIELDS]
    return [header, *([cell(field.value(row)) for field in CONTACT_FIELDS] for row in rows)]
```

`services/segments/members.py`:

```python
"""A segment's members as the kind's own list reads them.

Organisations and accounts come from the Organizations and Accounts books
with the segment's members as `scope`: the same rows, groups, sort and
keyset cursor. Contacts come from the Contacts list's own filters and
serializer, paged by a keyset cursor by name. Everything is the viewer's
own: `members_queryset` runs the rules over their book, and the books apply
visibility again.
"""

from services.accounts_portfolio import book as account_book
from services.accounts_portfolio import params as account_params
from services.accounts_portfolio import rows as account_rows
from services.accounts_portfolio import shape as account_shape
from services.accounts_portfolio.export import table as account_table
from services.customers.contact_list import filtered_contacts, parse_contact_filters
from services.customers.models import Contact
from services.customers.scoping import visible_customers
from services.customers.serializers import ContactSerializer
from services.organizations import book as organisation_book
from services.organizations import params as organisation_params
from services.organizations import rows as organisation_rows
from services.organizations import shape as organisation_shape
from services.organizations.export import table as organisation_table
from services.organizations.params import DEFAULT_LIMIT, MAX_LIMIT, int_or_none
from services.organizations.rows import person
from services.portfolio_core.shape import fingerprint, keyset_page, rank_of

from .evaluate import hidden_count, members_queryset
from .export import contacts_table
from .summary import segment_summary

#: How many members the builder's live preview lists.
PREVIEW_SIZE = 10


def _contacts(scope, viewer, query):
    """The people in `scope`, through the Contacts list's own filters and
    readable-calls annotation."""
    return filtered_contacts(viewer, parse_contact_filters(query)).filter(pk__in=scope.values("pk"))


def _contact_rows(contacts, viewer):
    # SOC2:AUTH-02 a contact names only organisations the viewer may open
    visible = set(visible_customers(viewer).values_list("pk", flat=True))
    return ContactSerializer(contacts, many=True, context={"visible_customer_ids": visible}).data


def _name_rank(entry):
    pk, name = entry
    folded = name.casefold()
    return rank_of((), folded, False, (folded, pk))


def _contacts_page(scope, viewer, query):
    contacts = _contacts(scope, viewer, query)
    filters = parse_contact_filters(query)
    entries = sorted(
        Contact.objects.filter(pk__in=contacts.values("pk")).values_list("pk", "name"),
        key=_name_rank,
    )
    limit = int_or_none(query.get("limit"))
    page, next_cursor = keyset_page(
        entries,
        rank=_name_rank,
        cursor=query.get("cursor") or "",
        limit=DEFAULT_LIMIT if limit is None or limit < 1 else min(limit, MAX_LIMIT),
        descending=False,
        fingerprint=fingerprint(
            [filters.search, filters.customer, filters.account, filters.sentiment, filters.role]
        ),
        grouped=False,
    )
    wanted = [pk for pk, _name in page]
    found = {contact.pk: contact for contact in contacts.filter(pk__in=wanted)}
    return {
        "results": _contact_rows([found[pk] for pk in wanted], viewer),
        "next_cursor": next_cursor,
        "count": len(entries),
        "groups": [],
    }


def members_listing(segment, viewer, query, *, today):
    """The members endpoint's body: the kind's own rows and paging, the tiles
    over every member the viewer may open, and how many they may not."""
    scope = members_queryset(segment, viewer, today=today)
    rates = None
    if segment.kind == "customer":
        params = organisation_params.parse_params(query)
        portfolio = organisation_book.load_portfolio(viewer, params, today=today, scope=scope)
        entries, groups = organisation_shape.select(portfolio, params)
        page, next_cursor = organisation_shape.paginate(entries, params=params, portfolio=portfolio)
        body = {
            "results": [organisation_rows.row_payload(entry) for entry in page],
            "next_cursor": next_cursor,
            "count": len(entries),
            "groups": groups,
        }
        rates = portfolio.rates
    elif segment.kind == "account":
        params = account_params.parse_params(query)
        portfolio = account_book.load_portfolio(viewer, params, today=today, scope=scope)
        entries, groups = account_shape.select(portfolio, params)
        page, next_cursor = account_shape.paginate(entries, params=params)
        body = {
            "results": [account_rows.row_payload(entry) for entry in page],
            "next_cursor": next_cursor,
            "count": len(entries),
            "groups": groups,
        }
    else:
        body = _contacts_page(scope, viewer, query)
    body.update(
        kind=segment.kind,
        currency=viewer.organisation.currency,
        summary=segment_summary(scope, segment, viewer, today=today, rates=rates),
        hidden_count=hidden_count(segment, viewer, today=today),
    )
    return body


def members_table(segment, viewer, query, *, today):
    """`(csv rows, member count)`: every member the viewer may open, in the
    list's order (its `search` and `sort` honoured), with the kind's own
    export columns."""
    scope = members_queryset(segment, viewer, today=today)
    if segment.kind == "customer":
        params = organisation_params.parse_params(query)
        portfolio = organisation_book.load_portfolio(viewer, params, today=today, scope=scope)
        entries, _groups = organisation_shape.select(portfolio, params)
        rows = [organisation_rows.row_payload(entry) for entry in entries]
        return organisation_table(rows), len(rows)
    if segment.kind == "account":
        params = account_params.parse_params(query)
        portfolio = account_book.load_portfolio(viewer, params, today=today, scope=scope)
        entries, _groups = account_shape.select(portfolio, params)
        rows = [account_rows.row_payload(entry) for entry in entries]
        return account_table(rows, currency=viewer.organisation.currency), len(rows)
    contacts = sorted(
        _contacts(scope, viewer, query), key=lambda contact: (contact.name.casefold(), contact.pk)
    )
    rows = _contact_rows(contacts, viewer)
    return contacts_table(rows), len(rows)


def _parent_of(contact):
    """A contact's own organisation or account, which the viewer may open
    because they may open the contact."""
    if contact.customer_id:
        return {"kind": "customer", "id": contact.customer_id, "name": contact.customer.name}
    return {"kind": "account", "id": contact.account_id, "name": contact.account.name}


def preview(draft, viewer, *, today):
    """The builder's live preview: how many match, the first ten by name, and
    the totals. Never the whole list."""
    scope = members_queryset(draft, viewer, today=today).order_by("name", "pk")
    if draft.kind == "contact":
        first = scope.select_related("customer", "account")[:PREVIEW_SIZE]
        results = [
            {
                "id": contact.pk,
                "name": contact.name,
                "role": contact.get_role_display(),
                "parent": _parent_of(contact),
            }
            for contact in first
        ]
    else:
        first = scope.select_related("owner")[:PREVIEW_SIZE]
        results = [
            {
                "id": record.pk,
                "name": record.name,
                "owner": person(record.owner),
                "health": {"score": float(record.health_score), "category": record.health_category},
            }
            for record in first
        ]
    summary = segment_summary(scope, draft, viewer, today=today)
    return {"kind": draft.kind, "count": summary["members"], "results": results, "summary": summary}
```

- [ ] **Step 4: Add the serializers, views and routes**

Append to `services/segments/serializers.py`, and add `MAX_PINNED` to its `.models` import (`from .models import MAX_PINNED, Segment, default_rules`):

```python
class PreviewSerializer(serializers.Serializer):
    """An unsaved segment, checked exactly as a save would check it."""

    kind = serializers.ChoiceField(choices=Segment.Kind.choices)
    rules = serializers.JSONField()
    pinned_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list, max_length=MAX_PINNED
    )
    excluded_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list, max_length=MAX_PINNED
    )

    def validate(self, attrs):
        try:
            attrs["rules"] = validate_rules(
                attrs["rules"], attrs["kind"], user=self.context["request"].user
            )
        except RuleError as exc:
            raise serializers.ValidationError({"rules": [str(exc)]}) from exc
        return attrs


class MemberStateSerializer(serializers.Serializer):
    state = serializers.ChoiceField(choices=("pinned", "excluded", "none"))
```

In `services/segments/views.py`, replace the import block with:

```python
from django.db import transaction
from django.http import Http404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core import audit
from services.organizations.export import CSVRenderer

from .access import get_owned, get_readable, readable_segments
from .baseline import rebaseline
from .evaluate import Draft, openable_ids, visible_records
from .members import members_listing, members_table, preview
from .models import MAX_OWNED, MAX_PINNED, Segment
from .payloads import list_rows, segment_payload
from .rules import present_rules
from .serializers import MemberStateSerializer, PreviewSerializer, SegmentWriteSerializer
```

and after `SHARING_FIELDS = …` add:

```python
PIN_LIMIT = f"A segment can pin at most {MAX_PINNED} records, and keep out as many."
```

Append to `services/segments/views.py`:

```python
class SegmentMembersView(APIView):
    """GET /api/v1/segments/<id>/members/ — the members the caller may open,
    as the kind's own list reads them (rows, groups, sort, search, cursor),
    with the tiles and `hidden_count`. See docs/API_CONTRACTS.md."""

    # SOC2:AUTH-02 authentication here; `get_readable` checks the segment and
    # `members_queryset` (services/segments/evaluate.py) every record
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        segment = get_readable(request.user, pk)
        body = members_listing(
            segment, request.user, request.query_params, today=timezone.localdate()
        )
        return Response(body)


class SegmentMembersExportView(APIView):
    """GET /api/v1/segments/<id>/members/export.csv — every member the caller
    may open, in list order, with the kind's export columns. Audited: this is
    confidential data leaving the app."""

    # SOC2:AUTH-02 authentication here; the same checks as the members view
    permission_classes = [IsAuthenticated]
    renderer_classes = [CSVRenderer]

    def get(self, request, pk):
        segment = get_readable(request.user, pk)
        today = timezone.localdate()
        rows, count = members_table(segment, request.user, request.query_params, today=today)
        audit.record(  # SOC2:LOG-01
            "segment.exported",
            request=request,
            target=segment,
            # Parameter names only: a search term is the user's own words.
            metadata={"count": count, "params": sorted(request.query_params.keys())},
        )
        response = Response(rows)
        response["Content-Disposition"] = (
            f'attachment; filename="segment-{segment.pk}-{today.isoformat()}.csv"'
        )
        return response


class SegmentMemberView(APIView):
    """PATCH /api/v1/segments/<id>/members/<record_id>/ — `{"state": "pinned"
    | "excluded" | "none"}`. Owner only, and only a record the owner may
    open: a missing one and a hidden one read the same 404."""

    # SOC2:AUTH-02 authentication here; `get_owned` and the record check below
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk, record_id):
        segment = get_owned(request.user, pk)
        serializer = MemberStateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        state = serializer.validated_data["state"]
        # SOC2:AUTH-02 only a record the owner may open; missing and hidden read the same
        if not visible_records(segment.kind, request.user).filter(pk=record_id).exists():
            raise Http404
        was_pinned = record_id in segment.pinned_ids
        was_excluded = record_id in segment.excluded_ids
        pinned = [pk for pk in segment.pinned_ids if pk != record_id]
        excluded = [pk for pk in segment.excluded_ids if pk != record_id]
        if state == "pinned":
            pinned.append(record_id)
        elif state == "excluded":
            excluded.append(record_id)
        if len(pinned) > MAX_PINNED or len(excluded) > MAX_PINNED:
            return Response({"detail": PIN_LIMIT}, status=status.HTTP_400_BAD_REQUEST)
        events = []
        if (state == "pinned") != was_pinned:
            events.append(
                ("segment.member_pinned", {"record_id": record_id, "pinned": state == "pinned"})
            )
        if (state == "excluded") != was_excluded:
            events.append(
                (
                    "segment.member_excluded",
                    {"record_id": record_id, "excluded": state == "excluded"},
                )
            )
        if events:
            with transaction.atomic():
                segment.pinned_ids, segment.excluded_ids = sorted(pinned), sorted(excluded)
                segment.save(update_fields=["pinned_ids", "excluded_ids", "updated_at"])
                rebaseline(segment, today=timezone.localdate())
                for action, metadata in events:
                    audit.record(  # SOC2:LOG-01
                        action, request=request, target=segment, metadata=metadata
                    )
        return Response(
            {
                "pinned_ids": openable_ids(segment.kind, request.user, segment.pinned_ids),
                "excluded_ids": openable_ids(segment.kind, request.user, segment.excluded_ids),
            }
        )


class SegmentPreviewView(APIView):
    """POST /api/v1/segments/preview/ — `{kind, rules, pinned_ids?,
    excluded_ids?}` evaluated for the caller without saving: the count, the
    first ten members and the totals. The rules are checked as a save would
    check them."""

    # SOC2:AUTH-02 authentication here; `validate_rules` and `members_queryset`
    # do the record checks
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PreviewSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        draft = Draft(data["kind"], data["rules"], data["pinned_ids"], data["excluded_ids"])
        return Response(preview(draft, request.user, today=timezone.localdate()))
```

In `services/segments/urls.py`, the full list becomes:

```python
urlpatterns = [
    path("", views.SegmentListCreateView.as_view(), name="segment-list"),
    path("preview/", views.SegmentPreviewView.as_view(), name="segment-preview"),
    path("<int:pk>/", views.SegmentDetailView.as_view(), name="segment-detail"),
    path("<int:pk>/duplicate/", views.SegmentDuplicateView.as_view(), name="segment-duplicate"),
    path("<int:pk>/members/", views.SegmentMembersView.as_view(), name="segment-members"),
    path(
        "<int:pk>/members/export.csv",
        views.SegmentMembersExportView.as_view(),
        name="segment-members-export",
    ),
    path(
        "<int:pk>/members/<int:record_id>/",
        views.SegmentMemberView.as_view(),
        name="segment-member",
    ),
]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.segments --noinput`
Expected: PASS.
- If a row comparison in `RowTests` fails, diff the two dicts. The members row and the portfolio row come from the same `row_payload` for the same viewer and day, so a difference means the scope changed what the book loaded.
- If a pinned count is off, print the queries: every one must be on the docstring's list.

- [ ] **Step 6: Commit**

```bash
git add services/segments
git commit -m "feat(segments): members as each list's own rows, tiles, CSV export, preview, pin and keep out

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: The nightly step, the change history and the owner's alert

**Files:**
- Create: `services/segments/nightly.py`, `services/segments/history.py`
- Modify: `services/segments/views.py` (imports; one view appended), `services/segments/urls.py` (one route)
- Modify: `services/customers/management/commands/run_health_maintenance.py` (one step, after the pulse dots and before the metric month-end)
- Test: `services/segments/tests/test_nightly.py`

**Interfaces:**
- Consumes:
  - `member_ids`, `visible_records` and `MODELS` (Task 6);
  - `compile_rules` and `Compiled.conditions`/`.named`/`.annotate` (Task 4);
  - `rebaseline` and `get_readable` (Task 7);
  - `notifications.realtime.notify`, `Notification.Kind.SEGMENT_CHANGES` (Task 1);
  - `organizations.book.CHURNED`;
  - `organizations.params.int_or_none`.
- Produces:
  - `nightly`:
    - `evaluate_nightly(organisation=None, *, today=None) -> NightlyResult(evaluated, changes, alerts, paused, failed)`;
    - `evaluate_segment(segment_id, *, today) -> Outcome(evaluated, changes, alerted, paused)`;
    - `change_reasons(segment, owner, entered, left, *, today) -> dict[int, list[str]]`;
    - `alert_message(segment, entered, left) -> str`.
  - `history.change_history(segment, viewer, *, today, days) -> {"kind", "days": [{"date", "entered": [...], "left": [...]}], "hidden_count"}`, with `DEFAULT_DAYS = 30` and `MAX_DAYS = 90`.
  - The route `GET /segments/<pk>/changes/?days=`.

- [ ] **Step 1: Write the failing tests**

Create `services/segments/tests/test_nightly.py`:

```python
"""The nightly step: entries and exits as the owner sees their book, with
the field keys that moved them; one run per date; one alert per segment per
day; an inactive owner pauses the segment. Then the history a viewer reads:
their own records named, the rest counted. Dates are passed in, never
waited for."""

from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from services.accounts.models import User
from services.customers.models import Customer
from services.notifications.models import Notification
from services.segments import nightly
from services.segments.baseline import rebaseline
from services.segments.models import Segment, SegmentChange
from services.segments.nightly import alert_message, evaluate_nightly
from services.segments.tests.fixtures import SegmentFixture, rule

POOR = rule("health_category", "is", "poor")
URL = "/api/v1/segments/"


class NightlyFixture(SegmentFixture):
    """Alice watches "Poor health" with alerts on. Its baseline was taken
    yesterday: Taco Bell (3.0) is in, Pizza Hut (8.0) is not."""

    def setUp(self):
        self.yesterday = self.today - timedelta(days=1)
        self.watch = self.segment(
            owner=self.admin, rules=POOR, alert_on_changes=True, name="Poor health"
        )
        rebaseline(self.watch, today=self.yesterday)

    def set_health(self, customer, score):
        Customer.objects.filter(pk=customer.pk).update(health_score=Decimal(score))

    def changes(self, segment=None):
        rows = SegmentChange.objects.filter(segment=segment or self.watch)
        return sorted(rows.values_list("record_id", "change", "changed_on", "reason"))


class NightlyTests(NightlyFixture):
    def test_entries_and_exits_are_recorded_with_the_fields_that_moved_them(self):
        self.set_health(self.pizza, "2.0")
        self.set_health(self.taco, "8.0")
        result = evaluate_nightly(today=self.today)
        self.assertEqual((result.evaluated, result.changes, result.alerts), (1, 2, 1))
        self.assertEqual(
            self.changes(),
            [
                (self.pizza.pk, "entered", self.today, ["health_category"]),
                (self.taco.pk, "left", self.today, ["health_category"]),
            ],
        )
        self.watch.refresh_from_db()
        self.assertEqual(
            (self.watch.last_members, self.watch.member_count, self.watch.last_evaluated_on),
            ([self.pizza.pk], 1, self.today),
        )

    def test_a_first_run_only_sets_the_baseline(self):
        fresh = self.segment(owner=self.admin, rules=POOR, name="Fresh")
        evaluate_nightly(today=self.today)
        fresh.refresh_from_db()
        self.assertEqual((fresh.last_members, fresh.member_count), ([self.taco.pk], 1))
        self.assertEqual(self.changes(fresh), [])

    def test_one_run_per_date(self):
        self.set_health(self.pizza, "2.0")
        evaluate_nightly(today=self.today)
        again = evaluate_nightly(today=self.today)
        self.assertEqual((again.evaluated, again.changes, again.alerts), (0, 0, 0))
        self.assertEqual(len(self.changes()), 1)

    def test_one_alert_per_segment_per_day_with_counts_only(self):
        self.set_health(self.pizza, "2.0")
        self.set_health(self.taco, "8.0")
        evaluate_nightly(today=self.today)
        evaluate_nightly(today=self.today)
        alert = Notification.objects.get()
        self.assertEqual(
            (alert.recipient, alert.kind, alert.message, alert.link),
            (
                self.admin,
                "segment_changes",
                "Poor health: 1 entered, 1 left",
                f"/segments/{self.watch.pk}?tab=changes",
            ),
        )
        # Nothing moves the next day, so nothing is sent.
        evaluate_nightly(today=self.today + timedelta(days=1))
        self.assertEqual(Notification.objects.count(), 1)

    def test_no_alert_when_the_owner_did_not_ask_for_one(self):
        Segment.objects.filter(pk=self.watch.pk).update(alert_on_changes=False)
        self.set_health(self.pizza, "2.0")
        evaluate_nightly(today=self.today)
        self.assertEqual((len(self.changes()), Notification.objects.count()), (1, 0))

    def test_the_alert_quotes_at_most_two_hundred_characters_of_the_name(self):
        self.watch.name = "x" * 120
        self.assertEqual(alert_message(self.watch, 3, 1), f"{'x' * 120}: 3 entered, 1 left")
        self.watch.name = "y" * 300
        self.assertLessEqual(len(alert_message(self.watch, 3, 1)), 255)

    def test_an_inactive_owner_pauses_and_a_returning_owner_resumes_from_that_day(self):
        User.objects.filter(pk=self.admin.pk).update(is_active=False)
        self.set_health(self.pizza, "2.0")
        result = evaluate_nightly(today=self.today)
        self.watch.refresh_from_db()
        self.assertEqual((result.paused, result.evaluated), (1, 0))
        self.assertEqual((self.watch.paused, self.changes()), (True, []))
        self.assertFalse(Notification.objects.exists())

        User.objects.filter(pk=self.admin.pk).update(is_active=True)
        tomorrow = self.today + timedelta(days=1)
        evaluate_nightly(today=tomorrow)
        self.watch.refresh_from_db()
        self.assertEqual(
            (self.watch.paused, self.watch.last_members, self.watch.last_evaluated_on),
            (False, sorted([self.pizza.pk, self.taco.pk]), tomorrow),
        )
        self.assertEqual(self.changes(), [])

    def test_evaluated_as_the_owner(self):
        carls = self.segment(owner=self.csm, rules=POOR, name="Carl's")
        rebaseline(carls, today=self.yesterday)
        self.set_health(self.taco, "1.0")
        evaluate_nightly(today=self.today)
        carls.refresh_from_db()
        self.assertEqual((carls.last_members, self.changes(carls)), ([], []))

    def test_reasons_for_a_deleted_record_lost_access_and_the_churn_default(self):
        segment = self.segment(owner=self.csm, rules=rule("health_score", "gt", 0), name="Book")
        brief, moved, gone = (
            Customer.objects.create(organisation=self.org, name=name, owner=self.csm)
            for name in ("Brief", "Moved", "Gone")
        )
        rebaseline(segment, today=self.yesterday)
        brief_id = brief.pk
        brief.delete()
        Customer.objects.filter(pk=moved.pk).update(owner=self.other)
        Customer.objects.filter(pk=gone.pk).update(churn_date=self.today)
        evaluate_nightly(today=self.today)
        reasons = {
            record: (change, reason) for record, change, _day, reason in self.changes(segment)
        }
        self.assertEqual(
            reasons,
            {
                brief_id: ("left", ["deleted"]),
                moved.pk: ("left", ["access"]),
                gone.pk: ("left", ["churned"]),
            },
        )

    def test_a_pin_the_owner_can_open_again_enters_as_pinned(self):
        segment = self.segment(
            owner=self.csm, rules=rule("health_score", "gt", 100), pinned_ids=[self.taco.pk]
        )
        rebaseline(segment, today=self.yesterday)
        self.assertEqual(segment.last_members, [])
        Customer.objects.filter(pk=self.taco.pk).update(owner=self.csm)
        evaluate_nightly(today=self.today)
        self.assertEqual(self.changes(segment), [(self.taco.pk, "entered", self.today, ["pinned"])])

    def test_a_segment_too_large_to_track_is_counted_not_tracked(self):
        with patch.object(nightly, "MAX_TRACKED_MEMBERS", 0):
            evaluate_nightly(today=self.today)
        self.watch.refresh_from_db()
        self.assertEqual((self.watch.last_members, self.watch.member_count), (None, 1))
        evaluate_nightly(today=self.today + timedelta(days=1))
        self.watch.refresh_from_db()
        self.assertEqual((self.watch.last_members, self.changes()), ([self.taco.pk], []))

    def test_a_failing_segment_is_logged_and_skipped(self):
        other = self.segment(owner=self.csm, rules=POOR, name="Carl's")
        real = nightly.member_ids

        def flaky(segment, user, *, today):
            if segment.pk == self.watch.pk:
                raise RuntimeError("boom")
            return real(segment, user, today=today)

        with (
            patch.object(nightly, "member_ids", side_effect=flaky),
            self.assertLogs("services.segments.nightly", "ERROR"),
        ):
            result = evaluate_nightly(today=self.today)
        self.assertEqual((result.failed, result.evaluated), (1, 1))
        self.watch.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(
            (self.watch.last_evaluated_on, other.last_evaluated_on), (self.yesterday, self.today)
        )

    def test_one_workspace_at_a_time(self):
        evaluate_nightly(self.other_org, today=self.today)
        self.watch.refresh_from_db()
        self.assertEqual(self.watch.last_evaluated_on, self.yesterday)

    def test_the_maintenance_command_runs_the_step(self):
        out = StringIO()
        call_command("run_health_maintenance", org_email="alice@acme.io", stdout=out)
        self.watch.refresh_from_db()
        self.assertEqual(self.watch.last_evaluated_on, self.today)
        self.assertIn("segment(s)", out.getvalue())

    def test_a_dry_run_leaves_segments_alone(self):
        call_command("run_health_maintenance", "--dry-run", stdout=StringIO())
        self.watch.refresh_from_db()
        self.assertEqual(self.watch.last_evaluated_on, self.yesterday)


class ChangesEndpointTests(NightlyFixture):
    def get(self, user, segment, **query):
        client = APIClient()
        client.force_authenticate(user)
        return client.get(f"{URL}{segment.pk}/changes/", query)

    def test_a_shared_viewer_reads_their_own_records_and_a_count(self):
        Segment.objects.filter(pk=self.watch.pk).update(sharing="workspace")
        self.set_health(self.pizza, "2.0")
        self.set_health(self.taco, "8.0")
        evaluate_nightly(today=self.today)
        self.assertEqual(
            self.get(self.csm, self.watch).data,
            {
                "kind": "customer",
                "days": [
                    {
                        "date": self.today.isoformat(),
                        "entered": [
                            {
                                "id": self.pizza.pk,
                                "name": "Pizza Hut",
                                "reason": ["health_category"],
                            }
                        ],
                        "left": [],
                    }
                ],
                "hidden_count": 1,
            },
        )
        admin = self.get(self.admin, self.watch).data
        self.assertEqual([row["name"] for row in admin["days"][0]["left"]], ["Taco Bell"])
        self.assertEqual(admin["hidden_count"], 0)

    def test_the_window_is_thirty_days_by_default_and_at_most_ninety(self):
        SegmentChange.objects.create(
            segment=self.watch, record_id=self.pizza.pk, change="entered",
            changed_on=self.today - timedelta(days=40),
        )  # fmt: skip
        self.assertEqual(self.get(self.admin, self.watch).data["days"], [])
        self.assertEqual(len(self.get(self.admin, self.watch, days="60").data["days"]), 1)
        self.assertEqual(len(self.get(self.admin, self.watch, days="9999").data["days"]), 1)
        self.assertEqual(self.get(self.admin, self.watch, days="soon").data["days"], [])

    def test_a_hidden_segment_reads_like_a_missing_one(self):
        hidden = self.get(self.csm, self.watch)
        missing = APIClient()
        missing.force_authenticate(self.csm)
        missing = missing.get(f"{URL}999999/changes/")
        self.assertEqual((hidden.status_code, hidden.data), (404, missing.data))

    def test_the_query_count_is_pinned(self):
        """Five queries for an admin: the segment, the caller's membership and
        role (capabilities), the changes in the window, and the names of the
        records among them the caller may open."""
        SegmentChange.objects.create(
            segment=self.watch, record_id=self.pizza.pk, change="entered", changed_on=self.today
        )
        client = APIClient()
        client.force_authenticate(User.objects.get(pk=self.admin.pk))
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(f"{URL}{self.watch.pk}/changes/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(ctx.captured_queries), 5)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.segments.tests.test_nightly --noinput`
Expected: an import error, because `services.segments` has no module `nightly`.

- [ ] **Step 3: Write the nightly step and the history**

`services/segments/nightly.py`:

```python
"""The nightly step: who entered each segment and who left.

`run_health_maintenance` calls `evaluate_nightly` after the health scores
and pulse dots are fresh. Each segment is evaluated **as its owner** (their
book is what the segment means to them), under a row lock, and compared with
`last_members`:
- the differences become `SegmentChange` rows, with the field keys that
  moved them;
- the owner gets at most one in-app alert per segment per day.

Idempotent per date: a segment evaluated today is skipped, and change rows
are unique per record per day. A segment whose owner is inactive is paused;
when the owner is back it resumes from a fresh baseline rather than report
the gap as one burst. One segment's failure is logged and skipped, as the
command's other steps are.
"""

import logging
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from services.notifications.models import Notification
from services.notifications.realtime import notify
from services.organizations.book import CHURNED

from .baseline import rebaseline
from .compiler import compile_rules
from .evaluate import MODELS, member_ids, visible_records
from .models import MAX_TRACKED_MEMBERS, Segment, SegmentChange

logger = logging.getLogger(__name__)

#: The alert quotes the owner's own name for the segment, cut to fit.
ALERT_NAME_LENGTH = 200


@dataclass
class NightlyResult:
    evaluated: int = 0
    changes: int = 0
    alerts: int = 0
    paused: int = 0
    failed: int = 0


@dataclass(frozen=True)
class Outcome:
    evaluated: bool = False
    changes: int = 0
    alerted: bool = False
    paused: bool = False


def alert_message(segment, entered, left):
    """ "Renewal risk: 3 entered, 1 left": the owner's own name, counts only."""
    return f"{segment.name[:ALERT_NAME_LENGTH]}: {entered} entered, {left} left"


def _keys(compiled, holding, pk, *, holds):
    return sorted(
        {
            key
            for (keys, _q), held in zip(compiled.conditions, holding)
            if (pk in held) == holds
            for key in keys
        }
    )


def _default_exclusions(compiled, owner, ids):
    """For organisations: which of `ids` the churned or archived default took
    out while the rules may still match them."""
    found = {}
    if compiled.kind != "customer" or not ids:
        return found
    records = visible_records("customer", owner).filter(pk__in=ids).order_by()
    for key, q in (("churned", CHURNED), ("archived", Q(is_archived=True))):
        if key in compiled.named:
            continue
        for pk in records.filter(q).values_list("pk", flat=True):
            found.setdefault(pk, []).append(key)
    return found


def change_reasons(segment, owner, entered, left, *, today):
    """Why each record moved, as field keys, never values. A record that
    entered names the top-level conditions that now hold. One that left
    names those that no longer hold, or `deleted` (it is gone), `access`
    (the owner can no longer open it), or `churned`/`archived` (the
    organisations default took it out). A pin that entered reads `pinned`.
    At most one query per condition, plus four."""
    if not entered and not left:
        return {}
    kind = segment.kind
    existing, openable = set(), set()
    if left:
        existing = set(MODELS[kind].objects.filter(pk__in=left).values_list("pk", flat=True))
        records = visible_records(kind, owner).filter(pk__in=left).order_by()
        openable = set(records.values_list("pk", flat=True))
    compiled = compile_rules(segment.rules, kind, user=owner, today=today)
    candidates = compiled.annotate(visible_records(kind, owner).filter(pk__in=entered | openable))
    holding = [
        set(candidates.filter(q).order_by().values_list("pk", flat=True))
        for _keys_of, q in compiled.conditions
    ]
    defaults = _default_exclusions(compiled, owner, openable)
    pinned = set(segment.pinned_ids or [])
    reasons = {}
    for pk in entered:
        reasons[pk] = ["pinned"] if pk in pinned else _keys(compiled, holding, pk, holds=True)
    for pk in left:
        if pk not in existing:
            reasons[pk] = ["deleted"]
        elif pk not in openable:
            reasons[pk] = ["access"]
        else:
            reasons[pk] = _keys(compiled, holding, pk, holds=False) or defaults.get(pk, [])
    return reasons


def evaluate_segment(segment_id, *, today):
    """One segment's night, under a row lock. Call inside a transaction."""
    segment = (
        Segment.objects.select_for_update(of=("self",)).select_related("owner").get(pk=segment_id)
    )
    if segment.last_evaluated_on is not None and segment.last_evaluated_on >= today:
        return Outcome()
    owner = segment.owner
    if not owner.is_active:
        if not segment.paused:
            segment.paused = True
            segment.save(update_fields=["paused"])
        return Outcome(paused=True)
    if segment.paused:
        segment.paused = False
        segment.save(update_fields=["paused"])
        rebaseline(segment, today=today)
        return Outcome(evaluated=True)

    ids = member_ids(segment, owner, today=today)
    tracked = len(ids) <= MAX_TRACKED_MEMBERS
    if segment.last_members is None or not tracked:
        # No baseline yet, or too large to track: count it, record nothing.
        segment.member_count = len(ids)
        segment.last_members = ids if tracked else None
        segment.last_evaluated_on = today
        segment.save(update_fields=["member_count", "last_members", "last_evaluated_on"])
        return Outcome(evaluated=True)

    previous, current = set(segment.last_members), set(ids)
    entered, left = current - previous, previous - current
    reasons = change_reasons(segment, owner, entered, left, today=today)
    SegmentChange.objects.bulk_create(
        [
            SegmentChange(
                segment=segment,
                record_id=pk,
                change=change,
                changed_on=today,
                reason=reasons.get(pk, []),
            )
            for change, records in (
                (SegmentChange.Change.ENTERED, entered),
                (SegmentChange.Change.LEFT, left),
            )
            for pk in sorted(records)
        ],
        ignore_conflicts=True,
        batch_size=1000,
    )
    segment.last_members, segment.member_count, segment.last_evaluated_on = ids, len(ids), today
    segment.save(update_fields=["last_members", "member_count", "last_evaluated_on"])
    alerted = bool(segment.alert_on_changes and (entered or left))
    if alerted:
        notify(
            recipient=owner,
            actor=None,
            kind=Notification.Kind.SEGMENT_CHANGES,
            message=alert_message(segment, len(entered), len(left)),
            link=f"/segments/{segment.pk}?tab=changes",
            push_on_commit=True,
        )
    return Outcome(evaluated=True, changes=len(entered) + len(left), alerted=alerted)


def evaluate_nightly(organisation=None, *, today=None):
    """Every segment due today (not yet evaluated on `today`), in one
    workspace or all of them."""
    today = today or timezone.localdate()
    result = NightlyResult()
    due = Segment.objects.filter(Q(last_evaluated_on__isnull=True) | Q(last_evaluated_on__lt=today))
    if organisation is not None:
        due = due.filter(organisation=organisation)
    for segment_id in list(due.order_by("pk").values_list("pk", flat=True)):
        try:
            with transaction.atomic():
                outcome = evaluate_segment(segment_id, today=today)
        except Exception:  # noqa: BLE001 - one segment's failure never stops the rest
            logger.exception("Segment %s: nightly evaluation failed", segment_id)
            result.failed += 1
            continue
        result.evaluated += outcome.evaluated
        result.changes += outcome.changes
        result.alerts += outcome.alerted
        result.paused += outcome.paused
    return result
```

`services/segments/history.py`:

```python
"""A segment's entries and exits, as a viewer may read them."""

from datetime import timedelta

from .evaluate import visible_records
from .models import SegmentChange

DEFAULT_DAYS = 30
MAX_DAYS = 90


def change_history(segment, viewer, *, today, days):
    """Entries and exits over the last `days` (today included), newest day
    first. Only records the viewer may open are named; the rest (deleted
    ones included) are counted in `hidden_count`. Reasons are field keys."""
    rows = list(
        SegmentChange.objects.filter(segment=segment, changed_on__gt=today - timedelta(days=days))
        .order_by("-changed_on", "record_id")
        .values_list("record_id", "change", "changed_on", "reason")
    )
    ids = {row[0] for row in rows}
    names = {}
    if ids:
        # SOC2:AUTH-02 a record is named only if the viewer may open it
        records = visible_records(segment.kind, viewer).filter(pk__in=ids).order_by()
        names = dict(records.values_list("pk", "name").distinct())
    by_day, hidden = {}, 0
    for record_id, change, changed_on, reason in rows:
        if record_id not in names:
            hidden += 1
            continue
        day = by_day.setdefault(
            changed_on, {"date": changed_on.isoformat(), "entered": [], "left": []}
        )
        day[change].append({"id": record_id, "name": names[record_id], "reason": list(reason)})
    return {"kind": segment.kind, "days": list(by_day.values()), "hidden_count": hidden}
```

- [ ] **Step 4: Add the changes view and route**

In `services/segments/views.py`, add two imports: `from services.organizations.params import int_or_none` on the line after `from services.organizations.export import CSVRenderer`, and `from .history import DEFAULT_DAYS, MAX_DAYS, change_history` on the line after `from .evaluate import …`.

Append:

```python
class SegmentChangesView(APIView):
    """GET /api/v1/segments/<id>/changes/?days=30 — the entry and exit
    history over the last `days` (1–90), naming only records the caller may
    open, plus `hidden_count` for the rest."""

    # SOC2:AUTH-02 authentication here; `get_readable` and `change_history` check the rest
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        segment = get_readable(request.user, pk)
        days = int_or_none(request.query_params.get("days"))
        days = DEFAULT_DAYS if days is None or days < 1 else min(days, MAX_DAYS)
        return Response(
            change_history(segment, request.user, today=timezone.localdate(), days=days)
        )
```

In `services/segments/urls.py`, add after the `members/<int:record_id>/` route:

```text
    path("<int:pk>/changes/", views.SegmentChangesView.as_view(), name="segment-changes"),
```

- [ ] **Step 5: Add the step to `run_health_maintenance`**

In `services/customers/management/commands/run_health_maintenance.py`, replace:

```python
        self.stdout.write(
            self.style.SUCCESS(f"{verb} {dots} pulse dot(s); {dots_had} already had today's.")
        )

        # The metric layer's month-end, after the health scores it reads are
```

with:

```python
        self.stdout.write(
            self.style.SUCCESS(f"{verb} {dots} pulse dot(s); {dots_had} already had today's.")
        )

        # Segments: who entered and who left, as each owner sees their book,
        # after the scores and pulses the rules read are fresh. Once per date
        # per segment, so a re-run is a no-op (services.segments.nightly).
        if not options["dry_run"]:
            from services.segments.nightly import evaluate_nightly

            try:
                night = evaluate_nightly(organisation)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"evaluated {night.evaluated} segment(s): {night.changes} change(s), "
                        f"{night.alerts} alert(s), {night.paused} paused, {night.failed} failed"
                    )
                )
            except Exception as exc:  # noqa: BLE001 - reported, never fatal
                self.stdout.write(self.style.WARNING(f"segments skipped: {exc}"))

        # The metric layer's month-end, after the health scores it reads are
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.segments services.customers.tests.test_health_maintenance --noinput`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add services/segments services/customers/management/commands/run_health_maintenance.py
git commit -m "feat(segments): nightly entries and exits as the owner, change history, daily alert

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: The e2e flow and the documents

**Files:**
- Create: `e2e/test_segments_flow.py`
- Modify: `docs/API_CONTRACTS.md`, `docs/audit-events.md`, `docs/data-classification.md`, `docs/product/01-prd.md`, `docs/product/02-trd.md`, `docs/product/05-backend-schema.md`

**Interfaces:**
- Consumes:
  - every route from Tasks 7–9;
  - `/auth/signup/`, `/auth/users/`, `/auth/login/`;
  - `/customers/` and `/customers/<id>/`;
  - `/notifications/`;
  - `services.segments.nightly.evaluate_nightly`, called in-process for "a night passes", as the Pipelines Ask flow stubs its model in-process.
- Produces: nothing later tasks use.

- [ ] **Step 1: Write the e2e flow**

```python
# e2e/test_segments_flow.py
"""End-to-end tier: real server, real HTTP, real test DB.

Carl builds a segment of his low-CSAT organisations. He previews it, saves
it shared with the workspace with alerts on, and pins one more. Dana, a
shared viewer, sees only her own members and a count of the rest. She cannot
edit it, and duplicates it into her own. A night passes: Carl's history and
alert show who left. Carl exports what he sees and deletes the segment. A
segment Dana may not read answers exactly like one that does not exist."""

import csv
import io
import urllib.request
from datetime import timedelta

from django.test import LiveServerTestCase
from django.utils import timezone

from e2e.http import http_delete, http_get, http_patch, http_post
from services.segments.nightly import evaluate_nightly

LOW_CSAT = {"match": "all", "conditions": [{"field": "csat_score", "op": "lt", "value": 60}]}


def http_get_text(url, token):
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request) as response:
        return response.status, response.headers.get("Content-Type"), response.read().decode()


class SegmentsFlowTests(LiveServerTestCase):
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

    def created(self, path, payload, token):
        status, body = http_post(self.api(path), payload, token=token)
        self.assertEqual(status, 201, body)
        return body

    def names(self, body):
        return [row["name"] for row in body["results"]]

    def test_full_flow(self):
        # 1. An organisation signs up; its admin adds two CSMs.
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

        # 2. Carl's two organisations and Dana's one, each owned by its creator.
        pizza = self.created("/customers/", {"name": "Pizza Hut", "csat_score": "40"}, carl)["id"]
        burger = self.created("/customers/", {"name": "Burger Barn", "csat_score": "85"}, carl)[
            "id"
        ]
        self.created("/customers/", {"name": "Taco Bell", "csat_score": "30"}, dana)

        # 3. The builder's preview: Carl's one low-CSAT organisation, not Dana's.
        status, body = http_post(
            self.api("/segments/preview/"), {"kind": "customer", "rules": LOW_CSAT}, token=carl
        )
        self.assertEqual(status, 200, body)
        self.assertEqual((body["count"], self.names(body)), (1, ["Pizza Hut"]))

        # 4. Saved, shared with the workspace, alerts on; then Burger Barn pinned in.
        segment = self.created(
            "/segments/",
            {
                "name": "Low CSAT",
                "kind": "customer",
                "rules": LOW_CSAT,
                "sharing": "workspace",
                "alert_on_changes": True,
            },
            carl,
        )
        sid = segment["id"]
        self.assertEqual(segment["member_count"], 1)
        status, body = http_patch(
            self.api(f"/segments/{sid}/members/{burger}/"), {"state": "pinned"}, token=carl
        )
        self.assertEqual((status, body["pinned_ids"]), (200, [burger]))
        status, body = http_get(self.api(f"/segments/{sid}/members/?sort=name"), token=carl)
        self.assertEqual(status, 200, body)
        self.assertEqual(self.names(body), ["Burger Barn", "Pizza Hut"])
        self.assertEqual((body["hidden_count"], body["summary"]["members"]), (0, 2))

        # 5. Dana: her own member and a count of Carl's; no edits; a copy of her own.
        status, body = http_get(self.api("/segments/?scope=shared"), token=dana)
        self.assertEqual([row["name"] for row in body], ["Low CSAT"])
        status, body = http_get(self.api(f"/segments/{sid}/members/"), token=dana)
        self.assertEqual((self.names(body), body["hidden_count"]), (["Taco Bell"], 2))
        status, body = http_get(self.api(f"/segments/{sid}/"), token=dana)
        self.assertEqual((body["pinned_ids"], body["is_owner"]), ([], False))
        status, body = http_patch(self.api(f"/segments/{sid}/"), {"name": "Mine now"}, token=dana)
        self.assertEqual(status, 403, body)
        copy = self.created(f"/segments/{sid}/duplicate/", {}, dana)
        self.assertEqual(
            (copy["name"], copy["sharing"], copy["pinned_ids"]), ("Low CSAT (copy)", "private", [])
        )
        status, body = http_get(self.api(f"/segments/{copy['id']}/members/"), token=dana)
        self.assertEqual((self.names(body), body["hidden_count"]), (["Taco Bell"], 0))

        # 6. A night passes: Pizza Hut's CSAT recovers and it leaves; Carl is told.
        status, body = http_patch(
            self.api(f"/customers/{pizza}/"), {"csat_score": "90"}, token=carl
        )
        self.assertEqual(status, 200, body)
        tomorrow = timezone.localdate() + timedelta(days=1)
        evaluate_nightly(today=tomorrow)
        status, body = http_get(self.api(f"/segments/{sid}/changes/"), token=carl)
        self.assertEqual(status, 200, body)
        self.assertEqual(
            body["days"],
            [
                {
                    "date": tomorrow.isoformat(),
                    "entered": [],
                    "left": [{"id": pizza, "name": "Pizza Hut", "reason": ["csat_score"]}],
                }
            ],
        )
        status, body = http_get(self.api("/notifications/"), token=carl)
        alerts = [row for row in body if row["kind"] == "segment_changes"]
        self.assertEqual(
            [(row["message"], row["link"]) for row in alerts],
            [("Low CSAT: 0 entered, 1 left", f"/segments/{sid}?tab=changes")],
        )

        # 7. Carl exports what he sees: the pinned organisation that stayed.
        status, content_type, text = http_get_text(
            self.api(f"/segments/{sid}/members/export.csv"), carl
        )
        self.assertEqual(status, 200)
        self.assertTrue(content_type.startswith("text/csv"))
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(
            (rows[0][0], [row[0] for row in rows[1:]]), ("Organization", ["Burger Barn"])
        )

        # 8. A segment Dana may not read answers like a missing one; Carl deletes his.
        private = self.created("/segments/", {"name": "Private", "kind": "customer"}, carl)["id"]
        hidden = http_get(self.api(f"/segments/{private}/"), token=dana)
        missing = http_get(self.api("/segments/999999/"), token=dana)
        self.assertEqual(hidden, missing)
        self.assertEqual(hidden[0], 404)
        status, _body = http_delete(self.api(f"/segments/{sid}/"), token=carl)
        self.assertEqual(status, 204)
        status, _body = http_get(self.api(f"/segments/{sid}/"), token=carl)
        self.assertEqual(status, 404)
```

- [ ] **Step 2: Run the e2e test**

Run: `venv/bin/python manage.py test e2e.test_segments_flow --noinput`
Expected: PASS. If step 2 fails because `csat_score` is not accepted on create, check `CustomerSerializer.Meta.fields`: it is listed there, so a failure means the value's format, `"40"`, not the field.

- [ ] **Step 3: Update the documents** (use skill `api-contracts` for the first one)

**`docs/API_CONTRACTS.md`**: insert this section immediately before `## \`scenarios\` — Automation builder`, after the `---` that ends the previous section:

````markdown
## `segments` — Segments (`/segments`)

Built for the tools section's first delivery (spec: react-ts-app
`docs/superpowers/specs/2026-10-03-segments-design.md` §1–§2). A segment
stores rules, never members. Every read is computed for the caller over the
records they may open (`services/segments/evaluate.py`), so a segment only
ever holds records its viewer may open. It is unrelated to
`services/customers/segments.py`, the "Size band" revenue brackets.

**Rules.** `{"match": "all" | "any", "conditions": [...]}`.
- A condition is `{"field", "op", "value"?}`, or one group `{"group": {"match", "conditions": [...]}}`.
- Groups do not nest.
- At most 20 conditions, counting each one inside a group.
- An empty list matches nothing: the segment is then its pins.

Fields (`services/segments/registry.py`) take operators by type:

| Type | Operators | Value |
|---|---|---|
| number, percent | `gt lt between is_empty is_not_empty` | a number; `between` is `[low, high]`, inclusive |
| days (days since) | `gt lt between is_empty` | whole days. Never counts as more than any number; `is_empty` means never |
| date | `within_next within_last gt lt between is_empty is_not_empty` | `within_*` take 1–3650 days; dates are `YYYY-MM-DD` |
| choice | `is is_not in` | one of the field's values; `in` takes 1–100 |
| text | `is is_not in is_empty is_not_empty` | up to 100 characters |
| boolean | `is` | `true` or `false` |
| owner | `is is_not in` | a person's id in the workspace, or `"unassigned"` |
| record | `is is_not in` | an organisation, account or product id |

The fields per kind:
- `customer` (organisations): `lifecycle_stage`, `health_score`, `health_category`, `csat_score`, `nps_score`, `nps_band`, `ces_percentage`, `arr`, `renewal_date`, `owner`, `product`, `seat_use`, `open_tickets`, `last_touch`, `ai_pulse`, `csm_pulse`, `churned`, `archived`, `created`.
- `account`: the same without `ces_percentage`, `product`, `seat_use`, `churned` and `archived`, plus `organisation` (a linked organisation).
- `contact`: `role`, `sentiment`, `status`, `language`, `last_contacted`, `organisation` (direct or through the account), `account`, and `parent.<field>`.
  - `parent.<field>` is any organisation or account field except `organisation`.
  - It is read on the contact's own organisation or account.
  - A field the account lacks never matches an account-level contact.
- Any kind: `attr:<api_name>`, an AI attribute that applies to the kind. It is read as its newest value, with its value type's operators plus `is_empty`/`is_not_empty` (not answered yet).

What each field means:
- `arr`:
  - for an organisation, the column the workspace's ARR mapping names (`global_attributes`), converted to the workspace currency with its FX rates; a currency with no rate never matches;
  - for an account, its own `arr`.
- `renewal_date within_next`: an overdue renewal counts as within.
- `open_tickets`: unresolved tickets on the record itself that the caller may read under the department rule.
- `last_touch`: the health rubric's newest contact.
- `is_not`: also matches "no value".
- Defaults: churned and archived organisations are left out unless a rule names `churned` or `archived`. Pins are added after the rules and exclusions removed, then visibility is applied last.

**Refusals (400)** read `{"rules": ["<text>"]}`. They cover:
- an unknown field (`Unknown field "x" for organisations.`);
- an operator the type does not take (`"is" cannot be used with Health score.`);
- a bad value, a nested group, or more than 20 conditions.

An id the writer cannot open reads `Not an organisation you can open.`, `Not an account you can open.`, `Not a product in your workspace.` or `Not a person in your workspace.`. Each reads the same for a missing id.

**Rules as read.** Every response's `rules` replaces each id the caller cannot open with `null`. `labels` (`{"organisations", "accounts", "products", "people"}`, each `{"<id>": name}`) names only what the caller may open; people are named only from their own workspace. When rules are evaluated for a caller, ids are narrowed to what they may open, so `null` matches nothing.

**Access.**
- Readers:
  - the owner;
  - everyone in the workspace when `sharing` is `workspace`;
  - the chosen teammates when `sharing` is `people`.
- A segment the caller may not read is a 404 identical to a missing one.
- Writes (PATCH, DELETE, pin and keep out) are owner only: another reader gets `403 {"detail": "Only the segment's owner can change it."}`.
- Limits: 50 segments per owner, and 500 pinned and 500 kept out per segment.

### `GET /api/v1/segments/`

Auth: `IsAuthenticated`. Query: `scope=mine|shared|all` (default all), `search=` (name contains). Unpaginated, by name:

```json
[
  {
    "id": 7, "name": "Renewal risk", "kind": "customer",
    "owner": {"id": 4, "name": "Carl CSM"}, "is_owner": true,
    "sharing": "workspace", "paused": false, "member_count": 41,
    "today": {"entered": 3, "left": 1},
    "sparkline": [38, 38, 39, "… 30 sizes, oldest first …", 41],
    "updated_at": "2026-10-03T09:12:00Z"
  }
]
```

`member_count`, `today` and `sparkline` are the owner's nightly figures: counts only, naming nobody. `sparkline` is rebuilt from the count and the change history, and is `[]` before the first evaluation.

### `POST /api/v1/segments/`

Body: `{name, kind: "customer" | "account" | "contact", description?, rules?, sharing?: "private" | "workspace" | "people", shared_with?: [user ids], alert_on_changes?}`.
- `shared_with` must be 1–50 active teammates in the caller's workspace (never the caller) when `sharing` is `people`, and it is cleared otherwise.
- The caller becomes the owner.
- The membership baseline is taken now, so no history is written for the save.

`201` returns the segment, as `GET` below. `400 {"detail": "You can own at most 50 segments."}` at the limit. Audited as `segment.created`, plus `segment.shared` when it is not private.

### `GET/PATCH/DELETE /api/v1/segments/<id>/`

`GET` returns:

```json
{
  "id": 7, "name": "Renewal risk", "description": "", "kind": "account",
  "rules": {"match": "all", "conditions": [{"field": "organisation", "op": "in", "value": [12, null]}]},
  "labels": {"organisations": {"12": "Pizza Hut"}, "accounts": {}, "products": {}, "people": {}},
  "pinned_ids": [31], "excluded_ids": [],
  "sharing": "people", "shared_with": [{"id": 5, "name": "Dana CSM"}],
  "owner": {"id": 4, "name": "Carl CSM"}, "is_owner": true,
  "alert_on_changes": true, "paused": false, "member_count": 41,
  "last_evaluated_on": "2026-10-03", "created_at": "…", "updated_at": "…"
}
```

- `pinned_ids` and `excluded_ids` list only records the caller may open.
- `PATCH` takes the POST fields except `kind`, which cannot change (`400 {"kind": ["A segment's kind cannot change."]}`).
  - Changing `rules` re-takes the baseline.
  - Audited as `segment.updated` with the changed field names, and as `segment.shared` when `sharing` or `shared_with` changed.
- `DELETE` → `204`, audited as `segment.deleted`.

### `POST /api/v1/segments/<id>/duplicate/`

Any reader. It creates a private copy owned by the caller, named "<name> (copy)", with alerts off.
- Its rules are as the caller reads them: ids they cannot open are `null`.
- It keeps only the pins and keep-outs the caller may open.
- `201` returns the copy. The limit applies. Audited as `segment.duplicated` (`source`).

### `GET /api/v1/segments/<id>/members/`

The members the caller may open, as the kind's own list reads them:
- organisations: `/organizations/portfolio/`'s rows and its `sort`, `group`, `group_value`, `search`, `cursor` and `limit`;
- accounts: `/accounts/portfolio/`'s;
- contacts: `/contacts/`'s rows and filters (`search`, `customer`, `account`, `sentiment`, `role`), by name, with `cursor` and `limit`.

Unknown values are ignored. Body:

```json
{
  "kind": "customer", "results": ["…the list's own rows…"], "next_cursor": "opaque",
  "count": 41, "groups": [], "currency": "USD", "hidden_count": 12,
  "summary": {
    "members": 41, "arr": 512000.0, "unconverted_count": 0, "avg_health": 5.4,
    "avg_csat": 71.2, "entered_7d": 6, "left_7d": 2, "currency": "USD"
  }
}
```

- `summary` covers every member the caller may open. Search narrows the rows, not the tiles.
- `arr` follows the rule's ARR: organisations through the workspace mapping, unconvertible ones counted in `unconverted_count`.
- A contacts segment's `summary` has `arr`, `avg_health` and `avg_csat` as `null`, and adds `contacts` (the Contacts list's own summary).
- `entered_7d` and `left_7d` count only records the caller may open.
- `hidden_count` is how many of the owner's members the caller cannot open: a count only, `0` for the owner.

### `GET /api/v1/segments/<id>/members/export.csv`

Every member the caller may open, in list order (`search` and `sort` honoured), with the kind's export columns:
- organisations: the Organizations export;
- accounts: the Accounts export;
- contacts: Name, Email, Phone, Role, Status, Sentiment, Language, Last Contacted, Organisation, Account.

Formula cells are neutralised. Audited as `segment.exported` with `count` and the parameter names only.

### `PATCH /api/v1/segments/<id>/members/<record_id>/`

Body `{"state": "pinned" | "excluded" | "none"}`. Owner only.
- The record must be one the owner may open; a missing one and a hidden one read the same 404.
- Pinning removes a keep-out and the other way round. The baseline is re-taken.
- Returns `{pinned_ids, excluded_ids}`.
- `400 {"detail": "A segment can pin at most 500 records, and keep out as many."}` at the limit.
- Audited as `segment.member_pinned` (`record_id`, `pinned`) or `segment.member_excluded` (`record_id`, `excluded`); clearing records `false`.

### `GET /api/v1/segments/<id>/changes/`

`?days=` 1–90 (default 30). Entries and exits, newest day first:

```json
{
  "kind": "customer",
  "days": [{"date": "2026-10-03",
            "entered": [{"id": 12, "name": "Pizza Hut", "reason": ["csat_score"]}],
            "left": []}],
  "hidden_count": 3
}
```

- Only records the caller may open are named. The rest, deleted ones included, are counted in `hidden_count`.
- `reason` is field keys, never values:
  - for an entry, the conditions that now hold;
  - for an exit, those that no longer hold;
  - or one of `pinned`, `deleted`, `access` (the owner can no longer open it), `churned` or `archived`.

### `POST /api/v1/segments/preview/`

Body `{kind, rules, pinned_ids?, excluded_ids?}`, checked exactly as a save is. It returns `{kind, count, results, summary}`:
- `results` are the first ten members by name: `{id, name, owner, health: {score, category}}`, or `{id, name, role, parent: {kind, id, name}}` for contacts.
- `summary` is the tiles, with `entered_7d`/`left_7d` `null`.

Nothing is saved.

**Nightly.** `run_health_maintenance` evaluates each segment once per date, as its owner (`services/segments/nightly.py`). It writes the entries and exits and updates `member_count`.
- If `alert_on_changes` is set and something moved, the owner gets one in-app notification. Its kind is `segment_changes`, its message is `"<name>: 3 entered, 1 left"` and its link is `/segments/<id>?tab=changes`.
- An inactive owner's segments are paused. They resume from a fresh baseline when the owner is active again.
- Above 100,000 members the count is kept and no history is recorded.
````

**`docs/audit-events.md`**: after the `pipelines.bulk_updated` row, add:

```markdown
| `segment.created` | `services.segments.views.SegmentListCreateView` | user | Segment (`target_repr` is "Segment <id>", never the name) | `kind` |
| `segment.updated` | `services.segments.views.SegmentDetailView` (PATCH) | user | Segment | `fields`: the changed field names only; a PATCH that changes nothing records nothing |
| `segment.shared` | `SegmentListCreateView` (a non-private create) and `SegmentDetailView` (PATCH changing `sharing` or `shared_with`) | user | Segment | `sharing`, `shared_with` (user ids) |
| `segment.duplicated` | `services.segments.views.SegmentDuplicateView` | user | the new Segment | `source`: the duplicated segment's id |
| `segment.deleted` | `services.segments.views.SegmentDetailView` (DELETE) | user | Segment | `kind` |
| `segment.member_pinned` / `segment.member_excluded` | `services.segments.views.SegmentMemberView` | user | Segment | `record_id`, and `pinned` or `excluded`: true when set, false when cleared |
| `segment.exported` | `services.segments.views.SegmentMembersExportView` | user | Segment | `count` of rows, `params`: the query-parameter names used (never their values) |
```

**`docs/data-classification.md`**: after the Pipelines CSV export row, add:

```markdown
| Segments (`services/segments`: `Segment`, `SegmentChange`) | a segment's name, description and rules (field keys, operators, thresholds, dates, choice values, record and person ids); pinned and kept-out ids; the owner's member ids (`last_members`); the entry and exit history (record ids, dates, field keys, never values) | confidential | members computed per reader over what they may open (AUTH-02); ids a reader cannot open read `null` and are never named; a shared viewer gets a count only; writes owner-only and audited (LOG-01) |
| Segment CSV export (`GET /segments/<id>/members/export.csv`) → the requester's device | the kind's export columns (Organizations, Accounts, or a contact's name, email, phone, role, status, sentiment, language, last contacted, organisation and account) for the members the requester may open | confidential | twice filtered (AUTH-02); audited `segment.exported` (LOG-01); formula cells neutralised against CSV injection |
| Segment alert (in-app `Notification`, kind `segment_changes`) | the segment's name and counts of entries and exits | internal | sent to the owner only; names no record |
```

**`docs/product/01-prd.md`**:
- After the "Pipelines (opportunities and risks)" feature row, add:
  `| Segments | Built (backend) | Saved, rule-based groups of organisations, accounts or contacts: match all or any of up to 20 conditions (one level of groups) over a fixed field registry, including AI attributes and, for contacts, their organisation's or account's fields; pins and keep-outs. Shared privately, with the workspace or chosen teammates; a viewer sees only the members they may open plus a count of the rest; only the owner edits. Members read as each list's own rows with tiles (members, ARR covered, average health and CSAT, entered and left in 7 days), CSV export, a live preview, and a nightly entry/exit history with an optional daily alert to the owner |`
- In the change log, after the 2026-10-01 line, add `| 2026-10-03 | Segments (backend): model, rule registry and compiler, endpoints, nightly entries and exits, alert |`.

**`docs/product/02-trd.md`**: after the `services/organizations/`, `services/accounts_portfolio/`, `services/pipelines_portfolio/` row, add:
`| `services/segments/` | Saved segments. `registry.py` names every field a rule may use and the operators its type takes; `rules.py` checks rules on the way in (ids the writer cannot open read like missing ones) and redacts them on the way out; `compiler.py` turns them into one `Q` over the reader's visible book (reusing the lists' health, NPS, churn and renewal rules, the rubric's last touch, and a latest-value subquery for AI attributes); `evaluate.py` adds pins, removes keep-outs and applies visibility last; members reuse the Organizations and Accounts books through `load_portfolio(scope=)` and the Contacts list; `nightly.py` runs in `run_health_maintenance` as each owner |`

**`docs/product/05-backend-schema.md`**:
- In section 10's table, change the `Notification` row's kinds to `copilot_invite, copilot_handoff, customer_assigned, account_assigned, question_asked, question_answered, segment_changes`.
- After the `### \`account_story\`` section, add:

  ```markdown
  ### `segments`

  | Model | Purpose |
  |---|---|
  | `Segment` | A saved group of one kind (`customer`, `account` or `contact`): `rules` JSON (`{match, conditions}`, one level of groups, at most 20 conditions), `pinned_ids` and `excluded_ids` (≤ 500 each), `sharing` (`private`, `workspace`, `people` with `shared_with`), `alert_on_changes`, `paused` (set while the owner is inactive), and the nightly baseline: `last_members` (sorted ids as the owner saw them; null before the first run or above 100,000), `member_count`, `last_evaluated_on`. Owned by one person (cascade); at most 50 per owner. Members are never stored for a reader: they are computed per request |
  | `SegmentChange` | One record entering or leaving one segment on one day (unique per segment, record and day): `record_id`, `change` (`entered`/`left`), `changed_on`, `reason` (field keys that moved it, or `pinned`, `deleted`, `access`, `churned`, `archived`; never values) |
  ```

- In section 11's table, after the Opportunities and risks row, add:
  `| Segments | Read by the owner, the workspace (`workspace`) or chosen teammates (`people`); written by the owner only. Members are the rules over the reader's own visible records, pins added, keep-outs removed, visibility last; ids in rules a reader cannot open read null; a shared viewer gets a count of the rest | `services/segments/access.py`, `evaluate.py` |`

- [ ] **Step 4: Format and lint** (ruff formats Markdown in this repo)

Run: `venv/bin/ruff format . && venv/bin/ruff check . && venv/bin/ruff format --check .`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add e2e/test_segments_flow.py docs
git commit -m "test(e2e): segments over real HTTP; docs for segments

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Final verification

**Files:** none change unless a check fails.

- [ ] **Step 1:** `venv/bin/python manage.py test --parallel --noinput`. Expected: 0 failures. (Serial runs hit the CI's 30-minute timeout, so always use `--parallel`.) Note the new tests' share of the wall time. `services.segments` plus the e2e flow should add well under a minute; if they do not, find the slow class and move its data into `setUpTestData`.
- [ ] **Step 2:** `venv/bin/ruff check . && venv/bin/ruff format --check .`. Expected: clean. If import order is flagged (I001) in a file a task edited, run `venv/bin/ruff check --fix <file>`.
- [ ] **Step 3:** `venv/bin/python manage.py makemigrations --check --dry-run`. Expected: "No changes detected". Both migrations (Task 1) are committed.
- [ ] **Step 4:** `grep -n "SOC2:AUTH-02" services/segments/*.py`. Expected: one at each of these:
  - `access.readable_segments`, `get_owned`;
  - `evaluate.visible_records`, `members_queryset`, `hidden_count`, `openable_ids`;
  - `rules._openable` and `validate_rules`' id check;
  - `compiler.open_tickets_expression`, `Compiler._openable`;
  - `payloads.segment_payload`;
  - `summary.recent_changes`;
  - `members._contact_rows`;
  - `history.change_history`;
  - the serializer's teammate queryset;
  - every view.
- [ ] **Step 5:** `grep -n "audit.record" services/segments/views.py`. Expected: each sits on a line with or right after `# SOC2:LOG-01`, and the eight action names are all present.
- [ ] **Step 6:** `grep -rn "segments.py\|REVENUE_BRACKETS" services/segments`. Expected: no match. The two "segments" stay unrelated.
- [ ] **Step 7:** If anything fails, fix it with superpowers:systematic-debugging, commit as `fix(segments): <what>`, and repeat Steps 1–6. Use superpowers:verification-before-completion before reporting. Do not push. The PR opens against `main` and **merges and deploys before the frontend PR**.

---

## Self-review

**Spec coverage (§1, §2, §4 item 1, §5 backend):**

- §1, the app and models:
  - `services/segments`, `Segment` (every listed field, plus `member_count`) and `SegmentChange` (record, change, date, reason) → Task 1.
  - The unrelated `customers/segments.py` stays untouched → Global Constraints, Task 11 Step 6.
- Rule shape, all/any, one level of groups → Task 2 (`_check_block`, `ShapeTests`) and Task 4 (`test_groups_and_match_any`).
- Operators, each field taking only its type's → Task 2 (`OPERATORS`, `FieldTests`), Decision 2.
- The field registry:
  - organisations: every field in §1's list, with health category bands, the NPS score and band, CES, ARR through the mapping and converted, renewal (overdue counts), owner or `unassigned`, product, seat use, open tickets, days since touch, both pulses, churned, archived and created, plus AI attributes;
  - accounts: the same where an account has the field;
  - contacts: role, sentiment, status, language, days since contacted and `parent.`.

  → Task 2 (registry), Task 4 (organisations, `test_every_field_and_operator` plus the coverage test), Task 5 (accounts, contacts, `parent.`).
- Unknown fields refused with a clear 400 → Task 2, Task 7 `test_bad_rules_are_a_400_naming_the_problem`.
- Churned and archived defaults → Task 4 (`Compiled.apply`; Delta and Echo in the cases), Task 6 (a pinned churned organisation stays in).
- Compilation to one `Q` reusing `health_q`, `renewing_q`, `NPS_Q`, `CHURNED`, the touch and ticket figures, the contact last-contact field and an AI latest-value subquery → Task 4. Decisions 8–9 say where the ticket count deliberately differs.
- Pins added, exclusions removed, visibility last → Task 6.
- Limits of 50 segments and 20 conditions → Task 2 (20), Task 7 (50, on create and duplicate).
- §2 privacy:
  - members computed for the viewer over `visible_customers`, `visible_accounts` or `visible_children_q` → Task 6;
  - `hidden_count` → Tasks 6 and 8;
  - hidden pins neither shown nor named → Tasks 6, 7 and 8;
  - rule values naming hidden records → Tasks 2, 7 and 8 (Decision 6);
  - owner names from the viewer's workspace → Task 2;
  - AI attribute values only on openable records → Task 4 (Decision 8);
  - missing and hidden read the same → Tasks 7, 8 and 9 and the e2e flow.
- §2's nine endpoints → Task 7 (list, create, get/patch/delete, duplicate), Task 8 (members, export, preview, member pin), Task 9 (changes).
- Nightly step:
  - after recalculation and pulses, as the owner, compared with `last_members`, writes changes, updates `last_members`/`last_evaluated_on`, sparkline rebuilt from count and changes → Task 9 (sparkline on read, Task 7).
  - alert once per segment per day with a new `Notification.Kind` → Tasks 1 and 9;
  - inactive owner pauses → Task 9;
  - idempotent per date → Task 9;
  - a failure logged and skipped → Task 9.
- Audit, all eight events with ids and field names only → Tasks 7–8; catalogued in Task 10.
- Performance: pinned query counts flat as members grow, and the preview limited to ten plus totals → Tasks 7, 8 and 9 (pins); Task 8 (`PREVIEW_SIZE`).
- §4 item 1, docs: API_CONTRACTS, audit-events, data-classification, PRD, TRD and schema → Task 10. Merge order → header and Task 11.
- §5 backend:
  - unit tests: every field and operator per kind (Tasks 4–5, with coverage tests that fail if a field or operator is added without a case); groups (Task 4); pins and exclusions (Task 6); churned and archived defaults (Task 4); ARR conversion (Task 4: Beta's EUR, the mapping, a currency with no rate); AI latest value (Task 4: Alpha's newer answer, Gamma's insufficient and type-changed answers); contact `parent.` (Task 5); unknown fields and operators refused (Task 2).
  - privacy:
    - blind to one account → Tasks 2, 6 and 8;
    - another tenant → Tasks 6, 7 and 8;
    - a shared viewer's own members and `hidden_count` → Tasks 6 and 8, e2e;
    - rule values naming hidden records → Tasks 2, 7 and 8;
    - hidden pins → Tasks 6, 7 and 8;
    - non-owner writes refused → Tasks 7 and 8, e2e;
    - a hidden segment's 404 → Tasks 7, 8 and 9, e2e.
  - nightly: entries and exits, idempotency, the alert once per segment per day, and a paused owner → Task 9.
  - other: pinned query counts (Tasks 7, 8 and 9), the summary equals the members it covers (Task 8 `test_the_tiles_equal_the_members_they_cover`), and an e2e flow (Task 10).
- Out of scope, and left out: Ask on Segments; survey and campaign audiences; scenario triggers on entry and exit (`SegmentChange` is ready for them); custom-object fields; per-message sentiment; reporting dashboards; on-save entry detection. The frontend (§3, §4 item 2) is the next plan, in `react-ts-app`.

**Spec ambiguities, stated rather than settled silently:**

1. **"The same field/operator/value shape the scenario condition editor uses."** The scenario engine stores `conditionAttribute`/`conditionOperator`/`conditionValue`, with operators `equals`, `greater_than`, `is_one_of` and so on. This plan follows the spec's own JSON example and operator list (`field`/`op`/`value`; `is`, `gt`, `within_next` …), and borrows only the engine's `attr:<api_name>` spelling. Scenarios v2 should adopt the segment shape, not the reverse.
2. **Organisation and account fields** are not in §1's field list, but the builder's "server-searched organisation or account picker" and §2's "an organisation, account … the viewer cannot open" need them. They are added (Decision 5).
3. **ARR on the members page.** The spec says ARR goes "through the workspace's global-attribute mapping". The Organizations list's rows ignore that mapping (they always convert `arr_billed_at_account`). The ARR rule and tile use the mapping; the reused rows keep the list's figure; they agree under the default mapping. Making the Organizations list honour the mapping is a separate change.
4. **Open tickets.** The spec says to reuse the health-input annotation, which counts every department's tickets. That would break "twice filtered", so the count reads only tickets the viewer may read (Decision 9).
5. **"Cursor paging … as the kind's portfolio endpoint" for contacts.** `/contacts/` pages by page number. Contacts members use the Contacts list's rows and filters with the portfolios' keyset cursor, by name, so all three kinds page one way.
6. **The sparkline** is "rebuilt from the current count and the changes". It is computed when the list is read, never stored, so no daily copy of anything exists.
7. **Edits and the history.** The spec does not say whether editing rules or pins records entries and exits. This plan re-baselines on every such edit (Decision 11), so the history (and delivery 3's triggers) see data moving, not edits.
8. **An empty rule list** matches nothing (Decision 3). The spec does not say.

**Placeholder scan:** no TBD, TODO or "similar to Task N". Every code step carries its code. Each pinned query count is derived and listed in its test's docstring, with the rule "an extra query is a bug to fix, not a number to bump".

**Type consistency:**
- `compile_rules(rules, kind, *, user, today)` is used the same way in Tasks 4–6 and 9.
- `Compiled.apply`/`.annotate`/`.conditions`/`.named` are used in Tasks 6 and 9.
- `members_queryset`, `member_ids`, `hidden_count`, `openable_ids` and `visible_records` (Task 6) are used in Tasks 7–9.
- `Draft(kind, rules, pinned_ids, excluded_ids)` is used in Tasks 6 and 8.
- `rebaseline(segment, *, today)` is used in Tasks 7–9.
- `present_rules(rules, kind, *, user) -> (rules, labels)` is used in Tasks 2 and 7.
- `validate_rules(rules, kind, *, user)` is used in Tasks 2, 7 and 8.
- `segment_summary(members, segment, viewer, *, today, rates=None)` is used in Task 8.
- `load_portfolio(user, params, *, today, scope=None)` (Task 3) is used in Task 8.
- `change_history(segment, viewer, *, today, days)` is used in Task 9.
- `evaluate_nightly(organisation=None, *, today=None)` is used in Task 9 and the e2e.
- Field keys are the same in the registry, the compiler cases, the reasons and the docs:
  - `health_category`, `nps_band`, `arr`, `seat_use`, `open_tickets`, `last_touch`, `ai_pulse`, `csm_pulse`, `churned`, `archived`, `created`;
  - `last_contacted`, `organisation`, `account`;
  - `parent.`, `attr:`.
