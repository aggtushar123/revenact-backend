# SOC 2 Gap Report — 2026-09-15

**System:** Revenact (revenact-backend, react-ts-app, revenact-infra)
**Scope:** Security, Confidentiality. Availability, Processing Integrity and Privacy deferred (product in development; organisational policies out of scope for now).
**Prepared by:** soc2-dev skill (heuristic scan + manual review of entry points, settings, auth, tenancy, webhooks, WebSockets, frontend token handling, Terraform/Compose).

## Readiness

Score counts **met** only. API-10 and API-11 are excluded (no uploads, no inbound webhooks).

| Category | Must in scope | Met | Partial | Missing | Score |
|----------|---------------|-----|---------|---------|-------|
| AUTH | 8 | 3 | 3 | 2 | 38% |
| API | 6 | 3 | 2 | 1 | 50% |
| DATA | 9 | 2 | 4 | 3 | 22% |
| LOG | 5 | 0 | 1 | 4 | 0% |
| SEC | 5 | 0 | 4 | 1 | 0% |
| CHG | 5 | 0 | 2 | 3 | 0% |
| INFRA | 4 | 0 | 4 | 0 | 0% |
| EVD | 2 | 1 | 1 | 0 | 50% |
| **Total** | **44** | **9** | **21** | **14** | **20%** |

Under 75%: do not schedule an audit window. The picture is better than the number: the
hard architectural controls (deny-by-default auth, capability RBAC, tenant scoping,
parameterised queries, signed outbound webhooks with SSRF guard, JWT-authenticated
WebSockets, secrets kept out of git) are in place. Most partials are settings-level
changes; the real gaps are brute-force protection, audit logging, backups, and CI gates.

## Status after remediation (same day)

Everything below is in the working trees, uncommitted. Score counts **met** only;
"addressed" also counts the three documented exceptions.

| Category | Must in scope | Met | Excepted | Partial | Missing | Score (met) |
|----------|---------------|-----|----------|---------|---------|-------------|
| AUTH | 8 | 6 | 1 (AUTH-03) | 1 (AUTH-05) | 0 | 75% |
| API | 6 | 6 | 0 | 0 | 0 | 100% |
| DATA | 9 | 4 | 1 (DATA-03) | 3 (02, 05, 06) | 1 (04) | 44% |
| LOG | 5 | 3 | 0 | 1 (04) | 1 (05) | 60% |
| SEC | 5 | 5 | 0 | 0 | 0 | 100% |
| CHG | 5 | 1 | 1 (CHG-01) | 2 (02, 06) | 1 (05) | 20% |
| INFRA | 4 | 3 | 0 | 1 (03) | 0 | 75% |
| EVD | 2 | 2 | 0 | 0 | 0 | 100% |
| **Total** | **44** | **30** | **3** | **8** | **3** | **68% met / 75% addressed** |

Landed (finding numbers from the table below): #1 throttling, #2 audit log, #4 backups,
#5 secure defaults, #6 SSH allow-list, #7 recorded as EX-002 (rulesets confirmed
unavailable: API 403), #8 CI gates + pre-commit + Dependabot, #9 classification
(`docs/data-classification.md`; retention still open), #10 JSON logging (shipping and
alerts still open), #11 Argon2 + 12-char policy, #12 refresh rotation + cookie flags,
#13 HSTS/CSP/headers, #17 non-root image, #18 deactivation audited, #19 request ids,
#20 deploy log (tags still open), #21 PR template/CODEOWNERS/SECURITY.md,
#22 unattended-upgrades, #23 `export_access_review`. #3 MFA recorded as EX-001.

Not yet live: the infra changes need `make apply` (NSG, VM identity, storage account;
plan shows in-place updates and additions only) then `make sync` (Caddyfile, compose,
backup job onto the existing VM), and the backend image needs a redeploy for the
non-root user. The backup restore has not been tested yet.

