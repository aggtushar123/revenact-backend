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
        self.assertEqual(seen_by(self.carl), ["dana note", "leadership note"])
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
