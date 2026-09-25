---
description: Product Requirements Document for Revenact — who it is for, what it does, what is built, and what is next.
---

# Revenact — Product Requirements Document

**Status of this document:** current as of 2026-09-18. Written from the code in
`revenact-backend`, `react-ts-app` and `revenact-infra`, not from a wishlist.
Anything marked *Planned* has no implementation.

Companion documents: [TRD](02-trd.md) (how it is built),
[UI/UX Design](../../../react-ts-app/docs/03-ui-ux-design.md),
[App Flow](../../../react-ts-app/docs/04-app-flow.md),
[Backend Schema](05-backend-schema.md), [Implementation Plan](06-implementation-plan.md).

---

## 1. What Revenact is

Revenact is a multi-tenant B2B SaaS platform for customer success. It gives a
software company one place to see how every customer relationship is actually
going, to understand why a number moved, and to decide what to do about it with
an AI agent that the whole team can watch and steer together.

It began as a churn-prevention tool for Customer Success Managers. It is now
positioned as a **company brain**: engineering, sales, analytics and leadership
all contribute what they know about a customer, and the same AI front door
answers questions for all of them.

One sentence: *Revenact turns the scattered evidence of a customer relationship
into a health score you can defend, a question you can ask in plain English, and
a decision your team makes together.*

---

## 2. The problem

| Problem | How it shows up | What Revenact does about it |
|---|---|---|
| Health scores are opinions | A CSM types "7 out of 10" into a CRM field and nobody can reconstruct why | `Customer.health_score` is computed from five weighted components and the breakdown is shown on hover. A human can override it, and the override is recorded as feedback. |
| Evidence is scattered | Calls in one tool, tickets in another, email in a personal inbox, context in someone's head | Mailboxes, ticket sources and calls are ingested into the customer record. Colleagues write contributions against a customer. Retrieval reads all of it. |
| Knowledge lives with one person | The only person who knows why an account is unhappy is on holiday | @mention a colleague or a whole function in a question; their answer is stored as company knowledge. |
| Management questions cut across dashboards | "What is NRR by product this quarter versus last?" has no home | A metric registry defines 21 numbers once, reads them from the same rollups the dashboards draw, and records them at each month end. |
| AI answers arrive without their working | One person gets an answer in a private chat and the team has to take it on faith | A Copilot conversation can be made a live session: colleagues join mid-run, see the same transcript, redirect the agent, and capture what was decided. |

---

## 3. Users

### 3.1 Tenancy

Every row belongs to an `Organisation` (the tenant). A tenant has members
(`User`), and each member has a **function** and a **role**.

### 3.2 Functions (who someone is)

`cs`, `engineering`, `sales`, `analytics`, `leadership`, `other`. Function drives
knowledge routing (`@engineering` reaches the engineering owner) and ticket
visibility (a ticket source is stamped with a department).

### 3.3 Roles and capabilities (what someone may do)

Roles are per tenant and hold a list of capabilities. Two system roles are created
with every organisation: **Admin** (all capabilities) and **CSM** (none).

| Capability | Unlocks |
|---|---|
| `manage_users` | User Management page, add/edit members, create roles |
| `manage_org_settings` | Organisation settings, currency, products, model budgets |
| `manage_custom_objects` | Define custom objects and fields |
| `manage_integrations` | Connectors and webhooks |
| `manage_fx_rates` | Exchange rates |
| `view_all_accounts` | The whole book rather than a personal one; the entire Knowledge Brain section |

### 3.4 The org chart

`User.reports_to` forms a tree. It is not decoration: it decides who can read a
personal note, a task, a synced email and a sliced Copilot transcript. The rule
the product owner chose is **"me and my juniors, not my seniors"**.

### 3.5 Primary personas

| Persona | Typical capabilities | Main surfaces |
|---|---|---|
| CSM | none beyond the defaults | Organisations, Accounts, Contacts, Pipelines, Copilot, Health dashboard |
| CS lead / manager | `view_all_accounts` | Everything a CSM sees plus their reports' books and the Knowledge Brain |
| Function specialist (engineering, sales, analytics) | none | Company View on a customer, the question inbox, Copilot |
| Leadership | `view_all_accounts`, usually all | Brain overview (brief, signals, metrics, drivers), Initiatives, Review Queue |
| Admin / ops | all | Settings, Users, Integrations, Agents (model budgets) |

---

## 4. Product principles

These are decisions already made and encoded in the product. Change them
deliberately, not by accident.

