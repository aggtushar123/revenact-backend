---
description: Backend schema reference — every model, field, relation, constraint and visibility rule in the Revenact database.
---

# Revenact — Backend Schema

Forty-six concrete models across fifteen Django apps, 86 migrations, PostgreSQL 16.
Current as of 2026-09-18, read from `models.py` in every app.

This document describes **storage**. The wire format is in
[`docs/API_CONTRACTS.md`](../API_CONTRACTS.md); the handling rules per entity are
in [`docs/data-classification.md`](../data-classification.md).

---

## 1. Conventions

| Convention | Rule |
|---|---|
| Primary key | `BigAutoField` named `id` on every model |
| Timestamps | `created_at` auto on insert, `updated_at` auto on save, ISO 8601 UTC |
| Tenant boundary | Every row reaches `accounts.Organisation`, directly or through `Customer` / `Account` |
| Deletion | `CASCADE` from the tenant down; `SET_NULL` for people references so removing a user never deletes their work; `PROTECT` where a reference must block deletion (`User.role`, `Customer.primary_product`) |
| Money | `DecimalField(12, 2)`, or `(18, 4)` for metric values |
| Scores | Health `Decimal(3,1)` 0 to 10; pulse `PositiveSmallIntegerField` 1 to 5, null means unrated |
| Enumerations | Django `TextChoices` inner classes, never free strings |

### 1.1 The "exactly one parent" pattern

Sixteen models belong to **either** a `Customer` **or** an `Account`, never both
and never neither. Rather than a generic foreign key, each carries two nullable
foreign keys plus a database check constraint:

```python
customer = models.ForeignKey(Customer, null=True, related_name="notes", on_delete=CASCADE)
account = models.ForeignKey(Account, null=True, related_name="notes", on_delete=CASCADE)


class Meta:
    constraints = [CheckConstraint(..., name="note_belongs_to_exactly_one_parent")]
```

The models using it: `HealthSnapshot`, `Activity`, `Email`, `Task`, `Note`,
`Ticket`, `Attachment`, `Call`, `CalendarEvent`, `Contact`, `Opportunity`,
`Risk`, `Survey`, `Canvas`, `Headline`, `CustomObjectRecord`.

This is chosen over a `GenericForeignKey` so the database enforces the invariant
and ordinary joins stay possible.

---

## 2. Entity overview

```
Organisation ──┬── Role ── User ──┬── reports_to (self, org chart)
               │                  ├── MailboxConnection (1:1)
               │                  └── Notification
               ├── Product
               ├── Connector
               ├── Customer ──┬── Account (many-to-many)
               │              ├── Contact, Activity, Email, Task, Note,
               │              │   Ticket, Call, CalendarEvent, Attachment,
               │              │   Opportunity, Risk, Survey, Canvas,
               │              │   Headline, HealthSnapshot, CustomObjectRecord
               │              │   (each hangs off the Customer OR one Account)
               │              ├── Contribution, Question, FunctionOwner
               │              └── CopilotSession (context)
               ├── Conversation ── Message
               │        └── CopilotSession ──┬── SessionInvite
               │                             ├── SessionParticipant
               │                             └── SessionEvent
               ├── MetricSnapshot, Brief, Explanation, Initiative,
               │   Proposal, Feedback
               ├── CustomObjectDefinition ── CustomFieldDefinition
               ├── Scenario ── ScenarioRun
               ├── WebhookSubscription ── WebhookDelivery
               ├── Campaign
               ├── FxRate
               ├── ModelCall, ModelBudget
               └── AuditEvent
```

---

## 3. `core`

### `AuditEvent`
One security-relevant event. Append-only: `save()` refuses updates and
`delete()` refuses outright.

| Field | Type | Notes |
|---|---|---|
| organisation, actor | FK, SET_NULL, null | denormalised `actor_email` kept alongside |
| action | Char(64), indexed | `<area>.<verb>`, catalogue in `docs/audit-events.md` |
| target_type, target_id, target_repr | Char | what was acted on |
| outcome | Char(16) | success, failure, denied |
| ip, user_agent, request_id | | request provenance |
| metadata | JSON | credential-shaped keys are dropped by `audit.record` |
| created_at | DateTime, indexed | |

Index on `(organisation, created_at)`. Retention 365 days by policy; no purge job
exists yet.

