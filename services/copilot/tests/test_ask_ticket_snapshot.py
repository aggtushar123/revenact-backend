"""A dashboard Ask reply's ticket figures — the Overview's "Open tickets" and
its attention list's support items, the Support area, a support attention
focus — count the tickets the asker could read. A shared reader reads it
only when they could read every one too: the accounts and departments are
fixed on the reply (`Message.grounded_tickets`). The ticket department rule
exempts only Leadership (`visible_tickets`), so even a reader who may view
all accounts follows it; the account check is skipped for them, as for the
customer snapshot."""

from services.copilot.models import Message
from services.copilot.views import REDACTED_REPLY
from services.customers.models import Account, Ticket
from services.knowledge.models import Question

from .test_ask_followups import AskFixture
from .test_ask_pipeline_snapshot import dashboard


class TicketSnapshotTests(AskFixture):
    LEAK = "Three tickets are open."

    def setUp(self):
        super().setUp()
        from services.accounts.capabilities import Capability
        from services.accounts.models import Role, User

        # Carl's division of Pizza Hut: Priya may open the organisation but
        # not the division.
        self.division = Account.objects.create(name="Pizza EMEA", owner=self.carl)
        self.division.customers.add(self.pizza)
        mk = lambda email, name, function, **kw: User.objects.create_user(  # noqa: E731
            email=email, password="x", name=name, organisation=self.org, function=function, **kw
        )
        self.lead = mk("lee@acme.io", "Lee Lead", User.Function.LEADERSHIP, role=User.Role.CSM)
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.carl,
            assignee=self.lead,
            text="Pizza Hut?",
        )
        viewer = Role.objects.create(
            organisation=self.org,
            name="Viewer",
            slug="viewer",
            permissions=[Capability.VIEW_ALL_ACCOUNTS],
        )
        self.vic = mk("vic@acme.io", "Vic View", User.Function.ENGINEERING, role=viewer)
        self.n = 0

    def ticket(self, **fields):
        self.n += 1
        return Ticket.objects.create(
            ticket_number=f"T-{self.n}",
            title="Broken",
            opened_at="2026-09-01",
            priority=Ticket.Priority.HIGH,
            **fields,
        )

    def ask(self, area, view=None, focus=None):
        context = dashboard(area, view, customer=str(self.pizza.pk))
        context["focus"] = focus
        return self.send(
            self.carl,
            "@Priya Nair @Lee Lead @Vic View what is going on?",
            context,
            reply=self.LEAK,
        )

    def test_the_snapshot_names_the_accounts_and_departments_counted(self):
        self.ticket(account=self.division, department="cs")
        self.ticket(customer=self.pizza)
        self.ask("support", "tickets")
        snapshot = Message.objects.get(role="assistant").grounded_tickets
        self.assertEqual(snapshot, {"account_ids": [self.division.pk], "departments": ["", "cs"]})

    def test_a_ticket_on_an_account_the_reader_cannot_open_withholds_it(self):
        self.ticket(account=self.division)
        for area, view in (("overview", None), ("support", "tickets")):
            with self.subTest(area=area):
                first = self.ask(area, view)
                self.assertEqual(self.replies(self.priya, first), [REDACTED_REPLY])
                self.assertEqual(self.replies(self.lead, first), [REDACTED_REPLY])
                self.assertEqual(self.replies(self.vic, first), [self.LEAK])

    def test_another_departments_ticket_withholds_it_but_not_from_leadership(self):
        self.ticket(customer=self.pizza, department="cs")
        for area, view in (("overview", None), ("support", "tickets")):
            with self.subTest(area=area):
                first = self.ask(area, view)
                self.assertEqual(self.replies(self.priya, first), [REDACTED_REPLY])
                self.assertEqual(self.replies(self.lead, first), [self.LEAK])
                # View-all is not Leadership: the ticket rule still applies.
                self.assertEqual(self.replies(self.vic, first), [REDACTED_REPLY])

    def test_tickets_the_reader_could_read_leave_it_readable(self):
        self.ticket(customer=self.pizza)
        first = self.ask("support", "tickets")
        self.assertEqual(self.replies(self.priya, first), [self.LEAK])

    def test_a_support_attention_focus_carries_its_tickets(self):
        self.ticket(customer=self.pizza, department="cs")
        first = self.ask(
            "health", "triage", focus={"kind": "attention", "key": f"support:{self.pizza.pk}"}
        )
        self.assertEqual(self.replies(self.priya, first), [REDACTED_REPLY])
        self.assertEqual(self.replies(self.lead, first), [self.LEAK])

    def test_a_followup_fed_the_reply_carries_its_snapshot(self):
        self.ticket(customer=self.pizza, department="cs")
        first = self.ask("support", "tickets")
        self.send(self.carl, "@Priya Nair summarise", conversation=first, reply=self.LEAK)
        self.assertEqual(self.replies(self.priya, first), [REDACTED_REPLY, REDACTED_REPLY])
        followup = Message.objects.filter(role="assistant").order_by("-id").first()
        self.assertEqual(followup.grounded_tickets["departments"], ["cs"])

    def test_legacy_replies_that_could_count_tickets_fail_closed(self):
        self.ticket(customer=self.pizza)
        cases = (
            ("overview", None, None),
            ("support", "tickets", None),
            ("health", "triage", {"kind": "attention", "key": f"support:{self.pizza.pk}"}),
        )
        for area, view, focus in cases:
            with self.subTest(area=area):
                first = self.ask(area, view, focus)
                Message.objects.filter(role="assistant").update(grounded_tickets=None)
                self.assertEqual(self.replies(self.priya, first), [REDACTED_REPLY])

    def test_a_legacy_health_reply_with_no_snapshot_stays_readable(self):
        first = self.ask("health", "triage")
        Message.objects.filter(role="assistant").update(grounded_tickets=None)
        self.assertEqual(self.replies(self.priya, first), [self.LEAK])

    def test_an_organizations_reply_snapshots_its_rows_urgent_tickets(self):
        from .test_ask_followups import organizations

        self.ticket(customer=self.pizza, department="cs")
        first = self.send(
            self.carl,
            "@Priya Nair @Lee Lead which need us?",
            organizations(ids=str(self.pizza.pk)),
            reply=self.LEAK,
        )
        reply = Message.objects.get(role="assistant")
        self.assertEqual(reply.grounded_tickets, {"account_ids": [], "departments": ["cs"]})
        self.assertEqual(self.replies(self.priya, first), [REDACTED_REPLY])
        self.assertEqual(self.replies(self.lead, first), [self.LEAK])

    def test_a_legacy_organizations_reply_with_no_snapshot_fails_closed(self):
        from .test_ask_followups import organizations

        first = self.send(
            self.carl,
            "@Priya Nair which need us?",
            organizations(ids=str(self.pizza.pk)),
            reply=self.LEAK,
        )
        self.assertEqual(self.replies(self.priya, first), [self.LEAK])
        Message.objects.filter(role="assistant").update(grounded_tickets=None)
        self.assertEqual(self.replies(self.priya, first), [REDACTED_REPLY])
