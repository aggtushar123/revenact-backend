"""Validates webhook URLs and actually delivers them. No `requests`
dependency (not in requirements.txt) — stdlib `urllib` is enough for a
single POST.

SSRF is the real risk a "call any URL an admin types in" feature
carries: without a check, that URL field lets this server be used to
probe its own internal network (cloud metadata endpoints, internal
services with no auth of their own, etc.). `validate_webhook_url`
rejects anything that isn't a resolvable public host, called both at
save time (WebhookSubscriptionSerializer) and immediately before each
send (DNS can change between the two). `_NoRedirectHandler` closes the
other common bypass — a URL that's safe at connect time but 302s
somewhere private — by refusing to follow any redirect at all rather
than silently trusting it. This is a real, meaningful barrier, not a
complete one: a host that resolves differently depending on which of
this process's own DNS lookups happens to land isn't caught by either
check. Good enough for what this feature actually needs to defend
against; a webhook platform taking outbound traffic at real scale
would want a dedicated egress proxy instead of relying on app-level
checks like these alone.
"""

import hashlib
import hmac
import ipaddress
import json
import socket
import urllib.error
import urllib.request
from urllib.parse import urlparse

TIMEOUT_SECONDS = 5


class UnsafeWebhookURLError(ValueError):
    pass


def validate_webhook_url(url):
    """Raises UnsafeWebhookURLError if `url` isn't a safe outbound
    target — see this module's own docstring."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeWebhookURLError("Only http:// and https:// URLs are allowed.")
    if not parsed.hostname:
        raise UnsafeWebhookURLError("That URL has no host.")

    try:
        resolved = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise UnsafeWebhookURLError("That host name couldn't be resolved.") from exc

    for info in resolved:
        addr = ipaddress.ip_address(info[4][0])
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
        ):
            raise UnsafeWebhookURLError(
                "That URL resolves to a private or internal address, which isn't allowed."
            )


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirectHandler)


def send_webhook(webhook, event, payload):
    """POSTs `payload` to `webhook.url`, signed with `webhook.secret`
    via HMAC-SHA256 in an X-Revenact-Signature header (hex digest of
    the raw JSON body) so the receiving end can verify authenticity.
    Records a WebhookDelivery either way — a delivery failure is data,
    never an exception that would break whatever action triggered it
    (creating a Customer, etc.)."""
    from .models import WebhookDelivery

    try:
        validate_webhook_url(webhook.url)
    except UnsafeWebhookURLError as exc:
        WebhookDelivery.objects.create(webhook=webhook, success=False, error=str(exc)[:255])
        return

    body = json.dumps({"event": event, "data": payload}).encode("utf-8")
    signature = hmac.new(webhook.secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    request = urllib.request.Request(
        webhook.url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Revenact-Signature": signature,
            "User-Agent": "Revenact-Webhooks/1.0",
        },
        method="POST",
    )
    try:
        with _opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            status = response.status
            WebhookDelivery.objects.create(
                webhook=webhook, success=200 <= status < 300, status_code=status
            )
    except urllib.error.HTTPError as exc:
        WebhookDelivery.objects.create(
            webhook=webhook, success=False, status_code=exc.code, error=str(exc.reason)[:255]
        )
    except Exception as exc:  # noqa: BLE001 — any network failure becomes a logged
        # delivery, never a crash of whatever triggered this send.
        WebhookDelivery.objects.create(webhook=webhook, success=False, error=str(exc)[:255])
