# Dashboard attention list, backend (PR 3a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve the Overview's ranked "Needs attention" list (`GET /api/v1/dashboard/attention/`) with per-person snoozing, move the Triage risk score to the backend so the list and the Triage tile share one rule, and add `open_count` / `oldest_open_days` to ticket stats for the Support headline card.

**Architecture:** `services/customers/triage.py` ports the Triage score from the frontend; `CustomerHealthRowSerializer` serves it on every Health row. A new app `services/attention` holds `AttentionSnooze` and `rules.py`, which builds the five item kinds from the same helpers the dashboard areas use (triage, `activity_tracking.dark_accounts`, the ticket scoping, the anomaly `visible_evidence`), scores them (at stake × urgency), removes snoozed items unless they got worse, and returns the top 25. No model call.

**Tech Stack:** Django 5, DRF, PostgreSQL, `APITestCase`, `LiveServerTestCase` for the e2e test.

**Spec:** `react-ts-app/docs/superpowers/specs/2026-09-23-dashboard-redesign-design.md` §2 plus its "Amendments (2026-09-24, before PR 3)" block (these win where they differ).

## Global Constraints

- Scoping is twice-filtered: companies through `live_customers(user)` (the set the Health/Activity dashboards use), tickets through `visible_tickets(user, Ticket.objects.filter(visible_children_q(user)).distinct())`, anomalies through `services.anomalies.views.visible_evidence(organisation, user)` counting only companies the viewer can see. No record text in any item (reasons are built from fields).
- Filters: `owner` (int or `unassigned`), `lifecycle` (a `Customer.LifecycleStage` value), `customer` (int) — the same parsing as `forecast.filtered_customers`; bad values are ignored, never a 400.
- Money: `arr_billed_at_account` converted with `convert_to_org_currency(..., rates=rates_for(org))`; unconvertible ARR counts as 0 at stake and the item still appears (reason says "ARR unknown").
- Urgency formulas exactly as the spec amendment. Score = `round(at_stake * urgency, 2)`.
- Response: `{"items": [AttentionItem...], "currency": str, "filters": {...}}`, items sorted by score desc then title, max 25.
- `AttentionItem = {key, kind, title, reason, at_stake, urgency, score, customer_id|null, companies: [{id, name}] (anomaly only, else []), fingerprint}`.
- Snooze: per user; hidden while `until` is in the future (or `until` is null = Done) unless the item's current fingerprint is worse than the stored one.
- Audit: `attention.snoozed` and `attention.unsnoozed` via `core.audit.record`; listed in `docs/audit-events.md`. `AttentionSnooze` classified **internal** in `docs/data-classification.md`.
- Every endpoint change updates `docs/API_CONTRACTS.md` in the same PR (`.claude/skills/api-contracts`); product docs per the product-docs rule (PRD row, backend schema doc).
- Tests: unit (`SimpleTestCase`), integration (`APITestCase`), e2e (`LiveServerTestCase` in top-level `e2e/`) per `.claude/skills/testing`.
- `ruff format --check .` and `ruff check .` pass (ruff also formats Python inside Markdown fences — run `ruff format` on any doc with code).
- Commits end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Tests: `venv/bin/python manage.py test <label> --noinput`.

## File Structure

| File | Responsibility |
|---|---|
| `services/customers/triage.py` (new) | `triage(...)`: the Triage score, factors, direction for one customer |
| `services/customers/serializers.py` | `triage_score`, `triage_factors`, `triage_direction` on `CustomerHealthRowSerializer` |
| `services/attention/` (new app) | `apps.py`, `models.py` (`AttentionSnooze`), `admin.py`, `migrations/0001`, `rules.py`, `snooze.py`, `views.py`, `urls.py`, `tests/` |
| `config/settings.py`, `config/urls.py` | register the app; mount `api/v1/dashboard/` |
| `services/customers/views.py` | `TicketStatsView` kpis gain `open_count`, `oldest_open_days` |
| `e2e/test_attention_flow.py` (new) | end-to-end: list, snooze, undo |
| docs | API_CONTRACTS, audit-events, data-classification, PRD, backend schema |

---

### Task 1: The Triage score on the backend

**Files:**
- Create: `services/customers/triage.py`, `services/customers/tests/test_triage.py`
- Modify: `services/customers/serializers.py` (`CustomerHealthRowSerializer`)
- Test: `services/customers/tests/test_views.py` (`CustomerHealthViewTests`)

