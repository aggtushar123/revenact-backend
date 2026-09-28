# Contacts redesign, backend (delivery 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every call gets a sentiment or an honest "not analysable", read from its transcript, else its summary, else its title, the moment it is created and nightly on the server; a contact's sentiment rests only on their own tenant's records; and the Contacts page gets a filtered, summarised list plus a per-person history in which every row follows its own record rule.

**Architecture:** One helper, `services/customers/calls.classify_call(call, *, transcript_text=None, user=None)`, runs on every call create path. It marks a call with nothing to read as not analysable (new `Call.not_analysable`, migration `0048`), else classifies it through `classify_records`, and it never raises. `classify_interactions` becomes the nightly job: one tenant at a time, metered to that tenant, stopping cleanly at a tenant's budget, and recomputing contacts per batch. The revenact-infra scheduler runs it before `run_health_maintenance`. A new `contact_history.py` builds `GET /contacts/<id>/history/` under the twice-filter, and `ContactListView` gains filters, `{id, name}` refs and a `summary`.

**Tech Stack:** Django 5, DRF, PostgreSQL, `TestCase`/`SimpleTestCase`/`APITestCase`, `LiveServerTestCase` for e2e. The model is always stubbed in tests (`classify_batch` or `get_completion` patched). Docker Compose for the infra check.

**Spec:** `react-ts-app/docs/superpowers/specs/2026-09-28-contacts-redesign-design.md`. §1 "Every call gets a sentiment (backend)", §2 "Contact sentiment (backend)", §6 "Delivery" item 1 (the backend PR and the schedule in `revenact-infra`) and §7 "Testing", Backend, are binding. §3–§5 are the frontend PR and delivery 2, not this plan.

## Global Constraints