---

## 4. `accounts`

### `Organisation` (the tenant)
`name`, unique auto-generated `slug`, `currency` (USD, EUR, GBP, INR, CAD, AUD,
JPY), `default_lifecycle_stage`, `ai_agent_enabled`, `ai_agent_tone`
(professional, friendly, concise), `global_attributes` JSON, `created_at`.

`ensure_system_roles()` creates the protected Admin and CSM roles.
`effective_global_attributes()` merges stored choices over the defaults, which map
ARR, MRR, renewal date and joined date onto concrete customer columns.

### `Role`
`organisation`, `name`, `slug`, `permissions` (a list of capability strings),
`is_system`. Unique on `(organisation, slug)`.

Capabilities, the closed set: `manage_users`, `manage_org_settings`,
`manage_custom_objects`, `manage_integrations`, `manage_fx_rates`,
`view_all_accounts`.

### `User` (`AUTH_USER_MODEL`)
Email is the username. `name`, `function` (cs, engineering, sales, analytics,
leadership, other), `reports_to` (self-FK, the org chart), `organisation`,
`role` (PROTECT), `is_active`, `is_staff`, `date_joined`, plus the inherited
password and permission fields.

`has_capability(cap)`: superuser true, no role false, else the role's list.

---

## 5. `customers` (the largest app, 45 migrations)

### `Product`
`organisation`, `name`, `is_active`. Unique case-insensitively per organisation.

### `Customer`
The company a CSM tracks. Not to be confused with `Organisation`, the tenant.

**Identity:** `name`, `address`, `domain`, `industry`, `email`, `phone`,
`owner` (the one accountable person, from any function), `created_by`,
`modified_by`, `is_archived`.

**Lifecycle and health:** `lifecycle_stage` (onboarding, kickoff, adoption, live,
renewal, churn, expansion, other), `health_score` Decimal(3,1) default 5.0,
`health_score_override`, `pulse` JSON (history dots), `ai_pulse_value` 1 to 5,
`ai_pulse_reason`, `csm_pulse_score` 1 to 5, `csm_pulse_modified_at`,
`pulse_recorded_on`, `nps_score`, `csat_score`.

**Dates:** `joined_date`, `renewal_date`, `contract_start_date`,
`contract_end_date`, `churn_date`.

**Money:** `currency`, `arr_billed_at_account`, `arr_billed_at_hq`,
`implementation_fee`, `total_contract_value`,
`total_forecasted_renewal_revenue`.

**Product and usage:** `primary_product` (PROTECT), `additional_products_count`,
`top_source_channel`, `total_contracted_seats`, `total_active_seats`,
`total_hires`, `scope_web_app`, `ces_percentage`.

**Churn:** `churn_reason` (price, budget, product_gap, adoption, competitor,
champion_left, acquired, shut_down, consolidation, support, other),
`churn_comment`.

Derived, not stored: `health_category` (good at 7, average at 4, else poor),
`ai_pulse_score` (the enum name for the numeric pulse),
`seat_utilization_percentage`, `csat_breakdown`, `health_breakdown`,
`health_score_is_overridden`, `account_pulse()`.

### `Account`
A named sub-account. **Many-to-many with `Customer`**, related name `accounts`.
Carries its own `name`, `domain`, `industry`, `address`, `email`, `phone`,
`owner`, `lifecycle_stage`, `health_score`, both pulses, `nps_score`,
`csat_score`, `renewal_date` and `arr`. Blank contact fields fall back to the
parent at the presentation layer, not in the model. There is deliberately no
`churn_date`, `is_archived` or `ces_percentage`.

### `HealthSnapshot`
One parent's health on one date: `captured_on`, `health_score`,
`csm_pulse_score`, `ai_pulse_value`. Partial unique constraints give one row per
parent per date; indexed on `(parent, captured_on)`. Written monthly at the last
completed month end.

### `AIClassified` (abstract)
Base of `Email`, `Ticket` and `Call`. Adds `sentiment` (positive, neutral,
negative), `ai_area`, `ai_category`, `ai_subcategory`, `ai_classified_at` and
`classification_corrected_at`. `clean()` refuses a subcategory that does not
belong to its category.

The taxonomy: three areas (product_growth, support_operations, customer_success),
ten categories, twenty-five subcategories, with both directions of the mapping
available in `taxonomy.py`.

