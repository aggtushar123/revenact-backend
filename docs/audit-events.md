# Audit event catalogue

`core.audit.record(action, ...)` writes one `core.AuditEvent` row (append-only, its own
table) and one `core.audit` log line per security-relevant event (SOC2:LOG-01, LOG-02).
Every row carries actor, organisation, action, target, outcome, source IP, user agent,
request id and a UTC timestamp. `metadata` holds small structured context and never a
credential (`record` drops password/token/secret keys as a safety net).

Query it in the Django admin (read-only) or directly:

```python
AuditEvent.objects.filter(organisation=org, action="auth.login", outcome="failure")
```

| Action | Emitted from | Actor | Target | Metadata |
|--------|--------------|-------|--------|----------|
| `auth.signup` | `SignupView` | new admin | Organisation | organisation name |
| `auth.login` (success) | `LoginView` | user | — | — |
| `auth.login` (success, `via: session`) | `core.signals` (`user_logged_in`) | user | — | Django admin session logins |
| `auth.login` (failure) | `core.signals` (`user_login_failed`) | — | — | `email` attempted |
| `auth.logout` | `LogoutView` | user | — | — |
| `auth.password_change` | `ChangePasswordView` | user | User (self) | — |
| `auth.password_reset_request` | `ForgotPasswordView` | — | — | `email` (recorded whether or not it exists) |
| `auth.password_reset` | `ResetPasswordView` | user | User (self) | — |
| `organisation.update` | `OrganisationSettingsView` | user | Organisation | changed field names |
| `role.create` / `role.update` / `role.delete` | `RoleListCreateView`, `RoleDetailView` | user | Role | permissions, changed fields |
| `user.create` | `OrgUserListCreateView` | admin | User | role slug |
| `user.update` | `OrgUserDetailView` | admin | User | changed field names (never the password) |
| `user.deactivate` / `user.reactivate` | `OrgUserDetailView` | admin | User | — |
| `mailbox.connect` / `mailbox.disconnect` | `services.mail.views` | user | MailboxConnection | provider, address |
| `webhook.create` / `webhook.update` / `webhook.delete` | `services.webhooks.views` | user | WebhookSubscription | url, event, changed fields |

Adding a new one: call `audit.record` at the point the change is committed, annotate the
line `# SOC2:LOG-01`, and add a row here.

Export for an access review (SOC2:AUTH-10): `python manage.py export_access_review > access-review.csv`.