**Interfaces:**
- Produces: `triage(*, health_category: str | None, csm_pulse: int | None, ai_pulse: int | None, renewal_date: date | None, history: list[str], today: date) -> Triage` where `Triage` is a frozen dataclass `(score: int, factors: list[dict], direction: str, days_to_renewal: int | None)`; `ACTION_THRESHOLD = 40`.
- The frontend formula (react-ts-app `src/pages/dashboard/tabs/health-overview/triage.ts`, ported exactly):
  - `SEVERITY = {"good": 0, "average": 1, "poor": 3}`, `WEIGHT = {severity: 22, pulse_gap: 11, renewal_urgent: 18, renewal_near: 8, decline: 9}`.
  - health: `SEVERITY[cat] * 22` when > 0, factor label "Poor health" / "Average health".
  - pulse gap: `csm - ai` when both not None and > 0: `gap * 11`, label "CSM pulse <gap> above AI".
  - renewal: `days = (renewal_date - today).days`; `days <= 90` → 18 ("renews in <days> days" or "renewal <abs> days overdue"), elif `days <= 180` → 8 ("renews in <days> days"); None → 0.
  - trajectory: last 3 of `history` (oldest-first list of categories); < 2 → "unknown"; `delta = SEV[last] - SEV[first]`; > 0 declining (`delta * 9`, "declining over 3 months"), < 0 improving, 0 flat.
  - The frontend's Pilot factor (+6 when lifecycle label is "Pilot") is dropped: the backend has no Pilot stage, so it never fired. Say so in the module docstring.
  - Factors sorted by points desc; `score = sum(points)`.
  - Factor dicts are `{"label": str, "points": int}`.
- Serializer fields: `triage_score` (int), `triage_factors` (list), `triage_direction` (str), computed from the row's `health_category`, `csm_pulse_score`, `ai_pulse_value`, `renewal_date`, the prefetched `health_snapshots` categories in order, and `timezone.localdate()`; cache on the instance like `_risk_of_loss`.

