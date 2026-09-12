"""Questions that age.

An open question is a promise someone has not kept yet. After `STALE_DAYS`
it is stale: the Brain counts it, the overview lists it, and the person
asked is reminded — once a day, not on every run, so a nightly job does
not become nagging. Answering is the only thing that clears it.
"""

from datetime import timedelta

from django.utils import timezone

from services.notifications.models import Notification
from services.notifications.realtime import notify

from .models import Question

STALE_DAYS = 3
NUDGE_EVERY = timedelta(hours=24)


def open_questions(organisation):
    return Question.objects.filter(
        organisation=organisation, status=Question.Status.OPEN
    ).select_related("asked_by", "assignee", "customer")


def stale_open_questions(organisation, days=STALE_DAYS, now=None):
    now = now or timezone.now()
    return open_questions(organisation).filter(created_at__lte=now - timedelta(days=days))


def nudge(organisation, days=STALE_DAYS, *, dry_run=False, now=None):
    """Remind the person asked about every stale question not reminded in
    the last day. Returns the questions nudged."""
    now = now or timezone.now()
    due = [
        q
        for q in stale_open_questions(organisation, days, now)
        if q.last_nudged_at is None or q.last_nudged_at <= now - NUDGE_EVERY
    ]
    for q in due:
        age = (now - q.created_at).days
        about = f" about {q.customer.name}" if q.customer_id else ""
        if not dry_run:
            notify(
                recipient=q.assignee,
                actor=q.asked_by,
                kind=Notification.Kind.QUESTION_ASKED,
                message=(
                    f"Still waiting: {q.asked_by.name} asked you{about} {age} days ago — "
                    f"{q.text[:100]}"
                ),
                link=f"/organizations/{q.customer_id}" if q.customer_id else "/copilot",
            )
            q.last_nudged_at = now
            q.save(update_fields=["last_nudged_at"])
    return due
