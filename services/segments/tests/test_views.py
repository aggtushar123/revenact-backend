"""The segment endpoints a person reads and writes: list, create, read, edit,
delete and duplicate. Missing and hidden read the same; only the owner
writes; a shared viewer reads the same rules with what they cannot open
redacted."""

import json
from datetime import timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from core.models import AuditEvent
from services.accounts.models import User
from services.segments.models import MAX_OWNED, Segment, SegmentChange
from services.segments.tests.fixtures import SegmentFixture, person, rule

URL = "/api/v1/segments/"
LOW_HEALTH = rule("health_score", "lt", 5)
HEALTHY = rule("health_score", "gt", 0)
NOT_OWNER = {"detail": "Only the segment's owner can change it."}
DETAIL_KEYS = {
    "id", "name", "description", "kind", "rules", "labels", "pinned_ids", "excluded_ids",
    "sharing", "shared_with", "owner", "is_owner", "alert_on_changes", "paused",
    "member_count", "last_evaluated_on", "created_at", "updated_at",
}  # fmt: skip


class Endpoint(SegmentFixture):
    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def events(self, action):
        rows = AuditEvent.objects.filter(action=action).order_by("pk")
        return list(rows.values_list("target_id", "metadata"))

    def count_queries(self, user, path):
        client = APIClient()
        # A fresh user, as a real request loads one: memoised lookups on a
        # reused instance would flatter the count.
        client.force_authenticate(User.objects.get(pk=user.pk))
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(path)
        self.assertEqual(response.status_code, 200, response.content)
        return len(ctx.captured_queries)


