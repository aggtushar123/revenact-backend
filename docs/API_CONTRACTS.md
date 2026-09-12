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
| Dashboards — Health Overview | `customers` | 🟢 All four tabs (Triage/Divergence/Movement/Controls) run on `GET /api/v1/customers/health/` — see that endpoint and `HealthSnapshot` below. |
| Dashboards — Ticket Overview | `customers` | 🟢 Controls tab runs on `GET /api/v1/tickets/stats/` (`TicketStatsView`), with `Connector` behind its origin chart. **Documented in the code, not here yet** — that view's own docstring is the contract for now. |
| Dashboards — AI Trending Topics | `customers` | 🟢 Controls tab runs on `GET /api/v1/interactions/stats/` — see below. Its other six sub-tabs are the filter bar, not separate screens. |
| Copilot (`/copilot`) | `copilot`, `customers` | 🟡 Real Anthropic Claude chat, grounded in real data — see the `copilot` app's own section below. `POST .../messages/` makes a real, synchronous call to Claude (no task queue, no streaming), with each request's system prompt grounded in a real-data digest of the *caller's own owned* book of business (health/NPS/lifecycle, top at-risk customers, open opportunity/risk/ticket counts), **plus real retrieved content** — a company identified from the question (an exact name match first, then a real local-embeddings semantic fallback for a company described but not named — embedding its name plus a real hand-entered `industry` when one's been set, e.g. "that video conferencing account" finding Zoom, see `services/copilot/embeddings.py`; no pgvector, plain Python cosine similarity, a documented real limitation once `industry` is blank and the name is also a common word) gets its own recent real Emails/Notes/open Tickets/Activities, relevance-ranked against the question (`services/copilot/retrieval.py`); otherwise falls back to "one of your own top at-risk companies" — not the whole tenant's, same "My" framing as Cockpit's own. Conversations are private per-user. Requires the selected provider's own real credentials (`COPILOT_LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY`, or `=bedrock` + real AWS credentials/`BEDROCK_MODEL_ID` — see the `copilot` app's own section below); returns a clear `503` without them rather than a fake answer. First real consumer of `Organisation.ai_agent_enabled`/`ai_agent_tone`. No per-skill tool-calling/function execution — the "Built-in Skills" cards just prefill the compose input. The page's own Cockpit tab is real too now — `GET /api/v1/cockpit/summary/` and `GET /api/v1/tasks/?mine=true` (see the `customers` app's own section) back "My Portfolio Summary"/"Renewals"/"My Tasks", scoped to the caller's own owned book of business; replaces what used to be fixed literal numbers and an entirely separate local mock Redux task list. |
| Scenarios (builder, `/scenarios`) | `scenarios` | 🟡 Full CRUD + a real (deliberately limited) execution engine — see below. `nodes`/`edges` round-trip verbatim; "Run Now" and the On Event → "Creation of new entity" trigger actually execute Send Email/Create Task/Set Attribute/Churn Entity/Condition/Filter against a real Customer. Every other node type (Assign Playbook, Slack Message, Create Pipeline, MS Teams, Send Survey, Schedule) stays a frontend-only mockup; hitting one during a run just logs "skipped". Only `apply_to === "organizations"` scenarios are runnable in v1. |
| Campaigns (`/campaigns`) | `campaigns` | 🟡 Full CRUD + a real (deliberately limited) send — see below. `POST .../send/` really emails every recipient via the same `send_mail` plumbing as Scenarios' own "Send Email," synchronously (no task queue), and creates one real `customers.Email` row per successful send so it shows up in that recipient's own parent's Activity Feed. A recipient with no email on file is logged as skipped, never fatal. No scheduled sends, no templates beyond plain text, no open/click tracking (plain SMTP, no ESP webhooks). |
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

GET returns the same shape as the `user` object in login/signup. PATCH
accepts `{ "name": "..." }` — `email` and `role` are read-only here; sending
them is silently ignored (not an error), not written.

**Response `200`** (both) — the (possibly updated) profile.

### `GET /api/v1/auth/organisation/`, `PATCH /api/v1/auth/organisation/`

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
  | Customer Touch | 4.0 | days since the newest `Activity.occurred_at`, decaying linearly to zero at 90 days; measured from `joined_date`/`created_at` when there are no activities yet, so a new logo isn't punished |
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
  split into two real columns), `top_source_channel`,
  `total_contracted_seats`, `total_active_seats`,
  `seat_utilization_percentage` (**derived** from those two — active ÷
  contracted × 100, `None` if contracted is 0/unset — this one *is* a
  pure function of its inputs with no independent real-world meaning, so
  deriving it is safe, unlike the financial fields), `total_hires`,
  `scope_web_app`, `ces_percentage`.
