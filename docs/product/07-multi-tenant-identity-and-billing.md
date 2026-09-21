---
description: Architecture assessment and target design for multi-tenant identity, RBAC, onboarding, seats and billing — mapped onto the existing Revenact codebase.
---

# Multi-tenant identity, RBAC, seats and billing

**Status:** architecture assessment and design. No implementation yet, by
design: the brief's own Step 1 says inspect before changing files.
**Date:** 2026-09-20. **Author:** engineering.

This maps the requirement onto what Revenact actually is today. Where the
codebase already provides a concept, the design reuses it rather than adding a
parallel one.

---

## 1. What was inspected

| Question | Answer |
|---|---|
| Backend | Django 5.2.17, DRF 3.18.1, Python 3.13 |
| Frontend | React 19, TypeScript, Vite 8, Redux Toolkit (28 slices) |
| Database | PostgreSQL 16, Django ORM, 86 migrations, 46 models |
| Authentication | Email and password, Argon2id, SimpleJWT (access 60 min, refresh 7 days, rotation plus blacklist) |
| Authorization | `Role.permissions` holding a list of six `Capability` values; DRF permission classes in `services/accounts/permissions.py` |
| User model | `accounts.User`, `AUTH_USER_MODEL`, email login, **direct `organisation` FK**, `role` FK, `function`, `reports_to` |
| Role model | `accounts.Role`, per organisation, unique on `(organisation, slug)`, `is_system` flag |
| Permission model | `accounts.capabilities.Capability`, a closed six-value enum |
| Department model | None. `User.function` is a six-value enum used for routing and visibility |
| Tenant model | `accounts.Organisation` exists and is already the tenant |
| Record scoping | `services/customers/scoping.py`, `personal.py`, `mail/visibility.py`, `accounts/hierarchy.py` |
| API conventions | `/api/v1/<domain>/`, DRF pagination, `{detail}` errors, drf-spectacular schema |
| Session strategy | Stateless JWT; **capabilities resolved per request from the database** |
| Email | `services/email.py` over Django SMTP; `services/notifications` model plus WebSocket push |
| OAuth | **Already implemented** for Google and Microsoft in `services/mail/providers/`, with signed expiring state |
| Audit | `core.AuditEvent`, append-only (`save()` refuses updates), catalogue in `docs/audit-events.md` |
| Billing | **Absent.** No payments, plans, credits, seats or invoices anywhere |
| Tests | ~1,509 backend methods, 114 frontend files, three tiers |
| Compliance | `.soc2/` control map, 43 of 66 met |

---

## 2. The five findings that shape the design

### 2.1 The tenant already exists, and isolation is already derived server-side

`Organisation` is a real tenant. Every customer record reaches it, and
`scoping.visible_customers(user)` derives the tenant **from the authenticated
user**, never from a request parameter. The brief's §5 rule is already the
house rule. Two end-to-end tests assert cross-tenant 404s.

This is the single biggest piece of the requirement already being satisfied.

### 2.2 Capabilities are resolved per request, not baked into the token

`HasCapability.has_permission` calls `request.user.has_capability(...)`, which
reads `self.role.permissions` from the database on every request. Nothing
authorization-related is carried in the JWT.

That is exactly what §46 asks for, and it means a role change or suspension
takes effect on the **next request** with no token revocation needed. It is a
better starting point than most codebases and must not be regressed by moving
permissions into token claims for speed.

### 2.3 OAuth for Google and Microsoft is already built and in production

`services/mail/providers/google.py` and `microsoft.py` implement the full
authorization-code flow: `authorize_url`, `exchange_code`, refresh, a host
allow-list, and Fernet-encrypted credential storage. The callback validates a
`django.core.signing` state with a salt and a 15-minute `max_age`, which is the
state and replay protection §49 requires.

It is scoped to *mailbox access* rather than *login*, but the provider
abstraction (`PROVIDERS` registry, a base class, per-provider setup metadata)
is the shape §7 wants. **Login should reuse this abstraction, not duplicate it.**

### 2.4 The one structural change that matters: membership

```python
# today — a user belongs to exactly one organisation, forever
class User(AbstractBaseUser):
    organisation = models.ForeignKey(Organisation, related_name="members", ...)
    role = models.ForeignKey(Role, ...)
```

Identity, tenancy and authorization are collapsed onto one row. This is the
`User != Membership != Organization` violation in §4, and it blocks §45.

Everything else in the brief is additive. This one is a refactor with a blast
radius across the codebase, because `user.organisation` is read in scoping,
hierarchy, retrieval, notifications and most serializers.

### 2.5 Billing does not exist at all

