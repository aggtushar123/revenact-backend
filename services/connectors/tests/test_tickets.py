"""Ticket sources: providers map their tickets into ours, the sync files
them on the right company, and departments read only their own."""

import hashlib
import hmac
import json
from datetime import date
from unittest.mock import patch

from django.core.management import call_command
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import AuditEvent
from services.accounts.models import Organisation, User
from services.connectors import sync
from services.connectors.models import Connector
from services.connectors.providers.base import ProviderError, RemoteTicket, tenant
from services.connectors.providers.jira import JiraProvider, adf_text
from services.connectors.providers.slack import SlackProvider
from services.connectors.providers.zendesk import ZendeskProvider
from services.customers.models import Account, Contact, Customer, Ticket


def remote(**overrides):
    base = dict(
        external_id="1042",
        number="ZD-1042",
        title="Login broken",
        description="Cannot log in since Monday.",
        status="open",
        priority="high",
        requester_email="sam@pizzahut.com",
        requester_name="Sam Pizza",
        assignee_name="Support Team",
        url="https://acme.zendesk.com/agent/tickets/1042",
        opened_at=date(2026, 9, 10),
    )
    base.update(overrides)
    return RemoteTicket(**base)


class FakeProvider:
    key = "zendesk"
    label = "Fake"
    uses_oauth = False
    queued = []
    fail = None

    def fetch_tickets(self, config, creds, cursor):
        if self.fail:
            raise self.fail
        return list(self.queued), "cursor-2", creds


class Fixture(APITestCase):
    def setUp(self):
        from services.copilot.anthropic_client import CopilotNotConfigured

        no_model = patch(
            "services.customers.classification.get_completion",
            side_effect=CopilotNotConfigured("no model in tests"),
        )
        no_model.start()
        self.addCleanup(no_model.stop)
        FakeProvider.queued = []
        FakeProvider.fail = None

        self.org = Organisation.objects.create(name="Acme")
        self.admin = User.objects.create_user(
            email="alice@acme.io",
            password="x",
            name="Alice",
            organisation=self.org,
            role=User.Role.ADMIN,
            function=User.Function.CS,
        )
        self.csm = User.objects.create_user(
            email="carl@acme.io",
            password="x",
            name="Carl",
            organisation=self.org,
            role=User.Role.CSM,
            function=User.Function.CS,
        )
        self.engineer = User.objects.create_user(
            email="eve@acme.io",
            password="x",
            name="Eve",
            organisation=self.org,
            role=User.Role.CSM,
            function=User.Function.ENGINEERING,
        )
        self.leader = User.objects.create_user(
            email="lee@acme.io",
            password="x",
            name="Lee",
            organisation=self.org,
            role=User.Role.CSM,
            function=User.Function.LEADERSHIP,
        )
        self.pizza = Customer.objects.create(
            organisation=self.org, name="Pizza Hut", domain="pizzahut.com"
        )
        self.kfc = Customer.objects.create(organisation=self.org, name="KFC", domain="kfc.com")
        self.kfc_uk = Account.objects.create(name="KFC UK", domain="kfc.co.uk")
        self.kfc_uk.customers.add(self.kfc)
        Contact.objects.create(customer=self.pizza, name="Sam", email="sam@pizzahut.com")
        self.zendesk = Connector.objects.create(
            organisation=self.org,
            provider="zendesk",
            name="Zendesk",
            department=User.Function.CS,
        )
        self.zendesk.set_credentials({"email": "a", "api_token": "t"})
        self.zendesk.config = {"host": "acme.zendesk.com"}
        self.zendesk.save()

    def run_sync(self, connector=None, provider=None):
        with patch(
            "services.connectors.sync.get_provider", return_value=provider or FakeProvider()
        ):
            return sync.sync_connector(connector or self.zendesk)


