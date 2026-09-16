"""Freshdesk over its REST API: tickets updated since the last pass."""

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
    parse_datetime,
    tenant,
)

STATUS = {2: "open", 3: "in-progress", 4: "resolved", 5: "closed"}
PRIORITY = {1: "low", 2: "medium", 3: "high", 4: "critical"}
FIRST_SYNC_DAYS = 90
PAGE = 100
MAX_PAGES = 10


class FreshdeskProvider(TicketProvider):
    key = "freshdesk"
    label = "Freshdesk"
    number_prefix = "FD"
    fields = [
        SetupField("domain", "Freshdesk domain", placeholder="acme (from acme.freshdesk.com)"),
        SetupField("api_key", "API key", secret=True),
    ]
    help = "Profile settings › View API key."

    def _headers(self, creds):
        return basic_auth(creds["api_key"], "X")

    def connect(self, form):
        host = tenant(form.get("domain", ""), ".freshdesk.com")
        key = (form.get("api_key") or "").strip()
        if not key:
            raise ProviderError("API key is required.")
        creds = {"api_key": key}
        me = http_json("GET", f"https://{host}/api/v2/agents/me", headers=self._headers(creds))
        if not me.get("id"):
            raise ProviderError("Freshdesk did not recognise that API key.")
        return {"host": host}, creds

    def fetch_tickets(self, config, creds, cursor):
        host = config["host"]
        since = parse_datetime(cursor) or (timezone.now() - timedelta(days=FIRST_SYNC_DAYS))
        out, newest = [], since
        for page in range(1, MAX_PAGES + 1):
            url = (
                f"https://{host}/api/v2/tickets?updated_since={since.isoformat()}"
                f"&include=requester&order_by=updated_at&order_type=asc&per_page={PAGE}&page={page}"
            )
            rows = http_json("GET", url, headers=self._headers(creds))
            if not isinstance(rows, list):
                raise ProviderError("Freshdesk answered with something other than a ticket list.")
            for raw in rows:
                out.append(self._ticket(host, raw))
                updated = parse_datetime(raw.get("updated_at"))
                if updated and updated > newest:
                    newest = updated
            if len(rows) < PAGE:
                break
        return out, newest.isoformat(), creds

    def _ticket(self, host, raw):
        requester = raw.get("requester") or {}
        status = STATUS.get(raw.get("status"), "open")
        return RemoteTicket(
            external_id=str(raw["id"]),
            number=f"{self.number_prefix}-{raw['id']}",
            title=(raw.get("subject") or "(no subject)")[:255],
            description=raw.get("description_text") or "",
            status=status,
            priority=PRIORITY.get(raw.get("priority"), "medium"),
            requester_email=(requester.get("email") or "").lower(),
            requester_name=requester.get("name") or "",
            url=f"https://{host}/a/tickets/{raw['id']}",
            opened_at=as_date(raw.get("created_at")),
            resolved_at=as_date(raw.get("updated_at"))
            if status in ("resolved", "closed")
            else None,
        )
