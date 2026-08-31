---
name: testing
description: Testing requirement for this backend - every feature ships with unit, integration, and end-to-end tests. Use whenever a feature (app, endpoint, model) is added or changed.
---

# Testing

**No feature is done until it has unit, integration, and end-to-end tests.**
All three use Django's built-in test tooling (`django.test`,
`rest_framework.test`) — no new test dependency has been added, so nothing
here requires `pip install` beyond what's already in `requirements.txt`.

## The three tiers

### 1. Unit — one function/method, fully isolated

Tests a single piece of logic with no DB and no HTTP: a model method/property,
a serializer's field-level `validate_*`, a plain utility function. Mock any
external boundary.

- Base class: `django.test.SimpleTestCase` (no DB) or plain `unittest.TestCase`.
- Location: `<app>/tests/test_<unit>.py`.

### 2. Integration — one endpoint, through the real stack

Tests a view through the full internal chain — URLconf → view → serializer →
model → **real test database** — the way `docs/API_CONTRACTS.md` describes
it. External third-party services (if any are ever added) get mocked; the
DB does not.

- Base class: `rest_framework.test.APITestCase`, calls via `self.client`.
- Assert: status code + response shape match `docs/API_CONTRACTS.md` exactly
  (field names, pagination envelope, error shape).
- Location: `<app>/tests/test_views.py`.

### 3. End-to-end — a full flow, over the wire, no browser

Tests a realistic multi-step scenario as an actual client would experience
it: real HTTP round trips against a real running server thread and a real
test database, spanning more than one call (create → list → update →
delete; or a flow that crosses more than one app).

- Base class: `django.test.LiveServerTestCase` — starts a real server on
  `self.live_server_url`; hit it with a real HTTP client (stdlib
  `urllib.request` — no new dependency) against that URL. `self.client`
  and `rest_framework.test.APIClient` don't work here: both call straight
  into the WSGI handler in-process and never touch the socket, which
  defeats the point of this tier.
- This is **API-level e2e, no browser** — it proves the real HTTP/DB path
  works end to end without needing a UI driver. (`react-ts-app` has its own
  `testing` skill for the frontend side.)
- Location: top-level `e2e/` package (flows usually cross app boundaries),
  e.g. `e2e/test_organizations_flow.py`. Create `e2e/__init__.py` the first
  time this tier is used.

## Workflow

Per the `api-contracts` skill's feature workflow, testing is not a separate
step tacked on at the end — write tests alongside each layer as you build
it:

1. Model/serializer logic → unit tests.
2. View wired up → integration test hitting it via `APITestCase`.
3. Feature complete (spans create/read/update, or multiple apps) → one
   end-to-end flow test in `e2e/`.

Run everything with `python manage.py test` before committing (see the
`commit-messages` skill — a `feat(<app>)` commit includes its tests, not a
separate `test(<app>)` follow-up, unless tests are the *only* thing changing).

## What NOT to do

- Don't skip a tier because "it's simple" — a one-field model still gets a
  unit test on its serializer validation.
- Don't fake integration coverage by testing the serializer alone and
  calling it done — integration means through `self.client`, hitting the URL.
- Don't reach for mocking in the e2e tier — the point is the real DB and
  real HTTP path, unmocked.
