"""Who may see whose words — the org chart rule on knowledge and chat."""

from unittest.mock import patch

from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts import hierarchy
from services.accounts.models import Organisation, User
from services.copilot.models import Conversation, Message
from services.customers.models import Customer
from services.knowledge.models import Contribution, Question


class ChartFixture(APITestCase):
    """Alice (leadership) ← Carl (cs) ← Dana (cs); Alice ← Priya (eng), Raj (sales)."""

    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        mk = lambda email, name, function, role=User.Role.CSM, boss=None: User.objects.create_user(  # noqa: E731
            email=email,
            password="x",
            name=name,
            organisation=self.org,
            role=role,
            function=function,
            reports_to=boss,
        )
        self.alice = mk("alice@acme.io", "Alice Admin", "leadership", User.Role.ADMIN)
        self.carl = mk("carl@acme.io", "Carl CSM", "cs", boss=self.alice)
        self.dana = mk("dana@acme.io", "Dana CSM", "cs", boss=self.carl)
        self.priya = mk("priya@acme.io", "Priya Nair", "engineering", boss=self.alice)
        self.raj = mk("raj@acme.io", "Raj Mehta", "sales", boss=self.alice)
        self.pizza = Customer.objects.create(
            organisation=self.org, name="Pizza Hut", owner=self.carl
        )

    def note(self, author, body):
        return Contribution.objects.create(
            organisation=self.org,
            customer=self.pizza,
            author=author,
            function=author.function,
            body=body,
        )


class ScopeTests(ChartFixture):
    def test_scope_is_self_reports_team_and_leadership_above(self):
        self.assertEqual(
            hierarchy.scope_ids(self.alice),
            {self.alice.id, self.carl.id, self.dana.id, self.priya.id, self.raj.id},
        )
        self.assertEqual(
            hierarchy.scope_ids(self.dana), {self.dana.id, self.carl.id, self.alice.id}
        )
        self.assertEqual(hierarchy.scope_ids(self.priya), {self.priya.id, self.alice.id})
        self.assertEqual(
            [a.name for a in hierarchy.ancestors(self.dana)], ["Carl CSM", "Alice Admin"]
        )
        self.assertTrue(hierarchy.would_cycle(self.alice, self.dana))
        self.assertFalse(hierarchy.would_cycle(self.dana, self.priya))

    def test_the_chart_refuses_loops_and_outsiders(self):
        self.client.force_authenticate(self.alice)
        looped = self.client.patch(
            f"/api/v1/auth/users/{self.alice.id}/", {"reports_to_id": self.dana.id}, format="json"
        )
        self.assertEqual(looped.status_code, status.HTTP_400_BAD_REQUEST)
        moved = self.client.patch(
            f"/api/v1/auth/users/{self.dana.id}/", {"reports_to_id": self.priya.id}, format="json"
        )
        self.assertEqual(moved.status_code, status.HTTP_200_OK)
        self.assertEqual(moved.data["reports_to"], {"id": self.priya.id, "name": "Priya Nair"})