class ProviderMappingTests(APITestCase):
    def test_tenant_accepts_a_name_or_a_host_and_nothing_else(self):
        self.assertEqual(tenant("acme", ".zendesk.com"), "acme.zendesk.com")
        self.assertEqual(tenant("https://Acme.zendesk.com/", ".zendesk.com"), "acme.zendesk.com")
        for bad in ("acme.evil.com", "a/b", "", "evil.com#@acme"):
            with self.assertRaises(ProviderError):
                tenant(bad, ".zendesk.com")

    def test_zendesk_ticket_maps_status_priority_and_sideloaded_people(self):
        users = {
            7: {"id": 7, "name": "Sam", "email": "Sam@PizzaHut.com"},
            9: {"id": 9, "name": "Agent A"},
        }
        raw = {
            "id": 1042,
            "subject": "Login broken",
            "description": "help",
            "status": "pending",
            "priority": "urgent",
            "requester_id": 7,
            "assignee_id": 9,
            "created_at": "2026-09-10T08:00:00Z",
            "updated_at": "2026-09-11T08:00:00Z",
        }
        t = ZendeskProvider()._ticket("acme.zendesk.com", raw, users)
        self.assertEqual((t.status, t.priority), ("in-progress", "critical"))
        self.assertEqual((t.requester_email, t.assignee_name), ("sam@pizzahut.com", "Agent A"))
        self.assertEqual(t.url, "https://acme.zendesk.com/agent/tickets/1042")
        self.assertEqual(t.opened_at, date(2026, 9, 10))
        self.assertIsNone(t.resolved_at)
        solved = ZendeskProvider()._ticket("acme.zendesk.com", {**raw, "status": "solved"}, users)
        self.assertEqual((solved.status, solved.resolved_at), ("resolved", date(2026, 9, 11)))
        self.assertIsNone(ZendeskProvider()._ticket("h", {**raw, "status": "deleted"}, users))

    def test_jira_issue_flattens_the_description_and_reads_the_status_category(self):
        raw = {
            "key": "SUP-12",
            "fields": {
                "summary": "Export fails",
                "status": {"statusCategory": {"key": "done"}},
                "priority": {"name": "Highest"},
                "reporter": {"emailAddress": "Sam@pizzahut.com", "displayName": "Sam"},
                "assignee": {"displayName": "Eve"},
                "created": "2026-09-01T10:00:00.000+0530",
                "resolutiondate": "2026-09-03T10:00:00.000+0000",
                "description": {
                    "type": "doc",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": "Fails on "},
                                {"type": "text", "text": "large files"},
                            ],
                        },
                    ],
                },
            },
        }
        t = JiraProvider()._ticket("acme.atlassian.net", raw)
        self.assertEqual((t.number, t.status, t.priority), ("SUP-12", "resolved", "critical"))
        self.assertEqual(t.description, "Fails on large files")
        self.assertEqual(t.requester_email, "sam@pizzahut.com")
        self.assertEqual((t.opened_at, t.resolved_at), (date(2026, 9, 1), date(2026, 9, 3)))
        self.assertEqual(t.url, "https://acme.atlassian.net/browse/SUP-12")
        self.assertEqual(adf_text(None), "")

    def test_slack_message_is_open_until_ticked_and_thread_replies_are_skipped(self):
        provider = SlackProvider()
        messages = [
            {
                "ts": "1757980800.000100",
                "user": "U1",
                "text": "URGENT: checkout is down\nsince 9am",
            },
            {
                "ts": "1757980900.000200",
                "user": "U1",
                "text": "reply",
                "thread_ts": "1757980800.000100",
            },
            {
                "ts": "1757981000.000300",
                "user": "U2",
                "text": "Invoice question",
                "reactions": [{"name": "white_check_mark"}],
            },
            {"ts": "1757981100.000400", "subtype": "channel_join", "user": "U3", "text": "joined"},
        ]
        calls = {
            "conversations.history": {"ok": True, "messages": messages, "has_more": False},
            "users.info": {
                "ok": True,
                "user": {"real_name": "Sam", "profile": {"email": "sam@pizzahut.com"}},
            },
        }
        with patch.object(SlackProvider, "_call", lambda self, creds, method, **p: calls[method]):
            tickets, cursor, _ = provider.fetch_tickets({"channel": "C1"}, {"bot_token": "x"}, "")
        self.assertEqual(
            [t.title for t in tickets], ["URGENT: checkout is down", "Invoice question"]
        )
        self.assertEqual([t.status for t in tickets], ["open", "resolved"])
        self.assertEqual(tickets[0].priority, "high")
        self.assertEqual(tickets[0].requester_email, "sam@pizzahut.com")
        self.assertEqual(tickets[0].url, "https://slack.com/archives/C1/p1757980800000100")
        self.assertEqual(cursor, "1757981000.000300")

    def test_every_ticket_source_describes_its_own_form_and_others_do_not(self):
        from services.connectors import providers

        self.assertEqual(
            [f["name"] for f in providers.describe("zendesk")["fields"]],
            ["subdomain", "email", "api_token"],
        )
        self.assertTrue(
            next(f for f in providers.describe("zendesk")["fields"] if f["name"] == "api_token")[
                "secret"
            ]
        )
        self.assertEqual(providers.describe("webhook")["fields"], [])
        self.assertIsNone(providers.describe("salesforce"))
        self.assertFalse(providers.describe("slack")["uses_oauth"])  # no Slack app in tests


