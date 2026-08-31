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
| Organizations (list/board/detail) | `customers` | 🟢 Full `tableData.ts` schema built, API-complete — see below. List view fetches real paginated data (`features/customers/customersSlice.ts` + `mapToOrgRow.ts`); MetricsPanel still on mock data (needs a stats endpoint, not built). Board, Details, activity feeds, nested Accounts/Contacts not started. |
| Accounts | — | ⏳ Not started |
| Contacts | — | ⏳ Not started |
| Pipelines | — | ⏳ Not started |
| Dashboards (Health/Ticket/AI Trending) | — | ⏳ Not started |
| Copilot | — | ⏳ Not started |
| Scenarios | — | ⏳ Not started |
| Company Brain | — | ⏳ Not started |

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
  `created_at`. The tenant. Created only via signup.
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

### `GET /api/v1/auth/members/`

Auth: `IsAuthenticated` (any role) — **not** admin-gated, unlike everything
below it. Every member (admin + CSMs) of the caller's own organisation.
Exists so any authenticated user can populate an owner-picker (e.g. the
`customers` app's "assign owner" field) without needing User Management
access.

**Response `200`** — a **plain array** (no pagination envelope; this list
is expected to stay small), each entry the same shape as elsewhere.

### `GET /api/v1/auth/csms/`, `POST /api/v1/auth/csms/`

Auth: **`IsAuthenticated` + org-admin only** (`accounts.permissions.IsOrgAdmin`).
Scoped to the caller's own `organisation` — there's no way to see or add a
CSM in a different org. `403` for an authenticated non-admin
(`{"detail": "Only an organisation admin can do this."}`), `401` if
unauthenticated.

GET lists the org's CSMs (paginated, per the usual envelope) — **not**
the admin themselves; this is "manage my members", not "list my org".
Ordered by name.

POST: **Request** `{ "name": "Carl CSM", "email": "carl@acme.io", "password": "csmpassword1" }`
**Response `201`** — the created user, same shape as the `user` object above
(no tokens — the CSM logs in themselves via `/login/`).

### `GET /api/v1/auth/csms/<id>/`, `PATCH /api/v1/auth/csms/<id>/`

Auth: same as above — org-admin only, scoped to the caller's own org.
**`404`, not `403`, for an id outside that scope** (wrong org, or not a
CSM) — an admin can't use the response to tell "doesn't exist" apart from
"exists but isn't yours".

PATCH accepts any of `{ "name": "...", "is_active": true|false, "password": "..." }`,
all optional (partial update). `password` here is an **admin override** —
no current-password check, since there's no other way for a CSM to
recover a forgotten password yet (no self-serve reset flow).

Setting `is_active: false` also blacklists every outstanding refresh
token for that user. Their access token is rejected on its very next
request regardless (simplejwt's `JWTAuthentication` checks `is_active` on
every request) — the blacklist call is defense-in-depth for the refresh
token specifically, not what makes deactivation effective.

**Response `200`** (both) — the (possibly updated) CSM, same shape as
elsewhere.

Not built yet, and deliberately out of scope for this pass: removing a
member outright (only deactivate), a second admin per org or promoting a
CSM to admin, self-serve organisation signup validation beyond email
uniqueness (e.g. org name collisions), email verification, a self-serve
"forgot password" flow (admin password-reset is the only recovery path
right now).

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
  "Name / Address" column), `domain`, `owner` (FK to `accounts.User`,
  nullable, same-tenant only), `created_by`/`modified_by` (FK to
  `accounts.User`, **always server-set** from the caller on create/update —
  not client-settable even if present in the request body),
  `created_at`/`updated_at` (the "Created Date"/"Modified Date" halves of
  those two mock columns). "Revenact ID" is just this row's own `id` —
  no separate field.
- **Lifecycle/health**: `lifecycle_stage` (choices, unchanged from the
  first pass), `health_score` (decimal **0.0–10.0** — matches this
  table's actual scale, which is *not* the 0–100 scale used elsewhere in
  the mock app, e.g. the dashboard's health donut; `health_category` is
  *derived* from it — good ≥7.0, average 4.0–6.9, poor <4.0 — never
  stored, so the two can't disagree), `pulse` (JSON list of small ints,
  e.g. `[1,1,0,2,1]`, the recent-pulse-history dots), `ai_pulse_score`
  (choices: very_satisfied/satisfied/moderate/high_risk — despite the
  name this is a categorical AI-generated label in the actual mock data,
  not a number), `ai_pulse_reason` (text), `nps_score` (integer, −100 to
  100), `csat_score` (decimal, 0–100).
- **Dates**: `joined_date`, `renewal_date`, `contract_start_date`,
  `contract_end_date` — all plain nullable dates, no derivation.
- **Financials** (all independently stored decimals, deliberately *not*
  derived from each other — see note below): `arr_billed_at_account`,
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
- **Owner must be same-tenant** — assigning `owner_id` to a user from a
  different organisation is a `400`, not silently ignored or allowed.
- **`organisation`, `created_by`, `modified_by` are never client-supplied** —
  `organisation` and `created_by` are set from `request.user` on create;
  `modified_by` is reset to `request.user` on *every* update. Sending any
  of the three in a request body has no effect.

### `GET /api/v1/customers/`, `POST /api/v1/customers/`

Auth: `IsAuthenticated` (any role). Scoped to the caller's own organisation.

GET: standard paginated envelope, ordered by name.
POST: only `name` is required — every other field above is optional.
**Response `201`** — the created customer, `owner`/`created_by`/`modified_by`
nested (same user shape as elsewhere), `health_category` and
`seat_utilization_percentage` computed.

### `GET /api/v1/customers/<id>/`, `PATCH /api/v1/customers/<id>/`

Auth: same as above. **`404`, not `403`,** for a customer outside the
caller's organisation.

PATCH accepts any subset of the POST fields (partial update), including
`owner_id` (`400` with a field error if the target user isn't in the
caller's organisation). Every PATCH sets `modified_by` to the caller,
regardless of which fields changed.

Not built yet, and deliberately out of scope: Board view, the Details
page (activity feed, pinned attributes), nested Accounts and Contacts,
deleting a customer (only field edits exist so far), and — the biggest
one — actually wiring any of this into the frontend, which still runs
entirely on `tableData.ts`.

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
