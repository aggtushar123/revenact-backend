"""`PortfolioOrder` over plain dicts: the shared order, sections and cursor
every portfolio list (Organizations, Accounts, Pipelines) runs on."""

from types import SimpleNamespace

from django.test import SimpleTestCase

from services.portfolio_core.shape import PortfolioOrder, name_rank

ORDER = PortfolioOrder(
    sort_value=lambda entry, sort_key: entry[sort_key],
    tiebreak=lambda entry: (entry["name"].casefold(), entry["id"]),
    group_key=lambda entry, group: (entry[group], entry[group].title()),
    section_rank=lambda key, label, group: name_rank(key, label),
    money=lambda entry: entry["amount"],
    money_field="amount",
)

ENTRIES = [
    {"id": 1, "name": "a", "value": 3.0, "team": "red", "amount": 10.0},
    {"id": 2, "name": "b", "value": None, "team": "none", "amount": None},
    {"id": 3, "name": "c", "value": 1.0, "team": "blue", "amount": 2.5},
    {"id": 4, "name": "d", "value": 2.0, "team": "red", "amount": 1.25},
]


def params(sort="value", *, descending=False, group="", group_value=None, cursor="", limit=50):
    return SimpleNamespace(
        sort_key=sort,
        descending=descending,
        group=group,
        group_value=group_value,
        cursor=cursor,
        limit=limit,
    )


def ids(entries):
    return [entry["id"] for entry in entries]


class PortfolioOrderTests(SimpleTestCase):
    def test_missing_values_sort_last_in_either_direction(self):
        self.assertEqual(ids(ORDER.order(ENTRIES, "value", False)), [3, 4, 1, 2])
        self.assertEqual(ids(ORDER.order(ENTRIES, "value", True)), [1, 4, 3, 2])

    def test_sections_by_name_empty_last_with_counts_and_unsummable_money_counted_only(self):
        entries, groups = ORDER.select(ENTRIES, params(group="team"))
        self.assertEqual(
            groups,
            [
                {"key": "blue", "label": "Blue", "count": 1, "amount": 2.5},
                {"key": "red", "label": "Red", "count": 2, "amount": 11.25},
                {"key": "none", "label": "None", "count": 1, "amount": 0.0},
            ],
        )
        self.assertEqual(ids(entries), [3, 4, 1, 2])

    def test_group_value_narrows_the_rows_but_not_the_sections(self):
        entries, groups = ORDER.select(ENTRIES, params(group="team", group_value="red"))
        self.assertEqual(ids(entries), [4, 1])
        self.assertEqual(len(groups), 3)

    def test_ungrouped_lists_have_no_sections(self):
        self.assertEqual(ORDER.select(ENTRIES, params())[1], [])

    def test_the_cursor_reads_every_row_once_and_only_for_its_own_fingerprint(self):
        for descending in (False, True):
            with self.subTest(descending=descending):
                ordered = ORDER.order(ENTRIES, "value", descending, "team")
                seen, cursor = [], ""
                for _ in range(10):
                    request = params(descending=descending, group="team", cursor=cursor, limit=1)
                    page, cursor = ORDER.paginate(ordered, request, fingerprint="f")
                    seen += ids(page)
                    if cursor is None:
                        break
                self.assertEqual(seen, ids(ordered))
        ordered = ORDER.order(ENTRIES, "value", False)
        _page, cursor = ORDER.paginate(ordered, params(limit=2), fingerprint="f")
        page, _next = ORDER.paginate(ordered, params(cursor=cursor, limit=2), fingerprint="g")
        self.assertEqual(ids(page), [3, 4])
