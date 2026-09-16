"""Who may read an email: its mailbox owner and their management chain.

Stricter than the knowledge scope (services.accounts.hierarchy.scope_ids):
mail is personal. You see what you sent and received, and what the people
who report to you — directly or indirectly — sent and received. Never a
senior's, never a peer's, never another branch's. Emails logged before
mailboxes existed have no owner and stay visible as they always were.
"""

from services.accounts.hierarchy import chain_visible_q


def visible_emails_q(user):
    # SOC2:AUTH-02 object-level rule for personal mail
    return chain_visible_q(user, "mailbox_owner")


def visible_emails(user, queryset):
    return queryset.filter(visible_emails_q(user))