class KnowledgeScopeTests(ChartFixture):
    def test_contributions_follow_the_chart(self):
        self.note(self.priya, "eng note")
        self.note(self.raj, "sales note")
        self.note(self.alice, "leadership note")
        self.note(self.dana, "dana note")
        url = f"/api/v1/customers/{self.pizza.id}/contributions/"

        def seen_by(user):
            self.client.force_authenticate(user)
            return sorted(c["body"] for c in self.client.get(url).data)

        self.assertEqual(
            seen_by(self.alice), ["dana note", "eng note", "leadership note", "sales note"]
        )
        self.assertEqual(seen_by(self.priya), ["eng note", "leadership note"])
        # Carl owns Pizza Hut: responsibility grants reading on it, so he sees all.
        self.assertEqual(
            seen_by(self.carl), ["dana note", "eng note", "leadership note", "sales note"]
        )
        self.assertEqual(seen_by(self.dana), ["dana note", "leadership note"])

    def test_an_answer_reaches_the_asker_across_branches(self):
        from services.knowledge import mentions

        q = Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.raj,
            assignee=self.priya,
            text="ETA?",
        )
        mentions.answer_question(q, self.priya, "25 Sep.")
        self.client.force_authenticate(self.raj)
        bodies = [
            c["body"]
            for c in self.client.get(f"/api/v1/customers/{self.pizza.id}/contributions/").data
        ]
        self.assertTrue(any("25 Sep." in b for b in bodies))
        # Dana is on another branch and another team: not hers to read.
        self.client.force_authenticate(self.dana)
        self.assertEqual(
            self.client.get(f"/api/v1/customers/{self.pizza.id}/contributions/").data, []
        )
        self.assertEqual([r["id"] for r in self.client.get("/api/v1/questions/").data], [])
        # Priya was asked: the question is hers even though Raj is outside her scope.
        self.client.force_authenticate(self.priya)
        self.assertEqual([r["id"] for r in self.client.get("/api/v1/questions/").data], [q.id])

    def test_responsibility_grants_reading_on_that_customer_only(self):
        from services.knowledge.models import FunctionOwner

        other = Customer.objects.create(organisation=self.org, name="Uber", owner=self.carl)
        self.note(self.raj, "sales note on pizza hut")
        Contribution.objects.create(
            organisation=self.org,
            customer=other,
            author=self.raj,
            function="sales",
            body="sales note on uber",
        )
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.raj,
            assignee=self.carl,
            text="pizza q",
        )
        FunctionOwner.objects.create(customer=self.pizza, function="engineering", user=self.priya)

        self.client.force_authenticate(self.priya)
        pizza = [
            c["body"]
            for c in self.client.get(f"/api/v1/customers/{self.pizza.id}/contributions/").data
        ]
        self.assertEqual(pizza, ["sales note on pizza hut"])
        uber = self.client.get(f"/api/v1/customers/{other.id}/contributions/").data
        self.assertEqual(uber, [])
        self.assertEqual(
            [q["text"] for q in self.client.get("/api/v1/questions/").data], ["pizza q"]
        )
        # Dana is responsible for nothing here and stays outside.
        self.client.force_authenticate(self.dana)
        self.assertEqual(
            self.client.get(f"/api/v1/customers/{self.pizza.id}/contributions/").data, []
        )

    def test_the_copilot_grounds_only_in_what_the_asker_may_see(self):
        from services.copilot.context import build_grounding

        self.note(self.raj, "SALES-ONLY procurement stalled")
        self.note(self.alice, "LEADERSHIP board wants this kept")
        summary = build_grounding(self.org, self.priya, query="What about Pizza Hut?").summary
        self.assertIn("LEADERSHIP board", summary)
        self.assertNotIn("SALES-ONLY", summary)


