"""`scope=`: a saved segment's members as the book. It narrows the visible
book and never widens it; archive and churn are the segment's to decide."""

from services.customers.models import Customer
from services.organizations.book import load_portfolio
from services.organizations.params import parse_params
from services.organizations.tests.fixtures import PortfolioFixture


class SegmentScopeTests(PortfolioFixture):
    def names(self, scope, user=None, **query):
        portfolio = load_portfolio(
            user or self.csm, parse_params(query), today=self.today, scope=scope
        )
        return sorted(entry.customer.name for entry in portfolio.entries)

    def test_a_scope_narrows_the_book(self):
        alpha = self.customer("Alpha")
        self.customer("Beta")
        self.assertEqual(self.names(Customer.objects.filter(pk=alpha.pk)), ["Alpha"])

    def test_a_scope_never_widens_what_the_viewer_may_open(self):
        alpha = self.customer("Alpha")
        hidden = self.customer("Hidden", owner=self.other)
        theirs = self.customer("Theirs", organisation=self.other_org, owner=None)
        scope = Customer.objects.filter(pk__in=[alpha.pk, hidden.pk, theirs.pk])
        self.assertEqual(self.names(scope), ["Alpha"])

    def test_a_scope_keeps_archived_and_churned_rows_it_holds(self):
        self.customer("Old", is_archived=True)
        self.customer("Gone", churn_date=self.today)
        everyone = Customer.objects.filter(organisation=self.org)
        self.assertEqual(self.names(everyone), ["Gone", "Old"])
        self.assertEqual(self.names(None), [])

    def test_the_page_filters_still_narrow_a_scope(self):
        self.customer("Alpha")
        self.customer("Beta")
        everyone = Customer.objects.filter(organisation=self.org)
        self.assertEqual(self.names(everyone, search="alp"), ["Alpha"])