### Interaction and record models

| Model | Distinctive fields |
|---|---|
| `Activity` | `type` (11 kinds of thing we did), `occurred_at`, `links`, `watchers` |
| `Email` | `subject`, `sender_name`, `recipient_name`, `body`, `sent_at`, `is_starred`, plus the sync set: `mailbox`, `mailbox_owner` (decides visibility), `direction`, `from_address`, `to_addresses`, `thread_id`, `provider_message_id`, `synced_at`. Unique per mailbox and provider message. Indexed on `(mailbox_owner, -sent_at)` |
| `Task` | `title`, `assignee_name`, `created_by`, `assignee`, `due_date`, `priority`, `status`, `initiative` (links work to a decision) |
| `Note` | `title`, `author_name`, `author` (decides visibility), `body`, `logged_at`, `links` |
| `Ticket` | `ticket_number`, `title`, `status`, `priority`, `connector`, `department` (decides visibility), `opened_at`, `resolved_at`, external id and url, `requester_*`, `synced_at`. Unique per connector and external id |
| `Attachment` | `file` stored at `attachments/<org>/<uuid><ext>`, sanitised `name`, `content_type`, `size`, `source` (upload or transcript), `uploaded_by`. `delete()` removes the bytes |
| `Call` | `title`, `host_name`, `occurred_at`, `duration_minutes`, `summary`, `connector`, `logged_by`, `transcript` (1:1 Attachment), `recording_url`, `participants` (M2M Contact) |
| `CalendarEvent` | `title`, `description`, `type`, `event_date`, `start_time`, `end_time`, `attendee_count` |
| `Contact` | `name`, `role` (8 kinds), `email`, `phone`, `status`, `sentiment`, `sentiment_source` (manual or computed), `sentiment_evidence` JSON (counts only, never text), `sentiment_computed_at`, `last_contacted_at` |
| `Opportunity` | `title`, `mrr`, `stage` (discovery, qualification, solution_validation, proposal_price_review, negotiation, closed_won), `priority`, `department` |
| `Risk` | Same shape; `stage` is open, mitigated, realised, abandoned |
| `Survey` | `survey_type` (nps, csat, ces), `status`, `score`, `sent_at`, `responded_at`. CES is customer-only, enforced in views |
| `Canvas` | `name`, `nodes`, `edges` JSON, for the React Flow stakeholder map |
| `Headline` | `kind` (summary or headline), `title`, `content`, `status`, period bounds, `data_sources`, `generated_at`. A check constraint forbids a status on a summary |

---

## 6. `copilot`

| Model | Purpose |
|---|---|
| `Conversation` | One chat thread, owned by a user within an organisation |
| `Message` | One turn: `role`, `content`, `author`, `sources` (citation snapshots), `ask_suggestions` |
| `CopilotSession` | Multiplayer wrapper, one-to-one with a conversation. Optional customer or account context. `status`: private, live, awaiting_handoff, closed |
| `SessionInvite` | The access gate. One per user per session, pending, accepted or declined |
| `SessionParticipant` | Who joined, and when they left |
| `SessionEvent` | joined, left, redirected, handed_off, made_live, closed. Chat content stays in `Message` and is never duplicated here |
| `ModelCall` | One row per model call: purpose, provider, model, token counts, latency, outcome (ok, failed, unconfigured, over_budget). No text |
| `ModelBudget` | Monthly token allowance per organisation per purpose |

---

## 7. `metrics`

| Model | Purpose |
|---|---|
| `MetricSnapshot` | One metric's value at one month end, optionally cut by dimension and member. Unique per organisation, metric, dimension, member and period |
| `Brief` | The organisation's written brief: headline, body, watch list, evidence |
| `Explanation` | Why one metric sits where it does: text plus cited figures and the inputs used |
| `Initiative` | A decision to move a number: hypothesis, metric, optional cut, target value and date, owner, status, outcome, and a baseline captured once at creation and never re-read |
| `Proposal` | An agent's suggested action: kind (task or initiative), rationale, evidence, the concrete action, the decision and its result, and the session that produced it when captured by the facilitator |
| `Feedback` | A human correction: classification, proposal decision or health override, with before and after |