class ChatSliceTests(ChartFixture):
    def test_a_mentioned_person_sees_only_their_slice_of_the_conversation(self):
        from services.copilot.views import visible_messages

        conversation = Conversation.objects.create(
            organisation=self.org, user=self.alice, title="Pizza Hut"
        )

        def turn(author, text):
            m = Message.objects.create(
                conversation=conversation, role="user", content=text, author=author
            )
            Message.objects.create(
                conversation=conversation, role="assistant", content=f"re: {text}"
            )
            return m

        turn(self.alice, "Alice opens")
        turn(self.raj, "Raj adds sales detail")
        asked = turn(self.alice, "@Priya Nair can you confirm the fix date?")
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.alice,
            assignee=self.priya,
            text=asked.content,
            message=asked,
        )
        turn(self.raj, "Raj follows up")

        kept = [m.content for m in visible_messages(conversation, self.priya)]
        self.assertEqual(
            kept,
            [
                "Alice opens",
                "re: Alice opens",
                "@Priya Nair can you confirm the fix date?",
                "re: @Priya Nair can you confirm the fix date?",
            ],
        )
        self.assertEqual(
            [m.content for m in visible_messages(conversation, self.alice)][::2][:4],
            [
                "Alice opens",
                "Raj adds sales detail",
                "@Priya Nair can you confirm the fix date?",
                "Raj follows up",
            ],
        )

        self.client.force_authenticate(self.priya)
        listed = self.client.get("/api/v1/copilot/conversations/").data
        self.assertEqual([c["id"] for c in listed], [conversation.id])
        detail = self.client.get(f"/api/v1/copilot/conversations/{conversation.id}/").data
        self.assertEqual(detail["visibility"], "partial")
        self.assertEqual(len(detail["messages"]), 4)
        self.assertEqual(detail["messages"][0]["author"]["name"], "Alice Admin")

        # Dana was never mentioned: nothing.
        self.client.force_authenticate(self.dana)
        self.assertEqual(
            self.client.get(f"/api/v1/copilot/conversations/{conversation.id}/").status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_a_turn_addressed_to_someone_else_is_not_shown_however_senior_its_author(self):
        from services.copilot.views import visible_messages

        conversation = Conversation.objects.create(
            organisation=self.org, user=self.alice, title="Pizza Hut"
        )
        general = Message.objects.create(
            conversation=conversation, role="user", content="Alice: general", author=self.alice
        )
        to_priya = Message.objects.create(
            conversation=conversation,
            role="user",
            content="@Priya Nair fix date?",
            author=self.alice,
        )
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.alice,
            assignee=self.priya,
            text=to_priya.content,
            message=to_priya,
        )
        to_raj = Message.objects.create(
            conversation=conversation,
            role="user",
            content="@Raj Mehta procurement?",
            author=self.alice,
        )
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.alice,
            assignee=self.raj,
            text=to_raj.content,
            message=to_raj,
        )
        Message.objects.create(conversation=conversation, role="assistant", content="re: raj")
        both = Message.objects.create(
            conversation=conversation,
            role="user",
            content="@Priya Nair @Raj Mehta agree?",
            author=self.alice,
        )
        for who in (self.priya, self.raj):
            Question.objects.create(
                organisation=self.org,
                customer=self.pizza,
                asked_by=self.alice,
                assignee=who,
                text=both.content,
                message=both,
            )

        self.assertEqual(
            [m.content for m in visible_messages(conversation, self.priya)],
            ["Alice: general", "@Priya Nair fix date?", "@Priya Nair @Raj Mehta agree?"],
        )
        self.assertEqual(
            [m.content for m in visible_messages(conversation, self.raj)],
            [
                "Alice: general",
                "@Raj Mehta procurement?",
                "re: raj",
                "@Priya Nair @Raj Mehta agree?",
            ],
        )
        self.assertEqual(len(visible_messages(conversation, self.alice)), 5)
        self.assertEqual(general.author, self.alice)

    def test_a_follow_up_from_the_mentioned_person_uses_only_their_slice_as_history(self):
        conversation = Conversation.objects.create(
            organisation=self.org, user=self.alice, title="Pizza Hut"
        )
        Message.objects.create(
            conversation=conversation, role="user", content="SECRET sales turn", author=self.raj
        )
        Message.objects.create(conversation=conversation, role="assistant", content="re: secret")
        asked = Message.objects.create(
            conversation=conversation,
            role="user",
            content="@Priya Nair fix date?",
            author=self.alice,
        )
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.alice,
            assignee=self.priya,
            text=asked.content,
            message=asked,
        )

        self.client.force_authenticate(self.priya)
        with patch("services.copilot.views.get_completion", return_value="25 Sep.") as call:
            response = self.client.post(
                "/api/v1/copilot/messages/",
                {"conversation_id": conversation.id, "content": "It ships 25 Sep."},
                format="json",
            )

        history = [m["content"] for m in call.call_args.kwargs["messages"]]
        self.assertNotIn("SECRET sales turn", history)
        self.assertIn("@Priya Nair fix date?", history)
        self.assertEqual(response.data["visibility"], "partial")
        self.assertEqual(Message.objects.get(content="It ships 25 Sep.").author, self.priya)


class CustomerPageTests(ChartFixture):
    def test_a_responsible_or_asked_person_can_open_the_customer_page(self):
        from services.knowledge.models import FunctionOwner

        url = f"/api/v1/customers/{self.pizza.id}/"
        # Priya owns nothing and is not a CSM: without a reason, no page.
        self.client.force_authenticate(self.priya)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)
        # Asked about it: the notification's link must open.
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.alice,
            assignee=self.priya,
            text="?",
        )
        self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK)
        # Responsible for it, likewise.
        self.client.force_authenticate(self.raj)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)
        FunctionOwner.objects.create(customer=self.pizza, function="sales", user=self.raj)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK)