No plan, payment, credit, seat, invoice or webhook-inbound-payment concept. This
is the largest greenfield piece, and the only one with no existing architecture
to respect. `services/webhooks` is **outbound only**; the inbound idempotency
pattern worth copying is `ConnectorInboundView`, which already does constant-time
token comparison, HMAC-SHA256 over the raw body, and dedupe on
`(connector, external_id)`.

---

## 3. Gap analysis

| Requirement | Status | Action |
|---|---|---|
| Tenant as first-class | Exists (`Organisation`) | Add `status`, billing relation |
| Server-derived tenant context | Exists | Preserve through the membership refactor |
| RBAC | Partial: 6 coarse capabilities | Expand to `resource.action`, keep the resolution path |
| Per-request permission resolution | Exists | Do not regress |
| Google / Microsoft OAuth | Exists for mail | Generalise to login |
| Identity separate from User | Absent | New `Identity` model |
| Membership | **Absent** | New `OrganizationMembership`; the core refactor |
| Departments | Absent (`User.function` is close but global) | New per-organisation `Department` |
| Organisation domains and verification | Absent | New models plus DNS TXT verification |
| Access requests | Absent | New |
| Invitations | Absent | New |
| Seats and credits | Absent | New, ledger-based |
| Billing account, plans, payments | Absent | New, behind a provider interface |
| Inbound payment webhooks | Absent (outbound only) | New, reusing the connector inbound pattern |
| Audit logging | **Exists and is strong** | Add the new action names to the catalogue |
| Platform admin | Partial (`is_superuser`, Django admin) | Needs a real platform-admin surface |
| Session invalidation | Partial (blacklist on logout/deactivate) | Extend to role and membership changes |
| Notifications | Exists | Reuse; keep out of the billing transaction |

---

## 4. Target design on this codebase

### 4.1 Model changes

New Django app `services/identity` for authentication and tenancy concerns, and
`services/billing` for money. Both sit beside the existing `services/*` apps.

```
accounts.Organisation          keep, add: status, primary_domain
  + OrganizationDomain         new: domain, verification_status, token, verified_at
  + BillingAccount             new: 1:1, provider customer id, currency

accounts.User                  keep, REMOVE organisation and role FKs (phase 3)
  + Identity                   new: provider, provider_user_id, email, verified
  + OrganizationMembership     new: user × organisation, status, role, department

accounts.Role                  keep, expand permissions vocabulary
  + Department                 new, per organisation

  + AccessRequest              new
  + Invitation                 new

billing.Plan                   new
billing.CreditLedger           new, append-only
billing.SeatAssignment         new, one row per membership that holds a seat
billing.Payment                new
billing.PaymentWebhookEvent    new, idempotency by (provider, event_id)
```

`core.AuditEvent` is reused unchanged. It already carries actor, organisation,
action, target, outcome, IP, user agent, request id and metadata, and it already
refuses updates and deletes. No second audit table.

### 4.2 Capability vocabulary

The existing six capabilities become a subset of a `resource.action` scheme.
`Role.permissions` stays a JSON list, so no schema change is needed, only a
wider enum and a data migration mapping old values to new:

```
manage_users        -> user.read, user.create, user.update,
                       access_request.approve, access_request.reject,
                       invitation.create, invitation.cancel, role.assign
manage_org_settings -> organization.update, billing.manage, seat.manage
manage_integrations -> integration.manage
view_all_accounts   -> (unchanged; a data-scope flag, not a CRUD permission)
```

`view_all_accounts` is deliberately *not* a CRUD permission. It widens which
records a user sees, and `scoping.py` already treats it that way. Keep that
distinction; collapsing it into the permission list would silently change record
visibility across every dashboard.

### 4.3 Seat and credit integrity

The brief's hard invariant is `availableSeats >= 0` under concurrency (§51, §71).
Three mechanisms together:

1. **The ledger is the truth.** `CreditLedger` is append-only, one row per
   movement, each carrying `balance_after`. Never `organisation.credits -= 1`.
2. **A single serialisation point.** Allocation takes
   `SELECT ... FOR UPDATE` on the organisation's `BillingAccount` row inside the
   transaction. Two concurrent approvals serialise on that lock; the second sees
   the first's ledger row and fails with `INSUFFICIENT_SEATS`.
3. **A database check constraint** asserting `balance_after >= 0` on the ledger,
   so even a logic bug cannot persist a negative balance.

Approval is one transaction: authorize, lock, check, allocate seat, activate
membership, approve request, write ledger row, write audit row. Notifications
are dispatched on `transaction.on_commit`, never inside it, which is the pattern
`services/webhooks/signals.py` already uses.

### 4.4 Payment webhooks

Reuse the `ConnectorInboundView` shape: raw-body HMAC-SHA256, constant-time
comparison, size cap, and an idempotency table. `PaymentWebhookEvent` is unique
on `(provider, event_id)`; a duplicate delivery is recorded and ignored rather
than granting credits twice. Credits are only ever granted from a verified
webhook, never from a client saying payment succeeded.

