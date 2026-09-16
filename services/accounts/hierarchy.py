"""Who may see whose words: the org chart, and the one rule read from it.

`User.reports_to` is the chart. From it, a person's **scope** is the set
of people whose records they may see:

- themselves;
- everyone who reports to them, directly or indirectly (the hierarchy —
  a manager sees their team's work, and leadership at the top sees all);
- everyone in their function (the team);
- everyone above them in the chain (leadership — a report sees what their
  managers write, so direction flows down).

Anything explicitly addressed to a person — a question routed to them, a
message that @mentions them — is theirs to see regardless. Records in a
different function, on a different branch, are not.

Customer and account records are not scoped by this: they keep the
capability-based scoping (`view_all_accounts`) they always had. This rule
is about what people *say* — contributions, questions, chat.
"""

from .models import User


def ancestors(user):
    """The management chain above `user`, nearest first. Stops on a cycle."""
    seen, chain = {user.id}, []
    current = user.reports_to
    while current is not None and current.id not in seen:
        chain.append(current)
        seen.add(current.id)
        current = current.reports_to
    return chain


def subtree_ids(user):
    """Everyone below `user` in the chart, recursively (not including them)."""
    found, frontier = set(), [user.id]
    while frontier:
        reports = list(
            User.objects.filter(reports_to_id__in=frontier)
            .exclude(id__in=found)
            .values_list("id", flat=True)
        )
        found.update(reports)
        frontier = reports
    found.discard(user.id)
    return found


def scope_ids(user):
    """The ids of everyone whose records `user` may see — see the module."""
    ids = {user.id}
    ids.update(subtree_ids(user))
    ids.update(
        User.objects.filter(organisation=user.organisation, function=user.function).values_list(
            "id", flat=True
        )
    )
    ids.update(a.id for a in ancestors(user))
    return ids


def would_cycle(user, manager):
    """True if making `manager` the boss of `user` would loop the chart."""
    if manager is None:
        return False
    if manager.id == user.id:
        return True
    return user.id in {a.id for a in ancestors(manager)} or user.id == manager.id


def chain_visible_q(user, field):
    """Personal records — mail, notes — are readable by the person on
    `field` and their management chain: a Q for rows that have nobody on
    the field (logged before it existed), the user, or someone below them.
    Stricter than `scope_ids`: no peers, no seniors."""
    from django.db.models import Q

    return (
        Q(**{f"{field}__isnull": True})
        | Q(**{field: user})
        | Q(**{f"{field}_id__in": subtree_ids(user)})
    )
