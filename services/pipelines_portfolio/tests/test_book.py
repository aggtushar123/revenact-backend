from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.http import QueryDict
from django.test import SimpleTestCase
from django.utils import timezone

from services.accounts.models import User
from services.customers.models import Customer, Opportunity
from services.customers.scoping import sees_everything
from services.customers.tests.test_views import blind_to_one_account
from services.pipelines_portfolio.book import filter_options, load_book, quarter_bounds
from services.pipelines_portfolio.kinds import OPPORTUNITIES, RISKS
from services.pipelines_portfolio.params import parse_params
from services.pipelines_portfolio.tests.fixtures import PipelineFixture

EVERY_STAGE = "stage=" + ",".join(OPPORTUNITIES.stages)


class QuarterTests(SimpleTestCase):
    def test_calendar_quarters(self):
        self.assertEqual(quarter_bounds(date(2026, 9, 30)), (date(2026, 7, 1), date(2026, 9, 30)))
        self.assertEqual(quarter_bounds(date(2026, 10, 1)), (date(2026, 10, 1), date(2026, 12, 31)))
        self.assertEqual(quarter_bounds(date(2026, 2, 14)), (date(2026, 1, 1), date(2026, 3, 31)))
        self.assertEqual(quarter_bounds(date(2026, 5, 31)), (date(2026, 4, 1), date(2026, 6, 30)))