class SyncTests(Fixture):
    def test_tickets_are_filed_on_the_requesters_company_and_stamped_with_the_department(self):
        FakeProvider.queued = [remote()]
        counts = self.run_sync()

        self.assertEqual(counts, {"created": 1, "updated": 0, "unmatched": 0})
        ticket = Ticket.objects.get()
        self.assertEqual(ticket.customer, self.pizza)
        self.assertEqual(ticket.department, "cs")
        self.assertEqual(ticket.connector, self.zendesk)
        self.assertEqual((ticket.ticket_number, ticket.external_id), ("ZD-1042", "1042"))
        self.assertEqual(ticket.requester_email, "sam@pizzahut.com")
        self.zendesk.refresh_from_db()
        self.assertEqual(self.zendesk.sync_cursor, "cursor-2")
        self.assertEqual(self.zendesk.status, "connected")
        self.assertEqual(self.zendesk.last_sync_note, "1 new, 0 updated")

    def test_domain_matching_reaches_an_account(self):
        FakeProvider.queued = [remote(requester_email="ops@kfc.co.uk")]
        self.run_sync()
        self.assertEqual(Ticket.objects.get().account, self.kfc_uk)

    def test_a_second_pass_updates_rather_than_duplicates(self):
        FakeProvider.queued = [remote()]
        self.run_sync()
        FakeProvider.queued = [
            remote(status="resolved", resolved_at=date(2026, 9, 12), assignee_name="Agent B")
        ]
        counts = self.run_sync()

        self.assertEqual(counts["updated"], 1)
        ticket = Ticket.objects.get()
        self.assertEqual(
            (ticket.status, ticket.resolved_at, ticket.assignee_name),
            ("resolved", date(2026, 9, 12), "Agent B"),
        )

    def test_an_unknown_requester_is_skipped_unless_the_connector_covers_one_company(self):
        FakeProvider.queued = [remote(requester_email="who@gmail.com")]
        counts = self.run_sync()
        self.assertEqual(counts["unmatched"], 1)
        self.assertEqual(Ticket.objects.count(), 0)
        self.zendesk.refresh_from_db()
        self.assertIn("1 without a matching account", self.zendesk.last_sync_note)

        self.zendesk.customers.add(self.kfc)
        counts = self.run_sync()
        self.assertEqual(counts["created"], 1)
        self.assertEqual(Ticket.objects.get().customer, self.kfc)

    def test_a_scoped_connector_refuses_a_company_outside_its_scope(self):
        self.zendesk.customers.add(self.kfc)
        FakeProvider.queued = [remote()]  # Sam is Pizza Hut's
        self.assertEqual(self.run_sync()["unmatched"], 1)

    def test_a_refused_token_marks_the_connector(self):
        FakeProvider.fail = ProviderError("401 from acme.zendesk.com", reauth=True)
        self.run_sync()
        self.zendesk.refresh_from_db()
        self.assertEqual(self.zendesk.status, "error")
        self.assertIn("401", self.zendesk.error)

    def test_the_command_syncs_every_connected_source(self):
        FakeProvider.queued = [remote()]
        with patch("services.connectors.sync.get_provider", return_value=FakeProvider()):
            call_command("sync_tickets")
        self.assertEqual(Ticket.objects.count(), 1)


