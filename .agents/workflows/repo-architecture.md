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
| `models.py: Customer` | Full `tableData.ts`-matching schema — identity/provenance, lifecycle/health, dates, financials, product/usage, churn |
| `models.py: Account` | One-to-many under `Customer` (`customer` FK, `related_name="accounts"`) — a named sub-account with its own health/pulse/NPS/CSAT. Mirrors `accountsData.ts`'s `AccountRow`; reuses `Customer`'s LifecycleStage/AIPulseScore choices and health thresholds rather than redefining them. |
| `views.py: CustomerListCreateView` | `GET/POST /customers/` — any authenticated user in the org (no admin gate, unlike User Management) |
| `views.py: CustomerDetailView` | `GET/PATCH /customers/<id>/` — same org only, 404 outside it |
| `views.py: CustomerStatsView` | `GET /customers/stats/` — Health/NPS/Lifecycle rollups for MetricsPanel |
| `views.py: AccountListView` | `GET /customers/<customer_id>/accounts/` — read-only, 404 (not empty list) for a customer_id outside the caller's org |
| `management/commands/seed_demo_customers.py` | Dev-only: seeds an org with the tableData.ts mock's 14 companies — `python manage.py seed_demo_customers --org-email <admin email>`. Idempotent. |
| `management/commands/seed_demo_accounts.py` | Dev-only: seeds Account rows (from accountsData.ts) under existing demo Customers — run after seed_demo_customers. Idempotent. |

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
metrics banner and PinnedAttributes/ActivityFeed panels, is real.
Add/Edit/Churn/Archive Organization are wired too (a quick-add/edit form
covering identity, ownership, lifecycle stage, and contract dates only —
financials, product usage, and NPS/CSAT/health are meant to sync from
other systems later, not be hand-typed; Churn and Archive are separate
actions from the general edit form — see `is_archived` on the `Customer`
model and the detail endpoint's archive/unarchive note in
`docs/API_CONTRACTS.md`).

Not built yet: Board view, nested Contacts, Add/Edit Account UI (Account
is read-only from the API so far — see above), and Search/Filter-by-
column UI (still decorative).

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
