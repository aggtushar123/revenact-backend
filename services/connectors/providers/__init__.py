"""The systems tickets can be pulled from (or pushed from), by key."""

from .freshdesk import FreshdeskProvider
from .jira import JiraProvider
from .slack import SlackProvider
from .webhook import WebhookProvider
from .zendesk import ZendeskProvider

PROVIDERS = {
    p.key: p
    for p in (ZendeskProvider, JiraProvider, SlackProvider, FreshdeskProvider, WebhookProvider)
}


def syncs_tickets(key) -> bool:
    return key in PROVIDERS


def get_provider(key):
    try:
        return PROVIDERS[key]()
    except KeyError as exc:
        raise ValueError(f"{key!r} is not a ticket source.") from exc


def describe(key):
    """The connect form for one provider, or None for a system that is
    attribution-only (Salesforce, Zoom, …)."""
    return PROVIDERS[key]().describe() if key in PROVIDERS else None
