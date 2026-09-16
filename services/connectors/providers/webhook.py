"""Any other system: it POSTs tickets to us.

Connecting mints a shared secret, shown once. The source sends JSON to
`/api/v1/connectors/<id>/inbound/` with either `X-Revenact-Token: <secret>`
or `X-Revenact-Signature: <hex hmac-sha256 of the body with the secret>`.
Nothing is pulled, so `fetch_tickets` has nothing to do.
"""

import secrets

from .base import RemoteTicket, TicketProvider, as_date, first_line

STATUSES = {"open", "in-progress", "on-hold", "resolved", "closed"}
PRIORITIES = {"critical", "high", "medium", "low"}


class WebhookProvider(TicketProvider):
    key = "webhook"
    label = "Any other system (webhook)"
    number_prefix = "WH"
    fields = []
    help = "We give you a URL and a secret; your system POSTs tickets to it as JSON."

    def connect(self, form):
        return {}, {"token": secrets.token_urlsafe(32)}

    def fetch_tickets(self, config, creds, cursor):
        return [], cursor, creds


def parse_inbound(payload: dict) -> RemoteTicket:
    """One inbound JSON object → RemoteTicket. Raises ValueError on a bad one."""
    if not isinstance(payload, dict):
        raise ValueError("each ticket must be an object")
    external_id = str(payload.get("external_id") or payload.get("id") or "").strip()
    title = str(payload.get("title") or payload.get("subject") or "").strip()
    if not external_id or not title:
        raise ValueError("external_id and title are required")
    status = str(payload.get("status") or "open").lower().replace("_", "-")
    priority = str(payload.get("priority") or "medium").lower()
    return RemoteTicket(
        external_id=external_id[:128],
        number=str(payload.get("number") or f"WH-{external_id}")[:32],
        title=first_line(title) or title[:255],
        description=str(payload.get("description") or ""),
        status=status if status in STATUSES else "open",
        priority=priority if priority in PRIORITIES else "medium",
        requester_email=str(payload.get("requester_email") or "").strip().lower()[:254],
        requester_name=str(payload.get("requester_name") or "")[:150],
        assignee_name=str(payload.get("assignee_name") or "")[:150],
        url=str(payload.get("url") or "")[:500],
        opened_at=as_date(payload.get("opened_at")),
        resolved_at=as_date(payload.get("resolved_at")),
    )
