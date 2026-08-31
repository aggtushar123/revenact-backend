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
| Local Postgres | docker-compose (`db` service, `postgres:16-alpine`) |

## Top-Level Directory Map

```
revenact-backend/
├── .agents/workflows/          ← Workflow knowledge files (this file)
├── .claude/skills/             ← Claude Code skills (karpathy-guidelines,
│                                  commit-messages, flow-docs, api-contracts)
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
├── docker-compose.yml          ← Postgres for local dev
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

### Everything else

Not started yet. Per `docs/API_CONTRACTS.md`'s Status table: Auth,
Organizations, Accounts, Contacts, Pipelines, Dashboards, Copilot,
Scenarios, Company Brain are all ⏳.

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

> No auth is enforced yet — every endpoint is `AllowAny` until the auth
> feature (mirroring `react-ts-app`'s `authSlice.ts`) is ported. See
> `docs/API_CONTRACTS.md` → Conventions.

## Adding a New Feature — Checklist

See the `api-contracts` and `flow-docs` Claude Code skills for the full
workflow. Short version:

1. `python manage.py startapp <name>` — models mirror the matching
   frontend TS interfaces / mock data files.
2. Serializers + views + URLs, mounted at `/api/v1/<name>/`.
3. Update `docs/API_CONTRACTS.md` (Status table + new endpoint section).
4. Update this file's App List section with the new app.
5. Generate + apply migrations.
6. Commit per the `commit-messages` skill.
