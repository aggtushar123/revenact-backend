---
description: Full repository architecture, app map, and URL reference for the Revenact Django backend
---

# Revenact Backend — Repository Architecture & Flow

## Tech Stack

| Layer | Technology |
|---|---|
| Language / framework | Python 3 + Django 5.2 |
| API layer | Django REST Framework 3.18 |
| Database | PostgreSQL 16 (Django ORM) |
| API docs | drf-spectacular — auto-generated OpenAPI 3 schema, Swagger UI, Redoc |
| CORS | django-cors-headers, scoped to the Vite dev server origin |
| Config | django-environ (`.env`, see `.env.example`) |
| Containerization | Docker — `docker-compose.yml` runs `web` (this Django app, built from `Dockerfile`) + `db` (`postgres:16-alpine`); `docker compose up --build` runs the whole backend |

## Top-Level Directory Map

```
revenact-backend/
├── .agents/workflows/          ← Workflow knowledge files (this file)
├── .claude/skills/             ← Claude Code skills (karpathy-guidelines,
│                                  commit-messages, flow-docs, api-contracts,
│                                  testing)
├── e2e/                        ← Cross-app end-to-end flow tests (LiveServerTestCase)
├── config/                     ← Django project (settings, root URLconf)
│   ├── settings.py
│   ├── urls.py                 ← /admin, /api/schema, /api/docs, /api/redoc, /api/v1/*
│   ├── asgi.py / wsgi.py
├── core/                       ← Shared/infra app — health check today
│   ├── models.py                (empty — infra app, no domain models)
│   ├── views.py                 health_check
│   ├── urls.py                  mounted at /api/v1/ in config/urls.py
│   └── migrations/
├── services/                   ← Houses the `accounts` and `customers`
│   │                              Django apps (moved here from top-level),
│   │                              plus cross-app business logic that isn't
│   │                              a serializer/view's job. `services/`
│   │                              itself is a plain package, not a Django
│   │                              app — INSTALLED_APPS lists
│   │                              "services.accounts"/"services.customers"
│   │                              directly, each keeping its original app
│   │                              label ("accounts"/"customers", set
│   │                              explicitly in its apps.py) so
│   │                              AUTH_USER_MODEL, migration dependencies,
│   │                              and every ForeignKey("accounts.X")/
│   │                              ("customers.X") string elsewhere needed
│   │                              no changes.
│   ├── accounts/                 Auth — see App List below
│   ├── customers/                Organizations — see App List below
│   └── email.py                 send_password_reset_email — used by
│                                 services.accounts.serializers.ForgotPasswordSerializer
├── docs/
│   └── API_CONTRACTS.md        ← Narrative companion to the OpenAPI schema
├── Dockerfile                  ← Containerizes the Django app (`web` service)
├── docker-compose.yml          ← web (this app) + db (Postgres) — whole backend
├── .env.example / .env
├── requirements.txt
├── manage.py
└── README.md
```

Each frontend feature (`react-ts-app/src/pages/<domain>/`,
`src/features/<domain>/`) gets its own Django app
(`python manage.py startapp <domain>`), mounted under `/api/v1/<domain>/`.
`core` lives at the top level; `accounts` and `customers` live under
`services/` (see above) — pick whichever placement fits when adding a
new one, there's no fixed rule forcing every future app under `services/`.

## URL Map (`config/urls.py`)

```
/admin/                → Django admin
/api/schema/           → Raw OpenAPI 3 schema (JSON), auto-generated
/api/docs/             → Swagger UI (interactive)
/api/redoc/            → Redoc (read-only reference)
/api/v1/
└── health/            → core.urls → health_check (GET, AllowAny)
```

Every new domain app adds one `path("api/v1/", include("<app>.urls"))` line
in `config/urls.py`.

## App List

### `core` — infrastructure

| File | Role |
|---|---|
| `views.py: health_check` | `GET /api/v1/health/` — liveness check, `AllowAny`, no params |
| `models.py` | Empty — infra app carries no domain models |

**Status:** ✅ Built (health check only).

### `accounts` — Auth (`authSlice.ts`, `Login.tsx`)

Django app named `accounts` (avoids colliding with `django.contrib.auth`'s
app label), mounted at `/api/v1/auth/` to match the frontend's
`features/auth/`. Also the app that defines `AUTH_USER_MODEL`.