1. **Never fabricate a number.** A component with no data is excluded from the
   health rubric and the rest are rescaled, rather than scored zero. A missing
   pulse rating is null, not a 1. Charts leave unrated accounts out and say how
   many.
2. **Computed with override.** Derived values stay derived. A human correction is
   stored separately (`health_score_override`) and recorded in the feedback log,
   so the rubric and the human opinion never overwrite each other silently.
3. **Visibility is a product feature, not a setting.** Personal notes, tasks and
   mail follow the management chain. Tickets and pipeline items follow the
   department. Customer records follow ownership and responsibility. There is one
   definition of each rule in code and every surface calls it.
4. **Explicit opt-in for multiplayer.** A Copilot session never becomes visible to
   colleagues because a risk signal fired. Someone clicks to make it live, or
   hands it off.
5. **Evidence with every answer.** Copilot replies carry sources. A metric
   explanation cites figures. A proposal carries the rows that justify it. A reply
   that cites something the viewer may not read is withheld rather than trimmed.
6. **No task queue.** Background work is idempotent management commands on a
   timer. This is a deliberate trade against Celery's operational weight, revisit
   only when work needs retries or fan-out.
7. **Mock data is a bug, not a stage.** Every screen either reads the backend or
   is honestly labelled as not built.

---

## 5. Feature areas and status

Legend: **Built** end to end; **Partial** works but a named piece is missing;
**Backend only**; **Planned** no implementation.

### 5.1 Foundation

| Capability | Status | Notes |
|---|---|---|
| Organisation signup, JWT login, refresh, logout | Built | Refresh tokens rotate and blacklist; logout revokes |
| Forgot / reset password by email | Built | Always answers 200, never reveals whether an address exists |
| Own profile, change password | Built | |
| Members, roles, capabilities | Built | Roles are editable per tenant; system roles are protected |
| Org chart (`reports_to`) | Built | Manager column in User Management |
| Organisation settings | Built | Name, currency, default lifecycle stage, AI agent enable and tone, global attribute mapping |
| Multi-currency with FX | Built | Current rates only, no history |
| Audit log | Built | Append-only `AuditEvent`, catalogue in `docs/audit-events.md` |
| MFA | Planned | Formally excepted until 2027-03-31 (`.soc2/EXCEPTIONS.md` EX-001) |

### 5.2 Records

| Capability | Status | Notes |
|---|---|---|
| Organisations (customers): list, board, detail, create, edit, churn, archive | Built | 34 selectable table columns, health and CSAT popovers show real breakdowns |
| Organizations portfolio (list redesign) | Built (backend) | One endpoint for the page: rows with health trend, renewal runway, pulse, last touch and one signal computed by the dashboard's own code; the six detail panels; group, sort, filters and cursor pages; summary tiles over the filtered set; CSV export of all 34 fields; bulk owner, stage and archive under the single-edit rules |
| Accounts: list, board, detail, create, edit | Built | Many-to-many with customers. No churn or archive by design |
| Contacts: list, detail, CRUD, computed sentiment | Built | Sentiment read from that contact's own calls, tickets and emails |
| Activity feed: activities, emails, tasks, notes, tickets, calendar events, surveys, sessions, headlines, files, CallSense | Built | Tasks and notes can be created in place; emails can be composed |
| Activity feed: Slack | Partial | The only remaining inline mock in the app |
| Activity feed: Pulse, Conversations, Revenact Support | Planned | Render "coming soon" |
| Success Plans tab | Planned | Placeholder on both detail pages |
| Files | Built | Closed type list, magic-byte check, 25 MB cap, authenticated download only |
| CallSense | Built | Log a call, attach a transcript, model writes the summary, call is classified immediately |
| Custom objects and fields | Built | Definitions, fields, records; records scoped to a customer or account |
| Pipelines (opportunities and risks) | Built | Kanban with drag to stage; department and role scoped |
| Surveys (NPS, CSAT, CES) | Partial | Logged and scored by hand; no email delivery, no multi-question surveys |
| CSV import of organisations | Built | Column mapping UI under Settings, Entity Uploads |
| Canvas (stakeholder map) | Built | React Flow, contacts as nodes, labelled relationships |
| Campaigns | Partial | Synchronous send only; no scheduling, templates or open tracking |

### 5.3 Intelligence

