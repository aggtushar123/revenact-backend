"""`scope=` on the Accounts book: narrows, never widens."""

from services.accounts_portfolio.book import load_portfolio
from services.accounts_portfolio.params import parse_params
from services.accounts_portfolio.tests.fixtures import AccountPortfolioFixture
from services.customers.models import Account


class SegmentScopeTests(AccountPortfolioFixture):
    def names(self, scope, **query):
        portfolio = load_portfolio(self.csm, parse_params(query), today=self.today, scope=scope)
        return sorted(entry.account.name for entry in portfolio.entries)

    def test_a_scope_narrows_the_book(self):
        north = self.account("North")
        self.account("South")
        self.assertEqual(self.names(Account.objects.filter(pk=north.pk)), ["North"])

    def test_a_scope_never_widens_what_the_viewer_may_open(self):
        north = self.account("North")
        hidden = self.account("Hidden", customers=[self.taco], owner=self.other)
        foreign = self.account("Foreign", customers=[self.globex], owner=None)
        scope = Account.objects.filter(pk__in=[north.pk, hidden.pk, foreign.pk])
        self.assertEqual(self.names(scope), ["North"])

    def test_the_page_filters_still_narrow_a_scope(self):
        self.account("North")
        self.account("South")
        self.assertEqual(self.names(Account.objects.all(), search="sou"), ["South"])
