"""The two segment tables and the alert's notification kind."""

from django.db import IntegrityError, transaction

from services.notifications.models import Notification
from services.segments.models import Segment, SegmentChange, default_rules
from services.segments.tests.fixtures import SegmentFixture


class SegmentModelTests(SegmentFixture):
    def test_a_new_segment_is_private_with_no_rules_and_no_history(self):
        segment = self.segment()
        self.assertEqual(segment.rules, {"match": "all", "conditions": []})
        self.assertEqual(
            (segment.sharing, segment.pinned_ids, segment.excluded_ids), ("private", [], [])
        )
        self.assertEqual((segment.alert_on_changes, segment.paused), (False, False))
        self.assertEqual(
            (segment.last_members, segment.member_count, segment.last_evaluated_on),
            (None, None, None),
        )

    def test_default_rules_are_a_fresh_dict_each_time(self):
        self.assertIsNot(default_rules(), default_rules())

    def test_its_string_never_quotes_the_owners_words(self):
        segment = self.segment(name="Accounts Carl is about to lose")
        self.assertEqual(str(segment), f"Segment {segment.pk}")

    def test_one_change_per_record_per_day(self):
        segment = self.segment()
        SegmentChange.objects.create(
            segment=segment, record_id=1, change="entered", changed_on=self.today
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            SegmentChange.objects.create(
                segment=segment, record_id=1, change="left", changed_on=self.today
            )

    def test_deleting_the_owner_deletes_their_segments(self):
        segment = self.segment(owner=self.other)
        self.other.delete()
        self.assertFalse(Segment.objects.filter(pk=segment.pk).exists())

    def test_segment_changes_is_a_real_choice_on_the_kind_field(self):
        choices = dict(Notification._meta.get_field("kind").choices)
        self.assertIn("segment_changes", choices)
        self.assertEqual(Notification.Kind.SEGMENT_CHANGES, "segment_changes")
        Notification.objects.create(
            recipient=self.csm, kind=Notification.Kind.SEGMENT_CHANGES, message="x: 1 entered"
        )
