"""The MCP endpoint: JSON-RPC 2.0 over one POST.

No new dependency. MCP is JSON-RPC, and the three methods a read-only
server needs — `initialize`, `tools/list`, `tools/call` — are a request
in and a response out. A notification (no id) is answered with nothing,
as the protocol asks.

Authentication is a personal token, and the token is the person: every
tool runs under their own visibility. An agent holding Dana's token reads
Dana's book and no further.
"""

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core import audit

from . import tools
from .models import McpToken

PROTOCOL_VERSION = "2025-06-18"
SERVER = {"name": "revenact", "version": "1.0.0"}

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603


def _result(rpc_id, result):
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _error(rpc_id, code, message):
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


class McpView(APIView):
    """POST /api/v1/mcp/ — the server itself.

    Authenticated by `Authorization: Bearer <token>` rather than a session
    or a short-lived JWT: an agent runs unattended, and a token its owner
    can see and revoke is the honest way to let it in."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request):
        header = request.headers.get("Authorization", "")
        raw = header[7:].strip() if header.lower().startswith("bearer ") else ""
        token = McpToken.resolve(raw)
        if token is None:
            return Response(
                {"detail": "A live MCP token is required."}, status=status.HTTP_401_UNAUTHORIZED
            )
        token.touch()

        payload = request.data
        if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
            return Response(_error(None, INVALID_REQUEST, "Expected a JSON-RPC 2.0 request."))
        method = payload.get("method")
        rpc_id = payload.get("id")
        if not isinstance(method, str):
            return Response(_error(rpc_id, INVALID_REQUEST, "A method name is required."))
        # A notification asks for nothing back.
        if rpc_id is None:
            return Response(status=status.HTTP_204_NO_CONTENT)

        params = payload.get("params") or {}
        if method == "initialize":
            return Response(
                _result(
                    rpc_id,
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": SERVER,
                        "instructions": (
                            "Revenact's own data, read as the person whose token you hold. "
                            "Every answer is limited to what they may see in the app."
                        ),
                    },
                )
            )
        if method == "tools/list":
            return Response(_result(rpc_id, {"tools": tools.describe()}))
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not isinstance(name, str):
                return Response(_error(rpc_id, INVALID_REQUEST, "A tool name is required."))
            result = tools.run(name, token.user, arguments)
            # SOC2:LOG-01 somebody else's agent read this company's records
            audit.record(
                "mcp.tool_called",
                actor=token.user,
                organisation=token.user.organisation,
                target=token,
                outcome="failure" if result["isError"] else "success",
                metadata={"tool": name, "label": token.label, "arguments": sorted(arguments)},
            )
            return Response(_result(rpc_id, result))
        return Response(_error(rpc_id, METHOD_NOT_FOUND, f"No method {method!r}."))


class McpTokenView(APIView):
    """GET/POST /api/v1/mcp/tokens/ — a person's own keys.

    The secret comes back once, on the POST that made it. After that it
    exists only as a hash: nobody can recover it, and the honest answer to
    a lost token is a new one."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        rows = McpToken.objects.filter(user=request.user, revoked_at__isnull=True)
        return Response(
            [
                {
                    "id": row.id,
                    "label": row.label,
                    "hint": row.hint,
                    "last_used_at": row.last_used_at,
                    "created_at": row.created_at,
                }
                for row in rows
            ]
        )

    def post(self, request):
        label = (request.data.get("label") or "").strip()
        if not label:
            return Response({"detail": "Give it a name you will recognise."}, status=400)
        row, secret = McpToken.issue(request.user, label)
        audit.record("mcp.token_issued", request=request, target=row, metadata={"label": row.label})
        return Response(
            {
                "id": row.id,
                "label": row.label,
                "hint": row.hint,
                "created_at": row.created_at,
                # The only time this is ever returned.
                "token": secret,
            },
            status=status.HTTP_201_CREATED,
        )


class McpTokenDetailView(APIView):
    """DELETE /api/v1/mcp/tokens/<id>/ — revoke your own."""

    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        row = McpToken.objects.filter(user=request.user, pk=pk, revoked_at__isnull=True).first()
        if row is None:
            return Response({"detail": "No such token of yours."}, status=404)
        from django.utils import timezone

        row.revoked_at = timezone.now()
        row.save(update_fields=["revoked_at"])
        audit.record(
            "mcp.token_revoked", request=request, target=row, metadata={"label": row.label}
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
