"""The audit-log primitive (SOC2:LOG-01, LOG-02).

    from core import audit
    audit.record("user.deactivate", request=request, target=user)

Writes one AuditEvent row and one line on the `core.audit` logger (so the
event also reaches wherever the container's stdout is shipped, giving a
second, off-box copy). Actor and organisation default to the request's
user. Never pass credentials in `metadata` — the obvious keys are dropped.

Action names are `<area>.<verb>`; the catalogue lives in
docs/audit-events.md and grows as views start emitting new ones.
"""

import logging

from rest_framework.settings import api_settings

from core.middleware import get_request_id
from core.models import AuditEvent

logger = logging.getLogger("core.audit")

_FORBIDDEN_METADATA_KEYS = {
    "password",
    "new_password",
    "current_password",
    "token",
    "access",
    "refresh",
    "secret",
    "authorization",
    "api_key",
}


def client_ip(request):
    """Same rule DRF's throttles use (NUM_PROXIES): behind Caddy the real
    client is the last X-Forwarded-For hop the proxy appended; with no proxy
    configured, REMOTE_ADDR."""
    if request is None:
        return None
    num_proxies = api_settings.NUM_PROXIES
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if num_proxies and forwarded:
        hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        if hops:
            return hops[-num_proxies] if len(hops) >= num_proxies else hops[0]
    return request.META.get("REMOTE_ADDR") or None


def record(
    action,
    *,
    request=None,
    actor=None,
    organisation=None,
    target=None,
    outcome=AuditEvent.Outcome.SUCCESS,
    metadata=None,
):
    if actor is None and request is not None:
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            actor = user
    if organisation is None and actor is not None:
        organisation = getattr(actor, "organisation", None)

    clean_metadata = {
        key: value
        for key, value in (metadata or {}).items()
        if key.lower() not in _FORBIDDEN_METADATA_KEYS
    }

    event = AuditEvent.objects.create(
        organisation=organisation,
        actor=actor,
        actor_email=getattr(actor, "email", "") or "",
        action=action,
        target_type=target._meta.label_lower if target is not None else "",
        target_id=str(target.pk) if target is not None and target.pk is not None else "",
        target_repr=str(target)[:255] if target is not None else "",
        outcome=outcome,
        ip=client_ip(request),
        user_agent=(request.META.get("HTTP_USER_AGENT", "") if request is not None else "")[:255],
        request_id=get_request_id(),
        metadata=clean_metadata,
    )
    logger.info(
        "audit %s %s",
        action,
        outcome,
        extra={
            "audit": {
                "id": event.pk,
                "action": action,
                "outcome": outcome,
                "actor": event.actor_email or None,
                "organisation_id": event.organisation_id,
                "target": f"{event.target_type}:{event.target_id}" if event.target_type else None,
                "ip": event.ip,
                "metadata": clean_metadata,
            }
        },
    )
    return event
