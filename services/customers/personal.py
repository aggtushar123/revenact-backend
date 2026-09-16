"""Personal records on a customer: notes and tasks, like mail, are the writer's;
tickets are their department's.

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


def visible_tickets(user, queryset):
    """Tickets are read department-wise: the department a connector belongs
    to sees what came through it. Leadership sees every department. A ticket
    with no department (raised here, or from a connector with none) is
    everyone's."""
    from django.db.models import Q

    from services.accounts.models import User

    if user.function == User.Function.LEADERSHIP:
        return queryset
    # SOC2:AUTH-02 object-level rule for departmental tickets
    return queryset.filter(Q(department="") | Q(department=user.function))
