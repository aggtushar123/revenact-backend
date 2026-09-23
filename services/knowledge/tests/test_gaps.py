"""What the company cannot answer, and the brief that says what it does know."""

from unittest.mock import patch

from django.utils import timezone
from rest_framework.test import APITestCase

from core.models import AuditEvent
from services.accounts.capabilities import Capability
from services.accounts.models import Organisation, Role, User
from services.copilot.anthropic_client import BudgetExceeded
from services.customers.models import Contact, Customer, Note
from services.knowledge.models import Contribution, FunctionOwner, KnowledgeGap, Question

GAPS = "/api/v1/knowledge/gaps/"
COMPLETION = "services.knowledge.brief.get_completion"


def brief_answer(
    use_cases=("Dispatching field crews",),
    stakeholders=(("Priya", "Wants fewer no-shows"),),
    open_threads=("Waiting on the multi-year quote",),
    missing=("Nobody has said what they use reporting for",),
):
    import json

    return json.dumps(
        {
            "use_cases": list(use_cases),
            "stakeholders": [{"name": n, "cares_about": c} for n, c in stakeholders],
            "open_threads": list(open_threads),
            "missing": list(missing),
        }
    )


class Fixture(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        mk = lambda email, name, **kw: User.objects.create_user(  # noqa: E731
            email=email, password="x", name=name, organisation=self.org, **kw
        )
        self.alice = mk("alice@acme.io", "Alice", role=User.Role.ADMIN)
        self.dana = mk("dana@acme.io", "Dana", reports_to=self.alice, function=User.Function.CS)
        self.mei = mk(
            "mei@acme.io", "Mei", reports_to=self.alice, function=User.Function.ENGINEERING
        )
        self.eve = mk("eve@acme.io", "Eve", reports_to=self.alice, function=User.Function.CS)
        self.pizza = Customer.objects.create(
            organisation=self.org, name="Pizza Hut", domain="pizzahut.com", owner=self.dana
        )
        self.client.force_authenticate(self.dana)

    def lead(self, user):
        role = Role.objects.create(
            organisation=self.org,
            name=f"Lead {user.id}",
            slug=f"lead-{user.id}",
            permissions=[Capability.VIEW_ALL_ACCOUNTS],
        )
        user.role = role
        user.save(update_fields=["role"])
        user.refresh_from_db()
        return user


class RaisingGaps(Fixture):
    def test_a_question_nobody_answered_becomes_a_gap(self):
        from services.knowledge.gaps import raise_from_stale_questions

        question = Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.dana,
            assignee=self.mei,
            text="What do they actually use the API for?",
        )
        question.created_at = timezone.now() - timezone.timedelta(days=4)
        question.save(update_fields=["created_at"])
        self.assertEqual(raise_from_stale_questions(), 1)
        gap = KnowledgeGap.objects.get()
        self.assertEqual(gap.customer, self.pizza)
        self.assertIn("use the API for", gap.subject)
        self.assertEqual(gap.function, User.Function.ENGINEERING)
        self.assertEqual(gap.times_asked, 1)
        # A second pass does not raise the same question twice.
        self.assertEqual(raise_from_stale_questions(), 0)
        self.assertEqual(KnowledgeGap.objects.count(), 1)

    def test_an_answered_question_raises_nothing(self):
        from services.knowledge.gaps import raise_from_stale_questions

        question = Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.dana,
            assignee=self.mei,
            text="Anything?",
            status=Question.Status.ANSWERED,
        )
        question.created_at = timezone.now() - timezone.timedelta(days=9)
        question.save(update_fields=["created_at"])
        self.assertEqual(raise_from_stale_questions(), 0)

    def test_a_copilot_question_with_nothing_to_go_on_becomes_a_gap(self):
        from services.knowledge.gaps import record_unanswered

        record_unanswered(
            organisation=self.org,
            customer=self.pizza,
            question="Which integrations do they run?",
            asked_by=self.dana,
        )
        record_unanswered(
            organisation=self.org,
            customer=self.pizza,
            question="which integrations do they RUN?",
            asked_by=self.eve,
        )
        gap = KnowledgeGap.objects.get()
        self.assertEqual(gap.times_asked, 2)
        self.assertEqual(gap.source, KnowledgeGap.Source.COPILOT)
        self.assertIsNotNone(gap.last_asked_at)

    def test_the_function_owner_is_who_should_answer_when_there_is_one(self):
        from services.knowledge.gaps import record_unanswered

        self.eve.function = User.Function.SALES
        self.eve.save(update_fields=["function"])
        FunctionOwner.objects.create(
            customer=self.pizza, function=User.Function.SALES, user=self.eve
        )
        record_unanswered(
            organisation=self.org,
            customer=self.pizza,
            question="What did they sign for?",
            asked_by=self.dana,
            function=User.Function.SALES,
        )
        self.assertEqual(KnowledgeGap.objects.get().assignee, self.eve)


