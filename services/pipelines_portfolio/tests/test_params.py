from django.http import QueryDict
from django.test import SimpleTestCase

from services.pipelines_portfolio.kinds import KINDS, OPPORTUNITIES, RISKS
from services.pipelines_portfolio.params import parse_params

OPEN_DEALS = (
    "discovery",
    "qualification",
    "solution_validation",
    "proposal_price_review",
    "negotiation",
)


def parse(query="", kind=OPPORTUNITIES):
    return parse_params(QueryDict(query), kind)


class KindTests(SimpleTestCase):
    def test_the_two_kinds(self):
        self.assertEqual(set(KINDS), {"opportunities", "risks"})
        self.assertEqual((OPPORTUNITIES.item, RISKS.item), ("opportunity", "risk"))
        self.assertEqual((OPPORTUNITIES.date_field, RISKS.date_field), ("expected_close", "due_by"))
        self.assertEqual((OPPORTUNITIES.date_label, RISKS.date_label), ("Expected Close", "Due By"))
        self.assertEqual((OPPORTUNITIES.done_stage, RISKS.done_stage), ("closed_won", "mitigated"))
        self.assertEqual(OPPORTUNITIES.open_stages, OPEN_DEALS)
        self.assertEqual(RISKS.open_stages, ("open",))
        self.assertEqual(OPPORTUNITIES.stages[-2:], ("closed_won", "closed_lost"))
        self.assertEqual(RISKS.stages, ("open", "mitigated", "realised", "abandoned"))


class ParamsTests(SimpleTestCase):
    def test_defaults(self):
        params = parse()
        self.assertEqual(params.stages, OPEN_DEALS)
        self.assertEqual((params.sort, params.sort_key, params.descending), ("-mrr", "mrr", True))
        self.assertEqual(
            (params.group, params.group_value, params.limit, params.ids), ("", None, 50, None)
        )
        self.assertEqual(parse(kind=RISKS).stages, ("open",))

    def test_closed_stages_are_opt_in(self):
        self.assertEqual(
            parse("stage=closed_won,closed_lost").stages, ("closed_won", "closed_lost")
        )
        self.assertEqual(parse("stage=negotiation,bogus,negotiation").stages, ("negotiation",))
        self.assertEqual(parse("stage=bogus").stages, OPEN_DEALS)
        self.assertEqual(parse("stage=mitigated", kind=RISKS).stages, ("mitigated",))
        # Another kind's stage is not this kind's.
        self.assertEqual(parse("stage=mitigated").stages, OPEN_DEALS)

    def test_ids_reach_every_stage_and_empty_ids_name_nothing(self):
        params = parse("ids=3,x,4")
        self.assertEqual((params.ids, params.stages), ((3, 4), OPPORTUNITIES.stages))
        self.assertEqual(parse("ids=").ids, ())
        self.assertEqual(parse("ids=3&stage=negotiation").stages, ("negotiation",))

    def test_parents_owner_and_choices(self):
        params = parse(
            "organisation=1,x,2&account=5&owner=7"
            "&priority=high,urgent&department=sales,none,pirates"
        )
        self.assertEqual((params.organisations, params.accounts, params.owner), ((1, 2), (5,), 7))
        self.assertEqual(params.priorities, ("high",))
        self.assertEqual(params.departments, ("sales", "none"))
        self.assertEqual(parse("owner=unassigned").owner, "unassigned")
        self.assertIsNone(parse("owner=someone").owner)

    def test_date_and_changed(self):
        for value in ("30", "90", "180", "overdue", "none"):
            self.assertEqual(parse(f"date={value}").date, value)
        self.assertIsNone(parse("date=7").date)
        self.assertEqual(parse("changed=quarter").changed, "quarter")
        self.assertIsNone(parse("changed=year").changed)

    def test_sort_group_search_and_paging(self):
        for sort in ("mrr", "-date", "priority", "-stage", "title"):
            self.assertEqual(parse(f"sort={sort}").sort, sort)
        self.assertEqual(parse("sort=colour").sort, "-mrr")
        for group in ("stage", "month", "parent", "owner", "department", "priority"):
            self.assertEqual(parse(f"group={group}").group, group)
        self.assertEqual(parse("group=health").group, "")
        self.assertEqual(parse("group=stage&group_value=negotiation").group_value, "negotiation")
        self.assertIsNone(parse("group_value=negotiation").group_value)
        self.assertEqual(
            (parse("limit=500").limit, parse("limit=0").limit, parse("limit=x").limit),
            (100, 50, 50),
        )
        self.assertEqual(parse("search=%20pizza%20").search, "pizza")