- **When** (spec §1): "A call is classified when it is created: hand-logged, logged from "+ Add", or synced by a connector. Every path that creates a call calls one shared helper. A failure never blocks the call being saved."
- **What it reads** (spec §1): "In order: the transcript text (capped as today), then the summary, then the title. The classifier input for a call is built from the best text available."
- **Nothing to read** (spec §1): "A call whose only text is a generic title, and which has no summary or transcript, is marked as not analysable: `ai_classified_at` is set and sentiment stays empty, with a flag or state the UI can read as "Not enough to analyse". It is never guessed."
- **Nightly** (spec §1): "`classify_interactions` runs nightly on the server, with a per-run limit and scoped to calls, emails and tickets. It is added to the deploy's schedule in `revenact-infra`, next to `run_health_maintenance`. Spend is metered against the workspace's AI credits as today, and stops cleanly when the budget runs out."
- **Recompute** (spec §1): "Classifying a call recomputes everyone on it (`recompute_for_records`), which already happens."
- **The mix** (spec §2): "The mix stays as it is (owner decision): calls, then tickets, then emails, weighted by recency, with the same thresholds." `KIND_WEIGHT`, `recency_weight`, `POSITIVE_ABOVE` and `NEGATIVE_BELOW` do not change.
- **History** (spec §2): "`GET /api/v1/contacts/<id>/history/` returns their calls newest first. Each call has sentiment or "not analysable", summary, AI classification, duration, host, organisation and account (id and name), and a link. The response also has their emails and tickets, with sentiment where analysed." "Every row follows its own record rule: `visible_emails` (the owner-only mail rule), ticket departments, and account visibility." "A contact the viewer cannot open is a 404."
- **List** (spec §2): "The existing list endpoint gains these filters: organisation (`customer`), `account`, `sentiment` and `role`, plus search. Each row carries its organisation and account (id and name), its sentiment, its evidence counts and `last_contacted_at`. A summary (counts by sentiment and decision makers) covers the whole filtered set. The query count is pinned."
- **Twice-filter rule** (memory, pylon-roadmap; #67, #68, #70): every read of record text is filtered by company and then by the record's own rule (`services/mail/visibility.py`, `services/customers/personal.py`).
- **Delivery** (spec §6): "A backend PR (§1, §2, and the schedule in `revenact-infra`), then a frontend PR (§3, §5)." "The backend merges and deploys before its frontend." The infra change deploys after the backend (Task 6).
- Every endpoint change updates `docs/API_CONTRACTS.md` in the same PR (`.claude/skills/api-contracts`), with `docs/product/01-prd.md`, `docs/product/05-backend-schema.md` and `docs/data-classification.md`.
- Tests come in three tiers (`.claude/skills/testing`): unit, integration and e2e (`e2e/`). Run one module with `venv/bin/python manage.py test <label> --noinput`.
- `venv/bin/ruff check .` and `venv/bin/ruff format --check .` must pass. **ruff formats Python inside Markdown fences** (this plan included), so every Python block in a `.md` file must already be ruff-formatted. The doc edits in this plan use only `text` and `json` fences.
- Every object-level visibility check carries `# SOC2:AUTH-02`.
- Commits are conventional (`feat(contacts): …`) and end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Backend work is on `feat/contacts-redesign` (off `main` `e1f1374`) in `revenact-backend`, with `venv/`. Infra work is on `feat/nightly-classification` (off `main`) in `revenact-infra`. Do not switch the backend branch.

## Pre-flight: where the spec meets the code

| # | Spec says | Code has | Resolution |
|---|---|---|---|
| 1 | "Every path that creates a call calls one shared helper", including "synced by a connector" | One production path creates a `Call`: `_CallListView.perform_create` (`services/customers/views.py`), serving both `+ Add` routes (`/customers/<id>/calls/` and `/customers/<id>/accounts/<id>/calls/`). No connector syncs calls: `Connector.Provider.ZOOM` exists but has no sync, and `connectors/sync.py` creates tickets, already classified by `classify_records`. `seed_demo_calls` is demo data. | **Decision:** the helper is `calls.classify_call(call, *, transcript_text=None, user=None)`, replacing today's `classify_call(call)`. `perform_create` passes the request's transcript and user. A guard test (`OneCreatePathTests`) fails when any non-test, non-seed module creates a `Call` without being listed, so a future recorder connector has to call the helper. |
| 2 | "the transcript text (capped as today), then the summary, then the title" | `_text_for` sends `"{title}. {summary}"` or the title. `build_prompt` caps every record at `MAX_TEXT_CHARS` (600). A pasted transcript is never stored; an uploaded one is an `Attachment`. | **Decision:** a call's text is its title, then `". "` and the best body: the transcript, else the summary. With neither, it is the title alone. The title stays as a short prefix because it is the cheapest context and the last fallback anyway. "Capped as today" is the classifier's own cap, `MAX_TEXT_CHARS`, unchanged. The transcript is the one handed over by the creating request (`call._transcript_text`), else the stored file. An unreadable file reads as no transcript. |
| 3 | "`ai_classified_at` is set and sentiment stays empty, with a flag or state the UI can read" | `AIClassified.sentiment` is a non-null choice defaulting to `neutral`. The prompt tells the model to omit a record "too thin to place", and `classify_records` leaves an omitted row pending, so the nightly pass pays for it again every night. | **Decision:** a new `Call.not_analysable` boolean (default `False`, migration `0048_call_not_analysable`, a safe add), and an `analysis` property on every `AIClassified` row: `pending`, `not_analysable` or `analysed`. APIs send `sentiment: null` unless `analysed`. A call is marked when it has no transcript, no summary and a generic title (every word in `calls.GENERIC_TITLE_WORDS`), **without** a model call, and also when the model declines it (on create and nightly), because a declined call has nothing to judge either. Emails and tickets keep today's behaviour. A later reading clears the mark. |
| 4 | "Contact sentiment follows immediately … which already happens" | True for `classify_records` (create paths, mail sync, ticket sync). **Not** true for `classify_interactions`, which calls `apply_classification` and never `recompute_for_records`, so nightly readings move no one until `run_health_maintenance`. | The command recomputes the contacts on each batch (Task 5). |
| 5 | "Spend is metered against the workspace's AI credits as today, and stops cleanly when the budget runs out" | Without `--org-email`, the command batches every tenant's rows together, and `classify_batch` meters each batch to its **first** record's organisation. `BudgetExceeded` raises `CommandError`, stopping every tenant. | **Decision:** records are gathered and batched per organisation and metered to it. An organisation out of budget is skipped for the night and named in the output, the others carry on, and the command exits 0. `--limit` keeps its meaning (records per run), taken from the tenants in turn so one backlog cannot starve the rest. A missing model configuration still stops the run (`CommandError`), as today. |
| 6 | "It is added to the deploy's schedule … next to `run_health_maintenance`" | The `scheduler` service runs `run_health_maintenance` daily and has no media volume, so it could not read a stored transcript. | Task 6: the loop runs `classify_interactions --limit 300` before `run_health_maintenance` (so the health job reads fresh sentiment), and mounts `media_files` read-only. It deploys with `deploy/sync.sh` after the backend PR is live. |
| 7 | "This replaces or extends today's contact interactions endpoint. The plan confirms which." | `GET /contacts/<id>/interactions/` lists every email from the address with no mailbox rule, every ticket with no department rule, and calls on accounts the viewer cannot open. The deployed `/contacts/:id` page calls it. | **Decision:** a new `GET /contacts/<id>/history/` is the endpoint the new page uses. `/interactions/` is kept for the deployed frontend, now under the same rules as `/history/`, and is removed when the frontend PR drops its only caller. |
| 8 | History rows carry "organisation and account (id and name), and a link" | The story items (`services/organizations/story/items.py`) already define `clip` and `safe_url` and a `link` object. | Rows carry `organisation` and `account` as `{id, name}` or `null`. An account's organisation is the first linked one the viewer may open. `link` is `{url}` (an `http(s)` recording or ticket URL, else `null`) or `{thread_id}` for an email. Each list holds the newest 100 (`HISTORY_LIMIT`), and `counts` gives the visible totals. |
| 9 | List filter "organisation (`customer`)" | `ContactListView` filters `?company=`, which the deployed page sends. `ContactSerializer.companies` lists **every** parent organisation, including ones the viewer cannot open. | `?customer=` is the filter, and `?company=` stays as an alias. Unusable values are ignored (the list endpoints' convention). The list passes `visible_customer_ids` to the serializer, so `companies` and the new `organisation` name only organisations the viewer may open. The nested lists are unchanged. |
| 10 | "decision makers" | The frontend already counts `executive_sponsor`, `decision_maker` and `economic_buyer` (`react-ts-app/src/features/organizations/listSummaries.ts`). | `DECISION_ROLES` is that set. |
| 11 | "its evidence counts" | `sentiment_evidence` (`{score, calls, emails, tickets, positive, neutral, negative, latest_at}`) is already on `ContactSerializer`. | Served as is. It holds counts only, never text, across the contact's own tenant. Found while planning: `interactions_for` matched emails and tickets by address across **every tenant**. Task 4 scopes it to the contact's own organisation. |
| 12 | "The query count is pinned" | Existing pins measure after a first warm-up read (the org chart is memoised on the user). | The list is pinned at **5** queries (count, page, prefetch of account parents, visible organisation ids, summary) and the history at **10**, each at two sizes. If a number moves, find out why before changing it. |

## File Structure

| File | Responsibility |
|---|---|
| `services/customers/models.py`, `migrations/0048_call_not_analysable.py` (new) | `Call.not_analysable`; `AIClassified.analysis` |
| `services/customers/classification.py` | `mark_not_analysable`; `apply_classification` clears the mark; `_text_for` reads `calls.call_text`; `classify_records` marks a declined call |
| `services/customers/calls.py` | `GENERIC_TITLE_WORDS`, `is_generic_title`, `transcript_text_of`, `call_text`, `has_something_to_read`, `organisation_of_call`, and the one helper `classify_call` |
| `services/customers/views.py` | `perform_create` runs the helper; `ContactListView` filters, context and `summary`; `_visible_contact`, `ContactHistoryView`; `ContactInteractionsView` under the record rules |
| `services/customers/serializers.py` | `CallSerializer.analysis`; `ContactSerializer.organisation`, `.account` and visible-only `companies` |
| `services/customers/contact_sentiment.py` | `organisation_id_of`, `in_organisation_q`; evidence inside the contact's tenant, not-analysable calls left out |
| `services/customers/contact_history.py` (new) | `HISTORY_LIMIT`, `history_querysets`, `build_history` |
| `services/customers/management/commands/classify_interactions.py` | per-tenant batches and metering, clean budget stop, round-robin cap, not-analysable calls, recompute per batch |
| `config/urls.py` | `api/v1/contacts/<int:pk>/history/` |
| `services/customers/tests/test_call_state.py` (new) | Task 1, plus `CallFixture` shared by Tasks 2 and 3 |
| `services/customers/tests/test_call_text.py` (new) | Task 2 |
| `services/customers/tests/test_call_classify.py` (new) | Task 3, including the create-path guard |
| `services/customers/tests/test_contact_sentiment.py` | Task 4 (`EvidenceScopeTests`) |
| `services/customers/tests/test_nightly_classification.py` (new) | Task 5 |
| `services/customers/tests/test_contact_history.py` (new) | Task 7, with `blind_to_one_account` |
| `services/customers/tests/test_contact_list.py` (new) | Task 8 |
| `e2e/test_contacts_flow.py` (new) | Task 9 |
| `docs/API_CONTRACTS.md`, `docs/product/01-prd.md`, `docs/product/05-backend-schema.md`, `docs/data-classification.md` | Task 9 |
| `revenact-infra`: `deploy/docker-compose.prod.yml`, `README.md` | Task 6 |

Shared helpers already in the repo: `services.customers.tests.test_views.blind_to_one_account(customer)` returns `(viewer, seen, hidden)`. The viewer opens the customer and its Seen account but not its Hidden one, which the customer's owner (`customer.owner`, "the colleague") owns. `services.customers.tests.test_views.create_account` links a new account to a customer.

---

### Task 1: A call can be "not analysable"

**Files:**
- Modify: `services/customers/models.py` (`AIClassified`, `Call`)
- Create: `services/customers/migrations/0048_call_not_analysable.py`
- Modify: `services/customers/classification.py` (`apply_classification`, new `mark_not_analysable`)
- Modify: `services/customers/serializers.py` (`CallSerializer`)
- Test: `services/customers/tests/test_call_state.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Call.not_analysable: bool`; `AIClassified.analysis -> "pending" | "not_analysable" | "analysed"`; `classification.mark_not_analysable(call) -> None`; `CallSerializer` field `analysis` (read-only); `tests.test_call_state.CallFixture` (`self.org`, `self.carl` admin, `self.pizza`, `self.emea`, `self.call(title=..., summary=..., **kw)`, `self.transcript(text, name=...)`) and `tests.test_call_state.WHEN`.

- [ ] **Step 1: Write the failing test**

**Create** `services/customers/tests/test_call_state.py`:

```python
"""A call is `pending`, `analysed` or `not_analysable`: read with nothing to
judge, stamped so no pass pays to look again, and never guessed."""

from datetime import datetime
from datetime import timezone as dt_timezone

from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers.classification import apply_classification, mark_not_analysable
from services.customers.models import Account, Attachment, Call, Customer

WHEN = datetime(2026, 9, 20, 10, 0, tzinfo=dt_timezone.utc)


class CallFixture(APITestCase):
    """Carl, an admin at Acme, with Pizza Hut and its EMEA account. Shared by
    the call tests of this delivery; it has no tests of its own."""

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme")
        self.carl = User.objects.create_user(
            email="carl@acme.io",
            password="x",
            name="Carl",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.pizza = Customer.objects.create(organisation=self.org, name="Pizza Hut")
        self.emea = Account.objects.create(name="EMEA")
        self.emea.customers.add(self.pizza)

    def call(self, title="Renewal readiness", summary="", **kw):
        kw.setdefault("customer", self.pizza)
        return Call.objects.create(
            title=title, summary=summary, host_name="Carl", occurred_at=WHEN, **kw
        )

    def transcript(self, text, name="qbr.txt"):
        return Attachment.objects.create(
            organisation=self.org,
            customer=self.pizza,
            file=SimpleUploadedFile(name, text.encode(), content_type="text/plain"),
            name=name,
            content_type="text/plain",
            size=len(text),
            source=Attachment.Source.TRANSCRIPT,
        )


class NotAnalysableTests(CallFixture):
    def test_a_new_call_is_pending(self):
        call = self.call()
        self.assertEqual((call.analysis, call.not_analysable), ("pending", False))

    def test_marking_stamps_it_and_blanks_the_reading(self):
        call = self.call(
            title="Sync", sentiment="negative", ai_category="onboarding", ai_area="customer_success"
        )
        mark_not_analysable(call)
        call.refresh_from_db()
        self.assertEqual(call.analysis, "not_analysable")
        self.assertIsNotNone(call.ai_classified_at)
        self.assertEqual((call.sentiment, call.ai_area, call.ai_category), ("neutral", "", ""))

    def test_a_reading_is_analysed(self):
        call = self.call()
        apply_classification(call, {"sentiment": "positive", "ai_category": "onboarding"})
        self.assertEqual(call.analysis, "analysed")

    def test_a_later_reading_clears_the_mark(self):
        call = self.call()
        mark_not_analysable(call)
        apply_classification(call, {"sentiment": "positive", "ai_category": "onboarding"})
        call.refresh_from_db()
        self.assertEqual((call.analysis, call.sentiment), ("analysed", "positive"))

    def test_the_calls_list_says_how_each_call_was_read(self):
        mark_not_analysable(self.call(title="Sync"))
        self.call(title="QBR")
        self.client.force_authenticate(self.carl)
        rows = self.client.get(f"/api/v1/customers/{self.pizza.id}/calls/").data
        self.assertEqual(
            {r["title"]: r["analysis"] for r in rows}, {"Sync": "not_analysable", "QBR": "pending"}
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python manage.py test services.customers.tests.test_call_state --noinput`
Expected: ERROR, `ImportError: cannot import name 'mark_not_analysable'`.

- [ ] **Step 3: Add the field and the `analysis` property**

**Find** in `services/customers/models.py`:

```text
    @property
    def is_classified(self):
        return self.ai_classified_at is not None
```

**Replace with:**

```text
    @property
    def is_classified(self):
        return self.ai_classified_at is not None

    @property
    def analysis(self):
        """`pending` (nothing has read it yet), `not_analysable` (read, and
        there was nothing to judge — only a call can be), or `analysed`.
        `sentiment` means something only when this is `analysed`."""
        if getattr(self, "not_analysable", False):
            return "not_analysable"
        if self.ai_classified_at is None:
            return "pending"
        return "analysed"
```

**Find** in `services/customers/models.py`:

```text
    recording_url = models.URLField(max_length=500, blank=True, default="")
    # Who from the customer's side was on the call. The call's sentiment
```

**Replace with:**

```text
    recording_url = models.URLField(max_length=500, blank=True, default="")
    not_analysable = models.BooleanField(
        default=False,
        help_text="Looked at, with nothing to judge: no transcript, no summary and a "
        "generic title, or the model declined it. `ai_classified_at` is set so no "
        "pass pays to retry it, and `sentiment` is not evidence of anything.",
    )
    # Who from the customer's side was on the call. The call's sentiment
```

- [ ] **Step 4: Add the migration**

Run `venv/bin/python manage.py makemigrations customers -n call_not_analysable` and then `venv/bin/ruff format services/customers/migrations/0048_call_not_analysable.py`. The file must read as follows (the generated date line may differ):

**Create** `services/customers/migrations/0048_call_not_analysable.py`:

```python
# Generated by Django 5.2.17 on 2026-09-28 14:14

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0047_translation_and_contact_language"),
    ]

    operations = [
        migrations.AddField(
            model_name="call",
            name="not_analysable",
            field=models.BooleanField(
                default=False,
                help_text="Looked at, with nothing to judge: no transcript, no summary and a generic title, or the model declined it. `ai_classified_at` is set so no pass pays to retry it, and `sentiment` is not evidence of anything.",
            ),
        ),
    ]
```

A boolean with a default is a safe add on PostgreSQL: existing calls read `False`, which is right, because none of them has been marked.

- [ ] **Step 5: Mark, and clear the mark on a reading**

**Find** in `services/customers/classification.py`:

```text
    for field, value in fields.items():
        setattr(record, field, value)
    record.ai_classified_at = timezone.now()
    record.save(update_fields=[*fields, "ai_classified_at"])
    interaction_classified.send(sender=type(record), record=record)
```

**Replace with:**

```text
    for field, value in fields.items():
        setattr(record, field, value)
    record.ai_classified_at = timezone.now()
    extra = []
    if getattr(record, "not_analysable", False):
        # Read now, so no longer "nothing to judge".
        record.not_analysable = False
        extra.append("not_analysable")
    record.save(update_fields=[*fields, "ai_classified_at", *extra])
    interaction_classified.send(sender=type(record), record=record)


def mark_not_analysable(call):
    """A call with nothing to judge: its taxonomy blanked, its sentiment back
    to the default (which `analysis` says is not a reading), and stamped, so
    no scheduled pass pays to look at it again. Never guessed, and no
    `interaction_classified` signal: nothing was learned."""

    call.ai_area = ""
    call.ai_category = ""
    call.ai_subcategory = ""
    call.sentiment = call.Sentiment.NEUTRAL
    call.not_analysable = True
    call.ai_classified_at = timezone.now()
    call.save(
        update_fields=[
            "ai_area",
            "ai_category",
            "ai_subcategory",
            "sentiment",
            "not_analysable",
            "ai_classified_at",
        ]
    )
```

- [ ] **Step 6: Serve `analysis` on every call**

**Find** in `services/customers/serializers.py`:

```text
            "sentiment",
            "ai_area",
            "ai_category",
            "recording_url",
```

**Replace with:**

```text
            "sentiment",
            "analysis",
            "ai_area",
            "ai_category",
            "recording_url",
```

**Find** in `services/customers/serializers.py`:

```text
        read_only_fields = ["sentiment", "ai_area", "ai_category", "links", "created_at"]
        extra_kwargs = {"summary": {"required": False, "allow_blank": True}}
```

**Replace with:**

```text
        read_only_fields = [
            "sentiment",
            "analysis",
            "ai_area",
            "ai_category",
            "links",
            "created_at",
        ]
        extra_kwargs = {"summary": {"required": False, "allow_blank": True}}
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.customers.tests.test_call_state services.customers.tests.test_files_and_calls --noinput`
Expected: PASS (5 new tests; the CallSense tests, including the pinned 3-query list, are unchanged).

- [ ] **Step 8: Commit**

```bash
git add services/customers/models.py services/customers/migrations/0048_call_not_analysable.py services/customers/classification.py services/customers/serializers.py services/customers/tests/test_call_state.py
git commit -m "feat(contacts): a call can be not analysable" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: What the classifier reads for a call

**Files:**
- Modify: `services/customers/calls.py`
- Modify: `services/customers/classification.py` (`_text_for`)
- Test: `services/customers/tests/test_call_text.py`

**Interfaces:**
- Consumes: `tests.test_call_state.CallFixture` (Task 1).
- Produces: `calls.GENERIC_TITLE_WORDS: frozenset[str]`; `calls.is_generic_title(title: str) -> bool`; `calls.transcript_text_of(call) -> str` (reads `call._transcript_text` when set, else the stored transcript file); `calls.call_text(call) -> str`; `calls.has_something_to_read(call) -> bool`.

- [ ] **Step 1: Write the failing test**

**Create** `services/customers/tests/test_call_text.py`:

```python
"""What the classifier reads for a call: the title, then the transcript, else
the summary. A title that only says a call happened is nothing to read."""

from django.test import SimpleTestCase

from services.customers import calls
from services.customers.classification import _text_for
from services.customers.models import Call
from services.customers.tests.test_call_state import CallFixture


class GenericTitleTests(SimpleTestCase):
    def test_titles_that_say_only_that_a_call_happened_are_generic(self):
        for title in ["", "Call", "Weekly sync", "Zoom meeting", "Quick check-in", "Untitled"]:
            with self.subTest(title=title):
                self.assertTrue(calls.is_generic_title(title))

    def test_a_title_naming_anything_is_not(self):
        for title in ["Renewal readiness", "Call with Kraft Heinz", "Q3 QBR", "SSO escalation"]:
            with self.subTest(title=title):
                self.assertFalse(calls.is_generic_title(title))


class CallTextTests(CallFixture):
    def test_the_transcript_comes_first(self):
        call = self.call(summary="They want SSO.", transcript=self.transcript("We need SSO by Q4."))
        self.assertEqual(_text_for(call), "Renewal readiness. We need SSO by Q4.")

    def test_a_transcript_handed_over_at_creation_beats_the_stored_one(self):
        call = self.call(transcript=self.transcript("stored words"))
        call._transcript_text = "pasted words"
        self.assertEqual(_text_for(call), "Renewal readiness. pasted words")

    def test_then_the_summary(self):
        self.assertEqual(
            _text_for(self.call(summary="They want SSO.")), "Renewal readiness. They want SSO."
        )

    def test_then_the_title_alone(self):
        self.assertEqual(_text_for(self.call()), "Renewal readiness")

    def test_an_unreadable_transcript_file_reads_as_none(self):
        call = self.call(summary="They want SSO.", transcript=self.transcript("gone"))
        call.transcript.file.storage.delete(call.transcript.file.name)
        call = Call.objects.get(pk=call.pk)
        self.assertEqual(_text_for(call), "Renewal readiness. They want SSO.")

    def test_something_to_read(self):
        self.assertFalse(calls.has_something_to_read(self.call(title="Weekly sync")))
        self.assertTrue(calls.has_something_to_read(self.call(title="Sync", summary="Fine.")))
        self.assertTrue(calls.has_something_to_read(self.call(title="SSO escalation")))
        handed = self.call(title="Sync")
        handed._transcript_text = "We need SSO."
        self.assertTrue(calls.has_something_to_read(handed))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python manage.py test services.customers.tests.test_call_text --noinput`
Expected: FAIL/ERROR, `AttributeError: module 'services.customers.calls' has no attribute 'is_generic_title'`, and `test_the_transcript_comes_first` fails with `'Renewal readiness. They want SSO.' != 'Renewal readiness. We need SSO by Q4.'`.

- [ ] **Step 3: Write the text functions**

**Find** in `services/customers/calls.py`:

```text
import logging

from services.copilot.anthropic_client import BudgetExceeded, CopilotNotConfigured, get_completion
```

**Replace with:**

```text
import logging
import re

from services.copilot.anthropic_client import BudgetExceeded, CopilotNotConfigured, get_completion
```

**Find** in `services/customers/calls.py`:

```text
def classify_call(call):
```

**Replace with:**

```text
#: Words that say a call happened and nothing about what was said. A title
#: made only of these ("Weekly sync", "Zoom meeting", "Call") gives the
#: classifier nothing to read, so it is never sent.
GENERIC_TITLE_WORDS = frozenset(
    {
        "a",
        "and",
        "call",
        "catch",
        "catchup",
        "chat",
        "check",
        "checkin",
        "daily",
        "discussion",
        "event",
        "follow",
        "followup",
        "google",
        "huddle",
        "in",
        "intro",
        "meet",
        "meeting",
        "monthly",
        "new",
        "quick",
        "recording",
        "session",
        "standup",
        "sync",
        "teams",
        "the",
        "untitled",
        "up",
        "weekly",
        "with",
        "zoom",
    }
)


def is_generic_title(title: str) -> bool:
    """True when every word of the title is a generic one, or it has none."""
    words = re.findall(r"[a-z]+", (title or "").lower())
    return all(word in GENERIC_TITLE_WORDS for word in words)


def transcript_text_of(call) -> str:
    """The call's transcript as text: what the creating request handed over
    (a pasted transcript is never stored), else the stored transcript file.
    A missing or unreadable file reads as no transcript."""
    handed = getattr(call, "_transcript_text", None)
    if handed is not None:
        return handed.strip()
    if call.transcript_id is None:
        return ""
    from .files import read_transcript_text

    try:
        with call.transcript.file.open("rb") as handle:
            return read_transcript_text(handle).strip()
    except (OSError, ValueError):
        logger.warning("transcript of call %s could not be read", call.pk, exc_info=True)
        return ""


def call_text(call) -> str:
    """What the classifier reads for a call: the title, then the best text
    there is — the transcript, else the summary. The prompt caps every
    record's text (classification.MAX_TEXT_CHARS)."""
    body = transcript_text_of(call) or (call.summary or "").strip()
    return f"{call.title}. {body}" if body else call.title


def has_something_to_read(call) -> bool:
    return bool(
        transcript_text_of(call) or (call.summary or "").strip() or not is_generic_title(call.title)
    )


def classify_call(call):
```

- [ ] **Step 4: Read a call through `call_text`**

**Find** in `services/customers/classification.py`:

```text
    if name == "call":
        return f"{record.title}. {record.summary}" if record.summary else record.title
```

**Replace with:**

```text
    if name == "call":
        # The transcript, else the summary, after the title (calls.call_text).
        from .calls import call_text

        return call_text(record)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.customers.tests.test_call_text services.customers.tests.test_classification --noinput`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/customers/calls.py services/customers/classification.py services/customers/tests/test_call_text.py
git commit -m "feat(contacts): the classifier reads a call's transcript, then summary, then title" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: One classify-on-create helper, on every create path

**Files:**
- Modify: `services/customers/calls.py` (`classify_call`, new `organisation_of_call`)
- Modify: `services/customers/classification.py` (`classify_records`)
- Modify: `services/customers/views.py` (`_CallListView.perform_create`)
- Test: `services/customers/tests/test_call_classify.py`

**Interfaces:**
- Consumes: `mark_not_analysable` (Task 1); `has_something_to_read`, `transcript_text_of` (Task 2); `classify_records(records, *, organisation=None, user=None)`; `contact_sentiment.recompute_for_records(records)`.
- Produces: `calls.classify_call(call, *, transcript_text=None, user=None) -> None` (never raises); `calls.organisation_of_call(call) -> Organisation | None`. `classify_records` marks a call the model declines as not analysable.

- [ ] **Step 1: Write the failing test**

**Create** `services/customers/tests/test_call_classify.py`:

```python
"""The one helper every path that creates a call runs (`calls.classify_call`):
a call is read at once, or marked not analysable, and a failure never blocks
the call. The model is stubbed throughout."""

import ast
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase
from rest_framework import status

from services.copilot.anthropic_client import CopilotNotConfigured
from services.customers import calls
from services.customers.classification import _text_for, classify_records
from services.customers.models import Account, Call, Contact, Email
from services.customers.tests.test_call_state import WHEN, CallFixture

BATCH = "services.customers.classification.classify_batch"


def placed(call, sentiment="negative"):
    return {
        f"call:{call.pk}": {
            "ai_area": "customer_success",
            "ai_category": "onboarding",
            "ai_subcategory": "",
            "sentiment": sentiment,
        }
    }


class DeclinedTests(CallFixture):
    def test_a_call_the_model_declines_is_not_analysable(self):
        call = self.call(summary="Hard to say.")
        with patch(BATCH, return_value={}):
            classify_records([call], organisation=self.org)
        call.refresh_from_db()
        self.assertEqual(call.analysis, "not_analysable")

    def test_an_email_the_model_declines_stays_pending(self):
        email = Email.objects.create(
            customer=self.pizza,
            subject="Hi",
            sender_name="Sam",
            recipient_name="Carl",
            body="hi",
            sent_at=WHEN,
        )
        with patch(BATCH, return_value={}):
            classify_records([email], organisation=self.org)
        email.refresh_from_db()
        self.assertEqual(email.analysis, "pending")


class ClassifyCallTests(CallFixture):
    def test_a_generic_call_with_nothing_else_is_marked_without_a_model_call(self):
        call = self.call(title="Weekly sync")
        with patch(BATCH) as batch:
            calls.classify_call(call)
        batch.assert_not_called()
        call.refresh_from_db()
        self.assertEqual(call.analysis, "not_analysable")

    def test_a_generic_title_with_a_pasted_transcript_is_read(self):
        call = self.call(title="Weekly sync")
        with patch(BATCH, side_effect=lambda batch, **_: placed(call)) as batch:
            calls.classify_call(call, transcript_text="We are unhappy with support.")
        self.assertIn("We are unhappy", _text_for(batch.call_args.args[0][0]))
        call.refresh_from_db()
        self.assertEqual((call.analysis, call.sentiment), ("analysed", "negative"))

    def test_an_account_call_is_metered_to_its_organisation_and_logger(self):
        call = self.call(customer=None, account=self.emea, logged_by=self.carl)
        with patch(BATCH, return_value=placed(call)) as batch:
            calls.classify_call(call)
        self.assertEqual(batch.call_args.kwargs, {"organisation": self.org, "user": self.carl})

    def test_classifying_updates_the_people_on_the_call(self):
        sam = Contact.objects.create(customer=self.pizza, name="Sam Pizza", email="sam@pizza.io")
        call = self.call()
        call.participants.set([sam])
        with patch(BATCH, return_value=placed(call)):
            calls.classify_call(call)
        sam.refresh_from_db()
        self.assertEqual((sam.sentiment, sam.sentiment_source), ("negative", "computed"))

    def test_no_model_leaves_it_pending_for_the_nightly_pass(self):
        call = self.call()
        with patch(
            "services.customers.classification.get_completion",
            side_effect=CopilotNotConfigured("no model"),
        ):
            calls.classify_call(call)
        call.refresh_from_db()
        self.assertEqual(call.analysis, "pending")

    def test_any_failure_is_swallowed(self):
        call = self.call()
        with patch(BATCH, side_effect=RuntimeError("boom")):
            calls.classify_call(call)
        call.refresh_from_db()
        self.assertEqual(call.analysis, "pending")

    def test_an_account_with_no_organisation_is_swallowed_too(self):
        orphan = Account.objects.create(name="Orphan")
        call = self.call(customer=None, account=orphan)
        calls.classify_call(call)
        call.refresh_from_db()
        self.assertEqual(call.analysis, "pending")


class CreatePathTests(CallFixture):
    """Logging a call through the API ("+ Add" on an organisation or an
    account) runs the helper."""

    def post(self, path, body):
        self.client.force_authenticate(self.carl)
        return self.client.post(f"/api/v1/customers/{self.pizza.id}{path}", body, format="json")

    def test_both_create_paths_classify_the_new_call(self):
        def answer(batch, **_):
            return placed(batch[0], "positive")

        for path in ["/calls/", f"/accounts/{self.emea.id}/calls/"]:
            with self.subTest(path=path), patch(BATCH, side_effect=answer):
                response = self.post(
                    path,
                    {"title": "QBR", "occurred_at": WHEN.isoformat(), "summary": "Going well."},
                )
                self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
                self.assertEqual(
                    (response.data["analysis"], response.data["sentiment"]),
                    ("analysed", "positive"),
                )

    def test_a_pasted_transcript_is_what_the_classifier_reads(self):
        with (
            patch("services.customers.calls.get_completion", return_value="A summary."),
            patch(BATCH, side_effect=lambda batch, **_: placed(batch[0])) as batch,
        ):
            self.post(
                "/calls/",
                {
                    "title": "QBR",
                    "occurred_at": WHEN.isoformat(),
                    "transcript_text": "We are unhappy with support.",
                },
            )
        self.assertEqual(_text_for(batch.call_args.args[0][0]), "QBR. We are unhappy with support.")

    def test_a_call_with_nothing_to_read_says_so(self):
        with patch(BATCH) as batch:
            response = self.post("/calls/", {"title": "Call", "occurred_at": WHEN.isoformat()})
        batch.assert_not_called()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["analysis"], "not_analysable")

    def test_a_classifier_failure_never_blocks_the_call(self):
        with patch(BATCH, side_effect=RuntimeError("boom")):
            response = self.post(
                "/calls/", {"title": "QBR", "occurred_at": WHEN.isoformat(), "summary": "Fine."}
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["analysis"], "pending")
        self.assertTrue(Call.objects.filter(pk=response.data["id"]).exists())


class OneCreatePathTests(SimpleTestCase):
    """A Call is created in one place outside tests and seed commands: the
    CallSense list view, which runs `calls.classify_call`. A new path — a
    recorder connector, say — must call it too, and then join ALLOWED."""

    CREATORS = {"create", "bulk_create", "get_or_create", "update_or_create"}
    ALLOWED = {"services/customers/views.py"}

    def test_every_module_that_creates_a_call_runs_the_helper(self):
        root = Path(settings.BASE_DIR)
        creators = set()
        for path in (root / "services").rglob("*.py"):
            relative = path.relative_to(root).as_posix()
            if "/tests/" in relative or "/migrations/" in relative or "/seed_demo_" in relative:
                continue
            source = path.read_text()
            if "Call" not in source:
                continue
            if "serializer_class = CallSerializer" in source:
                creators.add(relative)
            for node in ast.walk(ast.parse(source)):
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr in self.CREATORS
                    and isinstance(node.value, ast.Attribute)
                    and node.value.attr == "objects"
                    and isinstance(node.value.value, ast.Name)
                    and node.value.value.id == "Call"
                ):
                    creators.add(relative)
        self.assertEqual(creators, self.ALLOWED)
        views = (root / "services/customers/views.py").read_text()
        self.assertIn("classify_call(call, transcript_text=transcript_text", views)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python manage.py test services.customers.tests.test_call_classify --noinput`
Expected: FAIL/ERROR. `TypeError: classify_call() got an unexpected keyword argument 'transcript_text'`; `test_a_call_the_model_declines_is_not_analysable` reads `'pending' != 'not_analysable'`; `test_any_failure_is_swallowed` raises `RuntimeError: boom`; the guard fails its `assertIn`.

- [ ] **Step 3: Write the helper**

**Find** in `services/customers/calls.py`:

```text
def classify_call(call):
    from .classification import classify_records

    organisation = (
        call.customer.organisation
        if call.customer_id
        else call.account.customers.select_related("organisation").first().organisation
    )
    classify_records([call], organisation=organisation, user=call.logged_by)
