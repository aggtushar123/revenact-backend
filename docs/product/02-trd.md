---
description: Technical Requirements Document for Revenact — architecture, stack, security, deployment and non-functional requirements.
---

# Revenact — Technical Requirements Document

Current as of 2026-09-18. Every value here was read from the code or config named
beside it. Where a number is configurable, the default is given.

Companion documents: [PRD](01-prd.md), [Backend Schema](05-backend-schema.md),
[App Flow](../../../react-ts-app/docs/04-app-flow.md),
[Implementation Plan](06-implementation-plan.md).

---

## 1. System shape

```
                    Browser (React 19 SPA)
                            │
                   HTTPS + WSS (one origin)
                            │
                    ┌───────▼────────┐
                    │  Caddy         │  TLS, static files, CSP, security headers
                    └───┬────────┬───┘
            /api /ws /admin      │ everything else
                        │        └──► built SPA from a volume
                    ┌───▼──────────┐
                    │ Daphne       │  HTTP + WebSocket, one ASGI process
                    │ Django 5.2   │
                    └─┬───┬────┬───┘
                      │   │    │
          ┌───────────┘   │    └────────────┐
    ┌─────▼─────┐   ┌─────▼─────┐    ┌──────▼───────┐
    │ Postgres  │   │  Redis    │    │ Anthropic    │
    │ 16        │   │ channels  │    │ API or AWS   │
    │           │   │ + cache   │    │ Bedrock      │
    └───────────┘   └───────────┘    └──────────────┘

    scheduler container ──► run_health_maintenance, daily
    mailsync container  ──► sync_mail + sync_tickets, every 10 min
```

Three repositories:

| Repository | Contains |
|---|---|
| `revenact-backend` | Django project, 15 apps, API, WebSocket consumers, management commands, this document set |
| `react-ts-app` (GitHub: `revenact-frontend`) | React SPA, design system, UI/UX and App Flow documents |
| `revenact-infra` | Terraform, cloud-init, production Compose file, Caddyfile, backup and deploy scripts |

---

## 2. Backend

### 2.1 Stack

| Concern | Choice | Version |
|---|---|---|
| Language / framework | Python 3.13, Django | 5.2.17 |
| API | Django REST Framework | 3.18.1 |
| Auth tokens | djangorestframework-simplejwt | 5.5.1 |
| Schema docs | drf-spectacular | 0.30.0 |
| WebSockets | Channels + channels-redis | 4.3.2 / 4.3.0 |
| ASGI server | Daphne | 4.2.3 |
| Database | PostgreSQL | 16 |
| Driver | psycopg | 3.3.5 |
| Model provider SDK | anthropic (plus boto3 for Bedrock) | 1.4.0 |
| Embeddings | sentence-transformers, `all-MiniLM-L6-v2`, CPU only | 6.0.1 |
| Password hashing | argon2-cffi (Argon2id) | 25.1.0 |
| Lint / format | ruff | 0.16.6 |

There is no Celery, RQ, Dramatiq or APScheduler anywhere in the tree. Outbound
HTTP uses the standard library `urllib`, not `requests`.

### 2.2 Application layout

`config/` holds settings, the root URLconf and the ASGI entry point. `core/` is
the infrastructure app (audit, logging, middleware, throttling, WebSocket auth,
health check). Every domain lives under `services/` as its own Django app:

`accounts`, `customers`, `copilot`, `metrics`, `knowledge`, `mail`, `connectors`,
`notifications`, `scenarios`, `webhooks`, `campaigns`, `custom_objects`,
`fx_rates`. Fourteen apps plus `core`, 86 migrations, 46 concrete models.

`services/` itself is a plain package, not an app. `accounts` and `customers` set
their app label explicitly so every existing `ForeignKey("accounts.User")` string
kept working when they moved under `services/`.

### 2.3 Request pipeline

```
request → RequestIDMiddleware (X-Request-ID, validated or generated)
        → SecurityMiddleware → CorsMiddleware → SessionMiddleware
        → CommonMiddleware → CsrfViewMiddleware → AuthenticationMiddleware
        → DRF view (JWTAuthentication, IsAuthenticated by default)
              ├─ permission class (capability check)
              ├─ scoping helper (which rows this user may see)
              ├─ serializer
              └─ ORM
        → JSON response, X-Request-ID echoed
```

