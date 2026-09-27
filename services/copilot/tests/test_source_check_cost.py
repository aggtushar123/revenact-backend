"""Reading a shared conversation checks every cited record's company and its
own rule — in a fixed number of queries, however many replies and sources the
conversation holds (`_Reader.visible_company_ids`, `_Reader.readable_ids`)."""

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from services.accounts.models import Organisation, User
from services.copilot.models import Conversation, Message
from services.copilot.views import REDACTED_REPLY, visible_messages
from services.customers.models import Activity, Customer, Email, Note, Ticket
from services.customers.tests.test_views import create_account
from services.knowledge.models import Contribution


class SourceCheckCost(TestCase):
    def setUp(self):
        org = Organisation.objects.create(name="Acme Inc")
        mk = lambda email: User.objects.create_user(  # noqa: E731
            email=email, password="x", name=email, organisation=org, role=User.Role.CSM
        )
        self.owner, self.reader = mk("owner@acme.io"), mk("reader@acme.io")
        self.org = org
        # Unowned, so the reader opens it; the reader's own division is open
        # to them, the owner's is not.
        self.customer = Customer.objects.create(organisation=org, name="Globex")
        self.seen = create_account(self.customer, name="Seen", owner=self.reader)
        self.hidden = create_account(self.customer, name="Hidden", owner=self.owner)
        self.conversation = Conversation.objects.create(
            organisation=org, user=self.owner, title="Globex"
        )
        self.n = 0

    def ref(self, kind, record, company):
        is_account = company.__class__.__name__ == "Account"
        return {
            "type": kind,
            "id": record.id,
            "company_type": "account" if is_account else "customer",
            "company_id": company.id,
        }

    def turns(self, count, *, readable):
        today, now = timezone.localdate(), timezone.now()
        for _ in range(count):
            self.n += 1
            parent = self.seen if readable else self.hidden
            email = Email.objects.create(account=parent, subject="s", body="b", sent_at=now)
            note = Note.objects.create(account=parent, title="t", body="b", logged_at=today)
            ticket = Ticket.objects.create(
                account=parent, ticket_number=f"T-{self.n}", title="t", opened_at=today
            )
            activity = Activity.objects.create(account=parent, type="other", occurred_at=today)
            contribution = Contribution.objects.create(
                organisation=self.org,
                customer=self.customer,
                author=self.reader,
                function="cs",
                body="b",
            )
            asked = Message.objects.create(
                conversation=self.conversation, role="user", content="q", author=self.owner
            )
            Message.objects.create(
                conversation=self.conversation,
                role="assistant",
                content=f"reply {self.n}",
                reply_to=asked,
                sources=[
                    self.ref("email", email, parent),
                    self.ref("note", note, parent),
                    self.ref("ticket", ticket, parent),
                    self.ref("activity", activity, parent),
                    self.ref("contribution", contribution, self.customer),
                ],
            )

    def read(self):
        # Fresh instances, as a real request loads them.
        reader = User.objects.get(pk=self.reader.pk)
        conversation = Conversation.objects.get(pk=self.conversation.pk)
        with CaptureQueriesContext(connection) as ctx:
            kept = visible_messages(conversation, reader)
        return len(ctx.captured_queries), [m.content for m in kept if m.role == "assistant"]

    def test_the_check_is_right(self):
        self.turns(1, readable=True)
        self.turns(1, readable=False)
        self.assertEqual(self.read()[1], ["reply 1", REDACTED_REPLY])

    def test_the_cost_does_not_grow_with_the_conversation(self):
        self.turns(1, readable=True)
        self.turns(1, readable=False)
        small, _ = self.read()
        self.turns(4, readable=True)
        self.turns(4, readable=False)
        large, _ = self.read()
        self.assertEqual(small, large)
        # The messages, the session, the chart and scope (3), the questions,
        # membership and role, then the source check: two company queries,
        # and existing + readable per kind with a rule (8, the contribution
        # rule reading the scope's function-mates once more). 82 before the
        # check was batched.
        self.assertEqual(large, 19)