```

**Replace with:**

```text
def organisation_of_call(call):
    if call.customer_id:
        return call.customer.organisation
    customer = call.account.customers.select_related("organisation").first()
    return customer.organisation if customer is not None else None


def classify_call(call, *, transcript_text=None, user=None):
    """The one thing every path that creates a Call runs, once the call and
    its participants are saved: read it now, so its sentiment is on the
    record and on its participants before anyone looks.

    A call with nothing to read is marked not analysable without a model
    call. Anything that goes wrong is logged and swallowed, inside its own
    savepoint: classifying is never a reason for a call not to be saved,
    and `classify_interactions` picks up whatever was left pending."""
    from django.db import transaction

    from .classification import classify_records, mark_not_analysable
    from .contact_sentiment import recompute_for_records

    if transcript_text is not None:
        call._transcript_text = transcript_text
    try:
        with transaction.atomic():
            if not has_something_to_read(call):
                mark_not_analysable(call)
                recompute_for_records([call])
                return
            classify_records(
                [call], organisation=organisation_of_call(call), user=user or call.logged_by
            )
    except Exception:  # noqa: BLE001 — the call is saved whatever happens here
        logger.warning("classifying call %s failed", call.pk, exc_info=True)
```

- [ ] **Step 4: A call the model declines is not analysable**

**Find** in `services/customers/classification.py`:

```text
            if fields:
                apply_classification(record, fields)
                classified += 1
                done.append(record)
```

**Replace with:**

```text
            if fields:
                apply_classification(record, fields)
                classified += 1
                done.append(record)
            elif record._meta.model_name == "call":
                # The model looked and could not place it: nothing to judge.
                mark_not_analysable(record)
                done.append(record)
```

- [ ] **Step 5: Run the helper where calls are created**

**Find** in `services/customers/views.py`:

```text
        # Sentiment now: the pulse counts only classified conversations, and
        # the people on the call sound different once it is read.
        classify_call(call)
```

**Replace with:**

```text
        # Sentiment now: the pulse counts only classified conversations, and
        # the people on the call sound different once it is read. The one
        # helper every path that creates a call runs; it never raises.
        classify_call(call, transcript_text=transcript_text, user=request.user)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.customers.tests.test_call_classify services.customers.tests.test_files_and_calls services.customers.tests.test_contact_sentiment services.connectors services.mail --noinput`
Expected: PASS. The ticket-connector and mailbox sync tests show those paths still classify through `classify_records`.

- [ ] **Step 7: Commit**

```bash
git add services/customers/calls.py services/customers/classification.py services/customers/views.py services/customers/tests/test_call_classify.py
git commit -m "feat(contacts): every new call is classified by one helper, never blocking the save" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: A contact's evidence is their own tenant's, and says something

**Files:**
- Modify: `services/customers/contact_sentiment.py` (`interactions_for`, new `organisation_id_of`, `in_organisation_q`)
- Test: `services/customers/tests/test_contact_sentiment.py` (append `EvidenceScopeTests`)

**Interfaces:**
- Consumes: `Call.not_analysable`, `mark_not_analysable` (Task 1).
- Produces: `contact_sentiment.organisation_id_of(contact) -> int | None`; `contact_sentiment.in_organisation_q(organisation_id) -> Q` (for `Email`/`Ticket`). Both are used by Task 7. Weights and thresholds are unchanged.

- [ ] **Step 1: Write the failing test**

**Append to** `services/customers/tests/test_contact_sentiment.py`:

```python
class EvidenceScopeTests(Fixture):
    """What a contact's sentiment rests on: their own tenant's records, and
    only calls that said something."""

    def test_a_not_analysable_call_is_not_evidence(self):
        from services.customers.classification import mark_not_analysable

        call = self.call("negative", participants=[self.sam])
        mark_not_analysable(call)
        self.assertIsNone(contact_sentiment.recompute(self.sam))
        self.sam.refresh_from_db()
        # Being on it is still contact.
        self.assertEqual(self.sam.last_contacted_at, call.occurred_at)

    def test_another_tenants_mail_from_the_same_address_is_not_evidence(self):
        other = Organisation.objects.create(name="Other")
        theirs = Customer.objects.create(organisation=other, name="Theirs")
        Email.objects.create(
            customer=theirs,
            subject="Furious",
            sender_name="Sam",
            recipient_name="Them",
            body="angry",
            sent_at=NOW,
            from_address="sam@pizzahut.com",
            sentiment="negative",
            ai_classified_at=NOW,
        )
        Ticket.objects.create(
            customer=theirs,
            ticket_number="ZD-9",
            title="Broken",
            priority="high",
            opened_at=date.today(),
            requester_email="sam@pizzahut.com",
            sentiment="negative",
            ai_classified_at=NOW,
        )
        self.assertIsNone(contact_sentiment.recompute(self.sam))

    def test_an_account_contacts_mail_on_their_own_tenant_counts(self):
        Email.objects.create(
            account=self.hut_uk,
            subject="Thanks",
            sender_name="Uma",
            recipient_name="Carl",
            body="great",
            sent_at=NOW,
            from_address="uma@pizzahut.co.uk",
            sentiment="positive",
            ai_classified_at=NOW,
        )
        self.assertEqual(contact_sentiment.recompute(self.uma), "positive")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python manage.py test services.customers.tests.test_contact_sentiment.EvidenceScopeTests --noinput`
Expected: 2 FAIL. `test_a_not_analysable_call_is_not_evidence` reads `'neutral' is not None`; `test_another_tenants_mail_from_the_same_address_is_not_evidence` reads `'negative' is not None`. The third test passes already and guards the fix.

- [ ] **Step 3: Scope the evidence**

**Find** in `services/customers/contact_sentiment.py`:

```text
def interactions_for(contact):
    """Every classified interaction that is this person's, newest first,
    as dicts {kind, record, when, sentiment}."""
    rows = []
    for call in contact.calls.exclude(ai_classified_at=None).select_related("connector"):
        rows.append(
            {"kind": "call", "record": call, "when": call.occurred_at, "sentiment": call.sentiment}
        )
    if contact.email:
        emails = Email.objects.filter(from_address__iexact=contact.email).exclude(
            ai_classified_at=None
        )
```

**Replace with:**

```text
def organisation_id_of(contact):
    """The tenant a contact belongs to: its organisation's, or its
    account's first linked organisation's (an account never spans two)."""
    if contact.customer_id:
        return contact.customer.organisation_id
    return contact.account.customers.values_list("organisation_id", flat=True).first()


def in_organisation_q(organisation_id):
    """Emails and tickets under one tenant — an address is matched only
    inside the contact's own organisation, never another tenant's mail."""
    return Q(customer__organisation_id=organisation_id) | Q(
        account__customers__organisation_id=organisation_id
    )


def interactions_for(contact):
    """Every classified interaction that is this person's, newest first,
    as dicts {kind, record, when, sentiment}. A call marked not analysable
    was read and said nothing, so it is not evidence."""
    rows = []
    calls = (
        contact.calls.exclude(ai_classified_at=None)
        .exclude(not_analysable=True)
        .select_related("connector")
    )
    for call in calls:
        rows.append(
            {"kind": "call", "record": call, "when": call.occurred_at, "sentiment": call.sentiment}
        )
    organisation_id = organisation_id_of(contact) if contact.email else None
    if organisation_id is not None:
        tenant = in_organisation_q(organisation_id)
        emails = (
            Email.objects.filter(tenant, from_address__iexact=contact.email)
            .exclude(ai_classified_at=None)
            .distinct()
        )
```

**Find** in `services/customers/contact_sentiment.py`:

```text
        tickets = Ticket.objects.filter(requester_email__iexact=contact.email).exclude(
            ai_classified_at=None
        )
```

**Replace with:**

```text
        tickets = (
            Ticket.objects.filter(tenant, requester_email__iexact=contact.email)
            .exclude(ai_classified_at=None)
            .distinct()
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.customers.tests.test_contact_sentiment services.customers.tests.test_contact --noinput`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/customers/contact_sentiment.py services/customers/tests/test_contact_sentiment.py
git commit -m "fix(contacts): sentiment evidence stays in the contact's tenant and skips unreadable calls" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `classify_interactions` as the nightly job

**Files:**
- Modify: `services/customers/management/commands/classify_interactions.py`
- Test: `services/customers/tests/test_nightly_classification.py`

**Interfaces:**
- Consumes: `mark_not_analysable` (Task 1); `calls.has_something_to_read` (Task 2); `contact_sentiment.recompute_for_records`; `classify_batch(records, *, organisation=None, user=None)`.
- Produces: the command's options are unchanged (`--org-email`, `--only`, `--limit`, `--reclassify`, `--include-corrected`, `--dry-run`). Its success line reads `"{scope}: classified N of M record(s); U left unplaced by the model; K call(s) not analysable; F batch(es) failed."`, followed by `"Stopped at the AI budget for: A, B."` when a tenant ran out. Task 6 schedules `python manage.py classify_interactions --limit 300`.

- [ ] **Step 1: Write the failing test**

**Create** `services/customers/tests/test_nightly_classification.py`:

