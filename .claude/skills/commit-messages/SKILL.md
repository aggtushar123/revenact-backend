---
name: commit-messages
description: Conventional-commit message format used across the Revenact monorepo (this backend and react-ts-app). Use whenever composing a git commit message here.
---

# Commit Messages

This repo follows the same [Conventional Commits](https://www.conventionalcommits.org/)
style already established in `react-ts-app`'s history — keep both repos
consistent.

## Format

```
<type>(<scope>): <subject>
```

- **type** — one of:
  | Type | When |
  |---|---|
  | `feat` | New endpoint, model, or capability |
  | `fix` | Bug fix |
  | `docs` | `docs/API_CONTRACTS.md`, `README.md`, `.agents/workflows/*.md`, or comment-only changes |
  | `chore` | Scaffolding, deps, config, CI, non-feature housekeeping |
  | `refactor` | Internal restructuring with no behavior change |
  | `test` | Adding/updating tests only |
- **scope** — the Django app the change lives in (`core`, `organizations`,
  `accounts`, `auth`, `pipelines`, ...), or omitted for repo-wide changes.
- **subject** — imperative mood, lowercase, no trailing period.
  `add health check endpoint`, not `Added health check endpoint.`

## Examples (from this monorepo's actual history)

```
feat: scaffold Django + DRF backend
feat(organizations): add list/detail endpoints and serializers
fix(auth): correct refresh-token expiry check
docs: update API_CONTRACTS.md with organizations endpoints
chore: bump drf-spectacular to 0.31.0
```

## Body (optional)

For anything non-trivial, add a blank line then a short body explaining
*why*, not a restatement of the diff. Skip it for one-line scaffolding/chore
commits.

## What NOT to do

- Don't bundle unrelated changes (a feature + an unrelated fix) into one commit.
- Don't write vague subjects like "update code" or "fixes".
- Don't skip the scope when a change is clearly confined to one app.
