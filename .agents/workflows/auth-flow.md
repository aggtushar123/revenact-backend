---
description: Multi-tenant auth flow - signup, login, logout, own-profile editing, and admin User Management
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
   with the refresh token (7-day lifetime).

## Logout Flow

1. `POST /api/v1/auth/logout/` with `Authorization: Bearer <access token>`
   and `{"refresh": "<refresh token>"}` in the body.
2. The refresh token gets blacklisted (`rest_framework_simplejwt.token_blacklist`)
   — `/token/refresh/` will reject it with `401` afterward.
3. Always `205`, even if the refresh token was already invalid/expired/
   blacklisted — logging out isn't an error just because the token was
   already dead. Safe to call twice.
4. **The access token itself is not revoked** — it keeps working until its
   own 60-min expiry. Only the refresh token is tracked for revocation.
   Frontend clears its local access token immediately regardless (see
   `authSlice.ts: logout`), so this only matters if a token was captured
   by someone else before logout.

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

## User Profile — Your Own Profile (any role)

1. `GET /api/v1/auth/me/` returns your own profile (same shape as the
   `user` object from login/signup).
2. `PATCH /api/v1/auth/me/` with `{"name": "..."}` updates it. Email and
   role are read-only here — sending them is silently ignored, not an
   error (`MeSerializer.Meta.read_only_fields`).
3. To change your password: `POST /api/v1/auth/me/change-password/` with
   `{"current_password": "...", "new_password": "..."}`. Requires proving
   you know the current one — `400` with a field error if it's wrong.
4. This does **not** invalidate your other sessions/tokens. Changing your
   own password from an active session doesn't force that session to
   re-authenticate (unlike admin deactivation, below, which does force
   re-authentication — deliberately different: one is self-service from a
   trusted session, the other is an external actor cutting off access).

## User Management — Admin Manages Members

1. `GET /api/v1/auth/csms/` lists the CSMs in the admin's own
   organisation (paginated) — **not** the admin themselves. This is
   "manage my members", separate from the admin's own `/me/`.
2. `POST /api/v1/auth/csms/` — same as "Admin Adds a CSM" below.
3. `GET/PATCH /api/v1/auth/csms/<id>/` — org-admin only, scoped to the
   caller's own org. **A CSM outside that scope (wrong org, or not a CSM)
   404s, not 403s** — an admin can't distinguish "doesn't exist" from
   "exists but isn't yours" by the response.
4. PATCH accepts any of `name`, `is_active`, `password` (partial —
   send only what's changing):
   - `password`: an **admin override**, no current-password check. This
     is currently the *only* way a CSM recovers a forgotten password —
     there's no self-serve reset flow.
   - `is_active: false`: deactivates the CSM. Their access token is
     rejected on its very next request (simplejwt's `JWTAuthentication`
     checks `is_active` on every request — this isn't specific to
     deactivation, it's just the first time this feature has made it
     observable). Their outstanding refresh tokens also get blacklisted,
     as defense-in-depth for the refresh path specifically.
   - `is_active: true`: reactivates them — same account, they just log
     in again (their old refresh token, if it was blacklisted while
     deactivated, is still dead; they get a fresh one on next login).

## Roles

| Role | Created by | Can do |
|---|---|---|
| `admin` | Signup (exactly one per org, at creation) | Everything a CSM can, plus User Management |
| `csm` | An org admin, via `/csms/` | Everything except User Management |

Both roles authenticate identically and both can view/edit their own
`/me/` — `role` only gates User Management (`IsOrgAdmin`). There's no
path to a second admin, demoting/promoting a user, or removing (as
opposed to deactivating) a member yet.

## Key Files

| File | Purpose |
|---|---|
| `accounts/models.py` | `Organisation`, `User` (`AUTH_USER_MODEL`), `UserManager` |
| `accounts/serializers.py` | `SignupSerializer`, `LoginSerializer`, `LogoutSerializer`, `CreateCSMSerializer`, `MeSerializer`, `ChangePasswordSerializer`, `EditCSMSerializer`, `UserSerializer` |
| `accounts/permissions.py` | `IsOrgAdmin` |
| `accounts/views.py` | `SignupView`, `LoginView` (simplejwt `TokenObtainPairView`), `LogoutView`, `MeView`, `ChangePasswordView`, `CSMListCreateView`, `CSMDetailView` |
| `accounts/urls.py` | `/signup/`, `/login/`, `/logout/`, `/token/refresh/`, `/me/`, `/me/change-password/`, `/csms/`, `/csms/<id>/` |
| `config/settings.py` | `AUTH_USER_MODEL`, `SIMPLE_JWT`, `DEFAULT_AUTHENTICATION_CLASSES`/`DEFAULT_PERMISSION_CLASSES`, `rest_framework_simplejwt.token_blacklist` in `INSTALLED_APPS` |

## Not Built Yet

- Removing a member outright (only deactivate — the row and its history
  stay)
- A self-serve "forgot password" flow — admin password-reset via
  `/csms/<id>/` is the only recovery path for a CSM right now
- Email verification
- A second admin per organisation, promoting a CSM to admin, or any role
  beyond admin/csm
- Automatic refresh token rotation (a new refresh token issued — and the
  old one blacklisted — on every `/token/refresh/` call). Logout-time and
  deactivation-time blacklisting are both built; rotation-on-refresh is not.
- Invalidating other sessions when you change your *own* password (an
  admin *deactivating* someone does force re-authentication — see User
  Management above — but a self-service password change deliberately
  doesn't touch other active sessions of the same account)

## Extending This

Adding a new role, or a way to remove/demote a member? Update this file
alongside the code per the `flow-docs` skill — don't let it drift.