### 2.4 Non-view business logic

Roughly 45 modules hold logic that is neither a view nor a serializer. The ones
worth knowing:

| Module | Responsibility |
|---|---|
| `services/customers/scoping.py` | The single definition of which customers and accounts a user may see. `SystemActor` lets a job run whole-org without impersonating a person |
| `services/accounts/hierarchy.py` | Org-chart maths: ancestors, subtree, combined scope, cycle check |
| `services/customers/personal.py` | Notes, tasks and tickets visibility |
| `services/mail/visibility.py` | Synced email visibility by mailbox owner and chain |
| `services/customers/health.py` | The health rubric |
| `services/customers/pulse.py` | Account and organisation pulse |
| `services/customers/contact.py` | What counts as a touch, used by both the rubric and Activity Tracking so the two screens cannot disagree |
| `services/customers/classification.py`, `taxonomy.py` | Model-driven sentiment and the three-level taxonomy |
| `services/metrics/registry.py` | 21 metrics defined once, each reading an existing rollup |
| `services/copilot/anthropic_client.py` | The one place a model is called |
| `services/copilot/retrieval.py`, `embeddings.py`, `context.py` | Grounding, retrieval and citations |

### 2.5 API conventions

- Base path `/api/v1/`, JSON only, no form-encoded bodies.
- Pagination: DRF `PageNumberPagination`, 25 per page, `{count, next, previous, results}`.
  Board-style endpoints (opportunities, risks, health) are deliberately unpaginated.
- Errors: DRF shape, `{"detail": "..."}` or `{"field": ["..."]}`.
- Identifiers: `BigAutoField` integers.
- Timestamps: ISO 8601 UTC, `USE_TZ = True`.
- Roughly 150 routes. Six are `AllowAny` by design: health, signup, login, token
  refresh, password reset request and confirm. Three more are unauthenticated
  callbacks with their own proof: the mail OAuth callback (signed state), the
  connector OAuth callback, and the inbound ticket webhook (shared token compared
  in constant time, or HMAC-SHA256 over the raw body).

Wire-level detail for every endpoint lives in [`docs/API_CONTRACTS.md`](../API_CONTRACTS.md)
and the generated OpenAPI schema at `/api/schema/`, `/api/docs/`, `/api/redoc/`.

### 2.6 Real time

| Route | Consumer | Gate | Pushes |
|---|---|---|---|
| `ws/copilot/sessions/<conversation_id>/` | `SessionConsumer` | The same `conversations_visible_to()` the REST endpoints use; otherwise close 4003 | `session.update` on every session event |
| `ws/notifications/` | `NotificationConsumer` | Authenticated user only; group per user | `notification.new` |

A WebSocket handshake cannot carry an Authorization header, so the access token
travels as `?token=`. `core/ws_auth.py` validates it with SimpleJWT before the
consumer runs. Under `manage.py test` the channel layer swaps to Channels'
in-memory implementation, so the suite never needs Redis.

---

## 3. Frontend

| Concern | Choice | Version |
|---|---|---|
| Framework | React + TypeScript | 19.2.8 |
| Build | Vite | 8.3.0 |
| State | Redux Toolkit, 27 slices | 2.12.0 |
| Routing | React Router | 7.18.4 |
| Styling | Tailwind CSS v4, tokens in `@theme` | 4.2.2 |
| Icons | lucide-react, exclusively | 1.43.0 |
| Charts | Recharts | 3.10.1 |
| Graph canvases | @xyflow/react | 12.11.6 |
| Validation | Zod, login form only | 4.5.4 |
| Tests | Vitest, Testing Library, jsdom | 4.1.10 |
| Browser automation for agents | @playwright/cli | 0.1.20 |

`src/lib/apiClient.ts` is the only HTTP path: it attaches the bearer token, and on
a 401 refreshes once, retries, then forces logout. `src/lib/socket.ts` opens
WebSockets against the same origin with the token as a query parameter and
reconnects every two seconds until told to stop.

