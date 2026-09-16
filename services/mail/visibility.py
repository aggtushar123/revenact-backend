"""Who may read an email: its mailbox owner and their management chain.

Stricter than the knowledge scope (services.accounts.hierarchy.scope_ids):
mail is personal. You see what you sent and received, and what the people
who report to you — directly or indirectly — sent and received. Never a
senior's, never a peer's, never another branch's. Emails logged before
mailboxes existed have no owner and stay visible as they always were.
"""

from django.db.models import Q

from services.accounts.hierarchy import subtree_ids


def visible_emails_q(user) -> Q:
    # SOC2:AUTH-02 object-level rule for personal mail
    return (
        Q(mailbox_owner__isnull=True)
        | Q(mailbox_owner=user)
        | Q(mailbox_owner_id__in=subtree_ids(user))
    )


def visible_emails(user, queryset):
    return queryset.filter(visible_emails_q(user))
