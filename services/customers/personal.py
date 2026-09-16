"""Personal records on a customer: notes and tasks, like mail, are the writer's.

Readable by the author and their management chain — never by peers or
seniors — in the Notes tab and in the Copilot alike. Rows with no author
(seeded, or logged before authorship existed) stay visible as before.
"""

from services.accounts.hierarchy import chain_visible_q


def visible_notes(user, queryset):
    # SOC2:AUTH-02 object-level rule for personal notes
    return queryset.filter(chain_visible_q(user, "author"))


def visible_tasks(user, queryset):
    """A task is the creator's and the assignee's: either of them, or anyone
    above either, may read it. A task with neither (seeded) is everyone's."""
    from django.db.models import Q

    from services.accounts.hierarchy import subtree_ids

    mine = {user.id, *subtree_ids(user)}
    # SOC2:AUTH-02 object-level rule for personal tasks
    return queryset.filter(
        Q(created_by__isnull=True, assignee__isnull=True)
        | Q(created_by_id__in=mine)
        | Q(assignee_id__in=mine)
    )
