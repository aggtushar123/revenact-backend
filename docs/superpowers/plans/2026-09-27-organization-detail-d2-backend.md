# Organisation page, delivery 2: backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the organisation page's People, Deals & risks and Files tabs and the Surveys page what they need: files and calls on an organisation roll up its visible accounts' records with `account_id`/`account_name`, `GET /surveys/` accepts `?customer=<id>`, and contacts, opportunities and risks carry `account_id`.

**Architecture:** No new endpoint, no model, no migration. `CustomerFileListView` and `CustomerCallListView` read through `scoping.customer_rollup_q` (the rule the contact, opportunity, risk, survey and canvas roll-ups already use) instead of the organisation's own reverse relation; their POST keeps passing `customer` and `account=None`, so a create on the organisation path stays organisation-level. `AttachmentSerializer` and `CallSerializer` gain `account_id` (a read-only passthrough of the FK) and `account_name`. `SurveyListView` resolves `?customer=` through `visible_customers` and then `customer_rollup_q`; anything else reads as `Survey.objects.none()`. `ContactSerializer`, `OpportunitySerializer` and `RiskSerializer` add `account_id` to `fields`, which DRF builds as a `ReadOnlyField`, as `SurveySerializer` already does.

**Tech Stack:** Django 5, DRF, PostgreSQL, `SimpleTestCase` (unit), `APITestCase` (integration), `LiveServerTestCase` + `e2e.http` (e2e).

**Spec:** `react-ts-app/docs/superpowers/specs/2026-09-27-organization-detail-delivery-2.md`. §6 "Backend" is this plan. §1–§5 and §7 are the frontend plan's; they explain why each field exists (§1 chips filter client-side on `account_id`, §4 Files lists "each tagged with its account", §5 `/surveys?customer=<id>`). §8: this backend PR merges and deploys before the frontend PR.

## Global Constraints

- **Roll-up rule** (spec §6): "Files and calls on an organisation (`/customers/{id}/files/`, `/customers/{id}/calls/`) roll up account-level records under the same rule as the other roll-ups (`customer_rollup_q`: organisation-level plus accounts in `visible_accounts(user)`)."
- **Tags** (spec §6): "Each record carries `account_id` and `account_name`." Both are `null` on an organisation-level record.
- **Creates** (spec §6): "Creating on the organisation path still creates organisation-level records." An `account_id` in the body is ignored.
- **Survey filter** (spec §6): "The survey list (`GET /surveys/`) accepts `?customer=<id>` (organisation-level and its visible accounts' surveys); unknown or invisible ids give an empty list, never an error that confirms existence."
- **Chips** (spec §6): "Contact, opportunity and risk serializers add `account_id` so the chips filter client-side (these lists are unpaginated today)."
- **Tests** (spec §6): "Query counts pinned; privacy tests use the 'blind to one account' setup" (`blind_to_one_account()` in `services/customers/tests/test_views.py`).
- The organisation itself is resolved as today: `get_visible_customer` (404 when the caller cannot open it). The account path (`/customers/{id}/accounts/{id}/…`) keeps its behaviour and gains the same two fields.
- No model and no migration (`makemigrations --check` stays clean).
- Every endpoint change updates `docs/API_CONTRACTS.md` in the same PR (`.claude/skills/api-contracts`). `docs/product/01-prd.md` and `docs/product/05-backend-schema.md` change in the same PR. `docs/data-classification.md` and `docs/audit-events.md` need no change: no new data, no new audit action.
- Three test tiers per `.claude/skills/testing`: unit (`SimpleTestCase`), integration (`APITestCase`), e2e (`LiveServerTestCase` in `e2e/`). Run a label with `venv/bin/python manage.py test <label> --noinput`.
- `venv/bin/ruff check .` and `venv/bin/ruff format --check .` must pass. Line length is 100. **ruff formats Python inside Markdown fences**; the doc edits in this plan use only `json` fences and prose.
- Semgrep runs in CI with `p/security-audit`, `p/secrets`, `p/owasp-top-ten` and `p/django`. Do not return formatted URLs from views.
- Tag object-level visibility with `# SOC2:AUTH-02`.
- Commits are conventional (`feat(customers): …`, `docs(customers): …`, `test(e2e): …`) and end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Pre-flight: where the spec meets the code

