# Data classification (SOC2:DATA-01)

Every stored entity, what it holds and how it must be treated. Classes follow
the soc2-dev registry; the default for anything not listed is **internal**
(`.soc2/config.yml: classification_default`).

| Class | Meaning | Handling |
|-------|---------|----------|
| public | Safe to publish | none |
| internal | Business data, not for outsiders | tenant-scoped (DATA-08), authenticated (AUTH-01) |
| confidential | Personal data or customer content; disclosure harms a person or a customer | internal + never in logs (LOG-03), never in URLs, access audited where it changes (LOG-01), encrypted at rest by the platform (DATA-02) |
| restricted | Credentials and secrets | confidential + hashed or encrypted, never returned by the API, never in metadata |

Tenant boundary: every row hangs off `accounts.Organisation`, directly or through
`Customer`/`Account`; `services/customers/scoping.py` is the only read path for
customer records.

## Entities

| Model | Class | Personal data (PII) | Notes |
|-------|-------|---------------------|-------|
| `accounts.User` | confidential | email, name, function, reports_to, last_login | `password`: **restricted**, Argon2id hash only (AUTH-04). `is_superuser` unused by app code (AUTH-09). |
| `accounts.Organisation` | internal | — | name, slug, currency, AI settings, global attributes |
| `accounts.Role` | internal | — | capability sets (AUTH-02, AUTH-09) |
| `core.AuditEvent` | confidential | actor_email, ip, user_agent, `metadata.email` on failed logins | append-only (LOG-02); retention 365 d (`retention_days.audit_logs`) |
| `customers.Customer`, `customers.Account` | confidential | business contact email, phone, address; owner (a User) | commercial terms (ARR, TCV, fees, seats, churn reason) are confidential business data |
| `customers.Contact` | confidential | name, role, email, phone, sentiment | a named person at the customer |
| `customers.Email` | confidential | sender_name, recipient_name, subject, body | customer correspondence; sent to the LLM provider for classification and Copilot answers (see flows) |
| `customers.Note`, `customers.Call`, `customers.Ticket` | confidential | author_name / host_name / assignee_name, body / summary | people's words about an account |
| `customers.Task`, `customers.CalendarEvent`, `customers.Activity` | internal | assignee_name, attendee_count | titles and descriptions may quote customers — treat description as confidential |
| `customers.Opportunity`, `customers.Risk`, `customers.Survey`, `customers.HealthSnapshot`, `customers.Product` | internal | — | pipeline, survey scores, health history |
| `customers.Canvas`, `customers.Headline` | internal | — | AI-generated summaries derive from confidential records; do not expose outside the tenant |
| `campaigns.Campaign` | confidential | recipients (contacts), send_log | outbound email content and who received it |
| `webhooks.WebhookSubscription` | internal | — | `secret`: **restricted** — HMAC signing key, currently plaintext in the column (DATA-02 gap, gap report #15); returned only on create |
| `webhooks.WebhookDelivery` | internal | — | `error` may echo the receiver's response body; keep out of logs |
| `copilot.Conversation`, `copilot.Message` | confidential | user, author, content, sources | Copilot chat; content quotes confidential records and names colleagues |
| `copilot.CopilotSession`, `SessionInvite`, `SessionParticipant`, `SessionEvent` | confidential | user references, `SessionEvent.payload` | multiplayer session activity |
| `copilot.ModelCall`, `copilot.ModelBudget` | internal | user | token counts, latency, outcome; no prompt or completion text is stored |
| `notifications.Notification` | internal | recipient, actor | message text may name a customer |
| `knowledge.Contribution`, `knowledge.Question`, `knowledge.FunctionOwner` | confidential | author, asked_by, assignee, body, text, answer | what a colleague said about a customer, attributed |
| `metrics.MetricSnapshot`, `metrics.Initiative`, `metrics.Proposal`, `metrics.Feedback` | internal | owner, created_by, decided_by, made_by | aggregates and decisions |
| `metrics.Brief`, `metrics.Explanation` | internal | — | AI narrative over aggregates; evidence may name accounts |
| `custom_objects.*` | confidential | — | `CustomObjectRecord.data` is tenant-defined; assume it can hold personal data |
| `connectors.Connector` | internal | — | provider and name only; no credentials stored |
| `scenarios.Scenario`, `scenarios.ScenarioRun` | internal | triggered_by | automation graphs and run logs |
| `fx_rates.FxRate` | internal | — | |

## Flows that leave the tenant boundary

| Flow | Data | Class | Control |
|------|------|-------|---------|
| Copilot / classification → Anthropic API or AWS Bedrock (`services/copilot`) | Email, Ticket, Note, Call, Contribution text; account names and metrics | confidential | TLS; provider under DPA when a customer is onboarded (Third-Party Management policy, deferred); `ModelCall` records every call without content |
| Outbound webhooks (`services/webhooks/engine.py`) | event payloads the tenant subscribed to | internal / confidential | HTTPS only, no redirects, HMAC-SHA256 signature (SEC-07), SSRF guard |
| Password-reset email (`ForgotPasswordView`) | user email, single-use token link | confidential | Django token generator; reset audited (LOG-01) |
| Container stdout → log store (`config/settings.py: LOGGING`) | request ids, actions, outcomes | internal | `core.logging.RedactFilter` (LOG-03); no bodies or headers logged |
| Nightly `pg_dump` → Azure Blob (`revenact-infra/deploy/backup.sh`) | everything above | confidential | private account, identity auth, versioning, 35-day expiry (DATA-07) |

## Retention

From `.soc2/config.yml` `retention_days`: audit events 365, application logs 90,
personal data after account close 30, backups 35. Application records
(emails, tickets, notes, Copilot sessions, webhook deliveries) have no retention
job yet — DATA-04 in the gap report. Non-production data is synthetic
(`seed_demo`, DATA-10).

Update this file in the same pull request as any migration that adds a model
or a personal-data column.
