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
| Lint / format | [ruff](https://docs.astral.sh/ruff/) (`requirements-dev.txt`) |

## Project layout

```
revenact-backend/
├── config/              # Django project settings, root URLconf, WSGI/ASGI
│   ├── settings.py
│   └── urls.py           # /admin, /api/schema, /api/docs, /api/redoc, /api/v1/*
├── core/                 # Shared/infra app — health check, common utilities
├── services/             # Houses the `accounts` and `customers` Django apps,
│   ├── accounts/          #   plus cross-app business logic (services/email.py)
│   └── customers/         #   that isn't a serializer/view's job
├── docs/
│   └── API_CONTRACTS.md   # Running log of every endpoint, request/response shape, and decision
├── Dockerfile              # Containerizes the Django app itself (the `web` service)
├── docker-compose.yml    # web (this app) + db (Postgres) — the whole backend, dockerized
├── .env.example
├── requirements.txt
└── manage.py
```

As we build features, each one lands as its own Django app (e.g. `organizations/`,
`accounts/`, `auth/`, `pipelines/`) mounted under `/api/v1/` in `config/urls.py` —
mirroring the frontend's `src/pages/<domain>/` and `src/features/<domain>/` split.
They all run inside the same `web` container/process — this is one dockerized
service, not one container per app. If a future feature needs its own runtime
process (a worker, a cache, ...), add it as its own service in
`docker-compose.yml` alongside `web` and `db` — every backend service stays
dockerized, not just the database.

## Getting started

**Option A — fully Dockerized (recommended, needs Docker running):**

```bash
cp .env.example .env    # already done in this checkout; edit values if needed
docker compose up --build
```

Builds the `web` image, starts `db`, waits for its healthcheck, runs
migrations, then starts the dev server — all in one command. The API is
live at `http://localhost:8000/`. Run one-off commands (tests, migrations,
`createsuperuser`) with `docker compose exec web <command>`, e.g.
`docker compose exec web python manage.py createsuperuser`.

**Option B — local Python + Postgres:**

1. Database — either `docker compose up -d db` or a local Postgres install:
   ```bash
   psql -h localhost -U "$(whoami)" -d postgres <<'SQL'
   CREATE ROLE revenact LOGIN PASSWORD 'revenact' CREATEDB;
   CREATE DATABASE revenact OWNER revenact;
   SQL
   ```
   `CREATEDB` is required for a local install — `manage.py test` creates and
   drops its own `test_revenact` database on each run. (Docker's `db`
   service and CI's Postgres service both grant this by default, nothing
   to do there.)
2. Python environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements-dev.txt   # requirements.txt + ruff for local dev
   ```
3. Environment file:
   ```bash
   cp .env.example .env    # already done in this checkout; edit values if needed
   ```
4. Migrate and run:
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

## Scheduled work

One job needs to run on a timer:

```bash
python manage.py run_health_maintenance
```

It recalculates every `Customer.health_score` from the health rubric
(`services/customers/health.py`) and records that month's `HealthSnapshot`.
Both drift without anyone touching a customer row — the score counts days
since the last touch and open tickets, and the snapshots are what the Health
Overview's Movement tab charts.

**Run it daily.** It is idempotent: recalculation recomputes from scratch, and
the snapshot writes at most one row per customer per month and skips a month
already recorded. A tick firing twice, landing at an odd hour, or being missed
costs nothing.

`docker compose up scheduler` runs it in a loop for local/demo use. There is
deliberately **no task queue** in this project — celery plus a beat process and
a worker is a lot of infrastructure for one daily command, so the scheduler
service is a `sleep` loop and real deployments should point cron (or whatever
the platform offers) at the command directly. `--dry-run` reports what it would
do; `--org-email` limits it to one tenant.

`python manage.py recalculate_health` does the scores alone, without recording
a snapshot.

### Classifying interactions (costs money)

```bash
python manage.py classify_interactions --limit 300
```

Reads emails, calls and tickets that carry no AI classification yet and asks
Claude where each sits in the taxonomy (`services/customers/taxonomy.py`), which
is what fills the AI Trending Topics dashboard's Area / Category / Subcategory
charts. Every batch of 20 is a **real, paid** call through the same provider
Copilot uses, so this one is deliberately separate from the free health job
above: schedule it less often, and with `--limit`.

`--dry-run` counts the work without calling anything. It skips anything already
classified, so it's safe to re-run and won't overwrite a correction made in
admin; `--reclassify` redoes them anyway.

For a demo database, `python manage.py seed_demo_classifications --org-email ...`
fills the same fields from a keyword table instead — free, instant, and no API
key needed.

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
6. Unit, integration, and end-to-end tests are added for the feature (see
   the `testing` skill) — a feature isn't done without all three.

## Tests

```bash
python manage.py test
```

Every feature ships with three tiers of tests (see `.claude/skills/testing/SKILL.md`):

- **Unit** — one function/method in isolation (`<app>/tests/test_<unit>.py`).
- **Integration** — one endpoint through the real stack + test DB
  (`<app>/tests/test_views.py`, `APITestCase`).
- **End-to-end** — a full multi-step flow over real HTTP against a real
  live server + test DB, no browser (`e2e/`, `LiveServerTestCase`).

## Lint & format

```bash
ruff check .            # lint
ruff format .            # format
```

Both run in CI (see `.github/workflows/ci.yml`) and must pass before merge.