class BookTests(PipelineFixture):
    def titles(self, user=None, query="", kind=OPPORTUNITIES, rows=True):
        params = parse_params(QueryDict(query), kind)
        book = load_book(user or self.csm, kind, params, today=self.today)
        return sorted(entry.item.title for entry in (book.rows if rows else book.entries))

    def test_the_viewer_reads_only_what_they_may_open(self):
        self.opportunity("Mine")
        self.opportunity("Taco's", customer=self.taco)
        self.opportunity("Globex's", customer=self.globex)
        self.assertEqual(self.titles(), ["Mine"])
        self.assertEqual(self.titles(self.admin), ["Mine", "Taco's"])

    def test_the_department_rule_applies(self):
        self.opportunity("Ours")
        self.opportunity("Everyone's", department="")
        self.opportunity("Sales'", department=User.Function.SALES)
        self.assertEqual(self.titles(), ["Everyone's", "Ours"])
        self.assertEqual(self.titles(self.admin), ["Everyone's", "Ours", "Sales'"])

    def test_blind_to_one_account(self):
        viewer, seen, hidden = blind_to_one_account(self.pizza)
        self.opportunity("On seen", account=seen)
        on_hidden = self.opportunity("On hidden", account=hidden)
        self.opportunity("On the organisation")
        self.assertEqual(self.titles(viewer), ["On seen", "On the organisation"])
        self.assertEqual(self.titles(viewer, f"ids={on_hidden.pk}"), [])

    def test_the_stage_filter_shapes_rows_not_the_summary_set(self):
        self.opportunity("Open")
        self.opportunity("Won", stage="closed_won")
        self.opportunity("Lost", stage="closed_lost")
        self.assertEqual(self.titles(), ["Open"])
        self.assertEqual(self.titles(rows=False), ["Lost", "Open", "Won"])
        self.assertEqual(self.titles(query="stage=closed_won,closed_lost"), ["Lost", "Won"])

    def test_an_organisation_filter_rolls_up_accounts_and_hides_what_it_cannot_open(self):
        emea = self.account("Pizza EMEA")
        self.opportunity("On Pizza Hut")
        self.opportunity("On EMEA", account=emea)
        self.opportunity("On Taco Bell", customer=self.taco)
        self.assertEqual(
            self.titles(query=f"organisation={self.pizza.pk}"), ["On EMEA", "On Pizza Hut"]
        )
        # Carl cannot open Taco Bell: naming it narrows to nothing, tiles included.
        self.assertEqual(self.titles(query=f"organisation={self.taco.pk}"), [])
        self.assertEqual(self.titles(query=f"organisation={self.taco.pk}", rows=False), [])
        self.assertEqual(self.titles(self.admin, f"organisation={self.taco.pk}"), ["On Taco Bell"])

    def test_an_organisation_filter_the_viewer_cannot_open_ignores_a_shared_account(self):
        # Carl opens Shared through Pizza Hut, but Shared is also Taco Bell's,
        # which Carl cannot open: naming Taco Bell still narrows to nothing.
        shared = self.account("Shared", customers=[self.pizza, self.taco], owner=self.other)
        self.opportunity("On Shared", account=shared)
        self.assertEqual(self.titles(), ["On Shared"])
        self.assertEqual(self.titles(query=f"organisation={self.taco.pk}"), [])
        self.assertEqual(self.titles(query=f"organisation={self.taco.pk}", rows=False), [])
        self.assertEqual(self.titles(query=f"organisation={self.pizza.pk}"), ["On Shared"])

    def test_an_account_filter_the_viewer_cannot_open_narrows_to_nothing(self):
        emea = self.account("Pizza EMEA")
        tacos = self.account("Taco EMEA", customers=[self.taco], owner=self.other)
        self.opportunity("On EMEA", account=emea)
        self.opportunity("On Taco EMEA", account=tacos)
        self.assertEqual(self.titles(query=f"account={emea.pk}"), ["On EMEA"])
        self.assertEqual(self.titles(query=f"account={tacos.pk}"), [])
        self.assertEqual(self.titles(query=f"account={emea.pk},{tacos.pk}"), ["On EMEA"])

    def test_owner_is_the_organisations_or_the_accounts(self):
        unowned = self.account("Unowned", owner=None)
        danas = self.account("Dana's division", owner=self.other)
        self.opportunity("On Pizza Hut")
        self.opportunity("On unowned", account=unowned)
        self.opportunity("On Dana's", account=danas)
        self.assertEqual(self.titles(query=f"owner={self.csm.pk}"), ["On Pizza Hut"])
        self.assertEqual(self.titles(query=f"owner={self.other.pk}"), ["On Dana's"])
        self.assertEqual(self.titles(query="owner=unassigned"), ["On unowned"])

    def test_search_reads_the_title_and_the_parent_name(self):
        emea = self.account("Pizza EMEA")
        self.opportunity("Upsell")
        self.opportunity("Seats", account=emea)
        self.assertEqual(self.titles(query="search=upS"), ["Upsell"])
        self.assertEqual(self.titles(query="search=emea"), ["Seats"])
        self.assertEqual(self.titles(query="search=pizza"), ["Seats", "Upsell"])

    def test_date_filters(self):
        self.opportunity("Next week", expected_close=self.days(7))
        self.opportunity("In two months", expected_close=self.days(60))
        self.opportunity("Late", expected_close=self.days(-3))
        self.opportunity("Won late", expected_close=self.days(-3), stage="closed_won")
        self.opportunity("Undated")
        self.assertEqual(self.titles(query=f"date=30&{EVERY_STAGE}"), ["Next week"])
        self.assertEqual(
            self.titles(query=f"date=90&{EVERY_STAGE}"), ["In two months", "Next week"]
        )
        # Overdue is open by definition: a won deal past its date is not late.
        self.assertEqual(self.titles(query=f"date=overdue&{EVERY_STAGE}"), ["Late"])
        self.assertEqual(self.titles(query=f"date=none&{EVERY_STAGE}"), ["Undated"])

    def test_risks_use_due_by(self):
        self.risk("Due soon", due_by=self.days(10))
        self.risk("Due later", due_by=self.days(100))
        self.assertEqual(self.titles(query="date=30", kind=RISKS), ["Due soon"])

    def test_changed_this_quarter(self):
        start, _end = quarter_bounds(self.today)
        self.opportunity("Won now", stage="closed_won")
        old = self.opportunity("Won long ago", stage="closed_won")
        before = timezone.make_aware(datetime.combine(start - timedelta(days=1), time(12)))
        Opportunity.objects.filter(pk=old.pk).update(stage_changed_at=before)
        self.assertEqual(self.titles(query="stage=closed_won&changed=quarter"), ["Won now"])

    def test_priority_and_department_filters(self):
        self.opportunity("High", priority="high")
        self.opportunity("Low", priority="low")
        self.opportunity("Everyone's", department="")
        self.assertEqual(self.titles(query="priority=high"), ["High"])
        self.assertEqual(self.titles(query="department=none"), ["Everyone's"])
        self.assertEqual(self.titles(query="department=cs"), ["High", "Low"])

    def test_ids_narrow_and_never_widen(self):
        mine = self.opportunity("Mine")
        theirs = self.opportunity("Theirs", customer=self.taco)
        self.assertEqual(self.titles(query=f"ids={mine.pk},{theirs.pk}"), ["Mine"])
        self.assertEqual(self.titles(query="ids="), [])