class ReadingGaps(Fixture):
    def setUp(self):
        super().setUp()
        self.gap = KnowledgeGap.objects.create(
            organisation=self.org,
            customer=self.pizza,
            subject="Which integrations do they run?",
            function=User.Function.ENGINEERING,
            times_asked=3,
        )

    def test_lists_the_open_gaps_of_the_whole_company(self):
        response = self.client.get(GAPS)
        self.assertEqual(response.status_code, 200, response.data)
        row = response.data[0]
        self.assertEqual(row["subject"], "Which integrations do they run?")
        self.assertEqual(row["times_asked"], 3)
        self.assertEqual(row["customer"]["name"], "Pizza Hut")
        # A colleague who does not own this account still sees the question:
        # whoever can answer it is usually not the owner.
        self.client.force_authenticate(self.mei)
        self.assertEqual(len(self.client.get(GAPS).data), 1)

    def test_filters_by_customer_and_by_status(self):
        self.assertEqual(len(self.client.get(f"{GAPS}?customer={self.pizza.id}").data), 1)
        self.gap.status = KnowledgeGap.Status.FILLED
        self.gap.save(update_fields=["status"])
        self.assertEqual(self.client.get(GAPS).data, [])
        self.assertEqual(len(self.client.get(f"{GAPS}?status=filled").data), 1)

    def test_another_tenant_sees_nothing(self):
        other = Organisation.objects.create(name="Other")
        outsider = User.objects.create_user(
            email="o@other.io", password="x", name="O", organisation=other, role=User.Role.ADMIN
        )
        self.client.force_authenticate(outsider)
        self.assertEqual(self.client.get(GAPS).data, [])


