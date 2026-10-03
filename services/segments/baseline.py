"""Where a segment's change history starts."""

from .evaluate import member_ids
from .models import MAX_TRACKED_MEMBERS


def rebaseline(segment, *, today):
    """Start the history afresh from today's members, as the owner sees them.
    Called when the owner changes what the segment means (create, rules,
    pins, keep-outs, duplicate) and when a paused segment resumes, so the
    history records the data moving, never an edit."""
    ids = member_ids(segment, segment.owner, today=today)
    segment.member_count = len(ids)
    segment.last_members = ids if len(ids) <= MAX_TRACKED_MEMBERS else None
    segment.last_evaluated_on = today
    segment.save(update_fields=["member_count", "last_members", "last_evaluated_on"])
