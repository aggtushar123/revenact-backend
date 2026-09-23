# Dashboard drill-down, backend (PR 2a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Five dashboard stats endpoints accept `?drill=<segment>` and return the complete list of companies behind one number, scoped exactly like the totals; `/api/v1/customers/` accepts `?ids=` so the frontend can open a drill list as the Organizations list.

**Architecture:** One helper module, `services/customers/drill.py`, parses the segment and turns "a value per customer" into the response, always intersecting with `visible_customers(user)`. Each endpoint computes its per-customer values from the same filtered set its totals already use, then hands them to the helper. No new model, no migration, no model call.

**Tech Stack:** Django 5, DRF, PostgreSQL, `APITestCase`.

**Spec:** `react-ts-app/docs/superpowers/specs/2026-09-23-dashboard-redesign-design.md` §3 "Drill-down" (as amended 2026-09-24: complete lists only; server drill for Tickets, Topics, Forecast, Activity, Customers).

## Global Constraints

- The drill list is computed from **the same filtered queryset or customer list the endpoint's totals use**, then intersected with `visible_customers(request.user)`. A company the totals did not count never appears; a company the viewer may not see never appears (an account-level record belongs to all of the account's customers, some of which may be outside the viewer's book).
- House convention: a bad filter value is ignored, never a 400. An unknown or malformed `drill` is ignored and the endpoint returns its normal stats.
- Response when a drill applies: `{"drill": {"segment": str, "value_label": str, "count": int, "truncated": bool, "companies": [{"id": int, "name": str, "owner": str, "arr": float | null, "value": number | null}]}, "currency": str}` — nothing else. `count` is the full number of companies; `companies` holds at most `LIMIT = 500`.
- `arr` is `arr_billed_at_account` converted with `convert_to_org_currency(..., rates=rates_for(organisation))`; `None` when no rate.
- Read-only: no audit event, no data-classification change.
- Every endpoint shape change updates `docs/API_CONTRACTS.md` in the same PR (`.claude/skills/api-contracts`).
- `ruff format --check .` and `ruff check .` pass (ruff also formats Python fences inside Markdown).
- Commits: `type(scope): subject`, ending `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Run tests with `venv/bin/python manage.py test <label> --noinput`.

## File Structure

| File | Change |
|---|---|
| `services/customers/drill.py` | New: `LIMIT`, `parse_segment`, `record_counts`, `companies_payload` |
| `services/customers/tests/test_drill.py` | New: helper unit tests |
| `services/customers/views.py` | `TicketStatsView`, `InteractionStatsView`, `CustomerForecastView`, `ActivityTrackingView`, `CustomerOverviewView`, `CustomerListCreateView` |
| `services/customers/activity_tracking.py` | Extract `dark_accounts(...)` used by `build_stats` and the drill |
| `services/customers/portfolio.py` | Extract `churned_last_year(...)` (and fix the 29 Feb `replace`) |
| Tests | `test_views.py` (`TicketStatsTests`, customer list), `test_interactions.py`, `test_forecast.py`, `test_activity_tracking.py`, `test_portfolio.py` |
| `docs/API_CONTRACTS.md` | New "Dashboard drill" section + `ids` on the customers list |

---

### Task 1: The drill helper

**Files:**
- Create: `services/customers/drill.py`
- Test: `services/customers/tests/test_drill.py`

**Interfaces:**
- Produces:
  - `LIMIT = 500`
  - `parse_segment(params, kinds: dict[str, bool]) -> tuple[str, str] | None` — `kinds` maps a segment kind to whether it needs a value (`"priority": True`, `"all": False`). Returns `(kind, value)`; `None` when `drill` is absent, the kind is unknown, a value is required but empty, or a value is given to a kind that takes none.
  - `record_counts(querysets) -> collections.Counter[int]` — records per customer id across the given querysets; a record on an account counts once for each of the account's customers.
  - `companies_payload(user, segment: str, values: dict[int, float | int | None], *, value_label: str, none_first: bool = False) -> dict` — the response body described in Global Constraints, sorted by `value` descending (then name), `None` values last unless `none_first`.

- [ ] **Step 1: Write the failing tests**

```python
# services/customers/tests/test_drill.py
from django.test import TestCase
from django.utils import timezone

from services.accounts.models import Organisation, User
from services.customers.drill import LIMIT, companies_payload, parse_segment, record_counts
from services.customers.models import Account, Customer, Ticket

KINDS = {"all": False, "priority": True}


class ParseSegmentTests(TestCase):
    def test_absent_unknown_and_malformed_are_ignored(self):
        self.assertIsNone(parse_segment({}, KINDS))
        self.assertIsNone(parse_segment({"drill": "nope:1"}, KINDS))
        self.assertIsNone(parse_segment({"drill": "priority:"}, KINDS))
        self.assertIsNone(parse_segment({"drill": "all:extra"}, KINDS))

    def test_kinds_with_and_without_a_value(self):
        self.assertEqual(parse_segment({"drill": "all"}, KINDS), ("all", ""))
        self.assertEqual(parse_segment({"drill": "priority:high"}, KINDS), ("priority", "high"))

    def test_the_value_may_contain_colons(self):
        self.assertEqual(parse_segment({"drill": "priority:a:b"}, KINDS), ("priority", "a:b"))