class ClosingGaps(Fixture):
    def setUp(self):
        super().setUp()
        self.gap = KnowledgeGap.objects.create(
            organisation=self.org,
            customer=self.pizza,
            subject="Which integrations do they run?",
            function=User.Function.ENGINEERING,
        )

    def test_answering_writes_the_contribution_and_fills_the_gap(self):
        self.client.force_authenticate(self.mei)
        response = self.client.post(
            f"{GAPS}{self.gap.id}/answer/",
            {"body": "They run Salesforce and Slack."},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.gap.refresh_from_db()
        self.assertEqual(self.gap.status, KnowledgeGap.Status.FILLED)
        contribution = Contribution.objects.get()
        self.assertEqual(contribution.author, self.mei)
        self.assertEqual(contribution.function, User.Function.ENGINEERING)
        self.assertEqual(self.gap.filled_by, contribution)
        self.assertTrue(AuditEvent.objects.filter(action="knowledge.gap_filled").exists())

    def test_a_contribution_written_anywhere_else_fills_a_matching_gap(self):
        self.client.force_authenticate(self.mei)
        response = self.client.post(
            f"/api/v1/customers/{self.pizza.id}/contributions/",
            {"body": "They run Salesforce and Slack."},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.gap.refresh_from_db()
        self.assertEqual(self.gap.status, KnowledgeGap.Status.FILLED)

    def test_dismissing_closes_it_without_a_contribution(self):
        response = self.client.post(f"{GAPS}{self.gap.id}/dismiss/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.gap.refresh_from_db()
        self.assertEqual(self.gap.status, KnowledgeGap.Status.DISMISSED)
        self.assertFalse(Contribution.objects.exists())

    def test_another_tenants_gap_is_404(self):
        other = Organisation.objects.create(name="Other")
        outsider = User.objects.create_user(
            email="o@other.io", password="x", name="O", organisation=other, role=User.Role.ADMIN
        )
        self.client.force_authenticate(outsider)
        self.assertEqual(
            self.client.post(
                f"{GAPS}{self.gap.id}/answer/", {"body": "x"}, format="json"
            ).status_code,
            404,
        )


class TheBrief(Fixture):
    def setUp(self):
        super().setUp()
        Contribution.objects.create(
            organisation=self.org,
            customer=self.pizza,
            author=self.mei,
            function=User.Function.ENGINEERING,
            body="They dispatch field crews with our scheduler.",
        )
        Note.objects.create(
            customer=self.pizza,
            author=self.dana,
            title="Renewal",
            logged_at=timezone.now(),
            body="Priya wants fewer no-shows.",
        )
        Contact.objects.create(customer=self.pizza, name="Priya", email="priya@pizzahut.com")
        self.url = f"/api/v1/customers/{self.pizza.id}/brief/"

    @patch(COMPLETION, return_value=brief_answer())
    def test_generating_writes_the_brief_with_what_it_read(self, completion):
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        body = response.data
        self.assertEqual(body["use_cases"], ["Dispatching field crews"])
        self.assertEqual(body["stakeholders"][0]["name"], "Priya")
        self.assertEqual(body["open_threads"], ["Waiting on the multi-year quote"])
        self.assertTrue(body["sources"])
        self.assertEqual(completion.call_args.kwargs["purpose"], "account_brief")
        prompt = completion.call_args.kwargs["messages"][0]["content"]
        self.assertIn("dispatch field crews", prompt)
        self.assertIn("<record", prompt)
        self.assertTrue(AuditEvent.objects.filter(action="knowledge.brief").exists())

    @patch(COMPLETION, return_value=brief_answer())
    def test_reading_it_back_says_how_old_it_is_and_who_asked(self, completion):
        self.client.post(self.url, {}, format="json")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["generated_by"]["name"], "Dana")
        self.assertIn("generated_at", response.data)
        self.assertEqual(response.data["use_cases"], ["Dispatching field crews"])

    def test_no_brief_yet_is_an_empty_answer_not_a_404(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data["generated_at"])

    @patch(COMPLETION, return_value=brief_answer())
    def test_the_missing_list_is_the_open_gaps(self, completion):
        KnowledgeGap.objects.create(
            organisation=self.org,
            customer=self.pizza,
            subject="Which integrations do they run?",
            function=User.Function.ENGINEERING,
            times_asked=2,
        )
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(
            [gap["subject"] for gap in response.data["gaps"]], ["Which integrations do they run?"]
        )

    @patch(COMPLETION, side_effect=BudgetExceeded("spent"))
    def test_a_spent_budget_is_429_and_writes_nothing(self, completion):
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 429)
        self.assertIsNone(self.client.get(self.url).data["generated_at"])

    @patch(COMPLETION, return_value=brief_answer())
    def test_another_tenant_cannot_read_or_write_it(self, completion):
        self.client.post(self.url, {}, format="json")
        other = Organisation.objects.create(name="Other")
        outsider = User.objects.create_user(
            email="o@other.io", password="x", name="O", organisation=other, role=User.Role.ADMIN
        )
        self.client.force_authenticate(outsider)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 404)

    @patch(COMPLETION, return_value=brief_answer())
    def test_citations_a_reader_may_not_open_are_withheld_from_them(self, completion):
        # Dana's note is hers and her chain's. Mei may read the brief,
        # because knowledge is company-wide, but neither the note nor the
        # words written from it (see WhatAColleagueSeesOfTheBrief).
        self.client.post(self.url, {}, format="json")
        self.client.force_authenticate(self.mei)
        body = self.client.get(self.url).data
        self.assertNotIn("Renewal", [source["label"] for source in body["sources"]])
        self.assertGreaterEqual(body["hidden_sources"], 1)


