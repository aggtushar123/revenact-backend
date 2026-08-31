---
name: api-contracts
description: How to keep docs/API_CONTRACTS.md in sync whenever an endpoint is added or changed. Use whenever creating or modifying a Django view/serializer/URL in this backend.
---

# API Contracts

`docs/API_CONTRACTS.md` is the narrative companion to the auto-generated
OpenAPI schema (drf-spectacular, served at `/api/schema/`, `/api/docs/`,
`/api/redoc/`). The schema is the machine-readable source of truth for
*shape*; this doc explains *why* — decisions the schema can't carry, and
how each endpoint maps to the `react-ts-app` frontend it serves.

**Every time an endpoint is added, removed, or its shape changes, update
this file in the same change.** A PR that adds a view without touching
`docs/API_CONTRACTS.md` is incomplete.

## Existing conventions (already established — follow, don't reinvent)

- Base path `/api/v1/`, JSON only.
- List responses paginated via DRF `PageNumberPagination`, 25/page:
  `{ "count", "next", "previous", "results" }`.
- Auth is `AllowAny` on every endpoint until the real auth feature (mirroring
  `authSlice.ts`) is ported — call this out explicitly on new endpoints, don't
  silently assume it.
- Errors: DRF's default shape — `{"detail": "..."}` or
  `{"field_name": ["..."]}`.
- IDs: `BigAutoField` unless a feature has a specific reason otherwise
  (document the reason inline if so).
- Timestamps: ISO 8601 UTC (`USE_TZ = True`).

## Workflow for a new feature (matches README's "Workflow for adding a feature")

1. `python manage.py startapp <name>`; models mirror the relevant frontend
   TS interfaces / `src/**/*Data.ts` mock files in `react-ts-app` — derive
   field names/types from there, don't copy the mock data verbatim.
2. Serializers + views + URLs, mounted at `/api/v1/<name>/` in `config/urls.py`.
3. Update `docs/API_CONTRACTS.md`:
   - Flip the row in the **Status** table from ⏳ to ✅ (or add a new row).
   - Add a `## <app_name> — <Frontend feature name>` section using the
     template at the bottom of the file: mirrors line, `### Models`, then
     `### <METHOD> /api/v1/...` per endpoint with auth + request/response
     shapes, and a note on any place the backend shape deviates from the
     frontend's current TS interface and why.
4. Generate + apply migrations.
5. Sanity-check the live schema: `GET /api/schema/` (or hit `/api/docs/`)
   matches what you just wrote in prose.
6. Add unit + integration + end-to-end tests for the endpoint — see the
   `testing` skill. Not optional; a feature without all three isn't done.

Commit as `feat(<app>): ...` for the endpoint plus `docs: update
API_CONTRACTS.md with <app> endpoints` (or squash both into one `feat(<app>)`
commit if the change is small) — see the `commit-messages` skill.