| File | Role |
|---|---|
| `models.py: Organisation` | The tenant — `name`, unique `slug`, `created_at` |
| `models.py: User` | Custom `AUTH_USER_MODEL` — email login, `organisation` FK, `role` (admin/csm) |
| `views.py: SignupView` | `POST /signup/` — creates org + admin user, returns tokens |
| `views.py: LoginView` | `POST /login/` — JWT login for any user |
| `views.py: LogoutView` | `POST /logout/` — blacklists the given refresh token |
| `views.py: MeView` | `GET/PATCH /me/` — your own profile (any role) |
| `views.py: ChangePasswordView` | `POST /me/change-password/` — self-service, needs current password |
| `views.py: ForgotPasswordView` | `POST /password-reset/` — `AllowAny`, always 200; emails a reset link when the address matches a user (via `services/email.py`) |
| `views.py: ResetPasswordView` | `POST /password-reset/confirm/` — `AllowAny`, consumes the emailed uid/token to set a new password |
| `views.py: MembersListView` | `GET /members/` — any authenticated user, all org members (admin+CSMs), plain array. Not admin-gated — powers owner-pickers elsewhere (e.g. `customers`). |
| `views.py: CSMListCreateView` | `GET/POST /csms/` — admin-only, list/add CSMs in their own org |
| `views.py: CSMDetailView` | `GET/PATCH /csms/<id>/` — admin-only, edit/deactivate a CSM in their own org |
| `permissions.py: IsOrgAdmin` | Gates admin-only actions |

Full walkthrough: `auth-flow.md` in this same directory.

**Status:** ✅ Built (signup, login, logout, token refresh, own-profile
edit + password change, self-serve forgot/reset password, admin User
Management for CSMs).

### `customers` — Organizations

Not to be confused with `accounts.Organisation` (the tenant) — see
`docs/API_CONTRACTS.md` → `customers` for why these are deliberately
different models with different names. A `Customer` is one of a tenant's
own customers. Field set matches `tableData.ts`'s mock schema column for
column — see that doc section for the full field list, grouped, with the
reasoning behind what's derived (`health_category`,
`seat_utilization_percentage`) vs. independently stored (all the
financial fields, even ones the mock data happens to make look additive).