The production image is a build stage only. It compiles the site and copies it
into a volume that Caddy serves; no Node process runs in production.

---

## 4. AI and model usage

| Aspect | Value |
|---|---|
| Provider | `COPILOT_LLM_PROVIDER`, `anthropic` or `bedrock` |
| Default model | `claude-sonnet-5` via the Anthropic API, or `BEDROCK_MODEL_ID` on Bedrock |
| Entry point | `services/copilot/anthropic_client.get_completion(...)`, the only call site |
| Purposes | copilot, headlines, classification, brief, proposals, facilitator, explain |
| Budget | `ModelBudget` per organisation per purpose per month, default 2,000,000 tokens |
| Over budget | `BudgetExceeded` becomes HTTP 429 in views and a command error in the CLI |
| Not configured | HTTP 503, never a silent failure |
| Provider failure | HTTP 502 |
| Audit | One `ModelCall` row per call: purpose, model, tokens, latency, outcome. No prompt or completion text is stored |
| Embeddings | Local CPU `all-MiniLM-L6-v2`, plain cosine similarity in Python. No pgvector |
| Retrieval | Company identified by mention or semantic match above 0.3, then recent records become snippets with citations |

Under `manage.py test` every provider key is blanked in settings so a test cannot
reach a live model.

---

## 5. Scheduled and background work

There is no queue. Work runs either in the request, or as an idempotent
management command on a timer.

| Command | Cadence | What it does |
|---|---|---|
| `run_health_maintenance` | Daily | Classify new interactions, recompute contact sentiment, recalculate health, write the month-end health snapshot, append a daily pulse dot, record month-end metrics, nudge stale questions |
| `sync_mail` | Every 10 minutes | Pull each connected mailbox, file what belongs to a customer |
| `sync_tickets` | Every 10 minutes | Pull each connected ticket source |
| `classify_interactions` | On demand and inside the daily job | Paid model calls, batched 20 at a time, `--limit` recommended |
| `recalculate_health` | On demand | Scores only, no snapshot |
| `nudge_open_questions` | On demand and inside the daily job | |
| `export_access_review` | Quarterly, for audit | CSV of users, roles, capabilities, last login |

Two behaviours to preserve: health snapshots are **monthly**, dated the last
completed month end, and a month already recorded is **skipped, never
overwritten**. Re-running the job any number of times is safe.

In-request background-ish work: outbound webhook delivery, scenario On-Event
runs and campaign sends all execute synchronously, the first two on
`transaction.on_commit`.

Twenty-five `seed_demo_*` commands exist for demo data. They are idempotent, and
orchestrated by a single `seed_demo` command. Re-running `seed_demo_customers`
writes fixture health scores back, so run `recalculate_health` afterwards.

---

## 6. Security

### 6.1 Authentication

| Control | Value |
|---|---|
| Password hashing | Argon2id, PBKDF2 retained for upgrade on login |
| Password policy | Minimum 12 characters plus similarity, common-password and numeric validators |
| Access token | 60 minutes |
| Refresh token | 7 days, rotated on use, previous one blacklisted |
| Idle session (admin) | 30 minutes, refreshed on each request |
| Reset token | 1 hour |
| MFA | Not implemented, formally excepted |

### 6.2 Rate limiting

Per-scope throttles backed by Redis: login 10/min per IP, login 5/min per hashed
email address, signup 5/hour, password reset 5/hour, token refresh 30/min. All
tunable by environment variable and disabled under test.

### 6.3 Authorisation

Three independent layers, each with exactly one definition in code:

1. **Tenant.** Every row hangs off an `Organisation`, directly or through a
   customer or account.
2. **Capability.** DRF permission classes map to the six capabilities.
3. **Record visibility.** `scoping.visible_customers`, `personal.visible_notes`,
   `visible_tasks`, `visible_tickets`, `mail.visibility.visible_emails`,
   `pipeline_visible_q`, and `copilot.conversations_visible_to`. The Copilot
   applies the same rules to retrieval, and withholds a reply that cites records
   the viewer may not read.

