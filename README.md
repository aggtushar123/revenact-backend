# Revenact Backend

Django + Django REST Framework API backend for **Revenact**, a customer-success
intelligence platform. This is the backend counterpart to the
[`react-ts-app`](../react-ts-app) frontend, built **feature by feature**
alongside it — each frontend feature gets a matching backend app, model set,
and documented API contract, in that order.

## Stack

| Concern | Choice |
|---|---|
| Language / framework | Python 3 + Django 5.2 |
| API layer | Django REST Framework |
| Database | PostgreSQL (via Django's built-in ORM — not Prisma, that's JS-only) |
| API docs | [drf-spectacular](https://drf-spectacular.readthedocs.io/) — auto-generated OpenAPI 3 schema, Swagger UI, and Redoc, always in sync with the actual code |
| CORS | django-cors-headers, scoped to the Vite dev server origin |
| Config | django-environ (`.env` file, see `.env.example`) |

## Project layout

```
revenact-backend/
├── config/              # Django project settings, root URLconf, WSGI/ASGI
│   ├── settings.py
│   └── urls.py           # /admin, /api/schema, /api/docs, /api/redoc, /api/v1/*
├── core/                 # Shared/infra app — health check, common utilities
├── docs/
│   └── API_CONTRACTS.md   # Running log of every endpoint, request/response shape, and decision
├── docker-compose.yml    # Postgres for local dev (alternative to a local install)
├── .env.example
├── requirements.txt
└── manage.py
```

As we build features, each one lands as its own Django app (e.g. `organizations/`,
`accounts/`, `auth/`, `pipelines/`) mounted under `/api/v1/` in `config/urls.py` —
mirroring the frontend's `src/pages/<domain>/` and `src/features/<domain>/` split.

## Getting started

### 1. Database

Two ways to get Postgres running locally — pick one.

**Option A — Docker (recommended if you have Docker running):**

```bash
docker compose up -d db
```

**Option B — a local Postgres install** (e.g. Homebrew's `postgresql@15`):

```bash
psql -h localhost -U "$(whoami)" -d postgres <<'SQL'
CREATE ROLE revenact LOGIN PASSWORD 'revenact';
CREATE DATABASE revenact OWNER revenact;
SQL
```

Either way, the default `.env` (copied from `.env.example`) already points at
`postgres://revenact:revenact@localhost:5432/revenact` — no changes needed
unless your setup differs.

### 2. Python environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Environment file

```bash
cp .env.example .env    # already done in this checkout; edit values if needed
```

### 4. Migrate and run

```bash
python manage.py migrate
python manage.py createsuperuser   # optional, for /admin access
python manage.py runserver 8000
```

The API is now live at `http://localhost:8000/`.

## Key URLs

| URL | What |
|---|---|
| `GET /api/v1/health/` | Liveness check — `{"status": "ok", ...}` |
| `GET /api/docs/` | **Swagger UI** — interactive API contract, try requests live |
| `GET /api/redoc/` | Redoc — clean read-only reference view of the same schema |
| `GET /api/schema/` | Raw OpenAPI 3 schema (JSON) |
| `/admin/` | Django admin (needs `createsuperuser` first) |

`docs/API_CONTRACTS.md` is the human-written companion to the auto-generated
schema above — it explains *why* endpoints look the way they do, notes
frontend↔backend shape differences, and tracks what's built vs. pending.

## Workflow for adding a feature

This backend grows in lockstep with the frontend, one feature at a time:

1. Frontend feature/change is described (e.g. "Organizations list + detail").
2. A Django app is created for it (`python manage.py startapp <name>`), with
   models mirroring the relevant frontend TypeScript interfaces /
   `*Data.ts` mock files.
3. Serializers + views + URLs are added, mounted under `/api/v1/<name>/`.
4. `docs/API_CONTRACTS.md` is updated with the new endpoints and any notable
   shape decisions.
5. Migrations are generated and applied.

## Tests

```bash
python manage.py test
```

(No app-specific tests yet — added alongside each feature as it's built.)
