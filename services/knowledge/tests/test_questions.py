"""Questions routed to people; answers that become knowledge."""

from unittest.mock import patch

from rest_framework import status
from rest_framework.test import APITestCase

from services.accounts.models import Organisation, User
from services.customers.models import Customer
from services.knowledge import mentions
from services.knowledge.models import Contribution, FunctionOwner, Question
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
        # The chart: everyone reports to Alice (services.accounts.hierarchy).
        User.objects.filter(organisation=self.org).exclude(pk=self.alice.pk).update(
            reports_to=self.alice
        )
        # The in-memory users must see their new manager too.
        for person in User.objects.filter(organisation=self.org):
            for attr in vars(self):
                if (
                    getattr(self, attr, None).__class__ is User
                    and getattr(self, attr).pk == person.pk
                ):
                    getattr(self, attr).refresh_from_db()


class MentionTests(Fixture):
    def test_first_name_when_unique_full_name_when_not_never_yourself(self):
        found = mentions.resolve_mentions(
            "@Carl and @Mei Tanaka, thoughts? @Alice", self.org, exclude=self.alice
        )
        self.assertEqual([u.name for u in found], ["Carl CSM", "Mei Tanaka"])
        # "@Mei" alone is ambiguous between two Meis: nobody rather than the wrong one.
        self.assertEqual(mentions.resolve_mentions("@Mei why?", self.org), [])
        self.assertEqual(mentions.resolve_mentions("no mentions here", self.org), [])