The 21 metrics themselves live in code (`registry.py`), not in the database:
active_customers, active_arr, average_arr, logo_retention, churned_arr_12m,
top_three_share, forecast_arr, nrr, at_risk_arr, seat_utilisation, shelfware_arr,
at_capacity_arr, coverage, dark_accounts, dark_arr, healthy_share,
poor_health_count, open_tickets, open_questions, stale_questions,
contributions_30d.

---

## 8. `knowledge`

| Model | Purpose |
|---|---|
| `FunctionOwner` | Who answers for one function on one customer. One per customer and function. `clean()` requires the person to actually work in that function |
| `Contribution` | What a colleague knows about a customer, attributed, with the author's function snapshotted at write time |
| `Question` | An asked question routed to an assignee, optionally raised from a Copilot turn. Its answer is stored as a `Contribution`, so the knowledge base picks it up |

---

### `knowledge` (gaps and the brief)

| Model | Purpose |
|---|---|
| `KnowledgeGap` | A question the company could not answer about a customer: subject, how often it has been asked, which function owes the answer, and the `Contribution` that eventually filled it. Raised from a Copilot answer with no sources, or a routed question left open |
| `AccountBrief` | One per customer: use cases, stakeholders, open threads and the records it was written from. Generated on request, never on a schedule |

---

## 9. `mail`, `connectors`, `webhooks`, `campaigns`

| Model | Purpose |
|---|---|
| `MailboxConnection` | One per person. Provider (google, microsoft, imap), address, Fernet-encrypted `credentials`, sync cursor and status |
| `Connector` | An external system for the organisation. Thirteen provider choices, five implemented. `department` stamps every ticket it brings in. Encrypted credentials, sync cursor, and optional scoping to particular customers or accounts (empty means org-wide) |
| `WebhookSubscription` | Outbound subscription: url, event (only `customer.created` today), signing `secret` (plaintext, known gap), active flag |
| `WebhookDelivery` | One attempt: success, status code, error |
| `Campaign` | Bulk email to chosen contacts: subject, body, recipients M2M, `send_log` JSON, sent timestamp |

---

## 10. `custom_objects`, `scenarios`, `notifications`, `fx_rates`

| Model | Purpose |
|---|---|
| `CustomObjectDefinition` | A tenant-defined object; applies to customers, accounts or both |
| `CustomFieldDefinition` | A field on it: text, number, currency, date, boolean or picklist, with ordering and required flag |
| `CustomObjectRecord` | One instance, `data` JSON keyed by field api_name, attached to exactly one parent |
| `Scenario` | An automation graph: `apply_to`, `nodes` and `edges` JSON, `is_active` gating event runs. A condition reads an allowlist of customer facts, an AI attribute by name, or matches a plain-English phrase against recent interactions by embedding; actions include routing the customer to a person |
| `ScenarioRun` | One execution with a per-node log |
| `Notification` | copilot_invite, copilot_handoff, customer_assigned, account_assigned, question_asked, question_answered; message, relative link, read flag |
| `FxRate` | Current rate to the organisation's currency. No history; cleared when the organisation changes currency |

---

### `attributes`

| Model | Purpose |
|---|---|
| `AIAttribute` | A plain-English question asked of every company: `prompt`, typed `value_type` (text, number, boolean, picklist), `applies_to_customer` / `applies_to_account`, `refresh` (manual or nightly). Defined under `manage_custom_objects` |
| `AIAttributeValue` | One answer, append-only: `value`, `reasoning`, `sources` (citation snapshots), `status` (filled, insufficient, failed), `origin` (ai or human), `set_by`, `computed_at`. Exactly one of customer or account. The newest row is the current value; a reader's view of `sources` and `reasoning` is filtered by their own visibility of the cited records |

---

### `requests`

| Model | Purpose |
|---|---|
| `FeatureRequest` | One thing customers keep asking for: title, summary, status, owner, and the centroid `embedding` new asks are matched against |
| `RequestEvidence` | One classified interaction filed under a request: `kind` + `record_id` (unique together), the company it came from (exactly one of customer/account), a snippet and its date. `dismissed` keeps a rejected ask out of the next gather |

---

### `anomalies`

| Model | Purpose |
|---|---|
| `Anomaly` | A cluster of reports that mean the same thing across several companies: title, summary, status, and the centroid later reports are matched against |
| `AnomalyEvidence` | One interaction in a cluster, with the company it came from and copies of the source's `mailbox_owner` and `department` so the snippet stays behind the record's own rule |