class ReplyRedactionTests(ChartFixture):
    def test_a_reply_that_quotes_records_outside_the_viewers_scope_is_withheld(self):
        from services.copilot.views import REDACTED_REPLY, visible_messages

        sales_note = self.note(self.raj, "SALES-ONLY procurement stalled")
        conversation = Conversation.objects.create(
            organisation=self.org, user=self.alice, title="Pizza Hut"
        )
        asked = Message.objects.create(
            conversation=conversation,
            role="user",
            content="@Priya Nair what is going on?",
            author=self.alice,
        )
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.alice,
            assignee=self.priya,
            text=asked.content,
            message=asked,
        )
        Message.objects.create(
            conversation=conversation,
            role="assistant",
            content="Raj says procurement stalled.",
            sources=[
                {
                    "type": "contribution",
                    "id": sales_note.id,
                    "label": "Sales · Raj Mehta",
                    "date": "2026-09-13",
                    "company": "Pizza Hut",
                    "company_type": "customer",
                    "company_id": self.pizza.id,
                }
            ],
        )

        # Priya is mentioned but not responsible: the reply quotes a note she may not read.
        kept = visible_messages(conversation, self.priya)
        self.assertEqual([m.content for m in kept], [asked.content, REDACTED_REPLY])
        self.assertEqual(
            Message.objects.get(role="assistant").content, "Raj says procurement stalled."
        )

        # Once responsible for Pizza Hut she may read the note, so the reply is hers too.
        from services.knowledge.models import FunctionOwner

        FunctionOwner.objects.create(customer=self.pizza, function="engineering", user=self.priya)
        kept = visible_messages(conversation, self.priya)
        self.assertEqual(kept[1].content, "Raj says procurement stalled.")

        # The owner always sees her own conversation whole.
        self.assertEqual(
            visible_messages(conversation, self.alice)[1].content, "Raj says procurement stalled."
        )


class DashboardReplyRedactionTests(ChartFixture):
    """A dashboard-sourced reply carries aggregates and company names from
    the asker's whole filtered book, not just the records it cites — Fix
    round 1 on the dashboard Ask Revenact backend (task-8-report.md):
    `_reply_readable_by` now also requires the asker's whole filtered book
    to be inside the viewer's own visible customers."""

    def setUp(self):
        super().setUp()
        # Owned by Carl, same as Pizza Hut, but Priya has no reason (no
        # question, no contribution, no FunctionOwner) to see this one.
        self.secret = Customer.objects.create(
            organisation=self.org, name="Secret Corp", owner=self.carl
        )
        self.conversation = Conversation.objects.create(
            organisation=self.org, user=self.carl, title="Ask Revenact"
        )
        dashboard_context = {
            "surface": "dashboard",
            "area": "overview",
            "view": None,
            "filters": {"owner": "", "lifecycle": "", "customer": ""},
            "focus": None,
        }
        self.asked = Message.objects.create(
            conversation=self.conversation,
            role="user",
            content="@Priya Nair why is at-risk ARR up?",
            author=self.carl,
            context=dashboard_context,
        )
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.carl,
            assignee=self.priya,
            text=self.asked.content,
            message=self.asked,
        )
        self.reply = Message.objects.create(
            conversation=self.conversation,
            role="assistant",
            content="At-risk ARR is up because of renewals across the book.",
            sources=[],
        )

    def test_a_partial_visibility_viewer_cannot_see_the_askers_whole_book(self):
        from services.copilot.views import REDACTED_REPLY, visible_messages

        # Priya is mentioned (so she reads the question) and is even an
        # assignee on Pizza Hut (so she could see that one customer), but
        # Secret Corp — also in Carl's whole-book digest — is not hers.
        kept = visible_messages(self.conversation, self.priya)
        self.assertEqual([m.content for m in kept], [self.asked.content, REDACTED_REPLY])
        self.assertEqual(Message.objects.get(role="assistant").content, self.reply.content)

    def test_a_full_visibility_session_participant_sees_it(self):
        from services.copilot.models import CopilotSession, SessionInvite, SessionParticipant
        from services.copilot.views import visible_messages

        session = CopilotSession.objects.create(
            conversation=self.conversation, status=CopilotSession.Status.LIVE
        )
        SessionInvite.objects.create(
            session=session,
            invited_user=self.dana,
            invited_by=self.carl,
            status=SessionInvite.Status.ACCEPTED,
        )
        SessionParticipant.objects.create(session=session, user=self.dana)

        kept = [m.content for m in visible_messages(self.conversation, self.dana)]
        self.assertEqual(kept, [self.asked.content, self.reply.content])

    def test_the_asker_always_sees_their_own_reply(self):
        from services.copilot.views import visible_messages

        # Carl owns the conversation, so this also exercises the plain
        # owner path — sees_whole_conversation is True either way.
        kept = [m.content for m in visible_messages(self.conversation, self.carl)]
        self.assertEqual(kept, [self.asked.content, self.reply.content])

    def test_a_communications_reply_with_no_context_is_unaffected(self):
        from services.copilot.views import visible_messages

        # A plain follow-up from Priya, within her own mentioned slice:
        # no `context`, so no book to check — exactly today's behaviour.
        Message.objects.create(
            conversation=self.conversation,
            role="user",
            content="Thanks, got it.",
            author=self.priya,
        )
        plain_reply = Message.objects.create(
            conversation=self.conversation,
            role="assistant",
            content="You're welcome.",
            sources=[],
        )
        kept = [m.content for m in visible_messages(self.conversation, self.priya)]
        self.assertIn(plain_reply.content, kept)


