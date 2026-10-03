"""Who may read a segment, and who may change it."""

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied

from .models import Segment

NOT_OWNER = "Only the segment's owner can change it."


def readable_segments(user):
    """Segments `user` may read: their own, those shared with the workspace,
    and those shared with them by name. Inside their own workspace only."""
    # SOC2:AUTH-02 a segment is read by its owner and whoever it is shared with
    return (
        Segment.objects.filter(organisation_id=user.organisation_id)
        .filter(
            Q(owner=user)
            | Q(sharing=Segment.Sharing.WORKSPACE)
            | Q(sharing=Segment.Sharing.PEOPLE, shared_with=user)
        )
        .distinct()
    )


def get_readable(user, pk):
    """The segment, or a 404 that reads the same for a missing segment and
    for one `user` may not read."""
    return get_object_or_404(readable_segments(user).select_related("owner"), pk=pk)


def get_owned(user, pk):
    segment = get_readable(user, pk)
    # SOC2:AUTH-02 only the owner edits, deletes, pins or keeps out
    if segment.owner_id != user.pk:
        raise PermissionDenied(NOT_OWNER)
    return segment