| Capability | Status | Notes |
|---|---|---|
| Health rubric | Built | Five weighted components out of 10; largest-remainder apportionment so the parts sum to the headline |
| Account and organisation pulse | Built | 1 to 5 from five signals, daily dot recorded |
| Health history | Built | Monthly `HealthSnapshot`, month already recorded is skipped not overwritten |
| Interaction classification | Built | Claude assigns sentiment plus a three-level taxonomy to emails, calls and tickets |
| Contact sentiment | Built | Weighted by interaction kind and recency |
| Headlines (AI summaries per account) | Built | Explicit generate, evidence recorded |
| Copilot chat with retrieval and citations | Built | Local embeddings for semantic company matching, snippets as sources |
| Copilot streaming | Planned | Answers arrive in one shot |
| Copilot tool-calling agent (M3) | Planned | Today every send is one model call |
| Metric layer (21 metrics, month-end snapshots) | Built | Read from the same rollups the dashboards draw |
| Metric slices by owner, product, segment, lifecycle | Built | Only where the rollup already computes the cut |
| Signals (material moves with drivers) | Built | Material means 5 points or 10 percent |
| Management brief | Built | Written from the metric layer only, never from records |
| Metric explanations ("why is this number here") | Built | Behind a "Why?" control on every tile and signal |
| Ops agent proposals and review queue | Built | Proposes tasks and initiatives only; answers validated against what the prompt offered |
| Initiatives with follow-through | Built | Baseline captured once, progress read live, tasks link back |
| Feedback log | Built | Classification corrections, proposal decisions, health overrides |
| Model audit and budgets | Built | One `ModelCall` row per call, per-purpose monthly token budgets, 429 when exceeded |
| Knowledge graph | Built | Owners, customers, products, initiatives, proposals; edges only where a foreign key exists |
| AI-filled attributes | Built | An admin's plain-English question answered per company with reasoning and cited sources; append-only history with human overrides; nightly refresh reads as the owner |
| Feature requests with revenue | Built | Classified asks clustered into named requests by embedding; ARR, company and interaction counts computed over the reader's own book |
| Knowledge gaps and the account brief | Built | What the company cannot answer, raised from unanswered Copilot questions and stale routed ones; the standing brief on use cases, stakeholders and open threads |
| Anomaly clusters | Built | The same fault across several companies, found by embedding the fortnight's classified reports and kept only when it is a real spike |
| Translation | Built | Read an inbound message in your own language and write the reply in theirs; cached per record, and contacts learn the language they write in |
| Management brief to Slack | Built | Weekly or monthly delivery of the brief to a channel, posting what exists rather than generating on a schedule |
| MCP server | Built | An agent outside Revenact reads as the person whose token it holds, read-only, under their own visibility |
| Dashboard Overview — "Needs attention" | Built | Renewal, risk, going-quiet, support and anomaly items scored by ARR at stake × urgency, twice-filtered like every other dashboard; per-user snooze (a number of days, or Done) that reappears early if the item gets worse |
| Ask Revenact on the Dashboard | Built (backend) | Questions asked on the Dashboard carry where they were asked; the server recomputes that area's figures for the asker's own filtered book with the same code as the screen, adds the records behind the companies asked about under their own rules, and answers only from those. Metered as its own purpose; one history across Communications, Copilot and Dashboard, tagged with where each conversation started |

### 5.4 Multiplayer Copilot (the centerpiece)

The product owner describes this as "the crux of making this software".

| Phase | Scope | Status |
|---|---|---|
| M0 | Prototype: real Claude content, simulated multiplayer transport | Built, since replaced |
| M1 | Full account coverage | Built |
| M2 / Phase 2a | Real persistent invite-only cross-user sessions | Built |
| M2 / Phase 2b | Real WebSocket push, Redis channel layer, slow poll as fallback only | Built |
| Facilitator | Reads the session transcript and stores what the people decided as proposals | Built |
| M3 | Multi-step tool-calling agent replacing the single-shot call | Planned |
| Ephemeral presence ("who is looking right now") | Beyond `SessionParticipant` | Planned |

Access model: **invite only**. A session exists when the owner makes it live or
hands it off. An invited member must accept. The same visibility function gates
the REST endpoints and the WebSocket handshake.

### 5.5 Knowledge layer

| Capability | Status |
|---|---|
| Contributions per customer, attributed and function-stamped | Built |
| Per-function owners on a customer, plus one accountable account owner from any function | Built |
| Owner handover with a note, recorded as a contribution | Built |
| Questions with @mentions, routed and notified | Built |
| Function mentions (`@engineering`, `@team`) | Built |
| Question inbox in the Copilot sidebar, one-click ask suggestions | Built |
| Question aging, stale after 3 days, daily nudge | Built |
| Knowledge activity per function | Built |
| Org-chart scoping of everything above | Built |

### 5.6 Integrations

