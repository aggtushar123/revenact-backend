"""The management brief, delivered to Slack on a schedule.

Posting uses the same hardened outbound path the webhooks app already
has — stdlib urllib, no redirects, and the SSRF check in
`services.webhooks.engine` — rather than a second way of leaving the
building. Nothing is generated here: the pass posts the brief that
exists, so a schedule never quietly spends a model call.
"""

import calendar
import json
import urllib.error
import urllib.request
from urllib.parse import urlparse

from django.utils import timezone

from core import audit
from services.webhooks.engine import (
    UnsafeWebhookURLError,
    _opener,
    validate_webhook_url,
)

from .models import Brief, BriefSchedule

TIMEOUT_SECONDS = 10
#: Slack's own incoming-webhook host. Anything else is not a Slack hook,
#: whatever it claims, and a brief is not something to post at a guess.
SLACK_HOST = "hooks.slack.com"


class NotASlackHook(Exception):
    """The destination is not somewhere a brief may be posted."""


def check_destination(url: str) -> None:
    """Raises NotASlackHook unless this is a Slack incoming webhook we can
    safely reach."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme != "https" or parsed.hostname != SLACK_HOST:
        raise NotASlackHook("That must be an https://hooks.slack.com/... webhook URL.")
    if not parsed.path.startswith("/services/"):
        raise NotASlackHook("That does not look like a Slack incoming webhook.")
    try:
        validate_webhook_url(url)
    except UnsafeWebhookURLError as exc:
        raise NotASlackHook(str(exc)) from exc


def _post(url: str, payload: dict):
    """(delivered, status, error). Never raises for a refusal: a channel
    that has gone away is news for the caller, not an exception.

    Sent through the webhooks app's own opener, which refuses redirects —
    the destination is pinned to Slack's host before this runs, and a
    redirect away from it is the one way that could stop being true."""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "Revenact-Briefs/1.0"},
        method="POST",
    )
    try:
        with _opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            return 200 <= response.status < 300, response.status, ""
    except urllib.error.HTTPError as exc:
        return False, exc.code, exc.reason or ""
    except (urllib.error.URLError, TimeoutError) as exc:
        return False, None, str(exc)[:255]


def as_slack(brief: Brief) -> dict:
    """The brief as Slack will show it: the headline, the body, and what to
    watch. Plain text in one block, because a brief is prose."""
    lines = [f"*{brief.headline}*", "", brief.body.strip()]
    if brief.watch:
        lines += ["", "*Watching*"] + [f"• {item}" for item in brief.watch]
    lines += ["", f"_Figures as of {brief.as_of:%d %b %Y}._"]
    return {"text": "\n".join(lines)}


def latest_brief(organisation):
    return Brief.objects.filter(organisation=organisation).order_by("-as_of", "-id").first()


def send(schedule, *, actor=None, request=None, now=None):
    """Post the latest brief. Returns (sent, detail).

    `now` is the moment the caller is reasoning about, stamped on the
    schedule when the post lands: a pass that decided a schedule was due
    at 8am on Monday must record Monday, or it will decide the same thing
    again an hour later."""
    brief = latest_brief(schedule.organisation)
    if brief is None:
        return False, "No brief has been written yet."
    check_destination(schedule.destination)
    delivered, status, error = _post(schedule.destination, as_slack(brief))
    if not delivered:
        return False, error or f"Slack refused it ({status})."
    schedule.last_sent_at = now or timezone.now()
    schedule.save(update_fields=["last_sent_at"])
    audit.record(
        "brief.sent",
        request=request,
        actor=actor,
        organisation=schedule.organisation,
        target=schedule,
        metadata={"as_of": str(brief.as_of), "to": schedule.destination_hint},
    )
    return True, "Sent."


def _due(schedule, now) -> bool:
    """Is this schedule due, and not already sent today?

    Day granularity, because the job that asks runs once a night: a
    schedule is due on its day, and `last_sent_at` keeps a second run the
    same day from posting twice."""
    local = timezone.localtime(now)
    if schedule.last_sent_at and timezone.localtime(schedule.last_sent_at).date() == local.date():
        return False
    if schedule.cadence == BriefSchedule.Cadence.WEEKLY:
        return local.weekday() == schedule.weekday
    # A month too short for the chosen day sends on its last day rather
    # than skipping the month entirely.
    last = calendar.monthrange(local.year, local.month)[1]
    return local.day == min(schedule.day, last)


def send_due(now=None) -> int:
    """The scheduled pass. One organisation failing never stops the rest:
    a brief that did not go out is worth knowing about, not worth taking
    the nightly job down for."""
    now = now or timezone.now()
    sent = 0
    for schedule in BriefSchedule.objects.filter(is_active=True).select_related("organisation"):
        if not _due(schedule, now):
            continue
        try:
            delivered, _ = send(schedule, now=now)
        except Exception:  # noqa: BLE001 - one channel's problem is its own
            continue
        if delivered:
            sent += 1
    return sent
