from django.http import QueryDict
from django.test import SimpleTestCase

from services.organizations.story.params import (
    DEFAULT_LIMIT,
    GROUP_KINDS,
    KINDS,
    MAX_LIMIT,
    NO_ACCOUNT,
    StoryParams,
    parse_story_params,
)


class ParseStoryParamsTests(SimpleTestCase):
    def test_nothing_given_is_the_defaults(self):
        params = parse_story_params({})
        self.assertEqual(params, StoryParams())
        self.assertEqual(params.limit, 30)
        self.assertEqual(params.selected_kinds, KINDS)
        self.assertEqual(params.page_kinds, KINDS)

    def test_the_kinds_are_the_nine_with_real_data(self):
        self.assertEqual(
            KINDS,
            (
                "activity",
                "calendar_event",
                "call",
                "email",
                "health",
                "note",
                "survey",
                "task",
                "ticket",
            ),
        )
        self.assertEqual(
            set(GROUP_KINDS), {"conversations", "tickets", "tasks", "feedback", "health"}
        )

    def test_group_narrows_the_kinds(self):
        self.assertEqual(
            parse_story_params({"group": "conversations"}).selected_kinds,
            ("activity", "calendar_event", "call", "email"),
        )
        self.assertEqual(parse_story_params({"group": "tasks"}).selected_kinds, ("note", "task"))
        self.assertEqual(parse_story_params({"group": "mood"}).group, "")

    def test_source_is_a_list_of_exact_kinds(self):
        params = parse_story_params(QueryDict("source=email,%20call,slack,,email"))
        self.assertEqual(params.sources, ("call", "email"))
        self.assertEqual(params.selected_kinds, ("call", "email"))
        self.assertEqual(parse_story_params({"source": "slack"}).sources, ())

    def test_group_and_source_intersect(self):
        params = parse_story_params({"group": "conversations", "source": "email,ticket"})
        self.assertEqual(params.selected_kinds, ("email",))
        params = parse_story_params({"group": "tickets", "source": "email"})
        self.assertEqual(params.selected_kinds, ())

    def test_account_is_an_id_or_none(self):
        self.assertEqual(parse_story_params({"account": "12"}).account, 12)
        self.assertEqual(parse_story_params({"account": "none"}).account, NO_ACCOUNT)
        self.assertIsNone(parse_story_params({"account": "emea"}).account)
        self.assertIsNone(parse_story_params({"account": "-3"}).account)
        self.assertIsNone(parse_story_params({"account": ""}).account)

    def test_q_is_trimmed_and_capped(self):
        self.assertEqual(parse_story_params({"q": "  sso "}).q, "sso")
        self.assertEqual(len(parse_story_params({"q": "x" * 500}).q), 200)

    def test_thread_reads_only_emails_and_leaves_the_counted_kinds_alone(self):
        params = parse_story_params({"thread": " abc "})
        self.assertEqual(params.thread, "abc")
        self.assertEqual(params.page_kinds, ("email",))
        self.assertEqual(params.selected_kinds, KINDS)
        params = parse_story_params({"thread": "abc", "group": "tickets"})
        self.assertEqual(params.page_kinds, ())

    def test_limit_defaults_and_is_capped(self):
        self.assertEqual(parse_story_params({"limit": "10"}).limit, 10)
        self.assertEqual(parse_story_params({"limit": "1000"}).limit, MAX_LIMIT)
        self.assertEqual(parse_story_params({"limit": "0"}).limit, DEFAULT_LIMIT)
        self.assertEqual(parse_story_params({"limit": "lots"}).limit, DEFAULT_LIMIT)