### 6.4 Transport and browser hardening

HSTS one year with subdomains, `X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY`, `Referrer-Policy: same-origin`, a restrictive
`Permissions-Policy`, secure and HttpOnly cookies, and a Content Security Policy
set by Caddy for the SPA: scripts from self only, styles inline plus Google
Fonts, images from self plus the logo provider, connections to this origin only,
`frame-ancestors 'none'`. TLS terminates at Caddy, so Django's own SSL redirect
is deliberately off and `SECURE_PROXY_SSL_HEADER` is set instead.

### 6.5 Logging and audit

`core/audit.py` writes an append-only `AuditEvent` and a structured log line for
every security-relevant event. The catalogue is in
[`docs/audit-events.md`](../audit-events.md). `core/logging.py` redacts bearer
tokens, JWT-shaped strings and password, token, secret, api_key and authorization
pairs before any handler sees the record, and emits one JSON object per line
outside debug. No request bodies or headers are logged.

### 6.6 Secrets

| Secret | At rest |
|---|---|
| Mailbox credentials (OAuth tokens, IMAP passwords) | Fernet-encrypted with `MAIL_TOKEN_KEY`, derived from `SECRET_KEY` when unset |
| Connector credentials | Same |
| User passwords | Argon2id |
| Webhook signing secret | **Plaintext column.** Known gap, `.soc2` DATA-02 |
| Bedrock credentials | Static AWS key in the environment file on the VM. Known gap, INFRA-03 |
| SPA tokens | `localStorage`. Known gap, AUTH-05 |

### 6.7 Outbound request safety

Mail and connector providers may only reach a fixed host allow-list. Outbound
webhooks go through an SSRF guard, HTTPS only, no redirects followed, a five
second timeout, and an HMAC-SHA256 signature header.

### 6.8 Compliance posture

Both repositories carry a `.soc2/` directory: configuration, a control map, an
exceptions register and scan reports. Scope is the Security and Confidentiality
trust service criteria. As of the 2026-09-15 gap report and the 2026-09-16
remediation, 30 of 44 applicable controls are met.

Open items worth planning around: no retention purge job (DATA-04), logs are not
shipped off the machine (LOG-04), no alerting (LOG-05), webhook secret plaintext
(DATA-02), tokens in `localStorage` (AUTH-05), static Bedrock key (INFRA-03), no
signed commits (CHG-08), no release tags (CHG-06), MFA excepted to 2027-03-31.
All three exceptions still name `[TODO: name]` as approver.

---

## 7. Deployment

### 7.1 Topology

One Azure `Standard_B2ls_v2` virtual machine, 2 vCPU and 4 GiB, 64 GiB SSD, in
South India, created by Terraform in `revenact-infra`. Cloud-init installs
Docker, clones both repositories with read-only deploy keys and brings the stack
up. Seven containers: `caddy`, `backend` (Daphne), `scheduler`, `mailsync`,
`frontend` (build then exit), `db`, `redis`.

Live at `https://revenact.20-219-7-222.sslip.io`. TLS is automatic Let's Encrypt
against the sslip.io hostname, so no DNS setup is needed. Cost is roughly
39 dollars a month with the machine always on.

Model calls go to Bedrock and bill to AWS, not to the Azure credits. Embeddings
run locally on CPU.

### 7.2 Continuous delivery

Every push to `main` of either application repository: CI runs tests, waits for
that same commit's `soc2-gates` run to pass, logs into Azure with OpenID Connect
as a federated identity scoped to `main` of those two repositories only, and runs
`deploy.sh` on the machine through `az vm run-command`. SSH stays closed to
GitHub. Deploys serialise on a file lock and record who triggered them. The
deploy step retries up to four times, because two repositories deploying at once
once raced each other.

### 7.3 CI gates

