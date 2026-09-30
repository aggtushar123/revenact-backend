"""What the story engine needs from a scope, pinned so a scope other than the
organisation's (the account page's) can drive it: `accounts`, `cursor_key`,
`parent_q` and `account_ref`, plus the Needs attention builder it is given."""

import hashlib
import json
from dataclasses import dataclass

from django.db.models import Q

from services.organizations.story.build import build_story
from services.organizations.story.cursor import fingerprint
from services.organizations.story.params import StoryParams, parse_story_params

from .story_fixtures import StoryFixture


@dataclass(frozen=True)
class OneAccount:
    """The smallest scope the engine accepts: one account's rows."""

    account_id: int
    name: str

    @property
    def accounts(self):
        return {self.account_id: self.name}

    @property
    def cursor_key(self):
        return f"test:{self.account_id}"

    def parent_q(self, account=None):
        return Q(account_id=self.account_id)

    def account_ref(self, account_id):
        return None if account_id is None else {"id": account_id, "name": self.name}


def count_tickets(user, scope, bases, account, *, today):
    return {"tickets": bases["ticket"].count()}


class ScopeCursorKeyTests(StoryFixture):
    def test_the_organisation_scope_keys_its_cursors_by_the_organisation_id(self):
        self.assertEqual(self.scope().cursor_key, self.pizza.pk)

    def test_an_organisation_fingerprint_is_what_it_always_was(self):
        # Guards cursors already served: the key is the bare id, as before.
        state = [1, "", [], None, "", ""]
        expected = hashlib.sha256(json.dumps(state, separators=(",", ":")).encode()).hexdigest()[
            :16
        ]
        self.assertEqual(fingerprint(StoryParams(), 1), expected)

    def test_a_text_key_never_matches_the_organisation_id(self):
        self.assertNotEqual(fingerprint(StoryParams(), "account:1"), fingerprint(StoryParams(), 1))


class BuildStoryOverAnyScopeTests(StoryFixture):
    def story_of(self, scope, **query):
        return build_story(
            self.csm,
            scope,
            parse_story_params(query),
            today=self.today,
            attention=count_tickets,
        )

    def test_build_story_runs_over_any_scope_with_its_own_attention(self):
        self.note(self.pizza)
        self.note(self.apac)
        on_emea = self.note(self.emea)
        ticket = self.ticket(self.emea)
        body = self.story_of(OneAccount(self.emea.pk, "EMEA"))
        self.assertEqual(self.keys(body), [("ticket", ticket.pk), ("note", on_emea.pk)])
        self.assertEqual(body["items"][0]["account"], {"id": self.emea.pk, "name": "EMEA"})
        self.assertEqual(body["attention"], {"tickets": 1})
        self.assertEqual(body["counts"]["by_account"], {"all": 2, "none": 0, str(self.emea.pk): 2})

    def test_the_default_attention_is_still_the_organisation_s(self):
        self.assertEqual(
            set(self.story()["attention"]),
            {"renewal", "tickets", "overdue_tasks", "questions", "anomaly"},
        )

    def test_a_cursor_cut_under_another_scope_reads_the_first_page(self):
        for _ in range(3):
            self.note(self.emea)
        cursor = self.story(limit="1")["next_cursor"]
        self.assertIsNotNone(cursor)
        scope = OneAccount(self.emea.pk, "EMEA")
        first = self.story_of(scope, limit="1")
        moved = self.story_of(scope, limit="1", cursor=cursor)
        self.assertEqual(self.keys(moved), self.keys(first))
