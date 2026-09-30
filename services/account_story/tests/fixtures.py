"""The organisation story's fixture (Carl's Pizza Hut with the unowned EMEA
and APAC accounts, one maker per record kind), read through the account
page's scope."""

from services.account_story.attention import build_account_attention
from services.account_story.scope import resolve_account_scope
from services.organizations.story.build import build_story
from services.organizations.story.params import parse_story_params
from services.organizations.tests.story_fixtures import StoryFixture


class AccountStoryFixture(StoryFixture):
    def account_scope(self, user=None, account=None):
        return resolve_account_scope(user or self.csm, (account or self.emea).pk)

    def account_story(self, user=None, account=None, **query):
        user = user or self.csm
        return build_story(
            user,
            self.account_scope(user, account),
            parse_story_params(query),
            today=self.today,
            attention=build_account_attention,
        )