- **Churn**: `churn_date`, `churn_reason`, `churn_comment` — all nullable/blank.

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
Archived customers (`is_archived=true`) never appear in this list, or
in `?renewal_within=`, or in the stats endpoint below — soft-hidden,
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
given, so it is safe on a schedule and won't overwrite a hand
correction; `--dry-run` counts the work without calling anything and
`--limit` caps the spend. It is deliberately **not** bundled into
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

**Response `200`**

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

A Condition/Filter node's saved clause is intentionally narrower than
its own fancier-looking canvas UI: one attribute (`lifecycle_stage`/
`health_score`/`nps_score` — a fixed allowlist, not any model field) +
one operator (`equals`/`not_equals`/`greater_than`/`less_than`) + one
value, stored on the node's own `data` as `conditionAttribute`/
`conditionOperator`/`conditionValue`. Condition branches by which
outgoing edge has `label: "Yes"`/`"No"` — the same manually-set label
`CustomEdge.tsx`'s own "Set Label" pill already produces, not a new
concept.

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

### Models

- `Conversation` — `organisation`, `user` (private per-user, not shared
  org-wide), `title` (derived from the first message's own text — no
  rename UI), `created_at`/`updated_at`. Lazily created on the first
  message actually sent, same "no ghost rows" convention as Canvas/
  Campaign's own editors (POST on first Save).
- `Message` — `conversation`, `role` (`user`/`assistant` — mirrors the
  Anthropic Messages API's own two-role shape exactly), `content`,
  `created_at`.

### Conventions specific to this app

`content` is capped at 8000 characters (`400` if blank or over).
Replayed history to the model is capped to the conversation's own last
20 messages (Anthropic's Messages API is stateless — the full history
must be resent every turn; unbounded replay would grow cost/latency
without limit).

### `GET /api/v1/copilot/conversations/`

Auth: `IsAuthenticated`. Every Conversation the caller has started —
scoped to `request.user`, not the whole organisation. **Pagination is
off**, same reasoning as `CampaignListCreateView`. Powers the sidebar's
own "Chat history" list.

**Response `200`**
```json
[
  { "id": 5, "title": "What's my churn risk?", "created_at": "2026-09-05T10:00:00Z", "updated_at": "2026-09-05T10:01:00Z" }
]
```

### `GET/DELETE /api/v1/copilot/conversations/<id>/`

Auth: `IsAuthenticated`. Scoped to the caller's own conversations (404,
not 403, otherwise). No `PATCH` — a conversation's title/messages are
only ever set by `POST .../messages/`.

**Response `200`** (GET) — adds nested `messages` (each `{id, role,
content, created_at}`) to the list shape above.
**Response `204`** (DELETE) — empty body.

### `POST /api/v1/copilot/messages/`

Auth: `IsAuthenticated`. Body: `{"conversation_id": <id>?, "content":
"..."}`. Omit `conversation_id` to start a new Conversation (titled from
this message); pass an existing one (must be the caller's own, `404`
otherwise) to continue it. The real send — runs synchronously, no task
queue.

`400` if `content` is blank or over 8000 characters. `403` if
`Organisation.ai_agent_enabled` is `false`. `503` if the selected
provider's own real credentials aren't configured (`ANTHROPIC_API_KEY`,
or the AWS/Bedrock ones — see the `copilot` app's own section above).
`502` if the real API call itself fails (bad credentials, no Bedrock
model access granted, rate limit, network error).

**Response `200`** — the (possibly newly created) Conversation, same
nested shape as the detail endpoint's GET, now including this turn's
user message and the model's real assistant reply.

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

<!--
Template for each new feature section below:

## `<app_name>` — <Frontend feature name>

Mirrors: `src/pages/<domain>/...`, `src/features/<domain>/...`

### Models
- `ModelName` — field: type, notes...

### `<METHOD> /api/v1/<domain>/...`
Auth: <required/AllowAny>
**Request** / **Response** shapes, with a short note on any place the
backend shape deviates from the frontend's current TS interface and why.
-->