---

### `translation`

| Model | Purpose |
|---|---|
| `Translation` | One record's words in one language, kept so the next reader pays nothing. `source_hash` makes an edited record miss the cache. Read only under the source record's own rule |

---

### `attention`

| Model | Purpose |
|---|---|
| `AttentionSnooze` | One viewer's snooze on one Dashboard Overview "Needs attention" item: `user` + `key` (unique together), `until` (nullable; null means Done), `fingerprint` (the item's facts at snooze time — never a count of days — so a later read can tell whether it got worse) |

No model backs the items themselves — `services.attention.rules.build_items`
computes them fresh from `Customer`, `Ticket` and `Anomaly`/`AnomalyEvidence`
each request, the same reuse-the-existing-rule shape as the forecast and
health rollups above.
### `metrics` (brief delivery)

| Model | Purpose |
|---|---|
| `BriefSchedule` | One per organisation: where the management brief is posted (a Slack incoming webhook, never returned to a client) and on which day. Posts what exists; never generates, and has no hour because the job that posts it runs once a night |

---

### `mcp`

| Model | Purpose |
|---|---|
| `McpToken` | One person's key for an agent that reads Revenact as them. Stored hashed, shown once, revocable. Every MCP tool runs under that person's own visibility |

---

## 11. Visibility rules

Each rule has exactly one definition. Add a new reading endpoint and you must
call the matching helper.

| Records | Rule | Defined in |
|---|---|---|
| Customers and accounts | Own organisation, then: `view_all_accounts` sees all; otherwise owned, owned by a report, account-owned, unowned, function-owned, a customer you were asked about or answered for, or one you wrote about | `services/customers/scoping.py` |
| Notes | Author and their management chain; authorless seeded rows are visible to all | `services/customers/personal.py` |
| Tasks | Creator, assignee and both chains; authorless seeded rows visible to all | same |
| Tickets | Own department plus undeparted tickets; Leadership and `view_all_accounts` see all | same |
| Synced emails | Mailbox owner and their chain; rows with no mailbox owner are visible to all | `services/mail/visibility.py` |
| Opportunities and risks | Own department plus undeparted; Leadership and `view_all_accounts` see all | `scoping.pipeline_visible_q` |
| Contributions and questions | Self, subtree, same function, ancestors, plus anything addressed to you | `services/knowledge/views.py` with `services/accounts/hierarchy.py` |
| Conversations and sessions | Owner, or an accepted and active participant. The same function gates the WebSocket | `services/copilot/views.conversations_visible_to` |
| Copilot replies | A reply that cites records outside the viewer's scope is withheld entirely, not trimmed | `copilot.views._reply_readable_by` |

---

## 12. Derived versus stored

| Value | Where it lives | Why |
|---|---|---|
| `health_score` | Stored column, recomputed on write and nightly | The list endpoint sorts and filters on it in SQL |
| `health_score_override` | Separate column | A human opinion must not be silently overwritten by the next recalculation |
| `health_category`, `ai_pulse_score`, `seat_utilization_percentage` | Python properties | Pure functions of stored values |
| `account_pulse` | Computed per request from annotated inputs | Depends on a 30-day window, so it cannot be stale |
| Pulse history dots | Stored JSON list, one per day | Appended by the daily job |
| Metric values | Stored only at month end | Live values are computed from the same rollups the dashboards use |
| Contact `sentiment` | Stored, with `sentiment_source` saying whether a human set it | Lets a hand-set value survive until evidence exists |

---

## 13. Adding a model

1. Put it in the app that owns the concept. If it belongs to a customer or an
   account, use the two-nullable-keys plus check-constraint pattern above.
2. Add the visibility helper call to every endpoint that reads it, or explain in
   the docstring why it is unrestricted.
3. Update [`docs/data-classification.md`](../data-classification.md) in the same
   pull request as the migration. That file is the compliance artefact.
4. Update [`docs/API_CONTRACTS.md`](../API_CONTRACTS.md) with the endpoints.
5. Add this document's row.
6. Unit, integration and end-to-end tests, per the `testing` skill.
7. If it touches a security-relevant action, call `audit.record` and add a row to
   [`docs/audit-events.md`](../audit-events.md).
