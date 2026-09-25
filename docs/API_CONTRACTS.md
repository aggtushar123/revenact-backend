# API Contracts

Running log of every endpoint this backend exposes, why it's shaped the way
it is, and how it maps to the `react-ts-app` frontend. Updated every time a
new feature is ported from frontend → backend.

The **machine-readable, always-current** version of this contract is the
OpenAPI schema, generated straight from the code:

- Swagger UI (interactive): `http://localhost:8000/api/docs/`
- Redoc (reference view): `http://localhost:8000/api/redoc/`
- Raw schema (JSON): `http://localhost:8000/api/schema/`

This document is the **narrative companion** — it explains decisions the
schema alone can't (why a field is shaped this way, what changed and when,
what's intentionally not built yet).

---

## Conventions

- **Base path**: all feature endpoints live under `/api/v1/`.
- **Format**: JSON in, JSON out. No form-encoded bodies.
- **Pagination**: DRF's `PageNumberPagination`, 25 items/page by default.
  List responses look like:
  ```json
  { "count": 142, "next": "...", "previous": null, "results": [...] }
  ```
- **Auth**: JWT via `djangorestframework-simplejwt`. `DEFAULT_PERMISSION_CLASSES`
  is `IsAuthenticated` — every endpoint requires a valid `Authorization:
  Bearer <access token>` header unless it explicitly sets `AllowAny` (signup,
  login, token refresh, and `core`'s health check). Send the access token on
  every authenticated request; refresh it via `/api/v1/auth/token/refresh/`
  when it expires (60 min lifetime; refresh tokens last 7 days).
- **Errors**: standard DRF error shape, e.g.
  `{"detail": "..."}` or field-level `{"field_name": ["error message"]}`.
- **IDs**: Postgres auto-incrementing integers (`BigAutoField`) unless a
  feature has a specific reason to use something else (documented inline
  when it happens).
- **Timestamps**: ISO 8601, UTC, via Django's `USE_TZ = True`.

## Naming mapping (frontend → backend)

The frontend's `src/pages/<domain>/` and `src/features/<domain>/` folders
map to one Django app per domain, mounted at `/api/v1/<domain>/`. Frontend
mock data files (e.g. `tableData.ts`, `activityData.ts`) become the seed/shape
reference for that app's models — not copied verbatim, but used to derive
field names and types so the API contract matches what the UI already
expects.

---

## Status

| Frontend feature | Backend app | Status |
|---|---|---|
| — (infra) | `core` | ✅ Built — health check only |
| Auth (`authSlice.ts`, `Login.tsx`) | `accounts` | ✅ Built — signup, login, logout, token refresh |
| User Profile / User Management | `accounts` | ✅ Built — own profile (`/me/`), change password, admin list/add/edit/deactivate CSMs (`/csms/`) |
| Organizations (list/board/detail) | `customers` | 🟢 Full `tableData.ts` schema built, API-complete — see below. List view, MetricsPanel, Add/Edit/Churn/Archive, and the Details page's General tab all fetch real data. Board, nested Contacts not started. |
| Accounts (standalone `/accounts/list` page, Details page's Accounts tab) | `customers` (`Account` model) | 🟢 Model + full CRUD built, one-to-many under `Customer` — see below. A global paginated+searchable list (`AccountListView`, GET-only — Add/Edit reuse the nested endpoints below, see that view's own docstring), an aggregate `AccountStatsView` (Health/NPS/Lifecycle rollups, same shape as `CustomerStatsView`) backing the standalone page's own MetricsPanel, plus the nested per-Customer list-create/detail endpoints. Both the standalone list page and the Details page's own Accounts tab fetch/display real accounts and have Add/Edit wired (`createAccount`/`updateAccount`/`fetchAllAccounts`/`fetchAccountStats` in `features/customers/customersSlice.ts`, `AccountFormModal.tsx`). Churn/Archive for Account don't exist yet — not asked for, and Account has no `churn_date`/`is_archived` fields to back them. No Delete either — not asked for, matching the nested `AccountDetailView`'s own PATCH-only scope. |
| Activities (`ActivityFeed`'s "Activities" filter) | `customers` (`Activity` model) | 🟢 Read-only, API-complete — see below. Model + two scoped list endpoints (per-Customer, per-Account) exist, are seeded, and `ActivitiesTab.tsx` fetches real data through `fetchActivitiesForCustomer`/`fetchActivitiesForAccount`. No create/update endpoint yet. |
| Emails (`ActivityFeed`'s "Emails" filter) | `customers` (`Email` model) | 🟢 Read-only, API-complete — see below. `EmailsTab.tsx` fetches real data through `fetchEmailsForCustomer`/`fetchEmailsForAccount`. No create/update endpoint yet. |
| Tasks (`ActivityFeed`'s "Tasks" filter) | `customers` (`Task` model) | 🟢 Read-only, API-complete — see below. `TasksTab.tsx` fetches real data through `fetchTasksForCustomer`/`fetchTasksForAccount`; the Overdue/This Week/Next Week/Later bucket is computed client-side from `due_date`. No create/update endpoint yet. |
| Notes (`ActivityFeed`'s "Notes" filter) | `customers` (`Note` model) | 🟢 Read-only, API-complete — see below. `NotesTab.tsx` fetches real data through `fetchNotesForCustomer`/`fetchNotesForAccount`; the link line now reflects a real per-note count. No create/update endpoint yet. |
| Tickets (`ActivityFeed`'s "Tickets" filter) | `customers` (`Ticket` model) | 🟢 Read-only, API-complete — see below. `TicketsTab.tsx` fetches real data through `fetchTicketsForCustomer`/`fetchTicketsForAccount`; the flag icon now reflects real priority and the link line a real per-ticket count. No create/update endpoint yet. |
| Calendar Events (`ActivityFeed`'s "Calendar Events" filter) | `customers` (`CalendarEvent` model) | 🟡 Backend built, read-only — see below. Model + two scoped list endpoints (per-Customer, per-Account) exist and are seeded; frontend still reads the `CALENDAR_EVENTS_DATA`/`ACCOUNT_ID_MAP` mock in `activityData.ts`/`accountActivityData.ts`, not yet wired to these endpoints. |
| Contacts (standalone `/contacts/list` page, Organization/Account Details' Contacts tabs) | `customers` (`Contact` model) | 🟢 Full CRUD, API-complete — see below. Global paginated+searchable list (`ContactListView`/`ContactStatsView`), two scoped list-create endpoints (per-Customer, per-Account), and a flat `ContactDetailView` (GET/PATCH/DELETE by id, regardless of parent) exist and are seeded; the standalone list page's Add/Edit/Delete are wired to them. |
| Pipelines (standalone board — "Opportunities" and "Risks" tabs) | `customers` (`Opportunity`, `Risk` models) | 🟢 Full CRUD, API-complete — see below. Both tabs have the same shape: a global unpaginated list (`OpportunityListView`/`RiskListView`), two scoped list-create endpoints (per-Customer, per-Account), and a flat detail view (GET/PATCH/DELETE by id). Both are seeded; the board's own Add/Edit/Delete/drag-and-drop are wired to both tabs. |
| Surveys (`ActivityFeed`'s "Surveys" filter, standalone `/surveys` page) | `customers` (`Survey` model) | 🟢 Full CRUD, API-complete — see below. Same shape as Pipelines: a global unpaginated list (`SurveyListView`), two scoped list-create endpoints (per-Customer, per-Account), and a flat detail view (GET/PATCH/DELETE by id). Responding syncs the score onto the parent's own `nps_score`/`csat_score`/`ces_percentage`. CES is Customer-only (Account has no `ces_percentage`). No email delivery — logging only. |
| Canvas (sidebar gallery `/canvas`, "Canvas List" tab on Details pages) | `customers` (`Canvas` model) | 🟢 Full CRUD, API-complete — see below. Same shape as Pipelines/Surveys: a global unpaginated list (`CanvasListView`), two scoped list-create endpoints (per-Customer, per-Account), and a flat detail view (GET/PATCH/DELETE by id). `nodes`/`edges` round-trip verbatim (React Flow's own shape); a node references a real `Contact` by id rather than snapshotting its name/role/sentiment. |
| Headlines (`ActivityFeed`'s "Headlines" sub-tab on both Details pages) | `customers` (`Headline` model) | 🟢 API-complete — see below. Model + two scoped list-create endpoints (per-Customer, per-Account), a flat detail view (GET/PATCH/DELETE by id), and a real generation endpoint that summarises the parent's own Notes/Emails/Tickets/Activities through `services.copilot`'s Anthropic client. Seeded. `HeadlinesTab.tsx` fetches real data through `fetchHeadlinesForCustomer`/`fetchHeadlinesForAccount`; the group pill and the "Data sources" footer now reflect real values rather than stored/decorative text. |
| Dashboards — Health Overview | `customers` | 🟢 All five tabs (Triage/Divergence/Movement/Renewal Date/Controls) run on one `GET /api/v1/customers/health/` — see that endpoint and `HealthSnapshot` below. |
| Dashboards — Customer Overview | `customers` | 🟢 Runs on `GET /api/v1/customers/overview/` — composition, concentration, cohorts and churn reasons. The one dashboard that counts churned customers. See below. |
| Dashboards — Activity Tracking | `customers` | 🟢 Runs on `GET /api/v1/customers/activity/` — coverage, cadence and follow-through. See below. |
| Dashboards — Revenue Forecast | `customers` | 🟢 Runs on `GET /api/v1/customers/forecast/` — the ARR bridge, with churn weighted by the shared rule in `churn.py`. See below. |
| Dashboards — Usage Overview | `customers` | 🟢 Controls tab runs on `GET /api/v1/customers/usage/` — seat utilisation, shelfware and expansion capacity. See below. |
| Products (catalogue behind `primary_product`) | `customers` (`Product` model) | 🟢 Full CRUD, API-complete — see below. `GET/POST /api/v1/products/` and `GET/PATCH/DELETE /api/v1/products/<id>/`, mounted at their own top-level prefix (a product is org configuration, and `/customers/products/` already means the Product Usage rollup). Case-insensitively unique per organisation, deliberately **not** scoped by ownership, retirable via `is_active`, and un-deletable while customers are on it. Reads for any member, writes gated on `manage_org_settings`. Managed from Settings > Products (`ProductsPage.tsx`). |
| Metric layer (`/api/v1/metrics/`) | `metrics` | 🟢 Every headline number defined once (`services/metrics/registry.py`), read through the same rollups the dashboards draw, whole-organisation via `SystemActor`, with month-end history in `MetricSnapshot` recorded by `run_health_maintenance`. Gated on `view_all_accounts`. Phases 1–2 of the company-brain work: the Brain dashboard's Business metrics panel (`MetricLayerPanel.tsx`) reads it, and `<key>/by/<dimension>/` + `signals/` are the "why" layer — cuts by owner/product/segment/lifecycle and the material moves with their drivers. See below. |
| Dashboards — Product Usage | `customers` | 🟢 Runs on `GET /api/v1/customers/products/` — one row per product: ARR led, health mix, utilisation, satisfaction, support burden and churn. Attribution is by `primary_product` only, and the response says so. See below. |
| Dashboards — Ticket Overview | `customers` | 🟢 Controls tab runs on `GET /api/v1/tickets/stats/` (`TicketStatsView`), with `Connector` behind its origin chart, plus `open_count`/`oldest_open_days` for the Overview's own KPIs. See below. |
| Dashboards — AI Trending Topics | `customers` | 🟢 Controls tab runs on `GET /api/v1/interactions/stats/` — see below. Its other six sub-tabs are the filter bar, not separate screens. |
| Dashboard Overview — "Needs attention" | `attention` | 🟢 `GET /api/v1/dashboard/attention/` — five kinds of item (renewal, risk, going_quiet, support, anomaly), scored `at_stake × urgency`, twice-filtered like every other dashboard. Per-user snoozing (`POST`/`DELETE …/snooze/`) — see below. |
| Copilot (`/copilot`) | `copilot`, `customers` | 🟡 Real Anthropic Claude chat, grounded in real data — see the `copilot` app's own section below. `POST .../messages/` makes a real, synchronous call to Claude (no task queue, no streaming), with each request's system prompt grounded in a real-data digest of the *caller's own owned* book of business (health/NPS/lifecycle, top at-risk customers, open opportunity/risk/ticket counts), **plus real retrieved content** — a company identified from the question (an exact name match first, then a real local-embeddings semantic fallback for a company described but not named — embedding its name plus a real hand-entered `industry` when one's been set, e.g. "that video conferencing account" finding Zoom, see `services/copilot/embeddings.py`; no pgvector, plain Python cosine similarity, a documented real limitation once `industry` is blank and the name is also a common word) gets its own recent real Emails/Notes/open Tickets/Activities, relevance-ranked against the question (`services/copilot/retrieval.py`); otherwise falls back to "one of your own top at-risk companies" — not the whole tenant's, same "My" framing as Cockpit's own. Conversations are private per-user. Requires the selected provider's own real credentials (`COPILOT_LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY`, or `=bedrock` + real AWS credentials/`BEDROCK_MODEL_ID` — see the `copilot` app's own section below); returns a clear `503` without them rather than a fake answer. First real consumer of `Organisation.ai_agent_enabled`/`ai_agent_tone`. No per-skill tool-calling/function execution — the "Built-in Skills" cards just prefill the compose input. The page's own Cockpit tab is real too now — `GET /api/v1/cockpit/summary/` and `GET /api/v1/tasks/?mine=true` (see the `customers` app's own section) back "My Portfolio Summary"/"Renewals"/"My Tasks", scoped to the caller's own owned book of business; replaces what used to be fixed literal numbers and an entirely separate local mock Redux task list. |
| Scenarios (builder, `/scenarios`) | `scenarios` | 🟡 Full CRUD + a real (deliberately limited) execution engine — see below. `nodes`/`edges` round-trip verbatim; "Run Now" and the On Event → "Creation of new entity" trigger actually execute Send Email/Create Task/Set Attribute/Churn Entity/Condition/Filter against a real Customer. Every other node type (Assign Playbook, Slack Message, Create Pipeline, MS Teams, Send Survey, Schedule) stays a frontend-only mockup; hitting one during a run just logs "skipped". Only `apply_to === "organizations"` scenarios are runnable in v1. |
| Campaigns (`/campaigns`) | `campaigns` | 🟡 Full CRUD + a real (deliberately limited) send — see below. `POST .../send/` really emails every recipient via the same `send_mail` plumbing as Scenarios' own "Send Email," synchronously (no task queue), and creates one real `customers.Email` row per successful send so it shows up in that recipient's own parent's Activity Feed. A recipient with no email on file is logged as skipped, never fatal. No scheduled sends, no templates beyond plain text, no open/click tracking (plain SMTP, no ESP webhooks). |
| Communications (`/communications`) | `customers` | 🟢 Built — the queue of what is waiting on the caller, merged across Email, Question, Ticket and Call. Two endpoints, no new model. See below. |
| Company Brain | — | ⏳ Not started |
| Settings > Currency / Global Presets / AI Agent | `accounts` (`Organisation` model), `customers` (`Customer.currency`), `fx_rates` | 🟢 `Organisation.currency` actually controls money formatting everywhere now (every `$` in the frontend is currency-aware) and, since Tier 1, each `Customer` can carry its *own* contract currency independent of the org's, with an admin-maintained `fx_rates` table converting cross-currency rollups (see `GET /api/v1/customers/stats/`'s `unconverted_count`) — see the `fx_rates` app's own section below. `ai_agent_enabled`/`ai_agent_tone` are now genuinely read by the `copilot` app's own `SendMessageView` (see that app's own section below) — disabling AI Agent really blocks Copilot sends, and tone really changes the system prompt; `default_lifecycle_stage` is real end to end — it's what the standalone Add Organization flow actually pre-fills. |
| Settings > Entity Uploads | — (no new endpoint) | 🟢 CSV bulk-create for Organizations, one real `POST /customers/` per mapped row via the existing `CustomerListCreateView` — see react-ts-app's EntityUploadsPage.tsx. Accounts/Contacts import not built yet. |
| Settings > Webhooks | `webhooks` | 🟡 Full CRUD + real delivery for one event (`customer.created`) — see below. Admin-only both ways (unlike every other settings tab above, which any authenticated user can view). |
| Settings > Activities / Connect Widget | — | ⏳ Not started — Activities has no create/update endpoint to configure types *for* (Activity is fully read-only, seed-data only); Connect Widget would be a new public, unauthenticated surface this app doesn't have anywhere else, and needs a product decision on what a submission actually does before it's buildable as more than a generated snippet. |

---

## `core` — infrastructure

### `GET /api/v1/health/`

Liveness check. No auth, no params.

**Response `200`**
```json
{
  "status": "ok",
  "service": "revenact-backend",
  "time": "2026-08-31T12:30:16.307241+00:00"
}
```

---

## `accounts` — Auth (`authSlice.ts`, `Login.tsx`)

Mirrors: `src/pages/auth/Login.tsx`, `src/features/auth/authSlice.ts`,
`src/features/auth/loginSchema.ts`.

The Django app is named `accounts`, not `auth` — `auth` is already taken by
`django.contrib.auth`'s app label and can't be reused. It's mounted at
`/api/v1/auth/` (not `/api/v1/accounts/`) so the URL still matches the
frontend's `features/auth/` domain. See the `flow-docs` skill's
`auth-flow.md` for the full walkthrough of who can do what.

### Models

- `Organisation` — `name`, `slug` (auto-generated from `name`, unique),
  `currency` (`USD`/`EUR`/`GBP`/`INR`/`CAD`/`AUD`/`JPY`, default `USD` —
  display-only for now, see this model's own docstring on why nothing
  renders a symbol off it yet), `default_lifecycle_stage` (blank, or one
  of `customers.Customer.LifecycleStage`'s own values — the standalone
  Add Organization flow's own tenant-wide default, see
  `OrganisationSettingsView`), `created_at`. The tenant. Created only via
  signup; `currency`/`default_lifecycle_stage` are the only two fields
  ever changed after that (via `OrganisationSettingsView` below).
- `User` (custom `AUTH_USER_MODEL`, replaces Django's default) — `email`
  (unique, `USERNAME_FIELD`), `name`, `organisation` (FK, **null only for
  platform-staff superusers** — every org admin/CSM has one), `role`
  (`admin` | `csm`), `is_active`, `is_staff`, `date_joined`. No `avatar`
  field — it's derived at serialization time (see below), not stored.

### Auth model

- **One organisation, two roles.** `admin` = the user created at signup
  (owns the org). `csm` = added by an admin afterward. No "multiple admins"
  or finer-grained permissions yet — just enough to gate the one
  admin-only action that exists so far (adding a CSM).
- **CSM provisioning is admin-direct, not invite-based.** The admin sets
  the CSM's email + initial password themselves in the request body; the
  CSM logs in with exactly what the admin gave them. No email sending
  involved (deliberately, for now).
- **`avatar` is computed, not stored** — `https://i.pravatar.cc/150?u=<email>`,
  matching the placeholder scheme the frontend's dummy users already used
  in `authSlice.ts`. Swap for a real upload field if that's ever needed.

### `POST /api/v1/auth/signup/`

Auth: `AllowAny`. Creates an `Organisation` + its first `User` (`role=admin`)
in one call, and logs them in immediately (same shape as login) so the
frontend doesn't need a separate "now log in" step after signup.

**Request**
```json
{ "organisation_name": "Acme Inc", "name": "Alice Admin", "email": "alice@acme.io", "password": "supersecret1" }
```

**Response `201`**
```json
{
  "user": {
    "id": 1, "email": "alice@acme.io", "name": "Alice Admin",
    "avatar": "https://i.pravatar.cc/150?u=alice@acme.io",
    "role": "admin",
    "organisation": { "id": 1, "name": "Acme Inc", "slug": "acme-inc" }
  },
  "access": "<jwt>", "refresh": "<jwt>"
}
```

`400` if the email is already registered (field error: `{"email": ["A user with this email already exists."]}`).

### `POST /api/v1/auth/login/`

Auth: `AllowAny`. Works for any user — org admin or CSM — same credentials
they were created with.

**Request** `{ "email": "...", "password": "..." }`
**Response `200`** — same shape as signup's response (`user`, `access`,
`refresh`). `401` on bad credentials:
`{"detail": "No active account found with the given credentials"}`.

### `POST /api/v1/auth/login/mfa/`

The second half of a password login for someone with a second factor. When
the person has an authenticator enrolled, `POST /auth/login/` answers
`200 { "mfa_required": true, "mfa_token": "<5-minute challenge>" }` and no
tokens. Send `{ mfa_token, code }` here (a six-digit app code or a recovery
code) to receive the usual `{ access, refresh, user }`. Tokens minted this way
carry `mfa: true`, which survives refresh and is what `/api/v1/platform/`
requires. `401` with `MFA_CODE_INVALID`, `MFA_CODE_REUSED` (a code works
once), `MFA_CHALLENGE_EXPIRED` or `MFA_CHALLENGE_INVALID`.

### `POST /api/v1/auth/me/mfa/{setup,confirm,disable}/`

Auth: `IsAuthenticated`. `setup/` → `{ secret, otpauth_uri }` (nothing is on
yet; calling it again replaces an unconfirmed secret). `confirm/ { code }` →
`{ recovery_codes: [8] }`, shown once and stored hashed. `disable/ { code }`
needs a current code. `/auth/me/` reports `mfa_enrolled`.

### `POST /api/v1/auth/organisation/owner/`

`{ user_id }`. Only the current owner may hand the organisation to another
active member, who is given the built-in Admin role. `403 NOT_OWNER`,
`400 NOT_A_MEMBER | ALREADY_OWNER`. `/auth/organisation/` carries `owner`
`{id, name, email}` and `/auth/me/` carries `is_owner`. Another administrator
cannot deactivate or re-role the owner (`400` from `PATCH /auth/users/<id>/`).

### `POST /api/v1/auth/token/refresh/`

Auth: `AllowAny`. `{"refresh": "<jwt>"}` → `{"access": "<jwt>"}`. Standard
simplejwt `TokenRefreshView`, unmodified.

### `POST /api/v1/auth/logout/`

Auth: **`IsAuthenticated`** — you need a valid access token to log out, but
the call always succeeds regardless of the refresh token's own state.

**Request** `{ "refresh": "<jwt>" }` → blacklists that refresh token (via
`rest_framework_simplejwt.token_blacklist`) so it can no longer be used at
`/token/refresh/`. `401` if unauthenticated; an invalid, expired, or
already-blacklisted refresh token in the body is **not** an error — still
`205` (idempotent: safe to call twice with the same token).

**Response `205 Reset Content`** — empty body.

Only the refresh token is revoked. The current access token (60 min
lifetime) keeps working until it naturally expires — simplejwt doesn't
track individual access tokens for revocation, only refresh tokens via
this blacklist.

### `GET /api/v1/auth/me/`, `PATCH /api/v1/auth/me/`

Auth: `IsAuthenticated` — any user, admin or CSM, editing themselves.

GET returns the same shape as the `user` object in login/signup, plus
`sign_in_providers` (e.g. `["google"]`), which only your own profile carries.
Every user object also has `has_password` (false for someone who only ever
signed in with a provider, so Account settings hides the password form) and
`tour_completed_at` (null until the first-run tour is finished or skipped).

PATCH accepts `{ "name": "..." }` and `{ "tour_completed": true | false }` —
the server stamps `tour_completed_at` itself, and `false` clears it ("show me
the tour again"). `email` and `role` are read-only here; sending them is
silently ignored (not an error), not written.

**Response `200`** (both) — the (possibly updated) profile.

### `GET /api/v1/auth/organisation/`, `PATCH /api/v1/auth/organisation/`

**Global configuration (Settings › Data).** `name` is editable (PATCH,
`manage_org_settings`; `slug` stays read-only). `global_attributes`
maps each headline concept to the customer field that stands for it —
`arr`, `mrr`, `renewal_date`, `joined_date` — read as the stored
mapping over the defaults (`arr_billed_at_account`, `arr_billed_at_account`,
`renewal_date`, `joined_date`); `global_attribute_choices` lists what
each may be. PATCH accepts a partial mapping and refuses unknown keys or
a field that cannot stand for the concept (`400`).

Backs Settings > Currency, Global Presets, and AI Agent (react-ts-app's
`src/pages/settings/CurrencyPage.tsx`/`GlobalPresetsPage.tsx`/
`AIAgentPage.tsx`).

Auth: GET — `IsAuthenticated` (any user, admin or CSM — all three
settings pages show a read-only view to a CSM). PATCH — `IsOrgAdmin` on
top (`403` for a CSM), same gate as `/auth/csms/`'s own.

PATCH accepts any of `{ "currency": "EUR", "default_lifecycle_stage":
"adoption", "ai_agent_enabled": false, "ai_agent_tone": "friendly" }`
(any subset — each settings page only ever sends its own field(s)) —
`name`/`slug` are read-only here; sending them is silently ignored, not
written (same "can't smuggle an edit to a read-only field through"
convention as `/auth/me/`'s own). `ai_agent_enabled`/`ai_agent_tone` are
genuinely stored and shown back, same as `currency`, and are now
genuinely read by the `copilot` app's own `SendMessageView` — see that
app's own section below.

**Response `200`** (both)
```json
{
  "id": 3,
  "name": "Acme Inc",
  "slug": "acme-inc",
  "currency": "EUR",
  "currency_display": "Euro (€)",
  "default_lifecycle_stage": "adoption",
  "ai_agent_enabled": true,
  "ai_agent_tone": "friendly",
  "ai_agent_tone_display": "Friendly"
}
```

### `POST /api/v1/auth/me/change-password/`

Auth: `IsAuthenticated`. Self-service password change — requires proving
you know the current one.

**Request** `{ "current_password": "...", "new_password": "..." }`
(`new_password` min 8 chars, same as everywhere else). `400` with
`{"current_password": ["Current password is incorrect."]}` if it doesn't
match.

**Response `200`** — empty body.

Does **not** invalidate existing sessions/tokens — unlike an admin
deactivating you (below), which does. Changing your own password from an
active session doesn't force that same session to re-authenticate.

### `POST /api/v1/auth/password-reset/`

Auth: `AllowAny`. Step 1 of the forgot-password flow (`ForgotPassword.tsx`).

**Request** `{ "email": "..." }`
**Response `200`** — always, regardless of whether the email matches a
user: `{"detail": "If an account exists for that email, we've sent a
password reset link."}`. This is deliberate — a `404`/different message
for an unknown email would let the endpoint be used to enumerate
registered addresses.

When the email does match, sends a plaintext email (`django.core.mail`) to
that address with a link to `{FRONTEND_URL}/reset-password?uid=...&token=...`.
Uses Django's built-in `django.contrib.auth.tokens.default_token_generator`
— the token is tied to the user's pk, current password hash, and
last_login, and expires after `PASSWORD_RESET_TIMEOUT` (1 hour); nothing is
stored server-side for this step.

`EMAIL_BACKEND` falls back to Django's console backend (prints to the
`runserver` terminal) when `EMAIL_HOST_USER`/`EMAIL_HOST_PASSWORD` aren't
set in `.env` — see `.env.example`. `400` if `email` is missing/malformed.

### `POST /api/v1/auth/password-reset/confirm/`

Auth: `AllowAny`. Step 2 — consumes the `uid`/`token` pair from the emailed
link (`ResetPassword.tsx`, reading them off its own URL's query string).

**Request** `{ "uid": "...", "token": "...", "new_password": "..." }`
(`new_password` min 8 chars, same as everywhere else).
**Response `200`** — `{"detail": "Your password has been reset."}`. Does
**not** log the caller in — they sign in at `/login/` with the new
password same as any other time.

`400` for any invalid case — bad/unknown uid, wrong token, or an expired
one — all collapsed to the same generic
`{"non_field_errors": ["This reset link is invalid or has expired."]}`,
again to avoid leaking which case it was. A token stops working the moment
it's used once, since the generator's hash includes the password field
that the reset itself just changed.

### Sign in with Google / Microsoft — `/api/v1/auth/oauth/`

Mirrors: `src/features/auth/oauth.ts`, `src/pages/auth/AuthCallback.tsx`.
All gated on `AUTH_V2_ENABLED`; with it off, `providers/` returns an empty
list and everything else answers `PROVIDER_NOT_AVAILABLE`. Errors use
`{ "success": false, "error": { "code", "message" } }`.

| Call | Auth | Purpose |
|---|---|---|
| `GET providers/` | AllowAny | `{ providers: [{key, label}] }` — which buttons to show |
| `POST <provider>/start/` | AllowAny | `{ authorize_url }` — send the browser there |
| `GET <provider>/callback/` | AllowAny | the provider returns here; always a `302` to the frontend |
| `POST exchange/` | AllowAny | `{ handoff }` → `{ access, refresh, user }` (single use, 60 s) |
| `POST workspace/preview/` | AllowAny | `{ setup }` → `{ email, name, domain, suggested_organisation_name }` (does not spend the code) |
| `POST workspace/` | AllowAny, signup-throttled | `{ setup, organisation_name, name? }` → `201 { access, refresh, user }` |

The callback redirects to `FRONTEND_URL/auth/callback` with exactly one of:

- `?handoff=<code>` — signed in; exchange it at once.
- `?setup=<code>` — verified, and nobody has claimed their domain: show the
  workspace form, then `POST workspace/`. Fifteen minutes; single use.
- `?error=<CODE>` — `ACCESS_REQUEST_PENDING`, `DOMAIN_NOT_VERIFIED`,
  `PERSONAL_EMAIL_NOT_SUPPORTED`, `ORGANIZATION_SUSPENDED`,
  `EMAIL_NOT_VERIFIED`, `ACCOUNT_DISABLED`, `PROVIDER_REJECTED`,
  `PROVIDER_NOT_AVAILABLE`, `INVALID_STATE`, `STATE_EXPIRED`.

`POST workspace/` answers `401 INVALID_SETUP` for a spent or expired code,
`409 WORKSPACE_CLAIMED` when someone from that domain got there first (send
them back to sign in; they will be routed to that workspace), and `400` for
`INVALID_ORGANISATION_NAME` / `PERSONAL_EMAIL_NOT_SUPPORTED`.

Nothing here ever puts a token or an address in a URL: the codes are opaque
and short-lived, and the frontend removes them from the address bar on
arrival.

### Tenant administration — `/api/v1/identity/`

Mirrors: (frontend pending). Every call is scoped to the caller's own
organisation through their membership; **there is no organisation id in any
path**, so there is nothing to tamper with. A row in another tenant answers
`404`, never `403`. Errors use `{ "success": false, "error": { "code", "message" } }`.

| Call | Capability | Purpose |
|---|---|---|
| `GET/POST domains/` | `manage_org_settings` | Claim a domain; the response carries the exact TXT record to publish |
| `POST domains/<id>/verify/` | `manage_org_settings` | Check DNS. `400 DOMAIN_NOT_VERIFIED` = not published; `503 DNS_LOOKUP_FAILED` = could not check. Verifying revokes every other tenant's claim on that domain |
| `GET access-requests/?status=pending|all` | `manage_users` | People who signed in with a corporate address and are waiting |
| `POST access-requests/<id>/approve/` `{role_id, department_id?}` | `manage_users` | Grants the membership and the columns in one transaction. `403 INSUFFICIENT_PERMISSION` if the role holds a capability the approver does not |
| `POST access-requests/<id>/reject/` `{reason?}` | `manage_users` | |
| `GET invitations/?status=pending|all` | `manage_users` | Who this tenant has asked in |
| `POST invitations/` `{email, role_id, department_id?}` | `manage_users` | `201` new, `200` re-sent. Same grant rule as approval. `ALREADY_A_MEMBER` refused |
| `POST invitations/<id>/cancel/` | `manage_users` | |

Invitations carry no token. The invited person signs in with Google or
Microsoft using the invited address and is accepted on the spot, whatever the
domain rules would otherwise say; a different verified address simply finds no
invitation. Open for seven days.

### The internal portal — `/api/v1/platform/`

Mirrors: (frontend pending). Requires `IsPlatformStaff`: a superuser whose
token carries `mfa: true` (only `POST /auth/login/mfa/` mints one). A tenant
administrator is `403` whatever they hold; an unenrolled superuser is `403`
with `detail: "MFA_REQUIRED"`. **Metadata only**: nothing here returns a
tenant's customers, emails, notes, tickets or calls, and the test suite pins
the detail payload's keys.

| Call | Purpose |
|---|---|
| `GET overview/` | Organisations by status, active members, pending requests, open invitations, verified domains, staff count |
| `GET organisations/?q=&status=` | Summaries: owner, active members, pending requests, domains with verification state, `plan` (null until billing). Archived ones only with `status=archived` |
| `POST organisations/` `{name, owner_email, owner_name?}` | Creates the tenant **and its root user** in one transaction: the owner, Admin, no password (they sign in with a provider on that address, or from the reset email where mail is configured). `400 EMAIL_TAKEN` if the address has an account anywhere |
| `GET organisations/<id>/` | Summary plus memberships (name, email, role, department, status, owner, last login), domains, open invitations, last 20 audit events |
| `PATCH organisations/<id>/` `{name}` | Rename |
| `DELETE organisations/<id>/` `{reason}` | **Archives**: closed to sign-in, out of the default list, nothing purged |
| `POST organisations/<id>/status/` `{status: active\|suspended, reason}` | Reason required. Suspension refuses password and provider sign-in and voids every member's capabilities; nothing is deleted. `platform.organisation.status` on the tenant's audit trail |
| `POST organisations/<id>/owner/` `{user_id}` | Move ownership when the owner cannot (audited `by_platform`) |
| `GET staff/` | Who holds platform access and whether their second factor is on |

### Billing — `/api/v1/billing/`

Mirrors: `src/features/billing/billingSlice.ts`, Account settings › Plan & billing.
Scoped through the caller's organisation; no account id in any path.

| Call | Auth | Purpose |
|---|---|---|
| `GET account/` | any member | `{plan{code,name,seats_included,monthly_credits,price_cents,currency,is_trial}, status, seats{used,limit}, credits{balance}, trial_ends_at, current_period_end, enforced}` |
| `GET ledger/?limit=` | `manage_org_settings` | Credit movements, newest first: `kind` (grant/consume/refund/adjust/expire), signed `amount`, `balance_after`, `reason`, `actor`, `at` |
| `GET plans/` | any member | Public plans |

**What the meters mean.** Seats are people: an active member holds one, taken
where membership is granted (approval, invitation, founding, adding, reactivation)
under a row lock on the billing account, so the last seat can be taken exactly
once. Refusals are `INSUFFICIENT_SEATS` (`402` on the Users endpoints, `400`
with the code on approvals). Credits are AI: every model call charges
`BILLING_CREDITS_PER_MODEL_CALL` before it is made and refunds it if the call
fails; with none left the call is refused like a spent budget (`429`). A new
workspace starts on the trial plan (`BILLING_TRIAL_*`). `BILLING_ENFORCED=False`
records everything and refuses nothing.

The platform portal manages the other side: `GET /platform/organisations/<id>/billing/`
(summary, Stripe ids, last 50 ledger rows, all plans) and
`POST …/billing/{credits|seats|plan}/` with a mandatory `reason`, each audited.

### `GET /api/v1/auth/members/`

Auth: `IsAuthenticated` (any role) — **not** admin-gated, unlike everything
below it. Every member (admin + CSMs) of the caller's own organisation.
Exists so any authenticated user can populate an owner-picker (e.g. the
`customers` app's "assign owner" field) without needing User Management
access.

**Response `200`** — a **plain array** (no pagination envelope; this list
is expected to stay small), each entry the same shape as elsewhere.

### `GET /api/v1/auth/capabilities/`

Auth: `IsAuthenticated`. The closed set of capabilities a Role can hold,
as `[{key, label}]` — served rather than hardcoded in the frontend so the
role editor's checkboxes can't drift from what's actually enforced. A
static vocabulary, not org data.

**Response `200`**
```json
[
  { "key": "manage_users", "label": "Manage users & roles" },
  { "key": "manage_org_settings", "label": "Manage organisation settings" },
  { "key": "manage_custom_objects", "label": "Manage custom objects" },
  { "key": "manage_integrations", "label": "Manage integrations & webhooks" },
  { "key": "manage_fx_rates", "label": "Manage exchange rates" },
  { "key": "view_all_accounts", "label": "View all customers & accounts" }
]
```

`view_all_accounts` is the odd one out: the other five open an endpoint,
this one widens what an endpoint returns. Every customer/account view
stays reachable by everyone; what changes is how many rows come back.
See the `customers` app's own visibility section below for the rule it
switches off.

### `GET /api/v1/auth/roles/`, `POST /api/v1/auth/roles/`

Auth: `IsAuthenticated` (GET) / `CanManageUsers` (POST). Scoped to the
caller's own organisation. **Pagination off** — a handful of rows.

GET lists the org's roles. POST takes `{ "name": "...", "permissions":
[...] }`; `slug` is derived server-side (unique per org, suffixed on
collision) and `is_system` is always `false` for anything a client
creates. `400` for an unknown capability key, or for granting a
capability the caller doesn't hold themselves (no privilege escalation —
otherwise `manage_users` would silently be full admin).

**Response `200`/`201`**
```json
[
  {
    "id": 1,
    "name": "Admin",
    "slug": "admin",
    "permissions": ["manage_users", "manage_org_settings", "manage_custom_objects", "manage_integrations", "manage_fx_rates", "view_all_accounts"],
    "is_system": true,
    "users_count": 1,
    "created_at": "2026-09-07T10:00:00Z"
  }
]
```

### `GET/PATCH/DELETE /api/v1/auth/roles/<id>/`

Auth: `IsAuthenticated` (GET) / `CanManageUsers` (PATCH/DELETE). `404`,
not `403`, for a role in another organisation.

`400` when the target `is_system` (the built-in Admin/CSM roles can't be
renamed, re-permissioned, or deleted — Admin is the only thing standing
between an org and locking itself out, and CSM is the default every new
member falls back to), and on DELETE when the role is still assigned to
somebody.

### `GET /api/v1/auth/users/`, `POST /api/v1/auth/users/`

Auth: **`CanManageUsers`** for both. Scoped to the caller's own
`organisation` — there's no way to see or add a member in a different
org. `403` without the capability, `401` if unauthenticated.

GET lists **every** member of the org, admins included (paginated, per
the usual envelope), ordered by name. This replaced a CSM-only list at
`/auth/csms/`, which meant an admin couldn't see or manage themselves or
any fellow admin on the Users page at all.

POST: **Request** `{ "name": "Carl CSM", "email": "carl@acme.io", "password": "csmpassword1", "role_id": 4 }`
— `role_id` is optional and defaults to the org's CSM role; it's
validated against the caller's own org and against the same
no-privilege-escalation rule as role creation.
**Response `201`** — the created user, same shape as the `user` object
above (no tokens — they log in themselves via `/login/`).

### `GET /api/v1/auth/users/<id>/`, `PATCH /api/v1/auth/users/<id>/`

Auth: same as above. **`404`, not `403`, for an id outside that scope** —
the response can't be used to tell "doesn't exist" apart from "exists but
isn't yours".

PATCH accepts any of `{ "name": "...", "is_active": true|false, "role_id": 4, "password": "..." }`,
all optional (partial update). `password` here is an **admin override** —
no current-password check, unlike the self-service change-password flow.

`400` **when the change would leave the organisation with no active
member holding `manage_users`** — applies to both a role change and a
deactivation, since either can strip the last one. Without it an org
could lock itself out entirely: nobody could add a member, mint a role,
or restore anyone again.

Setting `is_active: false` also blacklists every outstanding refresh
token for that user. Their access token is rejected on its very next
request regardless (simplejwt's `JWTAuthentication` checks `is_active` on
every request) — the blacklist call is defense-in-depth for the refresh
token specifically, not what makes deactivation effective.

**Response `200`** (both) — the (possibly updated) member, same shape as
elsewhere.

Not built yet, and deliberately out of scope for this pass: removing a
member outright (only deactivate), reordering or renaming the built-in
roles, per-object (row-level) permissions, and email verification.

---

## `customers` — Organizations

Mirrors: `src/pages/organizations/List.tsx`, `src/components/organizations/tableData.ts`.
The model's field set matches that mock schema column for column (as of
this pass) — **the frontend itself is not wired to this app at all**; it
still renders entirely from `tableData.ts`. This is schema/API only,
ahead of the frontend, by design (see Status above).

**Naming**: this is a separate model from `accounts.Organisation`. `Organisation`
is the tenant (the company paying for Revenact — one admin, its CSMs).
`Customer` is one of *that tenant's own* customers — the company a CSM is
tracking. Same real-world shape ("a company"), two different roles in the
system, hence two different names — never call a `Customer` an
"organisation" in code or docs, and vice versa.

### Models

`Customer` — grouped by what the frontend table groups them as:

- **Identity/provenance**: `organisation` (FK, the tenant — always
  server-set, never client-supplied), `name`, `address` (the mock table's
  "Name / Address" column), `domain`, `industry` (free-text, hand-entered
  via Add/Edit Organization — not part of the original mock schema; folded
  into what Copilot's semantic company matching embeds when set, see the
  `copilot` app's own section below), `email`/`phone` (contact info —
  not part of the original mock schema; back ActivityFeed's Overview
  tab, replacing what used to be a fabricated `contact@<domain>` and a
  phone number hardcoded identically for every organization), `owner`
  (FK to `accounts.User`, nullable, same-tenant only),
  `created_by`/`modified_by` (FK to `accounts.User`, **always
  server-set** from the caller on create/update — not client-settable
  even if present in the request body), `created_at`/`updated_at` (the
  "Created Date"/"Modified Date" halves of those two mock columns).
  "Revenact ID" is just this row's own `id` — no separate field.
- **Lifecycle/health**: `lifecycle_stage` (choices, unchanged from the
  first pass), `health_score` (decimal **0.0–10.0** — matches this
  table's actual scale, which is *not* the 0–100 scale used elsewhere in
  the mock app, e.g. the dashboard's health donut; `health_category` is
  *derived* from it — good ≥7.0, average 4.0–6.9, poor <4.0 — never
  stored, so the two can't disagree),

  `health_score` is **calculated**, not typed in. Five weighted components
  (`services/customers/health.py`), totalling 10:

  | Component | Weight | Measured from |
  |---|---|---|
  | Customer Touch | 4.0 | days since the newest contact of **any** kind — call, email, note, meeting or logged activity, on the company or any of its accounts (`services/customers/contact.py` owns the list) — decaying linearly to zero at 90 days; measured from `joined_date`/`created_at` when there is no contact yet, so a new logo isn't punished. Was `Activity` rows only until this changed, which let a customer emailed every week decay to "no touch" |
  | AI Pulse | 2.0 | `ai_pulse_value`, 1-5 normalised onto 0-1 |
  | Licence Utilization | 2.0 | `total_active_seats / total_contracted_seats`, capped at 1.0 |
  | Aggregate Adoption Score | 1.5 | `primary_product` + `additional_products_count` against a target breadth of 4 |
  | Support Tickets Volume | 0.5 | open (non-`RESOLVED_STATUSES`) tickets, decaying to zero at 20 |

  A component with nothing to measure is **excluded and the rest
  rescaled**, not scored zero - a customer with no seat figures recorded
  is not one with no seats in use, and scoring the blank would make
  health a measure of how completely the CRM was filled in.
  `health_breakdown` (read-only) returns every component as `{key, label,
  weight, points, ratio, available}`; the `points` of the available ones
  sum exactly to `health_score`.

  It stays a stored column so the list endpoint can still order and
  filter on it in SQL. It is refreshed whenever a customer is written
  through the API, and by `manage.py run_health_maintenance` for the
  components that move without the row being saved (an activity logged, a
  ticket opened, a week passing).

  **`run_health_maintenance` is the job to schedule** — it recalculates every
  score and then records that month's `HealthSnapshot`. There is no task queue
  in this project, so it is a plain management command for cron to call; it is
  idempotent and writes at most one snapshot per customer per month. It skips
  a month already recorded rather than upserting it, since that row is a
  record of how the month *ended* and re-running later would drift it forwards.
  `manage.py recalculate_health` still exists for scores alone.

  `csat_breakdown` (read-only) is the distribution behind `csat_score`:
  `{responses, bands}` where each band is `{key, label, count, share}`, best
  to worst, always all five. Counts answered CSAT `Survey` rows bucketed into
  equal fifths of the 0-100 scale (0-20 very dissatisfied ... 81-100 very
  satisfied). `responses` is 0 when nobody has answered one — the bands come
  back all-zero rather than absent, so the popover keeps a stable shape.

  **Writing `health_score` pins an override.** A PATCH sets
  `health_score_override` rather than the score itself - the caller is
  saying they disagree with the calculation - and
  `health_score_is_overridden` (read-only bool) reports which of the two
  is in force. Send `null` to clear the override and hand the customer
  back to the rubric. `pulse` (JSON list of small ints,
  e.g. `[1,1,0,2,1]`, the recent-pulse-history dots), `ai_pulse_value`
  (integer **1–5**, nullable — the AI's own reading, and the stored one),
  `ai_pulse_score` (choices: very_satisfied/satisfied/moderate/high_risk
  — *derived* from `ai_pulse_value`: 5, 4, 3, and 1–2 respectively, blank
  when unscored), `ai_pulse_reason` (text), `csm_pulse_score` (integer
  **1–5**, nullable — the CSM's own hand-set reading, deliberately the
  same scale as `ai_pulse_value` so the two can be compared directly),
  `csm_pulse_modified_at` (read-only datetime, stamped server-side only
  when `csm_pulse_score` actually changes, so a stale read stays visibly
  stale), `nps_score` (integer, −100 to 100), `csat_score` (decimal,
  0–100).

  `ai_pulse_score` was its own column until the AI pulse gained a numeric
  scale; it is derived now so it can't disagree with the number, the same
  arrangement as `health_score`/`health_category`. **Both names still
  read and write**: POST/PATCH either `ai_pulse_score` (the category, as
  before) or `ai_pulse_value` (the number). Sending *both* in one request
  is a 400 — they write the same column, and silently resolving it would
  mean quietly discarding one of them.
- **Dates**: `joined_date`, `renewal_date`, `contract_start_date`,
  `contract_end_date` — all plain nullable dates, no derivation.
- **Financials**: `currency` (choices, same `Organisation.Currency` set —
  the currency this customer's own financial fields below are
  denominated in, independent of `Organisation.currency` (the tenant's
  own reporting currency); defaults to the org's currency at creation
  unless the client explicitly picks another one, e.g. a US-HQ org
  billing one particular customer in EUR — see `fx_rates` below for how
  rollups that sum across customers with different currencies convert),
  plus five independently stored decimals, deliberately *not* derived
  from each other — see note below: `arr_billed_at_account`,
  `arr_billed_at_hq`, `implementation_fee`, `total_contract_value`,
  `total_forecasted_renewal_revenue`.
- **Product/usage**: `primary_product` + `additional_products_count` (the
  mock's combined `productsUtilized: {primary, additional}` object,
  split into two real columns). `primary_product` is a **FK to
  `Product`** since migration 0030 — an id on write, with a read-only
  `primary_product_name` beside it so a screen that only prints what
  they bought needn't fetch the catalogue. It was free text before that,
  which is why the Product Usage dashboard used to report how many
  spellings it had folded. `PROTECT`ed: a product customers are on
  cannot be deleted, only retired. `additional_products_count` stays a
  bare integer — nobody records *which* other products a customer has,
  which is the limit Product Usage states on screen. `top_source_channel`,
  `total_contracted_seats`, `total_active_seats`,
  `seat_utilization_percentage` (**derived** from those two — active ÷
  contracted × 100, `None` if contracted is 0/unset — this one *is* a
  pure function of its inputs with no independent real-world meaning, so
  deriving it is safe, unlike the financial fields), `total_hires`,
  `scope_web_app`, `ces_percentage`.
- **Churn**: `churn_date`, `churn_reason`, `churn_comment` — all
  nullable/blank. `churn_reason` is a **closed list**
  (`Customer.ChurnReason`): `price`, `budget`, `product_gap`, `adoption`,
  `competitor`, `champion_left`, `acquired`, `shut_down`,
  `consolidation`, `support`, `other`, served alongside a read-only
  `churn_reason_display` label so no screen keeps its own copy of the
  taxonomy. It was free text until migration `0028`, which is why the
  Customer Overview used to report how many spellings it had folded
  together; the nuance a CSM wants to write down belongs in
  `churn_comment`, and `other` exists so nobody has to lie — a rising
  `other` count is the signal the list needs another entry. Blank is
  distinct from `other`: nobody recorded a reason, which is a gap in the
  CRM rather than a CSM's judgement.

**Why the financials aren't derived**: in the mock data, `total_contract_value`
happens to equal `arr_billed_at_account + arr_billed_at_hq` in every
sample row, and `total_forecasted_renewal_revenue` happens to be exactly
`total_contract_value × 1.05` every time. Those look like formulas the
mock generator used, not a business rule this schema should hard-code —
real contract terms (discounts, multi-year escalators, custom renewal
negotiations) can diverge from simple arithmetic. All five financial
fields are independently stored and settable.

### Conventions specific to this app

- **No admin gate** — unlike User Management, any authenticated user in the
  tenant (admin or CSM) can list, create, and edit customers.
- **Record visibility is ownership-based** — see the section immediately
  below. Not an admin gate: it narrows *which rows* you get, not which
  endpoints you may call.
- **Owner must be same-tenant** — assigning `owner_id` to a user from a
  different organisation is a `400`, not silently ignored or allowed.
- **`organisation`, `created_by`, `modified_by` are never client-supplied** —
  `organisation` and `created_by` are set from `request.user` on create;
  `modified_by` is reset to `request.user` on *every* update. Sending any
  of the three in a request body has no effect.

### Record visibility (`view_all_accounts`)

Implemented in `services/customers/scoping.py`, which is the single
definition and the one place to change it. For a caller **without** the
`view_all_accounts` capability:

- A **Customer** is visible if they own it, if they own one of its
  Accounts, or if it has no owner.
- An **Account** is visible if they own it, if they own one of its
  parent Customers, or if it has no owner.

Everything hanging off a Customer/Account — Activities, Emails, Tasks,
Notes, Tickets, Calendar Events, Contacts, Opportunities, Risks,
Surveys, Canvases, Headlines, custom-object records — follows its
parent. There is no per-type rule; gating one tab while leaving the
others open would be theatre, since a Note is as sensitive as an Email.

Holding `view_all_accounts` replaces the ownership predicate with the
organisation one. It never widens past the tenant.

**Out-of-scope records are `404`, not `403`.** A 403 would confirm to
someone that a record exists. This extends the "404, not an empty list"
convention these endpoints already used for another organisation's ids.

**Creating a Customer or Account makes you its owner** unless you send
an explicit `owner_id`.

**Reaching in both directions is deliberate.** Owning "Apple Inc" gives
you its divisions; owning "Apple EMEA" gives you the company it belongs
to, but *not* its sibling divisions. The upward leg isn't generosity —
the account page header names its parent org and its Organizations tab
lists it, so a strictly-own rule would 404 inside a page you are
allowed to open.

**Unowned records stay visible to everyone.** An unowned record is
nobody's secret, and hiding it would make the unassigned queue
invisible to the people meant to work it.

#### Not the same thing as "my book"

`GET /api/v1/cockpit/summary/` and Copilot's context builder filter
`owner=user` *strictly*, and deliberately still do. Those answer "what
am I responsible for", not "what am I allowed to open", and the answers
differ: a customer you can see through an account you own should not
count toward your own ARR.

#### Known consequences (accepted, not oversights)

1. **A shared Account is visible to both owners.** `Account.customers`
   is a many-to-many; an Account linked to two Customers with different
   owners is reachable by both, and its children appear in both
   Customers' rollups. That is the data model saying they share it.
2. **Parent names surface through rows you are allowed to see.** An
   Account you own returns every linked Customer's `{id, name}`, and
   Contact/Opportunity/Risk/Survey/Canvas rows carry a `companies`
   list. A name and an id, with no ARR/health/notes attached; filtering
   them would blank the account page header for account-only owners.
3. **Still organisation-wide:** Copilot session invites disclose an
   account name before acceptance — deliberate, since being invited to
   collaborate on an account is itself a decision to share it, and an
   invite you can't read the subject of is useless. Separately,
   `Notification.message` and Copilot `Message.content` are
   denormalised free text written once at creation — no queryset gate
   can retroactively scrub what they already say.
4. **A Campaign and a Scenario are themselves tenant-wide**, visible to
   every member. Neither has a creator or owner field, so there is
   nothing to scope them by; what *is* scoped is which customers and
   contacts you can point them at (see the two app sections below). A
   campaign's recipient roster therefore stays readable by any member —
   closing that needs a `created_by` on Campaign, which is a data-model
   decision rather than a queryset one.

### Dashboard drill (`?drill=`)

Five dashboard-stats endpoints share one drill-down convention rather
than inventing their own, all built on `services/customers/drill.py`:
`GET /api/v1/customers/overview/`, `GET /api/v1/customers/activity/`,
`GET /api/v1/customers/forecast/`, `GET /api/v1/interactions/stats/` and
`GET /api/v1/tickets/stats/`. Each accepts `?drill=<segment>` (some
segments take a value, written `kind:value`) that turns one chart figure
into the real list of companies behind it.

**Same set as the totals.** The drill list is computed from the exact
filtered queryset or customer list the endpoint's own totals use for
that figure, then intersected with `visible_customers(request.user)` — a
company the totals didn't count never appears, and a company the viewer
may not see never appears either (an account-level record belongs to all
of the account's customers, and some of those can sit outside the
viewer's own book).

**A filter that names companies narrows the list too.** On the ticket
and interaction drills, `?customer=` lists only that customer,
`?account=` only that account's customers and, for tickets, `?owner=`
only that owner's customers — an account-level record kept by the
filter would otherwise list every sibling customer of its account.

**Per-company values can add up to more than the chart.** On the
interaction and ticket drills, a record filed on an account counts once
for each of that account's customers, so the companies' `value`s can sum
to more than the chart figure the drill opened. The blank-assignee bar
on the ticket dashboard is not drillable: `assignee:` needs a name.

**A bad `drill` is ignored, never `400`.** An unrecognised segment name,
a value that isn't on the field's real choice list, or a `:value` on a
segment that doesn't take one all fall back silently to the endpoint's
normal stats response — the same "ignore, don't reject" convention every
dashboard filter in this app already follows.

When a drill applies, the response is *only*:

```json
{
  "drill": {
    "segment": "type:email",
    "value_label": "interactions",
    "count": 12,
    "truncated": false,
    "companies": [
      {"id": 41, "name": "Hyatt Regency", "owner": "Dana", "arr": 240000.0, "value": 7}
    ]
  },
  "currency": "USD"
}
```

`count` is the true total; `companies` is capped at `LIMIT = 500`
(`truncated` says whether the list was cut). `arr` is
`arr_billed_at_account` converted with `convert_to_org_currency` (the
org's own FX rates), `null` when no rate exists. `value` is whatever
that segment counts for the company — meaning differs per endpoint, see
the table below. Read-only: no audit event, no data-classification
change.

| Endpoint | Segments | `value_label` |
|---|---|---|
| `GET /api/v1/customers/overview/` | `churned_12m` | `ARR` |
| `GET /api/v1/customers/activity/` | `gone_quiet` | `days since contact` |
| `GET /api/v1/customers/forecast/` | `at_risk`, `churn`, `contraction` | `downside` |
| `GET /api/v1/customers/forecast/` | `expansion` | `expected expansion` |
| `GET /api/v1/interactions/stats/` | `all`; `type:<email\|call\|ticket>`; `sentiment:<value>`; `area:<value>`; `category:<value>`; `subcategory:<value>` | `interactions` |
| `GET /api/v1/tickets/stats/` | `all`; `on_hold`; `sentiment:<value>`; `priority:<value>`; `status:<value>`; `origin:<connector id>` or `origin:none`; `assignee:<name>` (exact match) | `tickets` |

`GET /api/v1/tickets/stats/` has its own section below for its non-drill
response; its drill segments are documented here, the one place a caller
would look for them.

**Open as a list.** `GET /api/v1/customers/?ids=1,2,3` (below) is how a
dashboard opens a drill's companies as a real, paginated list view with
full customer rows rather than a drill's `{id, name, owner, arr, value}`
shape. It does not get past the cap: a drill returns at most 500
companies (`truncated` says when there are more) and `?ids=` reads at
most 500 ids, so a truncated drill cannot be opened in full. The
frontend hides "Open as a list" when `truncated` is true.

### `GET /api/v1/customers/`, `POST /api/v1/customers/`

Auth: `IsAuthenticated` (any role). Scoped to the caller's own organisation.

GET: standard paginated envelope, ordered by name. `?search=<text>` filters
to customers whose `name` (case-insensitive substring) or Revenact ID
(the row's own `id`, also substring — e.g. `?search=8` matches id `8`
and `18`) matches; blank/omitted returns everything. Powers the
frontend's search box — there's no separate "External ID" field in the
schema, so that part of its placeholder text isn't wired to anything.
`?renewal_within=<days>` filters to non-churned customers with a
`renewal_date` on or before today+<days> — **no lower bound**, so an
already-overdue renewal (more urgent, not less) is included, not
filtered out; ordered soonest/most-overdue-first instead of by name.
Powers the Organizations page's Renewal card/popover (1-month/3-month
toggle). A non-integer value is ignored, not an error.
`?ids=1,2,3` narrows to exactly those customers (the dashboard's 'Open as
a list'), archived ones included — an explicit id list asks for those
records; non-integers are ignored, at most 500 are read, and scoping still
applies. `ids` present but empty or all-bad returns nothing, not the whole
book.
The response is still the standard paginated envelope (`PAGE_SIZE = 25`,
`?page=`), not the whole filtered set at once — a caller opening more
than a page of ids pages through with `?ids=...&page=2`, same as any
other list here.
Archived customers (`is_archived=true`) never appear in this list unless
named by `?ids=`, nor in `?renewal_within=`, or in the stats endpoint below — soft-hidden,
not deleted; see the detail endpoint below for how to archive/unarchive.
POST: only `name` is required — every other field above is optional.
**Response `201`** — the created customer, `owner`/`created_by`/`modified_by`
nested (same user shape as elsewhere), `health_category` and
`seat_utilization_percentage` computed.

### `GET /api/v1/customers/stats/`

Auth: `IsAuthenticated`. Scoped to the caller's own organisation, every
customer included (churned ones too — "churn" is itself a lifecycle
bucket below, unlike `?renewal_within=` above which excludes them).
Powers the Organizations page's MetricsPanel (Health / NPS / Lifecycle
Stages sections).

```json
{
  "health": {
    "good":    { "count": 1, "mrr": 4266.67, "arr": 51200.0 },
    "average": { "count": 0, "mrr": 0.0,     "arr": 0.0 },
    "poor":    { "count": 2, "mrr": 7800.0,  "arr": 93600.0 }
  },
  "nps": { "promoters": 1, "passives": 0, "detractors": 2, "score": -33 },
  "lifecycle": {
    "onboarding": { "count": 0, "mrr": 0.0, "arr": 0.0 },
    "kickoff":    { "count": 0, "mrr": 0.0, "arr": 0.0 },
    "adoption":   { "count": 0, "mrr": 0.0, "arr": 0.0 },
    "live":       { "count": 2, "mrr": 10066.67, "arr": 120800.0 },
    "renewal":    { "count": 0, "mrr": 0.0, "arr": 0.0 },
    "churn":      { "count": 1, "mrr": 2000.0, "arr": 24000.0 },
    "expansion":  { "count": 0, "mrr": 0.0, "arr": 0.0 },
    "other":      { "count": 0, "mrr": 0.0, "arr": 0.0 }
  },
  "unconverted_count": 0
}
```

`mrr` is derived (`arr_billed_at_account / 12`) — there's no stored MRR
field. `nps.score = round((promoters - detractors) / scored * 100)`; a
customer with no `nps_score` set is excluded from the breakdown and
from `scored`, not counted as a passive. An empty organisation returns
all-zero buckets, not an error.

**`unconverted_count`**: each customer's `arr_billed_at_account` is in
*its own* `currency`, converted into the org's own currency (via
`fx_rates`, below) before being added to any bucket's `mrr`/`arr` —
these buckets are one tenant-wide total and can't meaningfully mix
currencies. A customer whose currency has no configured `FxRate` is
still counted in `count`, but excluded from every `mrr`/`arr` sum
rather than having its unconverted amount silently treated as if it
were already in the org's currency. `unconverted_count` is how many
customers that happened to, across the whole response — the frontend
shows a caveat rather than a silently-too-low total when it's nonzero.
`GET /api/v1/accounts/stats/` has no equivalent field — `Account` has
no `currency` of its own (see `fx_rates` below), so its `arr` is always
treated as already being in the org's own currency.

### `GET /api/v1/customers/<id>/`, `PATCH /api/v1/customers/<id>/`

Auth: same as above. **`404`, not `403`,** for a customer outside the
caller's organisation.

PATCH accepts any subset of the POST fields (partial update), including
`owner_id` (`400` with a field error if the target user isn't in the
caller's organisation). Every PATCH sets `modified_by` to the caller,
regardless of which fields changed.

**Archive/unarchive**: `PATCH {"is_archived": true}` / `{"is_archived":
false}` — no dedicated endpoint, just a normal field. Distinct from
`lifecycle_stage=churn`: archiving is "stop showing me this" (a soft
delete — the record isn't touched otherwise), churning is a business
outcome (with its own `churn_date`/`churn_reason`/`churn_comment`
fields, still visible in lists unless separately archived). The detail
endpoint itself always works regardless of `is_archived` — only the
list/renewal-window/stats endpoints filter it out.

Not built yet, and deliberately out of scope: Board view, nested
Contacts, deleting a customer (only field edits exist so far). Accounts
(one Customer has many) are now built — see below. The rest of this app
*is* wired into the frontend (List page, MetricsPanel, Add/Edit/Churn/
Archive, and the Details page's General tab) — the note that used to be
here saying it wasn't is stale.

### Models — `Account`

Mirrors: `src/pages/organizations/Details.tsx` (`AccountsTab`,
`AccountsMetricsBanner`), `src/components/organizations/accountsData.ts`.
One-to-many under `Customer` (`customer` FK, `related_name="accounts"`)
— a named sub-account (regional/business-unit deployment) of one of the
tenant's own customers, with its own health/pulse/NPS/CSAT tracking
independent of the parent Customer's aggregate numbers.

Field set mirrors `AccountRow` (the mock schema) column for column, same
approach as `Customer` mirrors `tableData.ts`. Two mock fields
deliberately have no column: `orgName` is just `customer.name` (no need
to duplicate it) and `revenactId` is this row's own `id`. Reuses
`Customer.LifecycleStage`/`Customer.AIPulseScore` as its own field
choices and `Customer.HEALTH_THRESHOLDS` for its `health_category`
derivation, rather than redefining them — an account's lifecycle stage
and health mean the same thing as a customer's, just at a finer grain.

Fields: `customer` (FK, server-scoped — **read-only**, never client-
supplied, see the endpoints below), `name`, `domain`/`industry`/`address`/
`email`/`phone` (each blank falls back to the parent customer's own domain/
industry/address/email/phone — for the logo and for ActivityFeed's Overview
tab on the standalone Account page — frontend responsibility, in
`mapAccountToAccountRow.ts`, not enforced server-side; `industry` also gets
a second, real fallback resolution server-side, in
`services/copilot/retrieval.py`'s own `_effective_industry`, since Copilot's
semantic matching runs in Python, not the browser; `address`/`email`/
`phone`/`industry` aren't part of the original `AccountRow` mock schema,
added alongside Customer's own for the same Overview-tab reason),
`owner` (FK to `accounts.User`, nullable, same-tenant only, validated
the same way as `Customer.owner_id`), `created_at`/`updated_at`,
`lifecycle_stage`, `health_score` (0.0–10.0, `health_category` derived,
same thresholds as Customer), `pulse` (JSON list), `ai_pulse_value`
(1–5, nullable), `ai_pulse_score` (derived from it, same mapping and
same both-names-write rule as Customer), `ai_pulse_reason`,
`csm_pulse_score` (1–5, nullable), `csm_pulse_modified_at` (read-only),
`nps_score` (−100 to 100), `csat_score` (0–100),
`renewal_date`, `arr` (MRR is derived, `arr / 12`, not stored — same
convention as Customer).

**Add/Edit Account** covers identity, ownership, lifecycle stage, and
renewal date — same product decision as Customer's own Add/Edit form.
`health_score`/`pulse`/`ai_pulse_value`/`ai_pulse_score`/
`ai_pulse_reason`/`csm_pulse_score`/`nps_score`/
`csat_score`/`arr`/`address`/`email`/`phone` are technically writable
via `AccountSerializer` too (not restricted at the API layer, same as
`CustomerSerializer`) but the Add/Edit Account UI never sends them —
meant to sync from other systems later (`address`/`email`/`phone`
specifically could be added to that form as a follow-up, same category
as the identity fields it already covers, but that's a separate ask
from wiring the Overview tab's read path). See `seed_demo_accounts`
management command for demo data (run after `seed_demo_customers`) —
a handful of accounts are given their own address/email/phone there on
purpose, distinct from their parent's, to demonstrate both the
override and the fallback.

### `GET /api/v1/customers/<customer_id>/accounts/`, `POST /api/v1/customers/<customer_id>/accounts/`

Auth: `IsAuthenticated`. GET: every `Account` under one `Customer`,
scoped to the caller's own organisation. **`404`, not `403` or an empty
list,** for a `customer_id` outside the caller's organisation or that
doesn't exist — checked once via `get_object_or_404` on the parent
`Customer` before touching its accounts, so a real customer in another
org 404s the same way a nonexistent id does (a caller can't otherwise
tell "no accounts" apart from "not your customer").

**Response `200`** (GET) — a **plain array** (no pagination envelope; an
individual customer's account list is expected to stay small), each
entry with `owner` nested and `health_category` computed, same
conventions as `CustomerSerializer`.

POST: only `name` is required. `customer` is always taken from the URL's
`customer_id` — sending a different value in the body is silently
ignored, same convention as `organisation`/`created_by` on the Customer
create endpoint. `owner_id` (optional) is `400` if the target user isn't
in the caller's organisation.

**Response `201`** — the created account, same shape as a GET list entry.

### `GET /api/v1/customers/<customer_id>/accounts/<id>/`, `PATCH /api/v1/customers/<customer_id>/accounts/<id>/`

Auth: `IsAuthenticated`. **`404`, not `403`,** for either id outside the
caller's organisation/customer. PATCH accepts any subset of the POST
fields (partial update); `customer` in the body is ignored (read-only —
there's no way to move an account to a different customer via this
endpoint). `owner_id` follows the same same-organisation validation as
create.

**Response `200`** (both) — the (possibly updated) account.

### `GET /api/v1/accounts/`

Auth: `IsAuthenticated`. Every `Account` across every `Customer` the
caller's own organisation owns — the one Account view not nested under
`/customers/<id>/...`, mounted at its own top-level prefix, same
reasoning as `ContactListView`. Powers the standalone Accounts page
(`react-ts-app`'s `/accounts/list`).

GET-only — no matching POST here (see this view's own docstring):
Account has only one possible parent (a Customer), so "Add Account"
already knows exactly which nested endpoint to POST to once a company
is picked, same as the standalone Contacts page's own "Add Contact".

Paginated with the shared `DEFAULT_PAGINATION_CLASS`/`PAGE_SIZE`, same
reasoning as `ContactListView` — unlike the nested per-Customer list
above, this can span every account the tenant has. `?search=` matches
name (substring, case-insensitive). `?company=<customer_id>` filters
to one company's own Accounts.

**Response `200`**
```json
{
  "count": 12,
  "next": "http://.../api/v1/accounts/?limit=20&offset=20",
  "previous": null,
  "results": [
    {
      "id": 4,
      "customer": 6,
      "customer_name": "Apple Inc",
      "name": "Apple EMEA",
      "...": "... same shape as the nested list's own entries, plus customer_name"
    }
  ]
}
```

### `GET /api/v1/accounts/stats/`

Auth: `IsAuthenticated`. Aggregate rollups for the standalone Accounts
page's own MetricsPanel (Health / NPS / Lifecycle Stages sections) —
same shape and same reasoning as `CustomerStatsView`, every Account
across every Customer the caller's org owns (no `is_archived` field on
Account to exclude anything by). MRR is `arr / 12`, same derivation as
`CustomerStatsView`'s own.

**Response `200`**
```json
{
  "health": {
    "good": { "count": 7, "mrr": 4200.5, "arr": 50406.0 },
    "average": { "count": 5, "mrr": 1800.0, "arr": 21600.0 },
    "poor": { "count": 2, "mrr": 300.0, "arr": 3600.0 }
  },
  "nps": { "promoters": 9, "passives": 2, "detractors": 3, "score": 43 },
  "lifecycle": {
    "onboarding": { "count": 4, "mrr": 1200.0, "arr": 14400.0 },
    "...": "... every LifecycleStage value, zero-filled if unused"
  }
}
```

### `GET /api/v1/cockpit/summary/`

Auth: `IsAuthenticated`. Real numbers for Cockpit's own "My Portfolio
Summary"/"Renewals" tiles (react-ts-app's
`src/pages/copilot/CockpitView.tsx`), which used to show fixed literal
counts/values unrelated to any real Customer/Account. Scoped to the
caller's own *owned* book of business — `owner=request.user` on
Customer/Account, not the whole tenant's, same "My" framing as
`/tasks/?mine=true`.

`customers`/`accounts` mirror `CustomerStatsView`/`AccountStatsView`'s
own health-bucketing and FX-conversion rules exactly (`Customer`'s own
`arr_billed_at_hq` converted via `convert_to_org_currency`, excluded
rather than mis-summed when unconvertible — see `unconverted_count`;
`Account`'s own `arr` used as-is, no FX step). `renewals` reuses
`CustomerListCreateView`'s own `?renewal_within=` window/exclusion
rules (today through +`?days=` days inclusive, already-churned
excluded, no `renewal_date` excluded) — `?days=` defaults to `30`, any
positive int (an invalid value silently falls back to 30, same
"ignore, don't 400" convention as `?renewal_within=`'s own). `items`
is every renewing Customer/Account merged into one list and sorted
soonest-first — a real drill-down, not just a count/value pair, same
"give the real list" reasoning as `TaskListView`.

**Response `200`**
```json
{
  "customers": {
    "count": 4, "value": 63500.0, "unconverted_count": 0,
    "health": { "good": 3, "average": 1, "poor": 0 }
  },
  "accounts": {
    "count": 2, "value": 30000.0,
    "health": { "good": 2, "average": 0, "poor": 0 }
  },
  "renewals": {
    "window_days": 30,
    "customers": { "count": 1, "value": 12000.0 },
    "accounts": { "count": 0, "value": 0.0 },
    "items": [
      { "id": 12, "name": "Acme Co", "type": "customer", "value": 12000.0, "renewal_date": "2026-09-12" }
    ]
  }
}
```

### `GET /api/v1/customers/health/`

Auth: `IsAuthenticated`. The whole book with its health history, for every
tab of the Health Overview dashboard — Triage, Divergence, Movement,
Renewal Date and Controls.

**One request for five tabs.** They read the same rows differently —
ranked by risk, plotted CSM-pulse against AI-pulse, counted as
transitions between months, summed as renewal ARR — so an endpoint each
would fetch the same book five times and let the tabs disagree with each
other mid-render.

**Unpaginated on purpose.** Every tab aggregates over the entire book: a
triage queue that ranked only page one would rank nothing. Capped at 500
rows, with `truncated` in the payload rather than a silent cut. Archived
customers are excluded, matching the list endpoint — they are hidden from
the working views, and health is a working view.

Query params: `history_months` (default 12, the most any tab offers)
trims how much history comes back. It is for keeping the payload down,
not for the UI — Movement's own window selector re-slices client-side.

**Response `200`**

```json
{
  "results": [
    {
      "id": 9,
      "name": "Hyatt Hotels Corporation",
      "owner_id": 5,
      "owner_name": "Gerry Hill",
      "lifecycle_stage": "customer_active",
      "lifecycle_stage_display": "Customer - Active",
      "renewal_date": "2027-02-02",
      "arr": 59500.0,
      "days_since_touch": 56,
      "health_score": "5.4",
      "health_category": "average",
      "csm_pulse_score": 4,
      "csm_pulse_modified_at": "2026-02-04T09:12:00Z",
      "ai_pulse_value": 2,
      "ai_pulse_reason": "Seat utilisation at 41% of contract",
      "total_active_seats": 822,
      "history": [
        {"captured_on": "2026-08-31", "health_score": "6.1",
         "health_category": "average", "csm_pulse_score": 4, "ai_pulse_value": 3}
      ]
    }
  ],
  "count": 12,
  "history_months": 12,
  "truncated": false,
  "currency": "USD",
  "unconverted_count": 0
}
```

Notes on the shape:

* `risk_of_loss` / `risk_factors` are the shared churn rule
  (`services/customers/churn.py`) applied to that row — the probability
  the renewal is lost, and every contribution that produced it. Served
  rather than computed in the browser so the Renewal Date tab and the
  Revenue Forecast can't drift apart about the same account.
* `triage_score` (int, 0–155, not a percentage and never clamped: the
  sum of its factors, at most Poor 66 + a 4-point pulse gap 44 + renewal
  inside 90 days 18 + a Good → Poor fall 27; ≥ 40 means needs action now) /
  `triage_factors` (`[{"label": str, "points": int}]`, highest points first) / `triage_direction`
  (`declining` | `improving` | `flat` | `unknown`) are the Triage tab's own
  rule (`services/customers/triage.py`) applied to that row, from exactly
  the fields already on it plus its prefetched history — health category,
  the CSM/AI pulse gap, days to renewal, and whether the last three months
  of history end worse than they started. Served rather than computed in
  the browser so the Triage tab and the dashboard's attention-list endpoint
  (`GET /api/v1/dashboard/attention/`) can't drift apart about the same
  account. `triage_direction` is `unknown` rather than `flat` when fewer
  than two months of history are on record.
* `owner_id` accompanies `owner_name` because the dashboard's Primary
  Owner filter keys on it. Two CSMs sharing a name is ordinary in a real
  org, and a filter keyed on the label would merge their books. Both are
  null for an unowned customer.
* `csm_pulse_score` and `ai_pulse_value` stay **nullable all the way to
  the browser**. "Not rated yet" is a real state, and the Divergence tab
  must not read an unrated account as one both parties agree is terrible.
* **`arr` is converted**, into the organisation's own reporting currency
  (`currency`), through the same `convert_to_org_currency` hook
  `CustomerStatsView` uses — and is **null** where that customer's
  contract currency has no `FxRate` configured. `unconverted_count` says
  how many. The Renewal tab adds money up across the whole book; treating
  an unconverted figure as though it were already in the reporting
  currency produces a confident wrong number, which is worse than an
  incomplete one. The rate table is fetched once per request
  (`fx_rates.conversion.rates_for`), not per row.
* `days_since_touch` is the same number the health rubric's own Customer
  Touch component is built from — not a second definition of "touched".
  An account nobody has ever touched is measured from when it arrived, so
  a logo onboarded last week doesn't read as neglected.
* This is a **Customer-level** endpoint. Accounts carry their own health
  and renewal dates, and no tab reads them yet; adding them would change
  what "the book" means on every tab at once.

### `GET /api/v1/customers/overview/`

Auth: `IsAuthenticated`. What the book is made of, for the Customer
Overview dashboard: how many customers, how big, how concentrated, how
long they stayed, and why the ones who left left.

Every other dashboard asks how the customers you have are *doing* —
health, usage, revenue, voice, coverage. This asks what they **are**.

**The one endpoint that counts customers you no longer have.** Every
other rollup goes through `scoping.live_customers` (visible, not
archived, not churned), which is right for a working view — nobody
triages an account that left. Logo retention, cohort survival and churn
reasons are questions about exactly those rows, and computing retention
over survivors returns 100% every time. So this reads the whole visible
book, and every figure says which population it speaks for.

Params: `owner`, `lifecycle`, `customer`, plus `drill` — and its filter
options include churned customers, unlike every other dashboard's,
because being unable to filter to one here would be strange.

**Drill.** `?drill=churned_12m` opens every customer who churned in the
last year — the exact set `portfolio.churned_last_year` builds for
`kpis.churned_12m`, so a drill's count always matches the KPI. Response
shape and the two drill rules: see **Dashboard drill (`?drill=`)** above.
`value` is the customer's converted ARR, same figure as `arr`; nothing is
lost to the visible-book intersection here since this endpoint's own
book already includes churned and archived customers.

**Response `200`** (no `drill`, or an unrecognised one) — `kpis`,
`concentration`, `cohorts`, `churn_reasons`, `segments`, `lifecycle`,
`currency`, `filters`.

```json
{
  "kpis": {"active": 9, "active_arr": 688600.0, "average_arr": 76511.11,
           "churned": 4, "churned_arr": 236100.0,
           "churned_12m": 3, "churned_arr_12m": 212100.0,
           "logo_retention": 69.2, "unpriced": 0},
  "concentration": {
    "rows": [{"rank": 1, "id": 9, "name": "Shopify", "arr": 175000.0,
              "share": 25.4, "cumulative_share": 25.4,
              "owner": "Carl CSM", "health_category": "good"}],
    "total_arr": 688600.0, "counted": 9,
    "rest_count": 0, "rest_arr": 0.0, "top_three_share": 55.5
  },
  "cohorts": {"rows": [{"year": 2023, "joined": 5, "retained": 4,
                        "churned": 1, "retention": 80.0}], "undated": 1},
  "churn_reasons": [{"value": "budget", "reason": "Budget cut",
                     "customers": 2, "arr": 24000.0}],
  "segments": {"rows": [{"key": "over_100k", "name": "$100K and above",
                         "customers": 2, "arr": 287000.0}], "unplaced": 0},
  "lifecycle": [{"key": "live", "name": "Live", "customers": 1, "arr": 67200.0}]
}
```

The decisions behind those numbers:

* **Churn means a `churn_date`, not an archive flag.** Archiving is a
  filing decision — a duplicate row gets archived — and counting it as
  churn would pad every retention denominator with housekeeping. The two
  are separate actions by design.
* **Concentration is the number no other screen states.** If the largest
  three customers are 55% of ARR, that is the most important fact about
  the business. The rows carry a running share so the Pareto is readable
  rather than asserted, and the tail beyond the top ten is *folded* into
  `rest_count`/`rest_arr` rather than dropped — a reader needs to see how
  little of the book it is. An account with no convertible ARR is not
  ranked at all: unknown size can't be placed in a ranking by size.
* **Churn reasons group themselves**, because `churn_reason` is a closed
  list now (see the field above). Each row carries the stored `value` and
  its `reason` label, and **every reason on the list is returned even at
  zero** — which free text could not do, since an absent string and a
  reason nobody thought to type are indistinguishable. "Nothing lost to a
  missing capability this year" is a fact about the product, and it is
  only sayable now. A churned customer with no reason recorded gets a
  `"No reason recorded"` row of its own, separate from `other`, and a
  value left behind by an older release is counted there too rather than
  dropped — the reason counts have to add up to `kpis.churned`.
* **A customer with no `joined_date` is counted apart**, not dropped into
  the earliest cohort — which would make the oldest cohort look larger
  and its retention worse.
* `segments` reports **counts and ARR together**, because the two tell
  opposite stories on most books: the smallest band is usually the most
  accounts and the least money. Every band is present even when empty;
  empty *lifecycle* stages are dropped, because there are eight of those
  and most books use four.
* `logo_retention` and `average_arr` are null rather than flattering on
  an empty book, and the twelve-month churn figures are separate from
  all-time — all-time churn is a fact about history, not about how this
  year is going.

### `GET /api/v1/customers/activity/`

Auth: `IsAuthenticated`. The operations review behind the Activity
Tracking dashboard: **is the team working the book, and where isn't it?**

Not the same question as AI Trending Topics, which counts what
*customers* are talking about. This counts what we did — touches logged,
accounts covered, cadence kept, follow-through on tasks.

`?days=` sets the window (default 90, **clamped** 7–730), plus the usual
`owner` / `lifecycle` / `customer`, plus `drill`.

**Drill.** `?drill=gone_quiet` opens every account past the going-dark
threshold — the exact list `activity_tracking.dark_accounts` builds for
`kpis.dark_accounts` and the page's own capped `going_dark`, so a drill's
count always matches the KPI. Response shape and the two drill rules:
see **Dashboard drill (`?drill=`)** above. `value` is
`days_since_contact`; `null` means never contacted, and those rows sort
first, longest silence next.

**Response `200`** (no `drill`, or an unrecognised one) — `kpis`,
`timeline`, `sources`, `cadence`, `by_owner`, `going_dark`,
`going_dark_threshold`, `window_days`, `currency`, `filters`.

Three definitions decide what these numbers mean, and all three are
places a screen like this can mislead:

* **A touch is work we logged**: activities, calls, emails, notes and
  meetings. **Tickets are not touches** — a customer raising one is not
  evidence anyone called them back, and counting it would let a screen
  full of complaints read as a screen full of coverage. They come back
  as `kpis.inbound` beside the touches, because the ratio between the
  two is itself worth seeing. (Email has no direction flag, so an
  inbound reply does count as a touch: a named overstatement, taken
  because dropping email would remove the largest source of real contact
  from a coverage metric.)
* **"Last contact" here is the same number the health rubric uses.**
  Both read `services/customers/contact.py`, which defines once what
  counts as contact (calls, emails, notes, meetings, activities — on the
  company or any of its accounts; tickets are inbound and excluded). The
  rubric used to count `Activity` rows only, so this module returned a
  second figure, `days_since_activity`, beside `days_since_contact` and
  the cadence chart admitted the two could differ. They can't now, and
  the second field is gone. Broadening the rubric moved every health
  score that had non-activity contact; snapshots already recorded are
  history and were not rewritten — the Movement view shows the step.
* **Per-CSM figures are by account owner, never by the name on a
  record.** `sender_name`, `host_name`, `assignee_name` and
  `author_name` are all free text; grouping a team-performance view on
  those would split "J. Smith" from "John Smith" and invent a person.
  `by_owner` therefore answers "is this book being worked", not "who did
  the work".

Smaller decisions worth knowing: a touch on an *account* counts for its
parent company (otherwise a worked account reads as neglected because
the work was logged a level down); an account with no contact at all is
its own `never` bucket rather than "90+ days", which would imply a date
nobody has, and it sorts **above** merely-stale accounts in
`going_dark`; `going_dark_threshold` is imported from `churn.py` so this
screen and the renewal risk agree on what stale means; and `coverage` is
null rather than 0 on an empty book.

### `GET /api/v1/customers/forecast/`

Auth: `IsAuthenticated`. The ARR bridge behind the Revenue Forecast
dashboard: **what is this book worth a year from now, and what moves
it?**

    opening ARR − expected churn − expected contraction + expected
    expansion = forecast ARR

Deliberately a different question from the Health Overview's Renewal
Date tab. That one is operational (which renewals do I work this week,
inside ninety days); this is financial, over a year, and it is the only
place that reads renewals, open Risks and open Opportunities together.

Query params: `horizon_days` (default 365, **clamped** to 30–1095 rather
than rejected), plus the usual `owner` / `lifecycle` / `customer`, plus
`drill`.

**Drill.** `?drill=` opens a bridge step into the companies behind it:
`at_risk` (every account carrying downside — churn and contraction
together), `churn`, `contraction` or `expansion`. These are the exact
predicates `forecast.build_bridge` sums (churn = rows whose
`churn_exposure >= risk_exposure`, contraction = the rest; both value
`downside`; expansion values `expansion`), so a drill's values always add
up to the step it opened. Response shape and the two drill rules: see
**Dashboard drill (`?drill=`)** above.

**Response `200`** (no `drill`, or an unrecognised one) — `bridge`,
`scenarios`, `pipeline`, `swing`, `horizon_days`, `accounts`,
`renewing_count`, `unpriced_count`, `currency`, `filters`.

```json
{
  "bridge": {"opening_arr": 924700.0, "churn": 175430.0, "contraction": 33120.0,
             "expansion": 131880.0, "forecast_arr": 848030.0,
             "net_change": -76670.0, "nrr": 91.7},
  "scenarios": {"worst": 150200.0, "likely": 848030.0, "best": 1189900.0},
  "pipeline": [{"key": "negotiation", "name": "Negotiation", "open": 68400.0,
                "weighted": 54720.0, "count": 3}],
  "swing": [{"id": 14, "name": "Uber", "arr": 95000.0, "risk": 0.6,
             "factors": [{"label": "Poor health", "points": 0.5}],
             "churn_exposure": 57000.0, "risk_exposure": 0.0, "downside": 57000.0,
             "expansion": 13440.0, "net": -43560.0, "renews_in_horizon": true}]
}
```

The rules behind those numbers, each of which is a decision someone can
disagree with in one place:

* **Churn is weighted by `churn.py`** — the same rule the Renewal Date
  tab prints on its own rows (served on the health payload as
  `risk_of_loss` / `risk_factors`). A forecast that disagrees with the
  work list is a forecast nobody trusts twice. It is a stated business
  rule, not a fitted model: this product has no churn history to fit to.
* **Only renewals inside the horizon can churn.** However bad an account
  looks, it cannot be lost at a renewal that doesn't happen this year.
  An overdue renewal *is* inside the window — the date passed and the
  question is still open.
* **Expansion is weighted by sales stage** (discovery 10% → negotiation
  80% → closed won 100%) and **contraction by risk priority** (high 60%,
  medium 30%, low 10%). Ordinary ladders, stated as assumptions.
* **The same ARR is never lost twice.** An account both renewing badly
  and carrying an open Risk contributes the *larger* of the two, never
  the sum.
* **The downside is capped at what the account pays.** A risk can be
  logged with any MRR on it; without the cap the worst case came out
  negative, which is a forecast saying the book will owe money.
* **`worst` only loses what can be lost this year** — not every account,
  because a floor nobody believes is a floor nobody uses. `best` closes
  the whole open pipeline and loses nothing.
* `nrr` is null rather than a fake 100% on an empty book.
* **Not a time series.** `Opportunity` and `Risk` carry no close date, so
  expansion can't be placed in a quarter; spreading pipeline evenly
  across the year would be inventing the one thing a forecast is asked
  for. The scenario range is how uncertainty is shown instead.

### `GET /api/v1/customers/usage/`

Auth: `IsAuthenticated`. Every rollup the Usage Overview dashboard's
Controls tab draws.

The screen answers one question — **are customers using what they pay
for?** — and the answer has two commercial halves: shelfware below the
line (seats billed and not used, which is a renewal argument being built
for you), and capacity above it (an account with no room left is an
expansion conversation nobody has started). Both are measured off
`Customer.seat_utilization_percentage`, which the model has always
computed and nothing had ever charted.

Rollups are computed server-side, like `/tickets/stats/` and
`/interactions/stats/`. The Health endpoint ships rows instead, and the
difference is deliberate: five Health tabs slice one book five ways,
while this screen has one view and asks the server one thing. `scatter`
is the exception — per-account by nature, capped at 500 points.

**Customer-level only.** Seats live on `Customer`; `Account` has no seat
fields at all, so an account-grain usage view would have nothing to
count.

Query params, each ignored when unparseable (the house convention):
`owner` (a user id, or the literal `unassigned`), `lifecycle` (a
`LifecycleStage` value), `customer` (an id).

**Response `200`** — `kpis`, `bands`, `adoption`, `scatter`,
`shelfware`, `at_capacity`, `currency`, `filters`.

```json
{
  "kpis": {
    "accounts": 12, "contracted_seats": 8055, "active_seats": 5228,
    "utilisation": 64.9, "idle_seats": 2827,
    "shelfware_arr": 275014.42, "at_capacity_arr": 175000.0,
    "at_capacity_count": 1,
    "unmeasured_count": 1, "measured_count": 11, "unpriced_count": 0
  },
  "bands": [{"key": "dormant", "name": "Dormant (<25%)", "accounts": 3,
             "arr": 135600.0, "idle_seats": 1141}],
  "adoption": [{"key": "4+", "name": "4+ products", "accounts": 2, "arr": 287000.0}],
  "scatter": [{"id": 9, "name": "Shopify", "utilisation": 92.0, "arr": 175000.0,
               "active_seats": 1380, "contracted_seats": 1500, "idle_seats": 120,
               "shelfware_arr": 0.0, "products": 6, "band": "at_capacity",
               "owner": "Carl CSM", "lifecycle_stage": "Live",
               "health_category": "good", "renewal_date": "2027-05-15"}],
  "shelfware": [], "at_capacity": [],
  "currency": "USD",
  "filters": {"owners": [], "lifecycles": [], "customers": []}
}
```

The rules worth knowing before reading any of those numbers:

* **Unmeasured is not zero.** A customer with no contracted seats
  recorded has *no* utilisation. Those rows are excluded from every
  average, left out of `bands` and out of `scatter` — a point on the
  axis is a claim, and "we don't know" isn't one — and counted in
  `unmeasured_count`. Scoring a data gap as the worst possible number
  invents the most alarming reading available and then charts it. Same
  rule the health rubric follows for a component it can't measure.
* **`utilisation` is seats over seats**, not the mean of each account's
  percentage: otherwise a ten-seat pilot weighs as much as a
  fifteen-hundred-seat rollout.
* **`shelfware_arr` is a proxy, deliberately.** `arr × (1 −
  utilisation)`, counted only below 75% — a customer using four fifths
  of what they bought is using what they bought. Contracts are rarely
  priced purely per seat, so this estimates exposure rather than
  calculating a refund. It is worth having because it is the only number
  on the screen that starts a QBR.
* **Over 100% is its own band**, not an error to clamp. More actives
  than the contract allows is real, common, and an expansion (sometimes
  compliance) trigger. Such an account has no idle seats and no
  shelfware.
* The two lists rank by **money, not by percentage**: a dormant ten-seat
  pilot is a worse ratio and a smaller problem than a half-used
  enterprise rollout, and only one of them is worth a call this week.
* `arr` is converted into `currency` through the same
  `convert_to_org_currency` hook everything else uses, and is null when
  no rate is configured — those rows still count in every *seat* figure
  and are counted in `unpriced_count`.


### Models — `Product`

What this tenant sells. One row per product per organisation, **unique
case-insensitively** — which is the entire point: "Product A" and
"product a" cannot both exist, so they cannot both appear on a
dashboard.

`Customer.primary_product` was free text until migration 0030, for the
same reason `churn_reason` was: nobody had decided where the list of
products lived. So Product Usage grouped by folding case, reported how
many spellings it had folded, and could never have merged "Integrations
Module" with "Integrations module (EU)".

**Why a table and not a `TextChoices` enum**, unlike `churn_reason`:
every tenant sells something different. A list compiled into the code
would be one organisation's product line imposed on all of them. Churn
reasons are universal; products are not.

Fields: `organisation`, `name`, `is_active`, `created_at`,
`updated_at`. Migration `0030` creates the model, `0031` adds the FK
beside the text column, `0032` fills it (one product per distinct name
per organisation, folded on case, the **most common** spelling winning
the row and ties broken alphabetically so the same database always
migrates the same way), and `0033` drops the text column and renames the
FK into its place. Four steps rather than one because a single
`makemigrations` swap would drop the column and add an empty FK,
silently losing every customer's product.

**Retiring vs deleting**: `is_active=False` takes a product out of the
pickers and leaves every figure ever reported against it intact.
Deleting is for a product added by mistake, and is refused while any
customer is recorded against it — `Customer.primary_product` is
`PROTECT`ed, so the database refuses too; the view catches it so the
caller gets the count and the alternative instead of a 500.

### `GET /api/v1/products/`, `POST /api/v1/products/`

Auth: `IsAuthenticated` to read, `CanManageOrgSettings` to write — the
pickers need the list, so any member may GET it; adding, renaming and
retiring are organisation configuration, because a CSM able to add
"Prodcut A" from a form would be the free-text problem this table
replaced, back through a different door. The tenant's own product
catalogue, ordered by name, unpaginated (a catalogue is tens of rows and
every consumer is a dropdown that wants all of them).

Mounted at its own top-level prefix rather than under `/customers/`, for
two reasons: a product is organisation configuration rather than one
customer's sub-resource, and `/api/v1/customers/products/` already means
the Product Usage dashboard's rollup. Same treatment as `Contact`.

**Deliberately not scoped by ownership**, unlike every other list in
this app. A product list is the shape of the business: a CSM who cannot
see "Product C" cannot record a customer on it, and the pickers on the
Add/Edit form would differ per user. Retired products are returned too —
the caller needs to see one to bring it back, and the pickers filter on
`is_active` rather than on the endpoint.

**Response `200`** — a bare list. `customers` counts the customers led
by that product, annotated in one query for the whole page:

```json
[{"id": 1, "name": "Product A", "is_active": true, "customers": 5,
  "created_at": "2026-09-12T13:51:02Z", "updated_at": "2026-09-12T13:51:02Z"}]
```

**POST** takes `{"name": "..."}` (and optionally `is_active`). Names are
trimmed, and a duplicate is refused with a `400` naming the product it
clashes with — the message that tells somebody the product they're
adding is already on the list under a different capitalisation. A blank
name is refused.

### `GET/PATCH/DELETE /api/v1/products/<id>/`

Auth: same split as the list — any member may GET, `CanManageOrgSettings`
to PATCH or DELETE — scoped to the caller's own organisation (another
tenant's product is a `404`, not a `403`).

**Renaming is cheap and deliberately so**: the customers point at the
row, so fixing a typo fixes it everywhere at once. That is the whole
difference from the free-text field this replaced, where a rename meant
editing every customer and hoping. A rename that collides with another
product is refused, case-insensitively; a product may of course keep its
own name while something else about it changes.

**DELETE is refused with a `400` while customers are recorded against
it**, giving the count and pointing at `is_active=false` instead.
Churned customers count — the Product Usage dashboard's churn figures
are the reason that row exists.

### `GET /api/v1/customers/products/`

Auth: `IsAuthenticated`. One row per product, behind the Product Usage
dashboard: **which products carry the book, and how are the customers on
each one doing?**

Not a second Usage Overview. That one reads seats across the whole book —
one utilisation rate, one shelfware list, a CS operations screen. This
compares *products against each other*: ARR led, health mix, utilisation,
satisfaction, support burden and churn, side by side. It is the screen a
product manager opens, and it answers the question nothing else here asks
— is one of these products quietly responsible for most of the churn?

**The limitation that has to be on the screen, not just in this doc.**
A customer records **one** product.
`additional_products_count` is a bare integer — nobody recorded *which*
other products a customer has, so there is nothing to attribute them to.
Every figure below therefore counts customers this product **leads**, and
a customer on three products is counted once, under their primary one.
The response carries `attribution` so the UI states this above the
numbers rather than leaving a reader to assume revenue has been split
across products. The `Product` model arrived in migration 0030; closing
this gap needs the other half — a per-customer join naming the rest —
and the data to fill it, which nobody has ever collected. A count is all
that was recorded.

Params: `owner`, `lifecycle`, `product` (a `Product` id, or `none` for
customers with no product recorded). Like the Customer Overview and
unlike the working dashboards, this reads the whole visible book
including churned customers: churn by product is half of what the screen
is for, and a product whose customers all left would otherwise read as a
product with no problems.

**Response `200`** — `rows`, `kpis`, `attribution`, `currency`,
`filters`.

```json
{
  "rows": [{
    "id": 1, "product": "Product A",
    "customers": 4, "arr": 443600.0, "share": 64.4, "unpriced": 0,
    "utilisation": 75.1, "contracted_seats": 1040, "active_seats": 781,
    "health": {"good": 4, "average": 0, "poor": 0},
    "healthy_share": 100.0, "unhealthy_arr": 0.0,
    "ces": 84.5, "nps": 57.5,
    "open_tickets": 33, "tickets_per_customer": 8.2,
    "churned": 0, "churned_arr": 0.0, "churn_rate": 0.0
  }],
  "kpis": {
    "products": 5, "customers": 9, "arr": 688600.0,
    "largest": {"product": "Product A", "share": 64.4, "arr": 443600.0},
    "weakest": {"product": "Product B", "unhealthy_arr": 203000.0,
                "healthy_share": 0.0, "customers": 3, "healthy": 0},
    "worst_churn": {"product": "Integrations Module",
                    "churned": 1, "churned_arr": 152600.0},
    "without_customers": []
  },
  "attribution": {"basis": "primary_product", "note": "Every figure counts customers whose *primary* product this is. ..."}
}
```

The decisions behind those numbers:

* **"Weakest" is ranked on the ARR sitting in accounts that are not in
  good health (`unhealthy_arr`), not on the share that are.** The first
  version ranked on `healthy_share` and answered with a product that had
  one unhappy customer worth $42K over one with three unhappy customers
  worth $203K. "Which product do we fix first" is answered in money.
  `largest` is by ARR led, `worst_churn` by the ARR that left — all three
  headlines rank on money, none on a percentage.
* **Utilisation is seats over seats, never the mean of per-account
  percentages** — the same rule the Usage Overview uses, so the two
  screens cannot report different utilisation for the same accounts. A
  product with no seats recorded has `null` utilisation, not 0%:
  unmeasured is not unused.
* **CES and NPS average only the customers who answered.** A product
  nobody surveyed has no score; a zero would make it the worst-rated
  product on the screen.
* **Customers with no product recorded get their own row**
  (`"No product recorded"`). "Nobody wrote down what they bought" is a
  finding about the CRM, and dropping those rows would make the shares
  add up to less than 100% with nothing on screen to explain it.
* **Products are rows, not spellings.** Until migration 0030
  `primary_product` was free text, this endpoint grouped by folding case
  and whitespace, and each row carried a `spellings` count so a reader
  could see the folding doing the work — "Product A" and "product a"
  would not group themselves, and "Integrations Module" and
  "Integrations module (EU)" never could. It groups by id now, and
  `spellings` is gone. Each row carries the product `id` (null for the
  unrecorded bucket).
* **Every product in the tenant's catalogue gets a row, even at zero.**
  A free-text field has no entry for a product nobody bought, so "we
  sell this and nobody is on it" was unsayable; `kpis.without_customers`
  names them. It is *not* called "unsold": the rows are the whole
  catalogue while the customers are scoped to the caller's own book and
  whatever filters are set, so under owner scoping a zero means "nobody
  in this selection". A product with no active customers but some
  churned ones is not in that list — it had customers, and the churn
  figures are the point of its row.
* **`churn_rate` is over everyone the product ever led** (active +
  churned), and a product with no active customers left still gets a row
  — that is the most important row on the screen when it happens, and
  both an ARR sort and a customer-count filter would have hidden it.
  Archiving is neither churn nor active, for the same reason as in the
  Customer Overview.
* **The product dropdown comes from the tenant's own catalogue**, so the
  dashboard and the Add/Edit form offer the same products and cannot
  disagree about what exists. The filter takes a product **id**, or the
  literal `none` for "no product recorded". A retired product stays in
  the dropdown while anyone is still recorded against it, because its
  history is still on this screen; a retired product nobody is on drops
  out.
* Tickets hang off a Customer or one of its Accounts and both count
  toward the product: support load on a division is support load on that
  product. Counted once for the whole page in a single annotated query.

### Models — `HealthSnapshot`

One row per Customer (or Account) per date, recording what that row's
health looked like then. `Customer.health_score` / `csm_pulse_score` /
`ai_pulse_value` only ever hold *today's* reading — updating them
overwrites yesterday's — so this is what remembers.

Exists for the Health Overview's **Movement** tab, which counts how many
accounts moved between Good/Average/Poor from one month to the next
rather than how many sit in each today. Those are different questions: a
month where nine accounts fell and nine recovered is indistinguishable
from a quiet one if you only ever count the current state.

Fields: `customer` / `account` (both nullable FKs, **exactly one set** —
enforced by a `CheckConstraint`, same shape and reasoning as `Activity`),
`captured_on` (date; month-end when backfilled, so a series lines up as
monthly columns), `health_score` (decimal 0.0–10.0 as it stood then),
`csm_pulse_score` and `ai_pulse_value` (1–5, nullable — as they stood
then, null if unrated/unscored at the time), `created_at`.

`health_category` and `ai_pulse_score` are derived here too, from *this
row's* stored numbers — so a snapshot reports its categories exactly the
way a live row does, and a snapshot of a Poor month keeps reading Poor
even after the parent recovers.

One snapshot per parent per date, enforced by two partial unique
constraints (partial, not `unique_together`: a null parent must not
collide with every other null parent on the same date). A Customer and
one of its Accounts may both have a snapshot for the same date.

Served to the dashboard by `GET /api/v1/customers/health/` (above), and
written by `manage.py run_health_maintenance`. `capture_health_snapshot(parent, captured_on)`
is the entry point for recording a reading (upserts, so a scheduled job
firing twice in a day is harmless), and `seed_demo_health_snapshots`
backfills a year of history for demo data. Nothing writes these on a
schedule yet.

### Models — `Activity`

Mirrors: `src/components/shared/ActivityFeed.tsx`'s "Activities" filter,
`src/components/organizations/activity/ActivitiesTab.tsx` (the card:
title, date, watchers/links counts), rendered on both the Organization
Details page's General tab and the standalone Account page.

A timeline entry belonging to **exactly one** of `Customer`
(organization-level) or `Account` (account-level), never both — modeled
as two nullable FKs rather than a `GenericForeignKey` (simpler for
exactly two possible parent types), enforced by a DB `CheckConstraint`
(`activity_belongs_to_exactly_one_parent`) rather than serializer
validation, since there's no create/update endpoint yet to run that
validation through — this round is **read-only**.

Fields: `type` (`TextChoices` — `value_reinforcement`,
`enablement_retraining`, `health_check_review`,
`product_usage_analysis`, `escalation_triggered`,
`onboarding_milestone`, `success_plan_created`, `success_plan_updated`,
`executive_alignment_session`, `renewal_proposal_submitted`, `other`),
`occurred_at` (date), `links`/`watchers` (counts shown on the card's
link/eye icons). No `pulse` field — the card's "Pulse" badge is
decorative in the mock (always shown, tied to nothing), so it stays
decorative here too. No `group` field either — the mock's date-grouping
key is just a different string format of `occurred_at` and is derived
on the frontend instead of duplicated in storage.

See `seed_demo_activities` management command for demo data (run after
`seed_demo_accounts`).

### `GET /api/v1/customers/<customer_id>/activities/`

Auth: `IsAuthenticated`. Every organization-level `Activity` for one
`Customer`, scoped to the caller's own organisation. **`404`, not an
empty list,** for a `customer_id` outside that scope — same convention
as the Account list endpoint above. Powers ActivityFeed's "Activities"
filter on the Organization Details page.

**Response `200`** — a plain array, each entry: `id`, `type`,
`type_display` (the human label, e.g. `"Health Check Review"` — the
card's title text), `occurred_at`, `links`, `watchers`.

### `GET /api/v1/customers/<customer_id>/accounts/<account_id>/activities/`

Auth: `IsAuthenticated`. Every account-level `Activity` for one
`Account`, scoped to both its `customer_id` and the caller's own
organisation — **`404`** for either mismatch, same reasoning as the
Account detail endpoint. Powers ActivityFeed's "Activities" filter on
the standalone Account page — same component as the Customer-scoped
endpoint above, reading a different scope.

**Response `200`** — same shape as the Customer-scoped list above.

### Models — `Email`

Mirrors: `src/components/shared/ActivityFeed.tsx`'s "Emails" filter,
`src/components/organizations/activity/EmailsTab.tsx` (the card:
subject, sender/recipient, a summarized body, watchers/links, a
starred flag) plus the thread panel it opens into.

Same "belongs to exactly one of `Customer` or `Account`" shape as
`Activity` (two nullable FKs + a DB `CheckConstraint`, read-only for
this round — see that model's own docstring for the full reasoning).

Fields: `subject`, `sender_name`/`recipient_name` (plain text, not a
FK — there's no Contact model yet, and the mock data often names a
team rather than a person, e.g. "Support Team"), `body` (the
summarized preview shown on the card), `sent_at` (a single
`DateTimeField` — the frontend formats it into the card's separate
date and time displays rather than storing two different strings),
`links`/`watchers` (the card's two counters), `is_starred` (the star
icon — a real per-item flag, unlike Activity's decorative "Pulse"
badge, so it's a real field here). `sender_avatar` isn't stored — the
frontend derives a placeholder avatar from `sender_name`, same as
before this model existed.

See `seed_demo_emails` management command for demo data (run after
`seed_demo_accounts`).

### `GET /api/v1/customers/<customer_id>/emails/`

Auth: `IsAuthenticated`. Every organization-level `Email` for one
`Customer`, scoped to the caller's own organisation — same
404-not-empty-list convention as the Activity list endpoint. Powers
ActivityFeed's "Emails" filter on the Organization Details page.

**Response `200`** — a plain array, each entry: `id`, `subject`,
`sender_name`, `recipient_name`, `body`, `sent_at`, `links`,
`watchers`, `is_starred`.

### `GET /api/v1/customers/<customer_id>/accounts/<account_id>/emails/`

Auth: `IsAuthenticated`. Every account-level `Email` for one `Account`,
scoped to both its `customer_id` and the caller's own organisation —
same reasoning as the Activity account-level endpoint. Powers
ActivityFeed's "Emails" filter on the standalone Account page.

**Response `200`** — same shape as the Customer-scoped list above.

### Models — `Task`

Mirrors: `src/components/shared/ActivityFeed.tsx`'s "Tasks" filter,
`src/components/organizations/activity/TasksTab.tsx` (the card:
title, assignee, due date, priority, status).

Same "belongs to exactly one of `Customer` or `Account`" shape as
`Activity`/`Email` (read-only for this round).

Fields: `title`, `assignee_name` (plain text, not a FK — same
reasoning as Email's `sender_name`/`recipient_name`: the mock's names
aren't real signed-up users in any seeded organisation), `due_date`,
`priority` (`TextChoices` — `high`/`medium`/`low`), `status`
(`TextChoices` — `pending`/`in-progress`/`completed`, defaults to
`pending`). No `group` field — the card's "Overdue"/"This Week"/"Next
Week"/"Later" bucket is a function of `due_date` and the current date,
computed on the frontend at render time rather than stored (a stored
bucket would go stale the moment a week rolls over).

See `seed_demo_tasks` management command for demo data (run after
`seed_demo_accounts`) — unlike Activity/Email's fixed calendar dates,
its due dates are computed as offsets from the date the command is
run, since a Task's due date is inherently relative to "now" in a way
a past event's date isn't.

### `GET /api/v1/customers/<customer_id>/tasks/`

Auth: `IsAuthenticated`. Every organization-level `Task` for one
`Customer`, scoped to the caller's own organisation — same
404-not-empty-list convention as the Activity list endpoint. Powers
ActivityFeed's "Tasks" filter on the Organization Details page.

**Response `200`** — a plain array, each entry: `id`, `title`,
`assignee_name`, `due_date`, `priority`, `status`.

### `GET /api/v1/customers/<customer_id>/accounts/<account_id>/tasks/`

Auth: `IsAuthenticated`. Every account-level `Task` for one `Account`,
scoped to both its `customer_id` and the caller's own organisation —
same reasoning as the Activity account-level endpoint. Powers
ActivityFeed's "Tasks" filter on the standalone Account page.

**Response `200`** — same shape as the Customer-scoped list above.

### `GET /api/v1/tasks/`

Auth: `IsAuthenticated`. Every `Task` across every Customer/Account the
caller's own organisation owns, organisation-level and account-level
alike — the one Task view spanning every company at once, same
top-level reasoning as `/opportunities/`/`/risks/`. **Pagination is
off** — a small, whole-collection list.

Powers Cockpit's own "My Tasks" panel (react-ts-app's
`src/pages/copilot/CockpitView.tsx`), which used to read from an
entirely separate, purely local mock Redux list
(`features/tasks/tasksSlice.ts`) with no relation to this real model —
that mock list is untouched (`CallSenseTab.tsx`'s own, unrelated
"Create Tasks from Actions" mockup still uses it).

`?mine=true` additionally filters to Task rows whose parent
Customer/Account's own `owner` is the caller — one CSM's own assigned
book. Without it, this lists every Task the caller can *see*, which is
wider than "mine": it also covers unowned parents and ones reached
through the other side of the Customer/Account relationship. It is no
longer a plain tenant-wide list.

**Response `200`** — a plain array, each entry adds `priority_display`,
`status_display`, `parent_name`, and `parent_type`
(`"customer"`/`"account"`) to the nested shape above — a flat list
spanning every company needs to say which one each row belongs to,
same reasoning as `OpportunitySerializer`'s own `account_name`.

### `PATCH /api/v1/tasks/<id>/`

Auth: `IsAuthenticated`. `{ "status": "pending" | "in-progress" | "completed" }`,
nothing else is writable here. Scoped exactly like the list (a task on a
parent the caller cannot see is `404`). Powers Cockpit's tick-off; audited as
`task.update` with `from`/`to`. Returns the row in the list shape.

### Models — `Note`

Mirrors: `src/components/shared/ActivityFeed.tsx`'s "Notes" filter,
`src/components/organizations/activity/NotesTab.tsx` (the card:
title, author ("Logged by"), body, date, a link count shown only
when positive).

Same "belongs to exactly one of `Customer` or `Account`" shape as
`Activity`/`Email`/`Task` (read-only for this round).

Fields: `title`, `author_name` (plain text, not a FK — same reasoning
as Task's `assignee_name`), `body`, `logged_at`, `links` (a real
field — the frontend mock's card always showed a hardcoded "1 Links"
regardless of the note, which was a bug, not a deliberate decoration
the way Activity's "Pulse" badge is; this makes it a real per-note
count, shown on the card only when greater than zero). No `tags`
field — the mock carries one, but the component that renders it never
displays it, so there's no card field to back. No `group` field
either — the card's date-group header is derived from `logged_at` at
render time.

See `seed_demo_notes` management command for demo data (run after
`seed_demo_accounts`) — `links` varies across 0 and a few positive
counts on purpose, to exercise both the shown and hidden states.

### `GET /api/v1/customers/<customer_id>/notes/`

Auth: `IsAuthenticated`. Every organization-level `Note` for one
`Customer`, scoped to the caller's own organisation — same
404-not-empty-list convention as the Activity list endpoint. Powers
ActivityFeed's "Notes" filter on the Organization Details page.

**Response `200`** — a plain array, each entry: `id`, `title`,
`author_name`, `body`, `logged_at`, `links`.

### `GET /api/v1/customers/<customer_id>/accounts/<account_id>/notes/`

Auth: `IsAuthenticated`. Every account-level `Note` for one `Account`,
scoped to both its `customer_id` and the caller's own organisation —
same reasoning as the Activity account-level endpoint. Powers
ActivityFeed's "Notes" filter on the standalone Account page.

**Response `200`** — same shape as the Customer-scoped list above.

### Models — `Ticket`

Mirrors: `src/components/shared/ActivityFeed.tsx`'s "Tickets" filter,
`src/components/organizations/activity/TicketsTab.tsx` (the card:
assignee, date, title + ticket number, a status icon, a priority
flag icon, a link count shown only when positive).

Field set was reverse-engineered from the card rather than dictated
up front. Same "belongs to exactly one of `Customer` or `Account`"
shape as `Activity`/`Email`/`Task`/`Note` (read-only for this round).

Fields: `ticket_number` (e.g. `"TKT-1042"`), `title`, `assignee_name`
(plain text, not a FK — same reasoning as Task's `assignee_name`: the
mock's names are often a team, e.g. "Support Team"), `status`
(`TextChoices` — `open`/`in-progress`/`resolved`/`closed`, defaults to
`open` — already colored the card's status icon in the mock),
`priority` (`TextChoices` — `critical`/`high`/`medium`/`low` — the
mock carried real priority values but never actually used them to
style the flag icon, which rendered identically regardless; this pass
wires it up, same bug shape as the old hardcoded "1 Links" text),
`links` (a real field — same fix as `Note.links`, shown on the card
only when greater than zero). No `description` field — the mock's
own `TicketItem` type carries one, but no component renders it
anywhere (no ticket detail view exists), so there's no card field to
back it, same reasoning as `Note`'s excluded `tags`. No `group`
field — the card's date-group header is derived from `opened_at` at
render time.

See `seed_demo_tickets` management command for demo data (run after
`seed_demo_accounts`) — `links` and `priority` both vary across their
full range on purpose, to exercise every card state.

`Ticket` also carries `sentiment` and the AI taxonomy, which it shares
with `Email` and `Call` — see **Models — the AI taxonomy** below.
`sentiment` used to be declared on `Ticket` itself; `Ticket.Sentiment`
still resolves, through the shared abstract model. It additionally has
`connector` (where the ticket came from), `resolved_at`, and an
`on-hold` status, none of which are documented above yet — see the
model's own docstring.

### `GET /api/v1/customers/<customer_id>/tickets/`

Auth: `IsAuthenticated`. Every organization-level `Ticket` for one
`Customer`, scoped to the caller's own organisation — same
404-not-empty-list convention as the Activity list endpoint. Powers
ActivityFeed's "Tickets" filter on the Organization Details page.

**Response `200`** — a plain array, each entry: `id`, `ticket_number`,
`title`, `assignee_name`, `status`, `priority`, `opened_at`, `links`.

### `GET /api/v1/customers/<customer_id>/accounts/<account_id>/tickets/`

Auth: `IsAuthenticated`. Every account-level `Ticket` for one
`Account`, scoped to both its `customer_id` and the caller's own
organisation — same reasoning as the Activity account-level endpoint.
Powers ActivityFeed's "Tickets" filter on the standalone Account page.

**Response `200`** — same shape as the Customer-scoped list above.

### `GET /api/v1/tickets/stats/`

Auth: `IsAuthenticated`. Every rollup the Ticket Overview
dashboard's Controls tab needs, in one response. Unfiltered by default
to avoid the trap of a rolling window on seeded demo data with fixed
dates: calling today after the seeds' 2026 dates renders everything
empty, which looks like a broken integration. Callers that want a window
ask for one.

Query params all ignore a value they don't understand rather than
returning `400`, same convention as `?renewal_within=` and the
interactions stats endpoint:

| Param | Meaning |
|---|---|
| `from`, `to` | `YYYY-MM-DD`, inclusive, against `opened_at`. |
| `priority` | `critical`/`high`/`medium`/`low`. |
| `owner` | User id. |
| `customer` | Customer id. Includes that customer's accounts' tickets. |
| `account` | Account id. |
| `connector` | Connector id. |
| `drill` | Opens one chart segment into the companies behind it — see **Dashboard drill (`?drill=`)** above. |

**Response `200`** (no `drill`, or an unrecognised one) — `kpis`,
`priority`, `status`, `origin`, `assignees`, `sentiment_timeline`,
`filters`:

- `kpis`: `total` (all tickets), `on_hold` (on-hold status),
  `avg_lifetime_days` (average days from open to resolved/closed, or `null`
  if none resolved yet), `resolution_rate` (percent resolved/closed),
  `positive_sentiment` (positive-sentiment tickets), `negative_sentiment`
  (negative-sentiment tickets), `open_count` (tickets not in
  resolved/closed statuses), `oldest_open_days` (days since the oldest
  open ticket was opened, or `null` if no open tickets). Like every other
  KPI, these two follow the request's filters, including `from`/`to` on
  `opened_at`: a windowed call counts only the open tickets *opened* in that
  window, so a caller that wants every open ticket (the Overview card)
  calls without `from`/`to`.
- `priority`: `[{name, value}, ...]` — all four priority levels, even
  empty ones, ordered as they appear on the form.
- `status`: `[{name, value}, ...]` — all five statuses, even empty ones.
- `origin`: `[{name, value, provider, connector_id}, ...]` —
  connector name or "Revenact" if unattached, ordered by count descending.
- `assignees`: `[{name, total, [Status.label]: count, ...}, ...]` —
  per-assignee breakdown by status, ordered by total ascending (quietest
  first) for a horizontal chart.
- `sentiment_timeline`: `[{date, positive, negative}, ...]` — bucketed
  by month of `opened_at`.
- `filters`: read-only; ships with the response so the filter bar needs
  no second round trip.

### Models — the AI taxonomy (`Email`, `Call`, `Ticket`)

Mirrors: `src/pages/dashboard/tabs/ai-trending/ControlsView.tsx` — the
AI Trending Topics dashboard's seven charts.

Three fields plus a timestamp, declared once on an abstract model
(`AIClassified` in `services/customers/models.py`) and shared by the
three record types that dashboard counts — what the backend calls an
**interaction**. One definition rather than three, because the whole
point is to chart all three together: a donut slicing emails, calls and
tickets by sentiment is only meaningful if "negative" means the same
thing in all three.

* `sentiment` — `positive`/`neutral`/`negative`, defaults to `neutral`.
  The same three values as `Contact.sentiment`, deliberately.
* `ai_area` — `product_growth`/`support_operations`/`customer_success`.
  Which side of the business owns the conversation. Blank until
  something classifies it.
* `ai_category` — ten values (`onboarding`, `bug_report`,
  `workflow_automation`, `integration_support`, `system_notification`,
  `account_management`, `feature_request`, `customer_feedback`,
  `reporting_analytics`, `security_compliance`).
* `ai_subcategory` — twenty-five values, each belonging to **exactly
  one** category. Enforced in `clean()`: "API Issue" under "Onboarding"
  is a contradiction, not a judgement call, and the dashboard's Category
  and Subcategory bars are read together. A category with no
  subcategory is fine; a subcategory with no category is not.
* `ai_classified_at` — read-only in practice. Null means nothing has
  ever classified this row, which is what `classify_interactions` looks
  for and what separates "no opinion yet" from a deliberate blank.

The vocabulary lives in `services/customers/taxonomy.py`, including the
category → subcategory map, and it is closed on purpose: free text
would let a model invent a new bucket per call and the Category bar
would grow a tail of synonyms nobody can chart. Area is **not** derived
from category — who owns a conversation and what it is about are two
different questions, and the mock this replaced showed the same
category under two different areas.

`Activity`, `Note` and `Task` deliberately don't carry any of this. An
Activity is something *we* did, with no customer voice in it to read a
sentiment from.

**Who writes it.** `manage.py classify_interactions` — a real, paid
Claude call per batch of 20, through the same
`services/copilot/anthropic_client.py` the Copilot and Headlines use.
It only touches rows with no `ai_classified_at` unless `--reclassify` is
given, so it is safe on a schedule; and even `--reclassify` skips rows a
person corrected (`classification_corrected_at`, see the correction
endpoint under `metrics`) unless `--include-corrected` is passed; `--dry-run` counts the work without calling anything and
`--limit` caps the spend. On a `--reclassify` pass a row the model
declines to place has its previous tags **cleared** (and is stamped, so
a scheduled pass doesn't pay to retry it): the first real run over the
demo book declined two calls that had a title and no summary, and
without this they would have kept the seeder's invented category under
a fresh stamp. It is deliberately **not** bundled into
`run_health_maintenance`, which is free and idempotent.
`manage.py seed_demo_classifications` fills the same fields from a
keyword table instead, so a demo database has full charts with no API
key configured.

### Models — `Call`

Mirrors: `src/components/organizations/activity/CallSenseTab.tsx` (that
tab still reads its own `CALLSENSE_DATA` mock — this model exists for
the dashboard, and wiring CallSense to it is a separate piece of work).

A call that happened. Same "belongs to exactly one of `Customer` or
`Account`" shape as `Activity`/`Email`/`Ticket`, and read-only — there
is no create/update endpoint.

Distinct from `CalendarEvent`, which is a *scheduled* meeting: that one
is a plan and can be in the future, this one took place and has a
duration and a sentiment to read.

Fields: `title`, `host_name` (plain text, not a FK — same reasoning as
`Ticket.assignee_name`, and the host is as often external as ours),
`occurred_at` (a **datetime**, unlike `Activity`/`Ticket`'s plain
dates — the CallSense card renders a time, and two calls in a day are
ordinary), `duration_minutes` (nullable — "the recorder didn't say" is
not a zero-minute call), `summary` (the recap the card shows; blank for
an unsummarised call), `connector` (the recorder — `Connector.Provider`
gained `zoom` for this; null means logged by hand in Revenact, a real
case), `links`, plus the shared taxonomy above. No transcript field —
it would be megabytes per row, nothing renders one, and a copy we
can't keep in sync is a liability.

`Call.clean()` enforces that the recorder actually covers the call's
own company, the same invariant `Ticket.clean()` enforces for the same
reason and in the same place.

See `seed_demo_calls` (run after `seed_demo_connectors`) for demo data.

### `GET/POST /api/v1/customers/<id>/calls/`, `.../accounts/<id>/calls/` — CallSense

Auth: `IsAuthenticated`, same 404-not-empty-list scoping as the other
nested lists. `GET` is the company's calls, newest first, each with
`sentiment`/`ai_area`/`ai_category`, `connector_name` (the recorder, or
null), `logged_by {id, name}` (null for synced or seeded calls),
`recording_url`, and `transcript` (an Attachment row, or null).

`POST` logs a call: `title`, `occurred_at` (datetime), optional
`host_name` (defaults to the caller), `duration_minutes`, `summary`,
`recording_url`. Instead of writing the summary the caller may send
`transcript_text` (JSON) or a `transcript` file (multipart; .txt/.vtt/
.srt/.md, same rules as Files below): the transcript is kept as an
Attachment (`source: "transcript"`) on the same company, and when
`summary` is blank the model writes one from it (`purpose:
"call_summary"`; no model configured means an empty summary, not an
error). The new call is classified for sentiment straight away so the
Account Pulse counts it. Audit event `call.log`.

### Opportunities and risks are read department-wise

`Opportunity.department` / `Risk.department` (`User.Function` or blank)
say whose pipeline an item is on; both serializers carry `department` and
`department_display`, and a new item lands on its creator's department
unless the form says otherwise. `services/customers/scoping.py:
pipeline_visible_q(user)`: a person sees their own department's items
plus the undeparted ones; a role holding `view_all_accounts`, and anyone
in Leadership, see every department. Applied to every opportunity and
risk list (standalone and nested) and to the detail endpoints (another
department's item is a 404). The Pipelines page's filter narrows further
by department, priority and stage on the client.

### Contact sentiment is computed (`services/customers/contact_sentiment.py`)

A contact's `sentiment` pill is no longer only hand-set. Once a person has
any **classified** interaction — a call they were a participant on, an
email from their address, a ticket they raised — it is recomputed from
those: each is +1 / 0 / −1 by its sentiment, weighted by kind (call 1.0,
ticket 0.8, email 0.6) and by recency (≤30 d 1.0, ≤90 d 0.6, ≤365 d 0.3,
older 0.1); the weighted average above 0.25 is positive, below −0.25
negative, else neutral. `ContactSerializer` carries `sentiment_source`
(`manual` | `computed`), `sentiment_evidence` (`{score, calls, emails,
tickets, positive, neutral, negative, latest_at}`) and
`sentiment_computed_at`. A contact with no evidence keeps the hand-set
value; a `PATCH` of `sentiment` marks it `manual` again until evidence
returns. `last_contacted_at` moves forward with their newest interaction
(a call counts as contact even before it is classified).

Recomputed whenever records are classified (`classify_records`), when a
call is logged, and for every contact in `run_health_maintenance`.

`Call.participants` (write `participant_ids`, read `participants
[{id, name, role_display, sentiment}]`) says who from the customer's side
was on a call; only contacts of that company are accepted, and a
transcript that names a contact (full name or email address) links them
automatically.

### `GET /api/v1/contacts/<id>/interactions/`

What the sentiment rests on: `{sentiment, score, source, evidence,
interactions: [{kind: "call"|"email"|"ticket", id, title, snippet, when,
sentiment, ai_category}]}`, newest first. Same scoping as the contact.

## Files — the Files tab on organisations and accounts

### `GET/POST /api/v1/customers/<id>/files/`, `.../accounts/<id>/files/`

Auth: `IsAuthenticated`; anyone who may open the company may list and
add. `POST` is multipart: `file`, optional `description`. What is
accepted (SOC2:API-10, `services/customers/files.py`): a closed list of
document, image, transcript and audio types by extension *and* declared
content type (never HTML, SVG, scripts or archives), magic bytes checked
for the binary formats, `ATTACHMENT_MAX_BYTES` (25 MB) cap, the name
sanitised. The bytes live under `MEDIA_ROOT` (a private volume on the
VM, in the nightly backup) under a random name; the storage path is
never exposed.

```json
[{"id": 7, "name": "Signed MSA.pdf", "content_type": "application/pdf", "size": 183220,
  "description": "Countersigned 12 Sep", "source": "upload",
  "uploaded_by": {"id": 3, "name": "Carl"}, "download_url": "/api/v1/files/7/download/",
  "created_at": "2026-09-16T09:00:00Z"}]
```

### `GET /api/v1/files/<id>/`, `DELETE`, `GET /api/v1/files/<id>/download/`

Reached by id from any company's tab, so mounted at their own prefix;
each checks the caller may open the company the file hangs off (404
otherwise). `DELETE` is for the uploader or a `manage_org_settings`
holder (403 otherwise) and removes the bytes too. The download is always
`Content-Disposition: attachment` with `X-Content-Type-Options: nosniff`,
a sandboxed CSP and `Cache-Control: private, no-store`, so user content
is never rendered as part of the site. Audit events `file.upload`,
`file.delete`.

### `GET /api/v1/interactions/stats/`

Auth: `IsAuthenticated`. Every rollup the AI Trending Topics
dashboard's Controls tab draws, in one response — counted across
`Email`, `Call` and `Ticket` together.

Its own top-level prefix rather than a nest under `/customers/` or
`/tickets/`: an interaction spans three models and every
Customer/Account at once, so it belongs under neither. Stats-only, with
no matching list — each record type already has its own scoped list
endpoint for the Activity Feed.

One endpoint rather than seven for the same reason `/tickets/stats/` is
one: the seven charts are seven projections of the same filtered set,
and splitting them would apply the same filters seven times and let the
charts disagree with each other mid-render. Internally it is **one
grouped query per model**, not one per chart.

**Unfiltered by default**, same deliberate choice and same trap as
`/tickets/stats/`: a rolling "last 30 days" default renders every chart
empty once real time moves past the seeded demo dates, which looks like
a broken integration rather than an empty window.

Query params — every one ignores a value it doesn't understand rather
than returning `400`, the house convention for dashboard filters:

| Param | Meaning |
|---|---|
| `type` | `email`/`call`/`ticket`. **Repeatable**; drops whole record types. |
| `sentiment` | `positive`/`neutral`/`negative`. |
| `area`, `category`, `subcategory` | Taxonomy values (not labels). |
| `customer` | Customer id. Includes that customer's accounts' interactions. |
| `account` | Account id. |
| `revenue_bracket` | `under_25k`/`25k_50k`/`50k_100k`/`over_100k`, read off the parent's ARR (`Customer.arr_billed_at_account` or `Account.arr`). |
| `from`, `to` | `YYYY-MM-DD`, inclusive, against each model's own "when it happened" field. |
| `drill` | Opens one chart segment into the companies behind it — see **Dashboard drill (`?drill=`)** above. |

**Drill.** `?drill=` narrows the same filtered querysets the rollups
above use: `all`; `type:<email\|call\|ticket>`; `sentiment:<value>`;
`area:<value>`; `category:<value>`; `subcategory:<value>`; `value_label`
`interactions`. Response shape and the two drill rules (same filtered
set as the totals, intersected with the viewer's own book; a bad value
falls back to the normal response, never `400`) are in **Dashboard drill
(`?drill=`)** above. `value` is the number of interactions in that
segment belonging to the company; a company the totals above wouldn't
have counted — one the viewer can't see, or personal mail belonging to
someone else's mailbox chain — never appears, same
`visible_customers`/`visible_emails` rules the rest of this endpoint
already applies.

**Response `200`** (no `drill`, or an unrecognised one)

```json
{
  "total": 2090,
  "classified": 1832,
  "by_type":     [{"key": "email", "name": "Email", "value": 876}],
  "sentiment":   [{"key": "positive", "name": "Positive", "value": 1200}],
  "areas":       [{"key": "product_growth", "name": "Product & Growth", "value": 57}],
  "categories":  [{"key": "onboarding", "name": "Onboarding", "value": 20}],
  "subcategories": [{"key": "setup_assistance", "name": "Setup Assistance", "value": 9}],
  "sentiment_timeline": [
    {"date": "Jun 15, 2025", "positive": 5, "neutral": 2, "negative": 1}
  ],
  "recent": [
    {
      "id": "ticket:41",
      "source": "Ticket",
      "account": "Hyatt Regency Brand Portfolio",
      "title": "Export to CSV not including all columns",
      "sentiment": "Neutral",
      "area": "Product & Growth",
      "category": "Reporting & Analytics",
      "subcategory": "Export Problem",
      "occurred_on": "2026-03-03"
    }
  ],
  "filters": { "customers": [], "accounts": [], "types": [], "sentiments": [],
               "areas": [], "categories": [], "subcategories": [],
               "revenue_brackets": [] }
}
```

Notes on the shape:

* `{key, name, value}` everywhere — `name` is the label a chart renders,
  `key` the value its filter sends back. No colours: every real-data
  chart in this frontend maps a name to a CSS variable itself.
* `by_type`, `sentiment` and `areas` keep **every** bucket, at zero if
  empty, in the vocabulary's own order — a donut whose segment vanishes
  is harder to read than one with an empty segment, and a legend that
  reorders itself between refreshes is harder still. `categories` and
  `subcategories` are ranked biggest-first with empties dropped,
  because they are horizontal bars where the ranking *is* the reading.
* **`total` counts everything; the three taxonomy breakdowns count only
  classified rows.** An unclassified interaction is left out of them
  rather than bucketed as "Unknown" — `classified` is there so the
  screen can say how much of the book it is describing.
* `sentiment_timeline` is bucketed by **week**, on each model's own
  "when it happened" field (`sent_at`/`occurred_at`/`opened_at`), never
  `created_at` — that is `auto_now_add`, so every seeded row shares one
  timestamp and a trend over it is a single spike. Weeks with nothing
  in them are left out rather than zero-filled.
* `recent` is capped at 50 rows, newest first across all three models.
  It is a "recent examples" table, not a record browser. An
  unclassified row still appears, with its taxonomy columns blank —
  unlike the charts, the table is a list of what happened.
* `filters` ships alongside the numbers so the filter bar needs no
  second round trip and its options are scoped exactly as the numbers
  are. Each `subcategories` entry carries its parent `category`, so the
  bar can narrow that dropdown to the category already chosen.

### Models — `CalendarEvent`

Mirrors: `src/components/shared/ActivityFeed.tsx`'s "Calendar Events"
filter, `src/components/organizations/activity/CalendarEventsTab.tsx`
(the card: a type icon/badge, title, description, start–end time, an
attendee count, grouped by date).

Same "belongs to exactly one of `Customer` or `Account`" shape as
`Activity`/`Email`/`Task`/`Note`/`Ticket` (read-only for this round).
Unlike `Ticket`'s card, nothing here was found unwired in the mock —
`type` already colored the icon/badge for real, and every other field
maps to a real value.

Fields: `title`, `description`, `type` (`TextChoices` —
`meeting`/`call`/`review`/`demo`), `event_date`, `start_time`,
`end_time`, `attendee_count`. The mock's own `attendees` field carries
a full array of names, but the card only ever renders
`attendees.length` ("N attendees"), never the names — so this stores
`attendee_count` directly rather than a list no UI surface displays.
No `group` field — the card's date-group header is derived from
`event_date` at render time.

See `seed_demo_calendar_events` management command for demo data (run
after `seed_demo_accounts`).

### `GET /api/v1/customers/<customer_id>/calendar-events/`

Auth: `IsAuthenticated`. Every organization-level `CalendarEvent` for
one `Customer`, scoped to the caller's own organisation — same
404-not-empty-list convention as the Activity list endpoint. Powers
ActivityFeed's "Calendar Events" filter on the Organization Details
page.

**Response `200`** — a plain array, each entry: `id`, `title`,
`description`, `type`, `event_date`, `start_time`, `end_time`,
`attendee_count`.

### `GET /api/v1/customers/<customer_id>/accounts/<account_id>/calendar-events/`

Auth: `IsAuthenticated`. Every account-level `CalendarEvent` for one
`Account`, scoped to both its `customer_id` and the caller's own
organisation — same reasoning as the Activity account-level endpoint.
Powers ActivityFeed's "Calendar Events" filter on the standalone
Account page.

**Response `200`** — same shape as the Customer-scoped list above.

### Models — `Contact`

Mirrors: `src/components/organizations/contactsData.ts`'s mock
`Contact` shape (name, role, email, phone, status, sentiment, last
contacted). Unlike `Activity`/`Email`/`Task`/`Note`/`Ticket`/
`CalendarEvent` above — which each back one filter *within*
ActivityFeed — Contact backs its own sibling tab: the Contacts tab on
the Organization Details page, the Contacts tab on the standalone
Account page, and the global `/contacts/list` page.

Same "belongs to exactly one of `Customer` or `Account`" shape as
every model above (two nullable FKs + a `CheckConstraint`). `role` is
a closed set matching the mock's own 7 MEDDIC-style stakeholder roles
plus an `OTHER` catch-all; `status` (`active`/`inactive`) and
`sentiment` (`positive`/`neutral`/`negative`) are the card's pill/dot.
`last_contacted_at` is a real datetime, not the mock's frozen "2 hours
ago" string — the frontend formats it relative-to-now itself
(date-fns), so it stays accurate as time passes. `avatar` isn't
stored, same as every other entity's avatar in this codebase — derived
from `name` on the frontend.

`Contact.company` (a Python property, not a DB column) resolves to the
ultimate parent `Customer`: itself for an org-level contact, or its
account's own customer for an account-level one. `ContactSerializer`
exposes this as `company_id`/`company_name` (plus `account_name`,
`None` for an org-level contact) — needed by the standalone
`/contacts/list` page, which spans every Customer/Account at once and
can't assume which FK is set the way the two nested views below can.

See `seed_demo_contacts` management command for demo data (run after
`seed_demo_accounts`).

### `GET/POST /api/v1/customers/<customer_id>/contacts/`

Auth: `IsAuthenticated`. GET: every `Contact` under this `Customer`,
rolled up from both levels a Contact can exist at — organisation-level
(directly on this Customer) *and* account-level (on any of its
Accounts) — scoped to the caller's own organisation, same
404-not-empty-list convention as the Activity list endpoint. Which
level a row is at is `account_name`: `null` for organisation-level,
that Account's name otherwise. Powers the Organization Details page's
own Contacts tab, which renders both together.

POST always adds an organisation-level Contact here; `customer` is
taken from the URL, never client-supplied, same as
`AccountListCreateView`'s own `customer`. An account-level Contact is
added via the account-scoped endpoint below instead — including from
the Organization Details page's own Add Contact form, once the caller
picks one of this customer's accounts in its own optional Account
field — and, via a customer_id the frontend picks from a dropdown
rather than a URL param, the standalone `/contacts/list` page's own
"Add Contact".

**Response `200`** (GET) — a plain array, each entry: `id`, `name`, `role`,
`role_display`, `email`, `phone`, `status`, `sentiment`,
`last_contacted_at`, `company_id`, `company_name`, `account_name`
(`null` for an organisation-level row, that Account's name for an
account-level one). **Response `201`** (POST) — one such entry.

### `GET/POST /api/v1/customers/<customer_id>/accounts/<account_id>/contacts/`

Auth: `IsAuthenticated`. GET: every account-level `Contact` for one
`Account`, scoped to both its `customer_id` and the caller's own
organisation — same reasoning as the Activity account-level endpoint.
POST: adds a new account-level Contact to it; `account` taken from the
URL. Powers the standalone Account page's own Contacts tab.

**Response `200`**/**`201`** — same shape as the Customer-scoped
endpoint above, `account_name` set to that account's own name.

### `GET /api/v1/contacts/`

Auth: `IsAuthenticated`. Every `Contact` across every `Customer`/
`Account` the caller's own organisation owns — org-level and
account-level alike. The one Contact view not nested under
`/customers/<id>/...` (mounted directly in the project's root
`config/urls.py` instead), since it spans every Customer/Account at
once. Powers the standalone Contacts page (`/contacts/list`).

Paginated with the shared `DEFAULT_PAGINATION_CLASS`/`PAGE_SIZE`
(unlike every other Contact/Activity/Email/... list endpoint above,
which turns pagination off for what's normally a single entity's
already-small nested list) — same reasoning as `GET /api/v1/customers/`.

Query params:
- `?search=` — matches `name`/`email`/`role` (substring, case-
  insensitive), same convention as the Customer list's own search.
- `?company=<customer_id>` — filters to one company, matching a
  contact directly on that `Customer` or on any of its `Account`s.

**Response `200`**
```json
{
  "count": 16,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 2,
      "name": "James Wilson",
      "role": "champion",
      "role_display": "Champion",
      "email": "j.wilson@apple.com",
      "phone": "+1 (408) 555-0456",
      "status": "active",
      "sentiment": "positive",
      "last_contacted_at": "2026-09-02T04:35:18.707403Z",
      "company_id": 6,
      "company_name": "Apple Inc",
      "account_name": null
    }
  ]
}
```

### `GET /api/v1/contacts/stats/`

Auth: `IsAuthenticated`. Aggregate rollups for the standalone Contacts
page's MetricsPanel (Total/Active/Sentiment/Growth cards), across every
`Contact` the caller's organisation owns — same "spans everything, not
just the current page" reasoning as `GET /api/v1/customers/stats/`.

`growth_30d_pct` compares today's total against the total as of 30 days
ago (contacts whose `created_at` already predates the cutoff) — the
only "growth" there's real data for; there's no historical daily-
snapshot table to compare a true count-30-days-ago against anything
richer. `null` (not `0`) when there were no contacts yet 30 days ago,
since a percentage change off a zero base is undefined, not zero.

### `GET/PATCH/DELETE /api/v1/contacts/<id>/`

Auth: `IsAuthenticated`. A single Contact, scoped to the caller's own
organisation via either its `customer` or its `account`'s own
`customer` — 404 (not 403) outside that scope, regardless of whether
it's an organization- or account-level Contact.

Deliberately flat, not nested under `/customers/<id>/...` like the two
list-create endpoints above — Edit/Delete only ever needs the
Contact's own id, and the standalone `/contacts/list` page's own rows
don't carry an `account_id` to nest under even if it wanted to (only
`company_id`/`account_name`, for display). Powers the "Edit"/"Delete"
row actions on all three Contacts UIs.

PATCH can't move a Contact between parents — `customer`/`account`
aren't in `ContactSerializer`'s own field list at all, so naming
either in the request body is silently ignored, not an error, same
effect as `AccountSerializer`'s `read_only_fields = ["customer"]`.

**Response `200`** (GET/PATCH) — same shape as the list endpoints
above. **Response `204`** (DELETE) — empty body.

**Response `200`**
```json
{
  "total": 16,
  "active": 14,
  "sentiment": { "positive": 9, "neutral": 3, "negative": 4 },
  "sentiment_pct": { "positive": 56, "neutral": 19, "negative": 25 },
  "growth_30d_pct": 433.3
}
```

### Models — `Opportunity`

Mirrors: `src/pages/pipelines/PipelinesPage.tsx`'s mock `PipelineCard`/
`Column` shape — a sales opportunity moving through the standalone
Pipelines board's "Opportunities" tab.

Same "belongs to exactly one of `Customer` or `Account`" shape as
`Contact` above — there can be an organisation-level opportunity and,
separately, one tied to a specific Account (the mock's own card list
already mixed both, e.g. "Apple Inc" alongside "Apple EMEA", before
this model existed). `stage` is the closed set of 6 Kanban columns the
board already has (`discovery`/`qualification`/`solution_validation`/
`proposal_price_review`/`negotiation`/`closed_won`) — a fixed enum, not
a separate configurable-pipeline model, since no per-tenant
customisation was asked for. `priority` (`high`/`medium`/`low`) is a
real field, same as Task/Ticket's own. `mrr` is a
`DecimalField(max_digits=12, decimal_places=2)`, same shape as
Account's own ARR-family fields.

The mock's own `orgColor`/`orgInitials` aren't stored — decorative,
derived from the company name on the frontend (`EntityAvatar`, same as
every other entity). The mock's own per-column `count` isn't stored
either — it never actually matched `cards.length` in the mock (e.g.
"Discovery" claimed 12 while only listing 3 cards), a stale hardcoded
number; the frontend derives a real count from how many Opportunities
it actually fetched per stage.

See `seed_demo_opportunities` management command for demo data (run
after `seed_demo_accounts`).

### `GET/POST /api/v1/customers/<customer_id>/opportunities/`

Auth: `IsAuthenticated`. GET: every Opportunity under this Customer,
rolled up from both levels (organisation-level and account-level),
same reasoning as the Contact customer-scoped endpoint. POST always
adds an organisation-level Opportunity; `customer` taken from the URL.

**Response `200`** (GET) — a plain array, each entry: `id`, `title`,
`mrr`, `stage`, `stage_display`, `priority`, `priority_display`,
`company_id`, `company_name`, `account_name` (`null` for an
organisation-level row). **Response `201`** (POST) — one such entry.

### `GET/POST /api/v1/customers/<customer_id>/accounts/<account_id>/opportunities/`

Auth: `IsAuthenticated`. GET: every account-level Opportunity for one
Account. POST: adds a new one to it; `account` taken from the URL.

**Response `200`**/**`201`** — same shape as the Customer-scoped
endpoint above, `account_name` set to that account's own name.

### `GET/POST /api/v1/opportunities/`

Auth: `IsAuthenticated`. GET: every Opportunity across every Customer/
Account the caller's own organisation owns. The one Opportunity view
not nested under `/customers/<id>/...`, mounted at its own top-level
prefix, same reasoning as `ContactListView`. Powers the standalone
Pipelines board's own "Opportunities" tab.

Unlike `GET /api/v1/contacts/`, **pagination is off** here — a Kanban
board needs every card in every column to render/drag-and-drop
correctly, not one page of them.

POST takes a `customer_id` or an `account_id` in the request body
(neither is a real serializer field) and creates the Opportunity under
that parent — exactly one of the two must be given (`400` otherwise).

**Response `200`**
```json
[
  {
    "id": 4,
    "title": "Renewal Expansion Opportunity",
    "mrr": "30000.00",
    "stage": "qualification",
    "stage_display": "Qualification",
    "priority": "high",
    "priority_display": "High",
    "company_id": 6,
    "company_name": "Apple Inc",
    "account_name": "Apple EMEA"
  }
]
```

### `GET/PATCH/DELETE /api/v1/opportunities/<id>/`

Auth: `IsAuthenticated`. A single Opportunity, scoped to the caller's
own organisation, regardless of whether it's organisation-level or
account-level. Flat, not nested — same reasoning as
`ContactDetailView`. Powers both the board's drag-and-drop (PATCH
`stage`) and its Edit/Delete card actions. PATCH can't move an
Opportunity between parents, same as Contact.

**Response `200`** (GET/PATCH) — same shape as the list endpoints
above. **Response `204`** (DELETE) — empty body.

### Models — `Risk`

Mirrors: `src/pages/pipelines/PipelinesPage.tsx`'s mock `PipelineCard`/
`Column` shape used for the standalone Pipelines board's "Risks" tab —
field-for-field identical to `Opportunity` above, same reasoning.

Same "belongs to exactly one of `Customer` or `Account`" shape as
`Opportunity`. `stage` is the closed set of 4 Kanban columns the
board's Risks tab already has (`open`/`mitigated`/`realised`/
`abandoned`) — a fixed enum, same reasoning as Opportunity's own
`stage`. `priority`/`mrr` are the same shape as Opportunity's.

Unlike Opportunity's mock, none of the Risk mock's own card data named
real seeded companies — its org names ("Digital Operations", "Culinary
Innovation Lab (HCIL)", ...) don't exist anywhere in this codebase's
seed data — so `seed_demo_risks`'s demo rows are new content against
real seeded Customers/Accounts rather than a carryover of the mock's
own cards.

See `seed_demo_risks` management command for demo data (run after
`seed_demo_accounts`).

### `GET/POST /api/v1/customers/<customer_id>/risks/`

Auth: `IsAuthenticated`. GET: every Risk under this Customer, rolled up
from both levels (organisation-level and account-level), same
reasoning as the Opportunity customer-scoped endpoint. POST always
adds an organisation-level Risk; `customer` taken from the URL.

**Response `200`** (GET) — a plain array, each entry: `id`, `title`,
`mrr`, `stage`, `stage_display`, `priority`, `priority_display`,
`company_id`, `company_name`, `account_name` (`null` for an
organisation-level row). **Response `201`** (POST) — one such entry.

### `GET/POST /api/v1/customers/<customer_id>/accounts/<account_id>/risks/`

Auth: `IsAuthenticated`. GET: every account-level Risk for one Account.
POST: adds a new one to it; `account` taken from the URL.

**Response `200`**/**`201`** — same shape as the Customer-scoped
endpoint above, `account_name` set to that account's own name.

### `GET/POST /api/v1/risks/`

Auth: `IsAuthenticated`. GET: every Risk across every Customer/Account
the caller's own organisation owns. The one Risk view not nested under
`/customers/<id>/...`, mounted at its own top-level prefix, same
reasoning as `OpportunityListView`. Powers the standalone Pipelines
board's own "Risks" tab.

Unlike `GET /api/v1/contacts/`, **pagination is off** here, same
reasoning as `OpportunityListView` — a Kanban board needs every card in
every column to render/drag-and-drop correctly, not one page of them.

POST takes a `customer_id` or an `account_id` in the request body
(neither is a real serializer field) and creates the Risk under that
parent — exactly one of the two must be given (`400` otherwise).

**Response `200`**
```json
[
  {
    "id": 4,
    "title": "Renewal Risk — Contract Expiry",
    "mrr": "8500.00",
    "stage": "open",
    "stage_display": "Open",
    "priority": "high",
    "priority_display": "High",
    "company_id": 8,
    "company_name": "WeWork",
    "account_name": null
  }
]
```

### `GET/PATCH/DELETE /api/v1/risks/<id>/`

Auth: `IsAuthenticated`. A single Risk, scoped to the caller's own
organisation, regardless of whether it's organisation-level or
account-level. Flat, not nested — same reasoning as
`OpportunityDetailView`. Powers both the board's drag-and-drop (PATCH
`stage`) and its Edit/Delete card actions. PATCH can't move a Risk
between parents, same as Opportunity.

**Response `200`** (GET/PATCH) — same shape as the list endpoints
above. **Response `204`** (DELETE) — empty body.

### Models — `Survey`

Mirrors: the Activity Feed's own "Surveys" filter (react-ts-app's
`ActivityFeed.tsx` — previously an unimplemented chip with no data
behind it) and the standalone Surveys page (`src/pages/surveys/
SurveysPage.tsx`). Same "belongs to exactly one of `Customer` or
`Account`" shape as `Opportunity`/`Risk` above.

One sent instance of asking a Customer or Account for an NPS/CSAT/CES
score — `survey_type` (`nps`/`csat`/`ces`), `status`
(`sent`/`responded`/`expired`, default `sent`), `score` (one
`IntegerField` for all three types — -100..100 for NPS, 0..100 for
CSAT/CES, the same ranges `Customer.nps_score`/`csat_score`/
`ces_percentage` already use; required and range-checked, in the
serializer, only once `status` becomes `responded`), `sent_at`
(required), `responded_at` (set automatically to today on responding
if not given explicitly).

**Responding syncs the score onto the parent** — marking a Survey
`responded` with a `score` writes that value onto the parent Customer's
or Account's own `nps_score`/`csat_score`/`ces_percentage` field (see
`SurveyDetailView.perform_update`). Those fields go from "a number with
no provenance" to "the latest completed survey's result."

**CES is Customer-only** — `Account` has no `ces_percentage` field (a
real, pre-existing asymmetry with Customer), so a CES survey has
nowhere to sync a response on an Account. Creating one against an
Account (nested `AccountSurveyListView`, or the flat endpoint with
`account_id`) is a `400`.

Deliberately not a multi-question survey engine — no response-level
granularity beyond one score per Survey, no question builder, no
actual email delivery (there's no outbound customer-facing email
infrastructure in this codebase to send a real survey link with yet;
"send" means "log that it was sent," same honest framing as this app's
own Webhooks delivery being real but bounded).

See `seed_demo_surveys` management command for demo data (run after
`seed_demo_accounts`) — backfills one RESPONDED Survey per existing
non-null `nps_score`/`csat_score`/`ces_percentage` on every seeded
Customer/Account (same score, spread across the last several months so
the trend chart has real shape), plus a small hand-picked set of still-
`sent`/`expired` rows so every status shows up somewhere real too.

### `GET/POST /api/v1/customers/<customer_id>/surveys/`

Auth: `IsAuthenticated`. GET: every Survey under this Customer, rolled
up from both levels (organisation-level and account-level), same
reasoning as the Opportunity customer-scoped endpoint. POST always adds
an organisation-level Survey; `customer` taken from the URL. Powers the
Activity Feed's own Surveys filter.

**Response `200`** (GET) — a plain array, each entry: `id`,
`survey_type`, `survey_type_display`, `status`, `status_display`,
`score`, `sent_at`, `responded_at`, `companies` (every ultimate parent
Customer, plural since an account-level Survey's own Account can belong
to more than one), `account_id`/`account_name` (both `null` for an
organisation-level row — unlike Opportunity/Risk, `account_id` is a
real field here, not just `account_name`, since the standalone Surveys
page's own row-click needs it to navigate to that Account's Details
page), `created_at`. **Response `201`** (POST) — one such entry.

### `GET/POST /api/v1/customers/<customer_id>/accounts/<account_id>/surveys/`

Auth: `IsAuthenticated`. GET: every account-level Survey for one
Account. POST: adds a new one to it; `account` taken from the URL.
`400` if `survey_type` is `ces` — see the model's own note above.

**Response `200`**/**`201`** — same shape as the Customer-scoped
endpoint above, `account_name` set to that account's own name.

### `GET/POST /api/v1/surveys/`

Auth: `IsAuthenticated`. GET: every Survey across every Customer/
Account the caller's own organisation owns. The one Survey view not
nested under `/customers/<id>/...`, mounted at its own top-level
prefix, same reasoning as `OpportunityListView`. Powers the standalone
Surveys page — its own response-rate/average-score rollup cards are
computed client-side from this same list, not a separate stats
endpoint.

**Pagination is off** here, same reasoning as `OpportunityListView` — a
rollup page needs every record to total correctly, not one page of them.

POST takes a `customer_id` or an `account_id` in the request body
(neither is a real serializer field) and creates the Survey under that
parent — exactly one of the two must be given (`400` otherwise). `400`
if `survey_type` is `ces` and `account_id` was given.

**Response `200`**
```json
[
  {
    "id": 4,
    "survey_type": "nps",
    "survey_type_display": "NPS",
    "status": "responded",
    "status_display": "Responded",
    "score": 80,
    "sent_at": "2026-09-01",
    "responded_at": "2026-09-04",
    "companies": [{ "id": 6, "name": "Apple Inc" }],
    "account_id": null,
    "account_name": null,
    "created_at": "2026-09-01T10:00:00Z"
  }
]
```

### `GET/PATCH/DELETE /api/v1/surveys/<id>/`

Auth: `IsAuthenticated`. A single Survey, scoped to the caller's own
organisation, regardless of whether it's organisation-level or
account-level. Flat, not nested — same reasoning as
`OpportunityDetailView`. PATCH is "Log Response" (`status`/`score`) as
well as any other edit — this is the one place a responded Survey's
score gets synced onto its parent, see the model's own note above.

**Response `200`** (GET/PATCH) — same shape as the list endpoints
above. **Response `204`** (DELETE) — empty body.

---

### Models — `Canvas`

Mirrors: the sidebar's own "Canvas" gallery (`src/pages/canvas/
CanvasPage.tsx`) and the "Canvas List" tab on both Details pages
(previously two labels with nothing behind either). Same "belongs to
exactly one of `Customer` or `Account`" shape as Opportunity/Risk/
Survey above (`customer`/`account` nullable FKs +
`canvas_belongs_to_exactly_one_parent` CheckConstraint).

A Canvas is a stakeholder/relationship-map board: `nodes`/`edges` are
stored verbatim exactly as React Flow gives them — same "the graph
shape is the frontend's concern" philosophy as `scenarios.Scenario`'s
own `nodes`/`edges`. A node's own `data` holds only a `contact_id`
reference, never a name/role/sentiment snapshot — `Contact` already
carries real `role`/`sentiment` fields, so editing a Contact anywhere
is reflected on every Canvas it appears on. A Customer/Account can have
several Canvases (a list, not a one-per-company singleton).

`name` (default `"Untitled Canvas"`), `nodes`/`edges` (JSON, default
`[]`), `created_at`/`updated_at`.

See `seed_demo_canvases` management command for demo data (run after
`seed_demo_accounts`) — a handful of real seeded Customers each get a
Canvas with 2-4 of that company's own real seeded Contacts as nodes and
a couple of labeled relationship edges between them.

### `GET/POST /api/v1/customers/<customer_id>/canvases/`

Auth: `IsAuthenticated`. GET: every Canvas under this Customer, rolled
up across both organisation-level (directly on it) and account-level
(any of its Accounts' own) — same rollup as `CustomerSurveyListView`.
POST always creates an organisation-level Canvas; `customer` taken
from the URL. Powers the "Canvas List" tab on the Organization Details
page.

### `GET/POST /api/v1/customers/<customer_id>/accounts/<account_id>/canvases/`

Auth: `IsAuthenticated`. GET: every account-level Canvas for one
Account. POST creates a new one under it; `account` taken from the
URL. Powers the "Canvas List" tab on the standalone Account page.
`account_id` is a real field on the response (unlike Opportunity/Risk),
same reasoning as Survey's own — a gallery row needs it to link.

### `GET/POST /api/v1/canvases/`

Auth: `IsAuthenticated`. GET: every Canvas across every Customer/
Account the caller's own organisation owns. The one Canvas view not
nested under `/customers/<id>/...`, mounted at its own top-level
prefix, same reasoning as `SurveyListView`. Powers the standalone
Canvas gallery.

**Pagination is off** here, same reasoning as `SurveyListView` — the
gallery needs every record, not one page of them.

POST takes a `customer_id` or an `account_id` in the request body
(neither is a real serializer field) and creates the Canvas under that
parent — exactly one of the two must be given (`400` otherwise).

**Response `200`**
```json
[
  {
    "id": 4,
    "name": "Renewal Strategy Q3",
    "nodes": [
      { "id": "n1", "type": "contact", "position": { "x": 40, "y": 60 }, "data": { "contact_id": 12 } }
    ],
    "edges": [
      { "id": "n1-n2", "source": "n1", "target": "n2", "label": "Reports to" }
    ],
    "companies": [{ "id": 6, "name": "Apple Inc" }],
    "account_id": null,
    "account_name": null,
    "created_at": "2026-09-01T10:00:00Z",
    "updated_at": "2026-09-05T10:00:00Z"
  }
]
```

### `GET/PATCH/DELETE /api/v1/canvases/<id>/`

Auth: `IsAuthenticated`. A single Canvas, scoped to the caller's own
organisation, regardless of whether it's organisation-level or
account-level. Flat, not nested — same reasoning as
`SurveyDetailView`. PATCH is a plain save (`name`/`nodes`/`edges`) —
unlike Survey, there's no parent field this needs to sync on update.

**Response `200`** (GET/PATCH) — same shape as the list endpoints
above. **Response `204`** (DELETE) — empty body.

### Models — `Headline`

Mirrors: the "Headlines" sub-tab of `ActivityFeed` on both the
Organization Details page and the standalone Account page
(`src/components/organizations/activity/HeadlinesTab.tsx`). Same
"belongs to exactly one of `Customer` or `Account`" shape as
Note/Survey/Canvas above (`customer`/`account` nullable FKs +
`headline_belongs_to_exactly_one_parent` CheckConstraint).

Two card shapes, distinguished by `kind` rather than the mock's
`isSummary` boolean — the two genuinely differ in which fields apply:

* `summary` — the pinned TL;DR at the top of the tab. Carries
  `time_period_label` ("Last 3 months") and `data_sources`, which the
  card renders in its footer. Never carries a `status`; there is
  nothing open or closed about a rolling summary, enforced by a second
  CheckConstraint (`headline_summary_has_no_status`), not just in the
  serializer.
* `headline` — one themed storyline in the feed, with a `status`
  (`open`/`in_progress`/`closed`) and a `period_start`/`period_end`
  span shown on the card.

Fields: `kind`, `title`, `content`, `status`, `period_start`,
`period_end`, `time_period_label`, `data_sources`, `generated_at`.

`data_sources` is a list of closed-set keys (`notes`, `emails`,
`call_transcripts`, `tickets`, `activities`), not the mock's free-text
"Notes, Emails, Call Transcripts and Tickets" string. That string was
decorative — nothing computed it — whereas the generator knows which
records it actually read, so this records that honestly and
`data_sources_display` renders the prose list from it. Validated in
the serializer: a JSONField would otherwise accept any string at all
and the card would claim a source nobody read.

No `group` field, though the mock carried one (`'January'`): the
card's group pill is the read-only, derived `group` field, computed
from `period_end` — same as Note/Activity/Email/Task's own date-group
headers. Empty for a `summary` and for a `headline` with no
`period_end`. The mock's `orgId` is likewise gone; the parent is the
FK, and its absence is what made every account render the same two
hardcoded Apple cards.

`generated_at` is set only on cards written by the model, so
regeneration can replace its own previous output without touching
anything a CSM wrote or corrected by hand.

Read-only extras on every response: `kind_display`, `status_display`,
`data_sources_display`, `group`.

See the `seed_demo_headlines` management command for demo data (run
after `seed_demo_accounts`) — deliberately hand-written rather than
generated, so seeding works offline with no `ANTHROPIC_API_KEY` set,
and seeded rows carry `generated_at=None` so a later regenerate treats
them as hand-written.

### `GET/POST /api/v1/customers/<customer_id>/headlines/`

Auth: `IsAuthenticated`. GET is every organization-level `Headline`
for one `Customer`, scoped to the caller's own organisation — same
404-not-empty-list convention as the other nested lists. POST writes
one by hand; `customer` comes from the URL.

Writable, unlike the Note/Activity/Email lists: a headline is normally
generated, but a CSM writing or correcting one is a real case — and a
hand-written card is exactly what regeneration must not clobber.

**Response `200`** — a plain array (unpaginated), each entry: `id`,
`kind`, `kind_display`, `title`, `content`, `status`, `status_display`,
`period_start`, `period_end`, `time_period_label`, `data_sources`,
`data_sources_display`, `group`, `generated_at`, `created_at`.
**Response `201`** (POST) — the created entry, same shape.
**`400`** — an unknown `data_sources` key, a `summary` sent with a
`status`, or a period that ends before it starts.

### `GET/POST /api/v1/customers/<customer_id>/accounts/<account_id>/headlines/`

Auth: `IsAuthenticated`. Same as above but account-level, scoped to
both the URL's `customer_id` and the caller's own organisation.

**Responses** — same shapes as the Customer-scoped endpoint.

### `GET/PATCH/DELETE /api/v1/headlines/<id>/`

Auth: `IsAuthenticated`. One card, whether organization-level or
account-level. Flat, not nested — same reasoning as
`SurveyDetailView`/`CanvasDetailView`: the card's own edit/delete
controls have the Headline in hand and shouldn't need to know which of
the two parent shapes it came from.

**Response `200`** (GET/PATCH) — same shape as the list endpoints.
**Response `204`** (DELETE) — empty body.

### `POST /api/v1/customers/<customer_id>/headlines/generate/` and `.../accounts/<account_id>/headlines/generate/`

Auth: `IsAuthenticated`. Reads the parent's real `Note`/`Email`/
`Ticket`/`Activity` records from a recent window and writes its
Headline cards from them, through the same
`services.copilot.anthropic_client.get_completion` the Copilot uses —
one synchronous call, no task queue (same limit stated for Copilot
replies and Scenario runs). This is what makes the card footer's
"Data sources" line true rather than decorative: `data_sources` names
only the record types that were actually non-empty in the window.

Replaces only previously *generated* cards (`generated_at` not null),
never anything hand-written, and does the delete+insert in one
transaction so a failure can't leave the tab empty.

Optional body: `window_days` (default 90) and `time_period_label`
(default `"Last <n> days"`, used as the summary card's own label).

**Response `201`** — every card now on the parent, same shape as the
list endpoints. **`400`** — a non-numeric or sub-1 `window_days`.
**`422`** — the parent has no records at all in the window; no model
call is made. **`502`** — the call failed, or its answer couldn't be
read as JSON. **`503`** — the provider isn't configured (no
`ANTHROPIC_API_KEY`/Bedrock credentials). The 502/503 mapping matches
`SendMessageView`'s exactly, since it is the same one external call
underneath.

---

## `metrics` — The management brief on a schedule (Settings > Brief delivery)

### Model

- `BriefSchedule` — one per organisation: `destination` (a Slack incoming
  webhook), `cadence` (`weekly` | `monthly`), `weekday` (Monday is 0),
  `day` (of the month), `is_active`, `last_sent_at`, `created_by`.

### Conventions specific to this app

The Slack URL is a **credential** — anyone holding it can post to that
channel — so it goes in and never comes back out. Responses carry
`destination_hint`, the last few characters, enough to recognise which
hook is set. Only `https://hooks.slack.com/services/...` is accepted, and
it is checked against the same SSRF rules outbound webhooks use.

Configuring is gated on `manage_org_settings`, like webhooks: it is
configuration, not content. **Nothing is generated on a schedule.** The
pass posts the brief that exists; if nobody has written one, it says so
rather than spending a model call nobody asked for. A month too short for
the chosen day sends on its last day rather than skipping the month.

**There is no hour to choose.** `run_health_maintenance` runs once a
night, so a brief goes out on its day when that job runs. An hour the job
could never honour would be a promise the product cannot keep; a specific
time of day needs a more frequent job first.

### `GET/POST/PATCH/DELETE /api/v1/metrics/brief/schedule/`

```json
{"cadence": "weekly", "destination_hint": "…xxxx", "weekday": 1,
 "day": 1, "is_active": true, "last_sent_at": null}
```

POST takes `destination` plus any of the scheduling fields and returns
201. GET on an organisation with no schedule returns the same shape with
`cadence: null` rather than a 404. 400 for a destination that is not a
Slack hook, or a number out of range. Audited as `brief.schedule`.

### `POST /api/v1/metrics/brief/schedule/send/`

Posts the latest brief now, so a new schedule can be proved without
waiting a week → `{"sent": true, "detail": "Sent."}`. 404 with nothing
scheduled, 502 when Slack refuses it, and `{"sent": false}` with a reason
when no brief has been written yet. Audited as `brief.sent`.

---

## `scenarios` — Automation builder (`/scenarios`, `CreateScenario.tsx`)

Mirrors: `src/pages/scenarios/CreateScenario.tsx`, `types.ts`,
`scenarioApi.ts`. Its own top-level app (not nested under `customers`)
— a Scenario isn't owned by one Customer/Account, it's a tenant-wide
automation. See `services/scenarios/engine.py`'s own docstring for the
full "what's real vs. what's still a frontend mockup" line and why
(no task queue, no Slack/Teams/Survey/Playbook models in this
codebase).

### Models

- `Scenario` — `name`, `apply_to` (`organizations`/`accounts`/
  `contacts`, only `organizations` is runnable in v1), `nodes`/`edges`
  (JSON, stored exactly as React Flow gives them — see the model's own
  docstring), `is_active` (gates On Event auto-execution only; default
  `False`), `created_at`/`updated_at`.
- `ScenarioRun` — one execution: `scenario`, `customer` (the target),
  `triggered_by` (`manual`/`event`), `status` (`success`/`failed`),
  `log` (list of `{node_id, action, status, detail}`, one per node
  visited, in order), `started_at`/`finished_at`.

### Conventions specific to this app

`apply_to`'s values are lowercase to match every other choice field in
this codebase (`Customer.lifecycle_stage`, etc.) — the frontend's
`ApplyToTarget` type uses the same lowercase strings, not the
Title Case the radio labels display.

A Condition/Filter node's saved clause is one of two kinds, stored on
the node's own `data`. Condition branches by which outgoing edge has
`label: "Yes"`/`"No"` — the same manually-set label `CustomEdge.tsx`'s
own "Set Label" pill already produces, not a new concept.

**A field clause** (the default) is `conditionAttribute` +
`conditionOperator` + `conditionValue`. The attribute is a fixed
allowlist, never an arbitrary model field: `lifecycle_stage`, `name`,
`owner` (their email), `health_score`, `nps_score`, `arr` (read through
the organisation's own `arr` global attribute mapping), `renewal_days`
(days until renewal, negative once past), `open_tickets`, or
`attr:<api_name>` for any AI attribute, whose current value is its
newest row and therefore a person's correction when there has been one.
Operators: `equals`, `not_equals`, `greater_than`, `less_than`,
`contains`, `is_one_of` (a comma-separated list), `is_empty`,
`is_not_empty`.

**A semantic clause** is `conditionKind: "semantic"` with
`conditionPhrase` and an optional `conditionThreshold` (default 0.6).
The phrase and the company's most recent emails, tickets and calls go
through the same local embedding model the Copilot uses, and the clause
passes when the closest record clears the threshold. It spends no
credits and answers the same way every run.

The records it reads are the ones the **customer's owner** may read (the
same principal the nightly AI-attribute pass uses), so a scenario never
matches on mail from a mailbox nobody on the account may open or on
another department's tickets; a customer with no owner sees only the
records nobody owns personally. The run log says what matched by kind
and date ("an email from 12 Sep") and how closely, never a subject or
any of the record's words: run history is filtered by customer, not by
record.

Anything missing or unrecognised — an attribute, an operator, an
uncoercible value, a company with nothing on record — is `false`, never
an exception: a half-configured node skips its Yes branch rather than
stopping the run.

### What a node can actually do

| Action | What it does |
|---|---|
| `Send Email` | Sends to the customer's address through `send_scenario_email` |
| `Create Task` | A Task on the customer, due today |
| `Set Attribute` | Sets `lifecycle_stage` to one of its own valid values |
| `Churn Entity` | Marks churned and dates it |
| `Assign Owner` | Routes the customer to a person: `assignTo` (a user id in this organisation, checked rather than trusted) or `assignRule: "least_loaded"` with an optional `assignFunction`, which picks whoever in that function carries the fewest live customers, ties by lowest id. Notifies the new owner |
| `Notify` | Notifies `notifyWho`: `owner`, `manager` (the owner's manager) or `user` with `notifyUser`, carrying `notifyMessage` |
| `Wait` / `Conditional Wait` | A no-op that logs why: there is no task queue to wait on |

A node that names nobody reachable fails that node and marks the run
failed; the walk continues, because the side effects earlier nodes
committed are real either way.

### Triggers

`entry.data.eventTrigger` says what starts a scenario besides Run Now:

- `new_entity` — a Customer is created.
- `interaction_classified` — an email, ticket or call has just been
  tagged by the classifier. This is the trigger a semantic condition is
  for: the ticket arrives, the classifier says what it is about, the
  scenario decides where it goes. Only the record's own customer is run;
  an account-level interaction has no engine path yet.

Both run after the transaction commits, synchronously, in-request.

### `GET/POST /api/v1/scenarios/`

Auth: `IsAuthenticated`. GET: every Scenario the caller's own
organisation owns. **Pagination is off** — same reasoning as
`OpportunityListView`/`RiskListView`, a small whole-collection list.
POST: `organisation` is set from the caller, never client-provided.

**Response `200`/`201`**
```json
[
  {
    "id": 3,
    "name": "Low Health Save Play",
    "apply_to": "organizations",
    "apply_to_display": "Organizations",
    "nodes": [ { "id": "entry", "type": "entry", "position": {"x":0,"y":0}, "data": {"action": "Run Now", "label": "Start"} } ],
    "edges": [],
    "is_active": false,
    "created_at": "2026-09-04T10:00:00Z",
    "updated_at": "2026-09-04T10:00:00Z"
  }
]
```

### `GET/PATCH/DELETE /api/v1/scenarios/<id>/`

Auth: `IsAuthenticated`. Scoped to the caller's own organisation (404,
not 403, otherwise). The builder's Save/Save & Close PATCH `name`/
`apply_to`/`nodes`/`edges`/`is_active` together on every save — there's
no partial-field save from the UI today.

**Response `200`** (GET/PATCH) — same shape as the list endpoint.
**Response `204`** (DELETE) — empty body.

### `POST /api/v1/scenarios/<id>/run/`

Auth: `IsAuthenticated`. Body: `{"customer_id": <id>}`. The builder's
"Run Now" button. Runs synchronously — there's no task queue, so the
response IS the completed run. `400` if the scenario's `apply_to` isn't
`"organizations"`; `404` if the scenario isn't in the caller's own
organisation, or if the customer isn't one the caller can see (see the
`customers` app's visibility section).

The target is scoped by visibility, not just by tenant: a run is a
*write*, not a read — the engine sets `lifecycle_stage`/`churn_date`,
creates Tasks and sends real email — so an ungated target let any
member act on an account they couldn't open.

**Response `201`**
```json
{
  "id": 12,
  "scenario": 3,
  "customer": { "id": 9, "name": "Globex" },
  "triggered_by": "manual",
  "status": "success",
  "log": [
    { "node_id": "n1", "action": "Condition", "status": "ok", "detail": "Condition evaluated to True." },
    { "node_id": "n2", "action": "Churn Entity", "status": "ok", "detail": "Marked as churned" }
  ],
  "started_at": "2026-09-04T10:05:00Z",
  "finished_at": "2026-09-04T10:05:01Z"
}
```

### `GET /api/v1/scenarios/<id>/runs/`

Auth: `IsAuthenticated`. This scenario's own run history, newest first
— manual runs and On Event auto-runs both appear here, told apart by
`triggered_by`. **Pagination off**, same reasoning as the list endpoint.

Filtered to runs against customers the caller can see, even though the
Scenario itself is tenant-wide: each row names its customer and its
`log` quotes contact addresses (`Emailed x@y: "..."`). A run whose
customer has since been deleted (`customer` is `SET_NULL`) names nobody
and stays listed.

The On Event auto-run signal is deliberately *not* scoped — it fires on
every new Customer in the organisation regardless of owner, because a
tenant-wide automation that only ran for some people's customers would
be broken, not safer.

**Response `200`** — an array of the same shape as `POST .../run/`'s
response.

---

## `campaigns` — Bulk email send (`/campaigns`, `CampaignEditor.tsx`)

Mirrors: `src/pages/campaigns/CampaignEditor.tsx`, `types.ts`,
`campaignApi.ts`. Its own top-level app, same "tenant-wide, not owned
by one Customer/Account" reasoning as `scenarios` — a Campaign's
audience naturally spans many Customers/Accounts at once. A real send,
not a mockup: `services/email.py`'s `send_campaign_email` is the same
`send_mail` plumbing already used for password-reset emails and the
Scenario builder's own "Send Email" action, generalized to one
recipient at a time from a real list. No task queue exists in this
codebase, so a send runs synchronously, in-request — the same limit
`scenarios/engine.py`'s own docstring states outright.

### Models

- `Campaign` — `name`, `subject`, `body`, `status` (`draft`/`sent` — no
  `scheduled` state, no task queue to honor a future send time),
  `recipients` (a real ManyToManyField to `customers.Contact` — no new
  audience-builder, the same real Contacts already on `/contacts/list`),
  `send_log` (list of `{contact_id, contact_name, status: "sent"|"skipped",
  detail}`, one per recipient, written once by `POST .../send/` and
  never touched again — `sent_count`/`skipped_count` are derived from
  this in the serializer, not stored separately), `sent_at`,
  `created_at`/`updated_at`.
- `customers.Email` gained one new nullable field, `campaign` — set
  only on a row `CampaignSendView` itself created as a byproduct of a
  real send, so that send shows up for real in the recipient's own
  parent Customer/Account's Activity Feed, not just in the campaign's
  own send report. Every Email row logged any other way simply has it
  as `None`.

### Conventions specific to this app

A sent Campaign is locked — `PATCH` 400s once `status == "sent"` rather
than silently accepting an edit nobody could act on (you can't unsend a
real email). `recipient_ids` (a list of Contact ids) is never a real
serializer field — read straight off raw request data in
`perform_create`/`perform_update` and resolved against the contacts the
caller can actually see, same "not a real serializer field" convention
Survey/Canvas's own flat create views already use for `customer_id`/
`account_id`, generalized here to a list.

**An id you can't reach is a `400`, not a silent drop** — from another
organisation, or from an account somebody else owns. Dropping them
quietly was defensible when only a cross-tenant id could trigger it
(no UI flow produces one); now that a same-tenant contact can be out of
scope, sending to seven of the ten people you picked is the worse
failure, because you can't unsend the seven and nothing tells you about
the three. Recipients are resolved *before* the Campaign row is saved,
so a rejected list leaves nothing behind.

### `GET/POST /api/v1/campaigns/`

Auth: `IsAuthenticated`. GET: every Campaign the caller's own
organisation owns. **Pagination is off** — same reasoning as
`ScenarioListCreateView`, a small whole-collection list. POST accepts
an optional `recipient_ids` array alongside `name`/`subject`/`body`.

**Response `200`/`201`**
```json
[
  {
    "id": 4,
    "name": "Renewal Reminder",
    "subject": "Your renewal is coming up",
    "body": "Hi there, ...",
    "status": "sent",
    "status_display": "Sent",
    "recipients": [ { "id": 12, "name": "Sarah Chen", "email": "sarah@apple.example" } ],
    "send_log": [ { "contact_id": 12, "contact_name": "Sarah Chen", "status": "sent", "detail": "Emailed sarah@apple.example" } ],
    "sent_count": 1,
    "skipped_count": 0,
    "sent_at": "2026-09-05T10:05:00Z",
    "created_at": "2026-09-01T10:00:00Z",
    "updated_at": "2026-09-05T10:05:00Z"
  }
]
```

### `GET/PATCH/DELETE /api/v1/campaigns/<id>/`

Auth: `IsAuthenticated`. Scoped to the caller's own organisation (404,
not 403, otherwise). PATCH accepts `name`/`subject`/`body`/
`recipient_ids`; `400` if the campaign has already been sent.

**Response `200`** (GET/PATCH) — same shape as the list endpoint.
**Response `204`** (DELETE) — empty body.

### `POST /api/v1/campaigns/<id>/send/`

Auth: `IsAuthenticated`. Body: none. The real send. `400` if already
sent, if `subject`/`body` is blank, or if there are no recipients.
Runs synchronously — there's no task queue, so the response IS the
completed send, `send_log` and all. A recipient with no email on file
(or any other per-recipient send failure) is logged as `"skipped"`,
never aborts the rest of the send.

**Response `200`** — the updated Campaign, same shape as the list
endpoint, with `status: "sent"`, a populated `send_log`, and real
`sent_count`/`skipped_count`.

---

## `copilot` — AI chat (`/copilot`, `Index.tsx`/`ChatView.tsx`)

Mirrors: `src/pages/copilot/Index.tsx`, `ChatView.tsx`,
`CopilotSidebar.tsx`, `types.ts`, `copilotApi.ts`. The first real AI/LLM
integration in this codebase — a real call to a real Claude model
(`services/copilot/anthropic_client.py`), not a mock. Two interchangeable
providers behind `COPILOT_LLM_PROVIDER` (see `.env.example`): `anthropic`
(default) calls Anthropic's own API directly with **`ANTHROPIC_API_KEY`**;
`bedrock` calls the identical Claude model through AWS Bedrock instead,
with real AWS credentials (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/
`AWS_REGION`) and a real `BEDROCK_MODEL_ID` from your own AWS console (a
different id format than `ANTHROPIC_MODEL`) — that model must already have
real Bedrock model access approved in your own account/region first, a
one-time AWS Console step this app can't do for you. Live-verified against
real AWS Bedrock in `ap-south-1`; hit and fixed a real regional gotcha
along the way — the plain model id was rejected ("on-demand throughput
isn't supported... use an inference profile"), fixed by using the
region-group-prefixed cross-region inference profile id instead
(`apac.anthropic.claude-3-5-sonnet-20240620-v1:0`, not the bare
`anthropic.claude-3-5-sonnet-20240620-v1:0` — see `.env.example`'s own
note on `BEDROCK_MODEL_ID`). With the selected provider's own credentials
unset, every send returns a `503` rather than a
fake answer.

Its own top-level app, same "tenant-wide, not owned by one Customer/
Account" reasoning as `scenarios`/`campaigns` — except a Conversation is
scoped to one `User`, not the whole organisation: each CSM's own Copilot
history is private to them.

No task queue or streaming exists in this codebase, so a send runs
synchronously, in-request, one blocking API call per message — same
limit `scenarios/engine.py`'s own docstring states outright. Each
request is grounded in a compact, real-data digest of the caller's own
*owned* book of business (`services/copilot/context.py` — the
Customers/Accounts they own, not the whole tenant's, same "My" framing
as Cockpit's own `CockpitSummaryView`/`TaskListView`'s `?mine=true`:
health/NPS/lifecycle breakdown, top at-risk customers, open
opportunity/risk/ticket counts scoped to those same owned companies),
injected into the system prompt; this is grounding, not tool-calling —
the model can read this digest and converse, but can't run its own
queries or take real actions.

Past the aggregate numbers, `services/copilot/retrieval.py` pulls real
retrieved content. Two passes identify "which company is this about":
`find_mentioned_company` is a plain, free, case-insensitive substring
match of the question against the caller's own company names, tried
first; if that fails, `find_relevant_company_semantic` (see
`services/copilot/embeddings.py`) embeds the question against each
company's own profile text (`retrieval.py`'s own `_company_profile_text`
— the name alone, plus a real hand-entered `industry`
(`Customer.industry`/`Account.industry`, set via the Add/Edit
Organization/Account form) when one has been set, e.g. "Zoom. Industry:
Video conferencing software.") with a local `sentence-transformers`
model (`all-MiniLM-L6-v2`, no API key, no per-request cost, no
pgvector — this Postgres instance doesn't have the `vector` extension
available, so ranking is plain Python cosine similarity over in-memory
vectors, fine at this system's real scale) and returns the best match
above a calibrated `0.3` threshold — e.g. "that video conferencing
account struggling" finding Zoom once its industry is filled in. This
is a real, honest limitation, not full RAG: real communications content
(Notes/Emails/etc.) is deliberately not folded in — live testing found
this app's own seeded CS-ops content generic and templated across every
company, with no real company-identity signal to embed — so a company
whose `industry` is still blank falls back to name-only matching, which
works well for strong, distinctive brand names (Spotify/Uber both score
well against on-the-nose descriptions) and worse for names that are
also common words (Zoom scored too low against "video conferencing
account" to win, with no industry set, in real testing). Once a
company is identified — by either pass — it gets its
own recent real Emails (subject+body)/Notes (title+body)/open Tickets
(title only — the model has no body field)/Activities (a categorical
type label, no free text), and when there's a query, the same real
embeddings re-rank the overall most *relevant* items across every
source together (not a fixed per-source quota of the most recent) via
`retrieve_recent_communications`. No company identified either way
falls back to a smaller slice for each of the digest's own top-3
at-risk customers, so the digest isn't pure numbers even then. Every
source here is real data that already backs the Activity Feed's own
Emails/Notes/Tickets/Activities tabs — nothing new is captured, just
retrieved differently.

First real backend consumer of `Organisation.ai_agent_enabled`/
`ai_agent_tone` (see the `accounts` app's own section) — `ai_agent_enabled
= false` makes every send `403`, and `ai_agent_tone` picks one of three
real system-prompt tone instructions (`professional`/`friendly`/
`concise`).

**Ask Revenact on the Dashboard** (`services/copilot/dashboard_context.py`,
`dashboard_figures.py`, `dashboard_grounding.py`) is the same send endpoint
with an optional `context` — see `POST /api/v1/copilot/messages/` below for
the request/response shape. `DashboardContextSerializer` validates `context`
right after the conversation lookup and before anything the send does next:
a bad `context` is a `400` before any model call, before any budget is
spent, and before either `Message` is written — the same "don't record a
failed action as if it happened" discipline the rest of this send already
follows. The digest is built by `dashboard_grounding.build_dashboard_
grounding` from the same figures code the area's own endpoint calls, and the
system prompt wraps it in `<dashboard_data>…</dashboard_data>`, telling the
model everything inside is data from records, never instructions to follow,
however it is phrased — the same "customer/colleague words are data, not
instructions" guard this codebase already applies to translation, anomaly
summaries, gathered asks and the brief. The filters echoed into the prompt
are only the values among the asker's own filter options (`forecast.
filter_options`); anything else is treated as "all" rather than copied into
the prompt verbatim.

### Models

- `Conversation` — `organisation`, `user` (private per-user, not shared
  org-wide), `title` (derived from the first message's own text — no
  rename UI), `origin` (the first dashboard message's `context` without its
  `focus`, set once; null otherwise), `created_at`/`updated_at`. Lazily
  created on the first message actually sent, same "no ghost rows"
  convention as Canvas/Campaign's own editors (POST on first Save).
- `Message` — `conversation`, `role` (`user`/`assistant` — mirrors the
  Anthropic Messages API's own two-role shape exactly), `content`,
  `author`, `sources`, `ask_suggestions`, `context` (a user turn asked on
  the Dashboard; null otherwise), `reply_to` (an assistant reply's own
  user turn, set on every new reply), `created_at`.

### Conventions specific to this app

`content` is capped at 8000 characters (`400` if blank or over).
Replayed history to the model is capped to the conversation's own last
20 messages (Anthropic's Messages API is stateless — the full history
must be resent every turn; unbounded replay would grow cost/latency
without limit).

### Models — `ModelCall`, `ModelBudget`

The audit trail and the budget for every model call the codebase makes.
Every path that reaches Claude — the Copilot, Headlines, the classifier,
the brief, the Ops agent — goes through one function,
`anthropic_client.get_completion`, which now takes `purpose`,
`organisation` and `user` and writes one `ModelCall` per call: purpose,
provider, model, input/output tokens (from the SDK's own `usage`),
latency, `outcome` (`ok`/`failed`/`unconfigured`/`over_budget`) and the
error. Failures are rows too; a brain whose calls quietly fail is worse
than one whose calls are visible. Logging is best-effort and can never
break a working call.

`ModelBudget` is tokens (input + output) per organisation per purpose
per calendar month; absent a row, `settings.MODEL_BUDGET_DEFAULT_TOKENS`
(default 2,000,000) applies. `get_completion` checks it **before** the
call: over budget, the call is logged as `over_budget`, never sent, and
`BudgetExceeded` is raised — the views turn that into a `429`, and
`classify_interactions` stops the run rather than failing every batch.
A call with no organisation (none of ours today) is logged but not
budgeted.

### `POST /api/v1/copilot/draft-reply/`

`{"kind": "email" | "mail_message", "id": <int>}` → `{"draft": "…", "sources": [...]}`.

A reply written as the current user, for them to edit and send: nothing is sent
and no conversation is stored. `email` is a filed `customers.Email` under the
mailbox visibility rule (the whole thread on that mailbox goes into the prompt);
`mail_message` is a row of the person's own inbox, owner only. The prompt
carries the thread plus up to eight of the account's most relevant records from
the same retrieval the Copilot answers with (`retrieve_with_sources`), and those
records come back as `sources` in the message-source shape (`type, id, label,
date, company, company_type, company_id`) so the UI can say "N sources used"
and link each one. A mailbox message that matched nobody in the book gets an
empty `sources`. Charged as a `draft_reply` model call; 400 for an unknown kind
or missing id, 403 when the organisation's AI agent is off, 404 when the record
is not the caller's to read, 429/503/502 exactly as `POST /copilot/messages/`.

### `GET /api/v1/copilot/usage/`

Purposes: `copilot`, `headlines`, `classification`, `brief`, `proposals`, `facilitator`, `explain`.

Auth: `CanViewAllAccounts`. This month per purpose (`calls`, `ok`,
`failed`, `input_tokens`, `output_tokens`, `spent`, `budget`,
`remaining`, `custom_budget`), the `default_budget`, and the last fifty
calls with who made them.

### `PATCH /api/v1/copilot/usage/budgets/`

Auth: `CanManageOrgSettings` — spend is organisation configuration. Body
`{"purpose": "proposals", "monthly_tokens": 50000}` sets a budget;
`monthly_tokens: null` clears it back to the default. Unknown purpose,
non-integer or negative → `400`. Returns the summary.

### `GET /api/v1/copilot/skills/`

Auth: `CanViewAllAccounts`. The catalogue of what the brain's agents may
do — `services/copilot/skills.py`, one `Skill` per model-call purpose:
`name`, `summary`, `reads` (what the agent is given; nothing else can
reach it), `may`, `never`, `trigger`, `gate`, `surface`. The catalogue is
code beside the prompts, and a purpose without an entry fails a test.
Each skill carries `usage` (this month: `calls`, `ok`, `failed`, `spent`,
`budget`, `remaining`, `custom_budget` — the same figures as `usage/`),
`last_run` (`{at, outcome, user}` or null) and `produced` (`{label,
count}` all-time, plus `approved` for the two proposing skills; null
where no exact count exists). Frontend: `/brain/skills`.

### `GET /api/v1/copilot/conversations/`

Auth: `IsAuthenticated`. Every Conversation the caller may read
(`conversations_visible_to`): their own, plus the live sessions they were
invited into, accepted, and are still present in — not the whole
organisation. **Pagination is off**, same reasoning as
`CampaignListCreateView`. Powers the sidebar's own "Chat history" list,
so an accepted participant keeps a way back to a session after the
invite card is gone.

**Response `200`**
```json
[
  { "id": 5, "title": "What's my churn risk?", "origin": null, "created_at": "2026-09-05T10:00:00Z", "updated_at": "2026-09-05T10:01:00Z" },
  { "id": 7, "title": "Why is at-risk ARR up?", "origin": { "surface": "dashboard", "area": "revenue", "view": "forecast", "filters": { "owner": "2", "lifecycle": "", "customer": "" } }, "created_at": "2026-09-24T10:00:00Z", "updated_at": "2026-09-24T10:01:00Z" }
]
```

`origin` is where the conversation started on the Dashboard — the first
dashboard message's `context` without its `focus` — or `null`. Set once,
never changed. The history shows it as a tag.

### `GET/DELETE /api/v1/copilot/conversations/<id>/`

Auth: `IsAuthenticated`. Scoped to the caller's own conversations (404,
not 403, otherwise). No `PATCH` — a conversation's title/messages are
only ever set by `POST .../messages/`.

**Response `200`** (GET) — adds `origin`, and nested `messages` (each
`{id, role, content, author, sources, questions, ask_suggestions,
context, created_at}`; `context` is the dashboard context a user turn
was asked on, else `null`).
**Response `204`** (DELETE) — empty body.

### `POST /api/v1/copilot/messages/`

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

---

## `connectors` — Integrations (`/integrations`, `Integrations.tsx`)

### Models — `Connector`

One external system the organisation has connected — `provider` (a
closed list: Zendesk, Jira, Freshdesk, "any other system (webhook)",
Intercom, Salesforce, HubSpot, Slack, Gmail, Microsoft Teams, Zoom,
GitHub, Figma), `name` (distinguishes two of the same provider),
`is_enabled`, and a scope of `customers`/`accounts` where **empty means
the whole organisation**.

**Ticket sources are live.** Zendesk, Jira, Slack (one channel), Freshdesk
and the inbound webhook can be *connected*: an integration manager hands
the connector the organisation's credentials (Fernet-encrypted at rest,
never serialised), the scheduler container runs `sync_tickets` every ten
minutes, and each ticket is filed on the customer or account its
requester's email belongs to (a known contact first, then the domain —
the same lookup mail uses; a connector scoped to exactly one company
files everything there). Second passes update status, priority,
assignee and resolution in place. New tickets are classified for
sentiment on arrival so the Account Pulse can count them.

**Tickets are read department-wise.** Every ticket connector has a
`department` (`User.Function`: cs, engineering, sales, analytics,
leadership, other, or blank); tickets synced through it carry that
department, and `services/customers/personal.py:visible_tickets` shows a
person only their own department's tickets plus the undeparted ones.
Leadership reads every department. The rule applies to the Tickets tab
on organisations and accounts, the Ticket Overview and Interactions
dashboards, and the Copilot's retrieval and shared replies.

Other providers (Salesforce, Zoom, …) stay attribution-only: `setup` is
null and `connect/` answers 400.

### `GET/POST /api/v1/connectors/`, `GET/PATCH/DELETE /api/v1/connectors/<id>/`

Reading is open to any member (the Ticket Overview's origin chart and
the Integrations page need it); writing needs `manage_integrations`.
Every row carries what the connector has brought in — `ticket_count`,
`call_count`, and `last_record_at` (the newer of the latest ticket's
`opened_at` and the latest call's date, or null) — annotated in one
query, so the Integrations page can show "169 tickets · last 10 Sep"
without a second call. `DELETE` keeps the records: both foreign keys are
SET_NULL.

```json
[{"id": 3, "provider": "zendesk", "provider_display": "Zendesk", "name": "Zendesk",
  "is_enabled": true, "department": "cs", "department_display": "Customer Success",
  "customers": [{"id": 1, "name": "Apple"}], "accounts": [],
  "is_organisation_wide": false, "ticket_count": 169, "call_count": 0,
  "last_record_at": "2026-09-10",
  "has_credentials": true, "config": {"host": "acme.zendesk.com"},
  "status": "connected", "error": "", "last_synced_at": "2026-09-16T09:00:00Z",
  "last_sync_note": "3 new, 2 updated",
  "setup": {"key": "zendesk", "label": "Zendesk", "uses_oauth": false,
            "fields": [{"name": "subdomain", "label": "Zendesk subdomain", "secret": false, "placeholder": "acme (from acme.zendesk.com)", "required": true},
                       {"name": "email", "label": "Agent email", "secret": false, "placeholder": "…", "required": true},
                       {"name": "api_token", "label": "API token", "secret": true, "placeholder": "", "required": true}],
            "help": "Admin Center › …"},
  "created_at": "2026-08-01T00:00:00Z"}]
```

`POST` accepts `department` alongside `provider` and `name`; `PATCH` may
change it (existing tickets keep the department they were filed with).

### `POST /api/v1/connectors/<id>/connect/` — `manage_integrations`

Body is the provider's own form (`setup.fields`). The provider is called
to verify the credentials before anything is stored (`400` with the
provider's message otherwise); success is `201` with the connector and an
audit event `connector.connect`. `{"oauth": true, "channel": "C…"}` asks
for `{authorize_url}` instead when the provider supports sign-in
(Slack, once `SLACK_OAUTH_CLIENT_ID/SECRET` are set); the provider then
lands on `GET /api/v1/connectors/oauth/<provider>/callback/` (public,
trusts only the 15-minute signed state) and the browser is bounced to
`/integrations?connector=connected|error&detail=…`.

For the webhook provider the `201` also carries `token` (shown once) and
`inbound_url`.

### `DELETE /api/v1/connectors/<id>/credentials/` — `manage_integrations`

Disconnect: the secret is destroyed and the status returns to
`not_connected`; the tickets it brought in stay. Audit event
`connector.disconnect`.

### `POST /api/v1/connectors/<id>/sync/` — `manage_integrations`

Pull now. `200` with the connector plus `created`, `updated`, `unmatched`
counts; a provider that refuses the token leaves the connector in
`status: "error"` with the reason in `error`.

### `POST /api/v1/connectors/<id>/inbound/` — public, secret-authenticated

Where any other system pushes tickets. Authenticated by the connector's
own secret, either `X-Revenact-Token: <secret>` or
`X-Revenact-Signature: sha256=<hex hmac-sha256 of the raw body>`;
unknown connectors and bad secrets are both `404` (and the latter is
audited as `connector.inbound_rejected`). Body: one ticket object, or
`{"tickets": [...]}` up to 200 at a time, 512 KB max:

```json
{"external_id": "77", "title": "Export broken", "description": "…",
 "status": "open|in-progress|on-hold|resolved|closed", "priority": "critical|high|medium|low",
 "requester_email": "sam@pizzahut.com", "requester_name": "Sam", "assignee_name": "Eve",
 "url": "https://helpdesk/77", "opened_at": "2026-09-10", "resolved_at": null}
```

`202` with `{created, updated, unmatched}`. The same `external_id` again
updates the ticket.

### Ticket fields added for sources

`TicketSerializer` now also carries `department`, `department_display`,
`description`, `requester_name`, `requester_email`, `external_url` and
`synced_at`. Numbers come from the source: `ZD-1042`, `SUP-12` (Jira key),
`FD-88`, `SL-<ts>`, `WH-<id>`.

## `knowledge` — What the whole company knows (Organization Details › Company View, `CompanyViewTab.tsx`)

### Who may see whose words — the org chart rule

`User.reports_to` is the org chart (set via `/auth/users/` `reports_to_id`,
read as `reports_to {id, name}`; a loop is a `400`). From it,
`services/accounts/hierarchy.scope_ids(user)` is the set of people whose
records a person may see: themselves, everyone below them in the chart,
everyone in their function, and everyone above them in the chain.
Anything addressed to them (a question routed to them, a message that
@mentions them) is theirs regardless, and **responsibility grants
reading**: the people who answer for a customer — its CS owner and its
function owners — see every function's notes and questions on that
customer, whatever the chart says (`knowledge.views.responsible_for_q`). The rule applies to what people
*say*: contributions (`visible_contributions`, plus answers to the
reader's own questions), questions (`visible_questions`), the Copilot's
retrieval (scoped to the asker), and chat turns (below). Customer and
account records keep their capability scoping, with one addition: a
customer's page also opens for a manager whose report owns it (all
the way down the chart), for anyone responsible for it in
any function,
or asked / asked about it, or who wrote about it
(`customers.scoping.visible_customers`) — so a notification that links
there opens for the person it was sent to.

**A mentioned person sees a slice of a chat, not the chat.** A
conversation is visible to its owner, to accepted session participants,
and to anyone a turn routed a question to. The first two see every turn;
a mentioned person sees the turns whose author is in their scope **and
which are not addressed to someone else** (a turn that routes a question
to Raj is Raj's, however senior its author), the turns that mention
them **or anyone who reports to them** — a manager sees what was asked
of their team and what the team replied, and the conversation appears
in their list — their own, and the Copilot's replies to those
(`copilot.views.visible_messages`). `GET/POST` conversation payloads
carry `visibility: "full" | "partial"` and each turn its `author`
(`Message.author`, backfilled for older turns from the session's
redirect events). A follow-up posted by a mentioned person is grounded
and given history from their slice only. **A Copilot reply is withheld
from a sliced viewer when it cites a record they may not read** — a
contribution outside their scope, a customer they may not open — and
shows as "This reply isn't shared with you…" instead (`copilot.views.
_reply_readable_by`); the stored turn is untouched. Pairing a reply with
the question it answers uses `Message.reply_to`, set on every new reply
(a legacy row with none falls back to the immediately preceding user
turn) — ordering alone can't be trusted once two participants can send
concurrently. **A reply to a question asked on the Dashboard carries the
asker's whole filtered book**, not just the records it cites — totals,
top lists, company names never individually sourced — so a sliced viewer
needs every customer in `forecast.filtered_customers(asker, the answered
turn's context.filters)` to be inside their own visible customers, on
top of the per-source checks above; the asker always sees their own
dashboard replies regardless. A context-less (Communications/Copilot)
reply keeps exactly the per-source checks for every viewer, the asker
included.

Demo: `seed_demo_hierarchy` — Alice at the top; Carl, Priya, Raj, Mei
report to her; Dana to Carl.

### The account owner

`Customer.owner` is the **account owner**: the one accountable person for
the relationship, from any function (by convention CS; it can be an
engineer for a technical-led account). The per-function owners are the
team around them. Two rules, in `services/knowledge/ownership.py`, apply
on every path that changes it (`PATCH /customers/<id>/ owner_id`, and the
Company View's CS row `PATCH …/responsible/ {"function": "cs"}`):

- **Who may change it**: the current owner, anyone above them in the
  chart, or `manage_org_settings`; an unowned customer may be claimed by
  anyone (`400` / `403` otherwise).
- **A change is an event**: `handover_note` (customer PATCH) / `note`
  (responsible PATCH) is written down as a contribution by the person who
  made the change — "Account owner changed from Dana to Priya. <note>" —
  and the new owner is notified.

"Your customers" in the Copilot's digest means owned **or** responsible
in your function; the owner cut in the registry is labelled "Account
owner"; the Ops agent and facilitator are given each account's team and
told to assign a task to the person responsible for the function it
needs, falling back to the account owner. The responsible payload carries
`account_owner {id, name, function}`.

### Models — `User.function`, `Contribution`, `FunctionOwner`

`User.function` (`cs`, `engineering`, `sales`, `analytics`, `leadership`,
`other`; default `cs`) is which part of the company someone works in — not
a permission (roles carry those). It stamps their contributions and is
what "the responsible person" is looked up by. Set on create/edit via
`/auth/users/` (`function`), read everywhere a user is serialised
(`function`, `function_display`).

A `Contribution` is one person's knowledge about one customer from their
function: `customer`, `author`, `function` (a snapshot of the author's
function when written), `body`. **Scoped by the org chart** (see above), never by the CSM's book:
every member may write; who may read is the chart rule.
`FunctionOwner` (`customer`, `function`, `user`; unique per customer and
function) says who answers for the customer in each function; CS stays
`Customer.owner`.

### `GET/POST /api/v1/customers/<id>/contributions/`, `GET/PATCH/DELETE /api/v1/contributions/<id>/`

Auth: `IsAuthenticated`, any customer in the caller's organisation
(`404` outside it). `GET` lists newest first, `?function=` narrows.
`POST {"body"}` stamps `author` and `function` from the caller. The
author may edit or delete their own; `manage_users` may delete anyone's
(`403` otherwise).

### `GET/PATCH /api/v1/customers/<id>/responsible/`

`GET` returns `{"customer_id", "responsible": [{function,
function_display, user: {id, name} | null}]}` for every function; CS is
read from the customer's owner. `PATCH {"function", "user_id" | null}`
sets or clears one (CS writes `Customer.owner`) and needs
`view_all_accounts`; an unknown function is a `400`.

### Models — `Question`

A question routed to a person, on the record: `customer` (optional),
`asked_by`, `assignee`, `text`, `status` (`open`/`answered`), `answer`
(the `Contribution` the answer was stored as), `message` (the Copilot
turn that asked it, if any), `answered_at`. **A question answered once is
knowledge**: the answer is a contribution from the answerer's function,
so the Copilot has it from then on.

### `GET/POST /api/v1/customers/<id>/questions/`, `GET /api/v1/questions/`, `POST /api/v1/questions/<id>/answer/`

`POST {"text", "assignee_id"?}` — with an assignee the question goes to
them; otherwise to whoever the text **@mentions** (`@Mei` by first name
when unique, `@Mei Tanaka` by full name; two Meis and a bare `@Mei`
resolve to nobody rather than the wrong one; never yourself). `400`
when nobody is asked. Each person asked gets a `question_asked`
notification linking to the chat it was asked in (`/copilot?session=<conversation id>`)
when it was asked in one, otherwise to the customer's page; the answer's
notification links the same way. Lists are open first, newest
first; `/questions/` takes `?mine=true` (waiting on the caller),
`?asked=true` (asked by the caller), `?status=`. `answer/ {"body"}`:
the assignee (or `manage_users`) answers once (`409` after); the answer
is stored as a contribution whose body opens `In answer to <asker>'s
question "<text>": …`, the question closes, and the asker gets a
`question_answered` notification.

### Questions that age

After three days an open question is **stale** (`services/knowledge/
aging.py`). The registry gains `open_questions`, `stale_questions` and
`contributions_30d` (source `knowledge`), so the Brain counts them and a
month-end records them; `GET /questions/?stale=true` lists the stale
ones and every question payload carries `days_open`.
`run_health_maintenance` (and `nudge_open_questions` alone) reminds each
assignee of their stale questions — a `question_asked` notification
reading "Still waiting: … asked you about X 5 days ago" — at most once
a day (`Question.last_nudged_at`). Answering is the only thing that
clears it.

### `GET /api/v1/knowledge/activity/?days=30` — is the company writing things down?

Auth: `CanViewAllAccounts`. Per function (`services/knowledge/activity.py`):
`members`, `contributors` (distinct authors in the window),
`contributions` (answers included), `questions_asked` of that
function, `questions_answered` by it, `questions_waiting` on it (open,
any age), `avg_days_to_answer` (null with nothing answered). No model
call. Shown on the Brain overview as "Knowledge by function".

### @mentions in the Copilot

A message to `POST /copilot/messages/` that @mentions members routes a
question to each of them — on the customer the message was found to be
about, if any — attached to the user turn (`messages[].questions`:
`[{id, assignee, status}]`). The model is told the question has been
routed, acknowledges it in a sentence, and answers what the summary
already covers rather than answering for the person asked.

### The Copilot suggests whom to ask

Every assistant turn carries `ask_suggestions`: the people responsible
for the customer the question was about — `[{user_id, name, function,
function_display, customer_id, customer_name}]`, the asker left out,
empty when no customer was identified — a snapshot of who was
responsible when the answer was given. The screen offers them as one
click under the reply; the click is `POST /customers/<id>/questions/`
with `assignee_id` and `message_id` (the user turn), so the question
keeps the turn it came from and the chat shows whom it asked.
When the caller did not write that turn — forwarding someone else's
question — the stored text reads `Alice Admin asked: "…" — can you
answer?`, so the person asked knows whose question it is.

### The Copilot reads all of it

`copilot.retrieval` adds each customer's contributions as candidates —
prompt line `Engineering (Priya Nair, 2026-09-13): …`, source `type:
"contribution"` with label `Engineering · Priya Nair` — and
`copilot.context` looks the mentioned company up **across the whole
organisation**, so an engineer, a sales rep or the CEO with no book of
their own still gets an answer; their own-book figures are simply
omitted. The digest ends with `Responsible for <customer>: Engineering —
Priya; Customer Success — Carl` so the model can point at a person when
the summary runs out, and the persona tells it to.

Demo: `seed_demo_functions --org-email alice@acme.io` (Priya Nair /
Raj Mehta / Mei Tanaka, responsibilities and contributions on the
Analytics Suite accounts).

## `knowledge` — Gaps and the account brief (Organization Details › Company View, `CompanyViewTab.tsx`)

Two additions to the knowledge layer: what the company **cannot** answer
about a customer, and the standing brief on what it can.

### Models

- `KnowledgeGap` — `organisation`, `customer`, `subject`, `fingerprint`
  (the lowercased subject; unique per customer so the same question
  counts twice rather than raising twice), `function` and `assignee`
  (from `FunctionOwner` when there is one), `source` (`copilot` |
  `question`), `question` (the routed `Question` that first
  raised it), `times_asked`, `status` (`open` | `filled` |
  `dismissed`), `filled_by` (the `Contribution` that answered it),
  `first_asked_at`, `last_asked_at`.
- `AccountBrief` — one per customer: `use_cases`, `stakeholders`
  (`{name, cares_about}`), `open_threads`, `sources` (citation
  snapshots), `generated_by`, `generated_at`.

### Conventions specific to this app

Both are **company-wide**, like the rest of the knowledge layer (see
`_company_customer`): whoever can answer a question about an account is
usually in another function and does not own it, so scoping either to a
CSM's own book would hide the question from the person who could close
it. A gap holds a subject and a count, never a record's words.

The brief is generated as the **customer's owner** reads (the same
principal the nightly AI-attribute pass uses), and what a reader is shown
is filtered again for them. `sources` holds only the citations they may
open and `hidden_sources` counts the rest; when anything was withheld the
brief's own words go with it — `use_cases`, `stakeholders` and
`open_threads` come back empty — because they were written from those
records. This is exactly how an AI attribute's `reasoning` is treated. A
written brief that comes back empty with `hidden_sources` above zero
means "not yours to read", not "nothing to say".

Everything the model reads is citable, so nothing can reach the brief
that could not be counted as withheld: contributions arrive through
retrieval, already scoped, rather than through a second unscoped pass.
The only exception is the customer's own contact list, which anyone who
can open the customer can read anyway.

Gaps are raised from real events, never invented: a Copilot answer about
a company where retrieval returned no sources, and a routed `Question`
still open after three days (`aging.STALE_DAYS`), swept nightly by
`run_health_maintenance`. Every question the sweep sees is stamped with
`Question.gap_raised_at`, whether it named a new gap or joined one
somebody else had already raised in the same words, so no question is
swept twice. Writing a `Contribution` on that customer from
the function that owed the answer fills every open gap it covers,
wherever it was written.

### `GET /api/v1/knowledge/gaps/?customer=<id>&status=<status>`

Unpaginated, most-asked first. `status` defaults to `open`; an unknown
one is a 400.

```json
[{"id": 12, "subject": "Which integrations do they run?",
  "customer": {"id": 7, "name": "Pizza Hut"},
  "function": "engineering", "function_display": "Engineering",
  "assignee": {"id": 5, "name": "Mei"}, "source": "copilot",
  "times_asked": 3, "status": "open",
  "first_asked_at": "...", "last_asked_at": "..."}]
```

### `POST /api/v1/knowledge/gaps/<id>/answer/`

`{"body": "They run Salesforce and Slack."}` → 201 `{gap, contribution}`.
The answer is an ordinary `Contribution`, so it lives with everything
else the company knows. 409 when the gap is already closed, so a second
press never writes a second contribution. Audited as
`knowledge.gap_filled`.

### `POST /api/v1/knowledge/gaps/<id>/dismiss/`

Closes it without an answer → the gap. 409 when it is already closed, so
a dismissal never overwrites an answer. Audited as
`knowledge.gap_dismissed`.

### `GET/POST /api/v1/customers/<id>/brief/`

GET returns the brief as this reader sees it, and never 404s for a
customer that has none: an empty brief with `generated_at: null` is the
honest answer, and the open gaps come back beside it either way.

```json
{"use_cases": ["Dispatching field crews"],
 "stakeholders": [{"name": "Priya", "cares_about": "Fewer no-shows"}],
 "open_threads": ["Waiting on the multi-year quote"],
 "sources": [...], "hidden_sources": 1,
 "generated_at": "...", "generated_by": {"id": 5, "name": "Dana"},
 "gaps": [{"id": 12, "subject": "...", "times_asked": 3, "function": "engineering"}]}
```

POST writes a new one (one model call, purpose `account_brief`) and
returns the same shape with 201. 403 when the organisation's Copilot is
off, 429 over budget, 503 unconfigured, 502 upstream. Audited as
`knowledge.brief`.

---

## `webhooks` — Outbound integrations (Settings > Webhooks, `WebhooksPage.tsx`)

Mirrors: `src/pages/settings/WebhooksPage.tsx`. Its own top-level app,
same "tenant-wide, not owned by one Customer/Account" reasoning as
`scenarios`. See `services/webhooks/engine.py`'s own docstring for the
full SSRF-safety reasoning (URL validated at save time and again
immediately before every send; redirects are never followed) and why
only one event exists in v1.

### Models

- `WebhookSubscription` — `url`, `event` (only `customer.created` in
  v1), `secret` (server-generated, ~256 bits, never client-settable —
  signs every delivery's own `X-Revenact-Signature` header), `is_active`
  (default `True` — unlike Scenario's own `is_active`, there's no
  "silently fires on live data" concern here beyond what creating the
  webhook already implies), `created_at`.
- `WebhookDelivery` — one outbound POST attempt: `webhook`, `success`,
  `status_code` (null if the request never got a response at all —
  DNS failure, connection refused, timeout), `error`, `sent_at`. Same
  audit-trail reasoning as `scenarios.ScenarioRun`.

### Conventions specific to this app

Unlike every other Settings tab (Currency/Global Presets/AI Agent,
Entity Uploads), **both GET and PATCH/POST/DELETE are admin-only**
(`IsOrgAdmin`, not `IsAuthenticated`) — a webhook's URL/secret is
credential-adjacent configuration, same reasoning `/auth/csms/` gates
member management to admins only.

Delivery is synchronous, in the same request that triggered it (there's
no task queue in this codebase — see `services/scenarios/engine.py`'s
own docstring for the identical constraint) — a slow or dead receiving
URL adds real latency to whatever created the Customer, bounded by a
5-second timeout per webhook.

### `GET/POST /api/v1/webhooks/`

Auth: `IsOrgAdmin`. GET: every WebhookSubscription the caller's own
organisation owns. **Pagination off** — small collection, same
reasoning as Scenario's own list. POST: `organisation` set from the
caller; `url` is validated (rejects non-http(s) schemes and anything
resolving to a private/loopback/link-local address) — `400` with the
reason if it fails.

**Response `200`/`201`**
```json
[
  {
    "id": 2,
    "url": "https://example.com/hooks/revenact",
    "event": "customer.created",
    "event_display": "Organization Created",
    "secret": "kX8...redacted...",
    "is_active": true,
    "created_at": "2026-09-04T10:00:00Z",
    "recent_deliveries": [
      { "id": 9, "success": true, "status_code": 200, "error": "", "sent_at": "2026-09-04T10:05:00Z" }
    ]
  }
]
```

### `GET/PATCH/DELETE /api/v1/webhooks/<id>/`

Auth: `IsOrgAdmin`. Scoped to the caller's own organisation (404, not
403, otherwise). PATCH is mainly for toggling `is_active`; `url`/`event`
can be changed too (re-validated the same way as on create). `secret`
is read-only here too — there's no "rotate secret" action yet.

**Response `200`** (GET/PATCH) — same shape as the list endpoint's own
entries. **Response `204`** (DELETE) — empty body.

---

## `fx_rates` — Exchange rates (Settings > Currency's own Exchange Rates section)

Mirrors: `src/pages/settings/CurrencyPage.tsx`'s Exchange Rates section
(no separate Settings tab of its own). Its own top-level app, same
"tenant-wide, admin-only-both-ways" reasoning as `webhooks` — an
exchange rate is financial config, not everyday customer data. Exists
to convert a `customers.Customer`'s own `currency` into the org's
reporting currency wherever money is summed across customers that no
longer share one currency — see `GET /api/v1/customers/stats/`'s own
`unconverted_count` field above for the one place this is actually
used today.

### Models

- `FxRate` — `organisation` (FK, server-set, never client-supplied),
  `currency` (choices, same `Organisation.Currency` set — the "from"
  currency; can't be the org's own current currency, and at most one
  row per currency per organisation), `rate_to_org_currency` (decimal,
  6 places — how many units of the org's own currency equal 1 unit of
  `currency`), `created_at`/`updated_at`.

Deliberately manual entry, current rate only — no point-in-time
history. Nothing in this app renders a real historical ARR-over-time
trend from live backend data today, so a dated rate history would be
infrastructure with no consumer yet; a live/current rate is used
everywhere, a documented simplification, not an oversight.

### Conventions specific to this app

Same admin-gate reasoning as `webhooks`: **both GET and POST/PATCH/
DELETE are admin-only** (`IsOrgAdmin`).

**Changing `Organisation.currency` clears every `FxRate` row for that
organisation** (see `OrganisationSettingsView.perform_update`). A
stored rate means "X → the org's *old* currency" — silently
reinterpreting it as "X → the *new* currency" the moment the org
switches would produce a wrong number with no visible sign anything
was wrong, so the admin has to re-enter rates for the new base
currency instead.

### `GET/POST /api/v1/fx-rates/`

Auth: `IsOrgAdmin`. GET: every FxRate the caller's own organisation has
configured. **Pagination off** — small collection, same reasoning as
Webhook's own list. POST: `organisation` set from the caller; `currency`
is validated — `400` if it's the org's own current currency, or if a
rate for that currency already exists for this org (a friendly error;
the DB's own `unique_together` would otherwise raise a raw 500, since
`organisation` isn't a client-facing field DRF can build its usual
`UniqueTogetherValidator` from).

**Response `200`/`201`**
```json
[
  { "id": 3, "currency": "EUR", "currency_display": "Euro (€)", "rate_to_org_currency": "1.080000", "updated_at": "2026-09-04T10:00:00Z" }
]
```

### `GET/PATCH/DELETE /api/v1/fx-rates/<id>/`

Auth: `IsOrgAdmin`. Scoped to the caller's own organisation (404, not
403, otherwise). PATCH is mainly for updating `rate_to_org_currency` as
real-world rates move.

**Response `200`** (GET/PATCH) — same shape as the list endpoint's own
entries. **Response `204`** (DELETE) — empty body.

---

## `custom_objects` — Custom objects (Settings > Custom Objects, `CustomObjectsPage.tsx`; Organization/Account Details' own "Custom Objects" tab, `CustomObjectsTab.tsx`)

Its own top-level app, same "tenant-wide config, not owned by one
Customer/Account" reasoning as `webhooks`/`fx_rates` for *definitions* —
but unlike those, this app's *records* each belong to exactly one
Customer or Account, same shape as `Opportunity`/`Risk`. The SFDC
"custom object" pattern: an org admin declares a brand-new object type
with its own fields, with no code change or migration needed to add
another one later — see `CustomObjectDefinition`'s own docstring.

### Models

- `CustomObjectDefinition` — `organisation`, `name`, `api_name` (slug,
  server-derived from `name`, unique per organisation), 
  `applies_to_customer`/`applies_to_account` (at least one `True`),
  `created_by`, `created_at`.
- `CustomFieldDefinition` — `object_definition`, `name`, `api_name`
  (slug, server-derived, unique per object), `field_type` (`text` |
  `number` | `currency` | `date` | `boolean` | `picklist`),
  `is_required`, `picklist_options` (list of strings; only meaningful
  for `picklist`), `order` (server-assigned, append-only), `created_at`.
- `CustomObjectRecord` — `object_definition`, `customer`/`account`
  (exactly one set, same `CheckConstraint` shape as `Opportunity`),
  `data` (JSON object keyed by each field's own `api_name`), 
  `created_by`, `created_at`, `updated_at`.

### Conventions specific to this app

**Defining** object/field types is admin-only (`IsOrgAdmin` on every
POST/PATCH/DELETE under `/definitions/`); **reading** definitions and
**all record CRUD** is open to any authenticated org member — adding a
custom-object record is like adding a Task, not like changing webhook
config. `data` is validated field-by-field against the definition's own
real `CustomFieldDefinition`s on every write (required fields present,
values type-correct per `field_type`, no unmapped keys) — see
`CustomObjectRecordSerializer._validate_data`/`_coerce_value`.

### `GET/POST /api/v1/custom-objects/definitions/`

Auth: `IsAuthenticated` (GET) / `IsOrgAdmin` (POST). GET: every
definition in the caller's own organisation, `fields` nested,
`records_count` real (across every parent this definition applies to).
**Pagination off**. POST: `name` + `applies_to_customer`/
`applies_to_account`; `api_name`/`organisation`/`created_by` are always
server-derived, never client-set. `400` if neither `applies_to_*` flag
is set.

**Response `200`/`201`**
```json
[
  {
    "id": 4,
    "name": "Opportunity Line Item",
    "api_name": "opportunity_line_item",
    "applies_to_customer": false,
    "applies_to_account": true,
    "fields": [
      { "id": 9, "name": "Product", "api_name": "product", "field_type": "text", "field_type_display": "Text", "is_required": true, "picklist_options": [], "order": 1, "created_at": "2026-09-06T10:00:00Z" },
      { "id": 10, "name": "Quantity", "api_name": "qty", "field_type": "number", "field_type_display": "Number", "is_required": false, "picklist_options": [], "order": 2, "created_at": "2026-09-06T10:00:00Z" }
    ],
    "records_count": 3,
    "created_at": "2026-09-06T10:00:00Z"
  }
]
```

### `GET/PATCH/DELETE /api/v1/custom-objects/definitions/<id>/`

Auth: `IsAuthenticated` (GET) / `IsOrgAdmin` (PATCH/DELETE). Scoped to
the caller's own organisation (404, not 403, otherwise). DELETE
cascades to that definition's own fields and every record ever created
against it.

### `POST /api/v1/custom-objects/definitions/<definition_id>/fields/`

Auth: `IsOrgAdmin`. `name` + `field_type` (+ `is_required`,
`picklist_options` — required non-empty when `field_type` is
`picklist`); `api_name`/`order` are always server-derived.

### `PATCH/DELETE /api/v1/custom-objects/definitions/<definition_id>/fields/<id>/`

Auth: `IsOrgAdmin`. Scoped to that definition within the caller's own
organisation.

### `GET/POST /api/v1/custom-objects/records/?definition=<id>[&customer=<id>|&account=<id>]`

Auth: `IsAuthenticated`. GET always requires `?definition=`; adding
`&customer=<id>` or `&account=<id>` scopes the list to that one
specific parent's own records (**pagination off** — what
`CustomObjectsTab.tsx` calls on a Customer/Account's own page) —
omitting both instead returns every record of that object type across
the caller's whole organisation, any parent, **paginated** with the
shared `PageNumberPagination`/`PAGE_SIZE` (what the org-wide per-object
page, `CustomObjectRecordsPage.tsx`, calls from the sidebar's own
"Custom Objects" section). Every record — parent-scoped or org-wide —
carries `parent_name`/`parent_type` (same pair as `TaskListSerializer`'s
own), so a cross-parent list can say which Organization/Account each
row belongs to.

POST always requires a real parent: `object_definition_id` + exactly
one of `customer_id`/`account_id` + `data`; `400` with a real reason
for a missing required field, a wrong-typed value, an unmapped `data`
key, a picklist value outside its own real options, a parent type the
definition doesn't `applies_to_*`, or a parent outside the caller's own
organisation.

**Response `200`/`201`, parent-scoped (plain array)**
```json
[
  {
    "id": 21,
    "object_definition_id": 4,
    "customer_id": null,
    "account_id": 17,
    "parent_name": "North America",
    "parent_type": "account",
    "data": { "product": "Seat License", "qty": 50 },
    "created_at": "2026-09-06T10:05:00Z",
    "updated_at": "2026-09-06T10:05:00Z"
  }
]
```

**Response `200`, org-wide (`?definition=` only, paginated)**
```json
{
  "count": 42,
  "next": "http://.../api/v1/custom-objects/records/?definition=4&page=2",
  "previous": null,
  "results": [ /* same shape as above, spanning every parent */ ]
}
```

### `GET/PATCH/DELETE /api/v1/custom-objects/records/<id>/`

Auth: `IsAuthenticated`. Scoped to the caller's own organisation via
whichever of `customer`/`account` is set (404, not 403, otherwise).
PATCHing `data` re-validates it in full against the (unchanged)
definition's own fields.

---

## `requests` — Feature requests with revenue (Brain > Feature Requests, `FeatureRequests.tsx`)

Every email, ticket and call the classifier tagged `feature_request`
(`services.customers.taxonomy`) becomes evidence for a named request, so
product can see which asks carry the most ARR. Grouping is done with the
Copilot's local embeddings; only naming a **new** request costs a model
call (purpose `feature_request`).

### Models

- `FeatureRequest` — `organisation`, `title`, `summary`, `status` (`open` |
  `planned` | `shipped` | `declined`), `owner`, `embedding` (the group's
  centroid, used to match later asks without re-reading its evidence),
  `created_at`, `updated_at`.
- `RequestEvidence` — `request` (null when dismissed), `organisation`,
  `kind` (`email` | `ticket` | `call`) + `record_id` (unique together: one
  filing per interaction), `customer`/`account` (exactly one), `snippet`,
  `occurred_at`, `dismissed`.

### Conventions specific to this app

**Reading** is open to any member, but the revenue, the counts and the
evidence are computed **over the companies that member may open**
(`visible_customers`): two people legitimately see different ARR on the
same request. **Gathering and curating** (`PATCH`, merge, evidence move
and dismiss) need `view_all_accounts`, since gather spends credits and
curation is an organisation-wide judgement. An account's ask counts the
ARR of its parent organisations, under the org's own `arr` global
attribute mapping.

### `GET /api/v1/requests/?status=<status>`

Unpaginated, most revenue first:

```json
[{"id": 4, "title": "Slack alerts", "summary": "Push alerts into Slack.",
  "status": "open", "owner": {"id": 5, "name": "Dana"},
  "arr": "200000", "companies": 2, "interactions": 2,
  "last_90_days": 2, "previous_90_days": 0,
  "created_at": "...", "updated_at": "..."}]
```

### `GET /api/v1/requests/<id>/`

The row above plus `companies_asking` (each `{id, name, arr}`, most
revenue first; `companies` stays the count) and `evidence` (each `{id, kind, record_id, snippet, occurred_at,
company: {type, id, name}}`), both limited to what the reader may open.

### `PATCH /api/v1/requests/<id>/`

`{"status": "planned", "owner": 5, "title": "...", "summary": "..."}` →
the detail body. `view_all_accounts` only; audited as `request.update`.

### `POST /api/v1/requests/gather/`

Files every unfiled classified ask: into an existing request when its
embedding is close enough, else into new titled requests, one model call
each (naming reads a sample of at most 20 asks, not the whole cluster).
200 `{"created": 2, "linked": 3, "remaining": 0}`. When the model
stops part-way the work already done is kept and returned with the
failure code (429 budget, 503 not configured, 502 upstream) plus
`remaining`. `view_all_accounts` only; 403 when the Copilot is off. Each
new request is audited as `request.create`. The nightly
`run_health_maintenance` pass runs the same thing per organisation.

### `POST /api/v1/requests/<id>/merge/`

`{"into": 7}` moves every piece of evidence and deletes this request →
`{"id": 7, "moved": 2}`. 400 when merging into itself. Audited as
`request.merge`.

### `POST /api/v1/requests/evidence/<id>/move/` and `.../dismiss/`

Refile one ask under another request, or drop it (it stays filed as
`dismissed` so the next gather leaves it alone).

## `mcp` — Somebody's own agent, reading as them (Settings > Agent Access, `AgentAccessPage.tsx`)

An MCP server, so an agent outside Revenact can read what its owner could
read in the app. JSON-RPC 2.0 over one POST; no new dependency, because
MCP is JSON-RPC and a read-only server needs three methods.

### Model

- `McpToken` — `user`, `label`, `token_hash` (SHA-256; the secret is shown
  once and never stored), `hint` (the last few characters), `last_used_at`,
  `revoked_at`.

### Conventions specific to this app

**The token is the person.** Every tool runs under that user's own
visibility, through the same helpers the app uses: their book, their
mail, their department's tickets. There is no wider path, and no
organisation-level key — an agent is somebody's agent. It is also their
*current* access: a token whose owner has been deactivated stops
resolving, and deactivating somebody revokes their tokens outright,
beside the refresh tokens that are already blacklisted.

A key can spend the organisation's model budget through `ask_copilot`, so
requests are rate limited **per key** (`THROTTLE_MCP`, 60/min by default)
rather than per address: one key is one agent wherever it runs.

**Read-only by design.** A tool that changed a record would put a
person's name on an action they did not take. That needs its own
decision, not a default.

Every `tools/call` is audited as `mcp.tool_called` with the tool, the
token's label and the argument names, and its outcome, so a person can
see what their agent did.

### `POST /api/v1/mcp/`

`Authorization: Bearer <token>`. A missing, wrong or revoked token is a
401; everything else is a JSON-RPC response with HTTP 200, including
errors, which is what the protocol asks for.

- `initialize` → `protocolVersion`, `capabilities.tools`, `serverInfo`
  and instructions.
- `tools/list` → each tool's `name`, `description` and `inputSchema`.
- `tools/call` with `{name, arguments}` → `{content: [{type: "text",
  text}], isError}`. A refusal is a result, not a transport failure —
  including a spent budget or an unconfigured model, so an agent can tell
  "out of budget" from "no such company" and act differently.
- A notification (no `id`) is answered with 204.
- Anything else → JSON-RPC `-32601`; a malformed envelope → `-32600`.

**Tools:** `search_companies`, `get_company`, `recent_interactions`,
`list_feature_requests`, `list_anomalies`, `ask_copilot` (one model call,
purpose `mcp`). A company the caller may not open answers the same way as
one that does not exist.

### `GET/POST /api/v1/mcp/tokens/` and `DELETE /api/v1/mcp/tokens/<id>/`

A person's own keys. POST takes `label` and is the **only** time `token`
is returned:

```json
{"id": 4, "label": "Claude Desktop", "hint": "…7fQx",
 "created_at": "...", "token": "rvn_mcp_..."}
```

GET lists them without it. DELETE revokes one; another person's token is
a 404. Audited as `mcp.token_issued` / `mcp.token_revoked`.

---

## `translation` — Reading in your language, writing in theirs (`CommunicationsPage.tsx`, `ReplyBox.tsx`)

### Models

- `Translation` — `organisation`, `kind` (`email` | `ticket` | `call` |
  `mail_message`) + `record_id` + `language` (unique together: one
  translation per record per language), `detected_language`, `text`, and
  `source_hash`, a digest of the words it was made from so an edited
  record misses the cache rather than showing a translation of what it
  used to say.

### Conventions specific to this app

A translation is a **copy of the record's words**, so reading one is
gated on the record itself, every time: mail under its mailbox owner and
their chain, a ticket under its department, a mailbox message under its
owner, and everything under the company rule as well. A record the
caller may not read is a 404, cached translation or not.

Translating a record keeps the result, so the second person to open the
same message pays nothing. Translating loose text — a reply being
written — keeps nothing, because a draft is not a record.

When a record is translated for the first time, the sender's contact
learns the language it was written in, but only if nobody has set one:
`Contact.language` set by a person is their answer and a detection never
overrules it. The sender is read from `from_address` for mail and
`requester_email` for a ticket; a call has no writer, so nothing is
learned from one. The contact is looked up on the company the record
actually hangs off, customer or account.

The communications queue carries `writer_language` on the rows somebody
wrote, so the reply box can offer to write back in it.

### `POST /api/v1/translations/`

`{"kind": "email", "id": 412, "to": "en"}` → 201 when it was translated
now, 200 when it came from the cache:

```json
{"text": "Can you explain the September invoice?", "to": "en",
 "detected_language": "fr", "made_now": true}
```

`{"text": "Hello there", "to": "fr"}` translates a draft and stores
nothing. 400 without a valid language code or with nothing to translate,
403 when the organisation's Copilot is off, 404 for a record the caller
may not read, 429 over budget, 503 unconfigured, 502 upstream. One model
call, purpose `translate`. Audited as `translation.made` when a record's
translation is written.

`Contact` gains `language`, readable and writable on the contact
endpoints.

---

## `anomalies` — What suddenly started going wrong (Brain > Anomalies, `Anomalies.tsx`)

One fault reported by eight accounts reads as eight tickets everywhere
else in this product. Here it reads as one thing to fix.

### Models

- `Anomaly` — `organisation`, `title`, `summary`, `status` (`live` |
  `acknowledged` | `resolved`), `embedding` (the cluster's centroid, so a
  later report joins it without another model call), `acknowledged_by`,
  `first_seen_at`, `last_seen_at`.
- `AnomalyEvidence` — `anomaly`, `organisation`, `kind` + `record_id`
  (unique together: one filing per interaction), `customer`/`account`
  (exactly one), `snippet`, `mailbox_owner` and `department` copied from
  the source, `occurred_at`.

### How a cluster is found

Over the classified interactions of the last 14 days, detection embeds
each one with the local model, attaches it to an open cluster whose
centroid it is close to, and groups the rest among themselves. A group
becomes an anomaly only when it spans at least **3 different companies**
and is at least **twice** what the same subject drew in the preceding 14
days — a subject that draws the same traffic every fortnight is the
weather, not news. Each window is read by its own query per kind, so a
busy fortnight cannot crowd out the history it is being compared against.
A `resolved` cluster takes no new evidence.

**Naming reads only what nobody owns personally.** Anyone who can see one
report in a cluster sees its name, so the name is written from reports
with no mailbox owner and no department — never from one person's mail or
one department's queue. A cluster with nothing shared in it is named from
its own shape ("Unnamed cluster across 4 companies") and costs no model
call at all. When a name is written, it is one call (purpose `anomaly`),
from a sample of at most 15 reports, told to describe the problem in its
own words and never to quote a report, a person or a company.

### Conventions specific to this app

Reading is open to any member and scoped twice, as everywhere else that
copies record text: the companies they may open, and the records they may
read (`services.customers.personal.readable_evidence_q`, shared with
feature requests). A cluster a reader can see nothing of is left out
rather than shown as a zero, and its detail is a 404 for them: its title
was written from reports they may not read. Detecting and setting a
status need `view_all_accounts`.

### `GET /api/v1/anomalies/?status=<status>`

Unpaginated, most revenue first.

```json
[{"id": 3, "title": "SSO login failures", "summary": "...",
  "status": "live", "arr": "30000", "companies": 3, "interactions": 7,
  "first_seen_at": "...", "last_seen_at": "...", "acknowledged_by": null}]
```

### `GET /api/v1/anomalies/<id>/`

The row above plus `companies_hit` (each `{id, name, arr}`, most revenue
first) and `evidence` (each `{id, kind, record_id, snippet, occurred_at,
company}`), both as this reader may see them.

### `PATCH /api/v1/anomalies/<id>/`

`{"status": "acknowledged"}` → the detail body. `view_all_accounts` only;
audited as `anomaly.update`.

### `POST /api/v1/anomalies/detect/`

Runs detection now → 200 `{"found": 1, "attached": 4}`. The nightly
`run_health_maintenance` pass runs the same thing per organisation. When
the model stops part-way, what was found is kept and returned with the
failure code (429 budget, 503 unconfigured, 502 upstream).
`view_all_accounts` only; 403 when the Copilot is off. Each new cluster is
audited as `anomaly.found`.

---

## `attributes` — AI-filled attributes (Settings > AI Attributes, `AIAttributesPage.tsx`; Organization/Account Details' pinned panel, `AIAttributesPanel.tsx`)

An admin asks a question of every company in plain English ("Which tier
do they use?") and the model answers it per company from the same
evidence the Copilot retrieves, with its reasoning and the records it
cited. Every answer is an append-only row, so the history of a value is
readable, and a person can override in the same timeline.

### Models

- `AIAttribute` — `organisation`, `name`, `api_name` (slug, server-derived,
  unique per organisation), `prompt`, `value_type` (`text` | `number` |
  `boolean` | `picklist`), `picklist_options`, `applies_to_customer` /
  `applies_to_account` (at least one), `refresh` (`manual` | `nightly`),
  `created_by`, `created_at`, `updated_at`.
- `AIAttributeValue` — `attribute`, `customer`/`account` (exactly one),
  `value` (JSON, typed by the attribute; null when insufficient), `reasoning`,
  `sources` (citation snapshots, same shape as a Copilot message's), `status`
  (`filled` | `insufficient` | `failed`), `origin` (`ai` | `human`), `set_by`,
  `computed_at`. Never updated; the newest row is the current value.

### Conventions specific to this app

Defining, updating, deleting and **filling every company at once** are
gated by `manage_custom_objects` (tenant-wide schema and tenant-wide
spend); reading definitions, filling one company and overriding are open
to any member **for companies they may open** (`visible_customers` /
`visible_accounts`; an invisible company is a 404, a non-numeric id a
400). Filling is a model call charged as purpose `attribute`, so it
answers 429 when the budget is spent, 503 when no model is configured and
403 when the organisation's Copilot is off.

**Evidence is read under the asker's own visibility** (personal mail,
private notes, departmental tickets, knowledge contributions: the same
rules as the Copilot). The nightly pass (`run_health_maintenance`) reads
as the company's **owner** would; a company with no owner gets only the
shared records. It fills `refresh=nightly` attributes for companies with
no answer yet or with evidence newer than their last one, oldest answers
first, at most 100 companies per attribute per organisation per night,
and never on top of a person's own answer. One tenant's spent budget skips
that tenant only.

**What a reader sees is filtered again:** `sources` lists only the
citations the reader may open, `hidden_sources` counts the rest, and
`reasoning` is an empty string whenever anything was withheld (it quotes
the records).

### `GET/POST /api/v1/attributes/definitions/`

GET (any member): unpaginated list. POST (`manage_custom_objects`):

```json
{"name": "Product tier", "prompt": "Which tier of our product does this company use?",
 "value_type": "picklist", "picklist_options": ["SMB", "Enterprise"],
 "applies_to_customer": true, "applies_to_account": true, "refresh": "nightly"}
```

201 → the definition with `id`, `api_name` (`product_tier`), timestamps.
400 when a picklist has no options or neither parent type applies.
Audited as `attribute.define`.

### `GET/PATCH/DELETE /api/v1/attributes/definitions/<id>/`

Same read-open / write-gated split. Deleting removes every value ever
recorded; audited as `attribute.update` / `attribute.delete` (with the
number of values that went).

### `POST /api/v1/attributes/definitions/<id>/fill/`

`{"customer": 12}` or `{"account": 7}` → 201, the new value row:

```json
{"id": 90, "attribute": 3, "customer": 12, "account": null,
 "value": "Enterprise", "status": "filled", "origin": "ai",
 "reasoning": "Two notes and a ticket mention the Enterprise tier.",
 "sources": [{"type": "note", "id": 41, "label": "Product usage", "date": "2026-09-22",
              "company": "Pizza Hut", "company_type": "customer", "company_id": 12}],
 "hidden_sources": 0,
 "set_by": {"id": 5, "name": "Dana"}, "computed_at": "2026-09-22T09:00:00Z"}
```

`status` is `insufficient` (value null) when the records do not support
an answer, and `failed` when the answer did not fit the type (the
`reasoning` says why). 400 when the attribute does not apply to that kind
of company.

Empty body (`manage_custom_objects`) → every applicable company the
caller may open, up to 25 per request: 200 `{"filled": 25, "remaining":
40, "values": [...]}`. The rest is picked up by the nightly pass or
another call. Each fill is audited as `attribute.fill`.

### `GET /api/v1/attributes/values/?customer=<id>` (or `?account=<id>`)

Every attribute that applies to the company, with its latest row or null:

```json
[{"attribute": {"id": 3, "name": "Product tier", "api_name": "product_tier",
                "prompt": "...", "value_type": "picklist",
                "picklist_options": ["SMB", "Enterprise"], "refresh": "nightly"},
  "latest": {...value row...}},
 {"attribute": {...}, "latest": null}]
```

400 without a company, 404 for one the caller may not open.

### `POST /api/v1/attributes/values/`

A person's own answer: `{"attribute": 3, "customer": 12, "value": "SMB"}` →
201, a row with `origin: "human"`, `set_by`, no reasoning or sources.
400 when the value does not fit the type or the picklist. Audited as
`attribute.override`.

### `GET /api/v1/attributes/values/history/?attribute=<id>&customer=<id>`

Every row for that attribute on that company, newest first.

<!--
Template for each new feature section below:

## `metrics` — The metric layer (`/api/v1/metrics/`)

Nine dashboards each compute their own rollup, and they agree because the
rules they share (`churn.py`, `contact.py`, `segments.py`) have one home.
A management question cuts across them — "NRR, coverage and shelfware
this quarter against last" — and nothing could answer it, because there
was no list of what the numbers *are*. This app is that list, and the
memory to go with it. It is Phase 1 of turning the product into a
company brain: facts first, then explanations, then decisions.

### Models — `MetricSnapshot`

One number, for one organisation, as a month ended: `organisation`,
`metric` (a registry key), `dimension`/`member` (both empty for the
whole organisation — reserved so a slice by owner or product later is a
row, not a migration), `period_end`, `value` (null when unmeasured that
month — **not zero**), `captured_at`. Unique per
(organisation, metric, dimension, member, period_end).

**Recorded by `run_health_maintenance`**, after the health scores it
reads are fresh, on the same "last completed month" rule as
`HealthSnapshot`, and with the same discipline: an existing row is kept,
never overwritten — it is how the month ended, and upserting it a week
later would replace it with the next month's values so the history
drifted forward every run. A metric added to the registry later is
filled in for a period without touching the rows already there.

### Conventions specific to this app

* **`services/customers/scoping.SystemActor`** — an organisation acting
  as itself. Every rollup takes a `user` and asks scoping what they may
  see; a scheduled job has no user, and impersonating "some admin" would
  tie the job to whoever happens to hold a role. The actor exposes only
  what scoping reads (`organisation`, `has_capability` → True), so a
  rollup that started depending on anything else about a person fails
  loudly rather than seeing something odd.
* **Values are the dashboards' own numbers.** `registry.compute_all`
  runs each source rollup once (`portfolio.build_stats`, the forecast
  bridge, `activity_tracking.build_stats`, a health-mix count over the
  rubric's own `health_category`, an open-ticket count) and each metric
  reads a key out of it. Never a second computation that can drift.
* **Unmeasured stays `None`.** An empty book has no logo retention, no
  NRR and no coverage; it has zero customers and zero open tickets.
* **A source that raises takes its metrics down loudly.** A metric layer
  that swallowed errors and reported `None` would be reporting
  "unmeasured" for what is actually "broken".

### `GET /api/v1/metrics/`

Auth: `CanViewAllAccounts` — every figure is whole-org, and a CSM whose
book is scoped to their own customers would otherwise read the company's
ARR here. `401` unauthenticated, `403` without the capability.

```json
{
  "as_of": "2026-09-12", "currency": "USD",
  "metrics": [{
    "key": "nrr", "label": "Net revenue retention", "unit": "percent", "better": "up",
    "note": "Forecast ARR as a share of opening ARR, before any new logos. ...",
    "dimensions": ["owner", "product"],
    "value": 99.6,
    "previous": {"period_end": "2026-08-31", "value": 97.1},
    "change": 2.5
  }]
}
```

`unit` is `money` (in `currency`), `percent` or `count`; `better` is
`up`, `down` or `none` (context, not a target — a customer count is
neither good nor bad on its own). `previous` is the most recent
month-end snapshot, null when none exists; `change` is null whenever
either side is unmeasured — a move from "unknown" to 40 is not a rise
of 40.

The eighteen metrics in this slice: `active_customers`, `active_arr`,
`average_arr`, `logo_retention`, `churned_arr_12m`, `top_three_share`,
`forecast_arr`, `nrr`, `at_risk_arr`, `seat_utilisation`,
`shelfware_arr`, `at_capacity_arr`, `coverage`, `dark_accounts`,
`dark_arr`, `healthy_share`, `poor_health_count`, `open_tickets`.

### `GET /api/v1/metrics/<key>/by/<dimension>/`

One metric, cut one way — the "why" layer's raw material. `dimension` is
`owner`, `product`, `segment` or `lifecycle`; each metric declares which
cuts it has (`registry.Metric.slices`), and a cut is only ever one the
underlying rollup already draws or a plain regrouping of rows it scored
— never a new rule. Forecast metrics by owner/product go through
`forecast.bridge_by`, which reuses `build_bridge` per group so the
groups' opening ARR, downside and forecast add up to the whole's. A
metric asked for a cut it doesn't have is a `404` that names the cuts
it does.

```json
{"metric": {"key": "at_risk_arr", ...},
 "dimension": {"key": "product", "label": "Product"},
 "currency": "USD",
 "members": [{"member": "4", "label": "Product B", "value": 64090.0,
              "previous": {"period_end": "2026-08-31", "value": 64090.0}, "change": 0.0}]}
```

Members are largest first, unmeasured last; `member` is a stable id (an
owner or product pk, `unassigned`/`none`, a segment key, a lifecycle
value) so the same member's month-end row is found again next month.
`record_period_end` writes these cuts beside the whole-org rows, on the
same keep-don't-overwrite rule.

### `GET /api/v1/metrics/signals/`

The metrics that moved materially since the last month-end, bad news
first, each naming the members that moved it most. "Material" is
deliberately blunt: 5 points for a percent metric, 10% relative for
money and counts — a short list a manager reads, not a statistical test
over one month of history. Empty, with `baseline: null`, until a
month-end exists to compare against; the response says so rather than
inventing a baseline.

```json
{"as_of": "2026-09-12", "baseline": "2026-08-31", "currency": "USD",
 "signals": [{"key": "at_risk_arr", "label": "ARR at risk", ..., "value": 114540.0,
              "previous": {"period_end": "2026-08-31", "value": 80000.0}, "change": 34540.0,
              "improved": false,
              "drivers": [{"dimension": "product", "dimension_label": "Product", "member": "4",
                           "label": "Product B", "value": 64090.0, "change": 30000.0}]}]}
```

`improved` is null for a metric whose `better` is `none`. `drivers` is
the five biggest absolute moves across every cut the metric has, and is
empty for a metric with no cuts.

### `GET /api/v1/metrics/brief/`, `POST /api/v1/metrics/brief/generate/`

The management brief: the metric layer read out loud by Claude. The
per-customer Headlines generator writes one account's story from its
records; this writes the organisation's from the metric layer — the
numbers, what moved since the last month-end, and what the cuts say
drove it — through the same `anthropic_client`.

**Grounded, and checkably so.** The prompt carries exactly the figures
the screen shows (`brief.build_prompt`) — no records, no customer names,
nothing the snapshots don't hold — and the evidence is stored beside the
text (`Brief.evidence`), so any sentence can be checked against the
numbers it was written from. "Unmeasured" is written as unmeasured,
never zero.

**Stored, not regenerated on view.** A brief costs a real model call,
and last month's brief is itself a record. `GET` returns the latest
(`{"brief": null}` before one exists); `POST .../generate/` writes a new
one — an explicit action, never a side effect of loading the page.
Error mapping matches `HeadlineGenerateView`: `503` when no provider is
configured, `502` when the call fails or the answer isn't readable,
`422` for an organisation with no customers. Both gated on
`view_all_accounts`.

```json
{"brief": {"id": 3, "as_of": "2026-09-12", "baseline": "2026-08-31",
           "headline": "…one sentence, a fact…",
           "body": "…3-5 paragraphs, blank-line separated…",
           "watch": ["…2-4 things, each citing a figure…"],
           "generated_at": "2026-09-12T15:40:02Z", "generated_by": "Alice"}}
```

### Initiative `work` — the tasks under a decision

`Task.initiative` (customers migration 0035, SET_NULL) links a to-do to
the decision it serves. `proposals.approve` sets it when a task proposal
carries `initiative`, so approving from the review queue is the
follow-through, not a separate step. Every initiative payload carries
`work` `{open, done, tasks: [{id, title, parent_name, parent_type,
parent_id, assignee_name, due_date, priority, status}]}`, open first and
soonest due first; task payloads carry `initiative` `{id, title}` or
null.

### `GET /api/v1/metrics/graph/` — the knowledge graph

Auth: `CanViewAllAccounts`. The brain's real relations as one graph
(`services/metrics/graph.py`): `nodes` of five kinds — `owner`,
`customer`, `product`, `initiative` (planned/active), `proposal`
(pending) — and `edges` `{from, to, kind}` only where a real relation
exists: `owns` (owner → customer), `runs_on` (customer → primary
product), `targets` (initiative → the product or owner its cut names),
`acts_on` (task proposal → customer), `serves` (proposal → linked
initiative). Every figure is the dashboards' own: customer `arr`,
`downside`, `risk`, `days_to_renewal` from the forecast rows,
`health_category`/`health_score`, `open_tasks`; product and owner nodes
sum `customers`, `arr`, `downside` over their customers. Node ids are
`kind:pk`. Customers are capped at 200, largest ARR first. Frontend:
`/brain/graph`.

```json
{"as_of": "2026-09-13", "currency": "USD",
 "nodes": [{"id": "customer:7", "kind": "customer", "label": "Pizza Hut", "arr": 69600,
            "downside": 17400, "risk": 0.25, "health_category": "average", "health_score": 5.1,
            "days_to_renewal": -34, "open_tasks": 1},
           {"id": "product:4", "kind": "product", "label": "Product B", "customers": 3, "arr": 180000, "downside": 64090},
           {"id": "initiative:1", "kind": "initiative", "label": "Halve the ARR at risk on Product B", "status": "active",
            "metric": "at_risk_arr", "metric_label": "ARR at risk", "member_label": "Product B",
            "target_value": 32000, "target_by": "2026-11-30", "owner": "Carl CSM"}],
 "edges": [{"from": "customer:7", "to": "product:4", "kind": "runs_on"},
           {"from": "initiative:1", "to": "product:4", "kind": "targets"}]}
```

### Models — `Explanation`

Why one metric is where it is, in the model's words, as of one day. The
latest per metric is what the Brain shows beside the number; older rows
stay as a record of what was said about the figure then. `inputs` is
exactly what the prompt carried — the definition, the value and its
month-end, every cut with each member's own move, the accounts carrying
the downside, open decisions on the number — and `evidence` the lines
the model chose to cite.

### `GET /api/v1/metrics/<key>/explanation/`, `POST /api/v1/metrics/<key>/explain/`

Auth: `CanViewAllAccounts`; a key the registry lacks is a `404`.
`GET` returns `{"explanation": {...}}` or `{"explanation": null}` before
one is written — reading is free. `POST` (`services/metrics/explain.py`)
is a real, paid call under the `explain` purpose: 2–4 sentences on why
the number is where it is and, when a month-end exists, what moved it,
naming the members and accounts with their figures; no advice. Every
figure must come from the input; "unmeasured" stays unmeasured. `201`
with the new row; `422` with no live customers; `429`/`503`/`502` as the
brief's. The frontend offers it as **Why?** on every metric tile and
every signal row.

```json
{"explanation": {"id": 3, "metric": "at_risk_arr", "metric_label": "ARR at risk",
  "as_of": "2026-09-13", "baseline": "2026-09-12", "value": 114540, "previous_value": 80000,
  "text": "ARR at risk rose because …", "evidence": ["Product B: USD 64,090 (was USD 34,090)"],
  "generated_at": "2026-09-13T06:10:00Z", "generated_by": "Alice"}}
```

### Models — `Initiative`

A decision, with a number attached. Management's half of the brain: the
metric layer says what the numbers are and what moved them; an
initiative says what someone decided to do about one of them — a
`hypothesis`, a `metric` from the registry (optionally one cut of it,
`dimension` + `member`, which must be a cut the registry has), a
`target_value`, a `target_by`, an `owner` — and is then judged against
the same registry everything else reads, so "did it work" is a fact
rather than a memory.

`baseline_value`/`baseline_as_of` are the starting line, captured when
the initiative is written from the registry as it stood that day, and
never re-read: the point is movement since the decision. `member_label`
is kept as it read at creation so a card still says "Product B" if the
cut is later empty. `status` is `planned`/`active`/`done`/`abandoned`;
closing stamps `closed_at`, reopening clears it; `outcome` is what
happened, written at close.

### `GET/POST /api/v1/metrics/initiatives/`, `GET/PATCH/DELETE /api/v1/metrics/initiatives/<id>/`

Auth: `CanViewAllAccounts`. Unpaginated. Each row carries the live
judgement:

```json
{"id": 1, "title": "Bring Product B's risk down",
 "metric": "at_risk_arr", "metric_label": "ARR at risk",
 "dimension": "product", "dimension_label": "Product", "member": "4", "member_label": "Product B",
 "target_value": "30000.0000", "target_by": "2026-11-11",
 "owner": {"id": 5, "name": "Carl CSM"}, "status": "active", "status_display": "Active",
 "baseline_value": "64090.0000", "baseline_as_of": "2026-09-12",
 "progress": {"baseline": 64090.0, "current": 64090.0, "target": 30000.0,
              "progress_pct": 0.0, "days_left": 60, "direction": "down", "unit": "money", "better": "down"},
 "history": [{"period_end": "2026-09-30", "value": 58000.0}]}
```

`progress.current` is the registry's value now (whole-org or the cut's
member); `progress_pct` is the share of the way from the starting line
to the target, clamped 0–100 and null when either end is unmeasured or
the target equals the baseline; `history` is the month-end snapshots for
that number since the baseline, so a card can draw the path. A page of
initiatives computes the registry once (`initiatives.Figures`), not once
per row. Writes take `owner_id`; validation refuses an unknown metric, a
cut the metric doesn't have (naming the ones it does), a member not in
that cut, a past `target_by` on create, and an owner from another
organisation.

### Models — `Proposal`

An action an agent proposed, waiting for a person to decide — the review
queue. `kind` is `task` (on an account) or `initiative` (on a number);
`title`, `rationale` and `evidence` (the figures cited, as given to the
agent) are the case; `action` is exactly what approving will do,
validated when the proposal is written rather than at the click;
`initiative` links the open decision it serves, if any; `status` is
`proposed`/`approved`/`rejected` with `decided_by`/`decided_at`/
`decision_note`; `result` is what approval created; `batch` groups one
generation run.

### `GET /api/v1/metrics/proposals/`, `POST …/generate/`, `POST …/<id>/approve/`, `POST …/<id>/reject/`

Auth: `CanViewAllAccounts`. The Ops agent (`services/metrics/proposals.py`)
reads what the brain knows — the metric layer, what moved, the cuts, the
accounts carrying the downside (`forecast.exposure_list`, with owner,
renewal and the churn rule's factors), the team, the open decisions — and
proposes 2–5 next actions. Two kinds only, both things a person could do
by hand in the app: a task on an account, an initiative on a number. No
emails, campaigns or anything outward-facing; **an agent's reach is
exactly a person's, and a person still has to say yes.**

**Validated before stored.** The prompt gives the agent account ids,
member names, metric keys, cuts and member ids; an answer naming
anything outside that set is dropped at generation with a log line, an
unknown assignee falls back to the account's owner, a link to a decision
that isn't open is dropped. The reviewer sees only actions that will
work.

`GET` returns `{"pending": n, "proposals": [...]}`, proposed first then
newest; `?status=` narrows. `generate/` is a real, paid call (`503`
unconfigured, `502` failed/unreadable, `422` no live customers).
`approve/` executes: a Task is created on the customer, or an Initiative
through `initiatives.create_initiative` (the same path the Initiatives
API uses, starting line included) — and records `result`; `reject/`
takes an optional `note`. A proposal is decided once: a second decision
is a `409`.

```json
{"pending": 1, "proposals": [{
  "id": 7, "kind": "task", "kind_display": "Task on an account",
  "title": "Run a save play on Pizza Hut before renewal",
  "rationale": "Pizza Hut carries USD 42,000 of downside and renews in 34 days…",
  "evidence": ["Pizza Hut: USD 42,000 downside, renews in 34 days, health poor"],
  "action": {"customer_id": 12, "customer_name": "Pizza Hut", "title": "Save play: Pizza Hut",
             "assignee_name": "Carl CSM", "due_date": "2026-09-19", "priority": "high"},
  "initiative": {"id": 1, "title": "Halve the ARR at risk on Product B"},
  "status": "proposed", "decided_by": null, "result": {}, "generated_by": "Alice"}]}
```

### `GET /api/v1/copilot/conversations/<id>/session/` and the WebSocket push — events carry no turn text

The session snapshot (`GET .../session/?since_id=`, and the same
`CopilotSessionSerializer` payload pushed over `ws/copilot/sessions/<id>/`
by `copilot.realtime.broadcast_session_update`) reaches everyone
`conversations_visible_to` admits — including a person who is only
mentioned and may read just a slice of the turns. So each event's
`message` is a **reference only**, never the turn's `content`, `sources`,
`context`, `author`, `questions` or `ask_suggestions`:

```json
{"id": 41, "kind": "redirected", "actor": {"id": 3, "name": "Bob"},
 "message": {"id": 118, "role": "user", "created_at": "2026-09-25T10:02:11Z"},
 "payload": {}, "created_at": "2026-09-25T10:02:11Z"}
```

To show the turn, refetch `GET /api/v1/copilot/conversations/<id>/`,
whose `messages` apply `visible_messages` — the only path turn text
travels. `message` is `null` on events that are not about a turn
(`made_live`, `joined`, `left`, `handed_off`, `closed`); a `handed_off` event's
`payload` carries `to_user_id`, `to_user_name` and the owner's `note`.

### `POST /api/v1/copilot/conversations/<id>/session/close/` with `capture_decisions`

Body `{"capture_decisions": true}` runs the facilitator in the same
request once the session is closed, returning the closed session plus
`decisions` (the proposals written, as the review queue shows them) and
`decisions_error` (null, or why nothing was captured — nobody spoke,
budget spent, provider down). **The close always stands**: a failed
capture never undoes it. Without the flag the close makes no model call.

### `GET/POST /api/v1/copilot/conversations/<id>/session/decisions/` — the facilitator

Auth: `IsAuthenticated`, and the conversation must be visible to the
caller (`conversations_visible_to`: owner, or an accepted, still-present
participant) and have a session — otherwise `404`. The facilitator
(`services/metrics/facilitator.py`) reads the session — the transcript
with each human turn's author (the owner sent the opening query; every
later human turn is tagged by its `redirected` event), who took part, who
handed off to whom and why — beside the Ops agent's own evidence, and
writes **what the people decided** into the review queue as proposals
tagged with the session (`Proposal.session`, surfaced as `source`
`{session_id, conversation_id, title}` on every proposal payload).

The prompt also carries **what the rest of the company has written about
the account** — the last ten contributions (function, author, date), so a
decision is not captured blind to engineering, sales or analytics context
— and the agent may cite them as evidence.

Decisions, not suggestions: the prompt admits only what a person decided
or agreed to; an idea the assistant floated that nobody took up is not a
decision, and an empty array is the right answer for a session where
nothing was settled. The session's own account is added to the accounts
the agent may target even when it carries no measurable downside, so a
task on it validates; everything else is validated exactly as the Ops
agent's answers are (`proposals.store_answer`), and approval runs through
the same review-queue endpoints under their own permission.

`POST` is a real, paid call under the `facilitator` purpose (`201` with
`{"proposals": [...]}`; `422` when nobody has spoken in the session or
the book has no live customers; `429`/`503`/`502` as the Ops agent's).
`GET` lists the proposals already written from this session, oldest
first.

### Models — `Feedback`

A person correcting something the system said — the feedback log. Three
kinds: `classification` (the model's tags on a ticket/email/call, fixed
by hand), `proposal` (the Ops agent's proposal approved or rejected, with
the note), `health_override` (a CSM overriding the rubric's score, or
clearing the override). Each row keeps `before` (what the system said)
and `after` (what the person said), `subject_type`/`subject_id`/
`subject_label`, `note`, `made_by`. This is the set of cases the next
prompt, taxonomy or rubric change should be read against.

### `PATCH /api/v1/interactions/<ticket|email|call>/<id>/classification/`

Auth: `IsAuthenticated`, and the record must be visible to the caller
(otherwise `404`). Body: any of `area`, `category`, `subcategory`,
`sentiment` — stored values or human labels, the same tolerance the
classifier's parser has — plus an optional `note`. A subcategory must sit
under the chosen category (the taxonomy's rule, `400` otherwise); a
value the taxonomy lacks is a `400`; a no-op is a `200` with
`feedback: null`. Stamps `classification_corrected_at` on the row, and
**`classify_interactions --reclassify` leaves corrected rows alone** from
then on (`--include-corrected` to override): a person's correction
outranks the model. Returns the row's new `keys`, `labels`, `corrected`
and the `Feedback` entry written.

### `GET /api/v1/metrics/feedback/`

Auth: `CanViewAllAccounts`. Newest first, up to 200; `?kind=` narrows;
`counts` gives the total per kind — the cheap answer to "how often is
the model wrong, and about what".

```json
{"counts": {"classification": 2, "proposal": 4, "health_override": 1},
 "feedback": [{"id": 9, "kind": "classification", "kind_display": "Classification corrected",
               "subject_type": "ticket", "subject_id": 12, "subject_label": "Slow page load for large accounts",
               "before": {"area": "support_operations", "category": "integration_support", "subcategory": "webhook_failure", "sentiment": "neutral"},
               "after": {"area": "product_growth", "category": "bug_report", "subcategory": "performance_issue", "sentiment": "neutral"},
               "note": "It's a perf bug, not a webhook.", "made_by": "Carl CSM", "created_at": "2026-09-12T18:20:11Z"}]}
```

### `GET /api/v1/metrics/<key>/history/`

The month-end series for one metric, oldest first, this organisation
only. A month with no row is absent, not zero. Unknown key → `404`.

```json
{"metric": {"key": "active_arr", "label": "ARR", "unit": "money", "better": "up", "note": "..."},
 "currency": "USD",
 "points": [{"period_end": "2026-07-31", "value": 640200.0}, {"period_end": "2026-08-31", "value": 688600.0}]}
```

## `customers` — Communications (`/communications`, `CommunicationsPage.tsx`)

The queue of things where a person is waiting on the caller, merged across four
models. Mirrors `react-ts-app/docs/design/communications.md`, which is the
product specification this was built from.

Two endpoints and no new model. What "waiting" means lives in
`services/customers/communications.py`; the views only turn it into HTTP.

### Why these four, and why one list

A customer email with no reply, a colleague's open question, an open ticket in
your department and a call logged without a summary all mean the same thing to
whoever opens the page: somebody asked and nobody answered. Ordering them by how
long each has waited, rather than by which table they came from, is the whole
product idea. Everything else in the app is scoped to one customer, so there was
previously no way to find an unanswered email without opening accounts one at a
time.

### Visibility

The module owns **no** rules of its own. Each channel starts from the helper
that already decides who may read that model, so the queue can never show a row
the record's own page would hide:

| Channel | Rule | Defined in |
|---|---|---|
| email | mailbox owner and their management chain | `services/mail/visibility.py` |
| question | the assignee | filtered directly on `Question.assignee` |
| ticket | own department plus undeparted; Leadership sees all | `services/customers/personal.py` |
| call | the customers and accounts you can see | `services/customers/scoping.py` |

`?scope=team` widens from the person to the person plus their subtree, which is
what the chain rules already permit. It deliberately does **not** change the
ticket count: a ticket carries a department, not an assignee, so there is no
personal ticket to separate from a team one. `stats` says so in
`ticket_scope_note` rather than pretending the two differ.

### What counts as a reply owed

An `Email` with `direction="received"` that is the **newest message in its
thread** for that mailbox. Expressed as "no later email exists in the same
thread", not "no reply exists", because a thread where they wrote three times
and nobody answered is one debt rather than three: excluding any email with a
later sibling leaves exactly the newest message per thread, and it is owed
precisely when that message came from them. One predicate, and it dedupes.

Mail with no `mailbox_owner` (seeded, or filed by a campaign) is **excluded from
`mine`**. Replies owed is about your own inbox, and an email belonging to nobody
counted in everybody's queue would be nobody's job.

`Email.thread_id` was stored but never queried before this, so migration
`0046_email_thread_index` adds `(mailbox_owner, thread_id, sent_at)`. Without it
the correlated subquery scans the table once per candidate row.

### `GET /api/v1/communications/`

Auth: required (`IsAuthenticated`). Returns DRF's list shape so the frontend's
existing `next`/`previous` walking works unchanged, plus three extra keys.

Query parameters:

| Parameter | Values | Meaning |
|---|---|---|
| `needs` | `true` (default), `false` | The queue, or the Everything stream over the same records |
| `kind` | `email`, `question`, `ticket`, `call`, repeatable | Narrow to one channel, what a tile click sends |
| `scope` | `mine` (default), `team` | Yours, or yours and your reports' |
| `q` | free text | Matches who, subject, snippet and account name |
| `page`, `page_size` | ints, page_size capped at 100 | |

```json
{
  "count": 6,
  "next": null,
  "previous": null,
  "truncated": false,
  "mode": "needs",
  "scope": "mine",
  "results": [
    {
      "id": "email:412",
      "kind": "email",
      "who": "Dana Whitfield",
      "detail": "",
      "subject": "Re: revised renewal terms",
      "snippet": "We would need the revised terms before the board meets…",
      "preview": "…up to 1200 characters…",
      "sentiment": "negative",
      "waiting_since": "2026-09-09",
      "waiting_days": 9,
      "account": { "id": 3, "name": "Pizza Hut", "type": "customer" },
      "context": {
        "health_score": 5.2, "health_category": "average",
        "arr": 128400.0, "renewal_date": "2026-10-22",
        "days_to_renewal": 34, "owner": "Carl"
      },
      "action": "reply",
      "external_url": ""
    }
  ]
}
```

`context` travels with every row on purpose: a renewal question answered without
the renewal date in view is the exact mistake the page exists to prevent, and a
second request per selection would make the detail pane flicker. `preview` is
capped at 1200 characters for the same reason — the pane renders without a
follow-up call, and a page of 25 stays small.

`action` tells the client which composer to show: `reply` (email, through the
caller's own mailbox), `answer` (question, stored as a Contribution),
`open_external` (ticket, out to the source system by `external_url`),
`summarise` (call), or `none`.

`truncated` is `true` when any one channel hit `MAX_PER_KIND` (200). A queue is a
working list: past a couple of hundred debts of one kind the number is the story,
and the payload says so rather than quietly serving a prefix. The merge happens
in Python — four models sharing no columns — so pagination does too, which is
exactly what that cap keeps honest.

Merged, not unioned, for the same reason `interactions.recent_rows` is: aliasing
four different shapes into one SQL union to sort them would cost more than
reading a bounded number of rows per model.

### `POST /api/v1/communications/emails/<id>/reply/`

Answer a queue email from the person's own mailbox. Body `{"body": "…"}`. The
email must be readable under the mailbox rule (owner and management chain) and
must be one somebody wrote to them (`direction=received` with a sender); the
reply goes to that sender under `Re: <subject>`, through the requesting user's
own `MailboxConnection`, and the copy is filed on the same customer or account,
which is what takes the debt out of the queue. Returns **201**
`{id, direction: "sent", subject, sent_at, thread_id}`. 400 when empty or when
the email has nobody to reply to; 404 outside the chain; 409 when the person has
no mailbox connected; 502 with the provider's reason. Audited as
`mailbox.reply`.

The frontend's `ReplyBox` (shared by the queue's detail pane and the mailbox
view) sends here for email rows; the mailbox view sends to
`/mail/messages/<id>/reply/` instead.

### `GET /api/v1/communications/stats/`

Auth: required. The four tile numbers, shipped separately from the list so that
clicking a tile narrows the queue **without** the other three counts moving
underneath it. Same reasoning as `/contacts/stats/` and `/tickets/stats/`.

```json
{
  "counts": { "email": 2, "question": 2, "ticket": 1, "call": 1 },
  "total": 6,
  "oldest_waiting_days": 9,
  "stale_questions": 1,
  "has_mailbox": true,
  "scope": "mine",
  "ticket_scope_note": "Tickets are read by department, so this count is the same for you and your team."
}
```

`has_mailbox` drives the first-run state: with no mailbox connected the replies
tile shows a dash rather than a zero, because zero would be a lie, and the other
three still carry real numbers.

### Deliberately not built in v1

No archive, snooze or "mark handled". A row leaves the queue only because the
underlying thing changed — a reply was sent, a question answered, a ticket
closed, a summary written. A queue you can dismiss without acting stops being
true within a week, and inbox state would be the first new model this page needs.
Keyboard navigation, bulk actions and posting ticket comments back through the
connector are v2 in the specification.

---

## `mail` — The person's own inbox (`/communications?source=mailbox:<provider>`, `MailboxView.tsx`)

Connecting a mailbox used to file only the messages that matched someone in
the book (`customers.Email`, the team's view of a customer). The Communications
page, opened on the mailbox source, now shows the person's mail **whole**: every
message the provider handed back, in the folders the provider keeps, with its
flags and a category. That is `mail.MailMessage`, one row per message per
connection, created at sync next to the filed copy and pointing at it when
there is one.

### Ownership

A mailbox's contents are the owner's and nobody else's. Every endpoint below
filters on `owner=request.user`; a manager reads the **filed** copies under
`services/mail/visibility.py`, never a report's whole inbox. Disconnecting the
mailbox deletes the personal copy (`connection` cascades) and leaves the filed
`Email` rows on the customer, as before.

### What the provider tells us, and what we keep

`providers.base.Message.labels` carries one vocabulary for all three providers:
`inbox sent draft spam trash archive unread starred important promotions social
updates forums personal`. Gmail maps its label ids (the fetch includes spam,
trash and drafts; a message with no folder label at all is `archive`); Graph
maps `isRead`, `flag`, `importance`, `isDraft` and the six well-known folders
(resolved once per mailbox and kept in the credentials; a message in any other
folder is `archive`); IMAP maps the folder it read from and `\Seen` /
`\Flagged`, wherever the server puts FLAGS in the fetch response.

**Only inbox and sent mail is filed against a customer** (`customers.Email`).
Spam is never evidence, trash was thrown away and a draft has not happened yet:
those are kept in the person's store only, and never reach the timeline, the
classifier or the pulse.

At sync those become `folder` (`inbox sent drafts spam trash`), `is_read`,
`is_starred`, `is_important`, and `category` via `services/mail/categorise.py`:
`financial` (subject mentions an invoice, renewal, payment…), then the
provider's own tab (`promotions`, `social`), `newsletters` (a `List-Unsubscribe`
header), `notifications` (Gmail's updates/forums, or a system sender such as
`noreply@`), else `general`. No model call: a category is a filing decision and
must come out the same every time.

Nothing is written back to the provider (Gmail is connected read-only). `state`
(`open done muted`) is this product's own triage and exists nowhere else; an
`archive`d message arrives as `done`.

**What refreshes after the first sync.** When the provider lists a message
again, its flags and folder are taken afresh (read, starred, important, moved,
archived → `done`) **unless the person changed something here**: a PATCH stamps
`locally_changed_at`, and from then on what they did here wins. Graph re-lists
on `lastModifiedDateTime`, so a read or a move in Outlook reaches Revenact on
the next pass. Gmail's cursor lists only mail newer than the last pass, so a
Gmail message's flags are what they were when it first arrived; that is the
honest limit of a read-only, cursor-based sync.

**Existing mailboxes.** Migration `0003` clears every connection's
`sync_cursor`, so the next pass re-lists the provider's window (30 days) and the
personal store fills in; filing and storing are both idempotent on the
provider's id, so nothing is duplicated.

**Sent mail the app itself sent.** Graph and SMTP return no id for a send, so
the app records the send under a `sent:<uuid>` placeholder; when the provider's
own Sent copy is listed later (same subject, within ten minutes) the placeholder
takes its id rather than a second row appearing.

`priority` on a row is `is_important` **or** "from someone in the book"
(`email` is set).

### `GET /api/v1/mail/messages/`

Auth: required. DRF's paginated list shape (`page`, `page_size` ≤ 100).

| Parameter | Values | Meaning |
|---|---|---|
| `folder` | `inbox` (default), `drafts`, `sent`, `done`, `muted`, `spam`, `trash`, `starred`, `important` | Which list. `inbox` is `folder=inbox` **and** `state=open`; `done`/`muted` are the two triage states; `starred`/`important` are the flags across folders |
| `category` | `general financial newsletters notifications promotions social` | Narrow to one |
| `unread` | `true` | Only unread |
| `priority` | `true` | Only priority (see above) |
| `q` | text | Subject, sender name, sender address, snippet |

Row: `id, thread_id, direction (sent|received), from_name, from_address,
to ([[name, address]…]), subject, snippet (≤300 chars), sent_at, folder,
category, state, is_read, is_starred, is_important, priority, account
({id, name, type: customer|account} from the filed copy, else null)`. No body:
that is the detail's.

400 for an unknown `folder` or `category`.

### `GET /api/v1/mail/messages/summary/`

The numbers beside the folders and the Categories block at the top of the
inbox.

```json
{
  "has_mailbox": true,
  "address": "dana@acme.io",
  "last_synced_at": "2026-09-21T17:02:11Z",
  "folders": {"inbox": 41, "drafts": 2, "sent": 12, "done": 7, "muted": 1},
  "unread": 9,
  "categories": [
    {"category": "financial", "label": "Financial", "count": 2,
     "subjects": ["Subscription renewal", "Invoice 1042"],
     "senders": ["Circleback"], "more_senders": 1}
  ]
}
```

`categories` lists each non-general category with **unread inbox** mail, newest
first within it: up to two distinct subjects, the newest sender, and how many
other distinct senders there are. `general` is never a block; it is the list
itself.

### `GET | PATCH /api/v1/mail/messages/<id>/`

GET adds `body` to the row. PATCH accepts `is_read`, `is_starred`, `state`
(partial) and returns the detail. 404 for anyone but the owner, including
their manager.

### `POST /api/v1/mail/messages/<id>/reply/`

`{"body": "…"}` — sends from the mailbox the message arrived in, to whoever
wrote it (or, for something the person sent, to the same recipients), under
`Re: <subject>`. If the original was filed against a customer the reply is
filed there too (`send_email`, so the team's picture of that account gains the
answer and it is classified like any other email); otherwise it is only the
person's own sent mail. Marks the original read. Returns **201** with the sent
message as it now sits in `sent`. 400 when the message names nobody to reply
to; 409 when the mailbox is no longer connected;
502 with the provider's reason when the send fails. Audited as `mailbox.reply`.

### Where it sits in the frontend

`CommunicationsPage` renders `MailboxView` in the inbox card when the sidebar
source is `mailbox:<provider>`: folders (Inbox, Drafts, Sent, Done, Muted with
counts), Filters (Priority, Unread), Categories (Starred, Important, Spam,
Trash, then the five categories), the Categories block, the list grouped by
month, the open message in place with its reply box. The queue view (the four
kinds of waiting) is unchanged for every other source.

## `attention` — Dashboard Overview's "Needs attention" list

Mirrors: `src/pages/dashboard/...` (the Dashboard Overview redesign,
`react-ts-app/docs/superpowers/specs/2026-09-23-dashboard-redesign-design.md`).

Five kinds of item — **renewal**, **risk**, **going_quiet**, **support**,
**anomaly** — each built from the rule the screen it comes from already owns
(`services.attention.rules.build_items`), so the list can never disagree with
the page an item drills into. Every item is scored `at_stake × urgency`: ARR
in the organisation's currency times a 0.25–1.0 urgency whose formula is
per-kind. Twice filtered, same as every other dashboard here: companies
through `live_customers`, tickets through `visible_tickets`, anomaly evidence
through `visible_evidence` — a rule that reads across models never widens
what any one of them already restricts. No record text reaches an item;
reasons are built from fields only.

**Urgency formulas** (`services.attention.rules`, floor 0.25, ceiling 1.0,
straight line between the two points given):

| Kind | Measure | Formula |
|---|---|---|
| `renewal`, `risk` | days to renewal (negative = overdue) | overdue or ≤ 14 days → 1.0, down to 0.25 at 90 days; no renewal date at all → 0.5 |
| `going_quiet` | days since last contact | never contacted → 1.0; 0.25 at 60 days, up to 1.0 at 120 |
| `support` | the oldest matching open High/Critical ticket's age in days | 0.25 at 0 days, 1.0 at 14 days |
| `anomaly` | days since the anomaly was first seen | ≤ 7 days → 1.0, down to 0.25 at 90 days |

`score = round(at_stake * urgency, 2)`.

### Models

- `AttentionSnooze` — `organisation`, `user`, `key` (unique together),
  `until` (nullable; null means Done), `fingerprint` (JSON, the item's
  fingerprint at snooze time), `created_at`.

### `GET /api/v1/dashboard/attention/?owner=&lifecycle=&customer=`

Auth: `IsAuthenticated`. The viewer's own candidate items — same filter
parsing as `GET /api/v1/customers/forecast/` (bad values ignored, never a
400) — with anything the viewer has snoozed dropped
(`services.attention.snooze.visible_items`), sorted by score desc then
title, capped at 25:

```json
{
  "items": [
    {
      "key": "renewal:42", "kind": "renewal", "title": "Acme",
      "reason": "renewal 5 days overdue · health Poor",
      "at_stake": 100000.0, "urgency": 1.0, "score": 100000.0,
      "customer_id": 42, "companies": [], "fingerprint": {"overdue": true, "health": "poor", "arr": 100000.0}
    }
  ],
  "currency": "USD",
  "filters": {"owners": [...], "lifecycles": [...], "customers": [...]}
}
```

`fingerprint` is the item's facts at the time it was built — the snooze
endpoint stores it, and `worse` below compares against it. Its shape is
internal and can change; clients should not read it. Per kind:

| `kind` | `fingerprint` (every one also carries `arr`, the ARR at stake) |
|---|---|
| `renewal` | `overdue` (bool), `health` (category), `renewal_date` (ISO date) |
| `risk` | `score` (the Triage score) |
| `going_quiet` | `last_contact` (ISO date, or `null` for never contacted) |
| `support` | `count` (open High/Critical tickets), `open_ids` (their ticket ids, sorted) |
| `anomaly` | `companies` (how many of the viewer's companies it spans) |

It holds facts (dates, counts, ids), never a count of days, so time passing
on its own never changes it.

**Size.** The list is built over the viewer's whole (filtered) live book on
every call, and is not capped before scoring — a cap would silently drop
items. For a `view_all_accounts` viewer that is the whole organisation;
the query count is constant, but the work grows with the book.

`companies` is only ever populated for `kind: "anomaly"` (an anomaly can span
several companies in the viewer's book); every other kind carries `[]`. ARR
that can't be converted to the org's currency counts as 0 at stake — the item
still appears, and its `reason` says so — rather than being dropped.

**Anomaly title rule.** The stored, model-written `Anomaly.title` can name a
company outside this viewer's book, or count companies across the whole
organisation, so it is only shown as written to a viewer who passes
`sees_everything` (`view_all_accounts` — `services.customers.scoping`).
Everyone else gets a title built from fields instead: `"Similar reports
across <n> of your companies"`, where `<n>` is the count of companies behind
that anomaly that are actually in their own (filtered) book.

### `POST /api/v1/dashboard/attention/snooze/`

Auth: `IsAuthenticated`. `{"key": "renewal:42", "days": 7}` (1–90) or
`{"key": "renewal:42", "done": true}`. `key` must be one of the viewer's own
current items *right now*, built with no filters — the same rule the full
list itself reads, so a key from another organisation, or one that has
already resolved, is refused rather than silently stored:

```json
{"key": ["Not an item on your list."]}
```

(400) — also for a malformed key (anything but `<kind>:<id>`). Only the
key's own kind is built to check it, and for a company's kind only that one
company, still read through the viewer's visible book; an `anomaly:<id>` key
builds the anomaly kind only. On success, upserts an `AttentionSnooze` for `(user, key)` with the
item's current fingerprint and `until = now + days` (or `null` for Done).
Returns **201** `{"key": "renewal:42", "until": "2026-10-01T12:00:00Z"}`
(`until: null` for Done). Snoozing is strictly per-user: another member of
the same organisation, or the same account's other owner, still sees the item
until they snooze it themselves. If the item gets worse before the snooze
would have expired, it reappears immediately — a 7-day snooze or Done
alike. "Worse" (`services.attention.rules.worse`) compares the stored
fingerprint with the current one, and any one of these is enough: more ARR
at stake (every kind); a renewal that has become overdue, or whose health
band dropped; a higher risk score; more open High/Critical tickets; an
anomaly spanning more of the viewer's companies. A new episode is worse
too, so a Done lasts one episode, not forever: a renewal with a different
`renewal_date` (a new cycle), a going-quiet item with a different
`last_contact` (a new silence after a contact), a support item with an open
ticket id that wasn't open when it was snoozed (even if the count is the
same). The silence growing is not a change — it is why a going-quiet item
is on the list — and a day passing is never "worse" on its own, so a Done
item stays hidden while its facts hold. A stored fingerprint missing a key
(an older shape) counts as not worse on that key. Audited as `attention.snoozed` (`key`, and `days` or `done`).

### `DELETE /api/v1/dashboard/attention/snooze/<key>/`

Auth: `IsAuthenticated`. Deletes the viewer's own snooze for that key — 404
if there isn't one. `<key>` is matched with Django's `path` converter because
a key contains a `:` (e.g. `renewal:42`); the percent-encoded form a browser
sends for `encodeURIComponent(key)` (`renewal%3A42`) is accepted too.
Returns **204**. Audited as
`attention.unsnoozed` (`key`).

**One verb per URL.** The collection URL (`.../snooze/`) only accepts `POST`;
the key URL (`.../snooze/<key>/`) only accepts `DELETE`. A `POST` to the key
URL or a `DELETE` to the collection URL returns **405** — they are two
separate view classes (`AttentionSnoozeView`, `AttentionSnoozeDetailView`)
rather than one class answering both, precisely so neither wrong combination
can reach a handler that doesn't take the arguments it would need.

## `<app_name>` — <Frontend feature name>

Mirrors: `src/pages/<domain>/...`, `src/features/<domain>/...`

### Models
- `ModelName` — field: type, notes...

### `<METHOD> /api/v1/<domain>/...`
Auth: <required/AllowAny>
**Request** / **Response** shapes, with a short note on any place the
backend shape deviates from the frontend's current TS interface and why.
-->