class FunctionMentionTests(Fixture):
    """@engineering reaches the responsible engineer; failing one, all of them."""

    def setUp(self):
        super().setUp()
        self.priya = User.objects.create_user(
            email="priya@acme.io",
            password="x",
            name="Priya Nair",
            organisation=self.org,
            function=User.Function.ENGINEERING,
            reports_to=self.alice,
        )
        self.sam = User.objects.create_user(
            email="sam@acme.io",
            password="x",
            name="Sam Lee",
            organisation=self.org,
            function=User.Function.ENGINEERING,
            reports_to=self.alice,
        )

    def test_a_function_mention_reaches_the_person_responsible_for_the_customer(self):
        FunctionOwner.objects.create(customer=self.pizza, function="engineering", user=self.priya)
        routes = mentions.resolve_routes(
            "@engineering does SSO still break?", self.org, exclude=self.alice, customer=self.pizza
        )
        self.assertEqual([(r.user.name, r.via) for r in routes], [("Priya Nair", "owner")])
        self.assertEqual(
            mentions.routing_summary(routes, self.pizza),
            "Priya Nair (Engineering) — responsible for Pizza Hut in Engineering",
        )

    def test_without_a_responsible_person_everyone_in_the_function_is_asked(self):
        found = mentions.resolve_mentions(
            "@Eng thoughts?", self.org, exclude=self.alice, customer=self.pizza
        )
        self.assertEqual([u.name for u in found], ["Priya Nair", "Sam Lee"])
        # And with no customer at all, the same people.
        found = mentions.resolve_mentions("@engineering?", self.org, exclude=self.alice)
        self.assertEqual([u.name for u in found], ["Priya Nair", "Sam Lee"])
        routes = mentions.resolve_routes("@sales anyone?", self.org, customer=self.pizza)
        self.assertEqual([r.user.name for r in routes], ["Mei Ling"])
        self.assertIn(
            "nobody is responsible for Pizza Hut there yet",
            mentions.routing_summary(routes, self.pizza),
        )

    def test_the_responsible_person_asking_their_own_function_reaches_their_peers(self):
        FunctionOwner.objects.create(customer=self.pizza, function="engineering", user=self.priya)
        found = mentions.resolve_mentions(
            "@engineering anyone free?", self.org, exclude=self.priya, customer=self.pizza
        )
        self.assertEqual([u.name for u in found], ["Sam Lee"])

    def test_cs_falls_back_to_the_account_owner_and_team_is_everyone_responsible(self):
        FunctionOwner.objects.create(customer=self.pizza, function="analytics", user=self.mei)
        found = mentions.resolve_mentions(
            "@cs when is the renewal?", self.org, exclude=self.alice, customer=self.pizza
        )
        self.assertEqual([u.name for u in found], ["Carl CSM"])
        team = mentions.resolve_routes(
            "@team all hands on this one", self.org, exclude=self.carl, customer=self.pizza
        )
        # Carl owns the account but asked, so only the analytics owner remains.
        self.assertEqual([(r.user.name, r.via) for r in team], [("Mei Tanaka", "team")])
        self.assertEqual(
            mentions.routing_summary(team, self.pizza),
            "Mei Tanaka (Analytics) — everyone responsible for Pizza Hut",
        )
        # A team mention with no customer in sight reaches nobody.
        self.assertEqual(mentions.resolve_mentions("@team?", self.org, exclude=self.alice), [])

    def test_people_and_functions_mix_without_duplicates_and_unknown_words_are_ignored(self):
        FunctionOwner.objects.create(customer=self.pizza, function="engineering", user=self.priya)
        found = mentions.resolve_mentions(
            "@Priya and @engineering and @marketing: thoughts?",
            self.org,
            exclude=self.alice,
            customer=self.pizza,
        )
        self.assertEqual([u.name for u in found], ["Priya Nair"])

    def test_asking_a_function_on_the_customer_page_routes_to_its_owner(self):
        FunctionOwner.objects.create(customer=self.pizza, function="engineering", user=self.priya)
        self.client.force_authenticate(self.alice)
        response = self.client.post(
            f"/api/v1/customers/{self.pizza.id}/questions/",
            {"text": "@engineering is the SSO fix shipped?"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual([q["assignee"]["name"] for q in response.data], ["Priya Nair"])
        note = Notification.objects.get(recipient=self.priya)
        self.assertEqual(note.kind, Notification.Kind.QUESTION_ASKED)
        self.assertIn("about Pizza Hut", note.message)

    def test_the_copilot_is_told_why_a_function_mention_reached_whom(self):
        self.client.force_authenticate(self.alice)
        with patch("services.copilot.views.get_completion", return_value="Routed.") as call:
            response = self.client.post(
                "/api/v1/copilot/messages/",
                {"content": "@engineering why does Pizza Hut's SSO break?"},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        system = call.call_args.kwargs["system"]
        self.assertIn("everyone in Engineering, since nobody is responsible for Pizza Hut", system)
        asked = sorted(q["assignee"]["name"] for q in response.data["messages"][0]["questions"])
        self.assertEqual(asked, ["Priya Nair", "Sam Lee"])
        self.assertEqual(Question.objects.filter(customer=self.pizza).count(), 2)


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


class SuggestionTests(Fixture):
    def test_an_answer_about_a_customer_offers_the_responsible_people_but_not_the_asker(self):
        from services.knowledge.models import FunctionOwner

        FunctionOwner.objects.create(customer=self.pizza, function="analytics", user=self.mei)
        FunctionOwner.objects.create(customer=self.pizza, function="leadership", user=self.alice)
        self.client.force_authenticate(self.alice)
        with patch("services.copilot.views.get_completion", return_value="Here is what I know."):
            response = self.client.post(
                "/api/v1/copilot/messages/",
                {"content": "What is up with Pizza Hut?"},
                format="json",
            )

        reply = response.data["messages"][1]
        self.assertEqual(
            [
                (s["name"], s["function_display"], s["customer_name"])
                for s in reply["ask_suggestions"]
            ],
            [
                ("Carl CSM", "Customer Success", "Pizza Hut"),
                ("Mei Tanaka", "Analytics", "Pizza Hut"),
            ],
        )
        self.assertEqual(response.data["messages"][0]["ask_suggestions"], [])

        # One click on the suggestion: a question on the turn it came from.
        asked = self.client.post(
            f"/api/v1/customers/{self.pizza.id}/questions/",
            {
                "text": "What is up with Pizza Hut?",
                "assignee_id": self.mei.id,
                "message_id": response.data["messages"][0]["id"],
            },
            format="json",
        )
        self.assertEqual(asked.status_code, status.HTTP_201_CREATED)
        self.assertEqual(asked.data[0]["message_id"], response.data["messages"][0]["id"])

    def test_forwarding_someone_elses_question_says_whose_it_was(self):
        from services.copilot.models import Conversation, Message

        conversation = Conversation.objects.create(
            organisation=self.org, user=self.alice, title="PH"
        )
        turn = Message.objects.create(
            conversation=conversation,
            role="user",
            content="What did procurement say?",
            author=self.alice,
        )
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.alice,
            assignee=self.mei,
            text=turn.content,
            message=turn,
        )
        # Mei, mentioned in the thread, forwards Alice's question to Carl in one click.
        self.client.force_authenticate(self.mei)
        forwarded = self.client.post(
            f"/api/v1/customers/{self.pizza.id}/questions/",
            {"text": turn.content, "assignee_id": self.carl.id, "message_id": turn.id},
            format="json",
        )
        self.assertEqual(forwarded.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            forwarded.data[0]["text"],
            'Alice Admin asked: "What did procurement say?" — can you answer?',
        )
        self.assertEqual(forwarded.data[0]["asked_by"]["name"], "Mei Tanaka")
        self.assertIn(
            'asked: "What did procurement say?"',
            Notification.objects.get(recipient=self.carl).message,
        )
        # Asking on your own turn is not a forward.
        self.client.force_authenticate(self.alice)
        own = self.client.post(
            f"/api/v1/customers/{self.pizza.id}/questions/",
            {"text": turn.content, "assignee_id": self.carl.id, "message_id": turn.id},
            format="json",
        )
        self.assertEqual(own.data[0]["text"], "What did procurement say?")

    def test_a_question_about_no_company_offers_nobody(self):
        self.client.force_authenticate(self.alice)
        with patch("services.copilot.views.get_completion", return_value="Hi."):
            response = self.client.post(
                "/api/v1/copilot/messages/", {"content": "Hello there"}, format="json"
            )
        self.assertEqual(response.data["messages"][1]["ask_suggestions"], [])


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
        # Asked in a chat: the notification opens that chat, not the account page.
        note = Notification.objects.get(recipient=self.mei)
        self.assertEqual(note.link, f"/copilot?session={response.data['id']}")
        self.assertEqual(response.data["messages"][1]["questions"], [])


class AgingTests(Fixture):
    def _old_question(self, days):
        from datetime import timedelta

        from django.utils import timezone

        q = Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.alice,
            assignee=self.mei,
            text="Still waiting?",
        )
        Question.objects.filter(pk=q.pk).update(created_at=timezone.now() - timedelta(days=days))
        return Question.objects.get(pk=q.pk)

    def test_stale_questions_are_counted_listed_and_nudged_once_a_day(self):
        from services.knowledge import aging
        from services.metrics.registry import compute_all

        fresh = self._old_question(1)
        stale = self._old_question(5)

        values = compute_all(self.org)
        self.assertEqual((values["open_questions"], values["stale_questions"]), (2, 1))

        self.client.force_authenticate(self.alice)
        listed = self.client.get("/api/v1/questions/?stale=true").data
        self.assertEqual([q["id"] for q in listed], [stale.id])
        self.assertEqual(listed[0]["days_open"], 5)

        first = aging.nudge(self.org)
        self.assertEqual([q.id for q in first], [stale.id])
        note = Notification.objects.get(recipient=self.mei)
        self.assertIn(
            "Still waiting: Alice Admin asked you about Pizza Hut 5 days ago", note.message
        )
        self.assertEqual(aging.nudge(self.org), [])  # not again today
        self.assertEqual(Notification.objects.filter(recipient=self.mei).count(), 1)
        self.assertIsNone(Question.objects.get(pk=fresh.pk).last_nudged_at)

    def test_dry_run_changes_nothing(self):
        from services.knowledge import aging

        stale = self._old_question(4)
        self.assertEqual([q.id for q in aging.nudge(self.org, dry_run=True)], [stale.id])
        self.assertEqual(Notification.objects.count(), 0)
        self.assertIsNone(Question.objects.get(pk=stale.pk).last_nudged_at)


class ActivityTests(Fixture):
    def test_each_function_shows_what_it_wrote_asked_answered_and_still_owes(self):
        from datetime import timedelta

        from django.utils import timezone

        from services.knowledge import mentions

        Contribution.objects.create(
            organisation=self.org,
            customer=self.pizza,
            author=self.mei,
            function="analytics",
            body="a",
        )
        Contribution.objects.create(
            organisation=self.org,
            customer=self.pizza,
            author=self.mei,
            function="analytics",
            body="b",
        )
        old = Contribution.objects.create(
            organisation=self.org, customer=self.pizza, author=self.carl, function="cs", body="old"
        )
        Contribution.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=40)
        )
        answered = Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.alice,
            assignee=self.mei,
            text="q1",
        )
        Question.objects.filter(pk=answered.pk).update(
            created_at=timezone.now() - timedelta(days=2)
        )
        mentions.answer_question(Question.objects.get(pk=answered.pk), self.mei, "done")
        Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.alice,
            assignee=self.mei,
            text="q2",
        )

        self.client.force_authenticate(self.alice)
        payload = self.client.get("/api/v1/knowledge/activity/?days=30").data
        by = {r["function"]: r for r in payload["functions"]}
        analytics = by["analytics"]
        # Two notes plus the stored answer.
        self.assertEqual(
            (analytics["members"], analytics["contributors"], analytics["contributions"]), (1, 1, 3)
        )
        self.assertEqual(
            (
                analytics["questions_asked"],
                analytics["questions_answered"],
                analytics["questions_waiting"],
            ),
            (2, 1, 1),
        )
        self.assertEqual(analytics["avg_days_to_answer"], 2.0)
        # Carl's note is outside the window; sales has two members and nothing written.
        self.assertEqual(by["cs"]["contributions"], 0)
        self.assertEqual((by["sales"]["members"], by["sales"]["contributions"]), (1, 0))

        self.client.force_authenticate(self.carl)
        self.assertEqual(
            self.client.get("/api/v1/knowledge/activity/").status_code, status.HTTP_403_FORBIDDEN
        )