```python
"""`classify_interactions` as the nightly job: one tenant at a time, metered
to that tenant, capped, stopping cleanly at a tenant's budget, never guessing
a call, and moving the contacts on what it reads."""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from services.accounts.models import Organisation
from services.copilot.anthropic_client import BudgetExceeded
from services.customers.models import Call, Contact, Customer, Email

BATCH = "services.customers.management.commands.classify_interactions.classify_batch"


def answer(batch, sentiment="negative"):
    return {
        f"{r._meta.model_name}:{r.pk}": {
            "ai_area": "product_growth",
            "ai_category": "bug_report",
            "ai_subcategory": "ui_bug",
            "sentiment": sentiment,
        }
        for r in batch
    }


class NightlyCommandTests(TestCase):
    def setUp(self):
        self.acme = Organisation.objects.create(name="Acme")
        self.globex = Organisation.objects.create(name="Globex")
        self.pizza = Customer.objects.create(organisation=self.acme, name="Pizza Hut")
        self.initech = Customer.objects.create(organisation=self.globex, name="Initech")

    def email(self, customer, n=0):
        return Email.objects.create(
            customer=customer,
            subject=f"Subject {n}",
            sender_name="Ada",
            recipient_name="Support",
            body="Body.",
            sent_at=timezone.now(),
        )

    def call(self, title="Renewal readiness", summary=""):
        return Call.objects.create(
            customer=self.pizza,
            title=title,
            summary=summary,
            host_name="Carl",
            occurred_at=timezone.now(),
        )

    def run_command(self, **kwargs):
        out, err = StringIO(), StringIO()
        call_command("classify_interactions", stdout=out, stderr=err, **kwargs)
        return out.getvalue()

    def test_each_batch_is_one_tenants_and_metered_to_it(self):
        mine = [self.email(self.pizza, n) for n in range(2)]
        theirs = [self.email(self.initech, n) for n in range(2)]
        with patch(BATCH, side_effect=lambda batch, **_: answer(batch)) as batch:
            self.run_command()
        seen = {
            call.kwargs["organisation"].pk: {r.pk for r in call.args[0]}
            for call in batch.call_args_list
        }
        self.assertEqual(
            seen,
            {self.acme.pk: {e.pk for e in mine}, self.globex.pk: {e.pk for e in theirs}},
        )

    def test_a_capped_run_takes_the_tenants_in_turn(self):
        for n in range(3):
            self.email(self.pizza, n)
        self.email(self.initech)
        with patch(BATCH, side_effect=lambda batch, **_: answer(batch)):
            self.run_command(limit=2)
        classified = Email.objects.exclude(ai_classified_at=None)
        self.assertEqual(sorted(e.customer.name for e in classified), ["Initech", "Pizza Hut"])

    def test_a_tenant_out_of_budget_stops_cleanly_and_the_rest_carry_on(self):
        self.email(self.pizza)
        other = self.email(self.initech)

        def budget(batch, organisation, **_):
            if organisation == self.acme:
                raise BudgetExceeded("Acme is out of credits")
            return answer(batch)

        with patch(BATCH, side_effect=budget):
            out = self.run_command()
        self.assertIn("Stopped at the AI budget for: Acme.", out)
        other.refresh_from_db()
        self.assertIsNotNone(other.ai_classified_at)
        self.assertEqual(Email.objects.filter(ai_classified_at=None).count(), 1)

    def test_a_call_with_nothing_to_read_is_marked_without_a_model_call(self):
        call = self.call(title="Weekly sync")
        with patch(BATCH) as batch:
            out = self.run_command()
        batch.assert_not_called()
        call.refresh_from_db()
        self.assertEqual(call.analysis, "not_analysable")
        self.assertIn("1 call(s) not analysable", out)

    def test_a_call_the_model_declines_is_not_analysable_and_not_retried(self):
        call = self.call(summary="Hmm.")
        with patch(BATCH, return_value={}):
            self.run_command()
        call.refresh_from_db()
        self.assertEqual(call.analysis, "not_analysable")
        with patch(BATCH) as batch:
            self.assertIn("Nothing to classify", self.run_command())
        batch.assert_not_called()

    def test_classifying_moves_the_people_on_the_call(self):
        sam = Contact.objects.create(customer=self.pizza, name="Sam", email="sam@pizza.io")
        call = self.call(summary="They are unhappy.")
        call.participants.set([sam])
        with patch(BATCH, side_effect=lambda batch, **_: answer(batch)):
            self.run_command()
        sam.refresh_from_db()
        self.assertEqual((sam.sentiment, sam.sentiment_source), ("negative", "computed"))

    def test_the_dry_run_counts_one_model_call_per_tenant(self):
        self.email(self.pizza)
        self.email(self.initech)
        with patch(BATCH) as batch:
            out = self.run_command(dry_run=True)
        batch.assert_not_called()
        self.assertIn("Would classify 2 record(s)", out)
        self.assertIn("in 2 model call(s)", out)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python manage.py test services.customers.tests.test_nightly_classification --noinput`
Expected: FAIL/ERROR. `AttributeError: 'NoneType' object has no attribute 'pk'` (batches carry no organisation), `CommandError: Acme is out of credits`, a call left `pending`, Sam still `neutral`/`manual`, and `in 1 model call(s)`.

- [ ] **Step 3: Update the module docstring**

**Find** in `services/customers/management/commands/classify_interactions.py`:

```text
Not wired into `run_health_maintenance`, deliberately. That job is free,
idempotent and safe to run hourly; this one spends money per row, and bundling
them would mean every health tick billing a provider. Schedule it separately,
less often, and with `--limit`:

    # nightly, a few hundred rows at a time
    30 1 * * *  cd /srv/revenact && venv/bin/python manage.py classify_interactions --limit 300

```

**Replace with:**

```text
Not wired into `run_health_maintenance`, deliberately. That job is free,
idempotent and safe to run hourly; this one spends money per row, and bundling
them would mean every health tick billing a provider. It runs nightly on the
server, before the health job, with a cap (revenact-infra
`deploy/docker-compose.prod.yml`, the `scheduler` service):

    python manage.py classify_interactions --limit 300

**One tenant at a time.** Each organisation's records are batched and metered
on their own, so every model call is charged to the organisation whose
records it read; a capped run takes the tenants' oldest records in turn. An
organisation out of AI credits is skipped for the night and named in the
report, and the run carries on with the rest and exits cleanly.

**Calls are never guessed.** A call with no transcript, no summary and a
generic title is marked not analysable without a model call, and so is one the
model declines; either way it is not retried. Each batch recomputes the
contacts on its records, as `classify_records` does.

```

- [ ] **Step 4: Update the imports**

**Find** in `services/customers/management/commands/classify_interactions.py`:

```text
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from services.accounts.models import User
```

**Replace with:**

```text
from itertools import zip_longest

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from services.accounts.models import Organisation, User
```

**Find** in `services/customers/management/commands/classify_interactions.py`:

```text
    classify_batch,
    clear_classification,
)
```

**Replace with:**

```text
    classify_batch,
    clear_classification,
    mark_not_analysable,
)
```

- [ ] **Step 5: Batch, meter and stop per tenant**

Replace everything from `        names = options["only"] or sorted(MODELS)` to the end of the file.

**Find** in `services/customers/management/commands/classify_interactions.py`:

```text
        names = options["only"] or sorted(MODELS)
        pending = []
        for name in names:
            model = MODELS[name]
            queryset = model.objects.all()
            if organisation is not None:
                queryset = queryset.filter(_org_filter(model, organisation)).distinct()
            if not options["reclassify"]:
                queryset = queryset.filter(ai_classified_at__isnull=True)
            elif not options["include_corrected"]:
                # A person's correction outranks the model. A reclassify pass
                # is for a changed taxonomy or a better prompt, and neither is
                # a reason to put the model's answer back over a human's.
                queryset = queryset.filter(classification_corrected_at__isnull=True)
            # Oldest first: on a capped run, the records that have been waiting
            # longest are the ones to spend the budget on.
            pending.extend(queryset.order_by("pk"))

        if limit is not None:
            pending = pending[:limit]

        if not pending:
            self.stdout.write("Nothing to classify.")
            return

        if options["dry_run"]:
            batches = -(-len(pending) // BATCH_SIZE)
            by_type = {}
            for record in pending:
                by_type[record._meta.model_name] = by_type.get(record._meta.model_name, 0) + 1
            breakdown = ", ".join(f"{count} {name}(s)" for name, count in sorted(by_type.items()))
            self.stdout.write(
                f"Would classify {len(pending)} record(s) — {breakdown} — "
                f"in {batches} model call(s). Nothing written."
            )
            return

        classified, unplaced, failed_batches = 0, 0, 0

        for start in range(0, len(pending), BATCH_SIZE):
            batch = pending[start : start + BATCH_SIZE]
            try:
                results = classify_batch(batch, organisation=organisation)
            except BudgetExceeded as exc:
                raise CommandError(f"{exc}") from exc
            except CopilotNotConfigured as exc:
                # Nothing downstream can succeed either, so stop rather than
                # burning through every remaining batch to fail identically.
                raise CommandError(f"{exc}") from exc
            except (CopilotRequestFailed, ValueError) as exc:
                # One bad batch — a rate limit, a timeout, an unreadable answer
                # — shouldn't abandon the batches behind it. The records stay
                # unclassified, so the next run picks them up.
                self.stderr.write(f"  batch of {len(batch)} failed: {exc}")
                failed_batches += 1
                continue

            for record in batch:
                fields = results.get(f"{record._meta.model_name}:{record.pk}")
                if fields is None:
                    unplaced += 1
                    # On a reclassify pass the record already carries an
                    # answer — possibly the demo seed's, possibly a stale one.
                    # The model has now looked and could not place it, and a
                    # dashboard should not keep counting a category nobody
                    # stands behind. A default pass leaves it as it was: blank.
                    if options["reclassify"]:
                        clear_classification(record)
                    continue
                apply_classification(record, fields)
                classified += 1

        scope = organisation.name if organisation else "every organisation"
        self.stdout.write(
            self.style.SUCCESS(
                f"{scope}: classified {classified} of {len(pending)} record(s); "
                f"{unplaced} left unplaced by the model; {failed_batches} batch(es) failed."
            )
        )
```

**Replace with:**

```text
        names = options["only"] or sorted(MODELS)
        organisations = (
            [organisation] if organisation is not None else Organisation.objects.order_by("pk")
        )
        # Each tenant's work is gathered, batched and metered on its own: a
        # batch never mixes tenants, so every model call is charged to the
        # organisation whose records it read.
        queues = [(org, self._pending(org, names, options, limit)) for org in organisations]
        queues = [(org, rows) for org, rows in queues if rows]
        pending = _round_robin(queues, limit)

        if not pending:
            self.stdout.write("Nothing to classify.")
            return

        if options["dry_run"]:
            batches = sum(-(-len(rows) // BATCH_SIZE) for _org, rows in _by_org(pending))
            by_type = {}
            for _org, record in pending:
                by_type[record._meta.model_name] = by_type.get(record._meta.model_name, 0) + 1
            breakdown = ", ".join(f"{count} {name}(s)" for name, count in sorted(by_type.items()))
            self.stdout.write(
                f"Would classify {len(pending)} record(s) — {breakdown} — "
                f"in {batches} model call(s). Nothing written."
            )
            return

        totals = {"classified": 0, "unplaced": 0, "not_analysable": 0, "failed": 0}
        out_of_budget = []
        for org, rows in _by_org(pending):
            if not self._classify(org, rows, options, totals):
                out_of_budget.append(org.name)

        scope = organisation.name if organisation else "every organisation"
        self.stdout.write(
            self.style.SUCCESS(
                f"{scope}: classified {totals['classified']} of {len(pending)} record(s); "
                f"{totals['unplaced']} left unplaced by the model; "
                f"{totals['not_analysable']} call(s) not analysable; "
                f"{totals['failed']} batch(es) failed."
            )
        )
        if out_of_budget:
            # A clean stop, not a failure: the rest waits for next month's
            # credits or a raised budget, and the next run picks it up.
            self.stdout.write(
                "Stopped at the AI budget for: " + ", ".join(sorted(out_of_budget)) + "."
            )

    def _pending(self, organisation, names, options, limit):
        rows = []
        for name in names:
            model = MODELS[name]
            queryset = model.objects.filter(_org_filter(model, organisation)).distinct()
            if not options["reclassify"]:
                queryset = queryset.filter(ai_classified_at__isnull=True)
            elif not options["include_corrected"]:
                # A person's correction outranks the model. A reclassify pass
                # is for a changed taxonomy or a better prompt, and neither is
                # a reason to put the model's answer back over a human's.
                queryset = queryset.filter(classification_corrected_at__isnull=True)
            if model is Call:
                queryset = queryset.select_related("transcript")
            # Oldest first: on a capped run, the records that have been waiting
            # longest are the ones to spend the budget on.
            queryset = queryset.order_by("pk")
            rows.extend(queryset[:limit] if limit is not None else queryset)
        return rows

    def _classify(self, organisation, rows, options, totals):
        """One tenant's records, in batches. False when its budget ran out."""
        from services.customers.calls import has_something_to_read
        from services.customers.contact_sentiment import recompute_for_records

        # A call with nothing to read is marked without paying for a look.
        to_read = []
        for record in rows:
            if record._meta.model_name == "call" and not has_something_to_read(record):
                mark_not_analysable(record)
                totals["not_analysable"] += 1
                recompute_for_records([record])
            else:
                to_read.append(record)

        for start in range(0, len(to_read), BATCH_SIZE):
            batch = to_read[start : start + BATCH_SIZE]
            try:
                results = classify_batch(batch, organisation=organisation)
            except BudgetExceeded:
                return False
            except CopilotNotConfigured as exc:
                # Nothing downstream can succeed either, so stop rather than
                # burning through every remaining batch to fail identically.
                raise CommandError(f"{exc}") from exc
            except (CopilotRequestFailed, ValueError) as exc:
                # One bad batch — a rate limit, a timeout, an unreadable answer
                # — shouldn't abandon the batches behind it. The records stay
                # unclassified, so the next run picks them up.
                self.stderr.write(f"  batch of {len(batch)} failed: {exc}")
                totals["failed"] += 1
                continue

            touched = []
            for record in batch:
                fields = results.get(f"{record._meta.model_name}:{record.pk}")
                if fields is not None:
                    apply_classification(record, fields)
                    totals["classified"] += 1
                elif record._meta.model_name == "call":
                    # The model looked and could not place it. Never guessed,
                    # and never paid for again.
                    mark_not_analysable(record)
                    totals["not_analysable"] += 1
                else:
                    totals["unplaced"] += 1
                    # On a reclassify pass the record already carries an
                    # answer — possibly the demo seed's, possibly a stale one.
                    # The model has now looked and could not place it, and a
                    # dashboard should not keep counting a category nobody
                    # stands behind. A default pass leaves it as it was: blank.
                    if options["reclassify"]:
                        clear_classification(record)
                    continue
                touched.append(record)
            # The people on these calls, emails and tickets sound different now.
            recompute_for_records(touched)
        return True


def _round_robin(queues, limit):
    """`(organisation, record)` pairs, one tenant's oldest record after
    another's in turn, capped at `limit` — so one tenant's backlog never
    spends a whole capped run while another's waits."""
    pairs = []
    for group in zip_longest(*[[(org, r) for r in rows] for org, rows in queues]):
        pairs.extend(pair for pair in group if pair is not None)
    return pairs[:limit] if limit is not None else pairs


def _by_org(pairs):
    """The pairs regrouped by tenant, each tenant's records in order."""
    grouped = {}
    for org, record in pairs:
        grouped.setdefault(org.pk, (org, []))[1].append(record)
    return list(grouped.values())
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.customers.tests.test_nightly_classification services.customers.tests.test_classification services.metrics.tests.test_feedback --noinput`
Expected: PASS. The existing command tests keep their wording (`classified 3 of 3`, `1 left unplaced`, `1 batch(es) failed`, `Would classify 3`, `Nothing to classify`).

- [ ] **Step 7: Commit**

```bash
git add services/customers/management/commands/classify_interactions.py services/customers/tests/test_nightly_classification.py
git commit -m "feat(contacts): classify_interactions meters each tenant, stops cleanly and moves contacts" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: The nightly schedule (revenact-infra)

This task is in the **infra repo**, on its own branch. It merges and deploys **after** the backend PR is live, because an older backend would still mix tenants in a batch.

**Files:**
- Modify: `revenact-infra/deploy/docker-compose.prod.yml` (the `scheduler` service)
- Modify: `revenact-infra/README.md` (the services table)

**Interfaces:**
- Consumes: `python manage.py classify_interactions --limit 300` (Task 5) and `Call.transcript` files under `/app/media` (the `media_files` volume the `backend` service writes).
- Produces: nothing the backend reads.

- [ ] **Step 1: Branch**

```bash
cd /Users/tusharaggarwal/Desktop/Projects/Revenact/revenact-infra
git checkout main && git pull --ff-only && git checkout -b feat/nightly-classification
```

- [ ] **Step 2: Schedule the job and mount the transcripts**

**Find** in `deploy/docker-compose.prod.yml`:

```text
  # The daily job: health recalculation, month-end metric snapshots, stale
  # question reminders (see run_health_maintenance). A sleep loop on purpose —
  # no cron, no queue — the job is idempotent.
  scheduler:
    build: ./backend
    restart: unless-stopped
    env_file: ./backend.env
    # The image's HEALTHCHECK probes Daphne on :8000; this container runs a
    # sleep loop, not a server, so that probe can never pass.
    healthcheck:
      disable: true
    volumes:
      - model_cache:/app/.cache/huggingface
    depends_on:
      db:
        condition: service_healthy
    command: >
      sh -c "while true; do
               python manage.py run_health_maintenance || echo 'health maintenance failed; retrying tomorrow';
               sleep 86400;
             done"
