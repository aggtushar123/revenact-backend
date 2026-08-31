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
| Organizations (list/board/detail) | `customers` | 🟡 Core list fields only — see below. Board, Details, activity feeds, nested Accounts/Contacts not started. |
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

## `customers` — Organizations (list view only)

Mirrors: `src/pages/organizations/List.tsx`, `src/components/organizations/tableData.ts`
(core fields only — see Status above for what's deliberately deferred).

**Naming**: this is a separate model from `accounts.Organisation`. `Organisation`
is the tenant (the company paying for Revenact — one admin, its CSMs).
`Customer` is one of *that tenant's own* customers — the company a CSM is
tracking for health/ARR/renewal. Same real-world shape ("a company"), two
different roles in the system, hence two different names — never call a
`Customer` an "organisation" in code or docs, and vice versa.

### Models

- `Customer` — `organisation` (FK, the tenant that owns this record),
  `name`, `health_score` (0-100, `health_category` is *derived* from it at
  read time via fixed thresholds — good ≥70, average 40-69, poor <40 —
  not stored, so the two can never disagree), `arr` (decimal), `renewal_date`
  (nullable), `lifecycle_stage` (choices: onboarding/kickoff/adoption/live/
  renewal/churn/expansion/other), `owner` (FK to `accounts.User`, nullable —
  the assigned CSM or admin).

### Conventions specific to this app

- **No admin gate** — unlike User Management, any authenticated user in the
  tenant (admin or CSM) can list, create, and edit customers. This was a
  deliberate choice: managing your team's access (`accounts`) is more
  sensitive than managing shared customer records.
- **Owner must be same-tenant** — assigning `owner_id` to a user from a
  different organisation is a `400`, not silently ignored or allowed.
- **`organisation` is never client-supplied** — always taken from
  `request.user.organisation` server-side, even if a request body includes
  an `organisation` field. There is no way to create a customer under a
  different tenant than your own.

### `GET /api/v1/customers/`, `POST /api/v1/customers/`

Auth: `IsAuthenticated` (any role). Scoped to the caller's own organisation.

GET: standard paginated envelope, ordered by name.
POST **Request** `{ "name": "Globex Corp", "health_score": 82, "arr": "45000.00", "lifecycle_stage": "live", "renewal_date": "2027-01-15", "owner_id": 2 }`
— all fields but `name` optional. **Response `201`** — the created customer,
`owner` nested (same shape as elsewhere), `health_category` computed.

### `GET /api/v1/customers/<id>/`, `PATCH /api/v1/customers/<id>/`

Auth: same as above. **`404`, not `403`,** for a customer outside the
caller's organisation.

PATCH accepts any subset of the POST fields, including `owner_id` (`400`
with a field error if the target user isn't in the caller's organisation).

Not built yet, and deliberately out of scope for this pass: Board view,
the Details page (activity feed, pinned attributes), nested Accounts and
Contacts, deleting a customer (only field edits exist so far), the full
30+-field schema the frontend's mock data currently has (NPS, CSAT, TCV,
seat utilization, churn tracking, etc.).

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
