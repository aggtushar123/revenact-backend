# Revenact product documents

Six reference documents describing Revenact as it exists, kept beside the code so
they stay honest. Each was written from the two application repositories and the
deployment repository, not from memory: claims name the file, model, endpoint or
route they come from.

Each document lives next to the code it describes, because documents rot when
they sit far from their subject. Four are here; two are in the frontend.

| # | Document | Repository | Answers |
|---|---|---|---|
| 1 | [PRD](01-prd.md) | backend | Who Revenact is for, what it solves, what is built, what is next |
| 2 | [TRD](02-trd.md) | backend | Architecture, stack, security, deployment, non-functional requirements |
| 3 | [UI/UX Design](../../../react-ts-app/docs/03-ui-ux-design.md) | frontend | Design system, components, interaction rules, accessibility bar, design debt |
| 4 | [App Flow](../../../react-ts-app/docs/04-app-flow.md) | frontend | Every route and user journey, with the files and endpoints involved |
| 5 | [Backend Schema](05-backend-schema.md) | backend | Every model, field, relation, constraint and visibility rule |
| 6 | [Implementation Plan](06-implementation-plan.md) | backend | The work ahead as bite-sized tasks with tests |

## Relationship to the other documents in these repositories

- [`docs/API_CONTRACTS.md`](../API_CONTRACTS.md) is the wire-level contract for
  every endpoint. Document 5 describes the storage beneath it; document 4
  describes the frontend above it.
- `.agents/workflows/repo-architecture.md` in each repository is the agent-facing
  map of that one repository. These six are the product-level view across both.
  **Both are currently stale**; see task A2 in the plan.
- [`.soc2/CONTROL_MAP.md`](../../.soc2/CONTROL_MAP.md) is the compliance evidence.
  Document 2 summarises the posture; the control map is the authority.
- [`docs/data-classification.md`](../data-classification.md) and
  [`docs/audit-events.md`](../audit-events.md) are referenced rather than
  duplicated.

## Conventions

- Status words: **Built** (in main, wired end to end), **Partial**, **Backend
  only**, **Planned**.
- File paths are relative to the repository named in the same sentence.
- Dates are absolute, `YYYY-MM-DD`.
- When a document and the code disagree, the code is right and the document is a
  bug. Fix it in the same pull request.

## Keeping them current

| Document | Update when |
|---|---|
| PRD | A product decision changes, or a feature ships or is cut |
| TRD | The stack, an integration, a scheduled job or the deployment changes |
| UI/UX Design | A token, pattern or surface changes; a design review lands |
| App Flow | A route, flow or guard changes |
| Backend Schema | Any migration |
| Implementation Plan | A task is finished, added or re-ordered |

## Skills that maintain them

| Skill | Repository | Used for |
|---|---|---|
| `brainstorming`, `writing-plans` | backend | Turning a PRD change into plan tasks |
| `flow-docs`, `api-contracts` | backend | Keeping documents 4 and 5 in step with the workflow notes and the API contract |
| `revenact-design`, `impeccable`, `ui-ux-pro-max` | frontend | Document 3's rules and review checklist |
| `soc2-dev` | both | The TRD's security and compliance sections |
| `playwright-cli` | frontend | Verifying App Flow steps against the running app |
| `graphify` | backend | Regenerating a dependency picture for the TRD |
