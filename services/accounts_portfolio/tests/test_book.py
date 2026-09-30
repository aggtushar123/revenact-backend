from collections import Counter
from datetime import timedelta
from decimal import Decimal

from services.accounts.models import User
from services.accounts_portfolio import book
from services.accounts_portfolio.params import parse_params
from services.accounts_portfolio.tests.fixtures import AccountPortfolioFixture
from services.customers.models import Customer, Ticket


class FilteredQuerysetTests(AccountPortfolioFixture):
    def names(self, user=None, **query):
        queryset = book.filtered_queryset(user or self.csm, parse_params(query), today=self.today)
        # Instances, not `values_list`: a duplicate row must show as one.
        return sorted(account.name for account in queryset)

    def test_visibility_comes_first(self):
        self.account("Mine")
        self.account("Dana's under Pizza", owner=self.other)
        self.account("Pool under Taco", customers=[self.taco], owner=None)
        self.account("Dana's under Taco", customers=[self.taco], owner=self.other)
        self.account("Globex's", customers=[self.globex], owner=None)
        self.assertEqual(self.names(), ["Dana's under Pizza", "Mine", "Pool under Taco"])
        self.assertEqual(
            self.names(user=self.admin),
            ["Dana's under Pizza", "Dana's under Taco", "Mine", "Pool under Taco"],
        )

    def test_ids_only_narrow(self):
        mine = self.account("Mine")
        hidden = self.account("Hidden", customers=[self.taco], owner=self.other)
        self.assertEqual(self.names(ids=f"{mine.pk},{hidden.pk}"), ["Mine"])
        self.assertEqual(self.names(ids="x,"), [])

    def test_an_account_on_two_organisations_is_one_row(self):
        self.account("Shared", customers=[self.pizza, self.taco])
        self.assertEqual(self.names(user=self.admin), ["Shared"])

    def test_search_matches_name_or_id(self):
        east = self.account("Pizza East")
        self.account("Other")
        self.assertEqual(self.names(search="east"), ["Pizza East"])
        self.assertIn("Pizza East", self.names(search=str(east.pk)))

    def test_organisation_filter_reads_only_openable_organisations(self):
        self.account("Pizza one")
        self.account("Pool", customers=[self.taco], owner=None)
        self.assertEqual(self.names(organisation=str(self.pizza.pk)), ["Pizza one"])
        # Carl may open Pool but not Taco Bell: naming Taco Bell finds nothing,
        # so the filter cannot reveal what Taco Bell holds.
        self.assertEqual(self.names(organisation=str(self.taco.pk)), [])
        self.assertEqual(self.names(organisation=f"{self.pizza.pk},{self.taco.pk}"), ["Pizza one"])
        self.assertEqual(self.names(user=self.admin, organisation=str(self.taco.pk)), ["Pool"])

    def test_organisation_filter_on_an_account_spanning_open_and_hidden(self):
        # One account linked to an organisation Carl may open (Pizza Hut,
        # through the account's own owner having nothing to do with it) and
        # one he can't (owned by Dana, with none of Carl's accounts under
        # it): the openable one's id finds the account, the hidden one's
        # id finds nothing — it cannot reveal what it holds.
        hidden = Customer.objects.create(organisation=self.org, name="Hidden Org", owner=self.other)
        self.account("Cross", customers=[self.pizza, hidden], owner=self.other)
        self.assertEqual(self.names(organisation=str(self.pizza.pk)), ["Cross"])
        self.assertEqual(self.names(organisation=str(hidden.pk)), [])

    def test_organisation_filter_with_another_tenants_customer_id_finds_nothing(self):
        self.account("Pizza one")
        self.assertEqual(self.names(organisation=str(self.globex.pk)), [])

    def test_owner_filter(self):
        self.account("Carl's")
        self.account("Dana's", owner=self.other)
        self.account("Nobody's", owner=None)
        self.assertEqual(self.names(owner=str(self.other.pk)), ["Dana's"])
        self.assertEqual(self.names(owner="unassigned"), ["Nobody's"])

    def test_lifecycle_filter_churn_is_an_ordinary_stage(self):
        self.account("Live", lifecycle_stage="live")
        self.account("Renewal", lifecycle_stage="renewal")
        self.account("Churn stage", lifecycle_stage="churn")
        self.account("Onboarding")
        self.assertEqual(self.names(lifecycle="live,renewal"), ["Live", "Renewal"])
        self.assertEqual(self.names(lifecycle="churn"), ["Churn stage"])
        self.assertEqual(len(self.names()), 4)

    def test_health_bands_share_the_ring_thresholds(self):
        for name, score in (
            ("seven", "7.0"),
            ("six-nine", "6.9"),
            ("four", "4.0"),
            ("three-nine", "3.9"),
        ):
            self.account(name, health_score=Decimal(score))
        self.assertEqual(self.names(health="good"), ["seven"])
        self.assertEqual(self.names(health="average"), ["four", "six-nine"])
        self.assertEqual(self.names(health="poor,good"), ["seven", "three-nine"])

    def test_renews_within_includes_overdue_and_the_churn_stage(self):
        self.account("Overdue", renewal_date=self.today - timedelta(days=5))
        self.account("Ninety", renewal_date=self.today + timedelta(days=90))
        self.account("Ninety-one", renewal_date=self.today + timedelta(days=91))
        self.account(
            "Churn stage", lifecycle_stage="churn", renewal_date=self.today + timedelta(days=1)
        )
        self.account("Never")
        self.assertEqual(self.names(renews_within="90"), ["Churn stage", "Ninety", "Overdue"])

    def test_nps_bands_by_sign(self):
        self.account("Promoter", nps_score=50)
        self.account("Passive", nps_score=0)
        self.account("Detractor", nps_score=-10)
        self.account("Unscored")
        self.assertEqual(self.names(nps="promoter"), ["Promoter"])
        self.assertEqual(self.names(nps="passive"), ["Passive"])
        self.assertEqual(self.names(nps="detractor"), ["Detractor"])


