"""The organisation story's fixture (Carl's Pizza Hut with the unowned EMEA
and APAC accounts, one maker per record kind), read through the account
page's scope."""

from services.account_story.scope import resolve_account_scope
from services.organizations.tests.story_fixtures import StoryFixture


class AccountStoryFixture(StoryFixture):
    def account_scope(self, user=None, account=None):
        return resolve_account_scope(user or self.csm, (account or self.emea).pk)
