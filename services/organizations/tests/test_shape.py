from datetime import timedelta
from decimal import Decimal

from services.customers.models import Activity, Customer, Product
from services.organizations import book, shape
from services.organizations.params import parse_params
from services.organizations.tests.fixtures import PortfolioFixture


class ShapeFixture(PortfolioFixture):
    """Four customers, read by Alice (who sees every owner):

    | name    | owner | ARR (USD)   | health | renewal | last touch | product | stage      |
    | Alpha   | Carl  | 30,000      | 8.0    | +100    | 5 days     | Zeta    | live       |
    | Bravo   | Dana  | 20,000      | 5.0    | +10     | never      | Zeta    | onboarding |
    | Charlie | —     | 10,000      | 3.0    | —       | 20 days    | Acme    | renewal    |
    | Delta   | Carl  | GBP, no rate| 8.0    | −3      | 1 day      | —       | live       |

    Triage: Alpha 8 (renews 91-180d), Bravo 40 (Average 22 + renews ≤ 90d 18),
    Charlie 66 (Poor), Delta 18 (overdue counts as urgent).
    """

    def setUp(self):
        super().setUp()
        zeta = Product.objects.create(organisation=self.org, name="Zeta")
        acme = Product.objects.create(organisation=self.org, name="Acme")
        self.alpha = self.customer(
            "Alpha",
            arr_billed_at_account=Decimal("30000"),
            renewal_date=self.today + timedelta(days=100),
            primary_product=zeta,
            lifecycle_stage="live",
            total_hires=5,
        )
        self.bravo = self.customer(
            "Bravo",
            owner=self.other,
            arr_billed_at_account=Decimal("20000"),
            health_score=Decimal("5.0"),
            renewal_date=self.today + timedelta(days=10),
            primary_product=zeta,
            lifecycle_stage="onboarding",
        )
        self.charlie = self.customer(
            "Charlie",
            owner=None,
            arr_billed_at_account=Decimal("10000"),
            health_score=Decimal("3.0"),
            primary_product=acme,
            lifecycle_stage="renewal",
            total_hires=50,
        )
        self.delta = self.customer(
            "Delta",
            currency="GBP",
            arr_billed_at_account=Decimal("99999"),
            renewal_date=self.today - timedelta(days=3),
            lifecycle_stage="live",
            total_hires=7,
        )
        for customer, days in ((self.alpha, 5), (self.charlie, 20), (self.delta, 1)):
            Activity.objects.create(
                customer=customer,
                type=Activity.ActivityType.OTHER,
                occurred_at=self.today - timedelta(days=days),
            )

    def portfolio(self, **query):
        return book.load_portfolio(self.admin, parse_params(query), today=self.today)

    def names(self, entries):
        return [entry.customer.name for entry in entries]

    def ordered(self, sort):
        params = parse_params({"sort": sort})
        return self.names(shape.order_entries(self.portfolio(), params.sort_key, params.descending))


class OrderTests(ShapeFixture):
    def test_default_is_arr_descending_with_unconvertible_last(self):
        entries, groups = shape.select(self.portfolio(), parse_params({}))
        self.assertEqual(self.names(entries), ["Alpha", "Bravo", "Charlie", "Delta"])
        self.assertEqual(groups, [])

    def test_nulls_are_last_in_both_directions(self):
        self.assertEqual(self.ordered("arr"), ["Charlie", "Bravo", "Alpha", "Delta"])
        self.assertEqual(self.ordered("renewal"), ["Delta", "Bravo", "Alpha", "Charlie"])
        self.assertEqual(self.ordered("-renewal"), ["Alpha", "Bravo", "Delta", "Charlie"])

    def test_name(self):
        self.assertEqual(self.ordered("name"), ["Alpha", "Bravo", "Charlie", "Delta"])
        self.assertEqual(self.ordered("-name"), ["Delta", "Charlie", "Bravo", "Alpha"])

    def test_never_touched_is_the_longest_silence(self):
        self.assertEqual(self.ordered("-touch"), ["Bravo", "Charlie", "Alpha", "Delta"])
        self.assertEqual(self.ordered("touch"), ["Delta", "Alpha", "Charlie", "Bravo"])

    def test_risk(self):
        self.assertEqual(self.ordered("-risk"), ["Charlie", "Bravo", "Delta", "Alpha"])

    def test_ties_break_by_name_in_both_directions(self):
        self.assertEqual(self.ordered("health"), ["Charlie", "Bravo", "Alpha", "Delta"])
        self.assertEqual(self.ordered("-health"), ["Alpha", "Delta", "Bravo", "Charlie"])

    def test_numeric_field(self):
        self.assertEqual(self.ordered("-total_hires"), ["Charlie", "Delta", "Alpha", "Bravo"])

    def test_money_fields_sort_converted(self):
        Customer.objects.filter(pk=self.alpha.pk).update(arr_billed_at_hq=Decimal("100"))
        Customer.objects.filter(pk=self.delta.pk).update(arr_billed_at_hq=Decimal("5000"))
        # Delta's GBP has no rate, so it cannot be compared and goes last.
        self.assertEqual(self.ordered("-arr_billed_at_hq")[0], "Alpha")
        self.assertEqual(self.ordered("-arr_billed_at_hq")[-1], "Delta")