---

### 4.5 Self-serve workspaces, and the employee who gets there first

A verified corporate address whose domain nobody has claimed is offered the
workspace form rather than refused (`WORKSPACE_SETUP_REQUIRED`). The obvious
objection is an employee creating "Acme" before Acme's management knows. The
design makes that harmless rather than forbidding it:

1. **A self-made workspace holds only its founder.** It contains no company
   data and no seats beyond the founder's until billing is set up.
2. **The domain is attached as a claim, not ownership.** `OrganizationDomain`
   now allows several organisations to hold a `pending` claim on one domain
   and exactly one to hold it `verified` (partial unique constraint). Only a
   verified domain routes sign-ins.
3. **Proof supersedes claims.** `domains.verify` promoting one organisation's
   record revokes every other record for that domain, with a
   `domain.superseded` audit event each. DNS control is the only evidence, and
   only whoever controls the company's DNS can produce it, so the company
   always wins and an earlier claim never stands in its way.
4. **Colleagues are never auto-joined to a claim.** The second person from the
   domain is routed to the existing workspace as an *access request* the
   founder must approve explicitly — an administrator's decision, not domain
   trust — and never handed a second workspace. Two claims and no proof is
   `DOMAIN_NOT_VERIFIED`: nobody can be chosen.
5. **Authenticating is still not joining.** `login._require_standing` refuses
   a session to anyone without an active membership, so a pending person who
   signs in a second time waits again rather than opening an empty app, and is
   re-routed if the domain changed hands while they waited.

### 4.6 Invitations

The company asking someone in, as opposed to the person asking. An
`Invitation` names the address, role and department up front; it grants
nothing until a provider verifies exactly that address at sign-in, and then it
is accepted in the same transaction that writes the membership and the columns.
There is no token: acceptance is keyed on the verified address, so forwarding
the email hands nothing to anyone, and `INVITATION_EMAIL_MISMATCH` is true by
construction. An invitation overrides the domain rules (it is the only door
for a personal address), consumes no seat until accepted, expires after seven
days, and its inviter is held to the same rule as an approver: they cannot
grant a capability they do not hold.

### 4.7 Owners, and the platform surface

Every organisation has exactly one **owner** (`OrganizationMembership.is_owner`,
partial unique): the founder at signup or workspace creation, backfilled for
existing tenants as the earliest-joined admin. The owner is the billing
contact; other administrators cannot deactivate or demote them; ownership
moves only when the owner hands it over (`POST /auth/organisation/owner/`,
the new owner becomes Admin) or when platform staff do it.

**Platform staff** are the existing superusers: they belong to no tenant,
which is what makes them the right people to administer all of them. Because
that account is the one worth stealing, platform access needs a **second
factor**: TOTP (RFC 6238, implemented in `services/accounts/mfa.py` with no
new dependency), secret Fernet-encrypted at rest, one-use codes, hashed
single-use recovery codes. A password login for an enrolled person returns a
challenge, not a session; only `POST /auth/login/mfa/` mints tokens, and
those carry `mfa: true`. `IsPlatformStaff` requires superuser *and* that
claim, so a session that skipped the second factor can never reach
`/api/v1/platform/`. The portal itself (organisations, owners, status,
billing once it exists) sees tenant metadata only, never tenant data.

## 5. Licensing for a future downloadable build

This is the requirement most easily got wrong, so it is stated plainly.

**You cannot make a client-side check uncrackable.** Any value the client
evaluates locally, an attacker with the binary can patch. Obfuscation raises
cost; it does not change the outcome. Any design that promises otherwise is
selling a false guarantee.

What actually holds is an architecture where **the valuable work needs the
server**, and the licence is evidence rather than a switch.

### 5.1 What stays server-side, always

Revenact's value is already server-resident: the Copilot and every model call,
retrieval and embeddings, the metric layer and month-end snapshots, the health
rubric, multi-tenant customer data, mail and ticket sync. A cracked client with
no network is an empty shell, because none of that can be computed locally.

**Design rule: never move a revenue-bearing computation into the client to make
the desktop build feel faster.** That, not the licence check, is what protects
the product.

### 5.2 Entitlement tokens, not local flags

The client never stores `isPro = true`. It stores a short-lived entitlement
token the server signs:

```
{ org, plan, seats, features[], issued_at, expires_at, device_id, nonce }
signed with the server's PRIVATE key (Ed25519 or RS256)
```

The desktop build ships only the **public** key, so it can verify but never mint.
Tokens are short-lived (hours) and refreshed on every online session.

### 5.3 Offline grace, bounded and signed

