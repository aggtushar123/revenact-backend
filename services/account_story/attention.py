"""Needs attention on the account page (spec §2.5): the renewal overdue or
due within 30 days, open High/Critical tickets (count and oldest age), and
overdue tasks.

Tickets and tasks reuse the organisation story's aggregates over the story's
own base querysets, so the block reads exactly the rows the stream does
(`visible_tickets`, `visible_tasks`, this account only). Company knowledge is
per organisation and an anomaly is not an account's, so `questions` and
`anomaly` are always null; the keys stay so the block has the organisation
story's shape. Nothing here follows a filter or a search.
"""

from services.organizations.story.attention import (
    RENEWAL_WINDOW_DAYS,
    overdue_tasks,
    urgent_tickets,
)


def account_renewal(account, today):
    """Accounts have no churn, so only the date decides."""
    if account.renewal_date is None:
        return None
    days = (account.renewal_date - today).days
    if days > RENEWAL_WINDOW_DAYS:
        return None
    return {"date": account.renewal_date.isoformat(), "days": days, "overdue": days < 0}


def build_account_attention(user, scope, bases, account, *, today):
    """`build_attention`'s signature, so `build_story` takes either. `bases`
    are already this account's readable rows; `user` and `account` are not
    needed here."""
    return {
        "renewal": account_renewal(scope.account, today),
        "tickets": urgent_tickets(bases["ticket"], today),
        "overdue_tasks": overdue_tasks(bases["task"], today),
        "questions": None,
        "anomaly": None,
    }