| # | Spec says | Code has | Resolution |
|---|---|---|---|
| 1 | Files and calls roll up | `_FileListView.get_queryset` and `_CallListView.get_queryset` read `parent.attachments` / `parent.calls`, so the organisation path shows organisation-level rows only. `test_files_on_an_account` pins that ("0" files on the organisation after an account upload). | Customer path: `Attachment/Call.objects.filter(customer_rollup_q(user, customer))`. Account path unchanged. `test_files_on_an_account` now expects the account's file on the organisation's list, tagged (Task 1). |
| 2 | "Each record carries `account_id` and `account_name`" | `AttachmentSerializer` and `CallSerializer` carry neither. `SurveySerializer` already has `account_id` as a plain passthrough plus an `account_name` method field. | The same two fields on both serializers, on both paths. A call's nested `transcript` is an `AttachmentSerializer` row, so it carries them too. `FileDetailView` (same serializer) joins `account` so it does not add a query. |
| 3 | Creating on the organisation path stays organisation-level | File upload builds the `Attachment` from `_parent()`; `CallSerializer` has no `account` field and `perform_create` saves `customer=customer, account=None`. | Nothing to change; both creates are pinned by a test that sends `account_id` in the body and gets an organisation-level record back. |
| 4 | Query counts pinned | The calls list has no `prefetch_related("participants")`: `get_participants` runs one query per call today. The caller's membership and org chart (`subtree_ids`) are memoised on the user instance (`services.identity.context`, `services.accounts.hierarchy`), so the first request in a test costs 2–4 more than later ones. | Calls prefetch participants and join `account` and `transcript__account`. Tests warm up with one read, then pin the steady state at two list sizes: files **2** (the organisation; the files with uploader and account joined), calls **3** (+ participants), surveys `?customer=` **3** (the organisation; the surveys with parents joined; every account's organisations). Measured on this branch with the code in this plan. |
| 5 | "unknown or invisible ids give an empty list" | `ContactListView` / `AccountListCreateView` ignore an unparseable `?company=` (no filter). | `?customer=` is stricter, as the spec says: a non-blank value that is not an id the caller can open (another tenant's, an invisible one, `abc`, `-1`, `1.5`) is `[]` with 200. A blank `?customer=` is no filter, the same as leaving it out. An archived organisation the caller can open filters normally (`visible_customers` includes archived). |
| 6 | `account_id` on contact, opportunity and risk | Not in `fields`. `ContactDetailView`'s docstring says list rows "don't carry an account_id". Flat POSTs (`/opportunities/`, `/risks/`, `/surveys/`) read `account_id` from `request.data` directly, not through the serializer. | Add `"account_id"` to `fields`; DRF builds a `ReadOnlyField` for an attname, so a PATCH body's `account_id` is ignored and the flat POSTs are unaffected. Every endpoint using these serializers (nested, flat, detail) returns it. The stale docstrings are corrected. |
| 7 | — | `docs/API_CONTRACTS.md` still shows `company_id`/`company_name` on contact, opportunity and risk responses; the serializers return `companies` since the many-to-many change. | Corrected to `companies` in the same edit that adds `account_id` (Task 4). |
| 8 | — | The Files list includes call transcripts (`source: "transcript"`) of the parent it reads. | Unchanged: an account call's transcript now appears on the organisation's Files list, tagged with its account, like any other account file. |

## File structure

| File | Change |
|---|---|
| `services/customers/serializers.py` | `AttachmentSerializer`, `CallSerializer`: `account_id`, `account_name`. `ContactSerializer`, `OpportunitySerializer`, `RiskSerializer`: `account_id`. `SurveySerializer` docstring. |
| `services/customers/views.py` | `_FileListView.get_queryset`, `_visible_attachment`, `_CallListView.get_queryset`, `SurveyListView.get_queryset`; import `Call`; `ContactDetailView` docstring. |
| `services/customers/tests/test_rollup_serializers.py` | New. Unit tier for the serializer fields. |
| `services/customers/tests/test_files_and_calls.py` | `Fixture.attach`; `FileRollupTests`, `CallRollupTests`; `test_files_on_an_account` updated. |
| `services/customers/tests/test_views.py` | `SurveyListCustomerFilterTests`; `account_id` tests on the contact, opportunity and risk roll-ups and the contact PATCH. |
| `e2e/test_organization_lists_flow.py` | New. E2e tier. |
| `docs/API_CONTRACTS.md`, `docs/product/01-prd.md`, `docs/product/05-backend-schema.md` | Contract and product docs. |

---

### Task 1: Files roll up the organisation's visible accounts

**Files:**
- Modify: `services/customers/serializers.py:872-900` (`AttachmentSerializer`)
- Modify: `services/customers/views.py:2753-2767` (`_FileListView` docstring and `get_queryset`), `services/customers/views.py:2822-2827` (`_visible_attachment`)
- Create: `services/customers/tests/test_rollup_serializers.py`
- Modify: `services/customers/tests/test_files_and_calls.py` (imports, `Fixture`, `test_files_on_an_account`, new `FileRollupTests`)
- Modify: `docs/API_CONTRACTS.md` (section `### GET/POST /api/v1/customers/<id>/files/, .../accounts/<id>/files/`, ~line 2675)

**Interfaces:**
- Consumes: `scoping.customer_rollup_q(user, customer) -> Q`; `blind_to_one_account(customer) -> (viewer, seen, hidden)` from `services.customers.tests.test_views` (accounts are named `"Seen"` and `"Hidden"`; the customer's owner becomes the colleague).
- Produces: `AttachmentSerializer` fields `account_id: int | None`, `account_name: str | None`, and `AttachmentSerializer.get_account_name(obj) -> str | None`. `Fixture.attach(name: str, **fields) -> Attachment` in `test_files_and_calls.py` (Task 2 uses it). `services/customers/tests/test_rollup_serializers.py` (Tasks 2 and 4 extend it).

- [ ] **Step 1: Write the failing unit test**

Create `services/customers/tests/test_rollup_serializers.py`:

```python
"""Unit tier: the account fields the organisation page's account chips and
tags read. No database: the models are built in memory."""

from django.test import SimpleTestCase

from services.customers.models import Account, Attachment, Customer
from services.customers.serializers import AttachmentSerializer


class AttachmentAccountFieldsTests(SimpleTestCase):
    def test_an_account_level_file_names_its_account(self):
        row = Attachment(account=Account(id=5, name="EMEA"))
        self.assertEqual(AttachmentSerializer().get_account_name(row), "EMEA")

    def test_an_organisation_level_file_has_no_account(self):
        row = Attachment(customer=Customer(id=1, name="Pizza Hut"))
        self.assertIsNone(AttachmentSerializer().get_account_name(row))

    def test_the_account_id_is_part_of_the_row(self):
        self.assertIn("account_id", AttachmentSerializer().fields)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `venv/bin/python manage.py test services.customers.tests.test_rollup_serializers --noinput`
Expected: FAIL/ERROR with `AttributeError: 'AttachmentSerializer' object has no attribute 'get_account_name'` and `'account_id' not found`.

- [ ] **Step 3: Write the failing integration tests**

In `services/customers/tests/test_files_and_calls.py`, add the helper import below the existing imports (after `from services.customers.models import Account, Attachment, Call, Customer`):

```python
from services.customers.tests.test_views import blind_to_one_account
```

Add this method to `Fixture`, after `upload`:

```python
    def attach(self, name, **fields):
        """A stored file straight through the ORM, for the list tests."""
        return Attachment.objects.create(
            organisation=self.org,
            file=SimpleUploadedFile(name, b"hello", content_type="text/plain"),
            name=name,
            content_type="text/plain",
            size=5,
            uploaded_by=self.carl,
            **fields,
        )
```

Replace `FilesTests.test_files_on_an_account` with:

```python
    def test_files_on_an_account(self):
        self.client.force_authenticate(self.carl)
        response = self.client.post(
            f"/api/v1/customers/{self.pizza.id}/accounts/{self.hut_uk.id}/files/",
            {"file": SimpleUploadedFile("notes.txt", b"hello", content_type="text/plain")},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(Attachment.objects.get().account, self.hut_uk)
        self.assertEqual(
            (response.data["account_id"], response.data["account_name"]),
            (self.hut_uk.id, "Pizza Hut UK"),
        )
        # The organisation's own list rolls the account's file up, tagged.
        listed = self.client.get(f"/api/v1/customers/{self.pizza.id}/files/").data
        self.assertEqual(
            [(r["name"], r["account_id"]) for r in listed], [("notes.txt", self.hut_uk.id)]
        )
```

Append a new class after `FilesTests`:

```python
class FileRollupTests(Fixture):
    """The organisation's Files list: its own files and those of every account
    the viewer may open, each tagged with its account (customer_rollup_q)."""

    def setUp(self):
        super().setUp()
        self.viewer, self.seen, self.hidden = blind_to_one_account(self.pizza)
        self.url = f"/api/v1/customers/{self.pizza.id}/files/"

    def test_rolls_up_the_accounts_the_viewer_may_open(self):
        self.attach("org.txt", customer=self.pizza)
        self.attach("seen.txt", account=self.seen)
        self.attach("hidden.txt", account=self.hidden)
        self.client.force_authenticate(self.viewer)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = {r["name"]: (r["account_id"], r["account_name"]) for r in response.data}
        self.assertEqual(rows, {"org.txt": (None, None), "seen.txt": (self.seen.id, "Seen")})

    def test_a_hidden_accounts_file_stays_unreachable_by_id(self):
        hidden = self.attach("hidden.txt", account=self.hidden)
        self.client.force_authenticate(self.viewer)

        self.assertEqual(self.client.get(f"/api/v1/files/{hidden.id}/").status_code, 404)
        download = self.client.get(f"/api/v1/files/{hidden.id}/download/")
        self.assertEqual(download.status_code, 404)

    def test_someone_who_sees_everything_gets_every_account(self):
        self.attach("org.txt", customer=self.pizza)
        self.attach("seen.txt", account=self.seen)
        self.attach("hidden.txt", account=self.hidden)
        self.client.force_authenticate(self.admin)

        names = {r["name"] for r in self.client.get(self.url).data}

        self.assertEqual(names, {"org.txt", "seen.txt", "hidden.txt"})

    def test_uploading_on_the_organisation_path_stays_organisation_level(self):
        self.client.force_authenticate(self.viewer)

        response = self.client.post(
            self.url,
            {
                "file": SimpleUploadedFile("deck.txt", b"hello", content_type="text/plain"),
                "account_id": self.seen.id,
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual((response.data["account_id"], response.data["account_name"]), (None, None))
        attachment = Attachment.objects.get()
        self.assertEqual((attachment.customer, attachment.account), (self.pizza, None))

    def test_a_file_read_by_id_carries_its_account(self):
        seen = self.attach("seen.txt", account=self.seen)
        self.client.force_authenticate(self.viewer)

        row = self.client.get(f"/api/v1/files/{seen.id}/").data

        self.assertEqual((row["account_id"], row["account_name"]), (self.seen.id, "Seen"))

    def test_the_query_count_does_not_grow_with_the_list(self):
        self.client.force_authenticate(self.viewer)
        # The first read resolves the caller's membership and org chart, which
        # are memoised on the user; what is pinned is every read after it.
        self.client.get(self.url)
        for batch in range(2):
            for i in range(3):
                self.attach(f"org-{batch}-{i}.txt", customer=self.pizza)
                self.attach(f"seen-{batch}-{i}.txt", account=self.seen)
            # Two however long the list: the organisation, and the files with
            # their uploader and account joined.
            with self.assertNumQueries(2):
                response = self.client.get(self.url)
            self.assertEqual(len(response.data), 6 * (batch + 1))
```

- [ ] **Step 4: Run them to verify they fail**

Run: `venv/bin/python manage.py test services.customers.tests.test_files_and_calls --noinput`
Expected: FAIL. `test_files_on_an_account` and `test_uploading_on_the_organisation_path_stays_organisation_level` raise `KeyError: 'account_id'`; `test_rolls_up_the_accounts_the_viewer_may_open` and `test_someone_who_sees_everything_gets_every_account` miss the account rows.

- [ ] **Step 5: Add the fields to `AttachmentSerializer`**

In `services/customers/serializers.py`, replace the `AttachmentSerializer` class (lines 872-900) with:

```python
class AttachmentSerializer(serializers.ModelSerializer):
    """A file on a customer or account. Never the storage path: the bytes
    come from the download endpoint, which checks who is asking.

    `account_id`/`account_name` say which account a file hangs off (both
    null for an organisation-level file): the organisation's Files list
    rolls its accounts' files up and tags each one."""

    uploaded_by = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()
    account_name = serializers.SerializerMethodField()

    class Meta:
        model = Attachment
        fields = [
            "id",
            "account_id",
            "account_name",
            "name",
            "content_type",
            "size",
            "description",
            "source",
            "uploaded_by",
            "download_url",
            "created_at",
        ]
        read_only_fields = fields

    def get_uploaded_by(self, obj):
        if obj.uploaded_by_id is None:
            return None
        return {"id": obj.uploaded_by_id, "name": obj.uploaded_by.name}

    def get_download_url(self, obj):
        return f"/api/v1/files/{obj.id}/download/"

    def get_account_name(self, obj):
        return obj.account.name if obj.account_id else None
```

- [ ] **Step 6: Roll the list up**

In `services/customers/views.py`, replace the `_FileListView` docstring and `get_queryset` (lines 2753-2767):

```python
class _FileListView(generics.ListCreateAPIView):
    """List the files on one company, and upload one (multipart: `file`,
    optional `description`). Anyone who may open the company may read and
    add; the type list, size cap and name sanitising live in files.py.

    On an organisation the list rolls up its accounts' files too, under
    customer_rollup_q (only accounts the caller may open), each tagged
    with `account_id`/`account_name`. An upload there is always
    organisation-level: the parent comes from the URL, never the body."""

    serializer_class = AttachmentSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None
    parser_classes = [MultiPartParser, FormParser]

    def _parent(self):
        raise NotImplementedError

    def get_queryset(self):
        customer, account = self._parent()
        if customer is not None:
            # SOC2:AUTH-02 an account's file follows its own account's visibility
            rows = Attachment.objects.filter(customer_rollup_q(self.request.user, customer))
        else:
            rows = account.attachments.all()
        return rows.select_related("uploaded_by", "account")
```

Then join `account` where a file is read by id (lines 2822-2827):

```python
def _visible_attachment(request, pk):
    # SOC2:AUTH-02 a file is reachable only through a company the caller may open
    return get_object_or_404(
        Attachment.objects.filter(visible_children_q(request.user)).select_related(
            "uploaded_by", "account"
        ),
        pk=pk,
    )
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.customers.tests.test_rollup_serializers services.customers.tests.test_files_and_calls --noinput`
Expected: PASS (all).

- [ ] **Step 8: Update the contract**

In `docs/API_CONTRACTS.md`, section `### GET/POST /api/v1/customers/<id>/files/, .../accounts/<id>/files/`, replace the first paragraph ("Auth: `IsAuthenticated`; anyone who may open the company may list and add.") with:

```text
Auth: `IsAuthenticated`; anyone who may open the company may list and
add. On an organisation, `GET` rolls up its own files and those on its
accounts the caller may open (`customer_rollup_q`, the rule the contact,
opportunity, risk, survey and canvas roll-ups use: an account a colleague
owns keeps its files out even when the caller can open the organisation).
Each row carries `account_id` and `account_name`, both `null` for an
organisation-level file. A `POST` on the organisation path always creates
an organisation-level file; an `account_id` in the body is ignored. Upload
on the account path to attach a file to an account.
```

(The rest of that paragraph, from "`POST` is multipart", stays.) Replace the JSON example below it with:

```json
[{"id": 7, "account_id": null, "account_name": null, "name": "Signed MSA.pdf",
  "content_type": "application/pdf", "size": 183220, "description": "Countersigned 12 Sep",
  "source": "upload", "uploaded_by": {"id": 3, "name": "Carl"},
  "download_url": "/api/v1/files/7/download/", "created_at": "2026-09-16T09:00:00Z"},
 {"id": 9, "account_id": 4, "account_name": "EMEA", "name": "qbr.vtt",
  "content_type": "text/vtt", "size": 5120, "description": "", "source": "transcript",
  "uploaded_by": {"id": 3, "name": "Carl"}, "download_url": "/api/v1/files/9/download/",
  "created_at": "2026-09-15T14:00:00Z"}]
```

In the next section (`### GET /api/v1/files/<id>/ …`), append to its first paragraph: "The row has the same shape as the list's, `account_id`/`account_name` included."

- [ ] **Step 9: Lint and commit**

Run: `venv/bin/ruff check services/customers && venv/bin/ruff format --check services/customers`
Expected: no errors, no files to reformat.

```bash
git add services/customers/serializers.py services/customers/views.py \
  services/customers/tests/test_rollup_serializers.py \
  services/customers/tests/test_files_and_calls.py docs/API_CONTRACTS.md
git commit -m "feat(customers): organisation files roll up visible accounts, tagged

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Calls roll up the organisation's visible accounts

**Files:**
- Modify: `services/customers/serializers.py:903-967` (`CallSerializer`)
- Modify: `services/customers/views.py:29-46` (model imports), `services/customers/views.py:2873-2887` (`_CallListView` docstring and `get_queryset`)
- Modify: `services/customers/tests/test_rollup_serializers.py`
- Modify: `services/customers/tests/test_files_and_calls.py` (imports, new `CallRollupTests`)
- Modify: `docs/API_CONTRACTS.md` (section `### GET/POST /api/v1/customers/<id>/calls/, .../accounts/<id>/calls/ — CallSense`, ~line 2360)

**Interfaces:**
- Consumes: `Fixture.attach(name, **fields) -> Attachment` and the `blind_to_one_account` import (Task 1); `AttachmentSerializer`'s `account_id`/`account_name` (Task 1), which the nested `transcript` inherits.
- Produces: `CallSerializer` fields `account_id: int | None`, `account_name: str | None`, and `CallSerializer.get_account_name(obj) -> str | None`.

- [ ] **Step 1: Write the failing unit test**

In `services/customers/tests/test_rollup_serializers.py`, replace the two import lines

```python
from services.customers.models import Account, Attachment, Customer
from services.customers.serializers import AttachmentSerializer
```

with

```python
from services.customers.models import Account, Attachment, Call, Customer
from services.customers.serializers import AttachmentSerializer, CallSerializer
```

and append:

```python
class CallAccountFieldsTests(SimpleTestCase):
    def test_an_account_level_call_names_its_account(self):
        row = Call(account=Account(id=5, name="EMEA"))
        self.assertEqual(CallSerializer().get_account_name(row), "EMEA")

    def test_an_organisation_level_call_has_no_account(self):
        row = Call(customer=Customer(id=1, name="Pizza Hut"))
        self.assertIsNone(CallSerializer().get_account_name(row))

    def test_the_account_id_cannot_be_written(self):
        # Logging a call takes its parent from the URL, never the body.
        self.assertTrue(CallSerializer().fields["account_id"].read_only)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `venv/bin/python manage.py test services.customers.tests.test_rollup_serializers --noinput`
Expected: ERROR, `AttributeError: 'CallSerializer' object has no attribute 'get_account_name'` and `KeyError: 'account_id'`.

- [ ] **Step 3: Write the failing integration tests**

In `services/customers/tests/test_files_and_calls.py`, change the models import to:

```python
from services.customers.models import Account, Attachment, Call, Contact, Customer
```

Append after `CallsTests`:

```python
class CallRollupTests(Fixture):
    """The organisation's Calls list: its own calls and those of every account
    the viewer may open, each tagged with its account (customer_rollup_q)."""

    WHEN = datetime(2026, 9, 16, 10, 0, tzinfo=dt_timezone.utc)

    def setUp(self):
        super().setUp()
        self.viewer, self.seen, self.hidden = blind_to_one_account(self.pizza)
        self.url = f"/api/v1/customers/{self.pizza.id}/calls/"

    def call(self, title, **parent):
        return Call.objects.create(title=title, host_name="Sam", occurred_at=self.WHEN, **parent)

    def test_rolls_up_the_accounts_the_viewer_may_open(self):
        self.call("Org call", customer=self.pizza)
        self.call("Seen call", account=self.seen)
        self.call("Hidden call", account=self.hidden)
        self.client.force_authenticate(self.viewer)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = {r["title"]: (r["account_id"], r["account_name"]) for r in response.data}
        self.assertEqual(rows, {"Org call": (None, None), "Seen call": (self.seen.id, "Seen")})

    def test_someone_who_sees_everything_gets_every_account(self):
        self.call("Org call", customer=self.pizza)
        self.call("Seen call", account=self.seen)
        self.call("Hidden call", account=self.hidden)
        self.client.force_authenticate(self.admin)

        titles = {r["title"] for r in self.client.get(self.url).data}

        self.assertEqual(titles, {"Org call", "Seen call", "Hidden call"})

    def test_logging_on_the_organisation_path_stays_organisation_level(self):
        self.client.force_authenticate(self.viewer)

        response = self.client.post(
            self.url,
            {
                "title": "Check-in",
                "occurred_at": self.WHEN.isoformat(),
                "summary": "All fine.",
                "account_id": self.seen.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual((response.data["account_id"], response.data["account_name"]), (None, None))
        call = Call.objects.get()
        self.assertEqual((call.customer, call.account), (self.pizza, None))

    def test_an_accounts_call_and_its_transcript_carry_the_account(self):
        self.client.force_authenticate(self.viewer)
        with patch("services.customers.calls.get_completion", return_value="SSO by Q4."):
            response = self.client.post(
                f"/api/v1/customers/{self.pizza.id}/accounts/{self.seen.id}/calls/",
                {
                    "title": "QBR",
                    "occurred_at": self.WHEN.isoformat(),
                    "transcript": SimpleUploadedFile(
                        "qbr.txt", b"We need SSO by Q4.", content_type="text/plain"
                    ),
                },
                format="multipart",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(
            (response.data["account_id"], response.data["account_name"]), (self.seen.id, "Seen")
        )
        transcript = response.data["transcript"]
        self.assertEqual(
            (transcript["account_id"], transcript["account_name"]), (self.seen.id, "Seen")
        )
        listed = self.client.get(self.url).data
        self.assertEqual([(r["title"], r["account_id"]) for r in listed], [("QBR", self.seen.id)])

    def test_the_query_count_does_not_grow_with_the_list(self):
        self.client.force_authenticate(self.viewer)
        # The first read resolves the caller's membership and org chart, which
        # are memoised on the user; what is pinned is every read after it.
        self.client.get(self.url)
        for batch in range(2):
            for i in range(3):
                for parent in ({"customer": self.pizza}, {"account": self.seen}):
                    transcript = self.attach(
                        f"{batch}-{i}.txt", source=Attachment.Source.TRANSCRIPT, **parent
                    )
                    call = Call.objects.create(
                        title=f"Call {batch}-{i}",
                        host_name="Sam",
                        occurred_at=self.WHEN,
                        logged_by=self.carl,
                        transcript=transcript,
                        **parent,
                    )
                    call.participants.add(
                        Contact.objects.create(
                            name=f"Guest {call.id}",
                            email=f"guest{call.id}@pizza.io",
                            role=Contact.Role.OTHER,
                            **parent,
                        )
                    )
            # Three however long the list: the organisation, the calls with
            # their account, recorder, logger and transcript joined, and every
            # call's participants at once.
            with self.assertNumQueries(3):
                response = self.client.get(self.url)
            self.assertEqual(len(response.data), 6 * (batch + 1))
```

- [ ] **Step 4: Run them to verify they fail**

Run: `venv/bin/python manage.py test services.customers.tests.test_files_and_calls.CallRollupTests --noinput`
Expected: FAIL. `KeyError: 'account_id'` on the create tests; the roll-up tests miss the account rows; the query-count test reports more than 3 queries (one per call for participants).

- [ ] **Step 5: Add the fields to `CallSerializer`**

In `services/customers/serializers.py`, in `CallSerializer`, add the field after `participant_ids`:

```python
    account_name = serializers.SerializerMethodField()
```

add `"account_id"` and `"account_name"` to the start of `Meta.fields`, directly after `"id"`:

```python
        fields = [
            "id",
            "account_id",
            "account_name",
            "title",
```

(the rest of the list is unchanged), and add this method after `get_transcript`:

```python
    def get_account_name(self, obj):
        return obj.account.name if obj.account_id else None
```

Extend the class docstring's last sentence so it reads: "`host_name` defaults to the caller. `account_id`/`account_name` (null on an organisation-level call) tag the rows the organisation's list rolls up from its accounts; `account_id` is read-only, since a call's parent comes from the URL."

- [ ] **Step 6: Roll the list up**

In `services/customers/views.py`, add `Call` to the `.models` import, after `Attachment`:

```python
from .models import (
    Account,
    Attachment,
    Call,
    Canvas,
```

Replace the `_CallListView` docstring and `get_queryset` (lines 2873-2887):

```python
class _CallListView(generics.ListCreateAPIView):
    """List the calls on one company, and log one. JSON or multipart; a
    multipart body may carry a `transcript` file (.txt/.vtt/.srt/.md).

    On an organisation the list rolls up its accounts' calls too, under
    customer_rollup_q (only accounts the caller may open), each tagged
    with `account_id`/`account_name`. A call logged there is always
    organisation-level: the parent comes from the URL, never the body."""

    serializer_class = CallSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def _parent(self):
        raise NotImplementedError

    def get_queryset(self):
        customer, account = self._parent()
        if customer is not None:
            # SOC2:AUTH-02 an account's call follows its own account's visibility
            rows = Call.objects.filter(customer_rollup_q(self.request.user, customer))
        else:
            rows = account.calls.all()
        return rows.select_related(
            "account", "connector", "logged_by", "transcript__uploaded_by", "transcript__account"
        ).prefetch_related("participants")
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.customers.tests.test_rollup_serializers services.customers.tests.test_files_and_calls services.customers.tests.test_contact_sentiment --noinput`
Expected: PASS (all). `test_contact_sentiment` logs calls through the same view and must stay green.

- [ ] **Step 8: Update the contract**

In `docs/API_CONTRACTS.md`, section `### GET/POST /api/v1/customers/<id>/calls/, .../accounts/<id>/calls/ — CallSense`, replace the first paragraph (from "Auth: `IsAuthenticated`, same 404-not-empty-list scoping" to "`transcript` (an Attachment row, or null).") with:

```text
Auth: `IsAuthenticated`, same 404-not-empty-list scoping as the other
nested lists. `GET` is the company's calls, newest first, each with
`account_id`/`account_name` (both `null` for an organisation-level call),
`sentiment`/`ai_area`/`ai_category`, `connector_name` (the recorder, or
null), `logged_by {id, name}` (null for synced or seeded calls),
`recording_url`, `participants`, and `transcript` (an Attachment row with
the same `account_id`/`account_name`, or null). On an organisation, `GET`
rolls up its own calls and those on its accounts the caller may open
(`customer_rollup_q`, as for Files below). A call logged on the
organisation path is always organisation-level; an `account_id` in the
body is ignored. Log on the account path to put a call on an account.
```

- [ ] **Step 9: Lint and commit**

Run: `venv/bin/ruff check services/customers && venv/bin/ruff format --check services/customers`
Expected: no errors, no files to reformat.

```bash
git add services/customers/serializers.py services/customers/views.py \
  services/customers/tests/test_rollup_serializers.py \
  services/customers/tests/test_files_and_calls.py docs/API_CONTRACTS.md
git commit -m "feat(customers): organisation calls roll up visible accounts, tagged

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `GET /surveys/?customer=<id>`

**Files:**
- Modify: `services/customers/views.py:1972-2022` (`SurveyListView` docstring and `get_queryset`)
- Modify: `services/customers/tests/test_views.py` (new `SurveyListCustomerFilterTests` after `SurveyListTests`, ~line 3706)
- Modify: `docs/API_CONTRACTS.md` (section `### GET/POST /api/v1/surveys/`, ~line 3285)

**Interfaces:**
- Consumes: `visible_customers(user)`, `customer_rollup_q(user, customer)`, `visible_children_q(user)` (all already imported in `views.py`); `_parse_int(raw) -> int | None` (already imported from `.interactions`).
- Produces: `SurveyListView._one_organisation(user, raw: str) -> QuerySet[Survey]`. The query parameter `customer` on `GET /api/v1/surveys/`.

- [ ] **Step 1: Write the failing tests**

In `services/customers/tests/test_views.py`, add after the `SurveyListTests` class (before `class SurveyDetailTests`):

```python
class SurveyListCustomerFilterTests(APITestCase):
    """`GET /api/v1/surveys/?customer=<id>`: one organisation's surveys and
    those of its accounts the viewer may open (customer_rollup_q). An id the
    viewer cannot open reads as an empty list, never an error that would
    confirm the organisation exists."""

    url = "/api/v1/surveys/"

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.customer = Customer.objects.create(organisation=self.org, name="Globex")
        self.viewer, self.seen, self.hidden = blind_to_one_account(self.customer)
        self.other = Customer.objects.create(organisation=self.org, name="Initech")
        self.client.force_authenticate(self.viewer)

    def survey(self, **parent):
        return Survey.objects.create(
            survey_type=Survey.SurveyType.CSAT, sent_at="2026-09-01", **parent
        )

    def ids(self, query):
        response = self.client.get(self.url, query)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return {row["id"] for row in response.data}

    def test_narrows_to_the_organisation_and_its_visible_accounts(self):
        own = self.survey(customer=self.customer)
        seen = self.survey(account=self.seen)
        self.survey(account=self.hidden)
        self.survey(customer=self.other)

        self.assertEqual(self.ids({"customer": self.customer.id}), {own.id, seen.id})

    def test_rows_carry_their_account(self):
        self.survey(account=self.seen)

        row = self.client.get(self.url, {"customer": self.customer.id}).data[0]

        self.assertEqual((row["account_id"], row["account_name"]), (self.seen.id, "Seen"))

    def test_without_the_filter_the_list_is_unchanged(self):
        own = self.survey(customer=self.customer)
        seen = self.survey(account=self.seen)
        other = self.survey(customer=self.other)
        self.survey(account=self.hidden)

        self.assertEqual(self.ids({}), {own.id, seen.id, other.id})
        self.assertEqual(self.ids({"customer": ""}), {own.id, seen.id, other.id})

    def test_an_id_the_viewer_cannot_open_reads_as_empty(self):
        secret = Customer.objects.create(
            organisation=self.org, name="Secret", owner=self.customer.owner
        )
        self.survey(customer=secret)
        foreign = Customer.objects.create(
            organisation=Organisation.objects.create(name="Other Org"), name="Foreign"
        )
        self.survey(customer=foreign)
        self.survey(customer=self.customer)

        for value in (secret.id, foreign.id, 999999, "abc", "-1", "1.5"):
            with self.subTest(customer=value):
                response = self.client.get(self.url, {"customer": value})
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(response.data, [])

    def test_the_query_count_does_not_grow_with_the_list(self):
        query = {"customer": self.customer.id}
        # The first read resolves the caller's membership and org chart, which
        # are memoised on the user; what is pinned is every read after it.
        self.client.get(self.url, query)
        for batch in range(2):
            for _ in range(3):
                self.survey(customer=self.customer)
                self.survey(account=self.seen)
            # Three however long the list: the organisation, the surveys with
            # their parents joined, and every account's organisations at once.
            with self.assertNumQueries(3):
                response = self.client.get(self.url, query)
            self.assertEqual(len(response.data), 6 * (batch + 1))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `venv/bin/python manage.py test services.customers.tests.test_views.SurveyListCustomerFilterTests --noinput`
Expected: FAIL. `test_narrows_…` gets the other organisation's survey too; `test_an_id_the_viewer_cannot_open_reads_as_empty` gets a non-empty list. `test_without_the_filter_the_list_is_unchanged` and `test_rows_carry_their_account` already pass.

- [ ] **Step 3: Implement the filter**

In `services/customers/views.py`, replace the whole `SurveyListView` class (lines 1972-2022) with:

```python
class SurveyListView(generics.ListCreateAPIView):
    """GET/POST /api/v1/surveys/ — every Survey across every Customer/
    Account the caller's own organisation owns, organisation-level and
    account-level alike. Powers the standalone Surveys page
    (react-ts-app's src/pages/surveys/SurveysPage.tsx) — the one place
    a Survey is browsed independent of which Customer/Account it
    belongs to, same reasoning as OpportunityListView. Its own rollup
    cards (response rate / average score per type) are computed
    client-side from this same unpaginated list, same approach
    PipelinesPage.tsx's own "Pipelines Overview" banner already uses —
    no separate stats endpoint.

    Unpaginated for the same reason as OpportunityListView — the
    rollup page needs every record to compute real totals from, not
    one page of them.

    `?customer=<id>` narrows the list to one organisation: its own
    surveys and those on its accounts the caller may open
    (customer_rollup_q, the organisation page's rule). Powers the
    Surveys page's organisation filter. Blank means no filter; an id
    the caller cannot open, or one that is not an id at all, reads as
    no surveys — a 404 or a 400 would confirm the organisation exists.

    POST takes a `customer_id` or an `account_id` in the request body
    (neither is a real serializer field — `perform_create` below reads
    whichever one was sent directly off the raw request) and creates
    the Survey under that parent. Exactly one of the two must be
    given, same invariant as the model's own CheckConstraint. Rejects
    survey_type=CES for an account_id — see _reject_ces_for_account's
    own docstring."""

    serializer_class = SurveySerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        user = self.request.user
        raw = self.request.query_params.get("customer", "").strip()
        if raw:
            rows = self._one_organisation(user, raw)
        else:
            # `.distinct()` — same fan-out reasoning as ContactListView's own.
            rows = Survey.objects.filter(visible_children_q(user)).distinct()
        return rows.select_related("customer", "account").prefetch_related("account__customers")

    @staticmethod
    def _one_organisation(user, raw):
        customer_id = _parse_int(raw)
        if customer_id is None:
            return Survey.objects.none()
        customer = visible_customers(user).filter(pk=customer_id).first()
        if customer is None:
            return Survey.objects.none()
        # SOC2:AUTH-02 an account's survey follows its own account's visibility
        return Survey.objects.filter(customer_rollup_q(user, customer))

    def perform_create(self, serializer):
        account_id = self.request.data.get("account_id")
        customer_id = self.request.data.get("customer_id")
        if account_id:
            _reject_ces_for_account(serializer)
            # `.distinct()` before `get_object_or_404` — same
            # reasoning as OpportunityListView.perform_create's own.
            account = get_object_or_404(visible_accounts(self.request.user), pk=account_id)
            serializer.save(account=account)
        elif customer_id:
            customer = get_object_or_404(visible_customers(self.request.user), pk=customer_id)
            serializer.save(customer=customer)
        else:
            raise ValidationError("Provide either customer_id or account_id.")
```

`perform_create` is unchanged; it is repeated so the class can be pasted whole.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.customers.tests.test_views.SurveyListCustomerFilterTests services.customers.tests.test_views.SurveyListTests --noinput`
Expected: PASS (all).

- [ ] **Step 5: Update the contract**

In `docs/API_CONTRACTS.md`, section `### GET/POST /api/v1/surveys/`, add after the "**Pagination is off** here…" paragraph:

```text
Query params:
- `?customer=<id>` — one organisation's surveys: its own and those on its
  accounts the caller may open (`customer_rollup_q`, the organisation
  page's roll-up rule). An id the caller cannot open (another tenant's,
  one they may not see, or not an id at all) returns `[]` with `200`,
  never a `404` or `400` that would confirm it exists. A blank value is
  no filter. Powers the Surveys page's organisation filter
  (`/surveys?customer=<id>`).
```

- [ ] **Step 6: Lint and commit**

Run: `venv/bin/ruff check services/customers && venv/bin/ruff format --check services/customers`
Expected: no errors, no files to reformat.

```bash
git add services/customers/views.py services/customers/tests/test_views.py docs/API_CONTRACTS.md
git commit -m "feat(customers): filter the survey list by organisation

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `account_id` on contacts, opportunities and risks

**Files:**
- Modify: `services/customers/serializers.py:1032-1180` (`ContactSerializer`, `OpportunitySerializer`, `RiskSerializer`), `services/customers/serializers.py:1183-1195` (`SurveySerializer` docstring)
- Modify: `services/customers/views.py:1580-1591` (`ContactDetailView` docstring)
- Modify: `services/customers/tests/test_rollup_serializers.py`
- Modify: `services/customers/tests/test_views.py` (`CustomerContactListTests` ~2082, `ContactDetailTests` ~2567, `CustomerOpportunityListTests` ~2677, `CustomerRiskListTests` ~3098)
- Modify: `docs/API_CONTRACTS.md` (contacts ~2896 and ~2938, opportunities ~3060 and ~3080, risks ~3148 and ~3168, surveys ~3258)

**Interfaces:**
- Consumes: `create_account(customer, **kwargs)` and each class's `_contact_kwargs` / `_opportunity_kwargs` / `_risk_kwargs` helpers in `test_views.py`.
- Produces: `account_id: int | None` (read-only) on every Contact, Opportunity and Risk row, on every endpoint using those serializers.

- [ ] **Step 1: Write the failing unit test**

In `services/customers/tests/test_rollup_serializers.py`, replace the two import lines with:

```python
from services.customers.models import (
    Account,
    Attachment,
    Call,
    Contact,
    Customer,
    Opportunity,
    Risk,
)
from services.customers.serializers import (
    AttachmentSerializer,
    CallSerializer,
    ContactSerializer,
    OpportunitySerializer,
    RiskSerializer,
)
```

and append:

```python
class ChipAccountIdTests(SimpleTestCase):
    """The organisation page's account chips filter People and Deals & risks
    client-side on `account_id`."""

    def test_contacts_opportunities_and_risks_carry_a_read_only_account_id(self):
        for serializer, model in (
            (ContactSerializer, Contact),
            (OpportunitySerializer, Opportunity),
            (RiskSerializer, Risk),
        ):
            with self.subTest(serializer=serializer.__name__):
                field = serializer().fields["account_id"]
                self.assertTrue(field.read_only)
                self.assertEqual(field.get_attribute(model(account_id=5)), 5)
                self.assertIsNone(field.get_attribute(model(customer_id=1)))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `venv/bin/python manage.py test services.customers.tests.test_rollup_serializers.ChipAccountIdTests --noinput`
Expected: FAIL with `KeyError: 'account_id'` in each subtest.

- [ ] **Step 3: Write the failing integration tests**

In `services/customers/tests/test_views.py`, add to `CustomerContactListTests` (after `test_rolls_up_this_customers_own_accounts_contacts_too`):

```python
    def test_each_row_carries_its_account_id(self):
        account = create_account(self.customer, name="North America")
        Contact.objects.create(customer=self.customer, **self._contact_kwargs(name="Org-Level"))
        Contact.objects.create(account=account, **self._contact_kwargs(name="Account-Level"))
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        ids = {row["name"]: row["account_id"] for row in response.data}
        self.assertEqual(ids, {"Org-Level": None, "Account-Level": account.id})
```

Add to `CustomerOpportunityListTests` (after `test_rolls_up_this_customers_own_accounts_opportunities_too`):

```python
    def test_each_row_carries_its_account_id(self):
        account = create_account(self.customer, name="North America")
        Opportunity.objects.create(
            customer=self.customer, **self._opportunity_kwargs(title="Org-Level")
        )
        Opportunity.objects.create(
            account=account,
            **self._opportunity_kwargs(title="Account-Level"),
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        ids = {row["title"]: row["account_id"] for row in response.data}
        self.assertEqual(ids, {"Org-Level": None, "Account-Level": account.id})
```

Add to `CustomerRiskListTests` (after its roll-up test):

```python
    def test_each_row_carries_its_account_id(self):
        account = create_account(self.customer, name="North America")
        Risk.objects.create(customer=self.customer, **self._risk_kwargs(title="Org-Level"))
        Risk.objects.create(account=account, **self._risk_kwargs(title="Account-Level"))
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        ids = {row["title"]: row["account_id"] for row in response.data}
        self.assertEqual(ids, {"Org-Level": None, "Account-Level": account.id})
```

Add to `ContactDetailTests` (its `setUp` creates `self.admin`, `self.account` "North America" and `self.org_contact`, an organisation-level contact, and does not authenticate):

```python
    def test_patching_account_id_does_not_move_the_contact(self):
        self.client.force_authenticate(self.admin)
        url = f"/api/v1/contacts/{self.org_contact.id}/"

        response = self.client.patch(url, {"account_id": self.account.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["account_id"])
        self.org_contact.refresh_from_db()
        self.assertIsNone(self.org_contact.account_id)
```

- [ ] **Step 4: Run them to verify they fail**

Run: `venv/bin/python manage.py test services.customers.tests.test_views.CustomerContactListTests services.customers.tests.test_views.CustomerOpportunityListTests services.customers.tests.test_views.CustomerRiskListTests services.customers.tests.test_views.ContactDetailTests --noinput`
Expected: the three `test_each_row_carries_its_account_id` and `test_patching_account_id_does_not_move_the_contact` ERROR with `KeyError: 'account_id'`.

- [ ] **Step 5: Add `account_id` to the three serializers**

In `services/customers/serializers.py`:

`ContactSerializer.Meta.fields` — insert `"account_id"` before `"account_name"`:

```python
            "companies",
            "account_id",
            "account_name",
        ]
```

`OpportunitySerializer.Meta.fields` and `RiskSerializer.Meta.fields` — the same, at the end of each list:

```python
            "companies",
            "account_id",
            "account_name",
        ]
```

DRF builds an attname such as `account_id` as a `ReadOnlyField`, so no `read_only_fields` entry is needed (the unit test proves it).

In `ContactSerializer`'s docstring, replace "`account_name` is set only for an account-level contact, so the standalone page can show which account within the company it belongs to" with "`account_id`/`account_name` are set only for an account-level contact: the standalone page shows which account within the company it belongs to, and the organisation page's account chips filter on the id". In `OpportunitySerializer`'s docstring, replace "`companies`/`account_name`\n    mirror ContactSerializer's own fields exactly" with "`companies`/`account_id`/`account_name`\n    mirror ContactSerializer's own fields exactly".

In `SurveySerializer`'s docstring, replace:

```text
    way the Pipelines board does. Unlike Opportunity/Risk, this also
    exposes `account_id` (a plain passthrough of the FK, not a
    SerializerMethodField) — Opportunity/Risk rows never navigate
    anywhere on click, but the standalone Surveys page's own row-click
    does (into that Account's own Details page), and `account_name`
    alone isn't enough to build that link.
```

with:

```text
    way the Pipelines board does. `account_id` is a plain passthrough of
    the FK, not a SerializerMethodField: the standalone Surveys page's
    row-click opens that Account's Details page, and `account_name` alone
    isn't enough to build that link. Contact/Opportunity/RiskSerializer
    carry it too, for the organisation page's account chips.
```

In `services/customers/views.py`, `ContactDetailView`'s docstring, replace:

```text
    customer_id/account_id it may not even have on hand (the
    standalone /contacts/list page's own rows don't carry an
    account_id, only companies/account_name for display).

    PATCH can't move a Contact between parents — `customer`/`account`
    aren't in ContactSerializer's own `fields` list at all, so a PATCH
    body naming either is silently ignored rather than erroring."""
```

with:

```text
    customer_id/account_id it may not even have on hand.

    PATCH can't move a Contact between parents — `customer`/`account`
    aren't in ContactSerializer's own `fields` list and `account_id` is
    read-only, so a PATCH body naming any of them is silently ignored
    rather than erroring."""
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `venv/bin/python manage.py test services.customers.tests.test_rollup_serializers services.customers.tests.test_views services.customers.tests.test_pipeline_departments services.customers.tests.test_contact --noinput`
Expected: PASS (all).

- [ ] **Step 7: Update the contract**

In `docs/API_CONTRACTS.md`:

1. `### GET/POST /api/v1/customers/<customer_id>/contacts/` — replace its **Response `200`** paragraph with:

```text
**Response `200`** (GET) — a plain array, each entry: `id`, `name`, `role`,
`role_display`, `email`, `phone`, `language`, `status`, `sentiment`,
`sentiment_source`, `sentiment_evidence`, `sentiment_computed_at`,
`last_contacted_at`, `companies` (every ultimate parent Customer),
`account_id`/`account_name` (both `null` for an organisation-level row;
the organisation page's account chips filter on `account_id`).
**Response `201`** (POST) — one such entry.
```

2. `### GET /api/v1/contacts/` — in the JSON example, replace

```json
      "company_id": 6,
      "company_name": "Apple Inc",
      "account_name": null
```

with

```json
      "companies": [{ "id": 6, "name": "Apple Inc" }],
      "account_id": null,
      "account_name": null
```

3. `### GET/POST /api/v1/customers/<customer_id>/opportunities/` and `### GET/POST /api/v1/customers/<customer_id>/risks/` — in each, replace the **Response `200`** paragraph with:

```text
**Response `200`** (GET) — a plain array, each entry: `id`, `title`,
`mrr`, `stage`, `stage_display`, `priority`, `priority_display`,
`department`, `department_display`, `companies`, `account_id`/
`account_name` (both `null` for an organisation-level row; the
organisation page's account chips filter on `account_id`).
**Response `201`** (POST) — one such entry.
```

4. `### GET/POST /api/v1/opportunities/` — in the JSON example, replace

```json
    "company_id": 6,
    "company_name": "Apple Inc",
    "account_name": "Apple EMEA"
```

with

```json
    "department": "",
    "department_display": "",
    "companies": [{ "id": 6, "name": "Apple Inc" }],
    "account_id": 12,
    "account_name": "Apple EMEA"
```

5. `### GET/POST /api/v1/risks/` — in the JSON example, replace

```json
    "company_id": 8,
    "company_name": "WeWork",
    "account_name": null
```

with

```json
    "department": "",
    "department_display": "",
    "companies": [{ "id": 8, "name": "WeWork" }],
    "account_id": null,
    "account_name": null
```

6. `### GET/POST /api/v1/customers/<customer_id>/surveys/` — replace "`account_id`/`account_name` (both `null` for an organisation-level row — unlike Opportunity/Risk, `account_id` is a real field here, not just `account_name`, since the standalone Surveys page's own row-click needs it to navigate to that Account's Details page)" with "`account_id`/`account_name` (both `null` for an organisation-level row; the standalone Surveys page's row-click uses `account_id` to open that Account's Details page)".

- [ ] **Step 8: Lint and commit**

Run: `venv/bin/ruff check services/customers && venv/bin/ruff format --check services/customers`
Expected: no errors, no files to reformat.

```bash
git add services/customers/serializers.py services/customers/views.py \
  services/customers/tests/test_rollup_serializers.py services/customers/tests/test_views.py \
  docs/API_CONTRACTS.md
git commit -m "feat(customers): account_id on contacts, opportunities and risks

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: End-to-end flow

**Files:**
- Create: `e2e/test_organization_lists_flow.py`

**Interfaces:**
- Consumes: `e2e.http.http_get(url, token)` and `http_post(url, payload, token)` (JSON only); the existing create endpoints (`POST /auth/signup/`, `/auth/users/`, `/auth/login/`, `/customers/`, `/customers/<id>/accounts/`, `…/calls/`, `…/surveys/`, `…/accounts/<id>/contacts/`, `/customers/<id>/opportunities/`, `…/accounts/<id>/risks/`); Tasks 1–4.
- Produces: nothing later tasks use.

- [ ] **Step 1: Write the test**

Create `e2e/test_organization_lists_flow.py`:

```python
"""End-to-end tier: real server, real HTTP, real test DB. A CSM builds an
organisation with one account and logs records on both levels, then reads
the organisation page's lists: calls roll up with their account tags, the
survey list narrows to the organisation, and contacts, opportunities and
risks carry the account id the chips filter on. A peer who cannot open the
organisation gets a 404 on its calls and an empty survey list."""

from urllib.parse import urlencode

from django.test import LiveServerTestCase

from e2e.http import http_get, http_post


class OrganizationListsFlowTests(LiveServerTestCase):
    WHEN = "2026-09-20T10:00:00Z"

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

        # 2. Carl's organisation with one account, and a second organisation.
        pizza = self.create("/customers/", {"name": "Pizza Hut"}, carl)["id"]
        emea = self.create(f"/customers/{pizza}/accounts/", {"name": "EMEA"}, carl)["id"]
        initech = self.create("/customers/", {"name": "Initech"}, carl)["id"]

        # 3. A call on each level. The organisation path stays organisation-level.
        call = self.create(
            f"/customers/{pizza}/calls/",
            {"title": "Kick-off", "occurred_at": self.WHEN, "summary": "Scope agreed."},
            carl,
        )
        self.assertEqual((call["account_id"], call["account_name"]), (None, None))
        call = self.create(
            f"/customers/{pizza}/accounts/{emea}/calls/",
            {"title": "EMEA QBR", "occurred_at": self.WHEN, "summary": "Renewal on track."},
            carl,
        )
        self.assertEqual((call["account_id"], call["account_name"]), (emea, "EMEA"))

        # 4. The organisation's Calls list rolls the account's call up, tagged.
        calls = self.read(f"/customers/{pizza}/calls/", carl)
        self.assertEqual(
            {c["title"]: (c["account_id"], c["account_name"]) for c in calls},
            {"Kick-off": (None, None), "EMEA QBR": (emea, "EMEA")},
        )
        self.assertEqual(self.read(f"/customers/{pizza}/files/", carl), [])

        # 5. Surveys on both levels and on another organisation; the filter narrows.
        self.create(
            f"/customers/{pizza}/surveys/", {"survey_type": "nps", "sent_at": "2026-09-01"}, carl
        )
        self.create(
            f"/customers/{pizza}/accounts/{emea}/surveys/",
            {"survey_type": "csat", "sent_at": "2026-09-02"},
            carl,
        )
        self.create(
            f"/customers/{initech}/surveys/", {"survey_type": "nps", "sent_at": "2026-09-03"}, carl
        )
        surveys = self.read("/surveys/?" + urlencode({"customer": pizza}), carl)
        self.assertEqual(
            {(s["survey_type"], s["account_id"]) for s in surveys}, {("nps", None), ("csat", emea)}
        )
        self.assertEqual(len(self.read("/surveys/", carl)), 3)

        # 6. People and Deals & risks rows carry the account id the chips use.
        self.create(
            f"/customers/{pizza}/accounts/{emea}/contacts/",
            {"name": "Sam Lee", "role": "champion", "email": "sam@pizza.io"},
            carl,
        )
        self.create(
            f"/customers/{pizza}/opportunities/",
            {"title": "Upsell", "mrr": "500.00", "stage": "discovery", "priority": "high"},
            carl,
        )
        self.create(
            f"/customers/{pizza}/accounts/{emea}/risks/",
            {"title": "Budget cut", "mrr": "200.00", "stage": "open", "priority": "medium"},
            carl,
        )
        contacts = self.read(f"/customers/{pizza}/contacts/", carl)
        self.assertEqual([(c["name"], c["account_id"]) for c in contacts], [("Sam Lee", emea)])
        opportunities = self.read(f"/customers/{pizza}/opportunities/", carl)
        self.assertEqual([(o["title"], o["account_id"]) for o in opportunities], [("Upsell", None)])
        risks = self.read(f"/customers/{pizza}/risks/", carl)
        self.assertEqual([(r["title"], r["account_id"]) for r in risks], [("Budget cut", emea)])

        # 7. Dana cannot open Carl's organisation: a 404, and no surveys by id.
        status, _ = http_get(self.api(f"/customers/{pizza}/calls/"), token=dana)
        self.assertEqual(status, 404)
        self.assertEqual(self.read("/surveys/?" + urlencode({"customer": pizza}), dana), [])
```

- [ ] **Step 2: Run it**

Run: `venv/bin/python manage.py test e2e.test_organization_lists_flow --noinput`
Expected: PASS. It exercises Tasks 1–4 together, so it passes on the first run; if it fails, the failure names the task at fault (fix it there, not here). If step 7's 404 fails because a created customer is unowned, read `CustomerListCreateView.perform_create` (it defaults the owner to the caller) before changing the test.

- [ ] **Step 3: Lint and commit**

Run: `venv/bin/ruff check e2e && venv/bin/ruff format --check e2e`
Expected: no errors, no files to reformat.

```bash
git add e2e/test_organization_lists_flow.py
git commit -m "test(e2e): organisation page lists flow

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Product docs

**Files:**
- Modify: `docs/product/01-prd.md` (§5.2 Records table ~line 142; §9 Release history ~line 303)
- Modify: `docs/product/05-backend-schema.md` (§11 Visibility rules table ~line 379)

**Interfaces:**
- Consumes: the behaviour of Tasks 1–4.
- Produces: nothing code uses.

- [ ] **Step 1: Update the PRD**

In `docs/product/01-prd.md`, §5.2 Records, add this row directly after the "Organisation page Story (detail redesign)" row:

```text
| Organisation page lists (detail redesign, delivery 2) | Built (backend) | Files and calls on an organisation roll up its accounts' records the viewer may open, each tagged with its account; contacts, opportunities and risks carry `account_id` for the account chips; the survey list filters by organisation (`?customer=`), an id the viewer cannot open reading as empty |
```

Change the Files row's notes to "Closed type list, magic-byte check, 25 MB cap, authenticated download only. An organisation's list includes its visible accounts' files, tagged", the CallSense row's notes to "Log a call, attach a transcript, model writes the summary, call is classified immediately. An organisation's list includes its visible accounts' calls, tagged", and the Surveys row's notes to "Logged and scored by hand; filter by organisation; no email delivery, no multi-question surveys".

In §9 Release history, append:

```text
| 2026-09-27 | Organisation page lists (backend): files and calls roll up visible accounts, `account_id` on contacts, opportunities and risks, survey organisation filter |
```

- [ ] **Step 2: Update the schema doc**

In `docs/product/05-backend-schema.md`, §11 Visibility rules, add this row directly after the "Customers and accounts" row:

```text
| Organisation roll-ups (`/customers/<id>/…` contacts, opportunities, risks, surveys, canvases, files, calls; `/surveys/?customer=`) | The organisation's own records, plus those on its accounts in `visible_accounts`: being able to open the organisation is not enough for an account-level record | `scoping.customer_rollup_q` |
```

- [ ] **Step 3: Check the docs format and commit**

Run: `venv/bin/ruff format --check docs`
Expected: no files to reformat (the edits add no code fences).

```bash
git add docs/product/01-prd.md docs/product/05-backend-schema.md
git commit -m "docs(customers): organisation page lists in the PRD and schema

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Full verification

**Files:** none are created. Fix whatever a step below uncovers in the task that owns it, then re-run from Step 1.

- [ ] **Step 1: Run the whole suite as CI does**

Run: `venv/bin/python manage.py test --parallel auto --noinput`
Expected: every test passes. Record the final `Ran N tests … OK` line. If the local run reports `OperationalError('the connection is closed')` from the parallel runner (a local Postgres connection limit, not a test failure), re-run with `--parallel 4`, and re-run any named failures alone to confirm.

- [ ] **Step 2: Lint, format and migrations**

Run:

```bash
venv/bin/ruff check . && venv/bin/ruff format --check .
venv/bin/python manage.py makemigrations --check --dry-run
```

Expected: no lint errors, no files to reformat (this includes the Markdown docs), and "No changes detected".

- [ ] **Step 3: Run semgrep as CI runs it**

Run, from the repo root (semgrep is not installed locally; CI uses the `semgrep/semgrep` image):

```bash
docker run --rm -v "$PWD:/src" -w /src semgrep/semgrep semgrep scan --error --metrics=off \
  --config p/security-audit --config p/secrets --config p/owasp-top-ten --config p/django \
  --exclude .claude --exclude venv --exclude '**/tests/**' --exclude '**/migrations/**' --exclude e2e
python3 .claude/skills/soc2-dev/scripts/soc2_scan.py . --format md --fail-on critical
```

Expected: semgrep exits 0 with 0 blocking findings; the soc2 scan has no critical findings.

- [ ] **Step 4: Smoke test against the seeded dev database**

Run:

```bash
venv/bin/python manage.py runserver 8011 &
TOKEN=$(curl -s -X POST localhost:8011/api/v1/auth/login/ -H 'Content-Type: application/json' \
  -d '{"email":"<a seeded admin email>","password":"<its password>"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access"])')
ORG=$(curl -s "localhost:8011/api/v1/organizations/portfolio/?limit=1" -H "Authorization: Bearer $TOKEN" | python3 -c 'import sys,json;print(json.load(sys.stdin)["results"][0]["id"])')
curl -s "localhost:8011/api/v1/customers/$ORG/calls/" -H "Authorization: Bearer $TOKEN" | python3 -c 'import sys,json;print([(c["title"],c["account_name"]) for c in json.load(sys.stdin)][:5])'
curl -s "localhost:8011/api/v1/customers/$ORG/files/" -H "Authorization: Bearer $TOKEN" | python3 -c 'import sys,json;print([(f["name"],f["account_name"]) for f in json.load(sys.stdin)][:5])'
curl -s "localhost:8011/api/v1/surveys/?customer=$ORG" -H "Authorization: Bearer $TOKEN" | python3 -c 'import sys,json;print(len(json.load(sys.stdin)))'
curl -s "localhost:8011/api/v1/surveys/?customer=abc" -H "Authorization: Bearer $TOKEN"
curl -s "localhost:8011/api/v1/customers/$ORG/contacts/" -H "Authorization: Bearer $TOKEN" | python3 -c 'import sys,json;print([(c["name"],c["account_id"]) for c in json.load(sys.stdin)][:5])'
kill %1
```

Take the seeded admin's email and password from `README.md` ("Seed data"). Expected: calls and files list with `account_name` set on account rows; a survey count for the organisation; `[]` for `customer=abc`; contacts with `account_id`.

- [ ] **Step 5: Report**

Use superpowers:verification-before-completion. Report the test count and the ruff, migrations, semgrep and soc2 results, and the smoke output. Then hand off to superpowers:finishing-a-development-branch. The backend PR merges and deploys before the frontend PR (spec §8): wait for `/api/v1/surveys/?customer=1` on the live VM to answer before merging the frontend. The PR description lists the frontend-facing contract: `account_id`/`account_name` on files and calls (organisation path rolls up; creates stay organisation-level), `?customer=` on `/surveys/` (blank is no filter, anything unopenable is `[]`), and `account_id` on contacts, opportunities and risks.

---

## Self-review

**Spec coverage.**

| Spec §6 requirement | Where it is covered |
|---|---|
| Files on an organisation roll up under `customer_rollup_q` | Task 1, Steps 3 and 6; `FileRollupTests` |
| Calls on an organisation roll up under `customer_rollup_q` | Task 2, Steps 3 and 6; `CallRollupTests` |
| Each record carries `account_id` and `account_name` | Task 1 Step 5 (files, and a call's transcript), Task 2 Step 5 (calls); unit tests in `test_rollup_serializers.py` |
| Creating on the organisation path stays organisation-level | `test_uploading_on_the_organisation_path_stays_organisation_level`, `test_logging_on_the_organisation_path_stays_organisation_level`, e2e step 3 |
| `GET /surveys/?customer=<id>`, organisation plus visible accounts | Task 3; `test_narrows_to_the_organisation_and_its_visible_accounts`, e2e step 5 |
| Unknown or invisible ids give an empty list, never an error | `test_an_id_the_viewer_cannot_open_reads_as_empty` (invisible, other tenant, unknown, non-numeric, negative, decimal), e2e step 7 |
| `account_id` on contact, opportunity and risk serializers | Task 4; `ChipAccountIdTests`, three `test_each_row_carries_its_account_id`, e2e step 6 |
| Query counts pinned | files 2, calls 3, surveys `?customer=` 3, each at two list sizes (Tasks 1–3) |
| Privacy tests use the "blind to one account" setup | `FileRollupTests`, `CallRollupTests`, `SurveyListCustomerFilterTests` all start from `blind_to_one_account` |
| Backend PR first (§8) | Task 7 Step 5 |
| Docs | API_CONTRACTS in Tasks 1–4; PRD and schema in Task 6 |

**Placeholder scan.** No "TBD", "TODO" or "similar to Task N". One step asks the implementer to look one thing up, with the exact place: the smoke test's seeded credentials in `README.md` (Task 7 Step 4).

**Type consistency.** `Fixture.attach(name, **fields)` is defined in Task 1 and used with `source=` and a parent kwarg in Task 2. `get_account_name(obj)` has the same name on `AttachmentSerializer` and `CallSerializer`. `blind_to_one_account` returns `(viewer, seen, hidden)` with accounts named `"Seen"`/`"Hidden"`, as every test asserts. `SurveyListView._one_organisation(user, raw)` is only called from `get_queryset`. The pinned counts (2, 3, 3) were measured on this branch with exactly this code, with the warm-up read the tests make.

**Dry run.** Before this plan was committed, its code blocks were applied to this branch as written: the full suite (`--parallel 4`, 2,743 tests), `ruff check`, `ruff format --check`, `makemigrations --check` and semgrep (docker, `services/customers`) all passed, and the changes were then reverted. One wrapped call in Task 4 carries a trailing comma on purpose: ruff formats a fenced block dedented, so without it the plan and the code would want different line breaks.
