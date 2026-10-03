"""An Ask question routed to a colleague names its customer only to a person
who could already open it.

On an Ask surface the customer a question is about comes from the asker's
screen (a focus on one company, an Organizations list narrowed to one), not
from words the asker typed. A mentioned colleague who may not open that
customer gets the question without it: the notification says "asked you",
not "asked you about Secret Corp", and their inbox shows no customer, name
or id. The question also does not open the customer to them, as a routed
question otherwise would (`visible_customers`, `questions__assignee`). A
colleague who can open it gets it named, as before.

Separately, any reader of the questions list — not only the person asked —
gets `customer: null` on a question about a customer they may not open."""

from services.copilot.tests.test_withheld_ask_context import WithheldAskContextFixture
from services.customers.scoping import visible_customers
from services.knowledge.models import Question
from services.notifications.models import Notification

QUESTIONS = "/api/v1/questions/"


def dashboard_focus(customer):
    return {
        "surface": "dashboard",
        "area": "overview",
        "view": None,
        "filters": {"owner": "", "lifecycle": "", "customer": ""},
        "focus": {"kind": "companies", "ids": [customer.pk]},
    }


def organizations_focus(customer):
    return {
        "surface": "organizations",
        "view": "list",
        "filters": {},
        "focus": {"kind": "companies", "ids": [customer.pk]},
    }


class RoutedQuestionFixture(WithheldAskContextFixture):
    CONTENT = "@Viewer can you look at this one?"

    def ask(self, context):
        self.send(context, content=self.CONTENT)
        return Question.objects.get(assignee=self.viewer)

    def notification(self):
        return Notification.objects.get(recipient=self.viewer, kind="question_asked")

    def inbox(self, user=None):
        self.client.force_authenticate(user or self.viewer)
        response = self.client.get(f"{QUESTIONS}?mine=true")
        self.assertEqual(response.status_code, 200, response.data)
        return response


class HiddenCustomerTests(RoutedQuestionFixture):
    def assert_not_named(self, question):
        note = self.notification()
        self.assertEqual(note.message, f"Alice Admin asked you: {self.CONTENT}")
        self.assertNotIn(str(self.secret.pk), note.link)
        response = self.inbox()
        (row,) = response.data
        self.assertIsNone(row["customer"])
        self.assertNotIn("Secret Corp", response.content.decode())
        self.assertIsNone(question.customer_id)
        self.assertFalse(visible_customers(self.viewer).filter(pk=self.secret.pk).exists())

    def test_a_dashboard_focus_on_a_hidden_customer_is_not_named(self):
        self.assert_not_named(self.ask(dashboard_focus(self.secret)))

    def test_an_organizations_list_of_one_hidden_customer_is_not_named(self):
        self.assert_not_named(self.ask(organizations_focus(self.secret)))

    def test_the_question_can_still_be_answered(self):
        question = self.ask(organizations_focus(self.secret))
        self.client.force_authenticate(self.viewer)

        response = self.client.post(
            f"{QUESTIONS}{question.pk}/answer/", {"body": "On it."}, format="json"
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "answered")
        self.assertIsNone(response.data["customer"])
        note = Notification.objects.get(recipient=self.alice, kind="question_answered")
        self.assertEqual(note.message, "Viewer answered your question: On it.")


class VisibleCustomerTests(RoutedQuestionFixture):
    def test_a_customer_the_colleague_can_open_is_named(self):
        question = self.ask(organizations_focus(self.globex))

        self.assertEqual(question.customer_id, self.globex.pk)
        self.assertEqual(
            self.notification().message, f"Alice Admin asked you about Globex: {self.CONTENT}"
        )
        (row,) = self.inbox().data
        self.assertEqual(row["customer"], {"id": self.globex.pk, "name": "Globex"})


class QuestionListReaderTests(RoutedQuestionFixture):
    """Dana reads Carl's questions (he is above her in the chart) but may not
    open Carl's Secret Corp."""

    def setUp(self):
        super().setUp()
        self.question = Question.objects.create(
            organisation=self.org,
            customer=self.secret,
            asked_by=self.carl,
            assignee=self.priya,
            text="@Priya Nair what about Secret Corp's SSO?",
        )

    def test_a_reader_who_cannot_open_the_customer_gets_none(self):
        self.assertFalse(visible_customers(self.dana).filter(pk=self.secret.pk).exists())
        self.client.force_authenticate(self.dana)

        response = self.client.get(QUESTIONS)

        self.assertEqual(response.status_code, 200)
        (row,) = [r for r in response.data if r["id"] == self.question.pk]
        self.assertIsNone(row["customer"])

    def test_the_person_asked_and_the_asker_still_get_it(self):
        for user in (self.priya, self.carl):
            self.client.force_authenticate(user)
            (row,) = [r for r in self.client.get(QUESTIONS).data if r["id"] == self.question.pk]
            self.assertEqual(row["customer"], {"id": self.secret.pk, "name": "Secret Corp"})

    def answered_row(self, user):
        from services.knowledge.mentions import answer_question

        answer_question(self.question, self.priya, "SSO is fixed in 4.2.")
        self.client.force_authenticate(user)
        (row,) = [r for r in self.client.get(QUESTIONS).data if r["id"] == self.question.pk]
        return row

    def test_an_answered_question_does_not_name_the_customer_through_its_answer(self):
        row = self.answered_row(self.dana)

        self.assertIsNone(row["customer"])
        self.assertIsNone(row["answer"]["customer_id"])
        self.assertIsNone(row["answer"]["customer_name"])
        self.assertIn("SSO is fixed in 4.2.", row["answer"]["body"])

    def test_a_reader_who_can_open_the_customer_gets_the_answers_customer(self):
        row = self.answered_row(self.carl)

        self.assertEqual(row["answer"]["customer_id"], self.secret.pk)
        self.assertEqual(row["answer"]["customer_name"], "Secret Corp")