Still open, in priority order: DATA-04 retention purges (M), LOG-04 log shipping to
Azure Monitor + LOG-05 alerts on auth failures / backup failures (S), DATA-02 webhook
secret at rest (S), #14 refresh token out of `localStorage` (M), #16 Bedrock key
rotation (manual), CHG-05 emergency-change process and SEC-02 rotation schedule
(organisational, deferred), release tags (#20), CHG-01 upgrade the GitHub plan.

## Findings

| # | Priority | Requirement | Status | Where | Gap | Fix | Effort |
|---|----------|-------------|--------|-------|-----|-----|--------|
| 1 | critical | AUTH-06 / API-04 | missing | `services/accounts/views.py` Login/Signup/ForgotPassword/ResetPassword, `TokenRefreshView` | No rate limit or lockout on any public auth endpoint | Add DRF `AnonRateThrottle`/`ScopedRateThrottle` (e.g. `login: 10/min`, `password_reset: 5/hour`) via `throttle_classes` + `DEFAULT_THROTTLE_RATES`; needs a cache backend (Redis is already deployed) | S |
| 2 | critical | LOG-01 / LOG-02 | missing | whole backend | No audit trail for login success/failure, logout, password change/reset, role changes, user create/deactivate, org settings, webhook/connector changes | Add `core/audit.py` (append-only `AuditEvent` model: actor, action, target, ts UTC, ip, outcome, request id) and emit from accounts views/serializers, `OrgUserDetailView`, `RoleDetailView`, `OrganisationSettingsView`, webhooks views. Reuse the pattern of `copilot.SessionEvent` | M |
| 3 | critical | AUTH-03 | missing | accounts | No MFA | Add TOTP (e.g. `django-otp`) required for users holding MANAGE_USERS / MANAGE_ORG_SETTINGS; optional for others. Can be deferred with an EXCEPTIONS row while in development | M |
| 4 | high | DATA-07 | missing | `revenact-infra` | No database backups; single Docker volume on one VM | Nightly `pg_dump` from a cron container to Azure Blob (versioned, private); document restore; test once | S |
| 5 | high | SEC-06 | partial | `config/settings.py:16,26` | `DEBUG` defaults True; `SECRET_KEY` silently falls back to `django-insecure-…` | Default `DEBUG=False`; raise `ImproperlyConfigured` when `SECRET_KEY` is unset or starts with `django-insecure` and `DEBUG` is False | XS |
| 6 | high | INFRA-02 | partial | `revenact-infra/terraform/main.tf:67-71` | SSH open to `0.0.0.0/0` | Restrict `source_address_prefix` to your IP (a variable), or use Azure Bastion / Just-In-Time access; disable password auth in cloud-init | XS |
| 7 | high | CHG-01 / CHG-02 | missing / partial | GitHub | Private repos on GitHub Free cannot have branch protection, so CI is advisory | Either make repos public, upgrade to Pro/Team, or use GitHub rulesets (available on Free for private repos) to require PR + status checks | XS |
| 8 | high | SEC-04 / SEC-03 / SEC-01 | missing / partial | `.github/workflows/ci.yml` (both repos) | No SAST, SCA, or secret scanning | Run `soc2_init.py` in each repo to add `soc2-gates.yml` (gitleaks, semgrep, pip-audit / npm audit) and `.pre-commit-config.yaml`; enable Dependabot | S |
| 9 | high | DATA-01 / DATA-04 | missing | models | No data classification, no retention | Add a `docs/data-classification.md` table per model (PII flags), then retention jobs for `Email`/`Interaction`/`SessionEvent`/webhook deliveries via the existing `run_health_maintenance`-style cron entry point | M |
| 10 | high | LOG-04 / LOG-05 | missing | `config/settings.py` | No `LOGGING` config, no alerts | Add JSON `LOGGING` to stdout; ship container logs to Azure Monitor (Log Analytics agent on the VM); alert on repeated auth failures once LOG-01 exists | S |
| 11 | medium | AUTH-04 | partial | `config/settings.py:AUTH_PASSWORD_VALIDATORS` | PBKDF2 hasher, min length 8, no breach check | Add `argon2-cffi`, set `PASSWORD_HASHERS` with Argon2 first; `MinimumLengthValidator` `min_length: 12`; optional HIBP k-anonymity check on signup/reset | XS |
| 12 | medium | AUTH-05 | partial | `config/settings.py:SIMPLE_JWT`, admin cookies | No refresh rotation, no idle timeout, cookie flags unset | `ROTATE_REFRESH_TOKENS=True`, `BLACKLIST_AFTER_ROTATION=True`; when not DEBUG set `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SESSION_COOKIE_HTTPONLY`, `SESSION_COOKIE_AGE` 30 min | XS |
| 13 | medium | API-06 / DATA-03 | partial | `config/settings.py`, `revenact-infra/deploy/Caddyfile` | No HSTS, CSP | `SECURE_HSTS_SECONDS=31536000` + preload/subdomains when not DEBUG; Caddy `header` block with CSP, `X-Content-Type-Options`, `Referrer-Policy` | XS |
| 14 | medium | DATA-06 / AUTH-05 | partial | `react-ts-app/src/features/auth/authSlice.ts:110-139` | JWTs and user profile in `localStorage` (XSS-readable) | Keep access token in memory only; move refresh token to an httpOnly, Secure, SameSite cookie set by the backend; store only non-sensitive UI profile fields | M |
| 15 | medium | DATA-02 | partial | `services/webhooks/models.py:35` | Webhook signing secret plaintext in DB | Encrypt with a key from env (`cryptography` Fernet / AES-GCM); or store only a hash and show once on create | S |
| 16 | medium | INFRA-03 | partial | `revenact-infra/terraform/main.tf:18-19` | Long-lived AWS keys for Bedrock in `backend.env` | Scope the IAM user to `bedrock:InvokeModel` on the one model ARN; rotate on a schedule; or use Anthropic API key only and drop AWS | S |
| 17 | medium | SEC-05 | partial | `revenact-backend/Dockerfile` | Runs as root | `RUN useradd -r app && chown -R app /app` + `USER app` before `CMD` | XS |
| 18 | medium | AUTH-07 | partial | `OrgUserDetailView` | Deactivation not audited | Emit audit event (depends on #2) | XS |
| 19 | medium | API-05 | partial | middleware | No correlation ID in errors/logs | Add a request-id middleware that sets `X-Request-ID` and includes it in `LOGGING` format and DRF error responses | S |
| 20 | medium | CHG-06 | partial | `revenact-infra/deploy/deploy.sh` | No tags, no deploy log | Tag releases (`vX.Y.Z`), pin `git_ref` to tags, append who/what/when to a deploy log on the VM | XS |
| 21 | low | CHG-03 / CHG-04 / SEC-03 (SECURITY.md) | missing | repo root | No PR template, CODEOWNERS, SECURITY.md | `soc2_init.py` writes all three | XS |
| 22 | low | INFRA-04 / INFRA-05 | partial | Azure VM | Platform-managed disk keys; no explicit patching | Enable `unattended-upgrades` in cloud-init; CMK only if a customer asks | XS |
| 23 | low | AUTH-10 | partial | accounts | No user/role export | `manage.py export_access_review` (email, role, capabilities, last_login) as CSV | XS |

## Scanner output

`.soc2/reports/scan-2026-09-15.md` (backend), `react-ts-app/.soc2/reports/scan-2026-09-15.md`,
`revenact-infra/.soc2/reports/scan-2026-09-15.md`.

Scanner triage:
- The 6 backend "critical" SEC-01 hits are the dev-only Postgres URL `revenact:revenact@localhost` in `docker-compose.yml`, `ci.yml`, the `settings.py` default, and the local `.env`. Not production credentials. Suppress with `# soc2:ignore dev-only local postgres` or move the settings default to `.env.example` only.
- `.env:15` is a real-looking AWS access key. `.env` is git-ignored and has never been committed in any branch (`git log --all -- .env` is empty). If this key was ever pasted anywhere else, rotate it.
- "No password hashing library detected" is a false positive: Django's PBKDF2 is in use. Upgrade to Argon2 anyway (#11).
- Test-fixture passwords (`supersecret1` etc.) are excluded by `scan_ignore` in `.soc2/config.yml`.

## Control map drift

First version of `CONTROL_MAP.md` written today; no drift. No `SOC2:<ID>` annotations exist yet (EVD-02); add them as each fix above lands.

## Also noticed

- `react-ts-app/.github/workflows/ci.yml` still has a Vercel deploy job and uses `actions/checkout@v3`; the live deployment is the Azure VM.
- `USE_X_FORWARDED_HOST = True` is fine behind Caddy but trusts any client-supplied header in dev.
- The Copilot sends account emails/tickets/notes to Anthropic or Bedrock (`services/copilot/retrieval.py`). Not a code finding, but a vendor/DPA item for the Third-Party Management policy when that becomes relevant.
- `.env.example` in the backend documents Gmail app-password SMTP; fine for dev.

## Next steps

1. Land the XS/S items in one "security defaults" PR: #5, #6, #11, #12, #13, #17, throttling (#1). Roughly a day of work, moves 8 requirements to met.
2. Build the audit log primitive (#2) and wire it into accounts + settings views; annotate with `# SOC2:LOG-01`.
3. Run `python3 .claude/skills/soc2-dev/scripts/soc2_init.py . --company Revenact --owner @aggtushar123` in backend and frontend for CI gates, PR template, CODEOWNERS, SECURITY.md; add GitHub rulesets.
4. Backups and SSH lock-down in `revenact-infra` (#4, #6).
5. Decide on MFA timing; if deferred, record an EXCEPTIONS row with an expiry.
