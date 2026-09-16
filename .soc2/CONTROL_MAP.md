# SOC 2 Control Map — revenact-backend

Maps engineering requirements (soc2-dev skill, `references/requirements.md`) to where they
are implemented in this repository and the evidence an auditor can inspect. Update this
file in the same pull request as the code it describes. INFRA rows point into
`revenact-infra`; browser-side rows live in `react-ts-app/.soc2/CONTROL_MAP.md`.

Scope: Security + Confidentiality (`.soc2/config.yml`). Availability, Processing Integrity
and Privacy are out of scope while the product is in development, so their only-mapped
requirements are recommendations (`n/a`).

Status: **met** | **partial** | **missing** | **excepted** (see `EXCEPTIONS.md`) | **n/a**

Enforcement points are annotated `# SOC2:<ID>` in code; `python3 .claude/skills/soc2-dev/scripts/control_map.py .`
reconciles them with this file (also run in CI, `soc2-gates.yml`).

| Requirement | Status | Implementation (file:symbol) | Evidence | Owner | Last verified |
|-------------|--------|------------------------------|----------|-------|---------------|
| AUTH-01 | met | `config/settings.py:REST_FRAMEWORK.DEFAULT_PERMISSION_CLASSES` = IsAuthenticated, JWTAuthentication default. AllowAny only on the six `public_routes` in `config.yml` (`services/accounts/views.py` Signup/Login/Refresh/ForgotPassword/ResetPassword, `core/views.py:health_check`). WebSockets: `core/ws_auth.py` | `services/accounts/tests/test_views.py` 401 cases | @aggtushar123 | 2026-09-15 |
| AUTH-02 | met | Capability permissions `services/accounts/permissions.py:HasCapability` and subclasses; tenant/record scoping `services/customers/scoping.py`; `services/copilot/views.py:_same_org_member` | 403/404 tests under `services/*/tests/` | @aggtushar123 | 2026-09-15 |
| AUTH-03 | excepted | EX-001: no MFA while in development; compensating controls listed there | `EXCEPTIONS.md` | @aggtushar123 | 2026-09-15 |
| AUTH-04 | met | `config/settings.py:AUTH_PASSWORD_VALIDATORS` (min 12, common list, similarity, numeric) and `PASSWORD_HASHERS` Argon2 first; `services/accounts/serializers.py:_check_password_strength` on signup, admin-set, self-service change and reset | `services/accounts/tests/test_password_policy.py` | @aggtushar123 | 2026-09-15 |
| AUTH-05 | partial | `config/settings.py:SIMPLE_JWT` access 60 min, refresh 7 d, `ROTATE_REFRESH_TOKENS` + `BLACKLIST_AFTER_ROTATION` (single-use refresh); `LogoutView` and `OrgUserDetailView` blacklist; admin session cookies Secure/HttpOnly/SameSite, 30 min idle (`SESSION_COOKIE_AGE`, `SESSION_SAVE_EVERY_REQUEST`). Gap: SPA keeps tokens in `localStorage` (gap report #14) | `core/tests/test_audit.py`, `react-ts-app` `authSlice.test.ts` refresh cases | @aggtushar123 | 2026-09-15 |
| AUTH-06 | met | `core/throttling.py` (per-IP + per-account login, signup, password reset, token refresh) applied via `throttle_classes` in `services/accounts/views.py`; rates in `config/settings.py:AUTH_THROTTLE_RATES` (env-tunable), counters in Redis; `NUM_PROXIES` for the real client IP behind Caddy | `core/tests/test_throttling.py` (429 + Retry-After) | @aggtushar123 | 2026-09-15 |
| AUTH-07 | met | `OrgUserDetailView.perform_update`: deactivation is immediate (`is_active` checked per request), revokes every outstanding refresh token, and records `user.deactivate` / `user.reactivate` in the audit log; provisioning records `user.create` | `core/tests/test_audit.py:test_deactivating_a_user_is_recorded…` | @aggtushar123 | 2026-09-15 |
| AUTH-08 | n/a | No service-to-service calls (single backend) | | | 2026-09-15 |
| AUTH-09 | met | Org-defined roles with capability sets `services/accounts/models.py:Role`, `services/accounts/capabilities.py`; no code path uses superuser; no-lockout rule in `EditOrgUserSerializer`; role changes audited (`role.create/update/delete`) | accounts tests | @aggtushar123 | 2026-09-15 |
| AUTH-10 | met | `services/accounts/management/commands/export_access_review.py` — CSV of every user with role, capabilities, active flag, last login (`UPDATE_LAST_LOGIN=True`) | `python manage.py export_access_review`; reviewer sign-off is the evidence | @aggtushar123 | 2026-09-15 |
| API-01 | met | DRF serializers on every view; unknown fields ignored | `serializers.py` per app | @aggtushar123 | 2026-09-15 |
| API-02 | met | JSON-only renderer in prod (`DEFAULT_RENDERER_CLASSES`); SPA renders via React; CSP `script-src 'self'` at the edge (`revenact-infra/deploy/Caddyfile`) | settings.py, Caddyfile | @aggtushar123 | 2026-09-15 |
| API-03 | met | ORM only; no `.raw()`, `.extra()`, `RawSQL` or cursor use outside tests | `grep -rn "\.raw(\|RawSQL" services core` | @aggtushar123 | 2026-09-15 |
| API-04 | met | Auth-endpoint throttling (AUTH-06); `DATA_UPLOAD_MAX_MEMORY_SIZE` 2.5 MB (`config/settings.py`) | `core/tests/test_throttling.py` | @aggtushar123 | 2026-09-15 |
| API-05 | met | `core/middleware.py:RequestIDMiddleware` — `X-Request-ID` echoed, on every log line (`core/logging.py`) and audit row; `DEBUG` defaults False so DRF returns generic error bodies | `core/tests/test_observability.py:RequestIDTests` | @aggtushar123 | 2026-09-15 |
| API-06 | met | `config/settings.py`: HSTS 1 y + subdomains, `SECURE_CONTENT_TYPE_NOSNIFF`, referrer policy, `X_FRAME_OPTIONS=DENY`, Secure/HttpOnly/SameSite cookies when not DEBUG; CORS allow-list; `CsrfViewMiddleware` + `CSRF_TRUSTED_ORIGINS`; edge headers + CSP in `revenact-infra/deploy/Caddyfile` (verified live 2026-09-16) | `curl -I https://<host>/api/v1/health/` | @aggtushar123 | 2026-09-15 |
| API-07 | partial | drf-spectacular schema at `/api/schema/`; no per-route classification/owner manifest | | | 2026-09-15 |
| API-08 | partial | ORM transactions where used; no idempotency keys (Processing Integrity out of scope) | | | 2026-09-15 |
| API-09 | met | All routes under `/api/v1/` (`config/urls.py`) | | @aggtushar123 | 2026-09-15 |
| API-10 | n/a | No file uploads | | | 2026-09-15 |
| API-11 | n/a | No inbound webhooks; outbound only (`services/webhooks/engine.py`) | | | 2026-09-15 |
| DATA-01 | met | `docs/data-classification.md` — every model, its class, PII columns and the flows that leave the tenant | that file | @aggtushar123 | 2026-09-15 |
| DATA-02 | partial | Postgres volume on an Azure managed disk and the backup storage account: encrypted at rest with platform-managed keys. Passwords Argon2id. Gap: `services/webhooks/models.py:WebhookSubscription.secret` plaintext in its column (gap report #15) | | | 2026-09-15 |
| DATA-03 | met / EX-003 | Public edge TLS via Caddy/Let's Encrypt with HSTS (`revenact-infra/deploy/Caddyfile`); outbound webhooks https-only, no redirects (`services/webhooks/engine.py`); backup upload https-only, TLS 1.2+. Internal container hops on one host: EX-003 | Caddyfile, `revenact-infra/terraform/main.tf:azurerm_storage_account.backups` | @aggtushar123 | 2026-09-15 |
| DATA-04 | missing | No retention job for application records (emails, tickets, notes, Copilot sessions, webhook deliveries, audit events). Periods are set in `config.yml`; add purges to `run_health_maintenance` (gap report #9) | — | | 2026-09-15 |
| DATA-05 | partial | FK cascades in models; no erasure flow (Privacy out of scope) | | | 2026-09-15 |
| DATA-06 | partial | No PII in URLs or logs (`core/logging.py:RedactFilter`). SPA stores JWTs + profile in `localStorage` (gap report #14) | | | 2026-09-15 |
| DATA-07 | met | `revenact-infra/deploy/backup.sh` + `revenact-backup.timer` (daily 13:00 IST, catches up after shutdown): `pg_dump -Fc` to the private storage account `revenact-infra/terraform/main.tf:azurerm_storage_account.backups` via the VM's managed identity; versioning + soft delete, 35-day expiry (`backup_retention_days`); restore procedure in `revenact-infra/README.md` "Backups". Live since 2026-09-16; first run uploaded `revenact-20260916T055158Z.dump` | `make backups`, `/opt/revenact/backup.log`; **restore test 2026-09-16**: `make restore-test` pulled `revenact-20260916T055158Z.dump` from blob storage, verified its sha256, restored into a scratch database and matched live on 64 tables and row counts (orgs 1, users 6, customers 11, emails 39, audit events 5). Repeat quarterly; next due 2026-12-16 | @aggtushar123 | 2026-09-15 |
| DATA-08 | met | Every model FK to `Organisation`; querysets scoped via `services/customers/scoping.py`; `_same_org_member` for user refs; WebSocket `services/copilot/consumers.py:SessionConsumer.connect` checks visibility | tests per app | @aggtushar123 | 2026-09-15 |
| DATA-09 | n/a | Privacy out of scope | | | 2026-09-15 |
| DATA-10 | met | Non-prod data is synthetic (`seed_demo`, `seed_demo_functions` commands) | | @aggtushar123 | 2026-09-15 |
| DATA-11 | n/a | Processing Integrity out of scope | | | 2026-09-15 |
| DATA-12 | n/a | Privacy out of scope | | | 2026-09-15 |
| LOG-01 | met | `core/audit.py:record` → `core.AuditEvent`; emitted from `services/accounts/views.py` (signup, login, logout, password change/reset, org settings, roles, users), `core/signals.py` (failed logins, admin session logins), `services/webhooks/views.py`; catalogue in `docs/audit-events.md` | `core/tests/test_audit.py`; Django admin "Audit events" (read-only) | @aggtushar123 | 2026-09-15 |
| LOG-02 | met | `core/models.py:AuditEvent` — own table, actor/actor_email, action, target, outcome, ip, user agent, request id, UTC timestamp; `save()`/`delete()` refuse changes; admin has no add/change/delete; second copy on the `core.audit` logger | `core/tests/test_audit.py:AuditEventModelTests` | @aggtushar123 | 2026-09-15 |
| LOG-03 | met | `core/logging.py:RedactFilter` on every handler (bearer tokens, JWTs, `password=`/`token=`-style pairs); `core/audit.py` drops credential keys from metadata; no request bodies/headers logged | `core/tests/test_observability.py:RedactionTests` | @aggtushar123 | 2026-09-15 |
| LOG-04 | partial | `config/settings.py:LOGGING` — JSON lines (UTC ts, level, logger, request id, audit payload) on stdout in prod. Gap: container logs stay on the VM; not shipped to Azure Monitor / Log Analytics (gap report #10) | `docker compose logs backend` | | 2026-09-15 |
| LOG-05 | missing | No alerting on auth failures, 5xx or backup failures | — | | 2026-09-15 |
| LOG-06 | partial | `GET /api/v1/health/` + Docker `HEALTHCHECK`; no uptime monitor (Availability out of scope) | | | 2026-09-15 |
| LOG-07 | met | `USE_TZ`, UTC timestamps in models and in `core/logging.py:JSONFormatter` | | @aggtushar123 | 2026-09-15 |
| SEC-01 | met | Secrets only via environment (`django-environ`); `.env` git-ignored and never committed; prod secrets via Terraform `sensitive` vars → cloud-init → `/opt/revenact/backend.env` (0600). Scanning: gitleaks in CI (`.github/workflows/soc2-gates.yml: secret-scan`, full history) and pre-commit (`.pre-commit-config.yaml`); `.gitleaks.toml` allowlists the dev-only compose DB URL | Actions run "secret-scan"; `git log --all -- .env` empty | @aggtushar123 | 2026-09-15 |
| SEC-02 | partial | All secrets rotatable via env without a code change; no written rotation schedule (organisational, deferred) | | | 2026-09-15 |
| SEC-03 | met | `requirements.txt` pinned; `pip-audit --strict` in CI (`soc2-gates.yml: dependency-scan`); Dependabot weekly for pip + actions (`.github/dependabot.yml`); patch SLAs in `SECURITY.md` | Actions run "dependency-scan"; Dependabot PRs | @aggtushar123 | 2026-09-15 |
| SEC-04 | met | semgrep `p/security-audit`, `p/secrets`, `p/owasp-top-ten`, `p/django` in CI (`soc2-gates.yml: static-analysis`); ruff in `ci.yml` | Actions run "static-analysis" | @aggtushar123 | 2026-09-15 |
| SEC-05 | met | `Dockerfile`: `python:3.13-slim`, `USER app` (uid 10001), `HF_HOME` under `/app`; `docker compose build --pull` on every deploy refreshes the base image; trivy vulnerability scan (blocking on CRITICAL/HIGH) + Dockerfile misconfiguration scan (report) in CI | Actions run "container-scan" | @aggtushar123 | 2026-09-15 |
| SEC-06 | met | `config/settings.py`: `DEBUG` defaults False; `SECRET_KEY` required when DEBUG is off and the `django-insecure` placeholder is refused; browsable API only when DEBUG | settings.py:26-40 | @aggtushar123 | 2026-09-15 |
| SEC-07 | met | Argon2id password hashing; HMAC-SHA256 webhook signatures with `secrets.token_urlsafe(32)` keys; Django `default_token_generator` for resets; SimpleJWT HS256 with the Django secret | `services/webhooks/engine.py`, settings.py | @aggtushar123 | 2026-09-15 |
| CHG-01 | excepted | EX-002: GitHub Free private repo, branch protection/rulesets unavailable | `EXCEPTIONS.md` | @aggtushar123 | 2026-09-15 |
| CHG-02 | partial | `ci.yml` (ruff, migrate, tests) + `soc2-gates.yml` (5 security jobs) on every PR and push to main; cannot be made required (EX-002) | Actions tab | @aggtushar123 | 2026-09-15 |
| CHG-03 | met | `.github/pull_request_template.md` with SOC 2 impact + controls checklist | | @aggtushar123 | 2026-09-15 |
| CHG-04 | met | `.github/CODEOWNERS` for auth, core, config, webhooks, models/migrations, CI, Dockerfile, `.soc2/` | | @aggtushar123 | 2026-09-15 |
| CHG-05 | partial | PR template carries an "Emergency change" tick-box with the post-hoc review rule; no written process (organisational, deferred) | | | 2026-09-15 |
| CHG-06 | partial | `revenact-infra/deploy/deploy.sh` appends who/when/ref/commit SHAs to `/opt/revenact/deploy.log` (`make deploy-log`). Gap: deploys track `main`, no release tags | deploy.log on the VM | @aggtushar123 | 2026-09-15 |
| CHG-07 | met | Deploys run from GitHub Actions (`ci.yml: deploy` in both app repos) after tests and soc2-gates pass on `main`, as the Entra application `revenact-github-deployer` via OpenID Connect (no stored secret; federated to `main` of the two repos only; `Virtual Machine Contributor` on the one VM — `revenact-infra/terraform/deploy-identity.tf`), through `az vm run-command`; every run appended to `/opt/revenact/deploy.log` with actor, repo and commit. `make deploy`/`make sync` remain for operators | | | 2026-09-15 |
| CHG-08 | missing | No signed commits/provenance (may) | | | 2026-09-15 |
| CHG-09 | partial | Runtime config is env-only, changed via Terraform vars (reviewed as code when committed) | | | 2026-09-15 |
| INFRA-01 | met | Terraform + Compose in `revenact-infra`; `terraform plan` before every change; no automated drift detection | `revenact-infra/terraform/main.tf` | @aggtushar123 | 2026-09-15 |
| INFRA-02 | met | `revenact-infra/terraform/main.tf:azurerm_network_security_group.main` — 22 only from `var.ssh_allowed_cidrs` (validated non-empty, never `0.0.0.0/0`), 80/443 public; compose publishes only Caddy; password SSH disabled. Live since 2026-09-16 | `terraform plan`; `az network nsg rule list -g revenact-rg --nsg-name revenact-nsg -o table` | @aggtushar123 | 2026-09-15 |
| INFRA-03 | partial | VM user-assigned identity (`revenact-vm-identity`) with `Storage Blob Data Contributor` on the backup account only; no storage key is held anywhere on the VM (shared keys remain enabled on the account because the Terraform provider manages it through them). Gap: static AWS access key for Bedrock in `/opt/revenact/backend.env`; least-privilege policy provided in `revenact-infra/aws/bedrock-invoke-policy.json`, rotation manual (gap report #16) | `revenact-infra/terraform/main.tf:azurerm_role_assignment.vm_writes_backups` | @aggtushar123 | 2026-09-15 |
| INFRA-04 | met | OS disk and backup storage account encrypted at rest with Azure platform-managed keys (SSE); TLS 1.2 minimum on storage. Customer-managed keys not required by config | Azure portal → disk / storage account encryption | @aggtushar123 | 2026-09-15 |
| INFRA-05 | met | `revenact-infra/terraform/cloud-init.yaml.tftpl`: `package_upgrade` at first boot, `unattended-upgrades` with `20auto-upgrades` (daily security updates); `revenact-infra/deploy/deploy.sh`: `docker compose build --pull` rebuilds every image on its current base each deploy; trivy in CI | `/var/log/unattended-upgrades/` on the VM | @aggtushar123 | 2026-09-15 |
| INFRA-06 | n/a | Availability out of scope (single VM by design) | | | 2026-09-15 |
| INFRA-07 | missing | No WAF/DDoS in front of the VM (should) | | | 2026-09-15 |
| EVD-01 | met | This file; `.claude/skills/soc2-dev/scripts/control_map.py` drift check + scanner in `soc2-gates.yml` (artifact `soc2-scan`) | Actions run "soc2-heuristic-scan" | @aggtushar123 | 2026-09-15 |
| EVD-02 | met | `# SOC2:<ID>` annotations at enforcement points (`grep -rn "SOC2:" core services config Dockerfile`) | `.claude/skills/soc2-dev/scripts/control_map.py --index` | @aggtushar123 | 2026-09-15 |
| EVD-03 | met | `.soc2/EXCEPTIONS.md` (EX-001..003) | that file | @aggtushar123 | 2026-09-15 |