class Fixture(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc", currency="USD")
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
        self.mine = Customer.objects.create(
            organisation=self.org,
            name="Mine",
            owner=self.csm,
            arr_billed_at_account=100_000,
            currency="USD",
        )
        self.also_mine = Customer.objects.create(
            organisation=self.org,
            name="Also mine",
            owner=self.csm,
            arr_billed_at_account=50_000,
            currency="USD",
        )
        self.theirs = Customer.objects.create(
            organisation=self.org, name="Theirs", owner=self.other
        )

    def _ticket(self, n, **overrides):
        return Ticket.objects.create(
            **{
                "customer": self.mine,
                "ticket_number": f"TKT-{n}",
                "title": "x",
                "status": Ticket.Status.OPEN,
                "priority": Ticket.Priority.HIGH,
                "opened_at": timezone.now(),
                **overrides,
            }
        )


class RecordCountsTests(Fixture):
    def test_counts_every_record_not_distinct_companies(self):
        self._ticket(1)
        self._ticket(2)
        self._ticket(3, customer=self.also_mine)
        self.assertEqual(
            record_counts([Ticket.objects.all()]), {self.mine.pk: 2, self.also_mine.pk: 1}
        )

    def test_an_account_record_counts_for_each_of_its_customers(self):
        account = Account.objects.create(organisation=self.org, name="Shared", owner=self.csm)
        account.customers.add(self.mine, self.also_mine)
        self._ticket(1, customer=None, account=account)
        self.assertEqual(
            record_counts([Ticket.objects.all()]), {self.mine.pk: 1, self.also_mine.pk: 1}
        )


class CompaniesPayloadTests(Fixture):
    def test_shape_order_and_visibility(self):
        body = companies_payload(
            self.csm,
            "priority:high",
            {self.mine.pk: 2, self.also_mine.pk: 5, self.theirs.pk: 9},
            value_label="tickets",
        )
        drill = body["drill"]
        self.assertEqual(body["currency"], "USD")
        self.assertEqual(drill["segment"], "priority:high")
        self.assertEqual(drill["value_label"], "tickets")
        # Theirs is outside Carl's book, so it is dropped even though a value came in.
        self.assertEqual([c["name"] for c in drill["companies"]], ["Also mine", "Mine"])
        self.assertEqual(drill["count"], 2)
        self.assertFalse(drill["truncated"])
        self.assertEqual(
            drill["companies"][1],
            {"id": self.mine.pk, "name": "Mine", "owner": "Carl", "arr": 100000.0, "value": 2},
        )

    def test_none_values_sort_last_or_first(self):
        values = {self.mine.pk: None, self.also_mine.pk: 3}
        last = companies_payload(self.csm, "x", values, value_label="days")
        first = companies_payload(self.csm, "x", values, value_label="days", none_first=True)
        self.assertEqual([c["name"] for c in last["drill"]["companies"]], ["Also mine", "Mine"])
        self.assertEqual([c["name"] for c in first["drill"]["companies"]], ["Mine", "Also mine"])

    def test_truncates_to_the_limit_but_counts_everything(self):
        extra = [
            Customer(organisation=self.org, name=f"C{i}", owner=self.csm) for i in range(LIMIT + 1)
        ]
        Customer.objects.bulk_create(extra)
        values = {c.pk: 1 for c in Customer.objects.filter(owner=self.csm)}
        drill = companies_payload(self.csm, "all", values, value_label="tickets")["drill"]
        self.assertEqual(drill["count"], len(values))
        self.assertTrue(drill["truncated"])
        self.assertEqual(len(drill["companies"]), LIMIT)
```

Before running, open `services/customers/tests/test_views.py` and match its imports for `Organisation`, `User` (they may live in a different app) and the `Account` / `Ticket` required fields; adjust the fixture to the real models without changing what each test asserts.

- [ ] **Step 2: Run to verify it fails**

Run: `venv/bin/python manage.py test services.customers.tests.test_drill --noinput`
Expected: FAIL, `ModuleNotFoundError: services.customers.drill`.

- [ ] **Step 3: Implement**

```python
# services/customers/drill.py
"""The companies behind one dashboard number.

A dashboard figure is only worth clicking if the list it opens is the
whole list. Each stats endpoint hands this module one value per customer,
computed from the same filtered set its totals use; this module turns that
into the response and does the second filter every record-derived list
here needs: an account-level record belongs to all of the account's
customers, and some of those may sit outside the viewer's book, so the
result is always intersected with ``visible_customers``.

A bad ``drill`` is ignored, like every other dashboard filter: the caller
gets the normal stats back rather than a 400.
"""

from collections import Counter

from services.fx_rates.conversion import convert_to_org_currency, rates_for

from .models import Account
from .scoping import visible_customers

LIMIT = 500


def parse_segment(params, kinds):
    raw = (params.get("drill") or "").strip()
    if not raw:
        return None
    kind, sep, value = raw.partition(":")
    if kind not in kinds:
        return None
    needs_value = kinds[kind]
    if needs_value and not value:
        return None
    if not needs_value and sep:
        return None
    return kind, value


def record_counts(querysets):
    """Records per customer id. Counted by primary key, not by distinct
    (customer, account) pair, so three tickets on one company count three."""

    counts = Counter()
    by_account = Counter()
    for queryset in querysets:
        for _pk, customer_id, account_id in queryset.order_by().values_list(
            "pk", "customer_id", "account_id"
        ):
            if customer_id:
                counts[customer_id] += 1
            elif account_id:
                by_account[account_id] += 1
    if by_account:
        links = Account.customers.through.objects.filter(account_id__in=by_account)
        for account_id, customer_id in links.values_list("account_id", "customer_id"):
            counts[customer_id] += by_account[account_id]
    return counts


def companies_payload(user, segment, values, *, value_label, none_first=False):
    organisation = user.organisation
    rates = rates_for(organisation)
    customers = visible_customers(user).filter(pk__in=list(values)).select_related("owner")

    rows = []
    for customer in customers:
        converted = convert_to_org_currency(
            customer.arr_billed_at_account, customer.currency, organisation, rates=rates
        )
        rows.append(
            {
                "id": customer.id,
                "name": customer.name,
                "owner": customer.owner.name if customer.owner else "Unassigned",
                "arr": None if converted is None else float(converted),
                "value": values[customer.pk],
            }
        )

    def order(row):
        missing = row["value"] is None
        return (not missing if none_first else missing, -(row["value"] or 0), row["name"])

    rows.sort(key=order)
    return {
        "drill": {
            "segment": segment,
            "value_label": value_label,
            "count": len(rows),
            "truncated": len(rows) > LIMIT,
            "companies": rows[:LIMIT],
        },
        "currency": organisation.currency,
    }
```

If `Account.customers.through` names its columns differently (check `Account.customers.through._meta.get_fields()`), use the real names; the test pins the behaviour.

- [ ] **Step 4: Run tests**

Run: `venv/bin/python manage.py test services.customers.tests.test_drill --noinput`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add services/customers/drill.py services/customers/tests/test_drill.py
git commit -m "feat(customers): the companies behind one dashboard number"
```

---

### Task 2: Tickets drill

**Files:**
- Modify: `services/customers/views.py` (`TicketStatsView`)
- Test: `services/customers/tests/test_views.py` (`TicketStatsTests`)

**Interfaces:**
- Consumes: `parse_segment`, `record_counts`, `companies_payload` (Task 1).
- Segments (value label `"tickets"`): `all`; `on_hold`; `sentiment:<Ticket.Sentiment value>`; `priority:<Ticket.Priority value>`; `status:<Ticket.Status value>`; `origin:<connector id>` or `origin:none` (no connector, shown as "Revenact"); `assignee:<assignee_name>` (exact match). An unknown choice value ignores the drill.

- [ ] **Step 1: Write the failing tests** (add to `TicketStatsTests`)

```python
# ── drill ────────────────────────────────────────────────────────


def test_drill_lists_companies_with_their_ticket_counts(self):
    also = Customer.objects.create(organisation=self.org, name="Also mine", owner=self.csm)
    self._ticket(1)
    self._ticket(2)
    self._ticket(3, customer=also)
    self._ticket(4, priority=Ticket.Priority.LOW)
    body = self.client.get(self.url, {"drill": "priority:high"}).json()
    self.assertEqual(set(body), {"drill", "currency"})
    companies = body["drill"]["companies"]
    self.assertEqual([(c["name"], c["value"]) for c in companies], [("Mine", 2), ("Also mine", 1)])
    self.assertEqual(body["drill"]["value_label"], "tickets")


def test_drill_respects_the_other_filters_and_the_viewers_book(self):
    self._ticket(1)
    self._ticket(2, customer=self.theirs)
    body = self.client.get(self.url, {"drill": "all", "from": "2026-02-01"}).json()
    self.assertEqual([c["name"] for c in body["drill"]["companies"]], ["Mine"])
    body = self.client.get(self.url, {"drill": "all", "from": "2026-04-01"}).json()
    self.assertEqual(body["drill"]["companies"], [])


def test_each_segment_kind(self):
    self._ticket(
        1,
        status=Ticket.Status.ON_HOLD,
        sentiment=Ticket.Sentiment.NEGATIVE,
        connector=self.zendesk,
        assignee_name="Ada",
    )
    self._ticket(2)
    for drill, expected in [
        ("on_hold", 1),
        ("sentiment:negative", 1),
        ("status:on-hold", 1),
        (f"origin:{self.zendesk.pk}", 1),
        ("origin:none", 1),
        ("assignee:Ada", 1),
    ]:
        with self.subTest(drill=drill):
            companies = self.client.get(self.url, {"drill": drill}).json()["drill"]["companies"]
            self.assertEqual([c["value"] for c in companies], [expected])


def test_a_bad_drill_returns_the_normal_stats(self):
    self._ticket(1)
    for drill in ["priority:nope", "bogus", "all:x", "origin:abc"]:
        with self.subTest(drill=drill):
            body = self.client.get(self.url, {"drill": drill}).json()
            self.assertIn("kpis", body)
            self.assertNotIn("drill", body)
```

Check the real `Ticket.Status` / `Ticket.Sentiment` values in `services/customers/models.py` (~L1530) and use them in the drill strings.

- [ ] **Step 2: Run to verify they fail**

Run: `venv/bin/python manage.py test services.customers.tests.test_views.TicketStatsTests --noinput`
Expected: the four new tests FAIL (no `drill` key).

- [ ] **Step 3: Implement** in `TicketStatsView`:

```python
DRILL_KINDS = {
    "all": False,
    "on_hold": False,
    "sentiment": True,
    "priority": True,
    "status": True,
    "origin": True,
    "assignee": True,
}


def _drill_filter(self, kind, value):
    """A Q for one segment, or None when the value is not a real choice."""
    if kind == "all":
        return Q()
    if kind == "on_hold":
        return Q(status=Ticket.Status.ON_HOLD)
    if kind == "sentiment":
        return Q(sentiment=value) if value in Ticket.Sentiment.values else None
    if kind == "priority":
        return Q(priority=value) if value in Ticket.Priority.values else None
    if kind == "status":
        return Q(status=value) if value in Ticket.Status.values else None
    if kind == "origin":
        if value == "none":
            return Q(connector__isnull=True)
        connector_id = _parse_int(value)
        return Q(connector_id=connector_id) if connector_id is not None else None
    if kind == "assignee":
        return Q(assignee_name=value)
    return None
```

At the start of `get`, after `tickets = self._filtered_tickets(request)`:

```python
        segment = drill.parse_segment(request.query_params, self.DRILL_KINDS)
        if segment is not None:
            condition = self._drill_filter(*segment)
            if condition is not None:
                counts = drill.record_counts([tickets.filter(condition)])
                return Response(
                    drill.companies_payload(
                        request.user,
                        request.query_params["drill"].strip(),
                        dict(counts),
                        value_label="tickets",
                    )
                )
```

with `from . import drill` added to the module imports (check it does not clash with a local name; if it does, `from . import drill as drill_list`). Add the segments to the class docstring under a short "Drill" paragraph.

- [ ] **Step 4: Run tests**

Run: `venv/bin/python manage.py test services.customers.tests.test_views.TicketStatsTests services.connectors.tests.test_tickets --noinput`
Expected: PASS (the connectors file covers department scoping of the same queryset).

- [ ] **Step 5: Commit**

```bash
git add services/customers/views.py services/customers/tests/test_views.py
git commit -m "feat(tickets): drill from any ticket chart to the companies behind it"
```

---

### Task 3: Topics (interactions) drill

**Files:**
- Modify: `services/customers/views.py` (`InteractionStatsView`), `services/customers/interactions.py` (a small `drill_querysets` helper)
- Test: `services/customers/tests/test_interactions.py` (`InteractionStatsTests`)

**Interfaces:**
- Segments (value label `"interactions"`): `all`; `type:<email|call|ticket>` (keys of `interactions.SOURCES`); `sentiment:<taxonomy.Sentiment value>`; `area:<ai_area>`; `category:<ai_category>`; `subcategory:<ai_subcategory>`.
- Produces in `interactions.py`: `drill_querysets(querysets: dict[str, QuerySet], kind: str, value: str) -> list[QuerySet] | None` — the querysets narrowed to the segment, or `None` for an invalid type/sentiment value.

- [ ] **Step 1: Write the failing tests** (add to `InteractionStatsTests`, using its `_email` / `_call` / `_ticket` / `_classified` helpers)

```python
# ── drill ────────────────────────────────────────────────────────


def test_drill_counts_interactions_per_company(self):
    self._email(1)
    self._email(2)
    self._call(1)
    body = self.client.get(self.url, {"drill": "all"}).json()
    self.assertEqual(set(body), {"drill", "currency"})
    self.assertEqual([(c["name"], c["value"]) for c in body["drill"]["companies"]], [("Mine", 3)])
    self.assertEqual(body["drill"]["value_label"], "interactions")


def test_drill_by_type_and_by_taxonomy(self):
    self._email(1)
    call = self._call(1)
    self._classified(call, area="customer_success", category="onboarding", subcategory="kickoff")
    cases = {
        "type:email": 1,
        "type:call": 1,
        "area:customer_success": 1,
        "category:onboarding": 1,
        "subcategory:kickoff": 1,
    }
    for drill, expected in cases.items():
        with self.subTest(drill=drill):
            companies = self.client.get(self.url, {"drill": drill}).json()["drill"]["companies"]
            self.assertEqual([c["value"] for c in companies], [expected])


def test_drill_keeps_personal_mail_rules(self):
    """The same visible_emails rule the totals use: another person's
    mailbox mail is never counted towards a company."""
    self._email(1, mailbox_owner=self.other)
    companies = self.client.get(self.url, {"drill": "type:email"}).json()["drill"]["companies"]
    self.assertEqual(companies, [])


def test_a_bad_drill_returns_the_normal_stats(self):
    self._email(1)
    for drill in ["type:fax", "sentiment:furious", "nope"]:
        with self.subTest(drill=drill):
            self.assertNotIn("drill", self.client.get(self.url, {"drill": drill}).json())
```

Use the real taxonomy keys the `_classified` helper accepts (see `services/customers/taxonomy.py`) in place of the example strings, and match the mailbox-owner field the existing personal-mail tests use; if `self.other` owning the mailbox does not hide it from `self.csm` in this fixture (e.g. manager chain), pick the fixture the existing visibility test uses.

- [ ] **Step 2: Run to verify they fail**

Run: `venv/bin/python manage.py test services.customers.tests.test_interactions --noinput`
Expected: new tests FAIL.

- [ ] **Step 3: Implement**

In `interactions.py`:

```python
DRILL_KINDS = {
    "all": False,
    "type": True,
    "sentiment": True,
    "area": True,
    "category": True,
    "subcategory": True,
}

_TAXONOMY_FIELD = {"area": "ai_area", "category": "ai_category", "subcategory": "ai_subcategory"}


def drill_querysets(querysets, kind, value):
    if kind == "all":
        return list(querysets.values())
    if kind == "type":
        return [querysets[value]] if value in querysets else None
    if kind == "sentiment":
        if value not in taxonomy.Sentiment.values:
            return None
        return [qs.filter(sentiment=value) for qs in querysets.values()]
    field = _TAXONOMY_FIELD[kind]
    return [qs.filter(**{field: value}) for qs in querysets.values()]
```

(Use whatever `taxonomy.Sentiment` exposes for valid values — `.values` if it is a Django `TextChoices`, else the module's own list — matching how `filtered_querysets` validates `sentiment`.)

In `InteractionStatsView.get`, before building stats:

```python
        querysets = interactions.filtered_querysets(request.user, request.query_params)
        segment = drill.parse_segment(request.query_params, interactions.DRILL_KINDS)
        if segment is not None:
            narrowed = interactions.drill_querysets(querysets, *segment)
            if narrowed is not None:
                return Response(
                    drill.companies_payload(
                        request.user,
                        request.query_params["drill"].strip(),
                        dict(drill.record_counts(narrowed)),
                        value_label="interactions",
                    )
                )
```

- [ ] **Step 4: Run tests**

Run: `venv/bin/python manage.py test services.customers.tests.test_interactions --noinput`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/customers/interactions.py services/customers/views.py services/customers/tests/test_interactions.py
git commit -m "feat(interactions): drill from any topic chart to the companies behind it"
```

---

### Task 4: Forecast drill

**Files:**
- Modify: `services/customers/views.py` (`CustomerForecastView`)
- Test: `services/customers/tests/test_forecast.py` (`ForecastViewTests`)

**Interfaces:**
- Segments: `at_risk` (value `downside`, rows with `downside > 0`), `churn` (`downside > 0` and `churn_exposure >= risk_exposure`), `contraction` (`downside > 0` and `churn_exposure < risk_exposure`), `expansion` (value `expansion`, rows with `expansion > 0`). Value labels: `"downside"` for the first three, `"expected expansion"` for the last. These predicates match `forecast.build_bridge` exactly, so the drill values sum to the bridge step.

- [ ] **Step 1: Write the failing tests** (add to `ForecastViewTests`, using its `_customer(name, arr, renews_in_days=90, health="9.0", **overrides)` helper)

```python
def test_drill_values_sum_to_the_bridge_step(self):
    self._customer("Risky", 100_000, renews_in_days=30, health="2.0")
    self._customer("Safe", 50_000, renews_in_days=400)
    stats = self.client.get(self.url).json()
    bridge = stats["bridge"]
    for segment, step in [("churn", "churn"), ("contraction", "contraction")]:
        with self.subTest(segment=segment):
            drill = self.client.get(self.url, {"drill": segment}).json()["drill"]
            self.assertAlmostEqual(
                sum(c["value"] for c in drill["companies"]), bridge[step], places=2
            )
    at_risk = self.client.get(self.url, {"drill": "at_risk"}).json()["drill"]
    self.assertAlmostEqual(
        sum(c["value"] for c in at_risk["companies"]),
        bridge["churn"] + bridge["contraction"],
        places=2,
    )
    self.assertEqual(at_risk["value_label"], "downside")


def test_drill_is_the_whole_list_not_the_top_fifteen(self):
    for i in range(20):
        self._customer(f"Risky {i}", 10_000 + i, renews_in_days=30, health="2.0")
    drill = self.client.get(self.url, {"drill": "at_risk"}).json()["drill"]
    self.assertEqual(drill["count"], 20)


def test_drill_respects_the_horizon_and_filters(self):
    self._customer("Later", 100_000, renews_in_days=200, health="2.0")
    short = self.client.get(self.url, {"drill": "churn", "horizon_days": 90}).json()["drill"]
    long = self.client.get(self.url, {"drill": "churn", "horizon_days": 365}).json()["drill"]
    self.assertEqual(short["companies"], [])
    self.assertEqual([c["name"] for c in long["companies"]], ["Later"])
```

Read `build_bridge` and the bridge keys the view returns (`churn`, `contraction`, `expansion` — confirm the exact key names and whether they are negative numbers) and adjust the sums' signs to match; if churn is returned negative, compare against `abs(bridge["churn"])`.

- [ ] **Step 2: Run to verify they fail**

Run: `venv/bin/python manage.py test services.customers.tests.test_forecast --noinput`
Expected: new tests FAIL.

- [ ] **Step 3: Implement** in `CustomerForecastView.get`, after `rows = forecast.build_rows(...)`:

```python
segment = drill.parse_segment(
    request.query_params,
    {"at_risk": False, "churn": False, "contraction": False, "expansion": False},
)
if segment is not None:
    kind = segment[0]
    if kind == "expansion":
        values = {row.customer.pk: row.expansion for row in rows if row.expansion > 0}
        label = "expected expansion"
    else:
        picked = [row for row in rows if row.downside > 0]
        if kind == "churn":
            picked = [r for r in picked if r.churn_exposure >= r.risk_exposure]
        elif kind == "contraction":
            picked = [r for r in picked if r.churn_exposure < r.risk_exposure]
        values = {row.customer.pk: row.downside for row in picked}
        label = "downside"
    return Response(
        drill.companies_payload(
            request.user,
            kind,
            {k: round(float(v), 2) for k, v in values.items()},
            value_label=label,
        )
    )
```

- [ ] **Step 4: Run tests**

Run: `venv/bin/python manage.py test services.customers.tests.test_forecast services.metrics --noinput`
Expected: PASS (metrics exercises `forecast.bridge_by`; nothing there changes).

- [ ] **Step 5: Commit**

```bash
git add services/customers/views.py services/customers/tests/test_forecast.py
git commit -m "feat(forecast): drill from at-risk and each bridge step to every account in it"
```

---

### Task 5: Activity drill (gone quiet)

**Files:**
- Modify: `services/customers/activity_tracking.py`, `services/customers/views.py` (`ActivityTrackingView`)
- Test: `services/customers/tests/test_activity_tracking.py` (`ActivityTrackingViewTests`)

**Interfaces:**
- Produces in `activity_tracking.py`: `dark_accounts(customers, latest, today, organisation, rates) -> list[dict]` — the exact loop body `build_stats` uses today to build its going-dark rows (same fields, same sort). `build_stats` calls it instead of building the list inline, so the KPI and the drill share one predicate.
- Segment: `gone_quiet`, value `days_since_contact` (`None` = never contacted, listed first), value label `"days since contact"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_gone_quiet_drill_lists_every_dark_account(self):
    for i in range(18):
        self._customer_without_contact(f"Quiet {i}")
    stats = self.client.get(self.url).json()
    drill = self.client.get(self.url, {"drill": "gone_quiet"}).json()["drill"]
    self.assertEqual(drill["count"], stats["kpis"]["dark_accounts"])
    self.assertEqual(len(stats["going_dark"]), 15)  # the page's own list stays capped
    self.assertEqual(drill["value_label"], "days since contact")


def test_never_contacted_is_listed_first(self):
    never = self._customer_without_contact("Never")
    old = self._customer_without_contact("Old")
    self._activity(days_ago=120, customer=old)
    companies = self.client.get(self.url, {"drill": "gone_quiet"}).json()["drill"]["companies"]
    self.assertEqual([c["name"] for c in companies][:2], ["Never", "Old"])
    self.assertIsNone(companies[0]["value"])
    self.assertEqual(never.name, "Never")
```

Add a `_customer_without_contact(name)` helper to the test class if one does not exist (a live customer owned by the test user with no touches), mirroring the existing customer setup in that file.

- [ ] **Step 2: Run to verify they fail**

Run: `venv/bin/python manage.py test services.customers.tests.test_activity_tracking --noinput`
Expected: new tests FAIL.

- [ ] **Step 3: Implement**

Move the going-dark part of the loop in `build_stats` into:

```python
def dark_accounts(customers, latest, today, organisation, rates):
    """Every account nobody has touched in GOING_DARK_DAYS, never-contacted
    first, then longest silence. The KPI, the page's capped list and the
    drill all read this one list."""

    rows = []
    for customer in customers:
        last = latest.get(customer.pk)
        age = None if last is None else (today - last).days
        if age is not None and age < GOING_DARK_DAYS:
            continue
        converted = convert_to_org_currency(
            customer.arr_billed_at_account, customer.currency, organisation, rates=rates
        )
        rows.append(
            {
                "id": customer.id,
                "name": customer.name,
                "owner": customer.owner.name if customer.owner else "Unassigned",
                "arr": None if converted is None else float(converted),
                "health_category": customer.health_category,
                "lifecycle_stage": customer.get_lifecycle_stage_display(),
                "last_contact": last.isoformat() if last else None,
                "days_since_contact": age,
                "renewal_date": (
                    customer.renewal_date.isoformat() if customer.renewal_date else None
                ),
            }
        )
    rows.sort(
        key=lambda row: (row["days_since_contact"] is not None, -(row["days_since_contact"] or 0))
    )
    return rows
```

In `build_stats`, delete the inline `dark_accounts.append(...)` block and the sort, keep the cadence/coverage loop as it is, and after it set `dark = dark_accounts(customers, latest, today, organisation, rates)`; use `dark` for `kpis.dark_accounts`, `kpis.dark_arr` and `going_dark` (`dark[:LIST_LIMIT]`). Keep the existing comment about the sort order on the new function.

Add to `activity_tracking.py`:

```python
def gone_quiet_values(user, params):
    customers = list(filtered_customers(user, params))
    ids = [c.pk for c in customers]
    latest = last_contact_by_customer(ids) if ids else {}
    organisation = user.organisation
    rows = dark_accounts(
        customers, latest, timezone.localdate(), organisation, rates_for(organisation)
    )
    return {row["id"]: row["days_since_contact"] for row in rows}
```

In `ActivityTrackingView.get`:

```python
        if drill.parse_segment(request.query_params, {"gone_quiet": False}) is not None:
            return Response(
                drill.companies_payload(
                    request.user,
                    "gone_quiet",
                    activity_tracking.gone_quiet_values(request.user, request.query_params),
                    value_label="days since contact",
                    none_first=True,
                )
            )
```

- [ ] **Step 4: Run tests**

Run: `venv/bin/python manage.py test services.customers.tests.test_activity_tracking --noinput`
Expected: PASS (existing tests prove `build_stats` is unchanged in output).

- [ ] **Step 5: Commit**

```bash
git add services/customers/activity_tracking.py services/customers/views.py services/customers/tests/test_activity_tracking.py
git commit -m "feat(activity): drill from 'gone quiet' to every quiet account"
```

---

### Task 6: Customers drill (churned in the last year)

**Files:**
- Modify: `services/customers/portfolio.py`, `services/customers/views.py` (`CustomerOverviewView`)
- Test: `services/customers/tests/test_portfolio.py` (`CustomerOverviewTests`)

**Interfaces:**
- Produces in `portfolio.py`: `year_ago(today) -> date` (29 Feb safe) and `churned_last_year(customers, today) -> list[Customer]`, both used by `build_stats` and the drill.
- Segment: `churned_12m`, value = converted ARR, value label `"ARR"`.

- [ ] **Step 1: Write the failing tests**

```python
    def test_churned_12m_drill_matches_the_kpi(self):
        today = timezone.localdate()
        self._customer("Left recently", churn_date=today - timedelta(days=30))
        self._customer("Left long ago", churn_date=today - timedelta(days=500))
        self._customer("Still here")
        stats = self.client.get(self.url).json()
        drill = self.client.get(self.url, {"drill": "churned_12m"}).json()["drill"]
        self.assertEqual(drill["count"], stats["kpis"]["churned_12m"])
        self.assertEqual([c["name"] for c in drill["companies"]], ["Left recently"])
        self.assertEqual(drill["value_label"], "ARR")


class YearAgoTests(SimpleTestCase):
    def test_leap_day(self):
        self.assertEqual(portfolio.year_ago(date(2028, 2, 29)), date(2027, 2, 28))

    def test_ordinary_day(self):
        self.assertEqual(portfolio.year_ago(date(2026, 9, 24)), date(2025, 9, 24))
```

(Import `date`, `timedelta`, `SimpleTestCase` and `portfolio` as the file needs; use the file's `_customer(name, arr=100_000, **overrides)` helper.)

- [ ] **Step 2: Run to verify they fail**

Run: `venv/bin/python manage.py test services.customers.tests.test_portfolio --noinput`
Expected: FAIL.

- [ ] **Step 3: Implement** in `portfolio.py`:

```python
def year_ago(today):
    """The same calendar day a year earlier; 29 Feb falls back to 28 Feb
    rather than raising, which `date.replace` does."""
    try:
        return today.replace(year=today.year - 1)
    except ValueError:
        return today.replace(year=today.year - 1, day=28)


def churned_last_year(customers, today):
    """Churn inside the last year, which is the rate anyone quotes."""
    cutoff = year_ago(today)
    return [c for c in customers if is_churned(c) and c.churn_date >= cutoff]
```

In `build_stats`, replace the two `year_ago`/`churned_12m` lines with `churned_12m = churned_last_year(customers, today)` (keep the comment). Add:

```python
def churned_12m_values(user, params):
    organisation = user.organisation
    rates = rates_for(organisation)
    values = {}
    for customer in churned_last_year(list(filtered_customers(user, params)), timezone.localdate()):
        converted = convert_to_org_currency(
            customer.arr_billed_at_account, customer.currency, organisation, rates=rates
        )
        values[customer.pk] = None if converted is None else round(float(converted), 2)
    return values
```

In `CustomerOverviewView.get`:

```python
        if drill.parse_segment(request.query_params, {"churned_12m": False}) is not None:
            return Response(
                drill.companies_payload(
                    request.user,
                    "churned_12m",
                    portfolio.churned_12m_values(request.user, request.query_params),
                    value_label="ARR",
                )
            )
```

`visible_customers` includes churned and archived customers, the same set `portfolio.filtered_customers` starts from, so nothing counted by the KPI is dropped by the intersection.

- [ ] **Step 4: Run tests**

Run: `venv/bin/python manage.py test services.customers.tests.test_portfolio --noinput`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/customers/portfolio.py services/customers/views.py services/customers/tests/test_portfolio.py
git commit -m "feat(portfolio): drill from 'churned in 12 months' to who left"
```

---

### Task 7: `?ids=` on the customers list

**Files:**
- Modify: `services/customers/views.py` (`CustomerListCreateView.get_queryset` + docstring)
- Test: `services/customers/tests/test_views.py` (new `CustomerIdsFilterTests` beside `CustomerSearchTests`)

**Interfaces:**
- `GET /api/v1/customers/?ids=3,7,12` returns only those customers, still scoped to the viewer and still excluding archived; non-integer parts are ignored; at most 500 ids are read; an `ids` with no valid id is ignored (normal list).

- [ ] **Step 1: Write the failing tests**

```python
class CustomerIdsFilterTests(APITestCase):
    url = "/api/v1/customers/"

    def setUp(self):
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
        self.a = Customer.objects.create(organisation=self.org, name="A", owner=self.csm)
        self.b = Customer.objects.create(organisation=self.org, name="B", owner=self.csm)
        self.c = Customer.objects.create(organisation=self.org, name="C", owner=self.csm)
        self.theirs = Customer.objects.create(organisation=self.org, name="T", owner=self.other)
        self.client.force_authenticate(self.csm)

    def names(self, params):
        return sorted(row["name"] for row in self.client.get(self.url, params).json()["results"])

    def test_only_the_named_customers(self):
        self.assertEqual(self.names({"ids": f"{self.a.pk},{self.c.pk}"}), ["A", "C"])

    def test_scoping_still_applies(self):
        self.assertEqual(self.names({"ids": f"{self.a.pk},{self.theirs.pk}"}), ["A"])

    def test_bad_parts_are_ignored_and_all_bad_means_no_filter(self):
        self.assertEqual(self.names({"ids": f"x,{self.b.pk},"}), ["B"])
        self.assertEqual(self.names({"ids": "x,y"}), ["A", "B", "C"])
```

(Check how other list tests in this file read the paginated body — `["results"]` — and follow that.)

- [ ] **Step 2: Run to verify it fails**

Run: `venv/bin/python manage.py test services.customers.tests.test_views.CustomerIdsFilterTests --noinput`
Expected: FAIL.

- [ ] **Step 3: Implement** in `get_queryset`, after the search block:

```python
        ids = []
        for part in self.request.query_params.get("ids", "").split(",")[:500]:
            try:
                ids.append(int(part))
            except ValueError:
                continue
        if ids:
            queryset = queryset.filter(pk__in=ids)
```

and add to the docstring: "`?ids=1,2,3` narrows to those customers (the dashboard's 'Open as a list'); non-integers are ignored, at most 500 are read, and scoping still applies."

- [ ] **Step 4: Run tests**

Run: `venv/bin/python manage.py test services.customers.tests.test_views --noinput`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/customers/views.py services/customers/tests/test_views.py
git commit -m "feat(customers): list only the customers a dashboard drill named"
```

---

### Task 8: Contracts, docs and full verification

**Files:**
- Modify: `docs/API_CONTRACTS.md`; the product docs per `.claude/skills/api-contracts` and the repo's product-docs rule (`docs/product/01-prd.md` capability row for drill-down if a dashboard row exists there)

- [ ] **Step 1:** Add a `### Dashboard drill (`?drill=`)` section to `docs/API_CONTRACTS.md` in the same style as `### GET /api/v1/interactions/stats/` (L2213): the response shape from Global Constraints; a table of endpoint → segments → value label (tickets, interactions, forecast, activity, overview, exactly as Tasks 2–6 implemented them); the two rules (same filtered set as the totals, intersected with the viewer's customers; a bad drill returns the normal stats). Add `?drill=` to the existing interactions query-param table (L2236-2244). Add `ids` to the customers list documentation (search for where `renewal_within` is documented; if the customers list has no section, add the param note to the drill section under "Open as a list").
- [ ] **Step 2:** Update the Ticket Overview / AI Trending status rows (L81-82) only if they describe endpoint params.
- [ ] **Step 3:** Run `venv/bin/ruff format --check . && venv/bin/ruff check .` (fix any formatting ruff wants in Markdown fences) and the full suite `venv/bin/python manage.py test --noinput`. Expected: all pass.
- [ ] **Step 4: Commit**

```bash
git add docs
git commit -m "docs(api): the dashboard drill parameter and ?ids= on the customers list"
```

- [ ] **Step 5:** Hand off to `finishing-a-development-branch`. The backend PR merges **before** the frontend drill PR (deploy order).