| File | Role |
|---|---|
| `models.py: Customer` | Full `tableData.ts`-matching schema — identity/provenance, lifecycle/health, dates, financials, product/usage, churn. `email`/`phone` are additions beyond that original mock schema, backing ActivityFeed's Overview tab. |
| `models.py: Account` | One-to-many under `Customer` (`customer` FK, `related_name="accounts"`) — a named sub-account with its own health/pulse/NPS/CSAT. Mirrors `accountsData.ts`'s `AccountRow`; reuses `Customer`'s LifecycleStage/AIPulseScore choices and health thresholds rather than redefining them. `domain`/`address`/`email`/`phone` all fall back to the parent Customer's own value when blank — implemented in `mapAccountToAccountRow.ts`, not the model/serializer. |
| `views.py: CustomerListCreateView` | `GET/POST /customers/` — any authenticated user in the org (no admin gate, unlike User Management) |
| `views.py: CustomerDetailView` | `GET/PATCH /customers/<id>/` — same org only, 404 outside it |
| `views.py: CustomerStatsView` | `GET /customers/stats/` — Health/NPS/Lifecycle rollups for MetricsPanel |
| `views.py: AccountListCreateView` | `GET/POST /customers/<customer_id>/accounts/` — 404 (not empty list) for a customer_id outside the caller's org; POST's `customer` always comes from the URL |
| `views.py: AccountDetailView` | `GET/PATCH /customers/<customer_id>/accounts/<id>/` — same org + customer only, 404 outside it |
| `models.py: Activity` | Belongs to exactly one of `Customer` or `Account` (two nullable FKs + a DB `CheckConstraint`, not a `GenericForeignKey`) — a timeline entry backing `ActivityFeed`'s "Activities" filter. Read-only so far — no create/update endpoint yet. |
| `views.py: CustomerActivityListView` | `GET /customers/<customer_id>/activities/` — org-level activities for one Customer, 404 (not empty list) outside the caller's org |
| `views.py: AccountActivityListView` | `GET /customers/<customer_id>/accounts/<account_id>/activities/` — account-level activities for one Account, 404 for either id outside scope |
| `models.py: Email` | Same exactly-one-parent shape as `Activity` — a logged email backing `ActivityFeed`'s "Emails" filter. Read-only so far. |
| `views.py: CustomerEmailListView` | `GET /customers/<customer_id>/emails/` — org-level emails for one Customer, same 404 convention |
| `views.py: AccountEmailListView` | `GET /customers/<customer_id>/accounts/<account_id>/emails/` — account-level emails for one Account |
| `models.py: Task` | Same exactly-one-parent shape as `Activity`/`Email` — a to-do item backing `ActivityFeed`'s "Tasks" filter. Read-only so far. No stored `group` — the Overdue/This Week/Next Week/Later bucket is computed from `due_date` at render time. |
| `views.py: CustomerTaskListView` | `GET /customers/<customer_id>/tasks/` — org-level tasks for one Customer, same 404 convention |
| `views.py: AccountTaskListView` | `GET /customers/<customer_id>/accounts/<account_id>/tasks/` — account-level tasks for one Account |
| `models.py: Note` | Same exactly-one-parent shape as `Activity`/`Email`/`Task` — a logged note backing `ActivityFeed`'s "Notes" filter. Read-only so far. `links` is a real field (the old mock always showed a hardcoded "1 Links", a bug this replaces), shown on the card only when > 0. |
| `views.py: CustomerNoteListView` | `GET /customers/<customer_id>/notes/` — org-level notes for one Customer, same 404 convention |
| `views.py: AccountNoteListView` | `GET /customers/<customer_id>/accounts/<account_id>/notes/` — account-level notes for one Account |
| `models.py: Ticket` | Same exactly-one-parent shape as `Activity`/`Email`/`Task`/`Note` — a support ticket backing `ActivityFeed`'s "Tickets" filter. Read-only so far. Field set (including `priority`, not just `status`) was reverse-engineered from the card, not dictated up front — see the model's own docstring. |
| `views.py: CustomerTicketListView` | `GET /customers/<customer_id>/tickets/` — org-level tickets for one Customer, same 404 convention |
| `views.py: AccountTicketListView` | `GET /customers/<customer_id>/accounts/<account_id>/tickets/` — account-level tickets for one Account |
| `models.py: CalendarEvent` | Same exactly-one-parent shape as `Activity`/`Email`/`Task`/`Note`/`Ticket` — a scheduled meeting/call/review/demo backing `ActivityFeed`'s "Calendar Events" filter. Read-only so far. `attendee_count` is stored directly rather than a list of names, since the card only ever renders the count. |
| `views.py: CustomerCalendarEventListView` | `GET /customers/<customer_id>/calendar-events/` — org-level events for one Customer, same 404 convention |
| `views.py: AccountCalendarEventListView` | `GET /customers/<customer_id>/accounts/<account_id>/calendar-events/` — account-level events for one Account |
| `models.py: Contact` | Same exactly-one-parent shape as `Activity`/`Email`/`Task`/`Note`/`Ticket`/`CalendarEvent` — a person at a Customer or Account. Unlike those (which each back one filter *within* `ActivityFeed`), Contact backs its own sibling tab (Organization/Account Details' own Contacts tabs) plus the global `/contacts/list` page. `company` is a Python property (not a column) resolving to the ultimate parent Customer either way — see `ContactSerializer`'s `company_id`/`company_name`/`account_name`. Full CRUD. |
| `views.py: CustomerContactListView` | `GET/POST /customers/<customer_id>/contacts/` — org-level contacts for one Customer, same 404 convention; POST sets `customer` from the URL |
| `views.py: AccountContactListView` | `GET/POST /customers/<customer_id>/accounts/<account_id>/contacts/` — account-level contacts for one Account; POST sets `account` from the URL |
| `views.py: ContactListView` / `ContactStatsView` | `GET /api/v1/contacts/` (paginated, `?search=`/`?company=`) and `GET /api/v1/contacts/stats/` — the one Contact view spanning every Customer/Account, mounted at its own top-level prefix in `config/urls.py` rather than nested under `services.customers.urls`. Powers the standalone `/contacts/list` page. |
| `views.py: ContactDetailView` | `GET/PATCH/DELETE /api/v1/contacts/<id>/` — flat (not nested), scoped by org ownership via either parent FK. Powers Edit/Delete on all three Contacts UIs; can't move a Contact between parents (`customer`/`account` aren't in `ContactSerializer`'s fields at all). |
| `management/commands/seed_demo_customers.py` | Dev-only: seeds an org with the tableData.ts mock's 14 companies — `python manage.py seed_demo_customers --org-email <admin email>`. Idempotent. |
| `management/commands/seed_demo_accounts.py` | Dev-only: seeds Account rows (from accountsData.ts) under existing demo Customers — run after seed_demo_customers. Idempotent. |
| `management/commands/seed_demo_activities.py` | Dev-only: seeds Activity rows under every seeded Customer/Account — run after seed_demo_accounts. Idempotent. |
| `management/commands/seed_demo_emails.py` | Dev-only: seeds Email rows under every seeded Customer/Account — run after seed_demo_accounts. Idempotent. |
| `management/commands/seed_demo_tasks.py` | Dev-only: seeds Task rows under every seeded Customer/Account — due dates are offsets from the run date, not fixed calendar dates (a due date is inherently relative to "now"), so idempotency keys on (parent, title) and re-running refreshes the dates. Idempotent. |
| `management/commands/seed_demo_notes.py` | Dev-only: seeds Note rows under every seeded Customer/Account — `links` varies across 0 and a few positive counts to exercise both card states. Idempotent. |
| `management/commands/seed_demo_tickets.py` | Dev-only: seeds Ticket rows under every seeded Customer/Account — `links` and `priority` both vary across their full range to exercise every card state. Idempotent. |
| `management/commands/seed_demo_calendar_events.py` | Dev-only: seeds CalendarEvent rows under every seeded Customer/Account — spans all four event types. Idempotent. |
| `management/commands/seed_demo_contacts.py` | Dev-only: seeds Contact rows under every seeded Customer/Account — the first 7 are the original `contactsData.ts` mock's own contacts, unchanged; a few also backdate `created_at` so `ContactStatsView`'s `growth_30d_pct` has a real non-null number to show. Idempotent. |

**Status:** 🟢 Schema and API complete; the List view and the Details
page's General + Accounts tabs are wired to real data — `react-ts-app`'s
`pages/organizations/List.tsx` fetches `GET /api/v1/customers/` on mount
via `features/customers/customersSlice.ts` and pages forward/back
through DRF's own `next`/`previous` links (no hardcoded page-size
assumption — see that slice and `List.tsx`). `features/customers/
mapToOrgRow.ts` adapts each `Customer` into the table's existing
`OrgRow` shape so the mock-data-era table/popover components didn't need
to change — a few purely-presentational bits with no backend counterpart
(pill colors, avatar initials) are derived there rather than fabricated,
and the same pattern (`mapToAccountRow.ts`) does the same job for
`Account` -> `AccountRow` on the Details page's Accounts tab. Shared
formatting logic between the two mappers lives in `formatters.ts`.
`MetricsPanel` (the health/NPS/lifecycle summary banner) fetches
`GET /api/v1/customers/stats/` too (see `docs/API_CONTRACTS.md` -> that
endpoint) — every field on that banner, and on the Details page's own
metrics banner and PinnedAttributes/ActivityFeed panels, is real,
including ActivityFeed's own Overview tab (Domain/Location/Email/
Phone) — that used to show a fabricated `contact@<domain>` and a
phone number hardcoded identically for every organization/account
before `Customer.email`/`.phone` and `Account.address`/`.email`/
`.phone` existed.
Add/Edit/Churn/Archive Organization are wired too (a quick-add/edit form
covering identity, ownership, lifecycle stage, and contract dates only —
financials, product usage, and NPS/CSAT/health are meant to sync from
other systems later, not be hand-typed; Churn and Archive are separate
actions from the general edit form — see `is_archived` on the `Customer`
model and the detail endpoint's archive/unarchive note in
`docs/API_CONTRACTS.md`). Add/Edit Account (same product decision: name/
domain/owner/lifecycle stage/renewal date only) is wired too, on the
Accounts tab — `AccountFormModal.tsx`, same identity/ownership/lifecycle
scoping as Organization's own form. No Churn/Archive for Account —
Account has no `churn_date`/`is_archived` fields, and it wasn't asked for.

