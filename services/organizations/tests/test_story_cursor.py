import base64
import json
from datetime import UTC, datetime

from django.test import SimpleTestCase

from services.organizations.story.cursor import (
    Cut,
    decode_cursor,
    encode_cursor,
    fingerprint,
    is_after,
)
from services.organizations.story.params import StoryParams

AT = datetime(2026, 9, 20, 10, 30, 15, 123456, tzinfo=UTC)


def raw_token(data):
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode()


class CursorTests(SimpleTestCase):
    def test_a_cursor_round_trips(self):
        fp = fingerprint(StoryParams())
        token = encode_cursor(Cut(AT, "email", 7), fp)
        self.assertNotIn("=", token)
        self.assertEqual(decode_cursor(token, fp), Cut(AT, "email", 7))

    def test_a_cursor_is_bound_to_the_filters_it_was_cut_under(self):
        token = encode_cursor(Cut(AT, "email", 7), fingerprint(StoryParams(group="tickets")))
        self.assertIsNone(decode_cursor(token, fingerprint(StoryParams())))

    def test_the_fingerprint_ignores_only_cursor_and_limit(self):
        base = fingerprint(StoryParams())
        self.assertEqual(fingerprint(StoryParams(cursor="x", limit=5)), base)
        for changed in (
            StoryParams(group="tasks"),
            StoryParams(sources=("email",)),
            StoryParams(account=3),
            StoryParams(account="none"),
            StoryParams(q="sso"),
            StoryParams(thread="t-1"),
        ):
            self.assertNotEqual(fingerprint(changed), base, changed)

    def test_garbage_is_the_first_page(self):
        fp = fingerprint(StoryParams())
        truncated = encode_cursor(Cut(AT, "email", 7), fp)[:-3]
        for token in ("", "%%%", "bm90IGpzb24", truncated):
            self.assertIsNone(decode_cursor(token, fp), token)

    def test_a_well_formed_cursor_with_bad_fields_is_the_first_page(self):
        fp = fingerprint(StoryParams())
        good = {"at": AT.isoformat(), "k": "email", "id": 7, "f": fp}
        self.assertEqual(decode_cursor(raw_token(good), fp), Cut(AT, "email", 7))
        for bad in (
            {**good, "k": "slack"},
            {**good, "id": "7"},
            {**good, "id": True},
            {**good, "at": "2026-09-20T10:30:00"},
            {**good, "at": "yesterday"},
            ["not", "a", "dict"],
        ):
            self.assertIsNone(decode_cursor(raw_token(bad), fp), bad)

    def test_is_after_follows_newest_first_order(self):
        cut = Cut(AT, "email", 7)
        self.assertTrue(is_after((AT.replace(hour=9), "ticket", 99), cut))
        self.assertTrue(is_after((AT, "call", 99), cut))
        self.assertTrue(is_after((AT, "email", 6), cut))
        self.assertFalse(is_after((AT, "email", 7), cut))
        self.assertFalse(is_after((AT, "note", 1), cut))
        self.assertFalse(is_after((AT.replace(hour=11), "activity", 1), cut))
        self.assertTrue(is_after((AT, "zzz", 1), None))