```

**Replace with:**

```text
  # The daily jobs. First classify_interactions: the calls, emails and
  # tickets nothing has read yet, capped at 300 a night, one organisation at
  # a time and metered to its AI credits (an organisation out of credits is
  # skipped until the next night). Then health recalculation, month-end
  # metric snapshots, stale question reminders (see run_health_maintenance),
  # which reads the fresh sentiment. A sleep loop on purpose — no cron, no
  # queue — both jobs are idempotent.
  scheduler:
    build: ./backend
    restart: unless-stopped
    env_file: ./backend.env
    # The image's HEALTHCHECK probes Daphne on :8000; this container runs a
    # sleep loop, not a server, so that probe can never pass.
    healthcheck:
      disable: true
    volumes:
      - model_cache:/app/.cache/huggingface
      # Call transcripts are uploaded files: classification reads them.
      # Read-only; only the backend service writes here.
      - media_files:/app/media:ro
    depends_on:
      db:
        condition: service_healthy
    command: >
      sh -c "while true; do
               python manage.py classify_interactions --limit 300 || echo 'classification failed; retrying tomorrow';
               python manage.py run_health_maintenance || echo 'health maintenance failed; retrying tomorrow';
               sleep 86400;
             done"
```

- [ ] **Step 3: Say so in the README**

**Find** in `README.md`:

```text
| `scheduler` | daily `run_health_maintenance` (health, month-end snapshots, question reminders) |
```

**Replace with:**

```text
| `scheduler` | nightly `classify_interactions --limit 300` (reads the calls, emails and tickets nothing has read yet, one organisation at a time, metered to its AI credits), then daily `run_health_maintenance` (health, month-end snapshots, question reminders). Mounts the media volume read-only for call transcripts |
```

- [ ] **Step 4: Validate the compose file**

`backend.env` is not in the repo, and compose needs it to exist. Create an empty one only if it is missing, and remove only the one you created:

```bash
cd deploy
created=""; [ -e backend.env ] || { : > backend.env; created=1; }
PUBLIC_HOST=example.com docker compose -f docker-compose.prod.yml config -q && echo COMPOSE_OK
[ -n "$created" ] && rm backend.env
PUBLIC_HOST=example.com docker compose -f docker-compose.prod.yml config 2>/dev/null | grep -A3 "classify_interactions" | head -4 || true
cd ..
```

Expected: `COMPOSE_OK` (a warning about `POSTGRES_PASSWORD` not being set is fine). `git status` shows only the two modified files.

- [ ] **Step 5: Commit**

```bash
git add deploy/docker-compose.prod.yml README.md
git commit -m "feat(scheduler): classify interactions nightly before health maintenance" -m "The scheduler runs classify_interactions --limit 300 ahead of run_health_maintenance, so the health job reads fresh sentiment, and mounts the media volume read-only so call transcripts can be read." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