class VisibilityTests(Fixture):
    def setUp(self):
        super().setUp()
        self.cs_ticket = Ticket.objects.create(
            customer=self.pizza,
            title="CS ticket",
            ticket_number="ZD-1",
            department="cs",
            priority="medium",
            opened_at=date(2026, 9, 10),
            connector=self.zendesk,
            external_id="1",
        )
        self.eng_ticket = Ticket.objects.create(
            customer=self.pizza,
            title="Eng ticket",
            ticket_number="SUP-1",
            department="engineering",
            priority="high",
            opened_at=date(2026, 9, 11),
        )
        self.open_ticket = Ticket.objects.create(
            customer=self.pizza,
            title="Raised here",
            ticket_number="TKT-1",
            priority="low",
            opened_at=date(2026, 9, 12),
        )

    def titles(self, user):
        self.client.force_authenticate(user)
        response = self.client.get(f"/api/v1/customers/{self.pizza.id}/tickets/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return sorted(row["title"] for row in response.data)

    def test_each_department_reads_its_own_tickets_plus_the_undeparted_ones(self):
        self.assertEqual(self.titles(self.csm), ["CS ticket", "Raised here"])
        self.assertEqual(self.titles(self.engineer), ["Eng ticket", "Raised here"])

    def test_leadership_reads_every_department(self):
        self.assertEqual(self.titles(self.leader), ["CS ticket", "Eng ticket", "Raised here"])

    def test_the_card_carries_its_source_and_department(self):
        self.client.force_authenticate(self.csm)
        row = next(
            r
            for r in self.client.get(f"/api/v1/customers/{self.pizza.id}/tickets/").data
            if r["title"] == "CS ticket"
        )
        self.assertEqual(
            (row["department"], row["department_display"], row["connector_name"]),
            ("cs", "Customer Success", "Zendesk"),
        )

    def test_the_ticket_dashboard_counts_only_what_the_viewer_may_read(self):
        self.client.force_authenticate(self.engineer)
        response = self.client.get("/api/v1/tickets/stats/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["kpis"]["total"], 2)

    def test_the_copilot_reads_tickets_under_the_askers_department(self):
        from services.copilot.retrieval import _gather_candidates

        def seen(user):
            return {
                c.source["label"]
                for c in _gather_candidates(self.pizza, viewer=user)
                if c.source["type"] == "ticket"
            }

        self.assertEqual(seen(self.engineer), {"SUP-1 Eng ticket", "TKT-1 Raised here"})
        self.assertEqual(
            seen(self.leader), {"ZD-1 CS ticket", "SUP-1 Eng ticket", "TKT-1 Raised here"}
        )


class ConnectViewTests(Fixture):
    def test_the_connector_never_exposes_its_credentials(self):
        self.client.force_authenticate(self.csm)
        row = next(
            r for r in self.client.get("/api/v1/connectors/").data if r["id"] == self.zendesk.id
        )
        self.assertNotIn("credentials", row)
        self.assertTrue(row["has_credentials"])
        self.assertEqual(row["department_display"], "Customer Success")
        self.assertEqual(row["setup"]["label"], "Zendesk")

    def test_a_connector_is_created_with_a_department(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            "/api/v1/connectors/", {"provider": "jira", "name": "Jira", "department": "engineering"}
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["department"], "engineering")
        self.assertEqual(response.data["status"], "not_connected")

    def test_connecting_verifies_with_the_provider_and_stores_the_secret_encrypted(self):
        self.client.force_authenticate(self.admin)
        jira = Connector.objects.create(
            organisation=self.org, provider="jira", name="Jira", department="engineering"
        )
        with patch(
            "services.connectors.providers.jira.http_json", return_value={"accountId": "abc"}
        ) as call:
            response = self.client.post(
                f"/api/v1/connectors/{jira.id}/connect/",
                {
                    "site": "acme",
                    "email": "eve@acme.io",
                    "api_token": "secret-token",
                    "project": "sup",
                },
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(call.call_args.args[1], "https://acme.atlassian.net/rest/api/3/myself")
        jira.refresh_from_db()
        self.assertEqual(jira.config, {"host": "acme.atlassian.net", "project": "SUP"})
        self.assertNotIn("secret-token", jira.credentials)
        self.assertEqual(jira.get_credentials()["api_token"], "secret-token")
        self.assertEqual(jira.status, "connected")
        self.assertTrue(AuditEvent.objects.filter(action="connector.connect").exists())

    def test_a_refused_credential_is_a_400_and_stores_nothing(self):
        self.client.force_authenticate(self.admin)
        jira = Connector.objects.create(organisation=self.org, provider="jira", name="Jira")
        with patch(
            "services.connectors.providers.jira.http_json",
            side_effect=ProviderError("401 from jira"),
        ):
            response = self.client.post(
                f"/api/v1/connectors/{jira.id}/connect/",
                {"site": "acme", "email": "e", "api_token": "t"},
            )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        jira.refresh_from_db()
        self.assertFalse(jira.has_credentials)

    def test_only_integration_managers_connect_sync_or_disconnect(self):
        self.client.force_authenticate(self.csm)
        self.assertEqual(
            self.client.post(f"/api/v1/connectors/{self.zendesk.id}/connect/", {}).status_code, 403
        )
        self.assertEqual(
            self.client.post(f"/api/v1/connectors/{self.zendesk.id}/sync/").status_code, 403
        )
        self.assertEqual(
            self.client.delete(f"/api/v1/connectors/{self.zendesk.id}/credentials/").status_code,
            403,
        )

    def test_an_attribution_only_system_cannot_be_connected(self):
        self.client.force_authenticate(self.admin)
        zoom = Connector.objects.create(organisation=self.org, provider="zoom", name="Zoom")
        response = self.client.post(f"/api/v1/connectors/{zoom.id}/connect/", {})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_sync_now_files_and_reports(self):
        self.client.force_authenticate(self.admin)
        FakeProvider.queued = [remote()]
        with patch("services.connectors.sync.get_provider", return_value=FakeProvider()):
            response = self.client.post(f"/api/v1/connectors/{self.zendesk.id}/sync/")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual((response.data["created"], response.data["ticket_count"]), (1, 1))

    def test_disconnecting_destroys_the_secret_and_keeps_the_tickets(self):
        FakeProvider.queued = [remote()]
        self.run_sync()
        self.client.force_authenticate(self.admin)
        response = self.client.delete(f"/api/v1/connectors/{self.zendesk.id}/credentials/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.zendesk.refresh_from_db()
        self.assertFalse(self.zendesk.has_credentials)
        self.assertEqual(self.zendesk.status, "not_connected")
        self.assertEqual(Ticket.objects.count(), 1)
        self.assertTrue(AuditEvent.objects.filter(action="connector.disconnect").exists())

    def test_another_organisations_connector_is_a_404(self):
        other = Organisation.objects.create(name="Other")
        theirs = Connector.objects.create(organisation=other, provider="zendesk", name="Z")
        self.client.force_authenticate(self.admin)
        self.assertEqual(
            self.client.post(f"/api/v1/connectors/{theirs.id}/connect/", {}).status_code, 404
        )


class InboundWebhookTests(Fixture):
    def connect_webhook(self):
        self.client.force_authenticate(self.admin)
        hook = Connector.objects.create(
            organisation=self.org, provider="webhook", name="Our helpdesk", department="engineering"
        )
        response = self.client.post(f"/api/v1/connectors/{hook.id}/connect/", {})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.client.force_authenticate(None)
        return hook, response.data["token"], response.data["inbound_url"]

    def test_connecting_mints_a_secret_shown_once(self):
        hook, token, url = self.connect_webhook()
        self.assertGreater(len(token), 30)
        self.assertTrue(url.endswith(f"/api/v1/connectors/{hook.id}/inbound/"))
        self.client.force_authenticate(self.admin)
        row = self.client.get(f"/api/v1/connectors/{hook.id}/").data
        self.assertNotIn("token", row)

    def test_a_source_posts_tickets_with_its_token(self):
        hook, token, _ = self.connect_webhook()
        body = {
            "tickets": [
                {
                    "external_id": "77",
                    "title": "Export broken",
                    "priority": "high",
                    "requester_email": "sam@pizzahut.com",
                },
                {
                    "external_id": "78",
                    "title": "Nobody we know",
                    "requester_email": "x@nowhere.org",
                },
            ]
        }
        response = self.client.post(
            f"/api/v1/connectors/{hook.id}/inbound/",
            body,
            format="json",
            HTTP_X_REVENACT_TOKEN=token,
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED, response.data)
        self.assertEqual(response.data, {"created": 1, "updated": 0, "unmatched": 1})
        ticket = Ticket.objects.get()
        self.assertEqual(
            (ticket.customer, ticket.department, ticket.ticket_number),
            (self.pizza, "engineering", "WH-77"),
        )

        again = self.client.post(
            f"/api/v1/connectors/{hook.id}/inbound/",
            {"external_id": "77", "title": "Export broken", "status": "resolved"},
            format="json",
            HTTP_X_REVENACT_TOKEN=token,
        )
        self.assertEqual(again.data["updated"], 1)
        self.assertEqual(Ticket.objects.get().status, "resolved")

    def test_a_signature_over_the_body_also_authenticates(self):
        hook, token, _ = self.connect_webhook()
        raw = json.dumps(
            {"external_id": "1", "title": "Signed", "requester_email": "sam@pizzahut.com"}
        ).encode()
        signature = hmac.new(token.encode(), raw, hashlib.sha256).hexdigest()
        response = self.client.generic(
            "POST",
            f"/api/v1/connectors/{hook.id}/inbound/",
            raw,
            content_type="application/json",
            HTTP_X_REVENACT_SIGNATURE=f"sha256={signature}",
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED, response.data)

    def test_a_bad_secret_is_a_404_and_is_audited(self):
        hook, token, _ = self.connect_webhook()
        response = self.client.post(
            f"/api/v1/connectors/{hook.id}/inbound/",
            {"external_id": "1", "title": "x"},
            format="json",
            HTTP_X_REVENACT_TOKEN="nope",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(Ticket.objects.count(), 0)
        self.assertTrue(AuditEvent.objects.filter(action="connector.inbound_rejected").exists())
        self.assertEqual(
            self.client.post(
                f"/api/v1/connectors/{hook.id}/inbound/", {}, format="json"
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                f"/api/v1/connectors/{self.zendesk.id}/inbound/",
                {},
                format="json",
                HTTP_X_REVENACT_TOKEN=token,
            ).status_code,
            404,
        )

    def test_a_malformed_ticket_is_a_400(self):
        hook, token, _ = self.connect_webhook()
        response = self.client.post(
            f"/api/v1/connectors/{hook.id}/inbound/",
            {"title": "no id"},
            format="json",
            HTTP_X_REVENACT_TOKEN=token,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("external_id", response.data["detail"])
