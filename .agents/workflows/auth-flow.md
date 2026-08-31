---
description: Multi-tenant auth flow - organisation signup, login, and admin-adds-CSM
---

# Auth Flow

Revenact is shared across Organisations. Each Organisation is a tenant with
exactly one admin (created at signup) and any number of Customer Success
Managers the admin adds afterward. Everyone logs in with the same endpoint,
regardless of role.

## Signup Flow (Organisation + its first Admin)

1. Whoever is setting up the org submits `organisation_name`, `name`,
   `email`, `password` to `POST /api/v1/auth/signup/` (public, `AllowAny`).
2. Backend creates one `Organisation` row (slug auto-generated from the
   name) and one `User` row with `role=admin`, FK'd to it — in a single
   transaction (`accounts/serializers.py: SignupSerializer.create`).
3. Response includes `user` + `access` + `refresh` — the admin is logged
   in immediately, no separate login step required.

## Login Flow (any user — admin or CSM)

1. `POST /api/v1/auth/login/` with `email` + `password` (public, `AllowAny`).
2. `LoginSerializer` (subclasses simplejwt's `TokenObtainPairSerializer`)
   authenticates against `USERNAME_FIELD = "email"`, then adds the `user`
   object to the token response.
3. `401` with `{"detail": "No active account found with the given credentials"}`
   on bad credentials — same response whether the email doesn't exist or
   the password is wrong (doesn't leak which).
4. Access token expires in 60 min; refresh via `POST /api/v1/auth/token/refresh/`
   with the refresh token (7-day lifetime, no rotation/blacklist yet).

## Admin Adds a CSM

1. The admin calls `POST /api/v1/auth/csms/` with `Authorization: Bearer
   <admin's access token>` and `{name, email, password}` for the new CSM.
2. `IsOrgAdmin` permission checks `request.user.role == "admin"` — a CSM
   or an unauthenticated caller gets `403`/`401`.
3. The new `User` (`role=csm`) is created under **the admin's own**
   `organisation` — there's no field to target a different org.
4. The admin sets the CSM's password directly and communicates it to them
   out-of-band (Slack, in person, whatever) — no invite email is sent.
   The CSM then logs in themselves via the same `/login/` endpoint.

## Roles

| Role | Created by | Can do |
|---|---|---|
| `admin` | Signup (exactly one per org, at creation) | Everything a CSM can, plus add CSMs |
| `csm` | An org admin, via `/csms/` | Everything except add CSMs |

Both roles authenticate identically — `role` only gates the one admin-only
action (`IsOrgAdmin`). There's no path to a second admin, demoting/promoting
a user, or removing a member yet.

## Key Files

| File | Purpose |
|---|---|
| `accounts/models.py` | `Organisation`, `User` (`AUTH_USER_MODEL`), `UserManager` |
| `accounts/serializers.py` | `SignupSerializer`, `LoginSerializer`, `CreateCSMSerializer`, `UserSerializer` |
| `accounts/permissions.py` | `IsOrgAdmin` |
| `accounts/views.py` | `SignupView`, `LoginView` (simplejwt `TokenObtainPairView`), `CreateCSMView` |
| `accounts/urls.py` | `/signup/`, `/login/`, `/token/refresh/`, `/csms/` |
| `config/settings.py` | `AUTH_USER_MODEL`, `SIMPLE_JWT`, `DEFAULT_AUTHENTICATION_CLASSES`/`DEFAULT_PERMISSION_CLASSES` |

## Not Built Yet

- Listing/removing organisation members
- Password reset / change password
- Email verification
- A second admin per organisation, or any role beyond admin/csm
- Refresh token rotation/blacklist (a leaked refresh token is valid for its
  full 7-day lifetime — acceptable for now, revisit before production)

## Extending This

Adding a new role, or a way to remove/demote a member? Update this file
alongside the code per the `flow-docs` skill — don't let it drift.