class CreateTests(Endpoint):
    def post(self, user, **body):
        return self.api(user).post(
            URL, {"name": "Unwell", "kind": "customer", **body}, format="json"
        )

    def test_requires_authentication(self):
        self.assertEqual(APIClient().get(URL).status_code, 401)
        self.assertEqual(APIClient().post(URL, {}, format="json").status_code, 401)

    def test_create_sets_the_owner_and_the_baseline(self):
        response = self.post(self.admin, rules=LOW_HEALTH, alert_on_changes=True)
        self.assertEqual(response.status_code, 201, response.content)
        body = response.data
        segment = Segment.objects.get(pk=body["id"])
        self.assertEqual((segment.owner, segment.organisation), (self.admin, self.org))
        self.assertEqual(
            (segment.last_members, segment.member_count, segment.last_evaluated_on),
            ([self.taco.pk], 1, self.today),
        )
        self.assertEqual(set(body), DETAIL_KEYS)
        self.assertEqual(
            (body["is_owner"], body["sharing"], body["member_count"], body["alert_on_changes"]),
            (True, "private", 1, True),
        )
        self.assertEqual(self.events("segment.created"), [(str(segment.pk), {"kind": "customer"})])
        self.assertEqual(self.events("segment.shared"), [])
        self.assertFalse(SegmentChange.objects.exists())

    def test_sharing_on_create_is_audited_with_ids_only(self):
        response = self.post(self.csm, sharing="people", shared_with=[self.other.pk])
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(
            self.events("segment.shared"),
            [(str(response.data["id"]), {"sharing": "people", "shared_with": [self.other.pk]})],
        )
        self.assertEqual(response.data["shared_with"], [{"id": self.other.pk, "name": "Dana CSM"}])

    def test_bad_rules_are_a_400_naming_the_problem(self):
        response = self.post(self.csm, rules=rule("password", "is", "x"))
        self.assertEqual(
            (response.status_code, response.data),
            (400, {"rules": ['Unknown field "password" for organisations.']}),
        )
        response = self.post(
            self.csm, kind="account", rules=rule("organisation", "is", self.taco.pk)
        )
        self.assertEqual(response.data, {"rules": ["Not an organisation you can open."]})

    def test_a_kind_is_required(self):
        response = self.api(self.csm).post(URL, {"name": "No kind"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("kind", response.data)

    def test_at_most_fifty_owned(self):
        Segment.objects.bulk_create(
            Segment(organisation=self.org, owner=self.csm, name=f"S{i}", kind="customer")
            for i in range(MAX_OWNED)
        )
        response = self.post(self.csm)
        self.assertEqual(
            (response.status_code, response.data),
            (400, {"detail": "You can own at most 50 segments."}),
        )
        self.assertEqual(self.post(self.admin).status_code, 201)

    def test_the_limit_is_counted_under_a_lock_on_the_owner(self):
        """Two creates at once must not both pass the count: the owner's row
        is locked (`FOR UPDATE`) before their segments are counted."""
        for create in (
            lambda: self.post(self.csm),
            lambda: self.api(self.csm).post(
                f"{URL}{self.segment(owner=self.admin, sharing='workspace').pk}/duplicate/"
            ),
        ):
            with CaptureQueriesContext(connection) as ctx:
                self.assertEqual(create().status_code, 201)
            sql = [query["sql"] for query in ctx.captured_queries]
            lock = next(
                i for i, q in enumerate(sql) if '"accounts_user"' in q and "FOR UPDATE" in q
            )
            count = next(
                i
                for i, q in enumerate(sql)
                if q.startswith("SELECT COUNT(*)") and '"segments_segment"' in q
            )
            self.assertLess(lock, count)

    def test_sharing_with_people_needs_active_teammates_from_the_workspace(self):
        self.assertEqual(
            self.post(self.csm, sharing="people", shared_with=[]).data,
            {"shared_with": ["Choose at least one teammate."]},
        )
        inactive = person("ivy@acme.io", "Ivy", self.org, is_active=False)
        missing = self.post(self.csm, sharing="people", shared_with=[999999])
        self.assertEqual(missing.status_code, 400)
        self.assertIn("shared_with", missing.data)
        for outsider in (self.stranger.pk, self.csm.pk, inactive.pk):
            with self.subTest(outsider=outsider):
                response = self.post(self.csm, sharing="people", shared_with=[outsider])
                # The whole body, with the id swapped: a foreign teammate and
                # a missing one read the same.
                self.assertEqual(
                    (response.status_code, json.dumps(response.data)),
                    (400, json.dumps(missing.data).replace("999999", str(outsider))),
                )


class ListTests(Endpoint):
    def names(self, user, **query):
        return [row["name"] for row in self.api(user).get(URL, query).data]

    def test_mine_shared_and_all(self):
        self.segment(owner=self.csm, name="Mine")
        self.segment(owner=self.admin, name="Everyone's", sharing="workspace")
        self.segment(owner=self.other, name="For Carl", sharing="people").shared_with.add(self.csm)
        self.segment(owner=self.other, name="Dana's own")
        self.segment(owner=self.other, name="For Alice", sharing="people").shared_with.add(
            self.admin
        )
        Segment.objects.create(
            organisation=self.other_org, owner=self.stranger, name="Globex", kind="customer",
            sharing="workspace",
        )  # fmt: skip
        self.assertEqual(self.names(self.csm), ["Everyone's", "For Carl", "Mine"])
        self.assertEqual(self.names(self.csm, scope="mine"), ["Mine"])
        self.assertEqual(self.names(self.csm, scope="shared"), ["Everyone's", "For Carl"])
        self.assertEqual(self.names(self.csm, search="carl"), ["For Carl"])
        self.assertEqual(self.names(self.stranger), ["Globex"])

    def test_a_row_counts_today_and_draws_thirty_days(self):
        segment = self.segment(owner=self.csm, member_count=5, last_evaluated_on=self.today)
        record = iter(range(1, 100))
        for days_ago, change, count in ((0, "entered", 2), (0, "left", 1), (3, "entered", 1)):
            for _ in range(count):
                SegmentChange.objects.create(
                    segment=segment, record_id=next(record), change=change,
                    changed_on=self.today - timedelta(days=days_ago),
                )  # fmt: skip
        row = self.api(self.csm).get(URL).data[0]
        self.assertEqual(
            set(row),
            {
                "id", "name", "kind", "owner", "is_owner", "sharing", "paused",
                "member_count", "today", "sparkline", "updated_at",
            },
        )  # fmt: skip
        self.assertEqual(row["today"], {"entered": 2, "left": 1})
        # 5 today, 4 before today's moves, 3 before the entry three days ago.
        self.assertEqual(row["sparkline"], [3] * 26 + [4, 4, 4, 5])

    def test_a_segment_never_evaluated_has_no_sparkline(self):
        self.segment(owner=self.csm)
        row = self.api(self.csm).get(URL).data[0]
        self.assertEqual((row["sparkline"], row["today"]), ([], {"entered": 0, "left": 0}))

    def test_a_shared_row_carries_none_of_the_owners_figures(self):
        segment = self.segment(
            owner=self.csm, sharing="workspace", member_count=5, last_evaluated_on=self.today
        )
        SegmentChange.objects.create(
            segment=segment, record_id=1, change="entered", changed_on=self.today
        )
        carl = self.api(self.csm).get(URL).data[0]
        self.assertEqual((carl["member_count"], carl["today"]), (5, {"entered": 1, "left": 0}))
        self.assertEqual(len(carl["sparkline"]), 30)
        dana = self.api(self.other).get(URL).data[0]
        self.assertEqual(
            (dana["is_owner"], dana["member_count"], dana["today"], dana["sparkline"]),
            (False, None, None, None),
        )

    def test_the_query_count_is_pinned(self):
        """Two queries, whatever the number of segments: the segments (owner
        joined, member lists deferred), and every owned row's last 30 days
        of changes, counted per day. Rows owned by others and shared by
        name with several people are among them, so a per-row owner or
        teammates lookup would show."""
        for name in ("D", "E"):
            theirs = self.segment(owner=self.other, name=name, sharing="people")
            theirs.shared_with.add(self.csm, self.admin)
        self.segment(owner=self.admin, name="F", sharing="workspace")
        for name in ("A", "B", "C"):
            segment = self.segment(
                owner=self.csm, name=name, member_count=1, last_evaluated_on=self.today
            )
            SegmentChange.objects.create(
                segment=segment, record_id=1, change="entered", changed_on=self.today
            )
        self.assertEqual(self.count_queries(self.csm, URL), 2)


class DetailTests(Endpoint):
    def get(self, user, segment):
        return self.api(user).get(f"{URL}{segment.pk}/")

    def test_the_owner_reads_their_segment(self):
        segment = self.segment(owner=self.csm, rules=HEALTHY)
        body = self.get(self.csm, segment).data
        self.assertEqual(set(body), DETAIL_KEYS)
        self.assertEqual(body["owner"], {"id": self.csm.pk, "name": "Carl CSM"})
        self.assertEqual(body["rules"], HEALTHY)

    def test_only_the_owner_reads_the_owners_member_count(self):
        segment = self.segment(owner=self.csm, sharing="workspace", member_count=7)
        self.assertEqual(self.get(self.csm, segment).data["member_count"], 7)
        self.assertIsNone(self.get(self.other, segment).data["member_count"])

    def test_a_hidden_segment_reads_like_a_missing_one(self):
        private = self.segment(owner=self.csm)
        theirs = Segment.objects.create(
            organisation=self.other_org, owner=self.stranger, name="G", kind="customer",
            sharing="workspace",
        )  # fmt: skip
        missing = self.api(self.other).get(f"{URL}999999/")
        self.assertEqual(missing.status_code, 404)
        for segment in (private, theirs):
            response = self.get(self.other, segment)
            self.assertEqual((response.status_code, response.data), (404, missing.data))

    def test_rule_values_naming_hidden_records_read_null_and_unnamed(self):
        segment = self.segment(
            owner=self.csm, kind="account", sharing="workspace",
            rules=rule("organisation", "in", [self.pizza.pk]),
        )  # fmt: skip
        carl = self.get(self.csm, segment).data
        self.assertEqual(carl["rules"]["conditions"][0]["value"], [self.pizza.pk])
        self.assertEqual(carl["labels"]["organisations"], {str(self.pizza.pk): "Pizza Hut"})
        dana = self.get(self.other, segment).data
        self.assertEqual(dana["rules"]["conditions"][0]["value"], [None])
        self.assertEqual(dana["labels"]["organisations"], {})
        self.assertNotIn("Pizza Hut", json.dumps(dana))
        self.assertNotIn(str(self.pizza.pk), json.dumps(dana["rules"]))

    def test_pins_of_hidden_records_are_neither_shown_nor_named(self):
        apac = self.make_account("Pizza APAC", self.pizza, self.csm)
        segment = self.segment(
            owner=self.csm, kind="account", sharing="workspace", pinned_ids=[self.emea.pk],
            excluded_ids=[apac.pk],
        )  # fmt: skip
        carl, dana = self.get(self.csm, segment).data, self.get(self.other, segment).data
        self.assertEqual((carl["pinned_ids"], carl["excluded_ids"]), ([self.emea.pk], [apac.pk]))
        self.assertEqual((dana["pinned_ids"], dana["excluded_ids"]), ([], []))

    def test_the_query_count_is_pinned(self):
        """Two queries for a segment with no ids in its rules and no pins:
        the segment (owner joined) and its teammates."""
        segment = self.segment(owner=self.csm, rules=HEALTHY)
        self.assertEqual(self.count_queries(self.csm, f"{URL}{segment.pk}/"), 2)


class WriteTests(Endpoint):
    def patch(self, user, segment, **body):
        return self.api(user).patch(f"{URL}{segment.pk}/", body, format="json")

    def test_the_owner_edits_and_each_change_is_audited_by_field_name(self):
        segment = self.segment(owner=self.csm)
        response = self.patch(self.csm, segment, name="Renamed", description="Why")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["name"], "Renamed")
        self.assertEqual(
            self.events("segment.updated"), [(str(segment.pk), {"fields": ["description", "name"]})]
        )

    def test_a_patch_that_changes_nothing_records_nothing(self):
        segment = self.segment(owner=self.csm, name="Same")
        self.assertEqual(self.patch(self.csm, segment, name="Same").status_code, 200)
        self.assertEqual(self.events("segment.updated"), [])

    def test_changing_the_rules_starts_the_history_again(self):
        segment = self.segment(
            owner=self.admin, rules=LOW_HEALTH, last_members=[self.taco.pk], member_count=1,
            last_evaluated_on=self.today - timedelta(days=3),
        )  # fmt: skip
        self.assertEqual(self.patch(self.admin, segment, rules=HEALTHY).status_code, 200)
        segment.refresh_from_db()
        self.assertEqual(segment.last_members, sorted([self.pizza.pk, self.taco.pk]))
        self.assertEqual(segment.last_evaluated_on, self.today)
        self.assertFalse(SegmentChange.objects.exists())

    def test_sharing_changes_are_audited_as_shared(self):
        segment = self.segment(owner=self.csm)
        self.patch(self.csm, segment, sharing="workspace")
        self.patch(self.csm, segment, sharing="people", shared_with=[self.other.pk])
        self.patch(self.csm, segment, sharing="private")
        self.assertEqual(
            [metadata for _target, metadata in self.events("segment.shared")],
            [
                {"sharing": "workspace", "shared_with": []},
                {"sharing": "people", "shared_with": [self.other.pk]},
                {"sharing": "private", "shared_with": []},
            ],
        )
        self.assertFalse(segment.shared_with.exists())

    def test_the_kind_cannot_change(self):
        segment = self.segment(owner=self.csm)
        response = self.patch(self.csm, segment, kind="account")
        self.assertEqual(
            (response.status_code, response.data),
            (400, {"kind": ["A segment's kind cannot change."]}),
        )

    def test_non_owners_cannot_write(self):
        shared = self.segment(owner=self.csm, name="Shared", sharing="workspace")
        private = self.segment(owner=self.csm, name="Private")
        dana = self.api(self.other)
        missing = dana.delete(f"{URL}999999/")
        self.assertEqual(missing.status_code, 404)
        for target, expected in ((shared, (403, NOT_OWNER)), (private, (404, missing.data))):
            path = f"{URL}{target.pk}/"
            for response in (dana.patch(path, {"name": "x"}, format="json"), dana.delete(path)):
                with self.subTest(target=target.name, method=response.request["REQUEST_METHOD"]):
                    self.assertEqual((response.status_code, response.data), expected)
        self.assertEqual(
            sorted(Segment.objects.values_list("name", flat=True)), ["Private", "Shared"]
        )

    def test_the_owner_deletes(self):
        segment = self.segment(owner=self.csm)
        response = self.api(self.csm).delete(f"{URL}{segment.pk}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Segment.objects.exists())
        self.assertEqual(self.events("segment.deleted"), [(str(segment.pk), {"kind": "customer"})])

    def test_the_segment_is_locked_before_it_is_deleted(self):
        """A delete while the nightly step holds the segment must queue up,
        not race it: the row is read `FOR UPDATE` before the `DELETE`."""
        segment = self.segment(owner=self.csm)
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.api(self.csm).delete(f"{URL}{segment.pk}/").status_code, 204)
        sql = [query["sql"] for query in ctx.captured_queries]
        lock = next(
            (i for i, q in enumerate(sql) if '"segments_segment"' in q and "FOR UPDATE" in q),
            None,
        )
        delete = next(i for i, q in enumerate(sql) if q.startswith('DELETE FROM "segments_segment"'))
        self.assertIsNotNone(lock)
        self.assertLess(lock, delete)


class DuplicateTests(Endpoint):
    def duplicate(self, user, segment):
        return self.api(user).post(f"{URL}{segment.pk}/duplicate/")

    def test_a_reader_duplicates_into_their_own_without_what_they_cannot_open(self):
        source = self.segment(
            owner=self.csm, kind="account", sharing="workspace", alert_on_changes=True,
            description="Carl's", rules=rule("organisation", "in", [self.pizza.pk]),
            pinned_ids=[self.emea.pk],
        )  # fmt: skip
        response = self.duplicate(self.other, source)
        self.assertEqual(response.status_code, 201, response.content)
        copy = Segment.objects.get(pk=response.data["id"])
        self.assertEqual(copy.owner, self.other)
        self.assertEqual((copy.name, copy.description), ("Renewal risk (copy)", "Carl's"))
        self.assertEqual((copy.sharing, copy.alert_on_changes), ("private", False))
        self.assertEqual(copy.rules["conditions"][0]["value"], [None])
        self.assertEqual(copy.pinned_ids, [])
        self.assertEqual(copy.last_evaluated_on, self.today)
        self.assertEqual(self.events("segment.duplicated"), [(str(copy.pk), {"source": source.pk})])

    def test_a_segment_the_caller_cannot_read_cannot_be_duplicated(self):
        private = self.segment(owner=self.csm)
        self.assertEqual(self.duplicate(self.other, private).status_code, 404)

    def test_duplicating_counts_towards_the_limit(self):
        source = self.segment(owner=self.csm, sharing="workspace")
        Segment.objects.bulk_create(
            Segment(organisation=self.org, owner=self.other, name=f"S{i}", kind="customer")
            for i in range(MAX_OWNED)
        )
        response = self.duplicate(self.other, source)
        self.assertEqual(
            (response.status_code, response.data),
            (400, {"detail": "You can own at most 50 segments."}),
        )