- [ ] **Step 1: Write the failing unit tests** — `test_triage.py` (`SimpleTestCase`): one test per factor with near-misses (renewal at 90 vs 91 days, 180 vs 181, overdue −5; gap 0/1/2 and None; history `["good","average","poor"]` declining delta 3 → 27, `["poor","good"]` improving, `["good"]` unknown), a combined case whose score is exactly 40 (e.g. average 22 + renewal urgent 18) and one at 39 (average 22 + near 8 + ... pick values that sum to 39), and factor ordering. Mirror three cases from the frontend `triage.test.ts` (read it) so the port provably matches.
- [ ] **Step 2:** run `venv/bin/python manage.py test services.customers.tests.test_triage --noinput` → FAIL (module missing).
- [ ] **Step 3: Implement** `triage.py` per the interface (pure function, no ORM).
- [ ] **Step 4: Serializer + view test** — in `CustomerHealthViewTests`, a customer with average health renewing in 30 days returns `triage_score == 40` and a factors list with both labels; assert the query count for the health endpoint does not grow (reuse the existing `assertNumQueries` test's expected number).
- [ ] **Step 5:** run the triage and health view tests → PASS; ruff; commit `feat(customers): the Triage score, computed once on the server`.

---

### Task 2: The attention app and `AttentionSnooze`

**Files:**
- Create: `services/attention/{__init__,apps,models,admin}.py`, `services/attention/migrations/0001_initial.py` (via `makemigrations`), `services/attention/tests/__init__.py`, `services/attention/tests/test_models.py`
- Modify: `config/settings.py` (`INSTALLED_APPS += ["services.attention"]`)

**Interfaces:**
- `AttentionSnooze(models.Model)`: `organisation` FK (`accounts.Organisation`, CASCADE), `user` FK (`settings.AUTH_USER_MODEL`, CASCADE, related_name `attention_snoozes`), `key` CharField(120), `until` DateTimeField(null=True, blank=True) — null means Done, `fingerprint` JSONField(default=dict), `created_at` auto_now_add. `UniqueConstraint(fields=["user", "key"], name="attention_snooze_unique_per_user")`; index on `(user, key)`. `__str__` returns `f"{self.user_id}:{self.key}"`.
- Copy the app layout from `services/requests/` (apps.py `default_auto_field`, `verbose_name = "Attention"`).

- [ ] Step 1: test (`TestCase`) that two snoozes with the same user and key raise `IntegrityError`, and the same key for two users is allowed. Step 2: fail. Step 3: implement, `makemigrations attention`. Step 4: pass; `manage.py makemigrations --check`. Step 5: commit `feat(attention): a per-person snooze for dashboard items`.

---

### Task 3: The rules — building and scoring items

**Files:**
- Create: `services/attention/rules.py`, `services/attention/tests/test_rules.py`

**Interfaces:**
- `urgency_for_days_to(days: int | None) -> float` (renewal/risk; None → 0.5), `urgency_quiet(days_since: int | None) -> float`, `urgency_ticket_age(days: int) -> float`, `urgency_anomaly_age(days: int) -> float` — exactly the spec amendment's formulas, rounded to 3 places.
- `filtered_customers(user, params)` — reuse `forecast.filtered_customers(user, params)` (it already applies `live_customers` + owner/lifecycle/customer), with `with_health_inputs` and the health snapshot prefetch the Health view uses (last 3 months are enough: `captured_on >= today - 31*4 days`).
- `build_items(user, params, *, today) -> list[dict]` — returns every candidate item (not yet snooze-filtered), each with `key`, `kind`, `title`, `reason`, `at_stake`, `urgency`, `score`, `customer_id`, `companies`, `fingerprint`:
  - **renewal** (`renewal:<customer_id>`): `renewal_date` set, `days <= 90` (overdue included), `health_category != "good"`. Reason `"renewal <n> days overdue · health Average"` / `"renews in <n> days · health Poor"`. Fingerprint `{"days": days, "health": cat, "arr": at_stake}`.
  - **risk** (`risk:<customer_id>`): `triage(...).score >= ACTION_THRESHOLD`. Reason `"risk <score> · <top factor label>"`. Fingerprint `{"score": score, "arr": at_stake}`.
  - **going_quiet** (`going_quiet:<customer_id>`): rows from `activity_tracking.dark_accounts(customers, last_contact_by_customer(ids), today, org, rates)`. Reason `"no contact in <n> days"` / `"never contacted"`. Fingerprint `{"days": days_since_contact (None→ -1), "arr": at_stake}`.
  - **support** (`support:<customer_id>`): scoped tickets (Global Constraints) with `priority in {critical, high}` and status not in `RESOLVED_STATUSES`, grouped per customer (account-level tickets count for each of the account's customers that are in the filtered customer set). Reason `"<n> open High/Critical tickets · oldest <d> days"`. Urgency from the oldest `opened_at`. Fingerprint `{"count": n, "oldest": d, "arr": at_stake}`.
  - **anomaly** (`anomaly:<anomaly_id>`): `Anomaly` with `status == live` for the org whose `visible_evidence(org, user, anomaly=a)` resolves to at least one company in the filtered customer set; `companies` = those (id, name), at stake = sum of their converted ARR, title = the anomaly title (stored, model-written but not record text — confirm it is shown on the Anomalies page already), reason `"<n> companies · first seen <d> days ago"`. Fingerprint `{"companies": n, "arr": at_stake}`.
- `worse(kind, stored: dict, current: dict) -> bool`: renewal — days smaller (closer/more overdue) or health severity higher or arr higher; risk — score higher or arr higher; going_quiet — days larger (−1 = never counts as largest) or arr higher; support — count higher or oldest larger or arr higher; anomaly — companies higher or arr higher.

- [ ] **Step 1: Write failing tests** (`test_rules.py`): unit tests for each urgency function at its breakpoints (e.g. `urgency_for_days_to(14) == 1.0`, `(90) == 0.25`, `(52)` midpoint, `(-5) == 1.0`, `(None) == 0.5`, `(120) == 0.25`); `worse()` per kind (better, same, worse). Integration-style `TestCase` tests for `build_items`: one fixture per kind with a near-miss (renewal at 91 days; health good; ticket priority medium; ticket resolved; contact 59 days ago; anomaly acknowledged; an anomaly whose only evidence is on another CSM's customer), each asserting exactly which keys appear; ARR conversion with a non-org currency; an item for another CSM's customer never appears; `owner`/`lifecycle`/`customer` filters narrow items; the `risk` item's score equals the serializer's `triage_score` for the same customer.
- [ ] Step 2: fail. Step 3: implement. Step 4: pass. Step 5: commit `feat(attention): what needs a person first, scored by money at stake and urgency`.

---

### Task 4: Snoozing and the endpoints

**Files:**
- Create: `services/attention/snooze.py`, `services/attention/views.py`, `services/attention/urls.py`, `services/attention/tests/test_views.py`
- Modify: `config/urls.py` (`path("api/v1/dashboard/", include("services.attention.urls"))`)

**Interfaces:**
- `visible_items(user, items, *, now) -> list[dict]` in `snooze.py`: drops items with a snooze whose (`until` is null or `until > now`) AND not `worse(kind, snooze.fingerprint, item.fingerprint)`; expired snoozes are ignored (not deleted).
- `GET /api/v1/dashboard/attention/?owner=&lifecycle=&customer=` → `{"items": top 25 by score desc then title, "currency": org.currency, "filters": forecast.filter_options(user)}`. `IsAuthenticated`.
- `POST /api/v1/dashboard/attention/snooze/` body `{"key": str, "days": 7}` or `{"key": str, "done": true}`: validates the key is one of the viewer's current items (from `build_items` with no filters) — else 400 `{"key": ["Not an item on your list."]}`; upserts `AttentionSnooze` with the item's current fingerprint, `until = now + days` (days 1–90) or null for done; audit `attention.snoozed` (metadata: key, days or done). Returns 201 `{"key", "until"}`.
- `DELETE /api/v1/dashboard/attention/snooze/<key>/`: deletes the viewer's snooze (404 if none); audit `attention.unsnoozed` (metadata: key). Keys contain `:` — use `<path:key>` in the URL.

- [ ] **Step 1: Failing tests** (`APITestCase`, template `services/customers/tests/test_activity_tracking.py`): unauthenticated → 401; items sorted by score and capped at 25; response has `currency` and `filters`; snooze 7 days hides the item for that user only (another user still sees it); done hides it; a snooze whose item got worse (e.g. renewal now overdue further, or ARR raised) shows it again; an expired snooze shows it again; snoozing a key not on your list → 400; DELETE restores; both audit events recorded with the right metadata; another org's user cannot snooze your keys.
- [ ] Step 2: fail. Step 3: implement. Step 4: pass. Step 5: commit `feat(attention): the attention endpoint and snoozing`.

---

### Task 5: Ticket stats for the Support headline card

**Files:** `services/customers/views.py` (`TicketStatsView.get` kpis), `services/customers/tests/test_views.py` (`TicketStatsTests`)

- `kpis.open_count` = scoped, filtered tickets not in `RESOLVED_STATUSES`; `kpis.oldest_open_days` = `(today - min(opened_at)).days` over those, or `None` when there are none.
- [ ] Test (fixture with open, in-progress, resolved, closed tickets and known `opened_at`s; and an empty case → `None`), fail, implement, pass, commit `feat(tickets): open tickets and the oldest one, for the Overview`.

---

### Task 6: End-to-end test, docs, full verification

**Files:** `e2e/test_attention_flow.py` (template `e2e/test_requests_flow.py`); docs.

- [ ] **e2e:** a CSM with a renewal-due customer lists attention, snoozes the item, no longer sees it, undoes, sees it again.
- [ ] **Docs:** `docs/API_CONTRACTS.md` — new `### GET /api/v1/dashboard/attention/`, `### POST /api/v1/dashboard/attention/snooze/`, `### DELETE /api/v1/dashboard/attention/snooze/<key>/` sections (shape, kinds, urgency formulas, scoping, 25 cap) and the new Health row fields and ticket kpis; `docs/audit-events.md` two rows (`| attention.snoozed | services.attention.views.AttentionSnoozeView | the person | AttentionSnooze | key, days or done |`, and `attention.unsnoozed`); `docs/data-classification.md` row `attention.AttentionSnooze | internal | — | item keys and numeric fingerprints only; no record text`; `docs/product/01-prd.md` capability row; `docs/product/05-backend-schema.md` model entry. Run `venv/bin/ruff format` on any doc with Python fences.
- [ ] **Verify:** `venv/bin/ruff format --check .`, `venv/bin/ruff check .`, `venv/bin/python manage.py makemigrations --check --dry-run`, full `venv/bin/python manage.py test --noinput --parallel 4`. Commit `docs(attention): contracts, audit, classification and product docs`.
- [ ] Hand off to `finishing-a-development-branch`; this PR merges before the frontend PR 3b.