Offline use is a real requirement, so grant it explicitly rather than leaving a
hole. The token carries its own `expires_at`; the client refuses to run
entitled features past it. A typical window is 7 to 14 days. There is no way to
extend it without a new server signature.

### 5.4 Clock tampering

Turning off the internet and winding the clock back is the obvious attack. Two
cheap mitigations:

- **Monotonic high-water mark.** Persist the highest server timestamp ever seen.
  If the system clock reads earlier than that, treat the device as tampered and
  require reconnection. Clock rollback then *shortens* offline life rather than
  extending it.
- **Server-side device binding.** Each `device_id` gets its own token. The server
  counts activations per seat, so cloning a machine image is visible centrally
  even when it is invisible locally.

### 5.5 Seats are enforced where they are counted

Seat consumption is a server-side ledger write at membership activation. A
cracked client cannot create a seat, because seats are not a client concept.
The worst a cracked client achieves is using features it already paid for while
offline, for the length of the grace window.

### 5.6 What this deliberately does not do

No kernel drivers, no anti-debugger, no hardware dongles, no remote-kill of paid
installs. Those add support burden and user hostility for protection that a
determined attacker defeats anyway. The honest posture is: make casual tampering
pointless, make the product useless without the server, and price and support the
product so honest customers never look for a crack.

---

## 6. Migration strategy

The dangerous step is 2.4, the membership refactor, because `user.organisation`
is read throughout. It is done in expand-migrate-contract so nothing breaks
mid-flight:

| Phase | Action | Reversible |
|---|---|---|
| 1 | Add `Membership`, `Identity`, `Department`, domains, and all billing models. Nothing reads them yet | Yes |
| 2 | Backfill: one membership per existing user from their current `organisation` and `role`; one department per distinct `function`; a `BillingAccount` per organisation with an opening ledger grant covering current headcount | Yes |
| 3 | Introduce `request.membership`, resolved once per request. Change `scoping.py`, `hierarchy.py` and `permissions.py` to read it. `user.organisation` becomes a property delegating to the active membership, so every existing call site keeps working | Yes |
| 4 | Add OAuth login beside password login, behind `AUTH_V2_ENABLED`. Both work | Yes |
| 5 | Add access requests, invitations, seats, billing | Additive |
| 6 | Drop `User.organisation` and `User.role` columns once nothing reads them | One-way |

**Password login is not deleted.** The brief says not to *build* custom password
auth; this codebase already has it, with Argon2id, a 12-character policy,
per-IP and per-account throttling, and `.soc2` control AUTH-04 citing it as
evidence. Removing it would break the seeded demo accounts, both end-to-end test
flows, and a compliance control. It is retained for platform-admin break-glass
and disabled for ordinary members once OAuth is live.

**No dummy credentials survive.** Seeded users get memberships and are marked as
requiring real authentication; their password hashes are cleared in the same
migration.

---

## 7. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Membership refactor silently widens record visibility | **High.** A mistake here is a cross-tenant data leak | Phase 3 keeps one resolution point; the existing cross-tenant e2e tests must pass unchanged at every phase |
| `view_all_accounts` folded into the permission list | High | Keep it a data-scope flag, as `scoping.py` already treats it |
| Seat oversubscription under concurrency | High | Row lock plus a database check constraint, not just application logic |
| Duplicate webhook granting credits twice | High | Unique `(provider, event_id)`, verified signature, credits only from webhooks |
| Domain verification trusted without proof | High | DNS TXT ownership before any automatic mapping; never trust a typed domain |
| Permissions moved into JWT claims for speed | Medium | Explicitly forbidden; today's per-request resolution is the feature |
| Notifications inside billing transactions | Medium | `transaction.on_commit`, as the webhook signals already do |
| Scope: this is roughly a quarter of work | Medium | Phased above; each phase ships and is reversible except the last |

---

## 8. Recommended build order

1. Membership, Identity, Department; backfill; `request.membership` (no behaviour change) — done, PR #35
2. OAuth login reusing the mail provider abstraction, behind a flag — done, PR #36
3. Domains and DNS verification — done, PR #38 (with 4a)
4. Access requests (4a, PR #38), self-serve workspaces (4b, §4.5, PR #39), invitations (4c, §4.6, PR #40) — done
5. Billing account, plans, credit ledger, seat allocation with the locking test first
6. Payment provider interface, one implementation, idempotent webhooks
7. Platform-admin surface — owners + staff MFA (7a, §4.7, PR #41); portal API (7b, PR #42); pages next
8. Frontend: login, pending state, approvals, billing, platform admin
9. Expanded capability vocabulary and the data migration
10. Drop the legacy columns

Two tests are written before the code they cover, because they are the ones that
matter: the cross-tenant 403 (§70) and the concurrent-approval seat test (§71).
