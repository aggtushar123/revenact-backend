import calendar
from decimal import Decimal

from django.http import QueryDict

from services.accounts.models import User
from services.pipelines_portfolio.book import load_book
from services.pipelines_portfolio.kinds import OPPORTUNITIES
from services.pipelines_portfolio.params import parse_params
from services.pipelines_portfolio.shape import paginate, select
from services.pipelines_portfolio.tests.fixtures import PipelineFixture

EVERY_STAGE = "stage=" + ",".join(OPPORTUNITIES.stages)


class ShapeTests(PipelineFixture):
    def book(self, query=""):
        params = parse_params(QueryDict(query), OPPORTUNITIES)
        return load_book(self.csm, OPPORTUNITIES, params, today=self.today), params

    def listed(self, query=""):
        book, params = self.book(query)
        entries, groups = select(book, params)
        return [entry.item.title for entry in entries], groups

    def board(self):
        self.opportunity("N1", stage="negotiation", mrr=Decimal("100"))
        self.opportunity("N2", stage="negotiation", mrr=Decimal("50.25"))
        self.opportunity("D1", mrr=Decimal("10"))
        self.opportunity("Lost", stage="closed_lost", mrr=Decimal("5"))

    def stranger(self):
        return User.objects.create_user(
            email="gus@globex.io",
            password="supersecret1",
            name="Gus Globex",
            organisation=self.other_org,
            role=User.Role.CSM,
        )

    def test_default_sort_is_largest_mrr_first_ties_by_title(self):
        self.opportunity("B", mrr=Decimal("100"))
        self.opportunity("a", mrr=Decimal("100"))
        self.opportunity("Big", mrr=Decimal("900"))
        self.assertEqual(self.listed()[0], ["Big", "a", "B"])

    def test_missing_dates_sort_last_either_way(self):
        self.opportunity("Soon", expected_close=self.days(2))
        self.opportunity("Later", expected_close=self.days(40))
        self.opportunity("Undated")
        self.assertEqual(self.listed("sort=date")[0], ["Soon", "Later", "Undated"])
        self.assertEqual(self.listed("sort=-date")[0], ["Later", "Soon", "Undated"])

    def test_priority_stage_and_title_sorts(self):
        self.opportunity("Low", priority="low")
        self.opportunity("High", priority="high", stage="negotiation")
        self.opportunity("Medium", stage="qualification")
        self.assertEqual(self.listed("sort=-priority")[0], ["High", "Medium", "Low"])
        self.assertEqual(self.listed("sort=stage")[0], ["Low", "Medium", "High"])
        self.assertEqual(self.listed("sort=title")[0], ["High", "Low", "Medium"])

    def test_stage_groups_follow_the_board_and_carry_totals(self):
        self.board()
        titles, groups = self.listed(f"group=stage&{EVERY_STAGE}")
        self.assertEqual(
            groups,
            [
                {"key": "discovery", "label": "Discovery", "count": 1, "mrr": 10.0},
                {"key": "negotiation", "label": "Negotiation", "count": 2, "mrr": 150.25},
                {"key": "closed_lost", "label": "Closed Lost", "count": 1, "mrr": 5.0},
            ],
        )
        self.assertEqual(titles, ["D1", "N1", "N2", "Lost"])

    def test_group_value_serves_one_board_column_with_every_header(self):
        self.board()
        titles, groups = self.listed("group=stage&group_value=negotiation")
        self.assertEqual(titles, ["N1", "N2"])
        self.assertEqual([group["key"] for group in groups], ["discovery", "negotiation"])

    def test_close_month_sections(self):
        self.opportunity("Late", expected_close=self.days(-3))
        self.opportunity("Won in the past", expected_close=self.days(-3), stage="closed_won")
        self.opportunity("Undated")
        self.opportunity("Soon", expected_close=self.days(1))
        self.opportunity("Far", expected_close=self.days(70))
        _titles, groups = self.listed(f"group=month&{EVERY_STAGE}")
        months = sorted({self.days(n).strftime("%Y-%m") for n in (-3, 1, 70)})
        self.assertEqual([group["key"] for group in groups], ["overdue", *months, "none"])
        self.assertEqual((groups[0]["label"], groups[-1]["label"]), ("Overdue", "No date"))
        far = self.days(70)
        self.assertEqual(groups[-2]["label"], f"{calendar.month_name[far.month]} {far.year}")
        # A closed deal past its date is not overdue: it sits in its month.
        self.assertEqual(groups[0]["count"], 1)

    def test_owner_department_priority_and_parent_sections(self):
        unowned = self.account("Unowned", owner=None)
        self.opportunity("Carl's")
        self.opportunity("Nobody's", account=unowned, department="", priority="high")
        _t, owners = self.listed("group=owner")
        self.assertEqual([g["key"] for g in owners], [str(self.csm.pk), "unassigned"])
        _t, departments = self.listed("group=department")
        self.assertEqual(
            [(g["key"], g["label"]) for g in departments],
            [("cs", "Customer Success"), ("none", "No department")],
        )
        _t, priorities = self.listed("group=priority")
        self.assertEqual([g["key"] for g in priorities], ["high", "medium"])
        _t, parents = self.listed("group=parent")
        self.assertEqual(
            [(g["key"], g["label"]) for g in parents],
            [(f"organisation:{self.pizza.pk}", "Pizza Hut"), (f"account:{unowned.pk}", "Unowned")],
        )

    def test_an_owner_outside_the_tenant_groups_apart_from_unassigned(self):
        odd = self.account("Odd import", owner=self.stranger())
        unowned = self.account("Unowned", owner=None)
        self.opportunity("Carl's")
        self.opportunity("Odd", account=odd, mrr=Decimal("7"))
        self.opportunity("Nobody's", account=unowned)
        titles, owners = self.listed("group=owner")
        self.assertEqual(
            [(g["key"], g["label"], g["count"], g["mrr"]) for g in owners],
            [
                (str(self.csm.pk), "Carl CSM", 1, 1000.0),
                ("outside", "Not in your book", 1, 7.0),
                ("unassigned", "Unassigned", 1, 1000.0),
            ],
        )
        self.assertEqual(titles, ["Carl's", "Odd", "Nobody's"])
        self.assertEqual(self.listed("group=owner&group_value=outside")[0], ["Odd"])

    def test_following_the_cursor_reads_every_row_once(self):
        for i in range(7):
            self.opportunity(
                f"Deal {i}",
                mrr=Decimal(100 * (i % 3)),
                stage=OPPORTUNITIES.open_stages[i % 5],
                expected_close=None if i % 2 else self.days(i),
            )
        for query in ("", "sort=date", "group=stage&sort=-priority", "group=month&sort=title"):
            with self.subTest(query=query):
                book, params = self.book(f"{query}&limit=2")
                expected = [entry.item.pk for entry in select(book, params)[0]]
                seen, cursor = [], ""
                for _ in range(10):
                    params = parse_params(
                        QueryDict(f"{query}&limit=2&cursor={cursor}"), OPPORTUNITIES
                    )
                    page, cursor = paginate(
                        select(book, params)[0], params=params, kind=OPPORTUNITIES
                    )
                    seen += [entry.item.pk for entry in page]
                    if cursor is None:
                        break
                self.assertEqual(seen, expected)
                self.assertEqual(len(seen), 7)

    def test_a_cursor_from_other_filters_reads_the_first_page(self):
        for i in range(4):
            self.opportunity(f"Deal {i}", mrr=Decimal(i))
        book, params = self.book("limit=2")
        _page, cursor = paginate(select(book, params)[0], params=params, kind=OPPORTUNITIES)
        other = parse_params(QueryDict(f"limit=2&priority=medium&cursor={cursor}"), OPPORTUNITIES)
        page, _next = paginate(select(book, other)[0], params=other, kind=OPPORTUNITIES)
        self.assertEqual([entry.item.title for entry in page], ["Deal 3", "Deal 2"])

    def test_a_malformed_cursor_reads_the_first_page(self):
        for i in range(3):
            self.opportunity(f"Deal {i}", mrr=Decimal(i))
        for cursor in ("not-a-cursor", "eyJ4IjoxfQ", "%%%"):
            with self.subTest(cursor=cursor):
                params = parse_params(QueryDict(f"limit=2&cursor={cursor}"), OPPORTUNITIES)
                book, _params = self.book("limit=2")
                page, _next = paginate(select(book, params)[0], params=params, kind=OPPORTUNITIES)
                self.assertEqual([entry.item.title for entry in page], ["Deal 2", "Deal 1"])
