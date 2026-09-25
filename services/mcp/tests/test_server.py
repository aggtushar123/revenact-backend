"""The MCP server: someone else's agent, reading as the person whose token
it holds and never wider."""

import json
from decimal import Decimal
from unittest.mock import patch

from django.utils import timezone
from rest_framework.test import APITestCase

from core.models import AuditEvent
from services.accounts.models import Organisation, User
from services.customers.models import Customer, Email, Note
from services.mcp.models import McpToken

RPC = "/api/v1/mcp/"
TOKENS = "/api/v1/mcp/tokens/"


def call(method, params=None, rpc_id=1):
    return {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params or {}}


class Fixture(APITestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Acme Inc")
        mk = lambda email, name, **kw: User.objects.create_user(  # noqa: E731
            email=email, password="x", name=name, organisation=self.org, **kw
        )
        self.alice = mk("alice@acme.io", "Alice", role=User.Role.ADMIN)
        self.dana = mk("dana@acme.io", "Dana", function=User.Function.CS)
        self.eve = mk("eve@acme.io", "Eve", function=User.Function.CS)
        self.pizza = Customer.objects.create(
            organisation=self.org,
            name="Pizza Hut",
            domain="pizzahut.com",
            owner=self.dana,
            arr_billed_at_account=Decimal("120000"),
            health_score=Decimal("4.5"),
        )
        self.burger = Customer.objects.create(
            organisation=self.org, name="Burger King", domain="bk.com", owner=self.eve
        )
        self.raw = McpToken.issue(self.dana, label="Dana's agent")[1]

    def rpc(self, payload, token=None):
        return self.client.post(
            RPC,
            payload,
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {token if token is not None else self.raw}",
        )

    def tool(self, name, arguments=None, token=None):
        response = self.rpc(call("tools/call", {"name": name, "arguments": arguments or {}}), token)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data


class Tokens(Fixture):
    def test_a_person_issues_their_own_token_and_sees_it_once(self):
        self.client.force_authenticate(self.alice)
        response = self.client.post(TOKENS, {"label": "Claude Desktop"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        secret = response.data["token"]
        self.assertTrue(secret.startswith("rvn_mcp_"))
        self.assertEqual(response.data["label"], "Claude Desktop")
        # Listing never shows it again.
        rows = self.client.get(TOKENS).data
        self.assertEqual(len(rows), 1)
        self.assertNotIn("token", rows[0])
        self.assertTrue(rows[0]["hint"].endswith(secret[-4:]))
        self.assertTrue(AuditEvent.objects.filter(action="mcp.token_issued").exists())

    def test_it_is_stored_hashed_never_in_the_clear(self):
        self.client.force_authenticate(self.alice)
        secret = self.client.post(TOKENS, {"label": "x"}, format="json").data["token"]
        row = McpToken.objects.get(user=self.alice)
        self.assertNotIn(secret, json.dumps(list(McpToken.objects.values()), default=str))
        self.assertNotEqual(row.token_hash, secret)

    def test_only_your_own_tokens_are_listed_and_revoked(self):
        self.client.force_authenticate(self.alice)
        mine = self.client.post(TOKENS, {"label": "mine"}, format="json").data["id"]
        self.client.force_authenticate(self.dana)
        self.assertEqual([row["label"] for row in self.client.get(TOKENS).data], ["Dana's agent"])
        self.assertEqual(self.client.delete(f"{TOKENS}{mine}/").status_code, 404)
        self.client.force_authenticate(self.alice)
        self.assertEqual(self.client.delete(f"{TOKENS}{mine}/").status_code, 204)
        self.assertTrue(AuditEvent.objects.filter(action="mcp.token_revoked").exists())

    def test_a_revoked_token_stops_working(self):
        response = self.rpc(call("tools/list"))
        self.assertEqual(response.status_code, 200)
        McpToken.objects.filter(user=self.dana).update(revoked_at=timezone.now())
        self.assertEqual(self.rpc(call("tools/list")).status_code, 401)


class Handshake(Fixture):
    def test_initialize_names_the_server_and_its_tools(self):
        response = self.rpc(call("initialize", {"protocolVersion": "2025-06-18"}))
        self.assertEqual(response.status_code, 200, response.data)
        result = response.data["result"]
        self.assertEqual(result["serverInfo"]["name"], "revenact")
        self.assertIn("tools", result["capabilities"])
        self.assertEqual(response.data["jsonrpc"], "2.0")

    def test_tools_list_describes_each_tool(self):
        result = self.rpc(call("tools/list")).data["result"]
        names = {tool["name"] for tool in result["tools"]}
        self.assertIn("search_companies", names)
        self.assertIn("ask_copilot", names)
        for tool in result["tools"]:
            self.assertTrue(tool["description"])
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_no_token_and_a_wrong_token_are_both_401(self):
        self.assertEqual(self.client.post(RPC, call("tools/list"), format="json").status_code, 401)
        self.assertEqual(self.rpc(call("tools/list"), token="rvn_mcp_nonsense").status_code, 401)

    def test_an_unknown_method_is_a_json_rpc_error_not_a_500(self):
        response = self.rpc(call("tools/dance"))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["error"]["code"], -32601)
        self.assertEqual(response.data["id"], 1)

    def test_a_malformed_envelope_is_a_json_rpc_error(self):
        response = self.client.post(
            RPC, {"not": "jsonrpc"}, format="json", HTTP_AUTHORIZATION=f"Bearer {self.raw}"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["error"]["code"], -32600)

    def test_a_notification_gets_no_answer(self):
        response = self.client.post(
            RPC,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.raw}",
        )
        self.assertEqual(response.status_code, 204)


class Tools(Fixture):
    def setUp(self):
        super().setUp()
        Note.objects.create(
            customer=self.pizza,
            author=self.dana,
            title="Renewal",
            logged_at=timezone.now(),
            body="They want the multi-year option.",
        )

    def test_search_companies_finds_only_the_persons_own_book(self):
        result = self.tool("search_companies", {"query": "u"})
        names = [row["name"] for row in json.loads(result["result"]["content"][0]["text"])]
        self.assertIn("Pizza Hut", names)
        self.assertNotIn("Burger King", names)

    def test_get_company_answers_with_the_figures(self):
        result = self.tool("get_company", {"id": self.pizza.id})
        company = json.loads(result["result"]["content"][0]["text"])
        self.assertEqual(company["name"], "Pizza Hut")
        self.assertEqual(company["owner"], "Dana")
        self.assertEqual(Decimal(company["arr"]), Decimal("120000"))

    def test_a_company_outside_the_book_is_an_error_not_a_leak(self):
        result = self.tool("get_company", {"id": self.burger.id})
        self.assertTrue(result["result"]["isError"])
        self.assertNotIn("Burger King", json.dumps(result))

    def test_recent_interactions_reads_only_what_that_person_may_read(self):
        Email.objects.create(
            customer=self.pizza,
            subject="Hidden",
            body="Not for Dana.",
            sent_at=timezone.now(),
            mailbox_owner=self.eve,
        )
        Email.objects.create(
            customer=self.pizza, subject="Shared", body="For anybody.", sent_at=timezone.now()
        )
        result = self.tool("recent_interactions", {"company_id": self.pizza.id})
        text = result["result"]["content"][0]["text"]
        self.assertIn("Shared", text)
        self.assertNotIn("Hidden", text)
        self.assertNotIn("Not for Dana", text)

    @patch("services.mcp.tools.get_completion", return_value="They want a multi-year deal.")
    def test_ask_copilot_answers_as_that_person_and_cites(self, completion):
        result = self.tool("ask_copilot", {"question": "What does Pizza Hut want?"})
        text = result["result"]["content"][0]["text"]
        self.assertIn("multi-year", text)
        self.assertEqual(completion.call_args.kwargs["purpose"], "mcp")
        self.assertTrue(AuditEvent.objects.filter(action="mcp.tool_called").exists())

    def test_an_unknown_tool_is_an_error_result(self):
        result = self.tool("delete_everything", {})
        self.assertTrue(result["result"]["isError"])

    def test_a_tool_called_with_the_wrong_arguments_says_so(self):
        result = self.tool("get_company", {})
        self.assertTrue(result["result"]["isError"])
        self.assertIn("id", result["result"]["content"][0]["text"])

    def test_every_call_is_audited_with_the_tool_and_the_token(self):
        self.tool("search_companies", {"query": "pizza"})
        event = AuditEvent.objects.filter(action="mcp.tool_called").latest("created_at")
        self.assertEqual(event.metadata["tool"], "search_companies")
        self.assertIn("label", event.metadata)

    def test_another_tenant_is_never_reachable(self):
        other = Organisation.objects.create(name="Other")
        outsider = User.objects.create_user(
            email="o@other.io", password="x", name="O", organisation=other, role=User.Role.ADMIN
        )
        theirs = Customer.objects.create(
            organisation=other, name="Wendy's", domain="wendys.com", owner=outsider
        )
        result = self.tool("get_company", {"id": theirs.id})
        self.assertTrue(result["result"]["isError"])


class WhenSomebodyLeaves(Fixture):
    """A key is its owner's access. Someone who has lost their access has
    lost the key's access too, the moment they lose it."""

    def test_a_deactivated_persons_key_stops_working_at_once(self):
        self.assertEqual(self.rpc(call("tools/list")).status_code, 200)
        self.dana.is_active = False
        self.dana.save(update_fields=["is_active"])
        self.assertEqual(self.rpc(call("tools/list")).status_code, 401)

    def test_deactivating_someone_revokes_their_keys(self):
        self.client.force_authenticate(self.alice)
        response = self.client.patch(
            f"/api/v1/auth/users/{self.dana.id}/", {"is_active": False}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            McpToken.objects.filter(user=self.dana, revoked_at__isnull=True).count(), 0
        )
        # And still refused after they are brought back: a revoked key is
        # revoked, not paused.
        self.dana.is_active = True
        self.dana.save(update_fields=["is_active"])
        self.assertEqual(self.rpc(call("tools/list")).status_code, 401)


class WhenTheModelIsUnavailable(Fixture):
    @patch(
        "services.mcp.tools.get_completion",
        side_effect=__import__(
            "services.copilot.anthropic_client", fromlist=["BudgetExceeded"]
        ).BudgetExceeded("spent"),
    )
    def test_a_spent_budget_is_a_tool_error_not_a_crash(self, completion):
        result = self.tool("ask_copilot", {"question": "What does Pizza Hut want?"})
        self.assertTrue(result["result"]["isError"])
        # The reason travels: an agent can tell "out of budget" from
        # "no such company" and act differently.
        self.assertIn("spent", result["result"]["content"][0]["text"])
        # Still audited: a call that failed is a call that happened.
        self.assertTrue(
            AuditEvent.objects.filter(action="mcp.tool_called", outcome="failure").exists()
        )


class MoreTools(Fixture):
    def test_feature_requests_and_anomalies_come_back_scoped(self):
        from services.anomalies.models import Anomaly, AnomalyEvidence
        from services.requests.models import FeatureRequest, RequestEvidence

        feature = FeatureRequest.objects.create(organisation=self.org, title="Slack alerts")
        RequestEvidence.objects.create(
            request=feature,
            organisation=self.org,
            kind="ticket",
            record_id=1,
            customer=self.pizza,
            snippet="Slack please",
            occurred_at=timezone.now(),
        )
        # On a company Dana cannot open: she must not see this one at all.
        hidden = FeatureRequest.objects.create(organisation=self.org, title="Hidden ask")
        RequestEvidence.objects.create(
            request=hidden,
            organisation=self.org,
            kind="ticket",
            record_id=2,
            customer=self.burger,
            snippet="Not hers",
            occurred_at=timezone.now(),
        )
        anomaly = Anomaly.objects.create(
            organisation=self.org,
            title="SSO login failures",
            first_seen_at=timezone.now(),
            last_seen_at=timezone.now(),
        )
        AnomalyEvidence.objects.create(
            anomaly=anomaly,
            organisation=self.org,
            kind="ticket",
            record_id=3,
            customer=self.pizza,
            snippet="SSO broken",
            occurred_at=timezone.now(),
        )

        asks = json.loads(self.tool("list_feature_requests")["result"]["content"][0]["text"])
        self.assertEqual([row["title"] for row in asks], ["Slack alerts"])
        clusters = json.loads(self.tool("list_anomalies")["result"]["content"][0]["text"])
        # The stored title was written from reports across the whole
        # organisation; Dana does not see every account, so hers is built
        # from fields and counts only her own companies.
        self.assertEqual(
            [row["title"] for row in clusters], ["Similar reports across 1 of your companies"]
        )

        from services.accounts.capabilities import Capability
        from services.accounts.models import Role

        lead = Role.objects.create(
            organisation=self.org,
            name="Lead",
            slug="lead",
            permissions=[Capability.VIEW_ALL_ACCOUNTS],
        )
        self.alice.role = lead
        self.alice.save(update_fields=["role"])
        leader = McpToken.issue(self.alice, label="Alice's agent")[1]
        clusters = json.loads(
            self.tool("list_anomalies", token=leader)["result"]["content"][0]["text"]
        )
        self.assertEqual([row["title"] for row in clusters], ["SSO login failures"])