class EntryTests(PipelineFixture):
    def entry(self, item, kind=OPPORTUNITIES, user=None):
        params = parse_params(QueryDict(f"ids={item.pk}"), kind)
        [entry] = load_book(user or self.csm, kind, params, today=self.today).entries
        return entry

    def test_an_organisation_item(self):
        item = self.opportunity("Upsell", mrr=Decimal("2500.50"), expected_close=self.days(12))
        entry = self.entry(item)
        self.assertEqual(
            entry.parent, {"type": "organisation", "id": self.pizza.pk, "name": "Pizza Hut"}
        )
        self.assertEqual(entry.organisations, [(self.pizza.pk, "Pizza Hut")])
        self.assertEqual(
            (entry.mrr, entry.days, entry.is_open, entry.overdue, entry.signal),
            (2500.5, 12, True, False, None),
        )
        self.assertEqual(entry.owner, self.csm)

    def test_an_account_item_names_only_organisations_the_viewer_may_open(self):
        shared = self.account("Shared", customers=[self.pizza, self.taco], owner=self.other)
        item = self.opportunity("Seats", account=shared)
        entry = self.entry(item)
        self.assertEqual(entry.parent, {"type": "account", "id": shared.pk, "name": "Shared"})
        self.assertEqual(entry.organisations, [(self.pizza.pk, "Pizza Hut")])
        self.assertEqual(entry.owner, self.other)
        self.assertEqual(
            self.entry(item, user=self.admin).organisations,
            [(self.pizza.pk, "Pizza Hut"), (self.taco.pk, "Taco Bell")],
        )

    def test_overdue_comes_before_high_priority(self):
        late = self.entry(self.opportunity("Late", expected_close=self.days(-5), priority="high"))
        self.assertEqual(
            (late.days, late.overdue, late.signal),
            (-5, True, {"kind": "overdue", "label": "Overdue"}),
        )
        urgent = self.entry(self.opportunity("Urgent", priority="high"))
        self.assertEqual(urgent.signal, {"kind": "high_priority", "label": "High priority"})
        won = self.entry(
            self.opportunity(
                "Won", priority="high", expected_close=self.days(-5), stage="closed_won"
            )
        )
        self.assertEqual((won.is_open, won.overdue, won.signal), (False, False, None))

    def test_due_today_is_not_overdue(self):
        entry = self.entry(self.risk("Today", due_by=self.today), kind=RISKS)
        self.assertEqual((entry.days, entry.overdue), (0, False))

    def test_an_owner_from_another_tenant_is_not_named(self):
        stranger = User.objects.create_user(
            email="gus@globex.io",
            password="supersecret1",
            name="Gus Globex",
            organisation=self.other_org,
            role=User.Role.CSM,
        )
        odd = self.account("Odd import", owner=stranger)
        entry = self.entry(self.opportunity("On odd", account=odd))
        self.assertEqual((entry.owner.pk, entry.owner.name), (None, "Not in your book"))
        Customer.objects.filter(pk=self.pizza.pk).update(owner=stranger)
        entry = self.entry(self.opportunity("On Pizza Hut"), user=self.admin)
        self.assertEqual((entry.owner.pk, entry.owner.name), (None, "Not in your book"))