| Repository | Jobs |
|---|---|
| Backend | ruff lint and format check, migrate, full test suite on Postgres 16, 30 minute timeout. Then gitleaks, `pip-audit --strict`, semgrep (security-audit, secrets, OWASP top ten, Django), Trivy filesystem scan blocking on critical and high, and the SOC 2 scan and control-map drift check |
| Frontend | `npm ci`, lint, Vitest, build. Then gitleaks, `npm audit --omit=dev --audit-level=high`, semgrep (plus React and TypeScript rules), and the same SOC 2 scan |

All third-party actions are pinned to commit SHAs. Dependabot runs weekly with
grouped minor and patch updates. Branch protection is live.

> As of 2026-09-18 every CI job on both repositories fails within seconds because
> the GitHub account is out of Actions credits. The jobs never start; this is a
> billing state, not a code failure.

### 7.4 Backups

Daily at 13:00 IST a `pg_dump -Fc` plus the media directory is uploaded to a
private Azure storage account using the machine's managed identity. Blob
versioning and soft delete are on, blobs expire after 35 days, and the last week
also stays on the machine. A restore was tested on 2026-09-16; the next test is
due 2026-12-16.

---

## 8. Non-functional requirements

| Requirement | Target | How it is met today |
|---|---|---|
| Availability | Business hours for a shared team demo | Single VM, containers restart unless stopped, stack returns on boot. No redundancy by design |
| Recovery point | 24 hours | Nightly database and media backup |
| Recovery time | Under an hour | Documented restore procedure in the infra README, tested |
| Request latency | Interactive pages under a second on the demo book | No load testing has been done. The known heavy paths are the health endpoint (unpaginated by necessity) and any model call |
| Model latency | Seconds, visible to the user | Single-shot call, no streaming. Latency recorded per call in `ModelCall` |
| Data volume | Small: tens of customers, thousands of interactions | Postgres on one disk, plain B-tree indexes, no partitioning |
| Tenant isolation | Absolute | Every query goes through a scoping helper; two end-to-end tests assert cross-tenant 404s |
| Auditability | Every security-relevant change | Append-only audit table, 365 day retention |
| Browser support | Current Chrome, Safari, Firefox, Edge | ES2023 build target, no polyfills |
| Accessibility | WCAG 2.1 AA as the stated bar | Partially met, see the UI/UX document's conformance section |

---

## 9. Testing

| Tier | Backend | Frontend |
|---|---|---|
| Unit | `<app>/tests/test_<unit>.py` | Slice and helper tests under `src/features/` |
| Integration | `APITestCase` in `test_views.py`, real stack and test database | Component tests with Testing Library |
| End to end | `e2e/`, `LiveServerTestCase` over real HTTP, no browser | Route-level component tests; browser driving is available through `npm run pw` |

Roughly 1,509 test methods across 78 backend files, and 111 frontend test files.
Under test the backend swaps to the MD5 hasher (Argon2 pushed CI to 13 minutes),
an in-memory channel layer, a local-memory cache, disabled throttles, blanked
model keys and a temporary media root.

Rule inherited from experience: never chain a commit onto a grep of test output,
because grep exits zero when it *finds* the word FAILED. Gate on a match for
`^OK`. And never run two Django test suites at once; the second hangs on the
"test database exists" prompt unless `--noinput` is passed.

---

## 10. Known technical limitations

Deliberate, with the reasoning recorded:

- No task queue. Revisit when work needs retries or fan-out.
- FX rates are current only, with no history.
- Embeddings are cosine similarity in Python, not a vector index.
- Copilot is one model call per send: no streaming, no tool calling.
- Scenario "Schedule" trigger has no scheduler behind it.
- Surveys have no per-respondent or multi-question model.
- Notifications cover invites, hand-offs, owner assignment and questions only.
  Task, ticket and pipeline assignees are still plain text.
- Only five of the thirteen connector providers have implementations.
- Outbound webhooks support one event.

Stale documentation that contradicts the code, and should be fixed rather than
trusted: both repositories' `.agents/workflows/repo-architecture.md`, the backend
`auth-flow.md` (references `/csms/` and `IsOrgAdmin`, which no longer exist), the
frontend `README.md` (claims the app runs on in-memory fixtures), and several
status rows in `docs/API_CONTRACTS.md`.