`Activity` (backing `ActivityFeed`'s "Activities" filter) is 🟢
frontend-wired: `ActivitiesTab.tsx` fetches real data through
`fetchActivitiesForCustomer`/`fetchActivitiesForAccount` in
`customersSlice.ts` instead of the old `ACTIVITIES_DATA`/
`ACCOUNT_ID_MAP` mock (that mock's `ACCOUNT_ID_MAP` only knew its own
string ids like 'acc-1', so every real account used to fall back to
the same hardcoded activities — the bug this wiring fixed).

`Email` (backing `ActivityFeed`'s "Emails" filter) is 🟢 frontend-wired
too: `EmailsTab.tsx` fetches real data through
`fetchEmailsForCustomer`/`fetchEmailsForAccount`, same pattern as
`Activity`.

`Task` (backing `ActivityFeed`'s "Tasks" filter) is 🟢 frontend-wired
too: `TasksTab.tsx` fetches real data through
`fetchTasksForCustomer`/`fetchTasksForAccount`, same pattern as
`Activity`/`Email` — the Overdue/This Week/Next Week/Later bucket is
computed client-side from `due_date` against today, not stored.

`Note` (backing `ActivityFeed`'s "Notes" filter) is 🟢 frontend-wired
too: `NotesTab.tsx` fetches real data through
`fetchNotesForCustomer`/`fetchNotesForAccount`, same pattern as
`Activity`/`Email`/`Task` — the link line now reflects a real per-note
count instead of a hardcoded "1 Links".

`Ticket` (backing `ActivityFeed`'s "Tickets" filter) is 🟢
frontend-wired too: `TicketsTab.tsx` fetches real data through
`fetchTicketsForCustomer`/`fetchTicketsForAccount`, same pattern as
the others — the flag icon now reflects real `priority` (previously
unwired regardless of the mock's own priority values) and the link
line a real per-ticket count.

`CalendarEvent` (backing `ActivityFeed`'s "Calendar Events" filter) is
🟡 backend-only, same stage the others were in before their own
frontend pass: model, the two scoped list endpoints above, and demo
seed data all exist, but `CalendarEventsTab.tsx` still reads the
`CALENDAR_EVENTS_DATA`/`ACCOUNT_ID_MAP` mock rather than these
endpoints.

The feed's one remaining filter (Slack) is still 100% mock — no
backend model yet.

Not built yet: Board view, nested Contacts, Search/Filter-by-column UI
(still decorative), and the frontend wiring for `CalendarEvent` above.

### Everything else

Not started yet. Per `docs/API_CONTRACTS.md`'s Status table: Contacts,
Pipelines, Dashboards, Copilot, Scenarios, Company Brain are all ⏳.

## Data Flow Summary

```
HTTP request
    │
    ▼
config/urls.py           ← routes /api/v1/<app>/... to <app>.urls
    │
    ▼
<app>/views.py            ← DRF view (function- or class-based)
    │
    ├── <app>/serializers.py   ← validate request / shape response
    ├── <app>/models.py        ← ORM query against Postgres
    │
    ▼
Response (JSON)            ← DRF Response, paginated if a list endpoint
```

> Auth landed with the `accounts` app — `DEFAULT_PERMISSION_CLASSES` is
> `IsAuthenticated`. New endpoints require a valid JWT unless they
> explicitly set `AllowAny` (only signup/login/token-refresh do). See
> `docs/API_CONTRACTS.md` → Conventions and `auth-flow.md`.

## Adding a New Feature — Checklist

See the `api-contracts`, `flow-docs`, and `testing` Claude Code skills for
the full workflow. Short version:

1. `python manage.py startapp <name>` — models mirror the matching
   frontend TS interfaces / mock data files. Runs inside the existing `web`
   container; no new Dockerfile/compose service needed for a normal
   Django app. Only add a new service to `docker-compose.yml` if the
   feature needs its own runtime process (a worker, a cache, ...).
2. Serializers + views + URLs, mounted at `/api/v1/<name>/`.
3. Update `docs/API_CONTRACTS.md` (Status table + new endpoint section).
4. Update this file's App List section with the new app.
5. Generate + apply migrations.
6. Add unit tests (`<app>/tests/test_*.py`), an integration test
   (`APITestCase`), and — if the feature completes a user-facing flow — an
   end-to-end test in `e2e/` (`LiveServerTestCase`).
7. Commit per the `commit-messages` skill.