class FromTheCopilot(Fixture):
    """A question the Copilot had nothing to answer from is a gap."""

    def setUp(self):
        super().setUp()
        self.url = "/api/v1/copilot/messages/"

    @patch("services.copilot.views.get_completion", return_value="I could not find anything.")
    def test_a_question_about_a_company_with_no_records_raises_one(self, completion):
        response = self.client.post(
            self.url, {"content": "What does Pizza Hut use the API for?"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        gap = KnowledgeGap.objects.get()
        self.assertEqual(gap.customer, self.pizza)
        self.assertIn("use the API for", gap.subject)
        self.assertEqual(gap.source, KnowledgeGap.Source.COPILOT)

    @patch("services.copilot.views.get_completion", return_value="They dispatch crews.")
    def test_an_answer_with_records_behind_it_raises_nothing(self, completion):
        Note.objects.create(
            customer=self.pizza,
            author=self.dana,
            title="Usage",
            logged_at=timezone.now(),
            body="They dispatch field crews with the scheduler.",
        )
        response = self.client.post(
            self.url, {"content": "What does Pizza Hut use the API for?"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(KnowledgeGap.objects.exists())

    @patch("services.copilot.views.get_completion", return_value="Which company do you mean?")
    def test_a_question_about_nobody_in_particular_raises_nothing(self, completion):
        response = self.client.post(self.url, {"content": "How is the book doing?"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(KnowledgeGap.objects.exists())


class WhatAColleagueSeesOfTheBrief(Fixture):
    """The brief's prose is written from what the owner may read, so a
    reader who may not read those records does not get it paraphrased."""

    def setUp(self):
        super().setUp()
        Note.objects.create(
            customer=self.pizza,
            author=self.dana,
            title="Renewal",
            logged_at=timezone.now(),
            body="Priya wants fewer no-shows.",
        )
        self.url = f"/api/v1/customers/{self.pizza.id}/brief/"

    @patch(COMPLETION, return_value=brief_answer())
    def test_the_prose_is_withheld_when_any_citation_is(self, completion):
        self.client.post(self.url, {}, format="json")
        self.client.force_authenticate(self.mei)
        body = self.client.get(self.url).data
        self.assertEqual(body["use_cases"], [])
        self.assertEqual(body["stakeholders"], [])
        self.assertEqual(body["open_threads"], [])
        self.assertGreaterEqual(body["hidden_sources"], 1)
        # The gaps beside it are the company's, so they still show.
        self.assertIn("gaps", body)

    @patch(COMPLETION, return_value=brief_answer())
    def test_the_owner_sees_all_of_it(self, completion):
        self.client.post(self.url, {}, format="json")
        body = self.client.get(self.url).data
        self.assertEqual(body["use_cases"], ["Dispatching field crews"])
        self.assertEqual(body["hidden_sources"], 0)

    @patch(COMPLETION, return_value=brief_answer())
    def test_every_record_behind_the_prose_is_citable(self, completion):
        # A contribution reaches the model through retrieval, which cites
        # it; nothing may reach the prompt without a citation, or it could
        # never be counted as withheld.
        Contribution.objects.create(
            organisation=self.org,
            customer=self.pizza,
            author=self.mei,
            function=User.Function.ENGINEERING,
            body="They dispatch field crews with our scheduler.",
        )
        self.client.post(self.url, {}, format="json")
        prompt = completion.call_args.kwargs["messages"][0]["content"]
        cited = {source["label"] for source in self.client.get(self.url).data["sources"]}
        self.assertIn("dispatch field crews", prompt)
        self.assertTrue(any("Engineering" in label for label in cited), cited)


class StaleQuestionSweep(Fixture):
    def _stale(self, text, assignee):
        question = Question.objects.create(
            organisation=self.org,
            customer=self.pizza,
            asked_by=self.dana,
            assignee=assignee,
            text=text,
        )
        Question.objects.filter(pk=question.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=5)
        )
        return question

    def test_two_people_asking_the_same_thing_are_both_swept_once(self):
        from services.knowledge.gaps import raise_from_stale_questions

        self._stale("What do they use the API for?", self.mei)
        self._stale("what do they use the API for?", self.eve)
        self.assertEqual(raise_from_stale_questions(), 1)
        gap = KnowledgeGap.objects.get()
        self.assertEqual(gap.times_asked, 2)
        # Neither question is ever swept again, however often it runs.
        self.assertEqual(raise_from_stale_questions(), 0)
        self.assertEqual(raise_from_stale_questions(), 0)
        gap.refresh_from_db()
        self.assertEqual(gap.times_asked, 2)


class AlreadyClosed(Fixture):
    def setUp(self):
        super().setUp()
        self.gap = KnowledgeGap.objects.create(
            organisation=self.org,
            customer=self.pizza,
            subject="Which integrations do they run?",
            fingerprint="which integrations do they run?",
            function=User.Function.ENGINEERING,
        )

    def test_answering_a_closed_gap_is_a_conflict_and_writes_nothing(self):
        self.gap.status = KnowledgeGap.Status.DISMISSED
        self.gap.save(update_fields=["status"])
        response = self.client.post(f"{GAPS}{self.gap.id}/answer/", {"body": "x"}, format="json")
        self.assertEqual(response.status_code, 409, response.data)
        self.assertFalse(Contribution.objects.exists())
        self.gap.refresh_from_db()
        self.assertEqual(self.gap.status, KnowledgeGap.Status.DISMISSED)

    def test_dismissing_an_answered_gap_is_a_conflict(self):
        self.gap.status = KnowledgeGap.Status.FILLED
        self.gap.save(update_fields=["status"])
        response = self.client.post(f"{GAPS}{self.gap.id}/dismiss/", {}, format="json")
        self.assertEqual(response.status_code, 409, response.data)
        self.gap.refresh_from_db()
        self.assertEqual(self.gap.status, KnowledgeGap.Status.FILLED)