Roll-out (controller, with the owner's go-ahead, after the backend PR is deployed): open the infra PR, merge it, then run `deploy/sync.sh`, which copies the compose file to the VM and runs `deploy.sh`. Check it with `sudo docker compose logs --since 24h scheduler` on the VM the next morning. It should show the `classify_interactions` summary line before the health job's output.

---

### Task 7: A person's history, each row under its own rule

**Files:**
- Create: `services/customers/contact_history.py`
- Modify: `services/customers/views.py` (`_visible_contact`, `ContactHistoryView`, `ContactInteractionsView`)
- Modify: `config/urls.py`
- Test: `services/customers/tests/test_contact_history.py`

**Interfaces:**
- Consumes: `organisation_id_of`, `in_organisation_q` (Task 4); `AIClassified.analysis`, `mark_not_analysable` (Task 1); `scoping.visible_children_q`, `scoping.visible_customers`; `mail.visibility.visible_emails(user, queryset)`; `personal.visible_tickets(user, queryset)`; `organizations.story.items.clip(text)`, `safe_url(raw)`.
- Produces: `contact_history.HISTORY_LIMIT = 100`; `contact_history.history_querysets(contact, viewer) -> (calls, emails, tickets)` querysets already under every rule; `contact_history.build_history(contact, viewer) -> dict` (the response below); `views._visible_contact(request, pk) -> Contact` (404 otherwise); route name `contact-history`.

- [ ] **Step 1: Write the failing test**

**Create** `services/customers/tests/test_contact_history.py`:

```python
"""GET /api/v1/contacts/<id>/history/ — a person's calls, emails and tickets,
each under its own record rule, and a 404 for a contact the viewer cannot
open. /interactions/ reads under the same rules until it is retired."""

from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone

from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers.classification import mark_not_analysable
from services.customers.models import Call, Contact, Customer, Email, Ticket
from services.customers.tests.test_views import blind_to_one_account

WHEN = datetime(2026, 9, 20, 10, 0, tzinfo=dt_timezone.utc)


class Fixture(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme")
        self.pizza = Customer.objects.create(organisation=self.org, name="Pizza Hut")
        # The viewer opens Pizza Hut and its Seen account, not its Hidden one,
        # which the colleague (Pizza Hut's owner) owns.
        self.viewer, self.seen, self.hidden = blind_to_one_account(self.pizza)
        self.colleague = self.pizza.owner
        self.sam = Contact.objects.create(
            customer=self.pizza, name="Sam Pizza", email="sam@pizzahut.com"
        )
        self.url = f"/api/v1/contacts/{self.sam.id}/history/"

    def call(self, title, days_ago=0, sentiment="positive", **parent):
        parent = parent or {"customer": self.pizza}
        call = Call.objects.create(
            title=title,
            host_name="Carl",
            summary=f"{title} summary",
            occurred_at=WHEN - timedelta(days=days_ago),
            duration_minutes=30,
            sentiment=sentiment,
            ai_category="onboarding",
            ai_classified_at=WHEN,
            **parent,
        )
        call.participants.add(self.sam)
        return call

    def email(self, subject, mailbox_owner=None, customer=None, **kw):
        return Email.objects.create(
            customer=customer or self.pizza,
            subject=subject,
            sender_name="Sam",
            recipient_name="Carl",
            body="Body text.",
            sent_at=WHEN,
            from_address="Sam@PizzaHut.com",
            mailbox_owner=mailbox_owner,
            **kw,
        )

    def ticket(self, number, department="", customer=None):
        return Ticket.objects.create(
            customer=customer or self.pizza,
            ticket_number=number,
            title="Broken export",
            priority="high",
            opened_at=date(2026, 9, 19),
            requester_email="sam@pizzahut.com",
            department=department,
            external_url="https://acme.zendesk.com/t/1",
        )

    def read(self, user=None):
        self.client.force_authenticate(user or self.viewer)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data


class HistoryShapeTests(Fixture):
    def test_calls_come_newest_first_with_their_reading_and_parents(self):
        self.call("Older", days_ago=3, sentiment="negative")
        seen = self.call("On Seen", days_ago=1, account=self.seen)
        empty = self.call("Sync", days_ago=0)
        mark_not_analysable(empty)

        body = self.read()

        self.assertEqual([c["title"] for c in body["calls"]], ["Sync", "On Seen", "Older"])
        sync, on_seen, older = body["calls"]
        self.assertEqual((sync["analysis"], sync["sentiment"]), ("not_analysable", None))
        self.assertEqual((older["analysis"], older["sentiment"]), ("analysed", "negative"))
        self.assertEqual(older["classification"]["category"], "Onboarding")
        self.assertEqual(
            (on_seen["organisation"], on_seen["account"]),
            ({"id": self.pizza.id, "name": "Pizza Hut"}, {"id": self.seen.id, "name": "Seen"}),
        )
        self.assertEqual(older["organisation"], {"id": self.pizza.id, "name": "Pizza Hut"})
        self.assertIsNone(older["account"])
        self.assertEqual(
            set(on_seen),
            {
                "id",
                "title",
                "occurred_at",
                "duration_minutes",
                "host_name",
                "summary",
                "analysis",
                "sentiment",
                "classification",
                "organisation",
                "account",
                "link",
            },
        )
        self.assertEqual(on_seen["id"], seen.id)
        self.assertEqual(body["counts"], {"calls": 3, "emails": 0, "tickets": 0})

    def test_an_unread_call_is_pending_with_no_sentiment(self):
        Call.objects.create(
            customer=self.pizza, title="New", host_name="Carl", occurred_at=WHEN
        ).participants.add(self.sam)
        row = self.read()["calls"][0]
        self.assertEqual((row["analysis"], row["sentiment"]), ("pending", None))

    def test_emails_and_tickets_carry_their_reading_and_links(self):
        self.email("Thanks", sentiment="positive", ai_classified_at=WHEN, thread_id="t-1")
        self.ticket("ZD-1")

        body = self.read()

        email = body["emails"][0]
        self.assertEqual(
            (email["subject"], email["analysis"], email["sentiment"], email["link"]),
            ("Thanks", "analysed", "positive", {"thread_id": "t-1"}),
        )
        ticket = body["tickets"][0]
        self.assertEqual(
            (ticket["ticket_number"], ticket["analysis"], ticket["sentiment"]),
            ("ZD-1", "pending", None),
        )
        self.assertEqual(ticket["link"], {"url": "https://acme.zendesk.com/t/1"})

    def test_the_breakdown_comes_with_it(self):
        from services.customers.contact_sentiment import recompute

        self.call("QBR")
        recompute(self.sam)
        body = self.read()
        self.assertEqual((body["sentiment"], body["sentiment_source"]), ("positive", "computed"))
        self.assertEqual(body["sentiment_evidence"]["calls"], 1)


class HistoryPrivacyTests(Fixture):
    def test_a_call_on_an_account_the_viewer_cannot_open_is_left_out(self):
        self.call("Org call")
        self.call("Seen call", account=self.seen)
        self.call("Hidden call", account=self.hidden)

        body = self.read()

        self.assertEqual({c["title"] for c in body["calls"]}, {"Org call", "Seen call"})
        self.assertEqual(body["counts"]["calls"], 2)
        titles = {c["title"] for c in self.read(self.colleague)["calls"]}
        self.assertEqual(titles, {"Org call", "Seen call", "Hidden call"})

    def test_mail_follows_its_mailbox_owner(self):
        self.email("Shared")
        self.email("Mine", mailbox_owner=self.viewer)
        self.email("Colleague's", mailbox_owner=self.colleague)

        self.assertEqual({e["subject"] for e in self.read()["emails"]}, {"Shared", "Mine"})
        self.assertEqual(
            {e["subject"] for e in self.read(self.colleague)["emails"]}, {"Shared", "Colleague's"}
        )

    def test_mail_on_a_hidden_account_is_left_out(self):
        Email.objects.create(
            account=self.hidden,
            subject="Hidden",
            sender_name="Sam",
            recipient_name="Carl",
            body="x",
            sent_at=WHEN,
            from_address="sam@pizzahut.com",
        )
        self.assertEqual(self.read()["emails"], [])

    def test_tickets_follow_their_department(self):
        self.ticket("ZD-1")
        self.ticket("ZD-2", department=User.Function.CS)
        self.ticket("ZD-3", department=User.Function.ENGINEERING)

        numbers = {t["ticket_number"] for t in self.read()["tickets"]}
        self.assertEqual(numbers, {"ZD-1", "ZD-2"})
        self.viewer.function = User.Function.LEADERSHIP
        self.viewer.save(update_fields=["function"])
        numbers = {t["ticket_number"] for t in self.read(self.viewer)["tickets"]}
        self.assertEqual(numbers, {"ZD-1", "ZD-2", "ZD-3"})

    def test_another_tenants_records_from_the_same_address_are_left_out(self):
        other = Organisation.objects.create(name="Other")
        theirs = Customer.objects.create(organisation=other, name="Theirs")
        self.email("Theirs", customer=theirs)
        self.ticket("OT-1", customer=theirs)
        body = self.read()
        self.assertEqual((body["emails"], body["tickets"]), ([], []))

    def test_a_contact_the_viewer_cannot_open_is_a_404(self):
        hidden_contact = Contact.objects.create(
            account=self.hidden, name="Hal Hidden", email="hal@pizzahut.com"
        )
        self.client.force_authenticate(self.viewer)
        for pk in (hidden_contact.id, 999999):
            with self.subTest(pk=pk):
                response = self.client.get(f"/api/v1/contacts/{pk}/history/")
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        outsider = User.objects.create_user(
            email="o@other.io",
            password="x",
            name="O",
            organisation=Organisation.objects.create(name="O"),
        )
        self.client.force_authenticate(outsider)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_404_NOT_FOUND)

    def test_the_old_interactions_endpoint_reads_under_the_same_rules(self):
        self.call("Seen call", account=self.seen)
        self.call("Hidden call", account=self.hidden)
        self.email("Colleague's", mailbox_owner=self.colleague, ai_classified_at=WHEN)
        Ticket.objects.filter(pk=self.ticket("ZD-3", department="engineering").pk).update(
            ai_classified_at=WHEN
        )
        self.client.force_authenticate(self.viewer)

        response = self.client.get(f"/api/v1/contacts/{self.sam.id}/interactions/")

        self.assertEqual(
            [(i["kind"], i["title"]) for i in response.data["interactions"]],
            [("call", "Seen call")],
        )


class HistoryQueryCountTests(Fixture):
    def test_the_query_count_does_not_grow_with_the_history(self):
        self.client.force_authenticate(self.viewer)
        # The first read resolves the caller's membership and org chart, which
        # are memoised on the user; what is pinned is every read after it.
        self.client.get(self.url)
        for batch in range(2):
            for i in range(3):
                self.call(f"Org {batch}-{i}")
                self.call(f"Seen {batch}-{i}", account=self.seen)
                self.email(f"Mail {batch}-{i}")
                self.ticket(f"ZD-{batch}-{i}")
            with self.assertNumQueries(10):
                body = self.client.get(self.url).data
            self.assertEqual(len(body["calls"]), 6 * (batch + 1))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python manage.py test services.customers.tests.test_contact_history --noinput`
Expected: FAIL. Every `/history/` read is a `404` (no route), so the shape tests fail on `200 != 404`. `test_the_old_interactions_endpoint_reads_under_the_same_rules` lists the hidden call, the colleague's email and the engineering ticket too.

- [ ] **Step 3: Build the history**

**Create** `services/customers/contact_history.py`:

```python
"""A person's history: their calls, emails and tickets, newest first, each
under its own record rule for the viewer.

Twice filtered, like every read of record text here. First by company: a
record on an account the viewer may not open is not theirs, whichever
organisation it hangs off (`visible_children_q`). Then by the record's own
rule: mail is its mailbox owner's and their chain's (`visible_emails`), a
ticket is its department's (`visible_tickets`), a call belongs to no one
person. Mail and tickets are matched by the contact's address inside the
contact's own tenant only (`contact_sentiment.in_organisation_q`).

`sentiment` is a reading only when `analysis` is `analysed`; otherwise it
is null, and the UI says "Not enough to analyse" or waits.
"""

from services.mail.visibility import visible_emails
from services.organizations.story.items import clip, safe_url

from .contact_sentiment import in_organisation_q, organisation_id_of
from .models import Email, Ticket
from .personal import visible_tickets
from .scoping import visible_children_q, visible_customers

#: Newest first, per kind. `counts` carries the whole visible total.
HISTORY_LIMIT = 100


def _ref(row):
    return {"id": row.pk, "name": row.name} if row is not None else None


def _parents(record, visible_customer_ids):
    """(organisation, account) refs. An account's organisation is the first of
    its linked organisations the viewer may open."""
    if record.customer_id:
        return _ref(record.customer), None
    organisation = next(
        (c for c in record.account.customers.all() if c.pk in visible_customer_ids), None
    )
    return _ref(organisation), _ref(record.account)


def _classification(record):
    return {
        "area": record.get_ai_area_display() or "",
        "category": record.get_ai_category_display() or "",
        "subcategory": record.get_ai_subcategory_display() or "",
    }


def _reading(record):
    analysis = record.analysis
    return {
        "analysis": analysis,
        "sentiment": record.sentiment if analysis == "analysed" else None,
        "classification": _classification(record),
    }


def _call(call, visible_customer_ids):
    organisation, account = _parents(call, visible_customer_ids)
    return {
        "id": call.pk,
        "title": call.title,
        "occurred_at": call.occurred_at.isoformat(),
        "duration_minutes": call.duration_minutes,
        "host_name": call.host_name,
        "summary": call.summary,
        **_reading(call),
        "organisation": organisation,
        "account": account,
        "link": {"url": safe_url(call.recording_url)},
    }


def _email(email, visible_customer_ids):
    organisation, account = _parents(email, visible_customer_ids)
    return {
        "id": email.pk,
        "subject": email.subject,
        "sent_at": email.sent_at.isoformat(),
        "sender_name": email.sender_name,
        "snippet": clip(email.body),
        **_reading(email),
        "organisation": organisation,
        "account": account,
        "link": {"thread_id": email.thread_id or None},
    }


def _ticket(ticket, visible_customer_ids):
    organisation, account = _parents(ticket, visible_customer_ids)
    return {
        "id": ticket.pk,
        "ticket_number": ticket.ticket_number,
        "title": ticket.title,
        "status": ticket.status,
        "status_display": ticket.get_status_display(),
        "opened_at": ticket.opened_at.isoformat(),
        **_reading(ticket),
        "organisation": organisation,
        "account": account,
        "link": {"url": safe_url(ticket.external_url)},
    }


def history_querysets(contact, viewer):
    """The viewer's view of this person's calls, emails and tickets, each
    already under its own rule, unordered and unsliced."""
    # SOC2:AUTH-02 each record follows its own company's visibility and its own rule
    company = visible_children_q(viewer)
    calls = contact.calls.filter(company).distinct()
    emails = Email.objects.none()
    tickets = Ticket.objects.none()
    organisation_id = organisation_id_of(contact) if contact.email else None
    if organisation_id is not None:
        tenant = in_organisation_q(organisation_id)
        emails = visible_emails(
            viewer,
            Email.objects.filter(tenant, company, from_address__iexact=contact.email).distinct(),
        )
        tickets = visible_tickets(
            viewer,
            Ticket.objects.filter(
                tenant, company, requester_email__iexact=contact.email
            ).distinct(),
        )
    return calls, emails, tickets


def build_history(contact, viewer):
    calls, emails, tickets = history_querysets(contact, viewer)
    visible_customer_ids = set(visible_customers(viewer).values_list("pk", flat=True))
    parents = ("customer", "account")
    call_rows = (
        calls.select_related(*parents)
        .prefetch_related("account__customers")
        .order_by("-occurred_at", "-pk")[:HISTORY_LIMIT]
    )
    email_rows = (
        emails.select_related(*parents)
        .prefetch_related("account__customers")
        .order_by("-sent_at", "-pk")[:HISTORY_LIMIT]
    )
    ticket_rows = (
        tickets.select_related(*parents)
        .prefetch_related("account__customers")
        .order_by("-opened_at", "-pk")[:HISTORY_LIMIT]
    )
    return {
        "contact_id": contact.pk,
        "sentiment": contact.sentiment,
        "sentiment_source": contact.sentiment_source,
        "sentiment_evidence": contact.sentiment_evidence,
        "counts": {
            "calls": calls.count(),
            "emails": emails.count(),
            "tickets": tickets.count(),
        },
        "calls": [_call(row, visible_customer_ids) for row in call_rows],
        "emails": [_email(row, visible_customer_ids) for row in email_rows],
        "tickets": [_ticket(row, visible_customer_ids) for row in ticket_rows],
    }
```

- [ ] **Step 4: Serve it, and put `/interactions/` under the same rules**

**Find** in `services/customers/views.py`:

```text
class ContactInteractionsView(views.APIView):
    """GET /api/v1/contacts/<id>/interactions/ — what this person's
    sentiment rests on: every classified call they were on, email from
    their address and ticket they raised, newest first, each with its own
    sentiment. Same scoping as ContactDetailView."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        from .contact_sentiment import interactions_for, score

        contact = get_object_or_404(
            Contact.objects.filter(visible_children_q(request.user)).distinct(), pk=pk
        )
        rows = interactions_for(contact)
        value, label = score(rows)
        items = []
        for row in rows:
```

**Replace with:**

```text
def _visible_contact(request, pk):
    """A contact the caller may open, or 404 — the same scope as
    ContactDetailView, whether or not the id exists."""
    # SOC2:AUTH-02 a contact follows its organisation's or account's visibility
    return get_object_or_404(
        Contact.objects.filter(visible_children_q(request.user)).distinct(), pk=pk
    )


class ContactHistoryView(views.APIView):
    """GET /api/v1/contacts/<id>/history/ — the person's calls, emails and
    tickets, newest first, each under its own record rule for the caller,
    with the sentiment breakdown (contact_history.py). A contact the caller
    cannot open is a 404."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        from .contact_history import build_history

        return Response(build_history(_visible_contact(request, pk), request.user))


class ContactInteractionsView(views.APIView):
    """GET /api/v1/contacts/<id>/interactions/ — what this person's
    sentiment rests on: every classified call they were on, email from
    their address and ticket they raised, newest first, each with its own
    sentiment. Same scoping as ContactDetailView, and each row under its own
    record rule, as in /history/, which replaces this endpoint once the
    Contacts page no longer calls it."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        from .contact_history import history_querysets
        from .contact_sentiment import interactions_for, score

        contact = _visible_contact(request, pk)
        rows = interactions_for(contact)
        value, label = score(rows)
        readable = {
            kind: set(queryset.values_list("pk", flat=True))
            for kind, queryset in zip(
                ("call", "email", "ticket"), history_querysets(contact, request.user)
            )
        }
        items = []
        for row in rows:
            if row["record"].pk not in readable[row["kind"]]:
                continue
```

- [ ] **Step 5: Route it**

**Find** in `config/urls.py`:

```text
    ContactDetailView,
    ContactInteractionsView,
```

**Replace with:**

```text
    ContactDetailView,
    ContactHistoryView,
    ContactInteractionsView,
```

**Find** in `config/urls.py`:

```text
    path("api/v1/contacts/<int:pk>/", ContactDetailView.as_view(), name="contact-detail"),
```

**Replace with:**

```text
    path("api/v1/contacts/<int:pk>/", ContactDetailView.as_view(), name="contact-detail"),
    path(
        "api/v1/contacts/<int:pk>/history/",
        ContactHistoryView.as_view(),
        name="contact-history",
    ),
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.customers.tests.test_contact_history services.customers.tests.test_contact_sentiment --noinput`
Expected: PASS, including the 10-query pin at both sizes.

- [ ] **Step 7: Commit**

```bash
git add services/customers/contact_history.py services/customers/views.py config/urls.py services/customers/tests/test_contact_history.py
git commit -m "feat(contacts): a person's history, each call, email and ticket under its own rule" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: The contacts list: filters, parents and a summary

**Files:**
- Modify: `services/customers/views.py` (`ContactListView`, new `DECISION_ROLES`, `_int_param`)
- Modify: `services/customers/serializers.py` (`ContactSerializer`)
- Test: `services/customers/tests/test_contact_list.py`

**Interfaces:**
- Consumes: `scoping.visible_children_q`, `scoping.visible_customers`.
- Produces: `GET /api/v1/contacts/` accepts `search`, `customer` (alias `company`), `account`, `sentiment` and `role`; each row adds `organisation: {id, name} | null` and `account: {id, name} | null`; the page adds `summary: {total, positive, neutral, negative, decision_makers}`. `ContactSerializer` reads an optional `visible_customer_ids` context set; without it (the nested lists) `companies` is unfiltered, as today.

- [ ] **Step 1: Write the failing test**

**Create** `services/customers/tests/test_contact_list.py`:

```python
"""GET /api/v1/contacts/ — the Contacts page's list: its filters, the
organisation and account on every row, a summary over the whole filtered
set, and a query count that does not grow with the page."""

from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers.models import Account, Contact, Customer
from services.customers.tests.test_views import blind_to_one_account

URL = "/api/v1/contacts/"


class Fixture(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="x",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
        )
        self.pizza = Customer.objects.create(organisation=self.org, name="Pizza Hut")
        self.kraft = Customer.objects.create(organisation=self.org, name="Kraft Heinz")
        self.emea = Account.objects.create(name="EMEA")
        self.emea.customers.add(self.pizza)
        self.sam = self.person("Sam Pizza", customer=self.pizza, role="decision_maker")
        self.uma = self.person("Uma Hut", account=self.emea, sentiment="negative", role="champion")
        self.kim = self.person(
            "Kim Kraft", customer=self.kraft, sentiment="positive", role="economic_buyer"
        )

    def person(self, name, sentiment="neutral", role="other", **parent):
        return Contact.objects.create(
            name=name,
            email=f"{name.split()[0].lower()}@example.com",
            sentiment=sentiment,
            role=role,
            **parent,
        )

    def names(self, query="", user=None):
        self.client.force_authenticate(user or self.admin)
        response = self.client.get(URL + query)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return sorted(row["name"] for row in response.data["results"])


class FilterTests(Fixture):
    def test_the_organisation_filter_takes_its_accounts_contacts_too(self):
        self.assertEqual(self.names(f"?customer={self.pizza.id}"), ["Sam Pizza", "Uma Hut"])
        # The older name still works for the page that sends it.
        self.assertEqual(self.names(f"?company={self.pizza.id}"), ["Sam Pizza", "Uma Hut"])

    def test_the_account_filter(self):
        self.assertEqual(self.names(f"?account={self.emea.id}"), ["Uma Hut"])

    def test_the_sentiment_and_role_filters(self):
        self.assertEqual(self.names("?sentiment=negative"), ["Uma Hut"])
        self.assertEqual(self.names("?role=economic_buyer"), ["Kim Kraft"])

    def test_filters_combine_with_search(self):
        self.assertEqual(self.names(f"?customer={self.pizza.id}&search=uma"), ["Uma Hut"])

    def test_unusable_values_are_ignored(self):
        everyone = ["Kim Kraft", "Sam Pizza", "Uma Hut"]
        self.assertEqual(self.names("?customer=x&account=&sentiment=furious&role=boss"), everyone)


class RowTests(Fixture):
    def row(self, name, query=""):
        self.client.force_authenticate(self.admin)
        rows = self.client.get(URL + query).data["results"]
        return next(row for row in rows if row["name"] == name)

    def test_an_account_contact_names_its_organisation_and_account(self):
        row = self.row("Uma Hut")
        self.assertEqual(row["organisation"], {"id": self.pizza.id, "name": "Pizza Hut"})
        self.assertEqual(row["account"], {"id": self.emea.id, "name": "EMEA"})

    def test_an_organisation_contact_has_no_account(self):
        row = self.row("Sam Pizza")
        self.assertEqual(row["organisation"], {"id": self.pizza.id, "name": "Pizza Hut"})
        self.assertIsNone(row["account"])

    def test_a_row_carries_its_sentiment_evidence_and_last_contact(self):
        row = self.row("Sam Pizza")
        for field in ("sentiment", "sentiment_source", "sentiment_evidence", "last_contacted_at"):
            self.assertIn(field, row)

    def test_an_organisation_the_viewer_cannot_open_is_not_named(self):
        # Joint belongs to two organisations: the viewer's own, and the
        # colleague's Secret Co, which the viewer cannot open. The viewer sees
        # Joint through their own organisation.
        viewer, _seen, _hidden = blind_to_one_account(self.pizza)
        colleague = self.pizza.owner
        mine = Customer.objects.create(organisation=self.org, name="Mine", owner=viewer)
        secret = Customer.objects.create(organisation=self.org, name="Secret Co", owner=colleague)
        joint = Account.objects.create(name="Joint", owner=colleague)
        joint.customers.add(mine, secret)
        self.person("Jo Joint", account=joint)
        self.client.force_authenticate(viewer)
        row = next(r for r in self.client.get(URL).data["results"] if r["name"] == "Jo Joint")
        self.assertEqual(row["companies"], [{"id": mine.id, "name": "Mine"}])
        self.assertEqual(row["organisation"], {"id": mine.id, "name": "Mine"})
        self.assertEqual(row["account"], {"id": joint.id, "name": "Joint"})


class SummaryTests(Fixture):
    def summary(self, query="", user=None):
        self.client.force_authenticate(user or self.admin)
        return self.client.get(URL + query).data["summary"]

    def test_the_summary_counts_the_whole_filtered_set(self):
        for n in range(30):
            self.person(f"Guest{n} Pizza", customer=self.pizza, sentiment="positive")
        self.assertEqual(
            self.summary(f"?customer={self.pizza.id}"),
            {"total": 32, "positive": 30, "neutral": 1, "negative": 1, "decision_makers": 1},
        )
        self.assertEqual(self.summary()["decision_makers"], 2)

    def test_the_summary_follows_visibility(self):
        viewer, seen, hidden = blind_to_one_account(self.pizza)
        self.person("Sue Seen", account=seen, sentiment="positive")
        self.person("Hal Hidden", account=hidden, sentiment="negative")
        # The viewer sees Pizza Hut (through Seen), its own contacts and
        # Seen's, and the unowned EMEA and Kraft Heinz.
        self.assertEqual(self.names(user=viewer), ["Kim Kraft", "Sam Pizza", "Sue Seen", "Uma Hut"])
        self.assertEqual(self.summary(user=viewer)["total"], 4)


class QueryCountTests(Fixture):
    def test_the_query_count_does_not_grow_with_the_page(self):
        self.client.force_authenticate(self.admin)
        # The first read resolves the caller's membership and org chart, which
        # are memoised on the user; what is pinned is every read after it.
        self.client.get(URL)
        for batch in range(2):
            for n in range(6):
                self.person(f"Org{batch}{n} Person", customer=self.pizza)
                self.person(f"Acc{batch}{n} Person", account=self.emea)
            with self.assertNumQueries(5):
                response = self.client.get(URL)
            self.assertEqual(response.data["count"], 3 + 12 * (batch + 1))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python manage.py test services.customers.tests.test_contact_list --noinput`
Expected: FAIL/ERROR. `KeyError: 'summary'`, `KeyError: 'organisation'`, the account, sentiment and role filters return everyone, and the Joint row lists `Secret Co`.

- [ ] **Step 3: Filters and the summary**

**Find** in `services/customers/views.py`:

```text
class ContactListView(generics.ListAPIView):
    """GET /api/v1/contacts/ — every Contact across every Customer/
    Account the caller's own organisation owns, organization-level and
    account-level alike. Powers the standalone Contacts page
    (react-ts-app's /contacts/list) — the one place a Contact is
    browsed independent of which Customer/Account it belongs to, so
    this is the one Contact view that isn't nested under
    /customers/<id>/... (mounted directly at /api/v1/contacts/ in the
    project's root urls.py instead).

    Paginated with the shared DEFAULT_PAGINATION_CLASS/PAGE_SIZE —
    unlike every other List view in this file, which turns pagination
    off for what's normally a single entity's already-small nested
    list — since this one can span every contact the tenant has, same
    reasoning as CustomerListCreateView.

    `?search=` matches name/email/role (substring, case-insensitive),
    same convention as CustomerListCreateView's own search. `?company=
    <customer_id>` filters to one company, matching a contact directly
    on that Customer or on any of its Accounts."""

    serializer_class = ContactSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # `.distinct()` because `account__customers__organisation=` fans
        # out one row per matching linked Customer on the account — an
        # account-level Contact whose Account is linked to two+
        # Customers in this same organisation would otherwise appear
        # more than once.
        queryset = (
            Contact.objects.filter(visible_children_q(self.request.user))
            .select_related("customer", "account")
            .prefetch_related("account__customers")
            .distinct()
        )

        search = self.request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(email__icontains=search) | Q(role__icontains=search)
            )

        company = self.request.query_params.get("company")
        if company:
            try:
                company_id = int(company)
            except ValueError:
                company_id = None
            if company_id is not None:
                queryset = queryset.filter(
                    Q(customer_id=company_id) | Q(account__customers__id=company_id)
                )

        return queryset
```

**Replace with:**

```text
#: The roles the Contacts page counts as decision makers, the same set the
#: organisation page's People summary uses.
DECISION_ROLES = (
    Contact.Role.EXECUTIVE_SPONSOR,
    Contact.Role.DECISION_MAKER,
    Contact.Role.ECONOMIC_BUYER,
)


def _int_param(raw):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