| Capability | Status | Notes |
|---|---|---|
| Personal mailboxes: Google, Microsoft, IMAP | Built | Per person; sync files a message only when the counterpart is a known contact or domain |
| Compose and send from your own mailbox | Built | Copy filed as a sent email |
| Ticket sources: Zendesk, Jira, Slack, Freshdesk, generic webhook | Built | Department stamped on the connector and inherited by the ticket |
| Inbound ticket webhook | Built | Shared token or HMAC signature, size and count capped |
| Other connector providers (Intercom, Salesforce, HubSpot, Gmail, Teams, Zoom, GitHub, Figma) | Planned | Present as choices only |
| Outbound webhooks | Partial | One event (`customer.created`); SSRF guard and HMAC signature in place |
| Scenarios (visual automation) | Partial | Send Email, Create Task, Set Attribute, Churn Entity, Condition, Filter execute. Assign Playbook, Slack, Teams, Create Pipeline, Send Survey and Schedule are canvas-only |

### 5.7 Dashboards

Eight built dashboards, all reading the backend: Health Overview (five tabs),
Usage Overview, Revenue Forecast, AI Trending Topics, Activity Tracking, Ticket
Overview, Customer Overview, Product Usage. Plus standalone Lifecycle and Health
rollup pages. Custom Dashboard is a placeholder.

Five of them — Customer Overview, Activity Tracking, Revenue Forecast, AI
Trending Topics, Ticket Overview — support drill-down: a chart figure's
`?drill=` opens the real companies behind it, scoped to the same filtered
set the figure itself counts and the viewer's own book.

---

## 6. Non-goals

- **Not a CRM of record.** Revenact reads from the systems that own tickets and
  mail; it does not try to replace them.
- **Not a marketing automation suite.** Campaigns exist to reach known contacts,
  not to run drip programmes.
- **Not real-time analytics.** Metrics are recorded at month end. Health is
  recalculated daily. Nothing needs sub-minute freshness.
- **Not multi-region or high availability.** The deployment is deliberately one
  small virtual machine for a team to share.
- **No per-tenant pipeline customisation.** Opportunity and risk stages are fixed
  enums that match the board columns.

---

## 7. Open product questions

1. **Global attribute mapping is stored but not honoured.** An organisation can
   choose which field means ARR, but the dashboards still read the fixed default.
   Either wire the rollups to the mapping or drop the setting.
2. **"My book" on dashboards.** Visibility was widened so managers see their
   reports' customers, but the dashboards have no explicit personal-book filter.
3. **Ops agent assignee logic is prompt-only.** The rule "match the function,
   else the owner" is described to the model rather than enforced in code.
4. **Survey delivery.** Surveys are logged by hand. Sending them, and collecting
   responses, is unbuilt.
5. **Default visibility of a live session.** The PRD flags making sessions visible
   by default as an unresolved risk. Today it is invite only.
6. **Retention.** Classification policy sets retention windows; no purge job
   exists (`.soc2` DATA-04).

---

## 8. Success criteria

| Question a user asks | The product answers it when |
|---|---|
| "Why is this account at 5.2?" | The health popover shows five real components with weights that sum to the headline |
| "Who knows about this customer?" | Company View lists the accountable owner and a responsible person per function |
| "Why did NRR move?" | The metric explanation cites the cuts and the accounts carrying the move |
| "What should we do this week?" | The review queue holds proposals with evidence, and approving one creates the task |
| "What did we decide?" | The session's captured decisions appear in the review queue tagged with that session |

---

## 9. Release history

| Date | Milestone |
|---|---|
| 2026-09-05 | Multiplayer Copilot phases 2a and 2b: real cross-user sessions, real WebSocket push |
| 2026-09-11 | Health rubric, health snapshots, Health Overview redesign on real data |
| 2026-09-12 | Metric layer, signals, brief, initiatives, Ops agent, review queue, feedback log, model budgets |
| 2026-09-13 | Company knowledge: functions, contributions, questions with mentions, org-chart scoping, knowledge graph; Azure deployment live |
| 2026-09-15 | SOC 2 controls: audit log, throttling, secure defaults, CI gates |
| 2026-09-16 | Personal mailboxes, ticket connectors, files and CallSense, contact sentiment, pipeline departments, account pulse, continuous deployment |
| 2026-09-18 | Agent skill sets vendored in both repos; Playwright agent CLI in the frontend |
| 2026-09-24 | Dashboard attention list; Ask Revenact on the Dashboard (backend) |
| 2026-09-25 | Organizations portfolio (backend): portfolio endpoint, export, bulk edit |
