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
`src/features/<domain>/`) gets its own Django app here
(`python manage.py startapp <domain>`), mounted under `/api/v1/<domain>/`.

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
| `views.py: CSMListCreateView` | `GET/POST /csms/` — admin-only, list/add CSMs in their own org |
| `views.py: CSMDetailView` | `GET/PATCH /csms/<id>/` — admin-only, edit/deactivate a CSM in their own org |
| `permissions.py: IsOrgAdmin` | Gates admin-only actions |

Full walkthrough: `auth-flow.md` in this same directory.

**Status:** ✅ Built (signup, login, logout, token refresh, own-profile
edit + password change, admin User Management for CSMs).

### Everything else

Not started yet. Per `docs/API_CONTRACTS.md`'s Status table: Organizations,
Contacts, Pipelines, Dashboards, Copilot, Scenarios, Company Brain are all ⏳.

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
