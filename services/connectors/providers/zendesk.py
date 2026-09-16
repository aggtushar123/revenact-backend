"""Zendesk Support over the incremental ticket export (cursor-based)."""

from datetime import timedelta

from django.utils import timezone

from .base import (
    ProviderError,
    RemoteTicket,
    SetupField,
    TicketProvider,
    as_date,
    basic_auth,
    http_json,
    tenant,
)

STATUS = {
    "new": "open",
    "open": "open",
    "pending": "in-progress",
    "hold": "on-hold",
    "solved": "resolved",
    "closed": "closed",
}
PRIORITY = {"urgent": "critical", "high": "high", "normal": "medium", "low": "low"}
FIRST_SYNC_DAYS = 90
MAX_PAGES = 10


class ZendeskProvider(TicketProvider):
    key = "zendesk"
    label = "Zendesk"
    number_prefix = "ZD"
    fields = [
        SetupField("subdomain", "Zendesk subdomain", placeholder="acme (from acme.zendesk.com)"),
        SetupField("email", "Agent email", placeholder="support-admin@company.com"),
        SetupField("api_token", "API token", secret=True),
    ]
    help = (
        "Admin Center › Apps and integrations › APIs › Zendesk API: "
        "enable token access and create a token."
    )

    def _headers(self, creds):
        return basic_auth(f"{creds['email']}/token", creds["api_token"])

    def connect(self, form):
        host = tenant(form.get("subdomain", ""), ".zendesk.com")
        email = (form.get("email") or "").strip()
        token = (form.get("api_token") or "").strip()
        if not email or not token:
            raise ProviderError("Agent email and API token are required.")
        creds = {"email": email, "api_token": token}
        me = http_json("GET", f"https://{host}/api/v2/users/me.json", headers=self._headers(creds))
        if not (me.get("user") or {}).get("id"):
            raise ProviderError("Zendesk did not recognise that email and token.")
        return {"host": host}, creds

    def fetch_tickets(self, config, creds, cursor):
        host = config["host"]
        base = f"https://{host}/api/v2/incremental/tickets/cursor.json"
        if cursor:
            url = f"{base}?cursor={cursor}&include=users"
        else:
            start = int((timezone.now() - timedelta(days=FIRST_SYNC_DAYS)).timestamp())
            url = f"{base}?start_time={start}&include=users"
        out, users = [], {}
        for _ in range(MAX_PAGES):
            page = http_json("GET", url, headers=self._headers(creds))
            users.update({u["id"]: u for u in page.get("users", []) if u.get("id")})
            for raw in page.get("tickets", []):
                ticket = self._ticket(host, raw, users)
                if ticket is not None:
                    out.append(ticket)
            cursor = page.get("after_cursor") or cursor
            if page.get("end_of_stream", True) or not page.get("after_cursor"):
                break
            url = f"{base}?cursor={cursor}&include=users"
        return out, cursor or "", creds

    def _ticket(self, host, raw, users):
        if raw.get("status") == "deleted":
            return None
        requester = users.get(raw.get("requester_id")) or {}
        assignee = users.get(raw.get("assignee_id")) or {}
        status = STATUS.get(raw.get("status"), "open")
        return RemoteTicket(
            external_id=str(raw["id"]),
            number=f"{self.number_prefix}-{raw['id']}",
            title=(raw.get("subject") or raw.get("raw_subject") or "(no subject)")[:255],
            description=raw.get("description") or "",
            status=status,
            priority=PRIORITY.get(raw.get("priority"), "medium"),
            requester_email=(requester.get("email") or "").lower(),
            requester_name=requester.get("name") or "",
            assignee_name=assignee.get("name") or "",
            url=f"https://{host}/agent/tickets/{raw['id']}",
            opened_at=as_date(raw.get("created_at")),
            resolved_at=as_date(raw.get("updated_at"))
            if status in ("resolved", "closed")
            else None,
        )