class FilterOptionTests(PipelineFixture):
    def test_options_are_scoped_as_the_rows_are(self):
        emea = self.account("Pizza EMEA")
        unowned = self.account("Unowned", owner=None)
        tacos = self.account("Taco EMEA", customers=[self.taco], owner=self.other)
        self.opportunity("On Pizza Hut", department="")
        self.opportunity("On EMEA", account=emea)
        self.opportunity("On unowned", account=unowned)
        self.opportunity("On Taco Bell", customer=self.taco)
        self.opportunity("On Taco EMEA", account=tacos)

        options = filter_options(self.csm, OPPORTUNITIES)

        self.assertEqual(
            options["organisations"], [{"value": str(self.pizza.pk), "name": "Pizza Hut"}]
        )
        self.assertEqual(
            options["accounts"],
            [
                {"value": str(emea.pk), "name": "Pizza EMEA"},
                {"value": str(unowned.pk), "name": "Unowned"},
            ],
        )
        self.assertEqual(
            options["owners"],
            [
                {"value": str(self.csm.pk), "name": "Carl CSM"},
                {"value": "unassigned", "name": "Unassigned"},
            ],
        )
        self.assertEqual(
            options["departments"],
            [
                {"value": "cs", "name": "Customer Success"},
                {"value": "none", "name": "No department"},
            ],
        )
        self.assertEqual([s["value"] for s in options["stages"]], list(OPPORTUNITIES.stages))
        self.assertEqual([p["value"] for p in options["priorities"]], ["high", "medium", "low"])

    def test_an_organisation_is_offered_through_its_accounts_items(self):
        emea = self.account("Pizza EMEA")
        self.opportunity("On EMEA", account=emea)
        names = [o["name"] for o in filter_options(self.csm, OPPORTUNITIES)["organisations"]]
        self.assertEqual(names, ["Pizza Hut"])

    def test_an_owner_from_another_tenant_is_never_offered(self):
        stranger = User.objects.create_user(
            email="gus@globex.io",
            password="supersecret1",
            name="Gus Globex",
            organisation=self.other_org,
            role=User.Role.CSM,
        )
        odd = self.account("Odd import", owner=stranger)
        self.opportunity("On odd", account=odd)
        owners = filter_options(self.admin, OPPORTUNITIES)["owners"]
        self.assertNotIn("Gus Globex", [owner["name"] for owner in owners])


class QueryCountTests(PipelineFixture):
    """The book and its options cost the same number of queries at any size.
    The viewer's own reads (organisation, membership, role) happen once per
    request whatever the book holds, so each count starts from a viewer who
    has made them."""

    def grow(self, n):
        for i in range(n):
            account = self.account(f"Division {i}", customers=[self.pizza, self.taco])
            self.opportunity(f"On division {i}", account=account)
            self.opportunity(f"On Pizza Hut {i}", priority="high")

    def viewer(self):
        viewer = User.objects.get(pk=self.admin.pk)
        viewer.organisation  # the per-request reads, made up front
        sees_everything(viewer)
        return viewer

    def load(self, viewer):
        params = parse_params(QueryDict(EVERY_STAGE), OPPORTUNITIES)
        return load_book(viewer, OPPORTUNITIES, params, today=self.today)

    def test_the_book_costs_two_queries_at_any_size(self):
        self.grow(1)
        viewer = self.viewer()
        with self.assertNumQueries(2):
            small = self.load(viewer)
        self.grow(5)
        viewer = self.viewer()
        with self.assertNumQueries(2):
            large = self.load(viewer)
        self.assertEqual((len(small.entries), len(large.entries)), (2, 12))

    def test_a_book_with_no_account_items_skips_the_linked_organisations(self):
        self.opportunity("On Pizza Hut")
        viewer = self.viewer()
        with self.assertNumQueries(1):
            self.load(viewer)

    def test_the_options_cost_three_queries_at_any_size(self):
        self.grow(1)
        viewer = self.viewer()
        with self.assertNumQueries(3):
            filter_options(viewer, OPPORTUNITIES)
        self.grow(5)
        viewer = self.viewer()
        with self.assertNumQueries(3):
            options = filter_options(viewer, OPPORTUNITIES)
        self.assertEqual(len(options["accounts"]), 6)
