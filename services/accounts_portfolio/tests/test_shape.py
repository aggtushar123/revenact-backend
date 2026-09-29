from datetime import timedelta
from decimal import Decimal

from services.accounts_portfolio import book, shape
from services.accounts_portfolio.params import parse_params
from services.accounts_portfolio.tests.fixtures import AccountPortfolioFixture


class ShapeFixture(AccountPortfolioFixture):
    """Four accounts under Pizza Hut, read by Alice (who sees every owner):

    | name    | owner | ARR    | health | renewal | stage      |
    | Alpha   | Carl  | 30,000 | 8.0    | +100    | live       |
    | Bravo   | Dana  | 20,000 | 5.0    | +10     | onboarding |
    | Charlie | —     | 10,000 | 3.0    | —       | renewal    |
    | Delta   | Carl  | 0      | 8.0    | −3      | live       |

    Triage: Alpha 8 (renews in 91–180 days), Bravo 40 (Average 22 + renews
    within 90 days 18), Charlie 66 (Poor), Delta 18 (overdue counts as urgent).
    """

    def setUp(self):
        super().setUp()
        self.account(
            "Alpha",
            arr=Decimal("30000"),
            renewal_date=self.today + timedelta(days=100),
            lifecycle_stage="live",
        )
        self.account(
            "Bravo",
            owner=self.other,
            arr=Decimal("20000"),
            health_score=Decimal("5.0"),
            renewal_date=self.today + timedelta(days=10),
            lifecycle_stage="onboarding",
        )
        self.account(
            "Charlie",
            owner=None,
            arr=Decimal("10000"),
            health_score=Decimal("3.0"),
            lifecycle_stage="renewal",
        )
        self.account(
            "Delta",
            arr=Decimal("0"),
            renewal_date=self.today - timedelta(days=3),
            lifecycle_stage="live",
        )

    def portfolio(self, **query):
        return book.load_portfolio(self.admin, parse_params(query), today=self.today)

    def names(self, entries):
        return [entry.account.name for entry in entries]


class OrderTests(ShapeFixture):
    def test_every_sort_both_ways_missing_values_last(self):
        expected = {
            "-arr": ["Alpha", "Bravo", "Charlie", "Delta"],
            "arr": ["Delta", "Charlie", "Bravo", "Alpha"],
            "-risk": ["Charlie", "Bravo", "Delta", "Alpha"],
            "risk": ["Alpha", "Delta", "Bravo", "Charlie"],
            "renewal": ["Delta", "Bravo", "Alpha", "Charlie"],
            "-renewal": ["Alpha", "Bravo", "Delta", "Charlie"],
            "health": ["Charlie", "Bravo", "Alpha", "Delta"],
            "-health": ["Alpha", "Delta", "Bravo", "Charlie"],
            "name": ["Alpha", "Bravo", "Charlie", "Delta"],
            "-name": ["Delta", "Charlie", "Bravo", "Alpha"],
        }
        portfolio = self.portfolio()
        for sort, names in expected.items():
            with self.subTest(sort=sort):
                params = parse_params({"sort": sort})
                ordered = shape.order_entries(portfolio.entries, params.sort_key, params.descending)
                self.assertEqual(self.names(ordered), names)


class GroupTests(ShapeFixture):
    def groups(self, group):
        entries, groups = shape.select(self.portfolio(), parse_params({"group": group}))
        return [(g["key"], g["label"], g["count"], g["arr"]) for g in groups], entries

    def test_health_sections_poor_first(self):
        groups, entries = self.groups("health")
        self.assertEqual(
            groups,
            [
                ("poor", "Poor", 1, 10000.0),
                ("average", "Average", 1, 20000.0),
                ("good", "Good", 2, 30000.0),
            ],
        )
        self.assertEqual(self.names(entries), ["Charlie", "Bravo", "Alpha", "Delta"])

    def test_owner_sections_by_name_unassigned_last(self):
        groups, _entries = self.groups("owner")
        self.assertEqual(
            groups,
            [
                (str(self.csm.pk), "Carl CSM", 2, 30000.0),
                (str(self.other.pk), "Dana CSM", 1, 20000.0),
                ("unassigned", "Unassigned", 1, 10000.0),
            ],
        )

    def test_lifecycle_sections_in_stage_order(self):
        groups, _entries = self.groups("lifecycle")
        self.assertEqual(
            groups,
            [
                ("onboarding", "Onboarding", 1, 20000.0),
                ("live", "Live", 2, 30000.0),
                ("renewal", "Renewal", 1, 10000.0),
            ],
        )

    def test_renewal_windows(self):
        groups, _entries = self.groups("renewal")
        self.assertEqual(
            groups,
            [
                ("overdue", "Overdue", 1, 0.0),
                ("30", "Within 30 days", 1, 20000.0),
                ("180", "91–180 days", 1, 30000.0),
                ("none", "No renewal date", 1, 10000.0),
            ],
        )

    def test_group_value_is_one_board_column_with_every_header(self):
        params = parse_params({"group": "lifecycle", "group_value": "live"})
        entries, groups = shape.select(self.portfolio(), params)
        self.assertEqual(self.names(entries), ["Alpha", "Delta"])
        self.assertEqual(len(groups), 3)
        params = parse_params({"group": "lifecycle", "group_value": "expansion"})
        self.assertEqual(shape.select(self.portfolio(), params)[0], [])


class PagingTests(ShapeFixture):
    def follow(self, **query):
        seen, cursor = [], ""
        for _ in range(10):
            params = parse_params({**query, "cursor": cursor})
            entries, _groups = shape.select(self.portfolio(**query), params)
            page, cursor = shape.paginate(entries, params=params)
            seen += self.names(page)
            if cursor is None:
                return seen
        self.fail(f"the cursor never ended: {seen}")

    def test_following_the_cursor_reads_every_row_once(self):
        self.assertEqual(self.follow(limit="1"), ["Alpha", "Bravo", "Charlie", "Delta"])
        self.assertEqual(
            self.follow(limit="1", sort="renewal"), ["Delta", "Bravo", "Alpha", "Charlie"]
        )

    def test_following_the_cursor_over_a_grouped_list(self):
        self.assertEqual(
            self.follow(limit="1", group="health"), ["Charlie", "Bravo", "Alpha", "Delta"]
        )

    def test_a_cursor_from_another_list_is_the_first_page(self):
        first = parse_params({"limit": "2"})
        entries, _groups = shape.select(self.portfolio(), first)
        _page, cursor = shape.paginate(entries, params=first)
        changed = parse_params({"limit": "2", "health": "good", "cursor": cursor})
        entries, _groups = shape.select(self.portfolio(health="good"), changed)
        page, _next = shape.paginate(entries, params=changed)
        self.assertEqual(self.names(page), ["Alpha", "Delta"])

    def test_the_fingerprint_reads_multi_value_filters_as_sets(self):
        self.assertEqual(
            shape.filter_fingerprint(parse_params({"health": "poor,good"})),
            shape.filter_fingerprint(parse_params({"health": "good,poor"})),
        )
        self.assertNotEqual(
            shape.filter_fingerprint(parse_params({"organisation": "1"})),
            shape.filter_fingerprint(parse_params({"organisation": "2"})),
        )
        self.assertEqual(
            shape.filter_fingerprint(parse_params({"cursor": "abc", "limit": "5"})),
            shape.filter_fingerprint(parse_params({})),
        )