class GroupTests(ShapeFixture):
    def select(self, **query):
        return shape.select(self.portfolio(**query), parse_params(query))

    def test_health_sections_are_poor_average_good(self):
        entries, groups = self.select(group="health")
        self.assertEqual(self.names(entries), ["Charlie", "Bravo", "Alpha", "Delta"])
        self.assertEqual(
            groups,
            [
                {"key": "poor", "label": "Poor", "count": 1, "arr": 10000.0},
                {"key": "average", "label": "Average", "count": 1, "arr": 20000.0},
                {"key": "good", "label": "Good", "count": 2, "arr": 30000.0},
            ],
        )

    def test_owner_sections_name_the_unassigned_last(self):
        _entries, groups = self.select(group="owner")
        self.assertEqual(
            [(g["key"], g["label"], g["count"]) for g in groups],
            [
                (str(self.csm.pk), "Carl CSM", 2),
                (str(self.other.pk), "Dana CSM", 1),
                ("unassigned", "Unassigned", 1),
            ],
        )

    def test_lifecycle_sections_follow_the_stage_order(self):
        _entries, groups = self.select(group="lifecycle")
        self.assertEqual([g["key"] for g in groups], ["onboarding", "live", "renewal"])
        self.assertEqual(groups[1]["label"], "Live")

    def test_product_sections_put_no_product_last(self):
        _entries, groups = self.select(group="product")
        self.assertEqual([g["label"] for g in groups], ["Acme", "Zeta", "No product"])
        self.assertEqual(groups[-1]["key"], "none")

    def test_renewal_windows(self):
        _entries, groups = self.select(group="renewal")
        self.assertEqual([g["key"] for g in groups], ["overdue", "30", "180", "none"])
        self.assertEqual(
            [g["label"] for g in groups],
            ["Overdue", "Within 30 days", "91–180 days", "No renewal date"],
        )

    def test_group_value_narrows_rows_but_not_groups(self):
        entries, groups = self.select(group="health", group_value="good")
        self.assertEqual(self.names(entries), ["Alpha", "Delta"])
        self.assertEqual(sum(g["count"] for g in groups), 4)
        entries, _groups = self.select(group="health", group_value="purple")
        self.assertEqual(entries, [])


class CursorTests(ShapeFixture):
    def page(self, cursor="", **query):
        entries, _groups = shape.select(self.portfolio(**query), parse_params(query))
        return shape.paginate(entries, cursor=cursor, limit=2)

    def test_pages_follow_on_without_overlap(self):
        first, cursor = self.page(sort="name")
        self.assertEqual(self.names(first), ["Alpha", "Bravo"])
        self.assertIsNotNone(cursor)
        second, last = self.page(cursor, sort="name")
        self.assertEqual(self.names(second), ["Charlie", "Delta"])
        self.assertIsNone(last)

    def test_a_bad_cursor_is_the_first_page(self):
        for cursor in ("%%%", "bm90IGpzb24", shape.encode_cursor(0, -4)[:-2]):
            page, _next = self.page(cursor, sort="name")
            self.assertEqual(self.names(page), ["Alpha", "Bravo"])

    def test_when_the_cursor_row_leaves_the_set_nothing_is_skipped(self):
        _first, cursor = self.page(sort="name")
        Customer.objects.filter(pk=self.bravo.pk).update(is_archived=True)
        second, _last = self.page(cursor, sort="name")
        self.assertEqual(self.names(second), ["Charlie", "Delta"])

    def test_cursor_round_trip(self):
        self.assertEqual(shape.decode_cursor(shape.encode_cursor(7, 50)), (7, 50))
        self.assertIsNone(shape.decode_cursor(""))
