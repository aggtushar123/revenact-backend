---
name: flow-docs
description: How to write and maintain this repo's .agents/workflows/*.md flow docs, mirroring the convention already established in the react-ts-app frontend. Use when the repo structure changes materially, or a cross-cutting flow (auth, a background job, etc.) is added.
---

# Flow Docs

`react-ts-app` keeps agent-facing knowledge files in `.agents/workflows/`
(`repo-architecture.md`, `auth-flow.md`, ...) instead of letting that
knowledge rot in comments or PR descriptions. This backend mirrors the same
convention in its own `.agents/workflows/` so both repos are equally legible
to an agent (or a new engineer) with no code archaeology required.

## Two kinds of doc

1. **`repo-architecture.md`** — the single always-current map of the whole
   backend: stack, directory layout, app list, URL map, data flow. One file,
   updated in place as the repo grows (never forked into v2/v3 copies).
2. **`<feature>-flow.md`** — one file per cross-cutting flow that isn't just
   "a CRUD app" (e.g. `auth-flow.md`, `webhook-flow.md`). Write one when a
   feature has a multi-step sequence worth tracing end-to-end, not for every
   Django app.

## Format

Every file starts with frontmatter and a title:

```markdown
---
description: One line — what this doc covers.
---

# Title
```

`repo-architecture.md` sections, in order: Tech Stack, Directory Map, URL
map, App list (one subsection per Django app: models, endpoints, status),
Data Flow Summary, "Adding a New Feature — Checklist".

`<feature>-flow.md` sections, in order: Overview, the flow itself as a
numbered list, a "Key Files" table (`| File | Purpose |`). Keep it as
concrete as `react-ts-app/.agents/workflows/auth-flow.md` — actual file
paths and function/endpoint names, not abstract description.

## When to update (not just add)

Treat these as living documents, not changelogs:
- New Django app → add its subsection to `repo-architecture.md`, update the
  directory map.
- New cross-cutting flow → new `<feature>-flow.md`.
- Existing flow changes shape (e.g. auth moves from session to JWT) → edit
  the existing file in place; don't leave the old description stale.

Commit these under `docs(...)` per the `commit-messages` skill — e.g.
`docs: update repo-architecture.md with organizations app`.

## Relationship to API_CONTRACTS.md

Flow docs describe *how the system is wired* (files, modules, sequences).
`docs/API_CONTRACTS.md` describes *what the API looks like on the wire*
(endpoints, request/response shapes). A new feature usually touches both —
see the `api-contracts` skill.
