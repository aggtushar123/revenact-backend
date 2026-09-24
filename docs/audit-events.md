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
| `auth.signup` (`via: oauth`) | `services.identity.onboarding.create_workspace` | founder | Organisation | organisation name, provider; a self-serve workspace from an unclaimed domain |
| `auth.login` (success) | `LoginView` | user | — | — |
| `auth.login` (success, `via: session`) | `core.signals` (`user_logged_in`) | user | — | Django admin session logins |
| `auth.login` (failure) | `core.signals` (`user_login_failed`) | — | — | `email` attempted |
| `auth.login` (success, `via: oauth`) | `services.identity.views.CallbackView` | user | — | provider |
| `auth.login` (failure, `via: oauth`) | `services.identity.views.CallbackView` | — | — | provider, refusal `reason` code |
| `identity.linked` | `services.identity.login.resolve_user` | user | User (self) | provider; first sign-in with that provider |
| `access_request.created` | `services.identity.onboarding.request_access` | the person | User (self) | their verified email |
| `access_request.approved` | `services.identity.onboarding.approve` | reviewing admin | User | role slug, department |
| `access_request.rejected` | `services.identity.onboarding.reject` | reviewing admin | User | reason |
| `domain.added` | `services.identity.admin_views` | user | OrganizationDomain | domain |
| `domain.verified` | `services.identity.admin_views` | user | OrganizationDomain | domain |
| `domain.superseded` | `services.identity.domains.verify` | verifying user | OrganizationDomain (the loser's) | domain, `verified_by` organisation id; another organisation proved the domain |
| `invitation.created` / `invitation.resent` | `services.identity.onboarding.invite` | inviting admin | Invitation | email, role slug |
| `invitation.accepted` | `services.identity.onboarding.accept_invitation` | the person | User (self) | email, role slug; accepted at sign-in |
| `invitation.cancelled` | `services.identity.onboarding.cancel_invitation` | admin | Invitation | email |
| `mfa.enrolled` / `mfa.disabled` | `MfaConfirmView`, `MfaDisableView` | user | User (self) | — |
| `auth.login` (`stage: password`) | `LoginView` | user | — | password accepted, second factor still owed; no session issued |
| `auth.login` (`mfa: totp|recovery`) | `MfaLoginView` | user | — | the second factor that completed the sign-in |
| `organisation.owner_transferred` | `services.identity.ownership.transfer` | previous owner, or platform staff | User (new owner) | from, to, `by_platform` |
| `platform.organisation.status` | `services.platform.views` | platform staff | Organisation | from, to, reason; suspension refuses sign-in and voids capabilities |
| `platform.organisation.created` | `services.platform.views` | platform staff | Organisation | name, owner email; the owner is created as root user in the same transaction |
| `platform.organisation.updated` | `services.platform.views` | platform staff | Organisation | changed field names, from, to |
| `billing.credits.adjusted` | `services.billing.ledger.adjust` | platform staff | BillingAccount | signed amount, balance after, reason |
| `billing.seats.changed` | `services.billing.accounts.set_seats` | platform staff | BillingAccount | from, to, reason; never below seats in use |
| `billing.plan.changed` | `services.billing.accounts.change_plan` | platform staff (or the payment webhook, next phase) | BillingAccount | from, to, seat allowance, reason |
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
| `mailbox.reply` | `services.mail.views.MailReplyView`, `services.customers.communications_views.EmailReplyView` | user | MailMessage or Email (the one answered) | to, subject; mail left through a credential we hold |
| `attribute.define` | `services.attributes.views.AIAttributeListCreateView` | admin | AIAttribute | name, value_type |
| `attribute.update` | `services.attributes.views.AIAttributeDetailView` | admin | AIAttribute | name, fields changed |
| `attribute.delete` | `services.attributes.views.AIAttributeDetailView` | admin | AIAttribute | name, `values`: how many answers went with it |
| `attribute.fill` | `services.attributes.fill.fill` | user, or nobody (nightly, read as the company's owner) | AIAttributeValue | attribute, company, status; one model call per row |
| `attribute.override` | `services.attributes.views.ValueListView` | user | AIAttributeValue | attribute, company; a person's answer on top of the model's |
| `request.create` | `services.requests.gather.gather` | user, or nobody (nightly) | FeatureRequest | title, evidence count; one model call named it |
| `request.update` | `services.requests.views.FeatureRequestDetailView` | leadership | FeatureRequest | the fields changed |
| `request.merge` | `services.requests.views.MergeView` | leadership | FeatureRequest (the survivor) | from, into, evidence moved |
| `request.evidence_move` | `services.requests.views.EvidenceMoveView` | leadership | FeatureRequest (the new home) | evidence id, from, into |
| `request.evidence_dismiss` | `services.requests.views.EvidenceDismissView` | leadership | FeatureRequest it left | evidence id, kind, from |
| `knowledge.gap_filled` | `services.knowledge.gaps.fill_from_contribution` | the person who wrote it down | KnowledgeGap | subject, customer |
| `knowledge.gap_dismissed` | `services.knowledge.views.KnowledgeGapDismissView` | user | KnowledgeGap | subject, customer |
| `knowledge.brief` | `services.knowledge.brief.generate` | user | AccountBrief | customer, how many records it read |
| `anomaly.found` | `services.anomalies.detect.detect` | user, or nobody (nightly) | Anomaly | title, how many companies, how many reports |
| `anomaly.update` | `services.anomalies.views.AnomalyDetailView` | leadership | Anomaly | title, new status |
| `translation.made` | `services.translation.translate.of_record` | user | Translation | kind, record id, the language asked for and the one detected |
| `brief.schedule` | `services.metrics.views.BriefScheduleView` | admin | BriefSchedule | cadence, the hint of the hook it posts to |
| `brief.sent` | `services.metrics.delivery.send` | user, or nobody (nightly) | BriefSchedule | the brief's date and the hook hint; the company's own figures left the building |
| `mcp.token_issued` / `mcp.token_revoked` | `services.mcp.views.McpTokenView` | the person | McpToken | label |
| `mcp.tool_called` | `services.mcp.views.McpView` | the token's owner | McpToken | tool, token label, argument names; somebody's agent read this company's records |
| `connector.connect` / `connector.disconnect` | `services.connectors.views` | integration manager | Connector | provider, department |
| `file.upload` / `file.delete` | `services.customers.views` (Files tab) | user | Attachment | name, size, content_type |
| `call.log` | `services.customers.views` (CallSense) | user | Call | title, whether a transcript was attached |
| `task.update` | `TaskDetailView` (Cockpit tick-off) | user | Task | `fields`, `from`, `to` |
| `connector.inbound_rejected` | `ConnectorInboundView` | — (anonymous source) | Connector | outcome `failure`; a push with a wrong secret |
| `webhook.create` / `webhook.update` / `webhook.delete` | `services.webhooks.views` | user | WebhookSubscription | url, event, changed fields |
| `attention.snoozed` | `services.attention.views.AttentionSnoozeView` | user | AttentionSnooze | key, and days or done |
| `attention.unsnoozed` | `services.attention.views.AttentionSnoozeView` | user | — (already deleted) | key |

Adding a new one: call `audit.record` at the point the change is committed, annotate the
line `# SOC2:LOG-01`, and add a row here.

Export for an access review (SOC2:AUTH-10): `python manage.py export_access_review > access-review.csv`.
