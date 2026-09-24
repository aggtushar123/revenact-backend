# Ask Revenact on the Dashboard, backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `POST /api/v1/copilot/messages/` accepts an optional structured `context` (where on the Dashboard the question was asked), recomputes that area's figures on the server for the asker's own filtered book, grounds the answer in them and in the records behind them, meters it under a new `dashboard` purpose, and stores the context on the user turn and the origin on the conversation.

**Architecture:** Three new modules in `services/copilot`. `dashboard_context.py` holds the catalogue (`DASHBOARD_VIEWS`, mirroring the frontend's `areas.ts`) and the DRF serializer that validates `context` and silently intersects focus ids with the viewer's filtered book. `dashboard_figures.py` computes each area's numbers by calling the same service code as that area's endpoint. `dashboard_grounding.py` turns the numbers into a labelled digest, adds the records for the focus or named companies through the existing retrieval, and builds the system prompt. `SendMessageView` branches on `context`; without it, nothing changes. Two small extractions make the reuse possible: `attention.rules.current_item` (the snooze key check) and `customers.ticket_filters.filtered_tickets` (the `/tickets/stats/` filter).

**Tech Stack:** Django 5, DRF, PostgreSQL, `TestCase` / `APITestCase` / `LiveServerTestCase`, the Anthropic client stubbed with `unittest.mock.patch`.

**Spec:** `react-ts-app/docs/superpowers/specs/2026-09-24-dashboard-ask-revenact-design.md` (frontend repo, branch `feat/dashboard-ask`), sections 1, 2, 3, 5 (Backend) and 6 (item 1). Read it before starting any task.

## Global Constraints

- Contract: `POST /api/v1/copilot/messages/` gains an optional `context`. Absent (or `null`), the endpoint behaves exactly as today (Communications keeps its `[About: …]` text prefix).
- `surface`: only `"dashboard"`. `area`: `overview | revenue | health | support`. `view`: one of that area's sub-views from `DASHBOARD_VIEWS`; the Overview has no sub-view (`view: null`).
- `filters`: only the shared keys `owner`, `lifecycle`, `customer`. Unknown keys are dropped. Unknown values are ignored, as the dashboard endpoints already do.
- `focus`: `null`, `{"kind": "companies", "ids": [..]}` (at most 200 ids, intersected with the viewer's filtered, visible book; ids outside it are silently dropped), or `{"kind": "attention", "key": "risk:12"}` (must be an item on the viewer's own attention list, the same check snoozing uses).
- Errors are a `400` with field errors for a wrong `surface`, `area`, `view`, `focus.kind`, an over-long `ids` or a key not on the list. An attention key that is not the viewer's returns the same error as a malformed one.
- The response is unchanged, plus `context` echoed on the user message and `origin` on the conversation.
- First filter, always: `forecast.filtered_customers(user, filters)`, which starts from `live_customers(user)`. Nothing outside that set is read.
- Each area's digest equals its endpoint for the same user and filters. Money is converted to the organisation currency and labelled with it.
- Records come through the existing retrieval (`retrieve_with_sources(..., viewer=user)`), which keeps its record-level rules (`visible_tickets`, `visible_emails`, notes, contributions). Anomaly evidence comes through `visible_evidence`. The anomaly title is the stored title only for `sees_everything(user)`, otherwise "Similar reports across N of your companies".
- The system prompt tells the model to answer only from the digest and the sources, to say when the answer is not there, and to cite sources. The digest is labelled with the area, view and filters it was computed for.
- Metering: purpose `dashboard` in `usage.PURPOSES` and `skills.py` (surface `/dashboard`); an exhausted budget returns `429`.
- Storage: `Message.context` (JSON, nullable; the validated context after the focus intersection; ids and filter values only). `Conversation.origin` (JSON, nullable; set once from the first dashboard message's context without its focus; never overwritten). Both are classified `internal`.
- The model call is always stubbed in tests (`patch("services.copilot.views.get_completion")`), and tests assert on the prompt it receives.
- Every endpoint change updates `docs/API_CONTRACTS.md` in the same PR (`.claude/skills/api-contracts`); product docs per the product-docs rule (PRD, backend schema); new fields get rows in `docs/data-classification.md`.
- Tests: unit, integration and e2e (`.claude/skills/testing`). Run with `venv/bin/python manage.py test <label> --noinput`.
- `venv/bin/ruff format --check .` and `venv/bin/ruff check .` pass. Ruff also formats Python inside Markdown fences, so run `venv/bin/ruff format` on any doc you touch that has code.
- Commits follow `.claude/skills/commit-messages` and end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Pre-flight: where the spec and the code disagree

Each was resolved as below. The executor does not need to re-decide them.

1. **The forecast bridge has no "new" step.** The spec lists "starting ARR, new, expansion, contraction, churn", but `forecast.build_bridge` returns `opening_arr, churn, contraction, expansion, forecast_arr, net_change, nrr`. The forecast covers the existing book only. **Resolution:** the Revenue digest carries every bridge field the endpoint returns and says in one line that there is no new-business figure. Nothing is invented.
2. **Support has no lifecycle filter, and its own ticket filter.** `/tickets/stats/` filters by `owner` and `customer` only (the Overview's Support card sends only those two), starting from `visible_tickets(user, Ticket.objects.filter(visible_children_q(user)))`. **Resolution:** Task 1 extracts that filter as `customers.ticket_filters.filtered_tickets(user, params)`, the Support figures call it with `owner` and `customer` only so they equal the screen, and the digest says the lifecycle filter does not apply. The "10 accounts with the most open High/Critical tickets" list is further restricted to `forecast.filtered_customers(user, {owner, customer})`, so the first-filter rule holds for every company named.
3. **The "priority × status split" is not on the endpoint.** `/tickets/stats/` serves `priority` and `status` as two separate lists. **Resolution:** the digest carries the cross-tab, and the equality test checks that its row and column totals equal the endpoint's two lists.
4. **The Health endpoint is unfiltered.** `/customers/health/` returns the whole live book and the frontend filters it client-side (`matchesFilters` on owner, lifecycle and customer id). **Resolution:** the Health figures use `attention.rules.filtered_customers(user, filters)`, which is `forecast.filtered_customers` plus the Health view's own snapshot window, and the same `triage.triage(...)` call the Health serializer makes. The equality test filters the endpoint's rows by the same ids, which is what the screen does. The summary mirrors the frontend's `summarise()` (`total`, `atGood`, `needsAction`, `needsActionRenewingSoon`, `declining`, `blindSpots`).
5. **Retrieval has no renewal/health facts or contacts.** `retrieve_with_sources` covers emails, notes, open tickets, activities and contributions. **Resolution:** for each target company the digest adds one facts line built from the (already filtered) `Customer` row — health, renewal, ARR in the org currency, owner — and up to five contacts as name, role and sentiment (never email or phone). Facts are figures, not records, so they are not added to `sources`; citations stay retrieval snapshots only.
6. **"Any accounts the question names".** The existing matcher, `retrieval.find_mentioned_company`, returns the first match only, and the filtered set is Customers. **Resolution:** call it with the filtered book's customers (sorted by name) and no accounts; one named company at most. The semantic fallback is not used on the dashboard: "names" means named.
7. **`areas.ts` has no Overview entry.** Its `AreaKey` is `revenue | health | support`; the Overview is the dashboard root. **Resolution:** `DASHBOARD_VIEWS["overview"] = {}` and the Overview requires `view: null`; every other area requires one of its paths.
8. **"The same check snoozing uses" lives on a view.** It is `AttentionSnoozeView._current_item`, a staticmethod. **Resolution:** Task 1 moves it to `attention.rules.current_item(user, key, *, today=None)` (with `KEY_PATTERN`), and the snooze view calls the moved function. Behaviour is unchanged.
9. **N in the anomaly title.** The attention list's N counts the viewer's whole book. On the dashboard, the digest reads only the filtered book, so N counts the anomaly's companies inside the filtered book. The stored title is still shown only to `sees_everything(user)`, and the anomaly's model-written `summary` follows the same rule.
10. **The forecast horizon is not in the context.** The Revenue digest uses the endpoint's default, `forecast.DEFAULT_HORIZON_DAYS` (365). This is also what the Overview's Revenue card asks for.

## File Structure

| File | Responsibility |
|---|---|
| `services/attention/rules.py` | gains `KEY_PATTERN` and `current_item(user, key, *, today=None)` (moved from the view) |
| `services/attention/views.py` | `AttentionSnoozeView` calls `rules.current_item` |
| `services/customers/ticket_filters.py` (new) | `filtered_tickets(user, params)`: `/tickets/stats/`'s visibility-first filter |
| `services/customers/views.py` | `TicketStatsView` calls `filtered_tickets` |
| `services/copilot/models.py` | `Message.context`, `Conversation.origin` |
| `services/copilot/migrations/0010_dashboard_context.py` (new) | the two fields |
| `services/copilot/serializers.py` | expose `context` on messages and `origin` on conversations |
| `services/copilot/dashboard_context.py` (new) | `AREA_LABELS`, `DASHBOARD_VIEWS`, `clean_filters`, `origin_of`, `DashboardContextSerializer` |
| `services/copilot/dashboard_figures.py` (new) | `revenue_figures`, `health_figures`, `support_figures`, `attention_top`, `overview_figures` |
| `services/copilot/dashboard_grounding.py` (new) | `build_dashboard_grounding`, `dashboard_system_prompt` and the digest/record helpers |
| `services/copilot/usage.py`, `services/copilot/skills.py` | the `dashboard` purpose and its skill |
| `services/copilot/views.py` | `SendMessageView` validates `context`, grounds, meters, stores |
| `services/copilot/tests/dashboard_fixture.py` (new) | shared test set-up for the dashboard tests |
| `services/copilot/tests/test_dashboard_*.py` (new) | context, figures, grounding and send tests |
| `e2e/test_dashboard_ask_flow.py` (new) | end-to-end flow |
| `docs/API_CONTRACTS.md`, `docs/data-classification.md`, `docs/product/01-prd.md`, `docs/product/05-backend-schema.md` | docs |

---

### Task 1: Shared lookups — the snooze key check and the ticket filter

A pure extraction: two pieces of logic that live inside views move to module functions, so the dashboard code can call exactly what the endpoints call. No behaviour changes.

**Files:**
- Modify: `services/attention/rules.py` (append after `build_items`), `services/attention/views.py:17-36,90-127`
- Create: `services/customers/ticket_filters.py`
- Modify: `services/customers/views.py` (`TicketStatsView._filtered_tickets`, lines ~2538-2583)
- Test: `services/attention/tests/test_rules.py`, `services/customers/tests/test_ticket_filters.py` (new)

**Interfaces:**
- Produces: `services.attention.rules.KEY_PATTERN` (compiled regex `^(renewal|risk|going_quiet|support|anomaly):([1-9][0-9]*)$`); `services.attention.rules.current_item(user, key, *, today=None) -> dict | None` (the viewer's current item under `key`, or `None` for anything else, including a non-string key).
- Produces: `services.customers.ticket_filters.filtered_tickets(user, params) -> QuerySet[Ticket]` (`params` is any mapping with `.get`; keys `from`, `to`, `priority`, `owner`, `customer`, `account`, `connector`; bad values ignored).

- [ ] **Step 1: Write the failing tests**

Append to `services/attention/tests/test_rules.py`:

```python
class CurrentItemTests(Fixture):
    """The snooze endpoint's key check, now a function the Copilot shares."""

    def test_an_item_on_the_viewers_list(self):
        soon = self.customer(
            "Soon", health_score=POOR, renewal_date=self.today + timedelta(days=10)
        )

        item = rules.current_item(self.csm, f"renewal:{soon.pk}", today=self.today)

        self.assertEqual(item["key"], f"renewal:{soon.pk}")
        self.assertEqual(item["kind"], "renewal")

    def test_anything_else_is_none(self):
        theirs = self.customer(
            "Theirs",
            owner=self.other,
            health_score=POOR,
            renewal_date=self.today + timedelta(days=10),
        )
        for key in (f"renewal:{theirs.pk}", "renewal:abc", "bogus:1", "renewal:0", "", None, 12):
            with self.subTest(key=key):
                self.assertIsNone(rules.current_item(self.csm, key, today=self.today))
```

Create `services/customers/tests/test_ticket_filters.py`:

```python
"""The Ticket Overview's filter, shared by /tickets/stats/ and the dashboard's
Ask Revenact digest."""

from django.test import TestCase
from django.utils import timezone

from services.accounts.models import Organisation, User
from services.customers.models import Customer, Ticket
from services.customers.ticket_filters import filtered_tickets


class FilteredTicketsTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")
        self.carl = User.objects.create_user(
            email="carl@acme.io",
            password="supersecret1",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
            function=User.Function.CS,
        )
        self.dana = User.objects.create_user(
            email="dana@acme.io",
            password="supersecret1",
            name="Dana",
            organisation=self.org,
            role=User.Role.CSM,
            function=User.Function.CS,
        )
        self.mine = Customer.objects.create(organisation=self.org, name="Mine", owner=self.carl)
        self.also = Customer.objects.create(organisation=self.org, name="Also", owner=self.carl)
        self.theirs = Customer.objects.create(organisation=self.org, name="Theirs", owner=self.dana)
        self.ticket("TKT-1", self.mine)
        self.ticket("TKT-2", self.also)
        self.ticket("TKT-3", self.mine, department=User.Function.ENGINEERING)
        self.ticket("TKT-4", self.theirs)

    def ticket(self, number, customer, **fields):
        return Ticket.objects.create(
            customer=customer,
            ticket_number=number,
            title="t",
            priority=Ticket.Priority.HIGH,
            opened_at=timezone.localdate(),
            **fields,
        )

    def numbers(self, params):
        return set(filtered_tickets(self.carl, params).values_list("ticket_number", flat=True))

    def test_visibility_first(self):
        # Dana's customer and another department's ticket are never in the set.
        self.assertEqual(self.numbers({}), {"TKT-1", "TKT-2"})

    def test_customer_narrows(self):
        self.assertEqual(self.numbers({"customer": str(self.mine.pk)}), {"TKT-1"})

    def test_owner_narrows_and_cannot_widen(self):
        self.assertEqual(self.numbers({"owner": str(self.carl.pk)}), {"TKT-1", "TKT-2"})
        self.assertEqual(self.numbers({"owner": str(self.dana.pk)}), set())

    def test_a_bad_value_is_ignored(self):
        self.assertEqual(self.numbers({"owner": "abc", "priority": "urgent"}), {"TKT-1", "TKT-2"})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.attention.tests.test_rules.CurrentItemTests services.customers.tests.test_ticket_filters --noinput`
Expected: FAIL — `AttributeError: module 'services.attention.rules' has no attribute 'current_item'` and `ModuleNotFoundError: No module named 'services.customers.ticket_filters'`.

- [ ] **Step 3: Move the key check into `rules.py`**

In `services/attention/rules.py`, add `import re` to the imports (before `from collections import defaultdict`), then add directly after `build_items`:

```python
#: `<kind>:<id>` — the only shape a key ever has.
KEY_PATTERN = re.compile(rf"^({'|'.join(KINDS)}):([1-9][0-9]*)$")


def current_item(user, key, *, today=None):
    """The viewer's item under `key` right now, or None — the one check both
    snoozing and the dashboard's Ask Revenact apply to a key a client sends.

    Only the key's own kind is built, and for a company's kind only that
    company — through the filters' `customer` param, so it still has to be in
    the viewer's visible book. An anomaly key builds the anomaly kind over the
    whole book, since its fingerprint counts companies. Anything that is not
    a well-formed key, including a non-string, is None."""
    if not isinstance(key, str):
        return None
    match = KEY_PATTERN.match(key)
    if match is None:
        return None
    kind, ident = match.groups()
    params = {} if kind == "anomaly" else {"customer": ident}
    items = build_items(user, params, today=today or timezone.localdate(), kinds=(kind,))
    return next((item for item in items if item["key"] == key), None)
```

In `services/attention/views.py`:
- remove `import re`, the `KEY_PATTERN = ...` line and its comment;
- change `from .rules import KINDS, build_items` to `from .rules import build_items, current_item`;
- in `AttentionSnoozeView.post`, change `item = self._current_item(request.user, key)` to `item = current_item(request.user, key)`;
- delete the `_current_item` staticmethod.

- [ ] **Step 4: Extract the ticket filter**

Create `services/customers/ticket_filters.py`:

```python
"""The Ticket Overview's filtered set.

Shared by `/tickets/stats/` (`TicketStatsView`) and the dashboard's Ask
Revenact digest (`services.copilot.dashboard_figures`), so the assistant and
the screen can never count different tickets.
"""

from django.db.models import Q

from .interactions import _parse_date, _parse_int
from .models import Ticket
from .personal import visible_tickets
from .scoping import visible_children_q


def filtered_tickets(user, params):
    """Visibility first, then the caller's filters narrow from there.
    Applying a raw `?account=` id before the visibility gate is what
    previously let members read other owners' custom-object records — see
    CustomObjectRecordListCreateView's own note. Every filter ignores a bad
    value rather than failing."""

    tickets = visible_tickets(user, Ticket.objects.filter(visible_children_q(user)).distinct())

    opened_from = _parse_date(params.get("from"))
    if opened_from:
        tickets = tickets.filter(opened_at__gte=opened_from)
    opened_to = _parse_date(params.get("to"))
    if opened_to:
        tickets = tickets.filter(opened_at__lte=opened_to)

    priority = params.get("priority")
    if priority in Ticket.Priority.values:
        tickets = tickets.filter(priority=priority)

    owner_id = _parse_int(params.get("owner"))
    if owner_id is not None:
        tickets = tickets.filter(Q(customer__owner_id=owner_id) | Q(account__owner_id=owner_id))

    customer_id = _parse_int(params.get("customer"))
    if customer_id is not None:
        tickets = tickets.filter(Q(customer_id=customer_id) | Q(account__customers__id=customer_id))

    account_id = _parse_int(params.get("account"))
    if account_id is not None:
        tickets = tickets.filter(account_id=account_id)

    connector_id = _parse_int(params.get("connector"))
    if connector_id is not None:
        tickets = tickets.filter(connector_id=connector_id)

    return tickets.distinct()
```

In `services/customers/views.py`, add `from .ticket_filters import filtered_tickets` beside the other relative imports, delete the `_filtered_tickets` method from `TicketStatsView`, and change the first line of `TicketStatsView.get` to:

```python
tickets = filtered_tickets(request.user, request.query_params)
```

The moved method was the only user of `_parse_date` in `views.py`, so change `from .interactions import _parse_date, _parse_int` to `from .interactions import _parse_int` (`_parse_int` is still used by `_drill_filter` and others), or `ruff check` fails on an unused import.

- [ ] **Step 5: Run the new and the existing tests**

Run: `venv/bin/python manage.py test services.attention services.customers.tests.test_ticket_filters services.customers.tests.test_views.TicketStatsTests --noinput`
Expected: PASS (every existing snooze and ticket-stats test unchanged).

Run: `venv/bin/ruff format . && venv/bin/ruff check .` — clean.

- [ ] **Step 6: Commit**

```bash
git add services/attention/rules.py services/attention/views.py services/attention/tests/test_rules.py \
  services/customers/ticket_filters.py services/customers/views.py services/customers/tests/test_ticket_filters.py
git commit -m "refactor(dashboard): share the snooze key check and the ticket filter

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `Message.context` and `Conversation.origin`

**Files:**
- Modify: `services/copilot/models.py` (`Conversation`, `Message`)
- Create: `services/copilot/migrations/0010_dashboard_context.py` (generated)
- Modify: `services/copilot/serializers.py` (`MessageSerializer`, `ConversationListSerializer`, `ConversationDetailSerializer`)
- Test: `services/copilot/tests/test_models.py`, `services/copilot/tests/test_views.py`

**Interfaces:**
- Produces: `Conversation.origin: dict | None`, `Message.context: dict | None`; `MessageSerializer` fields gain `"context"`; both conversation serializers gain `"origin"`.

- [ ] **Step 1: Write the failing tests**

Append to `services/copilot/tests/test_models.py` (add any missing imports: `TestCase` from `django.test`, `Organisation`, `User` from `services.accounts.models`, `Conversation`, `Message` from `services.copilot.models`):

```python
class DashboardFieldsTests(TestCase):
    def test_both_are_null_by_default(self):
        org = Organisation.objects.create(name="Acme Inc")
        user = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=org
        )
        conversation = Conversation.objects.create(organisation=org, user=user)
        message = Message.objects.create(
            conversation=conversation, role=Message.Role.USER, content="Hi"
        )

        self.assertIsNone(conversation.origin)
        self.assertIsNone(message.context)
```

Append to `services/copilot/tests/test_views.py`:

```python
class DashboardFieldsSerializationTests(APITestCase):
    origin = {
        "surface": "dashboard",
        "area": "revenue",
        "view": "forecast",
        "filters": {"owner": "", "lifecycle": "", "customer": ""},
    }

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )
        self.conversation = Conversation.objects.create(
            organisation=self.org, user=self.user, title="Why?", origin=self.origin
        )
        Message.objects.create(
            conversation=self.conversation,
            role=Message.Role.USER,
            content="Why?",
            context={**self.origin, "focus": None},
        )
        self.client.force_authenticate(self.user)

    def test_the_list_carries_origin(self):
        response = self.client.get("/api/v1/copilot/conversations/")
        self.assertEqual(response.data[0]["origin"], self.origin)

    def test_the_detail_carries_origin_and_each_turns_context(self):
        response = self.client.get(f"/api/v1/copilot/conversations/{self.conversation.id}/")
        self.assertEqual(response.data["origin"], self.origin)
        self.assertEqual(response.data["messages"][0]["context"], {**self.origin, "focus": None})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.copilot.tests.test_models.DashboardFieldsTests services.copilot.tests.test_views.DashboardFieldsSerializationTests --noinput`
Expected: FAIL — `TypeError: Conversation() got unexpected keyword arguments: 'origin'` (and the same for `context`).

- [ ] **Step 3: Add the fields**

In `services/copilot/models.py`, add to `Conversation` after `title`:

```python
origin = models.JSONField(
    null=True,
    blank=True,
    help_text="Where the conversation started on the Dashboard: the first dashboard "
    "message's context without its focus ({surface, area, view, filters}). Set once, "
    "never overwritten. Null for a conversation that never had a dashboard message. "
    "Ids and filter values only.",
)
```

Add to `Message` after `ask_suggestions`:

```python
context = models.JSONField(
    null=True,
    blank=True,
    help_text="User turns asked on the Dashboard: the validated screen context "
    "({surface, area, view, filters, focus}) after the focus was intersected with the "
    "asker's filtered book (services/copilot/dashboard_context.py). Ids and filter "
    "values only, never record text. Null on every other turn.",
)
```

Run: `venv/bin/python manage.py makemigrations copilot -n dashboard_context`
Expected: creates `services/copilot/migrations/0010_dashboard_context.py` with two `AddField` operations (`conversation.origin`, `message.context`) depending on `0009_backfill_message_author`.

- [ ] **Step 4: Expose them**

In `services/copilot/serializers.py`:
- `MessageSerializer.Meta.fields`: add `"context"` after `"ask_suggestions"`;
- `ConversationListSerializer.Meta.fields`: `["id", "title", "origin", "created_at", "updated_at"]`;
- `ConversationDetailSerializer.Meta.fields`: `["id", "title", "origin", "messages", "visibility", "created_at", "updated_at"]`.

- [ ] **Step 5: Run the tests**

Run: `venv/bin/python manage.py test services.copilot --noinput`
Expected: PASS.

Run: `venv/bin/python manage.py makemigrations --check --dry-run` → "No changes detected". `venv/bin/ruff format . && venv/bin/ruff check .` — clean.

- [ ] **Step 6: Commit**

```bash
git add services/copilot/models.py services/copilot/migrations/0010_dashboard_context.py \
  services/copilot/serializers.py services/copilot/tests/test_models.py services/copilot/tests/test_views.py
git commit -m "feat(copilot): store the dashboard context on a turn and the origin on a conversation

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Validating `context`

**Files:**
- Create: `services/copilot/dashboard_context.py`
- Create: `services/copilot/tests/dashboard_fixture.py`, `services/copilot/tests/test_dashboard_context.py`

**Interfaces:**
- Consumes: `services.attention.rules.current_item` (Task 1); `services.customers.forecast.filtered_customers(user, params)`.
- Produces:
  - `AREA_LABELS: dict[str, str]`, `DASHBOARD_VIEWS: dict[str, dict[str, str]]` (area → {view path → label}), `MAX_FOCUS_IDS = 200`, `NOT_ON_LIST = "Not an item on your list."`, `FILTER_KEYS = ("owner", "lifecycle", "customer")`.
  - `clean_filters(raw: dict) -> dict[str, str]` — always the three keys, values as strings, `""` when absent or unusable.
  - `origin_of(context: dict) -> dict` — the context without `focus`.
  - `DashboardContextSerializer(data=..., context={"user": user})`; `validated_data` is exactly `{"surface": "dashboard", "area": str, "view": str | None, "filters": {"owner": str, "lifecycle": str, "customer": str}, "focus": None | {"kind": "companies", "ids": list[int]} | {"kind": "attention", "key": str}}`. Errors: `{"surface": [...]}`, `{"area": [...]}`, `{"view": [...]}`, `{"focus": {"kind": [...]}}`, `{"focus": {"ids": [...]}}`, `{"focus": {"key": [NOT_ON_LIST]}}`.
  - Test helper `DashboardFixture` (below), used by Tasks 4–8.

- [ ] **Step 1: Write the shared fixture**

Create `services/copilot/tests/dashboard_fixture.py`:

```python
"""Shared set-up for the dashboard Ask Revenact tests: one organisation, a CSM
(Carl, Customer Success), a second CSM (Dana) whose book Carl must never see,
and a leadership admin (Boss) who sees everything."""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from services.accounts.models import Organisation, User
from services.anomalies.models import Anomaly, AnomalyEvidence
from services.customers.models import Customer, Ticket

GOOD, AVERAGE, POOR = Decimal("8.0"), Decimal("5.0"), Decimal("2.0")


class DashboardFixture(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")
        self.csm = self.person("carl@acme.io", "Carl")
        self.other = self.person("dana@acme.io", "Dana")
        self.admin = self.person(
            "boss@acme.io", "Boss", role=User.Role.ADMIN, function=User.Function.LEADERSHIP
        )
        self.api = APIClient()
        self.api.force_authenticate(self.csm)

    def person(self, email, name, *, role=User.Role.CSM, function=User.Function.CS):
        return User.objects.create_user(
            email=email,
            password="supersecret1",
            name=name,
            organisation=self.org,
            role=role,
            function=function,
        )

    def customer(self, name, *, owner=None, **fields):
        return Customer.objects.create(
            organisation=self.org,
            name=name,
            owner=owner or self.csm,
            health_score=fields.pop("health_score", GOOD),
            arr_billed_at_account=fields.pop("arr", 100_000),
            currency=fields.pop("currency", "USD"),
            **fields,
        )

    def ticket(self, n, customer, **overrides):
        return Ticket.objects.create(
            **{
                "customer": customer,
                "ticket_number": f"TKT-{n}",
                "title": f"Ticket {n}",
                "status": Ticket.Status.OPEN,
                "priority": Ticket.Priority.HIGH,
                "opened_at": self.today,
                **overrides,
            }
        )

    def anomaly(self, title, summary=""):
        return Anomaly.objects.create(
            organisation=self.org,
            title=title,
            summary=summary,
            status=Anomaly.Status.LIVE,
            first_seen_at=timezone.now(),
            last_seen_at=timezone.now(),
        )

    def evidence(self, anomaly, n, *, customer, snippet="Login fails after SSO redirect"):
        return AnomalyEvidence.objects.create(
            anomaly=anomaly,
            organisation=self.org,
            kind=AnomalyEvidence.Kind.CALL,
            record_id=n,
            customer=customer,
            snippet=snippet,
            occurred_at=timezone.now(),
        )

    def context(self, area="overview", view=None, focus=None, **filters):
        return {
            "surface": "dashboard",
            "area": area,
            "view": view,
            "filters": {"owner": "", "lifecycle": "", "customer": "", **filters},
            "focus": focus,
        }
```

- [ ] **Step 2: Write the failing tests**

Create `services/copilot/tests/test_dashboard_context.py`:

```python
"""Validating the `context` a dashboard send carries."""

from datetime import timedelta

from django.test import SimpleTestCase

from services.copilot.dashboard_context import (
    DASHBOARD_VIEWS,
    MAX_FOCUS_IDS,
    NOT_ON_LIST,
    DashboardContextSerializer,
    clean_filters,
    origin_of,
)

from .dashboard_fixture import POOR, DashboardFixture


class CatalogueTests(SimpleTestCase):
    def test_mirrors_the_frontend_areas(self):
        # react-ts-app/src/pages/dashboard/areas.ts, plus the Overview root.
        self.assertEqual(
            {area: list(views) for area, views in DASHBOARD_VIEWS.items()},
            {
                "overview": [],
                "revenue": ["forecast", "customers", "products"],
                "health": [
                    "triage",
                    "divergence",
                    "movement",
                    "renewals",
                    "usage",
                    "activity",
                    "distribution",
                ],
                "support": ["tickets", "topics"],
            },
        )


class CleanFiltersTests(SimpleTestCase):
    def test_keeps_the_three_shared_keys_and_drops_the_rest(self):
        self.assertEqual(
            clean_filters(
                {"owner": 2, "lifecycle": "live", "horizon_days": "90", "customer": None}
            ),
            {"owner": "2", "lifecycle": "live", "customer": ""},
        )

    def test_a_value_that_is_not_text_or_a_number_is_ignored(self):
        self.assertEqual(
            clean_filters({"owner": ["2"], "lifecycle": True, "customer": "x" * 65}),
            {"owner": "", "lifecycle": "", "customer": ""},
        )

    def test_origin_is_the_context_without_its_focus(self):
        context = {"surface": "dashboard", "area": "health", "view": "triage", "filters": {}}
        self.assertEqual(
            origin_of({**context, "focus": {"kind": "attention", "key": "risk:1"}}), context
        )


class DashboardContextSerializerTests(DashboardFixture):
    def validate(self, data, user=None):
        serializer = DashboardContextSerializer(data=data, context={"user": user or self.csm})
        valid = serializer.is_valid()
        return valid, (serializer.validated_data if valid else serializer.errors)

    def test_a_valid_context_is_normalised(self):
        valid, data = self.validate(
            {"surface": "dashboard", "area": "revenue", "view": "forecast", "filters": {"owner": 7}}
        )
        self.assertTrue(valid)
        self.assertEqual(
            data,
            {
                "surface": "dashboard",
                "area": "revenue",
                "view": "forecast",
                "filters": {"owner": "7", "lifecycle": "", "customer": ""},
                "focus": None,
            },
        )

    def test_field_errors(self):
        cases = {
            "surface": {**self.context(), "surface": "communications"},
            "area": {**self.context(), "area": "brain"},
            "view": self.context("overview", "forecast"),
        }
        for field, data in cases.items():
            with self.subTest(field=field):
                valid, errors = self.validate(data)
                self.assertFalse(valid)
                self.assertIn(field, errors)

    def test_a_view_must_belong_to_its_area(self):
        for area, view in (("revenue", None), ("revenue", "triage"), ("support", "")):
            with self.subTest(area=area, view=view):
                valid, errors = self.validate(self.context(area, view))
                self.assertFalse(valid)
                self.assertIn("view", errors)

    def test_focus_errors(self):
        cases = {
            "kind": {"kind": "segment"},
            "ids": {"kind": "companies", "ids": list(range(1, MAX_FOCUS_IDS + 2))},
        }
        for field, focus in cases.items():
            with self.subTest(field=field):
                valid, errors = self.validate(self.context(focus=focus))
                self.assertFalse(valid)
                self.assertIn(field, errors["focus"])

    def test_ids_must_be_whole_numbers(self):
        valid, errors = self.validate(self.context(focus={"kind": "companies", "ids": ["1"]}))
        self.assertFalse(valid)
        self.assertIn("ids", errors["focus"])

    def test_focus_ids_outside_the_filtered_book_are_dropped_silently(self):
        mine = self.customer("Mine", lifecycle_stage="live")
        onboarding = self.customer("Onboarding", lifecycle_stage="onboarding")
        theirs = self.customer("Theirs", owner=self.other)
        focus = {"kind": "companies", "ids": [theirs.pk, mine.pk, onboarding.pk, 999_999]}

        valid, data = self.validate(self.context(focus=focus, lifecycle="live"))

        self.assertTrue(valid)
        self.assertEqual(data["focus"], {"kind": "companies", "ids": [mine.pk]})

    def test_an_attention_key_on_the_viewers_list(self):
        soon = self.customer(
            "Soon", health_score=POOR, renewal_date=self.today + timedelta(days=10)
        )
        focus = {"kind": "attention", "key": f"renewal:{soon.pk}"}

        valid, data = self.validate(self.context(focus=focus))

        self.assertTrue(valid)
        self.assertEqual(data["focus"], focus)

    def test_a_key_not_on_the_list_reads_exactly_like_a_malformed_one(self):
        theirs = self.customer(
            "Theirs",
            owner=self.other,
            health_score=POOR,
            renewal_date=self.today + timedelta(days=10),
        )
        for key in (f"renewal:{theirs.pk}", "renewal:abc", 12, None):
            with self.subTest(key=key):
                valid, errors = self.validate(self.context(focus={"kind": "attention", "key": key}))
                self.assertFalse(valid)
                self.assertEqual(errors, {"focus": {"key": [NOT_ON_LIST]}})
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.copilot.tests.test_dashboard_context --noinput`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.copilot.dashboard_context'`.

- [ ] **Step 4: Implement `dashboard_context.py`**

```python
"""Where on the Dashboard a question was asked, as the client sends it.

The client sends *where* it is — area, view, the shared filters and an
optional focus — never figures; the server recomputes what is there
(`dashboard_grounding`). This module is the gate: it checks the place against
the catalogue, keeps only the shared filters, and narrows a focus to what the
viewer may see, silently, so a refused id says nothing about whose it was.
"""

from rest_framework import serializers

from services.attention.rules import current_item
from services.customers import forecast

AREA_LABELS = {
    "overview": "Overview",
    "revenue": "Revenue",
    "health": "Health",
    "support": "Support",
}

#: Mirrors react-ts-app/src/pages/dashboard/areas.ts (view path -> label). The
#: Overview is the dashboard root and has no sub-view.
DASHBOARD_VIEWS = {
    "overview": {},
    "revenue": {"forecast": "Forecast", "customers": "Customers", "products": "Products"},
    "health": {
        "triage": "Triage",
        "divergence": "Divergence",
        "movement": "Movement",
        "renewals": "Renewals",
        "usage": "Usage",
        "activity": "Activity",
        "distribution": "Distribution",
    },
    "support": {"tickets": "Tickets", "topics": "Topics"},
}

#: The filters every book-level view shares; anything else is dropped.
FILTER_KEYS = ("owner", "lifecycle", "customer")

#: A filter value longer than this is not an id or a stage; it is ignored.
MAX_FILTER_LENGTH = 64

#: How many companies a drill's "Ask about these" may carry.
MAX_FOCUS_IDS = 200

#: One message for a key that is malformed, someone else's or gone, so the
#: answer never tells a caller whose item a key was.
NOT_ON_LIST = "Not an item on your list."


def _filter_value(value):
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and len(value) <= MAX_FILTER_LENGTH:
        return value
    return ""


def clean_filters(raw):
    """The three shared keys, always present, as strings. A value that is not
    text or a whole number is ignored ("" means "all"), like the endpoints do."""
    return {key: _filter_value(raw.get(key)) for key in FILTER_KEYS}


def origin_of(context):
    """What a conversation remembers about where it started: the place, not
    the focus."""
    return {key: value for key, value in context.items() if key != "focus"}


class DashboardContextSerializer(serializers.Serializer):
    """Validates a send's `context`. Needs `context={"user": user}`."""

    surface = serializers.ChoiceField(choices=["dashboard"])
    area = serializers.ChoiceField(choices=list(DASHBOARD_VIEWS))
    view = serializers.CharField(allow_null=True, required=False, default=None)
    filters = serializers.DictField(required=False, default=dict)
    focus = serializers.DictField(allow_null=True, required=False, default=None)

    def validate(self, data):
        area, view = data["area"], data.get("view")
        views = DASHBOARD_VIEWS[area]
        if (view is None and views) or (view is not None and view not in views):
            raise serializers.ValidationError({"view": [f"Not a view of {AREA_LABELS[area]}."]})
        filters = clean_filters(data.get("filters") or {})
        return {
            "surface": data["surface"],
            "area": area,
            "view": view,
            "filters": filters,
            "focus": self._focus(data.get("focus"), filters),
        }

    def _focus(self, focus, filters):
        if focus is None:
            return None
        user = self.context["user"]
        kind = focus.get("kind")
        if kind == "companies":
            ids = focus.get("ids")
            if not isinstance(ids, list) or not all(
                isinstance(pk, int) and not isinstance(pk, bool) for pk in ids
            ):
                raise serializers.ValidationError({"focus": {"ids": ["A list of company ids."]}})
            if len(ids) > MAX_FOCUS_IDS:
                raise serializers.ValidationError(
                    {"focus": {"ids": [f"At most {MAX_FOCUS_IDS} companies."]}}
                )
            # SOC2:AUTH-02 a focus is narrowed to the viewer's filtered, visible book;
            # ids outside it are dropped without saying so
            kept = (
                forecast.filtered_customers(user, filters)
                .filter(pk__in=ids)
                .values_list("pk", flat=True)
            )
            return {"kind": "companies", "ids": sorted(kept)}
        if kind == "attention":
            key = focus.get("key")
            if current_item(user, key) is None:
                raise serializers.ValidationError({"focus": {"key": [NOT_ON_LIST]}})
            return {"kind": "attention", "key": key}
        raise serializers.ValidationError({"focus": {"kind": ["Must be companies or attention."]}})
```

- [ ] **Step 5: Run the tests**

Run: `venv/bin/python manage.py test services.copilot.tests.test_dashboard_context --noinput`
Expected: PASS. `venv/bin/ruff format . && venv/bin/ruff check .` — clean.

- [ ] **Step 6: Commit**

```bash
git add services/copilot/dashboard_context.py services/copilot/tests/dashboard_fixture.py \
  services/copilot/tests/test_dashboard_context.py
git commit -m "feat(copilot): validate where on the dashboard a question was asked

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Revenue and Health figures

**Files:**
- Create: `services/copilot/dashboard_figures.py`
- Create: `services/copilot/tests/test_dashboard_figures.py`

**Interfaces:**
- Consumes: `forecast.filtered_customers`, `forecast.build_rows`, `forecast.build_bridge`, `forecast.swing_list`, `forecast.DEFAULT_HORIZON_DAYS`; `attention.rules.filtered_customers(user, params, *, history=True)`; `triage.triage(...)`, `triage.ACTION_THRESHOLD`, `triage.RENEWAL_URGENT_DAYS`; `dashboard_context.clean_filters` (tests).
- Produces:
  - `LIST_LIMIT = 10`, `BLIND_SPOT_GAP = 2`, `DIRECTIONS = ("declining", "improving", "flat", "unknown")`.
  - `revenue_figures(user, filters) -> {"currency": str, "bridge": dict (exactly build_bridge's), "at_risk": float, "movers": [{"id", "name", "net", "downside", "expansion"}] (≤ 10), "unpriced_count": int}`.
  - `health_figures(user, filters, *, today) -> {"total", "at_good", "needs_action", "needs_action_renewing_soon", "blind_spots": int, "by_direction": {direction: int}, "accounts_needing_action": [{"id", "name", "score": int, "factors": [str]}] (≤ 10, score desc then name)}`.

- [ ] **Step 1: Write the failing tests**

Create `services/copilot/tests/test_dashboard_figures.py`:

```python
"""Each area's figures equal what that area's endpoint returns for the same
viewer and filters — the screen and the assistant can never disagree."""

from datetime import timedelta
from decimal import Decimal

from services.copilot import dashboard_figures
from services.copilot.dashboard_context import clean_filters
from services.customers import forecast
from services.customers.models import HealthSnapshot, Opportunity

from .dashboard_fixture import AVERAGE, GOOD, POOR, DashboardFixture


class RevenueFiguresTests(DashboardFixture):
    def setUp(self):
        super().setUp()
        self.shaky = self.customer(
            "Shaky",
            health_score=POOR,
            renewal_date=self.today + timedelta(days=30),
            arr=50_000,
            lifecycle_stage="renewal",
        )
        self.growing = self.customer("Growing", arr=80_000, lifecycle_stage="live")
        Opportunity.objects.create(
            customer=self.growing,
            title="Seats",
            mrr=Decimal("1000"),
            stage=Opportunity.Stage.NEGOTIATION,
        )
        self.theirs = self.customer(
            "Theirs",
            owner=self.other,
            health_score=POOR,
            renewal_date=self.today + timedelta(days=30),
            arr=900_000,
        )

    def test_equals_the_forecast_endpoint(self):
        for params in (
            {},
            {"owner": str(self.csm.pk)},
            {"lifecycle": "renewal"},
            {"customer": str(self.growing.pk)},
        ):
            with self.subTest(params=params):
                figures = dashboard_figures.revenue_figures(self.csm, clean_filters(params))
                screen = self.api.get("/api/v1/customers/forecast/", params).data

                self.assertEqual(figures["bridge"], screen["bridge"])
                self.assertEqual(figures["currency"], screen["currency"])
                self.assertEqual(
                    figures["at_risk"],
                    round(screen["bridge"]["churn"] + screen["bridge"]["contraction"], 2),
                )
                self.assertEqual(
                    [(m["id"], m["net"]) for m in figures["movers"]],
                    [(row["id"], row["net"]) for row in screen["swing"][:10]],
                )

    def test_another_csms_customer_is_never_counted(self):
        figures = dashboard_figures.revenue_figures(self.csm, clean_filters({}))

        self.assertEqual(figures["bridge"]["opening_arr"], 130000.0)
        self.assertNotIn(self.theirs.pk, [m["id"] for m in figures["movers"]])


class HealthFiguresTests(DashboardFixture):
    def setUp(self):
        super().setUp()
        self.poor = self.customer(
            "Poor soon", health_score=POOR, renewal_date=self.today + timedelta(days=20)
        )
        self.average = self.customer("Average", health_score=AVERAGE, lifecycle_stage="live")
        self.good = self.customer("Good", health_score=GOOD, lifecycle_stage="live")
        for months_ago, score in ((2, GOOD), (1, AVERAGE)):
            HealthSnapshot.objects.create(
                customer=self.average,
                captured_on=self.today - timedelta(days=31 * months_ago),
                health_score=score,
            )
        self.customer("Theirs", owner=self.other, health_score=POOR)

    def test_equals_the_health_endpoint(self):
        rows = self.api.get("/api/v1/customers/health/").data["results"]
        for params in ({}, {"lifecycle": "live"}, {"customer": str(self.poor.pk)}):
            with self.subTest(params=params):
                filters = clean_filters(params)
                ids = set(
                    forecast.filtered_customers(self.csm, filters).values_list("pk", flat=True)
                )
                # What the screen does: the whole book, filtered client-side.
                shown = [row for row in rows if row["id"] in ids]
                acting = sorted(
                    (row for row in shown if row["triage_score"] >= 40),
                    key=lambda row: (-row["triage_score"], row["name"]),
                )

                figures = dashboard_figures.health_figures(self.csm, filters, today=self.today)

                self.assertEqual(figures["total"], len(shown))
                self.assertEqual(
                    figures["at_good"], sum(1 for row in shown if row["health_category"] == "good")
                )
                self.assertEqual(figures["needs_action"], len(acting))
                for direction in dashboard_figures.DIRECTIONS:
                    self.assertEqual(
                        figures["by_direction"][direction],
                        sum(1 for row in shown if row["triage_direction"] == direction),
                    )
                self.assertEqual(
                    [(a["id"], a["score"]) for a in figures["accounts_needing_action"]],
                    [(row["id"], row["triage_score"]) for row in acting[:10]],
                )
                self.assertEqual(
                    [a["factors"] for a in figures["accounts_needing_action"]],
                    [[f["label"] for f in row["triage_factors"]] for row in acting[:10]],
                )

    def test_summary_counts(self):
        figures = dashboard_figures.health_figures(self.csm, clean_filters({}), today=self.today)

        self.assertEqual(figures["total"], 3)
        self.assertEqual(figures["at_good"], 1)
        self.assertEqual(figures["needs_action"], 1)
        self.assertEqual(figures["needs_action_renewing_soon"], 1)
        self.assertEqual(figures["by_direction"]["declining"], 1)
        self.assertEqual(figures["accounts_needing_action"][0]["name"], "Poor soon")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.copilot.tests.test_dashboard_figures --noinput`
Expected: FAIL — `ImportError: cannot import name 'dashboard_figures' from 'services.copilot'`.

- [ ] **Step 3: Implement the module's first half**

Create `services/copilot/dashboard_figures.py`:

```python
"""The figures on each dashboard area, recomputed on the server for Ask Revenact.

Each function calls the same service code as its area's endpoint, for the
same viewer and filters, so what the assistant is told equals what the screen
shows:

- Revenue: `/customers/forecast/` — `forecast.build_rows` and `build_bridge`.
- Health: `/customers/health/` — the Triage score from `triage.triage`, over the
  book `attention.rules.filtered_customers` loads (the Health view's own history
  window), filtered as the screen filters it.
- Support: `/tickets/stats/` — `ticket_filters.filtered_tickets`. The Support
  screen has no lifecycle filter, so none is applied here either.
- Overview: the three headline cards (the three above) and the top of
  `/dashboard/attention/`.

Every set starts from `forecast.filtered_customers(user, filters)` or the
endpoint's own visibility-first filter. Nothing outside it is read.
"""

from services.attention import rules as attention_rules
from services.customers import forecast
from services.customers.triage import ACTION_THRESHOLD, RENEWAL_URGENT_DAYS, triage

#: How many rows any list in the digest carries.
LIST_LIMIT = 10

#: The Triage tile's blind-spot rule: the AI reads the account this many
#: points colder than the CSM does (frontend `summarise`).
BLIND_SPOT_GAP = 2

DIRECTIONS = ("declining", "improving", "flat", "unknown")


def revenue_figures(user, filters):
    organisation = user.organisation
    customers = list(forecast.filtered_customers(user, filters))
    rows = forecast.build_rows(customers, organisation, horizon=forecast.DEFAULT_HORIZON_DAYS)
    bridge = forecast.build_bridge(rows)
    return {
        "currency": organisation.currency,
        "bridge": bridge,
        "at_risk": round(bridge["churn"] + bridge["contraction"], 2),
        "movers": [
            {
                "id": row["id"],
                "name": row["name"],
                "net": row["net"],
                "downside": row["downside"],
                "expansion": row["expansion"],
            }
            for row in forecast.swing_list(rows)[:LIST_LIMIT]
        ],
        "unpriced_count": sum(1 for row in rows if row.arr is None),
    }


def _scored(user, filters, today):
    """Every customer in the filtered book with its Triage result — the Health
    serializer's own call (`CustomerHealthRowSerializer._triage`)."""
    customers = attention_rules.filtered_customers(user, filters)
    return [
        (
            customer,
            triage(
                health_category=customer.health_category,
                csm_pulse=customer.csm_pulse_score,
                ai_pulse=customer.ai_pulse_value,
                renewal_date=customer.renewal_date,
                history=[s.health_category for s in customer.health_snapshots.all()],
                today=today,
            ),
        )
        for customer in customers
    ]


def health_figures(user, filters, *, today):
    scored = _scored(user, filters, today)
    acting = sorted(
        ((c, r) for c, r in scored if r.score >= ACTION_THRESHOLD),
        key=lambda pair: (-pair[1].score, pair[0].name),
    )
    by_direction = {direction: 0 for direction in DIRECTIONS}
    for _customer, result in scored:
        by_direction[result.direction] += 1
    return {
        "total": len(scored),
        "at_good": sum(1 for c, _r in scored if c.health_category == "good"),
        "needs_action": len(acting),
        "needs_action_renewing_soon": sum(
            1
            for _c, r in acting
            if r.days_to_renewal is not None and r.days_to_renewal <= RENEWAL_URGENT_DAYS
        ),
        "blind_spots": sum(
            1
            for c, _r in scored
            if c.csm_pulse_score is not None
            and c.ai_pulse_value is not None
            and c.csm_pulse_score - c.ai_pulse_value >= BLIND_SPOT_GAP
        ),
        "by_direction": by_direction,
        "accounts_needing_action": [
            {
                "id": customer.pk,
                "name": customer.name,
                "score": result.score,
                "factors": [factor["label"] for factor in result.factors],
            }
            for customer, result in acting[:LIST_LIMIT]
        ],
    }
```

- [ ] **Step 4: Run the tests**

Run: `venv/bin/python manage.py test services.copilot.tests.test_dashboard_figures --noinput`
Expected: PASS. `venv/bin/ruff format . && venv/bin/ruff check .` — clean.

- [ ] **Step 5: Commit**

```bash
git add services/copilot/dashboard_figures.py services/copilot/tests/test_dashboard_figures.py
git commit -m "feat(copilot): recompute the Revenue and Health figures for the dashboard

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Support and Overview figures

**Files:**
- Modify: `services/copilot/dashboard_figures.py`
- Modify: `services/copilot/tests/test_dashboard_figures.py`

**Interfaces:**
- Consumes: `customers.ticket_filters.filtered_tickets` (Task 1); `attention.rules.build_items`, `attention.rules.SUPPORT_PRIORITIES`; `attention.snooze.visible_items`; `revenue_figures`, `health_figures` (Task 4).
- Produces:
  - `SUPPORT_FILTER_KEYS = ("owner", "customer")`, `support_filters(filters) -> dict`.
  - `support_figures(user, filters, *, today) -> {"open_count": int, "oldest_open_days": int | None, "priority_by_status": {priority_value: {status_value: int}}, "most_urgent": [{"id", "name", "open_urgent": int}] (≤ 10)}`.
  - `attention_top(user, filters, *, today, now, limit=LIST_LIMIT) -> list[AttentionItem]` (the attention view's items, snoozes dropped, sorted by score desc then title).
  - `overview_figures(user, filters, *, today, now) -> {"currency", "arr_today", "at_risk", "at_good", "total", "needs_action", "open_tickets", "oldest_open_days", "attention"}`.

- [ ] **Step 1: Write the failing tests**

Append to `services/copilot/tests/test_dashboard_figures.py` (add `from django.utils import timezone`, `from services.accounts.models import User`, `from services.attention.models import AttentionSnooze` and `from services.customers.models import Account, Ticket` to its imports):

```python
class SupportFiguresTests(DashboardFixture):
    def setUp(self):
        super().setUp()
        self.mine = self.customer("Mine", lifecycle_stage="live")
        self.busy = self.customer("Busy", lifecycle_stage="renewal")
        self.theirs = self.customer("Theirs", owner=self.other)
        self.ticket(1, self.mine, opened_at=self.today - timedelta(days=9))
        self.ticket(2, self.busy, priority=Ticket.Priority.CRITICAL)
        self.ticket(3, self.busy)
        self.ticket(4, self.busy, priority=Ticket.Priority.LOW)
        self.ticket(5, self.mine, status=Ticket.Status.RESOLVED)
        self.engineering = self.ticket(6, self.mine, department=User.Function.ENGINEERING)
        self.ticket(7, self.theirs)
        account = Account.objects.create(name="Shared", owner=self.other)
        account.customers.add(self.mine, self.theirs)
        self.ticket(8, None, account=account)

    def test_equals_the_ticket_stats_endpoint(self):
        for params in ({}, {"customer": str(self.busy.pk)}, {"owner": str(self.csm.pk)}):
            with self.subTest(params=params):
                figures = dashboard_figures.support_figures(
                    self.csm, clean_filters(params), today=self.today
                )
                screen = self.api.get("/api/v1/tickets/stats/", params).data
                split = figures["priority_by_status"]

                self.assertEqual(figures["open_count"], screen["kpis"]["open_count"])
                self.assertEqual(figures["oldest_open_days"], screen["kpis"]["oldest_open_days"])
                self.assertEqual(
                    {Ticket.Priority(p).label: sum(row.values()) for p, row in split.items()},
                    {row["name"]: row["value"] for row in screen["priority"]},
                )
                self.assertEqual(
                    {
                        Ticket.Status(s).label: sum(row[s] for row in split.values())
                        for s in Ticket.Status.values
                    },
                    {row["name"]: row["value"] for row in screen["status"]},
                )

    def test_lifecycle_does_not_apply_as_on_the_support_screen(self):
        with_lifecycle = dashboard_figures.support_figures(
            self.csm, clean_filters({"lifecycle": "live"}), today=self.today
        )
        without = dashboard_figures.support_figures(self.csm, clean_filters({}), today=self.today)
        self.assertEqual(with_lifecycle, without)

    def test_most_urgent_accounts_within_the_viewers_book_and_department(self):
        figures = dashboard_figures.support_figures(self.csm, clean_filters({}), today=self.today)

        # Busy: two open High/Critical. Mine: one of its own plus the shared
        # account's. Never Theirs; never the engineering ticket.
        self.assertEqual(
            figures["most_urgent"],
            [
                {"id": self.busy.pk, "name": "Busy", "open_urgent": 2},
                {"id": self.mine.pk, "name": "Mine", "open_urgent": 2},
            ],
        )


class OverviewFiguresTests(DashboardFixture):
    def setUp(self):
        super().setUp()
        self.soon = self.customer(
            "Soon", health_score=POOR, renewal_date=self.today + timedelta(days=10), arr=50_000
        )
        self.later = self.customer(
            "Later", health_score=AVERAGE, renewal_date=self.today + timedelta(days=60)
        )
        self.customer(
            "Theirs",
            owner=self.other,
            health_score=POOR,
            renewal_date=self.today + timedelta(days=5),
        )
        self.ticket(1, self.soon, opened_at=self.today - timedelta(days=4))

    def test_equals_the_headline_cards_and_the_attention_list(self):
        now = timezone.now()
        AttentionSnooze.objects.create(
            organisation=self.org,
            user=self.csm,
            key=f"renewal:{self.later.pk}",
            until=None,
            fingerprint={
                "arr": 100000.0,
                "overdue": False,
                "health": "average",
                "renewal_date": self.later.renewal_date.isoformat(),
            },
        )
        filters = clean_filters({})

        figures = dashboard_figures.overview_figures(self.csm, filters, today=self.today, now=now)

        attention = self.api.get("/api/v1/dashboard/attention/").data
        self.assertEqual(figures["attention"], attention["items"][:10])
        self.assertNotIn(f"renewal:{self.later.pk}", [i["key"] for i in figures["attention"]])
        bridge = self.api.get("/api/v1/customers/forecast/", {"horizon_days": "365"}).data["bridge"]
        self.assertEqual(figures["arr_today"], bridge["opening_arr"])
        self.assertEqual(figures["at_risk"], round(bridge["churn"] + bridge["contraction"], 2))
        health = dashboard_figures.health_figures(self.csm, filters, today=self.today)
        self.assertEqual(
            (figures["at_good"], figures["total"], figures["needs_action"]),
            (health["at_good"], health["total"], health["needs_action"]),
        )
        kpis = self.api.get("/api/v1/tickets/stats/").data["kpis"]
        self.assertEqual(
            (figures["open_tickets"], figures["oldest_open_days"]),
            (kpis["open_count"], kpis["oldest_open_days"]),
        )
        self.assertEqual(figures["currency"], "USD")
```

`AttentionSnooze` has `organisation`, `user`, `key`, `until` (null means Done) and `fingerprint`; the renewal fingerprint shape is the one `rules._renewal_items` builds plus `arr`, so this Done snooze hides the item because nothing got worse.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.copilot.tests.test_dashboard_figures --noinput`
Expected: FAIL — `AttributeError: module 'services.copilot.dashboard_figures' has no attribute 'support_figures'`.

- [ ] **Step 3: Implement the second half**

In `services/copilot/dashboard_figures.py`, extend the imports:

```python
from collections import Counter

from django.db.models import Count, Min, Q

from services.attention import rules as attention_rules
from services.attention.snooze import visible_items
from services.customers import forecast
from services.customers.models import Ticket
from services.customers.ticket_filters import filtered_tickets
from services.customers.triage import ACTION_THRESHOLD, RENEWAL_URGENT_DAYS, triage
```

Add below `DIRECTIONS`:

```python
#: The Support screen's filters. It has no lifecycle filter.
SUPPORT_FILTER_KEYS = ("owner", "customer")
```

Append:

```python
def support_filters(filters):
    return {key: filters.get(key, "") for key in SUPPORT_FILTER_KEYS}


def _most_urgent(user, open_tickets, params):
    """The companies with the most open High/Critical tickets, inside the
    filtered book. A ticket on an account counts for each of that account's
    companies in the book — the attention list's own rule."""
    book = {customer.pk: customer for customer in forecast.filtered_customers(user, params)}
    if not book:
        return []
    ids = list(book)
    urgent = (
        open_tickets.filter(priority__in=attention_rules.SUPPORT_PRIORITIES)
        .filter(Q(customer_id__in=ids) | Q(account__customers__id__in=ids))
        .distinct()
        .prefetch_related("account__customers")
    )
    counts = Counter()
    for ticket in urgent:
        targets = {ticket.customer_id} if ticket.customer_id else set()
        if ticket.account_id:
            targets |= {customer.pk for customer in ticket.account.customers.all()}
        for pk in targets & book.keys():
            counts[pk] += 1
    ranked = sorted(counts.items(), key=lambda pair: (-pair[1], book[pair[0]].name))
    return [{"id": pk, "name": book[pk].name, "open_urgent": n} for pk, n in ranked[:LIST_LIMIT]]


def support_figures(user, filters, *, today):
    params = support_filters(filters)
    tickets = filtered_tickets(user, params)
    open_tickets = tickets.exclude(status__in=Ticket.RESOLVED_STATUSES)
    stats = open_tickets.aggregate(count=Count("id"), oldest=Min("opened_at"))
    open_count = stats["count"]
    oldest = None
    if open_count > 0 and stats["oldest"] is not None:
        oldest = (today - stats["oldest"]).days

    # `.order_by()` before the annotate: Ticket's default ordering would
    # otherwise join the GROUP BY (see TicketStatsView).
    split = {p: {s: 0 for s in Ticket.Status.values} for p in Ticket.Priority.values}
    for priority, status_value, n in (
        tickets.order_by()
        .values_list("priority", "status")
        .annotate(n=Count("id"))
        .values_list("priority", "status", "n")
    ):
        split.setdefault(priority, {s: 0 for s in Ticket.Status.values})[status_value] = n

    return {
        "open_count": open_count,
        "oldest_open_days": oldest,
        "priority_by_status": split,
        "most_urgent": _most_urgent(user, open_tickets, params),
    }


def attention_top(user, filters, *, today, now, limit=LIST_LIMIT):
    """The top of the viewer's own "Needs attention" list — `AttentionListView`
    exactly: every candidate, snoozes dropped, score then title."""
    items = attention_rules.build_items(user, filters, today=today)
    items = visible_items(user, items, now=now)
    items.sort(key=lambda item: (-item["score"], item["title"]))
    return items[:limit]


def overview_figures(user, filters, *, today, now):
    """The Overview's three headline cards and its attention list."""
    revenue = revenue_figures(user, filters)
    health = health_figures(user, filters, today=today)
    support = support_figures(user, filters, today=today)
    return {
        "currency": revenue["currency"],
        "arr_today": revenue["bridge"]["opening_arr"],
        "at_risk": revenue["at_risk"],
        "at_good": health["at_good"],
        "total": health["total"],
        "needs_action": health["needs_action"],
        "open_tickets": support["open_count"],
        "oldest_open_days": support["oldest_open_days"],
        "attention": attention_top(user, filters, today=today, now=now),
    }
```

- [ ] **Step 4: Run the tests**

Run: `venv/bin/python manage.py test services.copilot.tests.test_dashboard_figures --noinput`
Expected: PASS. `venv/bin/ruff format . && venv/bin/ruff check .` — clean.

- [ ] **Step 5: Commit**

```bash
git add services/copilot/dashboard_figures.py services/copilot/tests/test_dashboard_figures.py
git commit -m "feat(copilot): recompute the Support and Overview figures for the dashboard

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: The digest, the records and the prompt

**Files:**
- Create: `services/copilot/dashboard_grounding.py`
- Create: `services/copilot/tests/test_dashboard_grounding.py`

**Interfaces:**
- Consumes: `dashboard_figures.*` (Tasks 4–5); `dashboard_context.AREA_LABELS`, `DASHBOARD_VIEWS` (Task 3); `attention.rules.current_item` (Task 1); `context.Grounding(summary, sources, company)`; `retrieval.find_mentioned_company(query, customers, accounts)`, `retrieval.retrieve_with_sources(company, limit, query="", viewer=None)`, `retrieval._source_ref(*, kind, record_id, label, date, company)`; `anomalies.views.visible_evidence(organisation, viewer, *, anomaly=None)`; `scoping.sees_everything(user)`; `forecast.filter_options(user)`.
- Produces:
  - `build_dashboard_grounding(user, context, question, *, today=None, now=None) -> Grounding` — `context` is a validated context (Task 3's shape). `Grounding.company` is the one target customer when there is exactly one, else `None`.
  - `dashboard_system_prompt(tone_instruction: str, summary: str) -> str`.
  - `DASHBOARD_PERSONA: str`.

- [ ] **Step 1: Write the failing tests**

Create `services/copilot/tests/test_dashboard_grounding.py`:

```python
"""What the model is told on the dashboard: the screen's figures, labelled
with where they were computed, and the records behind them — never anything
outside the asker's filtered, visible book."""

from datetime import timedelta
from unittest.mock import patch

from services.accounts.models import User
from services.copilot.dashboard_grounding import (
    build_dashboard_grounding,
    dashboard_system_prompt,
)
from services.customers.models import Contact, Note

from .dashboard_fixture import POOR, DashboardFixture


def _in_order(query, candidates):
    return [(index, 1.0) for index in range(len(candidates))]


class GroundingTests(DashboardFixture):
    def setUp(self):
        super().setUp()
        # Retrieval ranks with local embeddings; the order is not under test.
        patcher = patch("services.copilot.retrieval.rank_by_similarity", side_effect=_in_order)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.shaky = self.customer(
            "Shaky",
            health_score=POOR,
            renewal_date=self.today + timedelta(days=30),
            arr=50_000,
        )
        self.steady = self.customer("Steady", arr=80_000)
        self.theirs = self.customer("Theirs", owner=self.other, health_score=POOR)

    def ground(self, context, question="Why?", user=None):
        return build_dashboard_grounding(user or self.csm, context, question, today=self.today)

    def test_the_digest_is_labelled_with_the_screen(self):
        summary = self.ground(self.context("revenue", "forecast", owner=str(self.csm.pk))).summary

        self.assertIn("Screen: Revenue › Forecast", summary)
        self.assertIn("Filters: Owner: Carl", summary)
        self.assertIn("Currency: USD", summary)

    def test_each_area_has_its_digest(self):
        cases = {
            ("overview", None): "ARR today: 130,000.00 USD",
            ("revenue", "forecast"): "Opening ARR: 130,000.00 USD",
            ("health", "triage"): "Health triage over 2 accounts",
            ("support", "tickets"): "Open tickets: 0",
        }
        for (area, view), expected in cases.items():
            with self.subTest(area=area):
                self.assertIn(expected, self.ground(self.context(area, view)).summary)

    def test_revenue_says_there_is_no_new_business_figure(self):
        summary = self.ground(self.context("revenue", "forecast")).summary
        self.assertIn("no new-business figure", summary)

    def test_another_csms_customer_never_appears_even_as_focus(self):
        # Straight to the builder, past the serializer: the builder narrows too.
        focus = {"kind": "companies", "ids": [self.theirs.pk, self.shaky.pk]}
        for area, view in (("overview", None), ("revenue", "forecast"), ("health", "triage")):
            with self.subTest(area=area):
                grounding = self.ground(self.context(area, view, focus=focus))
                self.assertNotIn("Theirs", grounding.summary)
                self.assertIn("Shaky: health poor", grounding.summary)

    def test_a_named_company_inside_the_book_gets_its_facts_and_records(self):
        Note.objects.create(
            customer=self.shaky,
            title="Budget freeze",
            author_name="Edgar",
            body="Procurement froze all renewals.",
            logged_at=str(self.today),
        )
        Contact.objects.create(customer=self.shaky, name="Priya Rao", email="p@shaky.io")

        grounding = self.ground(self.context("revenue", "forecast"), "Why is Shaky at risk?")

        self.assertIn("The question names Shaky.", grounding.summary)
        self.assertIn("renews", grounding.summary)
        self.assertIn("Priya Rao", grounding.summary)
        self.assertNotIn("p@shaky.io", grounding.summary)
        self.assertIn("Budget freeze", grounding.summary)
        self.assertIn("Budget freeze", [s["label"] for s in grounding.sources])
        self.assertEqual(grounding.company, self.shaky)

    def test_a_named_company_outside_the_book_is_not_read(self):
        grounding = self.ground(self.context("overview"), "What about Theirs?")
        self.assertNotIn("Theirs", grounding.summary)
        self.assertIsNone(grounding.company)

    def test_a_ticket_outside_the_viewers_department_is_never_cited(self):
        self.ticket(1, self.shaky, title="Checkout broken")
        self.ticket(2, self.shaky, title="Kernel panic", department=User.Function.ENGINEERING)
        focus = {"kind": "companies", "ids": [self.shaky.pk]}

        grounding = self.ground(self.context("support", "tickets", focus=focus))

        self.assertIn("Checkout broken", grounding.summary)
        self.assertNotIn("Kernel panic", grounding.summary)
        self.assertEqual(
            {s["label"] for s in grounding.sources if s["type"] == "ticket"},
            {"TKT-1 Checkout broken"},
        )

    def test_an_attention_item_is_explained_with_its_company(self):
        key = f"renewal:{self.shaky.pk}"
        focus = {"kind": "attention", "key": key}

        grounding = self.ground(self.context(focus=focus))

        self.assertIn("why this is on their attention list: Shaky", grounding.summary)
        self.assertIn("Shaky: health poor", grounding.summary)
        self.assertEqual(grounding.company, self.shaky)

    def test_an_attention_item_outside_the_filters_is_not_read(self):
        focus = {"kind": "attention", "key": f"renewal:{self.shaky.pk}"}
        grounding = self.ground(self.context(focus=focus, customer=str(self.steady.pk)))
        self.assertIn("outside the current filters", grounding.summary)
        self.assertNotIn("Shaky: health", grounding.summary)


class AnomalyTitleTests(DashboardFixture):
    def setUp(self):
        super().setUp()
        self.mine = self.customer("Mine")
        self.customer("Theirs", owner=self.other)
        self.live = self.anomaly(
            "Theirs and Mine both report SSO failures", summary="Theirs is down too"
        )
        self.row = self.evidence(self.live, 41, customer=self.mine)
        self.focus = {"kind": "attention", "key": f"anomaly:{self.live.pk}"}

    def test_a_viewer_who_does_not_see_everything_gets_the_built_title(self):
        grounding = build_dashboard_grounding(
            self.csm, self.context(focus=self.focus), "Why?", today=self.today
        )

        self.assertIn("Similar reports across 1 of your companies", grounding.summary)
        self.assertNotIn("Theirs", grounding.summary)
        self.assertIn("Login fails after SSO redirect", grounding.summary)
        self.assertEqual(
            [(s["type"], s["id"]) for s in grounding.sources], [("call", self.row.record_id)]
        )

    def test_a_viewer_who_sees_everything_gets_the_stored_title(self):
        grounding = build_dashboard_grounding(
            self.admin, self.context(focus=self.focus), "Why?", today=self.today
        )

        self.assertIn("Theirs and Mine both report SSO failures", grounding.summary)
        self.assertIn("Theirs is down too", grounding.summary)


class PromptTests(DashboardFixture):
    def test_the_prompt_confines_the_answer_to_the_digest(self):
        prompt = dashboard_system_prompt("Be concise.", "Screen: Overview")

        self.assertIn("Answer only from", prompt)
        self.assertIn("say so plainly", prompt)
        self.assertIn("Cite", prompt)
        self.assertIn("Be concise.", prompt)
        self.assertTrue(prompt.endswith("Dashboard data:\nScreen: Overview"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.copilot.tests.test_dashboard_grounding --noinput`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.copilot.dashboard_grounding'`.

- [ ] **Step 3: Implement `dashboard_grounding.py`**

```python
"""Grounds a dashboard answer in what is on the asker's screen.

The client says where it is (`dashboard_context`); this module computes what
is there. The digest opens with the screen, the filters and the currency it
was computed for, then the area's figures from `dashboard_figures` — the same
code as the area's endpoint — then, for the companies the question is about,
a facts line each and their records through the existing retrieval, which
keeps its own record-level rules.

First filter, always: `forecast.filtered_customers(user, filters)`. A
company outside it is never named, however it was asked about.
"""

from django.utils import timezone

from services.anomalies.models import Anomaly
from services.anomalies.views import visible_evidence
from services.attention.rules import current_item
from services.customers import forecast
from services.customers.models import Contact, Customer, Ticket
from services.customers.scoping import sees_everything
from services.customers.triage import ACTION_THRESHOLD, RENEWAL_URGENT_DAYS
from services.fx_rates.conversion import convert_to_org_currency, rates_for

from . import dashboard_figures
from .context import Grounding
from .dashboard_context import AREA_LABELS, DASHBOARD_VIEWS
from .retrieval import _source_ref, find_mentioned_company, retrieve_with_sources

#: Companies that get a facts line; a drill can carry up to 200.
FACT_COMPANIES = 20
#: Companies whose records are retrieved, and how many records each.
RECORD_COMPANIES = 5
RECORDS_FOR_ONE = 6
RECORDS_FOR_MANY = 3
CONTACT_LIMIT = 5
EVIDENCE_LIMIT = 6

DASHBOARD_PERSONA = (
    "You are Ask Revenact, the assistant on the Revenact dashboard. Below is what is "
    "on the asker's screen: the figures for the screen and filters named at the top, "
    "recomputed for them, and the records behind the companies they asked about. "
    "Answer only from that data. When the answer is not in it, say so plainly and "
    "do not guess. Cite the records you rely on by their label. Give money in the "
    "currency it is labelled with. Never invent a figure, an event or a name."
)


def dashboard_system_prompt(tone_instruction, summary):
    return f"{DASHBOARD_PERSONA}\n\n{tone_instruction}\n\nDashboard data:\n{summary}"


def _money(value, currency):
    return f"{value:,.2f} {currency}"


def _days(n):
    return f"{n} day" if n == 1 else f"{n} days"


def _filter_summary(user, filters):
    options = forecast.filter_options(user)
    names = {
        "owner": {o["value"]: o["name"] for o in options["owners"]},
        "lifecycle": dict(Customer.LifecycleStage.choices),
        "customer": {o["value"]: o["name"] for o in options["customers"]},
    }
    parts = []
    for key, label in (("owner", "Owner"), ("lifecycle", "Lifecycle"), ("customer", "Account")):
        value = filters.get(key, "")
        if value:
            parts.append(f"{label}: {names[key].get(value, f'{value} (not in your options)')}")
    return "; ".join(parts) or "none (the whole book the asker can see)"


def _header(user, context):
    area, view = context["area"], context.get("view")
    screen = AREA_LABELS[area] + (f" › {DASHBOARD_VIEWS[area][view]}" if view else "")
    return [
        f"Screen: {screen}",
        f"Filters: {_filter_summary(user, context['filters'])}",
        f"Currency: {user.organisation.currency}",
    ]


def _overview_lines(user, filters, *, today, now):
    currency = user.organisation.currency
    f = dashboard_figures.overview_figures(user, filters, today=today, now=now)
    oldest = f["oldest_open_days"]
    lines = [
        "Headline figures:",
        f"  ARR today: {_money(f['arr_today'], currency)}; at risk over the next 12 months "
        f"(churn and contraction): {_money(f['at_risk'], currency)}",
        f"  Book at Good: {f['at_good']} of {f['total']}; needs action: {f['needs_action']}",
        f"  Open tickets: {f['open_tickets']}"
        + ("" if oldest is None else f"; oldest open {_days(oldest)}"),
    ]
    if f["attention"]:
        lines.append(f"Needs attention (top {len(f['attention'])}, highest first):")
        lines.extend(
            f"  - {item['title']}: {item['reason']}; at stake {_money(item['at_stake'], currency)}"
            for item in f["attention"]
        )
    else:
        lines.append("Needs attention: nothing on the list.")
    return lines


def _revenue_lines(user, filters, *, today, now):
    currency = user.organisation.currency
    f = dashboard_figures.revenue_figures(user, filters)
    bridge = f["bridge"]
    nrr = "n/a" if bridge["nrr"] is None else f"{bridge['nrr']}%"
    lines = [
        f"Revenue forecast over the next {forecast.DEFAULT_HORIZON_DAYS} days:",
        f"  Opening ARR: {_money(bridge['opening_arr'], currency)}",
        f"  Expected churn: {_money(bridge['churn'], currency)}",
        f"  Expected contraction: {_money(bridge['contraction'], currency)}",
        f"  Expected expansion: {_money(bridge['expansion'], currency)}",
        f"  Forecast ARR: {_money(bridge['forecast_arr'], currency)} "
        f"(net change {_money(bridge['net_change'], currency)}, NRR {nrr})",
        f"  At-risk ARR (churn and contraction): {_money(f['at_risk'], currency)}",
        "  The forecast covers the existing book only; it has no new-business figure.",
    ]
    if f["unpriced_count"]:
        lines.append(
            f"  {f['unpriced_count']} accounts have no exchange rate to {currency} "
            "and are left out of these sums."
        )
    if f["movers"]:
        lines.append("Largest movers (by net effect on the forecast):")
        lines.extend(
            f"  - {m['name']}: net {_money(m['net'], currency)} "
            f"(downside {_money(m['downside'], currency)}, "
            f"expansion {_money(m['expansion'], currency)})"
            for m in f["movers"]
        )
    return lines


def _health_lines(user, filters, *, today, now):
    f = dashboard_figures.health_figures(user, filters, today=today)
    d = f["by_direction"]
    lines = [
        f"Health triage over {f['total']} accounts:",
        f"  At Good: {f['at_good']} of {f['total']}",
        f"  Needs action now (triage score {ACTION_THRESHOLD} or more): {f['needs_action']}, "
        f"of which {f['needs_action_renewing_soon']} renew within {RENEWAL_URGENT_DAYS} days",
        f"  Over the last three months: {d['declining']} declining, {d['improving']} "
        f"improving, {d['flat']} flat, {d['unknown']} without enough history",
        f"  AI pulse {dashboard_figures.BLIND_SPOT_GAP} or more points colder than the "
        f"CSM's: {f['blind_spots']}",
    ]
    if f["accounts_needing_action"]:
        lines.append("Accounts needing action (highest score first):")
        lines.extend(
            f"  - {a['name']}: score {a['score']} ({'; '.join(a['factors'])})"
            for a in f["accounts_needing_action"]
        )
    return lines


def _support_lines(user, filters, *, today, now):
    f = dashboard_figures.support_figures(user, filters, today=today)
    oldest = f["oldest_open_days"]
    priorities, statuses = dict(Ticket.Priority.choices), dict(Ticket.Status.choices)
    lines = [
        "Support tickets (the Support screen has no lifecycle filter, so none is applied):",
        f"  Open tickets: {f['open_count']}"
        + ("" if oldest is None else f"; oldest open {_days(oldest)}"),
        "  All tickets by priority and status:",
    ]
    for priority, by_status in f["priority_by_status"].items():
        counts = ", ".join(f"{statuses.get(s, s)} {n}" for s, n in by_status.items() if n)
        lines.append(f"    {priorities.get(priority, priority)}: {counts or 'none'}")
    if f["most_urgent"]:
        lines.append("Accounts with the most open High or Critical tickets:")
        lines.extend(f"  - {row['name']}: {row['open_urgent']}" for row in f["most_urgent"])
    return lines


AREA_DIGESTS = {
    "overview": _overview_lines,
    "revenue": _revenue_lines,
    "health": _health_lines,
    "support": _support_lines,
}


def _facts_lines(customer, organisation, rates, today):
    arr = convert_to_org_currency(
        customer.arr_billed_at_account, customer.currency, organisation, rates=rates
    )
    money = (
        "ARR unknown (no exchange rate)"
        if arr is None
        else f"ARR {_money(float(arr), organisation.currency)}"
    )
    renewal = (
        "no renewal date"
        if customer.renewal_date is None
        else f"renews {customer.renewal_date.isoformat()} "
        f"({_days((customer.renewal_date - today).days)})"
    )
    owner = customer.owner.name if customer.owner else "Unassigned"
    lines = [
        f"{customer.name}: health {customer.health_category} ({customer.health_score}/10), "
        f"{renewal}, {money}, owner {owner}"
    ]
    # Names, roles and sentiment only — never an email address or a phone.
    contacts = Contact.objects.filter(customer=customer).order_by("name")[:CONTACT_LIMIT]
    if contacts:
        lines.append(
            f"  Contacts at {customer.name}: "
            + "; ".join(
                f"{c.name} ({c.get_role_display()}, sentiment {c.sentiment or 'unknown'})"
                for c in contacts
            )
        )
    return lines


def _company_in_book(row, book):
    if row.customer_id in book:
        return book[row.customer_id]
    if row.account_id:
        inside = [c for c in row.account.customers.all() if c.pk in book]
        return min(inside, key=lambda c: (c.name, c.pk)) if inside else None
    return None


def _anomaly_lines(user, anomaly_id, book):
    """The anomaly's evidence the viewer may read, on companies in the book.
    Its model-written summary follows the title rule: only for a viewer who
    sees every account."""
    anomaly = Anomaly.objects.filter(organisation=user.organisation, pk=anomaly_id).first()
    if anomaly is None:
        return [], []
    lines, sources = [], []
    if sees_everything(user) and anomaly.summary:
        lines.append(f"  What the reports have in common: {anomaly.summary}")
    rows = (
        visible_evidence(user.organisation, user, anomaly=anomaly)
        .prefetch_related("account__customers")
        .order_by("-occurred_at")
    )
    for row in rows:
        company = _company_in_book(row, book)
        if company is None:
            continue
        kind = row.get_kind_display()
        lines.append(f"  - {kind} ({row.occurred_at.date()}) at {company.name}: {row.snippet}")
        sources.append(
            _source_ref(
                kind=row.kind,
                record_id=row.record_id,
                label=f"{kind} at {company.name}",
                date=row.occurred_at.date(),
                company=company,
            )
        )
        if len(sources) == EVIDENCE_LIMIT:
            break
    return lines, sources


OUTSIDE = "The attention item asked about is outside the current filters, so it is not read."


def _attention_focus(user, key, book):
    """(lines, targets, sources) for an attention focus. `key` was checked
    against the viewer's list when the send was validated; it is looked up
    again here because the list can change."""
    item = current_item(user, key)
    if item is None:
        return ["The attention item asked about is no longer on the asker's list."], [], []
    currency = user.organisation.currency
    if item["kind"] == "anomaly":
        inside = [c for c in item["companies"] if c["id"] in book]
        if not inside:
            return [OUTSIDE], [], []
        title = (
            item["title"]
            if sees_everything(user)
            else f"Similar reports across {len(inside)} of your companies"
        )
        lines = [
            f"The asker wants to know why this is on their attention list: {title} "
            f"({', '.join(c['name'] for c in inside)})."
        ]
        evidence_lines, sources = _anomaly_lines(user, int(key.split(":")[1]), book)
        return lines + evidence_lines, [], sources
    customer = book.get(item["customer_id"])
    if customer is None:
        return [OUTSIDE], [], []
    return (
        [
            f"The asker wants to know why this is on their attention list: {item['title']}: "
            f"{item['reason']}; at stake {_money(item['at_stake'], currency)}."
        ],
        [customer],
        [],
    )


def _focus(user, focus, question, book):
    """(lines, targets, sources): who the question is about, inside the book."""
    if focus is not None and focus["kind"] == "attention":
        return _attention_focus(user, focus["key"], book)
    if focus is not None and focus["ids"]:
        targets = sorted(
            (book[pk] for pk in focus["ids"] if pk in book), key=lambda c: (c.name, c.pk)
        )
        return [f"The asker selected {len(targets)} companies on screen:"], targets, []
    ordered = sorted(book.values(), key=lambda c: (c.name, c.pk))
    named = find_mentioned_company(question, ordered, []) if question else None
    if named is None:
        return [], [], []
    return [f"The question names {named.name}."], [named], []


def build_dashboard_grounding(user, context, question, *, today=None, now=None):
    today = today or timezone.localdate()
    now = now or timezone.now()
    organisation = user.organisation
    filters = context["filters"]

    lines = _header(user, context)
    lines.extend(AREA_DIGESTS[context["area"]](user, filters, today=today, now=now))

    # SOC2:AUTH-02 records are read only for companies in the asker's filtered, visible book
    book = {customer.pk: customer for customer in forecast.filtered_customers(user, filters)}
    focus_lines, targets, sources = _focus(user, context.get("focus"), question, book)
    lines.extend(focus_lines)

    rates = rates_for(organisation)
    for customer in targets[:FACT_COMPANIES]:
        lines.extend(_facts_lines(customer, organisation, rates, today))

    limit = RECORDS_FOR_ONE if len(targets) == 1 else RECORDS_FOR_MANY
    for customer in targets[:RECORD_COMPANIES]:
        items = retrieve_with_sources(customer, limit=limit, query=question, viewer=user)
        if items:
            lines.append(f"Records for {customer.name}:")
            lines.extend(f"  - {item.line}" for item in items)
            sources.extend(item.source for item in items)

    company = targets[0] if len(targets) == 1 else None
    return Grounding("\n".join(lines), sources, company)
```

Note: `Contact.sentiment` may be a choices field; if `get_sentiment_display` exists, keep the raw value anyway — the test asserts only the name and the absence of the email.

- [ ] **Step 4: Run the tests**

Run: `venv/bin/python manage.py test services.copilot.tests.test_dashboard_grounding --noinput`
Expected: PASS. If `test_each_area_has_its_digest` fails on the Overview line, check that `arr_billed_at_account` for "Theirs" was not counted (it must not be) and that `130,000.00` equals Shaky 50,000 + Steady 80,000.

Run: `venv/bin/ruff format . && venv/bin/ruff check .` — clean.

- [ ] **Step 5: Commit**

```bash
git add services/copilot/dashboard_grounding.py services/copilot/tests/test_dashboard_grounding.py
git commit -m "feat(copilot): ground dashboard answers in the screen and its records

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: The `dashboard` purpose

**Files:**
- Modify: `services/copilot/usage.py` (`PURPOSES`), `services/copilot/skills.py` (`SKILLS`)
- Test: `services/copilot/tests/test_skills.py`

**Interfaces:**
- Produces: `usage.PURPOSES["dashboard"] == "Ask Revenact on the Dashboard"`; `skills.BY_PURPOSE["dashboard"].surface == "/dashboard"`.

- [ ] **Step 1: Write the failing test**

Append to `CatalogueTests` in `services/copilot/tests/test_skills.py`:

```python
    def test_the_dashboard_has_its_own_purpose_and_skill(self):
        self.assertEqual(usage.PURPOSES["dashboard"], "Ask Revenact on the Dashboard")
        skill = skills.BY_PURPOSE["dashboard"]
        self.assertEqual(skill.surface, "/dashboard")
        self.assertIn("See accounts outside the asker's filtered book", skill.never)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `venv/bin/python manage.py test services.copilot.tests.test_skills --noinput`
Expected: FAIL — `KeyError: 'dashboard'`.

- [ ] **Step 3: Add the purpose and the skill**

In `usage.PURPOSES`, after `"copilot": "Copilot chat",`:

```text
    "dashboard": "Ask Revenact on the Dashboard",
```

In `skills.SKILLS`, directly after the `copilot` skill:

```text
    Skill(
        "dashboard",
        "Ask Revenact on the Dashboard",
        "Answers a question about what the dashboard shows, from the same figures and "
        "the records behind them.",
        (
            "The figures on the asker's screen, recomputed for its area, view and filters",
            "The records retrieval finds for the companies asked about, under the asker's "
            "own visibility",
            "The conversation so far",
        ),
        ("Answer in prose", "Quote the records it used as sources on the reply"),
        (
            "Change a record",
            "Send anything to a customer",
            "See accounts outside the asker's filtered book",
            "Take figures from the client",
        ),
        "A person asks from the Dashboard's Ask rail",
        "Any signed-in user, while the organisation's AI agent is enabled",
        "/dashboard",
    ),
```

- [ ] **Step 4: Run the tests**

Run: `venv/bin/python manage.py test services.copilot.tests.test_skills services.copilot.tests.test_usage --noinput`
Expected: PASS (`test_every_purpose_a_call_can_run_under_is_described` still holds).

- [ ] **Step 5: Commit**

```bash
git add services/copilot/usage.py services/copilot/skills.py services/copilot/tests/test_skills.py
git commit -m "feat(copilot): meter dashboard answers under their own purpose

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: `SendMessageView` takes `context`

**Files:**
- Modify: `services/copilot/views.py` (`SendMessageView.post`, docstring)
- Create: `services/copilot/tests/test_dashboard_send.py`
- Modify: `services/copilot/tests/test_views.py` (Communications regression)

**Interfaces:**
- Consumes: `DashboardContextSerializer`, `origin_of` (Task 3); `build_dashboard_grounding`, `dashboard_system_prompt` (Task 6); purpose `dashboard` (Task 7); `Message.context`, `Conversation.origin` (Task 2).
- Produces: the contract in Global Constraints. `400` body for a bad context is `{"context": <serializer errors>}`.

- [ ] **Step 1: Write the failing tests**

Create `services/copilot/tests/test_dashboard_send.py`:

```python
"""POST /api/v1/copilot/messages/ with a dashboard `context`. The model call is
always stubbed; the tests read the prompt it was given."""

from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings

from services.copilot.anthropic_client import BudgetExceeded
from services.copilot.models import Conversation, Message, ModelCall

from .dashboard_fixture import POOR, DashboardFixture

URL = "/api/v1/copilot/messages/"


@patch("services.copilot.views.get_completion", return_value="Because Shaky renews soon.")
class DashboardSendTests(DashboardFixture):
    def setUp(self):
        super().setUp()
        self.shaky = self.customer(
            "Shaky", health_score=POOR, renewal_date=self.today + timedelta(days=10), arr=50_000
        )
        self.theirs = self.customer(
            "Theirs",
            owner=self.other,
            health_score=POOR,
            renewal_date=self.today + timedelta(days=5),
        )

    def send(self, context, content="Why is at-risk ARR up?", **extra):
        return self.api.post(URL, {"content": content, "context": context, **extra}, format="json")

    def test_the_answer_is_grounded_in_the_screen_and_metered_as_dashboard(self, completion):
        response = self.send(self.context("revenue", "forecast", owner=str(self.csm.pk)))

        self.assertEqual(response.status_code, 200, response.data)
        kwargs = completion.call_args.kwargs
        self.assertEqual(kwargs["purpose"], "dashboard")
        self.assertIn("Screen: Revenue › Forecast", kwargs["system"])
        self.assertIn("Opening ARR: 50,000.00 USD", kwargs["system"])
        self.assertIn("Answer only from", kwargs["system"])
        self.assertNotIn("Theirs", kwargs["system"])
        self.assertNotIn("Real-data summary", kwargs["system"])

    def test_the_context_is_echoed_on_the_turn_and_the_origin_on_the_conversation(self, completion):
        context = self.context("health", "triage", lifecycle="renewal")

        data = self.send(context).data

        self.assertEqual(data["messages"][0]["context"], context)
        self.assertIsNone(data["messages"][1]["context"])
        origin = {key: value for key, value in context.items() if key != "focus"}
        self.assertEqual(data["origin"], origin)
        self.assertEqual(Conversation.objects.get().origin, origin)

    def test_origin_is_set_once(self, completion):
        first = self.send(self.context("overview")).data
        second = self.send(self.context("support", "tickets"), conversation_id=first["id"]).data

        self.assertEqual(second["origin"]["area"], "overview")
        self.assertEqual(
            [m["context"]["area"] for m in second["messages"] if m["role"] == "user"],
            ["overview", "support"],
        )

    def test_origin_comes_from_the_first_dashboard_message(self, completion):
        started = self.api.post(URL, {"content": "[About: Shaky] Hi"}, format="json").data
        self.assertIsNone(started["origin"])

        data = self.send(self.context("revenue", "forecast"), conversation_id=started["id"]).data

        self.assertEqual(data["origin"]["area"], "revenue")

    def test_focus_ids_of_another_csm_are_dropped_silently(self, completion):
        focus = {"kind": "companies", "ids": [self.theirs.pk, self.shaky.pk]}

        response = self.send(self.context("overview", focus=focus))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Message.objects.get(role="user").context["focus"],
            {"kind": "companies", "ids": [self.shaky.pk]},
        )
        self.assertNotIn("Theirs", completion.call_args.kwargs["system"])

    def test_why_on_an_attention_row(self, completion):
        focus = {"kind": "attention", "key": f"renewal:{self.shaky.pk}"}

        response = self.send(self.context("overview", focus=focus), "Why is this on my list?")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "why this is on their attention list: Shaky", completion.call_args.kwargs["system"]
        )

    def test_a_key_not_on_the_list_is_the_generic_400(self, completion):
        bodies = [
            self.send(self.context(focus={"kind": "attention", "key": key})).data
            for key in (f"renewal:{self.theirs.pk}", "renewal:nope")
        ]

        self.assertEqual(bodies[0], {"context": {"focus": {"key": ["Not an item on your list."]}}})
        self.assertEqual(bodies[0], bodies[1])
        completion.assert_not_called()
        self.assertEqual(Conversation.objects.count(), 0)

    def test_validation_errors_are_400_and_leave_no_trace(self, completion):
        for context in (
            {**self.context(), "surface": "brain"},
            {**self.context(), "area": "brain"},
            self.context("revenue", None),
            self.context(focus={"kind": "segment"}),
            self.context(focus={"kind": "companies", "ids": list(range(1, 202))}),
            "revenue",
        ):
            with self.subTest(context=context):
                response = self.send(context)
                self.assertEqual(response.status_code, 400)
                self.assertIn("context", response.data)
        completion.assert_not_called()
        self.assertEqual(Message.objects.count(), 0)

    def test_a_null_context_is_a_plain_send(self, completion):
        response = self.api.post(URL, {"content": "Hi", "context": None}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(completion.call_args.kwargs["purpose"], "copilot")

    def test_an_exhausted_budget_is_a_429(self, completion):
        completion.side_effect = BudgetExceeded("Monthly budget reached")

        response = self.send(self.context("overview"))

        self.assertEqual(response.status_code, 429)
        self.assertEqual(Conversation.objects.count(), 0)


class DashboardBudgetTests(DashboardFixture):
    """Not stubbed: the real budget check runs before any call is made."""

    @override_settings(MODEL_BUDGET_DEFAULT_TOKENS=1)
    def test_the_dashboard_purpose_has_its_own_budget(self):
        ModelCall.objects.create(
            organisation=self.org, purpose="dashboard", input_tokens=5, outcome="ok"
        )

        response = self.api.post(
            URL, {"content": "Why?", "context": self.context("overview")}, format="json"
        )

        self.assertEqual(response.status_code, 429)
        self.assertIn("budget", response.data["detail"])
        self.assertTrue(
            ModelCall.objects.filter(purpose="dashboard", outcome="over_budget").exists()
        )
```

Append to `services/copilot/tests/test_views.py`:

```python
class CommunicationsRegressionTests(APITestCase):
    """A send without `context` — Communications and Copilot — behaves exactly
    as before the dashboard: the text prefix, the book summary, the copilot
    purpose, nothing stored about a screen."""

    url = "/api/v1/copilot/messages/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.user = User.objects.create_user(
            email="alice@acme.io", password="supersecret1", name="Alice", organisation=self.org
        )
        Customer.objects.create(organisation=self.org, name="Globex", owner=self.user)
        self.client.force_authenticate(self.user)

    @patch("services.copilot.views.get_completion")
    def test_a_prefixed_send_without_context(self, mock_get_completion):
        mock_get_completion.return_value = "All quiet."

        response = self.client.post(
            self.url, {"content": "[About: Globex] What changed?"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        kwargs = mock_get_completion.call_args.kwargs
        self.assertEqual(kwargs["purpose"], "copilot")
        self.assertIn("Real-data summary", kwargs["system"])
        self.assertNotIn("Screen:", kwargs["system"])
        self.assertEqual(kwargs["messages"][-1]["content"], "[About: Globex] What changed?")
        self.assertIsNone(response.data["origin"])
        self.assertIsNone(response.data["messages"][0]["context"])
        self.assertIsNone(Message.objects.get(role=Message.Role.USER).context)
        self.assertIsNone(Conversation.objects.get().origin)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python manage.py test services.copilot.tests.test_dashboard_send services.copilot.tests.test_views.CommunicationsRegressionTests --noinput`
Expected: FAIL — dashboard sends are answered as plain Copilot sends (`purpose` is `"copilot"`, no `Screen:` in the prompt, validation cases return 200). The regression test passes already; it guards Step 3.

- [ ] **Step 3: Wire the view**

In `services/copilot/views.py`, add to the imports:

```python
from .dashboard_context import DashboardContextSerializer, origin_of
from .dashboard_grounding import build_dashboard_grounding, dashboard_system_prompt
```

In `SendMessageView.post`, directly after the closed-session check (the end of `if conversation_id:`), add:

```python
        # A send from the Dashboard says where it was asked; the server
        # recomputes what is there (services/copilot/dashboard_grounding.py).
        # Absent or null, this is the Communications/Copilot send, unchanged.
        dashboard = None
        raw_context = request.data.get("context")
        if raw_context is not None:
            checked = DashboardContextSerializer(data=raw_context, context={"user": request.user})
            if not checked.is_valid():
                return Response({"context": checked.errors}, status=status.HTTP_400_BAD_REQUEST)
            dashboard = checked.validated_data
```

Replace the two lines

```python
        grounding = build_grounding(organisation, user=request.user, query=content)
        book_summary = grounding.summary
```

with

```python
        if dashboard is not None:
            grounding = build_dashboard_grounding(request.user, dashboard, content)
        else:
            grounding = build_grounding(organisation, user=request.user, query=content)
```

Replace

```text
        system = (
            f"{SYSTEM_PERSONA}\n\n{tone_instruction}\n\nReal-data summary:\n{book_summary}"
            f"{routing_note}"
        )
```

with

```python
        if dashboard is not None:
            system = dashboard_system_prompt(tone_instruction, grounding.summary) + routing_note
        else:
            system = (
                f"{SYSTEM_PERSONA}\n\n{tone_instruction}\n\nReal-data summary:\n"
                f"{grounding.summary}{routing_note}"
            )
```

In the `get_completion(...)` call, change `purpose="copilot",` to:

```text
                purpose="dashboard" if dashboard is not None else "copilot",
```

Change the user-turn create to carry the context:

```python
        user_message = Message.objects.create(
            conversation=conversation,
            role=Message.Role.USER,
            content=content,
            author=request.user,
            context=dashboard,
        )
```

Replace `conversation.save(update_fields=["updated_at"])` with:

```python
        # Set once, from the first dashboard message, and never overwritten:
        # the history's tag says where a conversation started.
        if dashboard is not None and conversation.origin is None:
            conversation.origin = origin_of(dashboard)
            conversation.save(update_fields=["origin", "updated_at"])
        else:
            conversation.save(update_fields=["updated_at"])
```

Add one paragraph to the end of `SendMessageView`'s docstring:

```
    Dashboard: an optional `context` ({surface, area, view, filters, focus})
    says where on the Dashboard the question was asked. It is validated by
    DashboardContextSerializer (a 400 `{"context": {...}}` otherwise), the
    answer is grounded by dashboard_grounding in that screen's recomputed
    figures and records, metered as `dashboard`, and the validated context is
    stored on the user turn; the conversation's `origin` is set from the first
    one and never changed.
```

If importing `dashboard_grounding` at module level raises a circular import at startup (`venv/bin/python manage.py check`), move both new imports into `post` beside their first use.

- [ ] **Step 4: Run the tests**

Run: `venv/bin/python manage.py test services.copilot services.attention --noinput`
Expected: PASS.

Run: `venv/bin/python manage.py check && venv/bin/ruff format . && venv/bin/ruff check .` — clean.

- [ ] **Step 5: Commit**

```bash
git add services/copilot/views.py services/copilot/tests/test_dashboard_send.py services/copilot/tests/test_views.py
git commit -m "feat(copilot): answer questions asked on the dashboard from the screen

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Docs

**Files:**
- Modify: `docs/API_CONTRACTS.md` (the `copilot` section: `GET /copilot/conversations/`, `GET/DELETE /copilot/conversations/<id>/`, `POST /copilot/messages/`)
- Modify: `docs/data-classification.md` (model table)
- Modify: `docs/product/01-prd.md` (feature table, release history)
- Modify: `docs/product/05-backend-schema.md` (`## 6. copilot`)

**Interfaces:**
- Consumes: the behaviour built in Tasks 1–8. Nothing downstream depends on these files.

- [ ] **Step 1: API_CONTRACTS**

Read `.claude/skills/api-contracts/SKILL.md` first and follow its format.

In `### GET /api/v1/copilot/conversations/`, change the example to:

```json
[
  { "id": 5, "title": "What's my churn risk?", "origin": null, "created_at": "2026-09-05T10:00:00Z", "updated_at": "2026-09-05T10:01:00Z" },
  { "id": 7, "title": "Why is at-risk ARR up?", "origin": { "surface": "dashboard", "area": "revenue", "view": "forecast", "filters": { "owner": "2", "lifecycle": "", "customer": "" } }, "created_at": "2026-09-24T10:00:00Z", "updated_at": "2026-09-24T10:01:00Z" }
]
```

and add below it: "`origin` is where the conversation started on the Dashboard — the first dashboard message's `context` without its `focus` — or `null`. Set once, never changed. The history shows it as a tag."

In `### GET/DELETE /api/v1/copilot/conversations/<id>/`, change the 200 line to: "adds `origin`, and nested `messages` (each `{id, role, content, author, sources, questions, ask_suggestions, context, created_at}`; `context` is the dashboard context a user turn was asked on, else `null`)."

Replace the body of `### POST /api/v1/copilot/messages/` with:

````markdown
Auth: `IsAuthenticated`. Body: `{"conversation_id": <id>?, "content": "...", "context": {...}?}`.
Omit `conversation_id` to start a new Conversation (titled from this message); pass an existing
one (one the caller may read, `404` otherwise) to continue it. The real send — runs
synchronously, no task queue.

**`context` (optional) — asked from the Dashboard.** Absent or `null`, the endpoint behaves
exactly as before (Communications sends its `[About: …]` text prefix in `content` and no
`context`). Present, the client says *where* it is and the server computes *what* is there; the
client never sends figures.

```json
{
  "conversation_id": 7,
  "content": "Why is at-risk ARR up?",
  "context": {
    "surface": "dashboard",
    "area": "revenue",
    "view": "forecast",
    "filters": {"owner": "2", "lifecycle": "customer", "customer": ""},
    "focus": null
  }
}
```

- `surface`: `"dashboard"` only.
- `area`: `overview | revenue | health | support`.
- `view`: one of the area's sub-views (`DASHBOARD_VIEWS` in `services/copilot/dashboard_context.py`, mirroring the frontend's `src/pages/dashboard/areas.ts`): revenue `forecast|customers|products`; health `triage|divergence|movement|renewals|usage|activity|distribution`; support `tickets|topics`. The Overview takes `null`.
- `filters`: only `owner`, `lifecycle`, `customer`; other keys are dropped, and a value that is not text or a whole number is ignored.
- `focus`: `null`; `{"kind": "companies", "ids": [..]}` (at most 200; intersected with the caller's filtered, visible book, and ids outside it dropped silently); or `{"kind": "attention", "key": "risk:12"}` (must be on the caller's own attention list — the snooze check).

The answer is grounded in that area's figures, recomputed for the caller and the filters by the
same code as the area's endpoint (`/dashboard/attention/`, `/customers/forecast/`,
`/customers/health/`, `/tickets/stats/`; Support ignores `lifecycle`, as its screen does), plus
the records behind the focus company or companies, or the one the question names — each read
under its own rule. Money is in the organisation currency. Metered as the `dashboard` purpose.

`400` if `content` is blank or over 8000 characters, or `{"context": {<field>: [..]}}` for a
wrong `surface`, `area`, `view`, `focus.kind`, more than 200 `ids`, or an attention key not on
the caller's list (`{"context": {"focus": {"key": ["Not an item on your list."]}}}` — the same
for a malformed key and someone else's). `403` if `Organisation.ai_agent_enabled` is `false`.
`429` if the organisation's monthly budget for the purpose (`copilot`, or `dashboard` with a
`context`) is spent. `503` if the selected provider's credentials aren't configured. `502` if
the API call itself fails.

**Response `200`** — the (possibly newly created) Conversation, same nested shape as the detail
endpoint's GET, including this turn's user message (with `context` echoed, after the focus
intersection) and the model's reply. `origin` is set from the first dashboard message and never
overwritten.
````

- [ ] **Step 2: data-classification**

In the model table of `docs/data-classification.md`, directly after the `copilot.Conversation`, `copilot.Message` row, add:

```markdown
| `copilot.Message.context`, `copilot.Conversation.origin` | internal | — | where on the Dashboard a question was asked: area, view, filter values and focus ids or an attention key. Ids and filter values only, never record text. `origin` is set once from the first dashboard message, without its focus |
```

- [ ] **Step 3: PRD**

In `docs/product/01-prd.md`, add a row after "Dashboard Overview — "Needs attention"":

```markdown
| Ask Revenact on the Dashboard | Built (backend) | Questions asked on the Dashboard carry where they were asked; the server recomputes that area's figures for the asker's own filtered book with the same code as the screen, adds the records behind the companies asked about under their own rules, and answers only from those. Metered as its own purpose; one history across Communications, Copilot and Dashboard, tagged with where each conversation started |
```

Add to `## 9. Release history`:

```markdown
| 2026-09-24 | Dashboard attention list; Ask Revenact on the Dashboard (backend) |
```

(If a 2026-09-24 row already exists by the time this runs, append "; Ask Revenact on the Dashboard (backend)" to it instead.)

- [ ] **Step 4: Backend schema**

In `docs/product/05-backend-schema.md`, `## 6. copilot`, change the two rows to:

```markdown
| `Conversation` | One chat thread, owned by a user within an organisation. `origin`: where it started on the Dashboard (the first dashboard message's context without its focus), set once; null otherwise |
| `Message` | One turn: `role`, `content`, `author`, `sources` (citation snapshots), `ask_suggestions`, `context` (user turns asked on the Dashboard: area, view, filters and focus ids — never record text) |
```

- [ ] **Step 5: Format and check**

Run: `venv/bin/ruff format docs && venv/bin/ruff format --check . && venv/bin/ruff check .`
Expected: clean (ruff formats Python inside Markdown fences; the blocks above are JSON and Markdown).

- [ ] **Step 6: Commit**

```bash
git add docs/API_CONTRACTS.md docs/data-classification.md docs/product/01-prd.md docs/product/05-backend-schema.md
git commit -m "docs(copilot): the dashboard context, its storage and its classification

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: End-to-end test and full verification

**Files:**
- Create: `e2e/test_dashboard_ask_flow.py` (template: `e2e/test_attention_flow.py`)

**Interfaces:**
- Consumes: the whole feature over real HTTP; `e2e.http.http_get`, `http_post`.

- [ ] **Step 1: Write the e2e test**

```python
"""End-to-end tier: real server, real HTTP, real test DB. A CSM asks on the
Dashboard Overview, follows up after changing the filter, asks about a drill's
companies (one of them someone else's), presses "Why?" on an attention row, is
refused a key that is not theirs, and finds the conversation in history
tagged with where it started. The model call is stubbed in-process."""

from datetime import timedelta
from unittest.mock import patch

from django.test import LiveServerTestCase
from django.utils import timezone

from e2e.http import http_get, http_post

FILTERS = {"owner": "", "lifecycle": "", "customer": ""}


def dashboard(area="overview", view=None, focus=None, **filters):
    return {
        "surface": "dashboard",
        "area": area,
        "view": view,
        "filters": {**FILTERS, **filters},
        "focus": focus,
    }


class DashboardAskFlowTests(LiveServerTestCase):
    def api(self, path):
        return f"{self.live_server_url}/api/v1{path}"

    def test_full_flow(self):
        # 1. Organisation signs up (creates org + admin, logs the admin in).
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
        admin_access = body["access"]

        # 2. Admin adds a CSM, and creates a customer of their own (not the CSM's).
        status, body = http_post(
            self.api("/auth/users/"),
            {"name": "Carl CSM", "email": "carl@acme.io", "password": "csmpassword1"},
            token=admin_access,
        )
        self.assertEqual(status, 201, body)
        status, body = http_post(
            self.api("/customers/"),
            {"name": "Initech", "health_score": "2.0", "arr_billed_at_account": "70000"},
            token=admin_access,
        )
        self.assertEqual(status, 201, body)
        foreign_id = body["id"]

        # 3. The CSM logs in and creates a renewal-due, Poor customer.
        status, body = http_post(
            self.api("/auth/login/"), {"email": "carl@acme.io", "password": "csmpassword1"}
        )
        self.assertEqual(status, 200, body)
        csm = body["access"]
        status, body = http_post(
            self.api("/customers/"),
            {
                "name": "Globex Corp",
                "health_score": "2.0",
                "arr_billed_at_account": "50000",
                "renewal_date": str(timezone.localdate() + timedelta(days=10)),
            },
            token=csm,
        )
        self.assertEqual(status, 201, body)
        customer_id = body["id"]

        with patch(
            "services.copilot.views.get_completion", return_value="Globex renews soon."
        ) as completion:
            # 4. Ask on the Overview.
            status, body = http_post(
                self.api("/copilot/messages/"),
                {"content": "What needs me first?", "context": dashboard()},
                token=csm,
            )
            self.assertEqual(status, 200, body)
            conversation_id = body["id"]
            self.assertEqual(body["origin"], {k: v for k, v in dashboard().items() if k != "focus"})
            self.assertEqual(body["messages"][0]["context"], dashboard())
            self.assertEqual(completion.call_args.kwargs["purpose"], "dashboard")
            self.assertIn("Screen: Overview", completion.call_args.kwargs["system"])
            self.assertIn("Globex Corp", completion.call_args.kwargs["system"])
            self.assertNotIn("Initech", completion.call_args.kwargs["system"])

            # 5. Change a filter and ask a follow-up: the new screen, the old origin.
            follow_up = dashboard("revenue", "forecast", customer=str(customer_id))
            status, body = http_post(
                self.api("/copilot/messages/"),
                {
                    "conversation_id": conversation_id,
                    "content": "And revenue?",
                    "context": follow_up,
                },
                token=csm,
            )
            self.assertEqual(status, 200, body)
            self.assertEqual(body["messages"][2]["context"], follow_up)
            self.assertEqual(body["origin"]["area"], "overview")
            self.assertIn("Screen: Revenue › Forecast", completion.call_args.kwargs["system"])

            # 6. "Ask about these" from a drill: someone else's id is dropped silently.
            status, body = http_post(
                self.api("/copilot/messages/"),
                {
                    "conversation_id": conversation_id,
                    "content": "Why are these in At risk?",
                    "context": dashboard(
                        focus={"kind": "companies", "ids": [customer_id, foreign_id]}
                    ),
                },
                token=csm,
            )
            self.assertEqual(status, 200, body)
            self.assertEqual(
                body["messages"][4]["context"]["focus"],
                {"kind": "companies", "ids": [customer_id]},
            )
            self.assertNotIn("Initech", completion.call_args.kwargs["system"])

            # 7. "Why?" on an attention row.
            status, body = http_get(self.api("/dashboard/attention/"), token=csm)
            self.assertEqual(status, 200, body)
            key = f"renewal:{customer_id}"
            self.assertIn(key, {item["key"] for item in body["items"]})
            status, body = http_post(
                self.api("/copilot/messages/"),
                {
                    "content": "Why is this on my list?",
                    "context": dashboard(focus={"kind": "attention", "key": key}),
                },
                token=csm,
            )
            self.assertEqual(status, 200, body)

            # 8. A key that is not on the CSM's list is refused, generically.
            status, body = http_post(
                self.api("/copilot/messages/"),
                {
                    "content": "Why?",
                    "context": dashboard(
                        focus={"kind": "attention", "key": f"renewal:{foreign_id}"}
                    ),
                },
                token=csm,
            )
            self.assertEqual(status, 400, body)
            self.assertEqual(body, {"context": {"focus": {"key": ["Not an item on your list."]}}})

        # 9. History shows where the first conversation started.
        status, body = http_get(self.api("/copilot/conversations/"), token=csm)
        self.assertEqual(status, 200, body)
        by_id = {row["id"]: row for row in body}
        self.assertEqual(by_id[conversation_id]["origin"]["area"], "overview")
```

Note on step 2: a new customer is owned by whoever creates it (as `e2e/test_attention_flow.py` relies on), so Initech is the admin's and outside Carl's book (`visible_customers` shows a CSM their own, their reports', and unowned customers).

- [ ] **Step 2: Run the e2e test**

Run: `venv/bin/python manage.py test e2e.test_dashboard_ask_flow --noinput`
Expected: PASS.

- [ ] **Step 3: Full verification**

Run each and read the output before claiming success (`.claude/skills/verification-before-completion`):

```bash
venv/bin/ruff format --check .
venv/bin/ruff check .
venv/bin/python manage.py makemigrations --check --dry-run
venv/bin/python manage.py test --noinput
```

Expected: ruff "N files already formatted" and "All checks passed!"; makemigrations "No changes detected"; the full suite OK with zero failures and zero errors.

- [ ] **Step 4: Commit**

```bash
git add e2e/test_dashboard_ask_flow.py
git commit -m "test(e2e): ask Revenact on the dashboard, end to end

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Self-review

**Spec coverage** (spec section → task):
- §1 contract, `surface`/`area`/`view`/`filters`/`focus`, error rules, silent intersection, same error for foreign and malformed keys → Task 3 (serializer), Task 8 (400 shape, no trace).
- §1 response echoes `context` and `origin` → Tasks 2 and 8.
- §2 `build_dashboard_grounding(user, context, question) -> Grounding`, first filter → Task 6.
- §2 per-area digests equal to endpoints → Tasks 4 (Revenue, Health), 5 (Support, Overview), each with an equality test.
- §2 money converted and labelled → Tasks 4–6 (`currency` in figures; `_money` labels every amount).
- §2 records via existing retrieval, `visible_tickets`, `visible_emails`, `visible_evidence`, anomaly title rule → Task 6 (pre-flight 5 for facts and contacts).
- §2 prompt → Task 6 (`dashboard_system_prompt`), asserted in Tasks 6 and 8.
- §2 metering, 429 → Task 7 (purpose), Task 8 (stubbed and real budget tests).
- §3 `Message.context`, `Conversation.origin`, migration, set once, classification → Tasks 2, 8, 9.
- §5 backend tests: digest equals endpoints (4, 5); another CSM's customer never appears, including as focus (3, 4, 6, 8, 10); department ticket never cited (5, 6); anomaly titles (6); foreign attention key → generic 400 (3, 8, 10); validation (3, 8); origin once (8); 429 (8); Communications unchanged (8); stubbed model with prompt assertions (8, 10).
- §6 backend PR contents including docs → Task 9.

**Placeholder scan:** no TBD/TODO; every code step carries its code, and every field name used in a fixture was checked against the models while writing this plan.

**Type consistency:** `clean_filters` → `dict[str, str]` used by every figures function and test; `revenue_figures(user, filters)`, `health_figures(user, filters, *, today)`, `support_figures(user, filters, *, today)`, `attention_top(user, filters, *, today, now)`, `overview_figures(user, filters, *, today, now)` match their callers in Task 6; `current_item(user, key, *, today=None)` matches Tasks 3 and 6; `build_dashboard_grounding(user, context, question, *, today=None, now=None)` and `dashboard_system_prompt(tone_instruction, summary)` match Task 8; `origin_of(context)` matches Task 8.
