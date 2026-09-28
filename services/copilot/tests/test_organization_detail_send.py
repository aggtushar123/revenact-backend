"""POST /api/v1/copilot/messages/ from one organisation's page. The model call
is stubbed; the tests read the prompt it was given and what was stored."""

from unittest.mock import patch

from rest_framework.test import APIClient

from services.copilot.grounded_records import account_ref
from services.copilot.models import Conversation, Message, ModelCall
from services.copilot.organization_detail_context import NOT_AN_ACCOUNT, NOT_OPEN

from .test_organization_detail_grounding import DetailFixture

URL = "/api/v1/copilot/messages/"


@patch("services.copilot.views.get_completion", return_value="EMEA is waiting on terms.")
class DetailSendTests(DetailFixture):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.csm)

    def send(self, context, content="What is going on here?", **extra):
        return self.api.post(URL, {"content": content, "context": context, **extra}, format="json")

    def test_the_answer_is_grounded_in_the_page_and_metered_as_organizations(self, completion):
        self.note(self.emea, title="Terms pending")

        response = self.send(self.detail(self.pizza, self.emea))

        self.assertEqual(response.status_code, 200, response.data)
        kwargs = completion.call_args.kwargs
        self.assertEqual(kwargs["purpose"], "organizations")
        self.assertIn("Screen: Organizations › Pizza Hut · EMEA", kwargs["system"])
        self.assertIn("Terms pending", kwargs["system"])
        self.assertIn("Organizations data:\n<dashboard_data>", kwargs["system"])

    def test_the_context_is_stored_with_the_servers_label_and_becomes_the_origin(self, completion):
        note = self.note(self.emea)
        context = {
            **self.detail(self.pizza, self.emea, focus={"kind": "note", "id": note.pk}),
            "label": "Spoofed",
        }

        data = self.send(context).data

        origin = {
            "surface": "organizations",
            "view": "detail",
            "organization": self.pizza.pk,
            "account": self.emea.pk,
            "label": "Pizza Hut · EMEA",
        }
        self.assertEqual(data["origin"], origin)
        self.assertEqual(
            data["messages"][0]["context"], {**origin, "focus": {"kind": "note", "id": note.pk}}
        )
        listed = self.api.get("/api/v1/copilot/conversations/").data
        self.assertEqual(listed[0]["origin"], origin)

    def test_the_reply_stores_what_a_shared_reader_is_checked_against(self, completion):
        self.send(self.detail(self.pizza))

        reply = Message.objects.get(role="assistant")
        self.assertEqual(reply.grounded_customer_ids, [self.pizza.pk])
        self.assertFalse(reply.carries_anomaly_text)
        self.assertEqual(reply.grounded_pipeline, {"account_ids": [], "departments": []})
        self.assertEqual(reply.grounded_tickets, {"account_ids": [], "departments": []})
        self.assertEqual(
            reply.grounded_records, [account_ref(self.emea.pk), account_ref(self.apac.pk)]
        )

    def test_a_follow_up_without_context_carries_the_pages_records(self, completion):
        first = self.send(self.detail(self.pizza)).data

        self.api.post(
            URL, {"conversation_id": first["id"], "content": "Summarise that"}, format="json"
        )

        follow_up = Message.objects.filter(role="assistant").order_by("id").last()
        self.assertEqual(
            follow_up.grounded_records, [account_ref(self.emea.pk), account_ref(self.apac.pk)]
        )

    def test_a_page_the_asker_cannot_open_is_refused_before_the_model_is_called(self, completion):
        hooli = self.customer("Hooli")
        elsewhere = self.account("Hooli EU", customers=[hooli])
        cases = (
            (self.detail(self.pizza) | {"organization": 999999}, {"organization": [NOT_OPEN]}),
            (self.detail(self.pizza, elsewhere), {"account": [NOT_AN_ACCOUNT]}),
        )
        for context, errors in cases:
            with self.subTest(errors=errors):
                response = self.send(context)

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data, {"context": errors})
        self.api.force_authenticate(self.other)
        response = self.send(self.detail(self.pizza))
        self.assertEqual(response.data, {"context": {"organization": [NOT_OPEN]}})
        completion.assert_not_called()
        self.assertFalse(Message.objects.exists())
        self.assertFalse(Conversation.objects.exists())
        self.assertFalse(ModelCall.objects.exists())

    def test_a_focus_the_asker_may_not_read_is_dropped_before_grounding(self, completion):
        private = self.note(self.pizza, title="Dana's private note", author=self.other)

        data = self.send(self.detail(self.pizza, focus={"kind": "note", "id": private.pk})).data

        self.assertIsNone(data["messages"][0]["context"]["focus"])
        self.assertNotIn("Dana's private note", completion.call_args.kwargs["system"])