class ContactListView(generics.ListAPIView):
    """GET /api/v1/contacts/ — every Contact the caller may open, across
    every Customer/Account, organisation-level and account-level alike.
    Powers the Contacts page; the one Contact list not nested under
    /customers/<id>/... (mounted in the project's root urls.py).

    Paginated with the shared DEFAULT_PAGINATION_CLASS/PAGE_SIZE, since it
    can span every contact the tenant has.

    Filters (an unusable value is ignored, never a 400):
    `?search=` name/email/role, case-insensitive contains;
    `?customer=<id>` (or the older `?company=`) a contact on that
    organisation or on any of its accounts; `?account=<id>`;
    `?sentiment=positive|neutral|negative`; `?role=<Contact.Role>`.

    Each row carries `organisation` and `account` as `{id, name}` refs (the
    organisation is the first linked one the caller may open) beside
    ContactSerializer's fields. `summary` covers the whole filtered set,
    not the page: `{total, positive, neutral, negative, decision_makers}`."""

    serializer_class = ContactSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # `.distinct()` because `account__customers__organisation=` fans
        # out one row per matching linked Customer on the account — an
        # account-level Contact whose Account is linked to two+
        # Customers in this same organisation would otherwise appear
        # more than once.
        # SOC2:AUTH-02 a contact follows its organisation's or account's visibility
        queryset = (
            Contact.objects.filter(visible_children_q(self.request.user))
            .select_related("customer", "account")
            .prefetch_related("account__customers")
            .distinct()
        )
        params = self.request.query_params

        search = params.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(email__icontains=search) | Q(role__icontains=search)
            )

        customer_id = _int_param(params.get("customer") or params.get("company"))
        if customer_id is not None:
            queryset = queryset.filter(
                Q(customer_id=customer_id) | Q(account__customers__id=customer_id)
            )

        account_id = _int_param(params.get("account"))
        if account_id is not None:
            queryset = queryset.filter(account_id=account_id)

        sentiment = params.get("sentiment")
        if sentiment in Contact.Sentiment.values:
            queryset = queryset.filter(sentiment=sentiment)

        role = params.get("role")
        if role in Contact.Role.values:
            queryset = queryset.filter(role=role)

        return queryset

    def get_serializer_context(self):
        from .scoping import visible_customers

        context = super().get_serializer_context()
        context["visible_customer_ids"] = set(
            visible_customers(self.request.user).values_list("pk", flat=True)
        )
        return context

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        rows = self.get_serializer(page, many=True).data
        response = self.get_paginated_response(rows)
        counted = Contact.objects.filter(pk__in=queryset.values("pk")).aggregate(
            total=Count("pk"),
            positive=Count("pk", filter=Q(sentiment=Contact.Sentiment.POSITIVE)),
            neutral=Count("pk", filter=Q(sentiment=Contact.Sentiment.NEUTRAL)),
            negative=Count("pk", filter=Q(sentiment=Contact.Sentiment.NEGATIVE)),
            decision_makers=Count("pk", filter=Q(role__in=DECISION_ROLES)),
        )
        response.data["summary"] = counted
        return response
```

- [ ] **Step 4: Parents on every row, visible ones only**

**Find** in `services/customers/serializers.py`:

```text
    role_display = serializers.CharField(source="get_role_display", read_only=True)
    companies = serializers.SerializerMethodField()
    account_name = serializers.SerializerMethodField()

    class Meta:
        model = Contact
```

**Replace with:**

```text
    role_display = serializers.CharField(source="get_role_display", read_only=True)
    companies = serializers.SerializerMethodField()
    account_name = serializers.SerializerMethodField()
    organisation = serializers.SerializerMethodField()
    account = serializers.SerializerMethodField()

    class Meta:
        model = Contact
```

**Find** in `services/customers/serializers.py`:

```text
            "companies",
            "account_id",
            "account_name",
        ]
        read_only_fields = ["sentiment_source", "sentiment_evidence", "sentiment_computed_at"]
```

**Replace with:**

```text
            "companies",
            "organisation",
            "account_id",
            "account_name",
            "account",
        ]
        read_only_fields = ["sentiment_source", "sentiment_evidence", "sentiment_computed_at"]
```

**Find** in `services/customers/serializers.py`:

```text
    def get_companies(self, obj):
        return [{"id": c.id, "name": c.name} for c in obj.companies]

    def get_account_name(self, obj):
        return obj.account.name if obj.account_id else None


class OpportunitySerializer(serializers.ModelSerializer):
```

**Replace with:**

```text
    def _companies(self, obj):
        """Every parent organisation — only those the caller may open when
        the view passes `visible_customer_ids` (the Contacts page does)."""
        companies = obj.companies
        visible = self.context.get("visible_customer_ids")
        if visible is not None:
            companies = [c for c in companies if c.pk in visible]
        return sorted(companies, key=lambda c: c.pk)

    def get_companies(self, obj):
        return [{"id": c.id, "name": c.name} for c in self._companies(obj)]

    def get_organisation(self, obj):
        companies = self._companies(obj)
        return {"id": companies[0].id, "name": companies[0].name} if companies else None

    def get_account_name(self, obj):
        return obj.account.name if obj.account_id else None

    def get_account(self, obj):
        return {"id": obj.account_id, "name": obj.account.name} if obj.account_id else None


class OpportunitySerializer(serializers.ModelSerializer):
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.customers.tests.test_contact_list services.customers.tests.test_contact services.customers.tests.test_views services.customers.tests.test_contact_sentiment --noinput`
Expected: PASS, including the 5-query pin at both sizes. The nested contact lists and the detail view keep their shape, plus the two new fields.

- [ ] **Step 6: Commit**

```bash
git add services/customers/views.py services/customers/serializers.py services/customers/tests/test_contact_list.py
git commit -m "feat(contacts): list filters by organisation, account, sentiment and role, with a summary" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: End to end, and the docs

**Files:**
- Create: `e2e/test_contacts_flow.py`
- Modify: `docs/API_CONTRACTS.md`, `docs/product/01-prd.md`, `docs/product/05-backend-schema.md`, `docs/data-classification.md`

**Interfaces:**
- Consumes: everything above, over real HTTP.
- Produces: the contract the frontend PR builds on (see "Notes for the frontend plan").

- [ ] **Step 1: Write the e2e flow**

**Create** `e2e/test_contacts_flow.py`:

```python
"""End-to-end tier: real server, real HTTP, real test DB, the classifier
stubbed. A CSM adds people to an organisation and its account and logs two
calls: one the model reads, one with nothing to read. The person's sentiment
follows the call at once; the Contacts list filters to the organisation and
summarises it; the person's history shows both calls, one of them "not
analysable". A peer who cannot open the organisation gets a 404."""

from unittest.mock import patch
from urllib.parse import urlencode

from django.test import LiveServerTestCase

from e2e.http import http_get, http_post

WHEN = "2026-09-20T10:00:00Z"


def negative(batch, **_kwargs):
    return {
        f"{r._meta.model_name}:{r.pk}": {
            "ai_area": "customer_success",
            "ai_category": "onboarding",
            "ai_subcategory": "",
            "sentiment": "negative",
        }
        for r in batch
    }


class ContactsFlowTests(LiveServerTestCase):
    def api(self, path):
        return f"{self.live_server_url}/api/v1{path}"

    def add_user(self, admin, name, email, password):
        status, body = http_post(
            self.api("/auth/users/"),
            {"name": name, "email": email, "password": password},
            token=admin,
        )
        self.assertEqual(status, 201, body)
        status, body = http_post(self.api("/auth/login/"), {"email": email, "password": password})
        self.assertEqual(status, 200, body)
        return body["access"]

    def create(self, path, payload, token):
        status, body = http_post(self.api(path), payload, token=token)
        self.assertEqual(status, 201, body)
        return body

    def read(self, path, token):
        status, body = http_get(self.api(path), token=token)
        self.assertEqual(status, 200, body)
        return body

    def test_full_flow(self):
        # 1. An organisation signs up; its admin adds two CSMs, who log in.
        status, body = http_post(
            self.api("/auth/signup/"),
            {
                "organisation_name": "Acme Inc",
                "name": "Alice Admin",
                "email": "alice@acme.io",
                "password": "supersecret1",
            },
        )
        self.assertEqual(status, 201, body)
        admin = body["access"]
        carl = self.add_user(admin, "Carl CSM", "carl@acme.io", "csmpassword1")
        dana = self.add_user(admin, "Dana CSM", "dana@acme.io", "csmpassword2")

        # 2. Carl's organisation, its account, and one person on each.
        pizza = self.create("/customers/", {"name": "Pizza Hut"}, carl)["id"]
        emea = self.create(f"/customers/{pizza}/accounts/", {"name": "EMEA"}, carl)["id"]
        sam = self.create(
            f"/customers/{pizza}/contacts/",
            {"name": "Sam Pizza", "role": "decision_maker", "email": "sam@pizza.io"},
            carl,
        )
        uma = self.create(
            f"/customers/{pizza}/accounts/{emea}/contacts/",
            {"name": "Uma Hut", "role": "champion", "email": "uma@pizza.io"},
            carl,
        )

        # 3. A call the model reads, with Sam on it: Sam turns negative at once.
        with patch("services.customers.classification.classify_batch", side_effect=negative):
            call = self.create(
                f"/customers/{pizza}/calls/",
                {
                    "title": "Escalation",
                    "occurred_at": WHEN,
                    "summary": "They are unhappy with support.",
                    "participant_ids": [sam["id"]],
                },
                carl,
            )
        self.assertEqual((call["analysis"], call["sentiment"]), ("analysed", "negative"))
        # 4. A call with nothing to read is marked, never guessed.
        with patch("services.customers.classification.classify_batch") as model:
            empty = self.create(
                f"/customers/{pizza}/accounts/{emea}/calls/",
                {"title": "Weekly sync", "occurred_at": WHEN, "participant_ids": [sam["id"]]},
                carl,
            )
        model.assert_not_called()
        self.assertEqual(empty["analysis"], "not_analysable")

        # 5. The list filters to the organisation, names parents, and summarises.
        page = self.read("/contacts/?" + urlencode({"customer": pizza}), carl)
        rows = {r["name"]: r for r in page["results"]}
        self.assertEqual(set(rows), {"Sam Pizza", "Uma Hut"})
        self.assertEqual(rows["Uma Hut"]["account"], {"id": emea, "name": "EMEA"})
        self.assertEqual(rows["Sam Pizza"]["organisation"], {"id": pizza, "name": "Pizza Hut"})
        self.assertEqual(rows["Sam Pizza"]["sentiment"], "negative")
        self.assertEqual(rows["Sam Pizza"]["sentiment_evidence"]["calls"], 1)
        self.assertEqual(
            page["summary"],
            {"total": 2, "positive": 0, "neutral": 1, "negative": 1, "decision_makers": 1},
        )
        negative_only = self.read("/contacts/?" + urlencode({"sentiment": "negative"}), carl)
        self.assertEqual([r["name"] for r in negative_only["results"]], ["Sam Pizza"])

        # 6. Sam's history: both calls, newest first by time then id.
        history = self.read(f"/contacts/{sam['id']}/history/", carl)
        self.assertEqual(
            [(c["title"], c["analysis"], c["sentiment"]) for c in history["calls"]],
            [("Weekly sync", "not_analysable", None), ("Escalation", "analysed", "negative")],
        )
        self.assertEqual(history["calls"][0]["account"], {"id": emea, "name": "EMEA"})
        self.assertEqual(history["sentiment_evidence"]["calls"], 1)

        # 7. Dana cannot open Carl's organisation, so not its people either.
        for person in (sam, uma):
            status, _ = http_get(self.api(f"/contacts/{person['id']}/history/"), token=dana)
            self.assertEqual(status, 404)
        self.assertEqual(self.read("/contacts/", dana)["summary"]["total"], 0)
```

- [ ] **Step 2: Run it**

Run: `venv/bin/python manage.py test e2e.test_contacts_flow --noinput`
Expected: PASS. This tier checks the assembled flow, so it passes as soon as it is written. If it fails, the task that owns the failing piece is not done.

- [ ] **Step 3: CallSense in `docs/API_CONTRACTS.md`**

**Find** in `docs/API_CONTRACTS.md`:

```text
error). The new call is classified for sentiment straight away so the
Account Pulse counts it. Audit event `call.log`.
```

**Replace with:**

```text
error). The new call is classified straight away by the one helper every
path that creates a call runs (`calls.classify_call`): the classifier reads
the title, then the transcript, else the summary. A call with no
transcript, no summary and a generic title ("Weekly sync") is marked not
analysable without a model call, and so is one the model declines. A
classifier failure never blocks the call: it stays `pending` for the
nightly `classify_interactions`. Every row carries `analysis`: `pending`,
`not_analysable` or `analysed`; `sentiment` is a reading only when it is
`analysed`. Audit event `call.log`.
```

- [ ] **Step 4: Contact sentiment in `docs/API_CONTRACTS.md`**

**Find** in `docs/API_CONTRACTS.md`:

```text
Recomputed whenever records are classified (`classify_records`), when a
call is logged, and for every contact in `run_health_maintenance`.
```

**Replace with:**

```text
Only the contact's own tenant counts: an email or ticket from their
address under another organisation is never evidence. A call marked not
analysable is not evidence either, though being on it still moves
`last_contacted_at`.

Recomputed whenever records are classified (`classify_records`, and each
batch of the nightly `classify_interactions`), when a call is logged, and
for every contact in `run_health_maintenance`.
```

- [ ] **Step 5: The history endpoint in `docs/API_CONTRACTS.md`**

**Find** in `docs/API_CONTRACTS.md`:

```text
### `GET /api/v1/contacts/<id>/interactions/`

What the sentiment rests on: `{sentiment, score, source, evidence,
interactions: [{kind: "call"|"email"|"ticket", id, title, snippet, when,
sentiment, ai_category}]}`, newest first. Same scoping as the contact.
```

**Replace with:**

````text
### `GET /api/v1/contacts/<id>/history/`

Auth: `IsAuthenticated`. The person's calls, emails and tickets, newest
first, for the Contacts page's profile panel
(`services/customers/contact_history.py`). A contact the caller cannot
open is a `404`, the same scope as `GET /contacts/<id>/`.

Every row is twice filtered: by its own company (a record on an account
the caller cannot open is left out, `visible_children_q`), then by its
own rule — mail by its mailbox owner and their chain (`visible_emails`),
tickets by department (`visible_tickets`). Calls are the ones the person
was on (`Call.participants`); emails are from their address and tickets
raised by it, inside the contact's own tenant only. Each list holds the
newest 100; `counts` is the whole visible total per kind.

`analysis` is `pending` (not read yet), `not_analysable` (a call with
nothing to judge: the UI says "Not enough to analyse") or `analysed`.
`sentiment` is `null` unless `analysed`. `classification` holds the
taxonomy labels, blank when unset. `organisation` and `account` are
`{id, name}` or `null`; an account's organisation is the first linked
one the caller may open. `link.url` is an `http(s)` recording or ticket
URL, else `null`; `link.thread_id` is the email's thread.

**Response `200`**
```json
{
  "contact_id": 2,
  "sentiment": "neutral",
  "sentiment_source": "computed",
  "sentiment_evidence": {
    "score": 0.1,
    "calls": 6,
    "emails": 2,
    "tickets": 0,
    "positive": 3,
    "neutral": 4,
    "negative": 1,
    "latest_at": "2026-09-12T10:00:00+00:00"
  },
  "counts": { "calls": 7, "emails": 2, "tickets": 1 },
  "calls": [
    {
      "id": 41,
      "title": "Renewal readiness",
      "occurred_at": "2026-09-12T10:00:00+00:00",
      "duration_minutes": 45,
      "host_name": "Carl CSM",
      "summary": "They want the enterprise tier.",
      "analysis": "analysed",
      "sentiment": "positive",
      "classification": {
        "area": "Customer Success",
        "category": "Account Management",
        "subcategory": ""
      },
      "organisation": { "id": 6, "name": "Apple Inc" },
      "account": { "id": 31, "name": "Apple EMEA" },
      "link": { "url": "https://zoom.us/rec/1" }
    }
  ],
  "emails": [
    {
      "id": 88,
      "subject": "Thanks for the call",
      "sent_at": "2026-09-11T08:00:00+00:00",
      "sender_name": "James Wilson",
      "snippet": "Thanks for walking us through the plan…",
      "analysis": "analysed",
      "sentiment": "positive",
      "classification": { "area": "", "category": "", "subcategory": "" },
      "organisation": { "id": 6, "name": "Apple Inc" },
      "account": null,
      "link": { "thread_id": "t-19" }
    }
  ],
  "tickets": [
    {
      "id": 7,
      "ticket_number": "ZD-1042",
      "title": "Export fails",
      "status": "open",
      "status_display": "Open",
      "opened_at": "2026-09-10",
      "analysis": "pending",
      "sentiment": null,
      "classification": { "area": "", "category": "", "subcategory": "" },
      "organisation": { "id": 6, "name": "Apple Inc" },
      "account": null,
      "link": { "url": "https://apple.zendesk.com/t/1042" }
    }
  ]
}
```

