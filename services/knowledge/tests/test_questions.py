"""Questions routed to people; answers that become knowledge."""

from unittest.mock import patch

from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers.models import Customer
from services.knowledge import mentions
from services.knowledge.models import Contribution, Question
from services.notifications.models import Notification


class Fixture(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        self.alice = User.objects.create_user(
            email="alice@acme.io",
            password="x",
            name="Alice Admin",
            organisation=self.org,
            role=User.Role.ADMIN,
            function=User.Function.LEADERSHIP,
        )
        self.mei = User.objects.create_user(
            email="mei@acme.io",
            password="x",
            name="Mei Tanaka",
            organisation=self.org,
            role=User.Role.CSM,
            function=User.Function.ANALYTICS,
        )
        self.mei2 = User.objects.create_user(
            email="mei2@acme.io",
            password="x",
            name="Mei Ling",
            organisation=self.org,
            role=User.Role.CSM,
            function=User.Function.SALES,
        )
        self.carl = User.objects.create_user(
            email="carl@acme.io",
            password="x",
            name="Carl CSM",
            organisation=self.org,
            role=User.Role.CSM,
        )
        self.pizza = Customer.objects.create(
            organisation=self.org, name="Pizza Hut", owner=self.carl
        )


class MentionTests(Fixture):
    def test_first_name_when_unique_full_name_when_not_never_yourself(self):
        found = mentions.resolve_mentions(
            "@Carl and @Mei Tanaka, thoughts? @Alice", self.org, exclude=self.alice
        )
        self.assertEqual([u.name for u in found], ["Carl CSM", "Mei Tanaka"])
        # "@Mei" alone is ambiguous between two Meis: nobody rather than the wrong one.
        self.assertEqual(mentions.resolve_mentions("@Mei why?", self.org), [])
        self.assertEqual(mentions.resolve_mentions("no mentions here", self.org), [])


class QuestionFlowTests(Fixture):
    def test_ask_by_mention_notifies_and_answer_becomes_a_contribution(self):
        self.client.force_authenticate(self.alice)
        asked = self.client.post(
            f"/api/v1/customers/{self.pizza.id}/questions/",
            {"text": "@Mei Tanaka why is Pizza Hut usage down?"},
            format="json",
        )
        self.assertEqual(asked.status_code, status.HTTP_201_CREATED)
        (q,) = asked.data
        self.assertEqual(q["assignee"]["name"], "Mei Tanaka")
        self.assertEqual(q["status"], "open")
        note = Notification.objects.get(recipient=self.mei)
        self.assertEqual(note.kind, "question_asked")
        self.assertIn("Alice Admin asked you about Pizza Hut", note.message)
        self.assertEqual(note.link, f"/organizations/{self.pizza.id}")

        # Carl was not asked.
        self.client.force_authenticate(self.carl)
        self.assertEqual(
            self.client.post(
                f"/api/v1/questions/{q['id']}/answer/", {"body": "x"}, format="json"
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )

        self.client.force_authenticate(self.mei)
        mine = self.client.get("/api/v1/questions/?mine=true&status=open").data
        self.assertEqual([m["id"] for m in mine], [q["id"]])
        answered = self.client.post(
            f"/api/v1/questions/{q['id']}/answer/",
            {"body": "The reporting module broke in the March release; reports fell 70%."},
            format="json",
        )
        self.assertEqual(answered.status_code, status.HTTP_200_OK)
        self.assertEqual(answered.data["status"], "answered")
        self.assertEqual(answered.data["answer"]["function"], "analytics")
        contribution = Contribution.objects.get(customer=self.pizza, author=self.mei)
        self.assertTrue(
            contribution.body.startswith("In answer to Alice Admin's question \"@Mei Tanaka why")
        )
        self.assertIn("reporting module broke", contribution.body)
        self.assertEqual(Notification.objects.get(recipient=self.alice).kind, "question_answered")
        self.assertEqual(
            self.client.post(
                f"/api/v1/questions/{q['id']}/answer/", {"body": "again"}, format="json"
            ).status_code,
            status.HTTP_409_CONFLICT,
        )

    def test_ask_with_an_explicit_assignee_and_refuse_nobody_or_yourself(self):
        self.client.force_authenticate(self.alice)
        url = f"/api/v1/customers/{self.pizza.id}/questions/"
        self.assertEqual(
            self.client.post(url, {"text": "Anyone?"}, format="json").status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(
            self.client.post(
                url, {"text": "Me?", "assignee_id": self.alice.id}, format="json"
            ).status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        ok = self.client.post(url, {"text": "Status?", "assignee_id": self.carl.id}, format="json")
        self.assertEqual(ok.data[0]["assignee"]["name"], "Carl CSM")
        listed = self.client.get(url).data
        self.assertEqual([r["text"] for r in listed], ["Status?"])


class CopilotRoutingTests(Fixture):
    def test_a_mention_in_the_copilot_routes_a_question_on_the_named_customer(self):
        self.client.force_authenticate(self.alice)
        with patch(
            "services.copilot.views.get_completion", return_value="Noted; I've routed that to Mei."
        ) as call:
            response = self.client.post(
                "/api/v1/copilot/messages/",
                {"content": "@Mei Tanaka why is Pizza Hut usage down?"},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(
            "routed this question to Mei Tanaka (Analytics)", call.call_args.kwargs["system"]
        )
        user_turn = response.data["messages"][0]
        self.assertEqual(user_turn["questions"][0]["assignee"]["name"], "Mei Tanaka")
        question = Question.objects.get()
        self.assertEqual(question.customer, self.pizza)
        self.assertEqual(question.message_id, user_turn["id"])
        self.assertEqual(response.data["messages"][1]["questions"], [])
