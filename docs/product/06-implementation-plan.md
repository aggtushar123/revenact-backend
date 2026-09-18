---
description: Prioritised implementation plan for Revenact — the work ahead as bite-sized tasks with files, tests and done-when criteria.
---

# Revenact — Implementation Plan

Written with the `writing-plans` skill. Every task assumes an engineer who is
skilled but has no context for this codebase: it names the files to touch, the
tests to write and what "done" means.

Derived from the gaps recorded in [PRD §7](01-prd.md#7-open-product-questions),
[TRD §10](02-trd.md#10-known-technical-limitations),
[UI/UX §12](../../../react-ts-app/docs/03-ui-ux-design.md#12-current-design-debt)
and the SOC 2 control map. Current as of 2026-09-18.

---

## 0. Working agreements

- **Read the skills first.** `brainstorming` before any creative work,
  `test-driven-development` while writing, `commit-messages` for the message,
  `api-contracts` if an endpoint changes, `flow-docs` if the wiring changes,
  `revenact-design` before any visual change, `soc2-dev` for anything touching
  auth, data, logging or infrastructure.
- **Three tiers of tests, always.** Unit, integration, end to end. A feature
  without all three is not done.
- **One concern per pull request.** Stack branches when a migration ordering
  forces it, and say so in the description.
- **Never chain a commit onto a grep of test output.** `grep` exits zero when it
  *finds* the word FAILED. Gate on a match for `^OK`.
- **Never run two Django test suites at once.** The second hangs on the "test
  database exists" prompt. Pass `--noinput`.
- **After any `seed_demo_*` run, run `recalculate_health`.** The seed writes
  fixture health scores back over computed ones.
- **Verify in the running app, not only in tests.** `npm run dev`, then
  `npm run pw -- open http://localhost:5173`.

### Prerequisite, blocking everything

**P0. Restore GitHub Actions billing.** Since some time after 2026-09-16 every CI
job on both repositories fails in under three seconds with "recent account
payments have failed or your spending limit needs to be increased". No job
starts, so nothing merges through the gates and nothing deploys. This is an
account action in GitHub billing settings, not a code change. Until it is fixed,
verify locally and say so in each pull request.

---

## 1. Correctness and trust

### A1. Account detail page must not fall back to mock data

**Why.** `/accounts/:id` renders from `location.state.account`. On a direct visit
or a refresh there is no state, and the page silently shows
`ACCOUNTS_DATA.find(...) ?? ACCOUNTS_DATA[0]`: a fabricated account, with the
Navbar header agreeing. A user can screenshot a customer meeting with invented
numbers. This is the last real mock dependency in the application.

**Files.** `src/pages/accounts/Details.tsx`, `src/components/layout/Navbar.tsx`,
`src/features/customers/customersSlice.ts`,
`src/components/organizations/accountsData.ts`.

**Steps.**
1. Add a `fetchAccountById(accountId)` thunk. The backend has no flat account
   detail route, so either add `GET /api/v1/accounts/<pk>/` in
   `services/customers/views.py` beside `AccountListView`, or resolve through the
   nested route when the customer id is known. Prefer the flat endpoint; it
   matches `ContactDetailView`'s precedent and the contract note about flat views.
2. On mount, use `location.state.account` when present, otherwise dispatch the
   fetch and render a loading state.
3. Render a real not-found state for an id outside the caller's scope. The
   backend already answers 404 rather than an empty body.
4. Do the same in the Navbar's account branch.
5. Delete `accountsData.ts` entirely once nothing imports it, including
   `accountActivityData.ts` and `activityData.ts` if they become orphans.

**Tests.** Backend: an `APITestCase` for the new endpoint including a
cross-tenant 404. Frontend: a Details test that renders with no navigation state
and asserts a fetch and a loading state, and one that asserts the not-found state.

**Done when.** A hard refresh on an account detail URL shows that account's real
data or an honest error, and `grep -r ACCOUNTS_DATA src` returns nothing.

### A2. Bring the stale documents back in line with the code

**Why.** Four documents actively mislead. An agent or a new engineer following
them will look for endpoints that no longer exist.

**Files.**
- `revenact-backend/.agents/workflows/repo-architecture.md`: lists only `core`,
  `accounts` and `customers`; says Contacts, Pipelines, Dashboards, Copilot,
  Scenarios and Company Brain are not started; references
  `CSMListCreateView`, `/csms/` and `IsOrgAdmin`, all removed.
- `revenact-backend/.agents/workflows/auth-flow.md`: same removed names.
- `revenact-backend/README.md`: project layout lists two apps under `services/`.
- `react-ts-app/.agents/workflows/repo-architecture.md`: claims only auth is
  wired, describes a four-reducer store, mentions `AdminRoute`.
- `react-ts-app/README.md`: claims the app "runs against in-memory fixture data
  (no backend required)" and lists demo credentials that are not the real ones.
- `revenact-backend/docs/API_CONTRACTS.md`: the Calendar Events row says the
  frontend is on a mock; it is wired. The Company Brain row says not started; two
  phases shipped.
- `services/accounts/models.py`: the `Organisation` docstring says Copilot has no
  backend.

**Steps.** Rewrite each in place. Never fork into a v2 copy; these are living
documents. Use this document set as the source for the current picture.

**Tests.** None, but the `soc2-scan` control-map drift check must still pass.

**Done when.** No document names a symbol that `grep` cannot find in the code.

### A3. Give the frontend README the real getting-started

**Why.** It tells a new engineer no backend is needed, which is false for every
screen, and offers demo logins that do not exist.

**Files.** `react-ts-app/README.md`.

**Steps.** State that the API is required, point at `revenact-backend`'s README
for bringing it up, give the real seeded logins, document `VITE_API_URL`, and
document `npm run pw` for driving the app.

---

## 2. Compliance, the named SOC 2 gaps

Each of these closes a control that is currently Partial or Missing in
`.soc2/CONTROL_MAP.md`. Load the `soc2-dev` skill before starting, and update the
control map and the gap report in the same pull request.

### B1. Retention purge job (DATA-04, Missing)

**Why.** `.soc2/config.yml` declares retention windows (audit 365 days,
application logs 90, personal data 30 days after account close, backups 35,
tickets 730). Nothing enforces the application-record windows.

**Files.** New `services/customers/management/commands/purge_expired_records.py`,
new `services/customers/retention.py`, `docs/data-classification.md`,
`.soc2/CONTROL_MAP.md`.

**Steps.**
1. Put the policy in one module: a table of model, date field and window, read
   from `.soc2/config.yml` so the policy and the code cannot drift.
2. Write the command with `--dry-run` reporting counts per model, and
   `--organisation` to limit the blast radius. Delete in batches.
3. Audit each run with `audit.record("retention.purge", ...)`.
4. Add it to `run_health_maintenance` only after it has run clean by hand for a
   week. Until then keep it manual.

**Tests.** Unit tests per window boundary, one day either side. An integration
test proving a row inside the window survives. Assert `--dry-run` deletes nothing.

**Done when.** DATA-04 reads Met with the command named as evidence.

### B2. Encrypt the webhook signing secret (DATA-02, Partial)

**Why.** `WebhookSubscription.secret` is plaintext in the column while mail and
connector credentials are Fernet-encrypted. The crypto helper already exists.

**Files.** `services/webhooks/models.py`, `services/webhooks/engine.py`,
`services/webhooks/views.py`, a migration, `services/mail/crypto.py` (reuse, do
not copy).

**Steps.**
1. Move `services/mail/crypto.py` to a shared location, or import it as is and
   note the dependency. Do not write a second Fernet implementation.
2. Add an encrypted column, write a data migration that encrypts existing values,
   drop the plaintext column in a follow-up migration.
3. Keep the behaviour that the secret is returned only once, on create.

**Tests.** A model test asserting the raw column is unreadable, a signing test
asserting delivery still verifies, and a migration test over an existing row.

### B3. Ship logs off the machine and alert on them (LOG-04, LOG-05)

**Why.** Logs live only in the container's stdout on one virtual machine. A
security event after a restart is unrecoverable, and nothing tells anyone.

**Files.** `revenact-infra/deploy/docker-compose.prod.yml`,
`revenact-infra/terraform/`, `.soc2/CONTROL_MAP.md`.

**Steps.** Pick the smallest thing that works on one machine: the Azure Monitor
agent to a Log Analytics workspace, or a log driver to a hosted collector. Then
one alert rule on repeated `auth.login` failures and one on any 5xx rate. Keep it
to one VM; do not introduce managed services beyond the log sink.

**Done when.** A failed login on the live site is visible off the box within five
minutes, and produces an alert when repeated.

### B4. Name the exception approvers

**Why.** All three entries in `.soc2/EXCEPTIONS.md` still say `[TODO: name]`. An
unapproved exception is not an exception, it is a gap.

**Files.** `.soc2/EXCEPTIONS.md` in both repositories.

**Steps.** Fill the approver and the approval date for EX-001 (no MFA), EX-002
(closed) and EX-003 (internal-hop TLS). This is a five minute task blocked only
on a decision.

### B5. Replace the static Bedrock key (INFRA-03, Partial)

**Why.** A long-lived AWS access key sits in the environment file on the VM and
is rotated by hand. One such key already reached git history and needs rotating.

**Files.** `revenact-infra/terraform/`, `revenact-infra/deploy/`,
`config/settings.py`.

**Steps.** Prefer short-lived credentials. If AWS and Azure cannot be federated
cheaply, at minimum document a rotation schedule, set a calendar reminder, and
rotate the compromised key now.

---

## 3. Design system

Load `.claude/skills/revenact-design/SKILL.md` first. One surface per pull
request. Run the eleven-point checklist in the UI/UX document before review.

### F1. Wire the three brand fonts

**Why.** The highest-leverage typography fix in the repo. DM Serif Display, DM
Mono and Lato are downloaded on every page load and never applied, so the app
renders in Tailwind's default stack and `.font-display` resolves to `inherit`.
Every "the app looks generic" complaint starts here.

**Files.** `src/index.css`, `src/pages/auth/Login.css`.

**Steps.**
1. Add `--font-display`, `--font-mono` and `--font-sans` to the `@theme` block.
2. Set `body { font-family: var(--font-sans) }`.
3. Make `.font-display` and `.font-mono-brand` real.
4. Remove the Inter request from `Login.css`; Inter is not loaded anywhere.
5. Sweep for text that should now be mono: table numbers, identifiers, badges,
   token counts. Add `tabular-nums` wherever numbers stack in a column.

**Tests.** A render test asserting a computed font family on a heading and on a
numeric cell. Take before and after screenshots with `npm run pw`.

**Done when.** No surface renders in the default system stack, and numeric
columns align.

### F2. Keyboard and focus

**Why.** Zero `focus-visible` styles exist. Inputs use `focus:outline-none`
without replacing the indicator, so keyboard users lose their place. Fifteen
modals have no focus trap, no focus return and, in twelve cases, no
`role="dialog"`.

**Files.** `src/index.css` for the shared ring, a new
`src/components/shared/Modal.tsx`, then every modal listed in the UI/UX document.

**Steps.**
1. Define one `focus-visible` ring token and apply it to the shared control
   classes.
2. Write one `Modal` shell that owns the scrim, `role="dialog"`,
   `aria-modal="true"`, a labelled heading, focus trap, focus return to the
   trigger, and Escape to close. Do not add a dependency for this.
3. Migrate modals one pull request at a time, starting with the destructive ones:
   `ConfirmDialog`, `ChurnOrganizationModal`.
4. Give the Navbar's four icon buttons labels, or remove them if they will stay
   unwired (see C2).

**Tests.** Per modal: Tab cycles inside, Escape closes, focus returns to the
trigger. Use `@testing-library/user-event`.

### F3. Remove the 108 raw hex values

**Files.** `src/pages/integrations/Integrations.tsx` (11, vendor logo SVGs),
`SurveysTab.tsx` (4), and every `text-[#0D0F0E]` on an accent background.

**Steps.** Map each to an existing token. Vendor brand colours inside an SVG are
a legitimate exception: move them into a `VENDOR_BRAND` constant with a comment
explaining why they are not tokens, so the lint sweep stays clean. Add the
on-accent foreground as a real token rather than repeating a literal.

**Done when.** A grep for six-digit hex in `src/**/*.tsx` returns only the
documented vendor constant.

### F4. Real loading skeletons

**Why.** The bar says a skeleton matching the layout. The code shows text lines.
On a slow connection, tables jump.

**Files.** New `src/components/shared/Skeleton.tsx`, then `OrganizationsTable`,
`AccountsTable`, `ContactsTable`, the Health tabs, the Brain panels.

**Steps.** One primitive, then per-surface compositions that match the real row
heights and column widths. Respect reduced motion.

### F5. Close the small rule violations

`h-screen w-screen` in `DashboardLayout` becomes `min-h-[100dvh]`.
`rounded-2xl` and `rounded-3xl` in the contact detail and account placeholder come
back to the scale. `backdrop-blur-sm` leaves the placeholder routes. Delete
`src/App.css`, the `counter` slice and the seeded `tasks` slice, all unimported
template leftovers.

---

## 4. Product gaps

### C1. Slack tab: real or gone

**Why.** The one inline mock left in the app. A Slack connector already exists and
brings in tickets, so the data path is half built.

**Options.** Either render real Slack-sourced records through the connector, or
remove the filter chip. Do not leave a mock on a customer-facing surface. This
needs a product decision first; take it to `brainstorming`.

### C2. Decide on the unwired controls

Navbar Search, Plus, Help and Message; the activity feed's search box, "Add
Action" and filter icon; the list-page title chevrons. Each is either built or
removed. A control that does nothing teaches users not to trust the interface.

### C3. Honour the global attribute mapping

**Why.** An organisation can already choose which column means ARR, MRR, renewal
date and joined date. The setting is stored and exposed, and then every rollup
reads the fixed default. The setting currently lies.

**Files.** `services/customers/portfolio.py`, `forecast.py`, `usage.py`,
`segments.py`, `activity_tracking.py`, `product_usage.py`,
`services/metrics/registry.py`.

**Steps.** Resolve the mapping once per request into a field name, and have the
rollups read through it. Add one helper rather than six branches. If this is not
worth doing, remove the setting instead; either is better than the present state.

**Tests.** A rollup test per metric under a non-default mapping.

### C4. Move the Ops agent's assignee rule into code

**Why.** "Match the function, else the owner" is described to the model in a
prompt. The model can ignore it, and there is no test that catches it.

**Files.** `services/metrics/proposals.py`.

**Steps.** Compute the assignee on approval from the account's team and the
proposal's function, rather than trusting the answer. Keep the prompt hint, but
validate like every other field already is.

### C5. Survey delivery

Surveys are logged by hand today. Sending them, collecting a response and
attributing it needs a per-respondent model. This is architectural: take it
through `brainstorming` and a written spec before any code.

### C6. Notifications for the rest of the assignees

Task, ticket, risk and opportunity assignees are plain text and notify nobody.
The `Notification` model and both transports already exist; this is wiring, plus
the decision of which events deserve a notification.

### C7 to C9. The long tail

Remaining scenario actions (Assign Playbook, Slack, Teams, Create Pipeline, Send
Survey, Schedule), webhook events beyond `customer.created`, and connector
providers beyond the five implemented. Each follows the same pattern as an
existing sibling, so each is a well-scoped day of work rather than a design
problem. Take them in the order customers ask for them.

---

## 5. The centerpiece: Copilot M3

The product owner calls Multiplayer Copilot the crux of the product. Phases M0
through 2b are built. What remains is the agent itself.

### D1. Streaming responses

**Why.** Every send is one blocking call. In a live session, several people watch
a spinner together, which is the worst possible shared experience.

**Files.** `services/copilot/anthropic_client.py`, `services/copilot/views.py`,
`services/copilot/consumers.py`, `src/pages/copilot/ChatView.tsx`.

**Steps.** Stream from the provider and push deltas over the existing session
socket rather than inventing a second transport. Keep the REST endpoint working
unchanged for clients that do not stream. Record the `ModelCall` on completion as
now.

**Tests.** A consumer test asserting deltas arrive in order and a final message is
persisted once. Assert a dropped socket does not duplicate the stored message.

### D2. Multi-step tool-calling agent

**Why.** Today the model gets one shot with whatever retrieval found. It cannot
look something up, then decide, then look again. This is the difference between a
chat box and an agent.

This is architectural. Follow the full process: `brainstorming`, then a written
spec, then `writing-plans` for its own plan. Do not start from this paragraph.

Constraints already fixed by the existing system, and not up for renegotiation in
the design:
- Every tool call must respect the same visibility helpers as the REST layer. An
  agent that reads more than its user can is a data breach, not a feature.
- Every call goes through `anthropic_client.get_completion` so budgets and the
  `ModelCall` audit keep working. A multi-step agent will consume budget far
  faster; model the cost before building.
- Answers must stay citable. Sources are a product principle.
- Redirects mid-run must still work, because that is what makes a session
  multiplayer.

### D3. Ephemeral presence

"Who is looking right now", beyond the `SessionParticipant` rows. Small, and it
makes a live session feel live. Deliberately deferred so far.

---

## 6. Suggested order

| Wave | Tasks | Rationale |
|---|---|---|
| 0 | P0 | Nothing merges or deploys until CI can run |
| 1 | A1, A2, A3, B4 | Stop showing wrong data and stop documents lying. Cheap, high trust |
| 2 | F1, F2 | The two changes that most alter how the product feels |
| 3 | B1, B2, B5 | The compliance gaps with real risk behind them |
| 4 | F3, F4, F5, C1, C2 | Finish the design system sweep and the honesty pass |
| 5 | C3, C4, C6 | Make existing settings and rules true |
| 6 | D1 | Streaming, the largest felt improvement to the centerpiece |
| 7 | B3, C5, C7 to C9 | Operational and long tail |
| 8 | D2, D3 | The agent, once its own spec exists |

Each wave is a set of independently shippable pull requests, not a milestone to
batch.