### `GET /api/v1/contacts/<id>/interactions/` (retiring)

What the sentiment rests on: `{sentiment, score, source, evidence,
interactions: [{kind: "call"|"email"|"ticket", id, title, snippet, when,
sentiment, ai_category}]}`, newest first. Same scoping as the contact,
and each row under its own record rule, as in `/history/`. Kept only
until the Contacts page moves to `/history/`, then removed.
````

- [ ] **Step 6: The list in `docs/API_CONTRACTS.md`**

In the `### GET /api/v1/contacts/` section, replace everything from `Query params:` up to (not including) `### GET /api/v1/contacts/stats/`.

**Find** in `docs/API_CONTRACTS.md`:

````text
Query params:
- `?search=` — matches `name`/`email`/`role` (substring, case-
  insensitive), same convention as the Customer list's own search.
- `?company=<customer_id>` — filters to one company, matching a
  contact directly on that `Customer` or on any of its `Account`s.

**Response `200`**
```json
{
  "count": 16,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 2,
      "name": "James Wilson",
      "role": "champion",
      "role_display": "Champion",
      "email": "j.wilson@apple.com",
      "phone": "+1 (408) 555-0456",
      "status": "active",
      "sentiment": "positive",
      "last_contacted_at": "2026-09-02T04:35:18.707403Z",
      "companies": [{ "id": 6, "name": "Apple Inc" }],
      "account_id": null,
      "account_name": null
    }
  ]
}
```

````

**Replace with:**

````text
Filters (an unusable value is ignored, never a `400`):
- `?search=` — matches `name`/`email`/`role` (substring, case-
  insensitive), same convention as the Customer list's own search.
- `?customer=<customer_id>` — one organisation: a contact directly on
  it or on any of its accounts. `?company=` is the older name and still
  works.
- `?account=<account_id>` — one account's contacts.
- `?sentiment=positive|neutral|negative`.
- `?role=` — one `Contact.Role` value.

Every row adds `organisation` and `account`, each `{id, name}` or
`null`: the organisation is the first linked one the caller may open,
and `companies` lists only those. `sentiment_evidence` holds the counts
behind a computed sentiment (see "Contact sentiment is computed").

`summary` covers the whole filtered set, not the page: `total`, the
count per sentiment, and `decision_makers` (roles `executive_sponsor`,
`decision_maker` and `economic_buyer`, the set the organisation page's
People summary uses). Five queries whatever the page size.

**Response `200`**
```json
{
  "count": 16,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 2,
      "name": "James Wilson",
      "role": "champion",
      "role_display": "Champion",
      "email": "j.wilson@apple.com",
      "phone": "+1 (408) 555-0456",
      "language": "",
      "status": "active",
      "sentiment": "negative",
      "sentiment_source": "computed",
      "sentiment_evidence": {
        "score": -0.4,
        "calls": 2,
        "emails": 1,
        "tickets": 0,
        "positive": 0,
        "neutral": 1,
        "negative": 2,
        "latest_at": "2026-09-12T10:00:00+00:00"
      },
      "sentiment_computed_at": "2026-09-12T10:05:00Z",
      "last_contacted_at": "2026-09-12T10:00:00Z",
      "companies": [{ "id": 6, "name": "Apple Inc" }],
      "organisation": { "id": 6, "name": "Apple Inc" },
      "account_id": 31,
      "account_name": "Apple EMEA",
      "account": { "id": 31, "name": "Apple EMEA" }
    }
  ],
  "summary": {
    "total": 16,
    "positive": 7,
    "neutral": 6,
    "negative": 3,
    "decision_makers": 5
  }
}
```

````

- [ ] **Step 7: The PRD**

**Find** in `docs/product/01-prd.md`:

```text
| Contacts: list, detail, CRUD, computed sentiment | Built | Sentiment read from that contact's own calls, tickets and emails |
```

**Replace with:**

```text
| Contacts: list, detail, CRUD, computed sentiment | Built | Sentiment read from that contact's own calls, tickets and emails, in their own tenant. The list filters by organisation, account, sentiment and role, names each person's organisation and account, and summarises the whole filtered set. `GET /contacts/<id>/history/` gives their calls, emails and tickets, each under its own record rule (backend) |
```

**Find** in `docs/product/01-prd.md`:

```text
| CallSense | Built | Log a call, attach a transcript, model writes the summary, call is classified immediately. An organisation's list includes its visible accounts' calls, tagged |
```

**Replace with:**

```text
| CallSense | Built | Log a call, attach a transcript, model writes the summary, call is classified immediately from its transcript, else its summary, else its title. A call with nothing to read is "not analysable", never guessed. An organisation's list includes its visible accounts' calls, tagged |
```

**Find** in `docs/product/01-prd.md`:

```text
| Interaction classification | Built | Claude assigns sentiment plus a three-level taxonomy to emails, calls and tickets |
```

**Replace with:**

```text
| Interaction classification | Built | Claude assigns sentiment plus a three-level taxonomy to emails, calls and tickets. On create, and nightly on the server (`classify_interactions --limit 300`), one tenant at a time and metered to it |
```

**Find** in `docs/product/01-prd.md`:

```text
| 2026-09-28 | Ask Revenact on the organisation page (backend) |
```

**Replace with:**

```text
| 2026-09-28 | Ask Revenact on the organisation page (backend) |
| 2026-09-28 | Contacts, delivery 1 (backend): every call analysed or marked not analysable, nightly classification, contact history, contacts list filters and summary |
```

- [ ] **Step 8: The schema doc**

**Find** in `docs/product/05-backend-schema.md`:

```text
| `Call` | `title`, `host_name`, `occurred_at`, `duration_minutes`, `summary`, `connector`, `logged_by`, `transcript` (1:1 Attachment), `recording_url`, `participants` (M2M Contact) |
```

**Replace with:**

```text
| `Call` | `title`, `host_name`, `occurred_at`, `duration_minutes`, `summary`, `connector`, `logged_by`, `transcript` (1:1 Attachment), `recording_url`, `participants` (M2M Contact), `not_analysable` (read with nothing to judge; `ai_classified_at` set, `sentiment` not evidence). `analysis` (a property on every `AIClassified`) reads `pending`, `not_analysable` or `analysed` |
```

**Find** in `docs/product/05-backend-schema.md`:

```text
| Synced emails | Mailbox owner and their chain; rows with no mailbox owner are visible to all | `services/mail/visibility.py` |
```

**Replace with:**

```text
| Synced emails | Mailbox owner and their chain; rows with no mailbox owner are visible to all | `services/mail/visibility.py` |
| A contact's history (`/contacts/<id>/history/`) | The contact must be visible; then each call, email and ticket by its own company (`visible_children_q`) and its own rule (mail, tickets above), inside the contact's own tenant | `services/customers/contact_history.py` |
```

- [ ] **Step 9: Data classification**

**Find** in `docs/data-classification.md`:

```text
`sentiment` is computed from their own classified calls (`Call.participants`), emails and tickets (`contact_sentiment.py`); `sentiment_evidence` holds counts only, never text |
```

**Replace with:**

```text
`sentiment` is computed from their own classified calls (`Call.participants`), emails and tickets in their own tenant (`contact_sentiment.py`); `sentiment_evidence` holds counts only, never text. Their history (`contact_history.py`) shows each record only under its own rule |
```

- [ ] **Step 10: Check the docs format**

Run: `venv/bin/ruff format --check docs e2e && venv/bin/ruff check e2e`
Expected: every file already formatted, no lint errors.

- [ ] **Step 11: Commit**

```bash
git add e2e/test_contacts_flow.py docs/API_CONTRACTS.md docs/product/01-prd.md docs/product/05-backend-schema.md docs/data-classification.md
git commit -m "docs(contacts): contacts list, history and call analysis contracts, with the e2e flow" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Full verification

**Files:** none are created. If a step uncovers something, fix it in the task that owns it, then re-run from Step 1.

- [ ] **Step 1: Run the whole suite**

Run: `venv/bin/python manage.py test --parallel auto --noinput`
Expected: every test passes. Record the final `Ran N tests … OK` line. The suite must run in parallel, because a serial run hits CI's 30-minute timeout.

- [ ] **Step 2: Lint, format and migrations**

Run:

```bash
venv/bin/ruff check . && venv/bin/ruff format --check .
venv/bin/python manage.py makemigrations --check --dry-run
venv/bin/python manage.py migrate --plan | grep 0048_call_not_analysable
```

Expected:
- no lint errors and no files to reformat (this includes the Markdown docs and this plan);
- "No changes detected";
- `customers.0048_call_not_analysable` in the plan.

- [ ] **Step 3: Run the security gates as CI runs them**

Run (Docker; `semgrep` is not installed locally):

```bash
docker run --rm -v "$PWD:/src" -w /src semgrep/semgrep semgrep scan --error --metrics=off --config p/security-audit --config p/secrets --config p/owasp-top-ten --config p/django --exclude .claude --exclude venv --exclude '**/tests/**' --exclude '**/migrations/**' --exclude e2e services/customers config
python3 .claude/skills/soc2-dev/scripts/soc2_scan.py . --format md --fail-on critical
```

Expected:
- semgrep reports 0 findings. It scans files tracked by git, so run it after the Task 9 commit.
- the soc2 scan finds nothing new under `services/` or `e2e/`. Locally it exits 1 on the git-ignored, symlinked `.env`. That finding is identical on `main` (`e1f1374`), and CI has no `.env`. Compare against a run on `main` rather than reading the exit code.
- every new visibility point carries `# SOC2:AUTH-02`: `ContactListView.get_queryset`, `_visible_contact` and `contact_history.history_querysets`.

- [ ] **Step 4: Smoke test against the seeded dev database**

Run:

```bash
venv/bin/python manage.py migrate --noinput
venv/bin/python manage.py shell -c "
from services.accounts.models import User
from services.customers.models import Contact
from services.customers.scoping import visible_children_q
from services.customers.contact_history import build_history
user = User.objects.filter(role=User.Role.ADMIN).order_by('id').first()
contact = Contact.objects.filter(visible_children_q(user)).exclude(calls=None).distinct().first()
h = build_history(contact, user)
print(contact.name, h['counts'], [(c['title'], c['analysis'], c['sentiment']) for c in h['calls'][:3]])
"
```

Then, with that admin's token, `curl` `GET /api/v1/contacts/?sentiment=negative` and `GET /api/v1/contacts/<id>/history/`. Expected: the list's `summary.total` equals `count`, and the history's calls match the shell output. `venv/bin/python manage.py classify_interactions --limit 300 --dry-run` reports the pending work per tenant without spending anything.

This migrates the local dev database to `0048`. Before switching that database back to `main`, run `venv/bin/python manage.py migrate customers 0047`: the new column has no database default, so `main` could not insert a call.

- [ ] **Step 5: Report**

Use superpowers:verification-before-completion. Report the test count and the ruff, migration, semgrep and soc2 results. Then hand off to superpowers:finishing-a-development-branch. The backend PR merges and deploys first. The infra PR (Task 6) merges and is synced after that, and the frontend PR merges last.

---

## Notes for the frontend plan (delivery 1, react-ts-app)

- **List:** `GET /api/v1/contacts/?search=&customer=&account=&sentiment=&role=&page=`. It returns `{count, next, previous, results, summary}`. `summary` is `{total, positive, neutral, negative, decision_makers}` over the whole filtered set. Each row carries `id, name, role, role_display, email, phone, language, status, sentiment, sentiment_source, sentiment_evidence {score, calls, emails, tickets, positive, neutral, negative, latest_at}, sentiment_computed_at, last_contacted_at, companies [{id, name}], organisation {id, name} | null, account_id, account_name, account {id, name} | null`. "n calls" is `sentiment_evidence.calls` (0 or absent for a hand-set sentiment). Send `customer`, not `company`.
- **History:** `GET /api/v1/contacts/<id>/history/`. It returns `{contact_id, sentiment, sentiment_source, sentiment_evidence, counts {calls, emails, tickets}, calls [], emails [], tickets []}`, newest first, 100 per kind at most. A call has `{id, title, occurred_at, duration_minutes, host_name, summary, analysis, sentiment, classification {area, category, subcategory}, organisation, account, link {url}}`. An email has `{id, subject, sent_at, sender_name, snippet, analysis, sentiment, classification, organisation, account, link {thread_id}}`. A ticket has `{id, ticket_number, title, status, status_display, opened_at, analysis, sentiment, classification, organisation, account, link {url}}`. A `404` means the viewer cannot open the person.
- **Reading state:** `analysis` is `"pending" | "not_analysable" | "analysed"`. Show "Not enough to analyse" for `not_analysable`. `sentiment` is `null` unless `analysed`. The org page's calls (`GET /customers/<id>/calls/`) carry the same `analysis` (spec §5).
- **Retire** `fetchContactInteractions` (`/contacts/<id>/interactions/`) in the frontend PR. The backend removes that endpoint in a follow-up once no deployed page calls it.

## Self-review

**Spec coverage.**

| Requirement | Where it is covered |
|---|---|
| §1 classified on create, one helper on every path, a failure never blocks | Task 3 (`classify_call`, `CreatePathTests`, `OneCreatePathTests`), Task 9 e2e |
| §1 connector sync | Pre-flight #1: no connector creates calls; the guard test forces a future one through the helper; ticket and mail sync keep `classify_records` (Task 3 Step 6 runs their suites) |
| §1 transcript, then summary, then title | Task 2 (`call_text`, `CallTextTests`), Task 3 (`test_a_pasted_transcript_is_what_the_classifier_reads`) |
| §1 not analysable, `ai_classified_at` set, readable state, never guessed | Task 1 (field, `analysis`, `mark_not_analysable`), Task 3 (generic title with no model call, declined call), Task 7 (`sentiment: null`) |
| §1 nightly with a limit, scoped to calls, emails and tickets, metered, clean budget stop | Task 5 (`NightlyCommandTests`), Task 6 (schedule) |
| §1 recompute on classify | Task 3 (`test_classifying_updates_the_people_on_the_call`), Task 5 (`test_classifying_moves_the_people_on_the_call`), Task 9 e2e |
| §2 the mix stays | Task 4 changes only which records count; weights and thresholds untouched |
| §2 breakdown served on the contact | existing `sentiment_evidence`; Task 7 (`test_the_breakdown_comes_with_it`), Task 8 (`test_a_row_carries_its_sentiment_evidence_and_last_contact`) |
| §2 history: fields, newest first, emails and tickets | Task 7 `HistoryShapeTests` |
| §2 history privacy: accounts, owner-only mail, ticket departments, 404 | Task 7 `HistoryPrivacyTests` with `blind_to_one_account` |
| §2 replaces or extends `/interactions/` | Pre-flight #7; Task 7 (`test_the_old_interactions_endpoint_reads_under_the_same_rules`) |
| §2 list filters, row fields, summary, pinned count | Task 8 (`FilterTests`, `RowTests`, `SummaryTests`, `QueryCountTests` at 5) |
| §6 schedule in `revenact-infra` | Task 6 |
| §7 backend tests | Tasks 1–9, three tiers |
| Docs, ruff on Markdown | Task 9, Task 10 Step 2 |
| Full suite `--parallel auto`, ruff, makemigrations, semgrep | Task 10 |

**Placeholder scan.** Every code step has complete code or an exact find/replace. Every doc step gives the exact text and a unique anchor. Both query pins are measured numbers, and the plan says to investigate if one moves.

**Type consistency.**
- `classify_call(call, *, transcript_text=None, user=None)` is defined in Task 3 and called that way in `perform_create` and the tests.
- `mark_not_analysable(call)` (Task 1) is used in Tasks 3, 4, 5 and 7. `has_something_to_read(call)` (Task 2) is used in Tasks 3 and 5.
- `organisation_id_of(contact)` and `in_organisation_q(organisation_id)` (Task 4) are used by `history_querysets` (Task 7).
- `analysis` values `pending | not_analysable | analysed` are the same in the model, `CallSerializer`, the history and the docs.
- `summary` keys `total, positive, neutral, negative, decision_makers` are the same in the view, the tests, the e2e and the docs.