class ManagerSeesTeamTests(ChartFixture):
    """Carl manages Dana. The CEO asks Dana something in a chat; Carl sees
    the ask and the reply. Raj, on another branch, sees nothing."""

    def test_a_manager_sees_what_was_asked_of_a_report_and_what_they_replied(self):
        from services.copilot.views import visible_messages

        conversation = Conversation.objects.create(
            organisation=self.org, user=self.alice, title="Pizza Hut"
        )
        Message.objects.create(
            conversation=conversation, role="user", content="Alice: general", author=self.alice
        )
        to_raj = Message.objects.create(
            conversation=conversation,
            role="user",
            content="@Raj Mehta procurement?",
            author=self.alice,
        )
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.alice,
            assignee=self.raj,
            text=to_raj.content,
            message=to_raj,
        )
        to_dana = Message.objects.create(
            conversation=conversation,
            role="user",
            content="@Dana CSM when is the QBR?",
            author=self.alice,
        )
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.alice,
            assignee=self.dana,
            text=to_dana.content,
            message=to_dana,
        )
        Message.objects.create(conversation=conversation, role="assistant", content="re: dana")
        Message.objects.create(
            conversation=conversation,
            role="user",
            content="Dana: QBR is on the 20th.",
            author=self.dana,
        )
        Message.objects.create(
            conversation=conversation, role="assistant", content="re: dana reply"
        )

        self.client.force_authenticate(self.carl)
        self.assertEqual(
            [c["id"] for c in self.client.get("/api/v1/copilot/conversations/").data],
            [conversation.id],
        )
        self.assertEqual(
            [m.content for m in visible_messages(conversation, self.carl)],
            [
                "Alice: general",
                "@Dana CSM when is the QBR?",
                "re: dana",
                "Dana: QBR is on the 20th.",
                "re: dana reply",
            ],
        )
        self.assertEqual(
            [q["text"] for q in self.client.get("/api/v1/questions/").data if "Dana" in q["text"]],
            ["@Dana CSM when is the QBR?"],
        )
        # Raj was asked too, but Dana's part is not his.
        self.assertEqual(
            [m.content for m in visible_messages(conversation, self.raj)],
            ["Alice: general", "@Raj Mehta procurement?"],
        )
        # Priya reports to Alice, not Carl: not a manager of Dana, never mentioned.
        self.client.force_authenticate(self.priya)
        self.assertEqual(self.client.get("/api/v1/copilot/conversations/").data, [])

    def test_a_manager_opens_the_customers_their_reports_own(self):
        # Dana owns nothing here; give her one and Carl, her manager, can open it.
        mine = Customer.objects.create(organisation=self.org, name="Dana's", owner=self.dana)
        self.client.force_authenticate(self.carl)
        self.assertEqual(
            self.client.get(f"/api/v1/customers/{mine.id}/").status_code, status.HTTP_200_OK
        )
        self.client.force_authenticate(self.raj)
        self.assertEqual(
            self.client.get(f"/api/v1/customers/{mine.id}/").status_code, status.HTTP_404_NOT_FOUND
        )
