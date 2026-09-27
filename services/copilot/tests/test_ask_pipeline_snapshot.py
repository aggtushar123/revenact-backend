"""A dashboard Ask reply's Revenue figures fold in open opportunities and
risks the asker could list — on accounts they may open, in departments they
may read. A shared reader who sees only a slice reads it only when they could
list every one of those too: the accounts and departments are fixed on the
reply when it is written (`Message.grounded_pipeline`), never re-derived, and
a reply that could carry pipeline with no snapshot fails closed."""

from decimal import Decimal

from services.copilot.models import Message
from services.copilot.views import REDACTED_REPLY
from services.customers.models import Account, Opportunity, Risk

from .test_ask_followups import AskFixture


def dashboard(area, view=None, **filters):
    return {
        "surface": "dashboard",
        "area": area,
        "view": view,
        "filters": {"owner": "", "lifecycle": "", "customer": "", **filters},
        "focus": None,
    }


class PipelineSnapshotTests(AskFixture):
    LEAK = "Pipeline is 12k."

    def setUp(self):
        super().setUp()
        # Carl's division of Pizza Hut: Priya may open the organisation (a
        # question routed to her) but not the division.
        self.division = Account.objects.create(name="Pizza EMEA", owner=self.carl)
        self.division.customers.add(self.pizza)

    def revenue(self, area="revenue", view="forecast"):
        return self.send(
            self.carl,
            "@Priya Nair what moves the forecast?",
            dashboard(area, view, customer=str(self.pizza.pk)),
            reply=self.LEAK,
        )

    def opportunity(self, **fields):
        return Opportunity.objects.create(
            title="Seats",
            mrr=Decimal("1000"),
            stage=Opportunity.Stage.NEGOTIATION,
            **fields,
        )

    def test_the_snapshot_names_the_accounts_and_departments_counted(self):
        self.opportunity(account=self.division, department="cs")
        Risk.objects.create(
            customer=self.pizza, title="Risk", mrr=Decimal("10"), department="sales"
        )
        self.revenue()
        snapshot = Message.objects.get(role="assistant").grounded_pipeline
        self.assertEqual(snapshot, {"account_ids": [self.division.pk], "departments": ["cs"]})

    def test_pipeline_on_an_account_the_reader_cannot_open_withholds_the_reply(self):
        self.opportunity(account=self.division)
        first = self.revenue()
        self.assertEqual(self.replies(self.priya, first), [REDACTED_REPLY])
        self.assertEqual(self.replies(self.alice, first), [self.LEAK])

    def test_the_overview_carries_the_same_figures(self):
        self.opportunity(account=self.division)
        first = self.revenue("overview", None)
        self.assertEqual(self.replies(self.priya, first), [REDACTED_REPLY])

    def test_another_departments_pipeline_withholds_the_reply(self):
        self.opportunity(customer=self.pizza, department="cs")
        first = self.revenue()
        self.assertEqual(self.replies(self.priya, first), [REDACTED_REPLY])

    def test_pipeline_the_reader_could_list_leaves_the_reply_readable(self):
        self.opportunity(customer=self.pizza)
        first = self.revenue()
        self.assertEqual(self.replies(self.priya, first), [self.LEAK])

    def test_a_followup_fed_the_reply_carries_its_snapshot(self):
        self.opportunity(account=self.division)
        first = self.revenue()
        self.send(self.carl, "@Priya Nair summarise", conversation=first, reply=self.LEAK)
        self.assertEqual(self.replies(self.priya, first), [REDACTED_REPLY, REDACTED_REPLY])
        followup = Message.objects.filter(role="assistant").order_by("-id").first()
        self.assertEqual(followup.grounded_pipeline["account_ids"], [self.division.pk])

    def test_a_legacy_revenue_reply_with_no_snapshot_fails_closed(self):
        first = self.revenue()
        Message.objects.filter(role="assistant").update(grounded_pipeline=None)
        self.assertEqual(self.replies(self.priya, first), [REDACTED_REPLY])

    def test_a_legacy_health_reply_with_no_snapshot_stays_readable(self):
        # Health's digest never carried pipeline, before or since.
        first = self.send(
            self.carl,
            "@Priya Nair who needs us?",
            dashboard("health", "triage", customer=str(self.pizza.pk)),
            reply=self.LEAK,
        )
        Message.objects.filter(role="assistant").update(grounded_pipeline=None)
        self.assertEqual(self.replies(self.priya, first), [self.LEAK])