class AccountRenewingQTests(AccountPortfolioFixture):
    def test_the_rule(self):
        self.account("Due", renewal_date=self.today + timedelta(days=30))
        self.account("Later", renewal_date=self.today + timedelta(days=31))
        queryset = book.filtered_queryset(self.csm, parse_params({}), today=self.today)
        names = [a.name for a in queryset.filter(book.account_renewing_q(30, today=self.today))]
        self.assertEqual(names, ["Due"])


class FilterOptionsTests(AccountPortfolioFixture):
    def test_options_are_scoped_like_the_rows(self):
        self.account("Mine", lifecycle_stage="live")
        self.account("Dana's under Pizza", owner=self.other, lifecycle_stage="renewal")
        self.account("Pool", customers=[self.taco], owner=None, lifecycle_stage="live")
        self.account("Hidden", customers=[self.taco], owner=self.other, lifecycle_stage="expansion")
        options = book.filter_options(self.csm)
        self.assertEqual(set(options), {"organisations", "owners", "lifecycles"})
        # Pool is Carl's to see, but Taco Bell is not his to open.
        self.assertEqual(
            options["organisations"], [{"value": str(self.pizza.pk), "name": "Pizza Hut"}]
        )
        self.assertEqual(
            options["owners"],
            [
                {"value": str(self.csm.pk), "name": "Carl CSM"},
                {"value": str(self.other.pk), "name": "Dana CSM"},
                {"value": "unassigned", "name": "Unassigned"},
            ],
        )
        self.assertEqual(
            options["lifecycles"],
            [{"value": "live", "name": "Live"}, {"value": "renewal", "name": "Renewal"}],
        )
        admin = book.filter_options(self.admin)
        self.assertEqual(
            [row["name"] for row in admin["organisations"]], ["Pizza Hut", "Taco Bell"]
        )

    def test_owners_are_only_from_the_viewers_own_organisation(self):
        outsider = User.objects.create_user(
            email="olga@globex.io",
            password="supersecret1",
            name="Olga",
            organisation=self.other_org,
            role=User.Role.CSM,
        )
        self.account("Bad import", owner=outsider)
        names = [row["name"] for row in book.filter_options(self.admin)["owners"]]
        self.assertNotIn("Olga", names)

    def test_a_hidden_accounts_owner_is_not_offered_as_a_filter(self):
        # Ghost owns only this one account, and it's under Taco Bell (Dana's,
        # not Carl's) with no account of Carl's under it -- so Carl can't
        # see the account, and Ghost must not appear as something to filter
        # by, as if Carl had something of Ghost's to find.
        ghost = User.objects.create_user(
            email="ghost@acme.io",
            password="supersecret1",
            name="Ghost",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.account("Hidden", customers=[self.taco], owner=ghost)
        names = [row["name"] for row in book.filter_options(self.csm)["owners"]]
        self.assertNotIn("Ghost", names)


class AccountUrgentTicketsTests(AccountPortfolioFixture):
    """The open High/Critical tickets filed on these accounts, under the
    department rule: what a row's signal counts and what an Ask reply's
    ticket snapshot fixes."""

    def ticket(self, number, **fields):
        values = {
            "ticket_number": number,
            "title": f"Ticket {number}",
            "priority": Ticket.Priority.HIGH,
            "opened_at": self.today,
            **fields,
        }
        return Ticket.objects.create(**values)

    def test_open_urgent_tickets_on_the_accounts_under_the_department_rule(self):
        emea = self.account("EMEA")
        urgent = self.ticket("T-1", account=emea, department="cs")
        critical = self.ticket("T-2", account=emea, priority=Ticket.Priority.CRITICAL)
        self.ticket("T-3", account=emea, priority=Ticket.Priority.MEDIUM)
        self.ticket("T-4", account=emea, status=Ticket.RESOLVED_STATUSES[0])
        engineering = self.ticket("T-5", account=emea, department="engineering")
        self.ticket("T-6", customer=self.pizza)  # the organisation's, not the account's

        carl = set(book.account_urgent_tickets(self.csm, [emea.pk]).values_list("pk", flat=True))
        alice = set(book.account_urgent_tickets(self.admin, [emea.pk]).values_list("pk", flat=True))

        self.assertEqual(carl, {urgent.pk, critical.pk})
        self.assertEqual(alice, {urgent.pk, critical.pk, engineering.pk})
        self.assertEqual(
            book.account_urgent_ticket_counts(self.csm, [emea.pk]), Counter({emea.pk: 2})
        )

    def test_no_ids_reads_nothing(self):
        self.assertFalse(book.account_urgent_tickets(self.csm, []).exists())
        self.assertEqual(book.account_urgent_ticket_counts(self.csm, []), Counter())
