"""The providers a mailbox can be connected through, by key."""

from .google import GoogleProvider
from .imap import ImapProvider
from .microsoft import MicrosoftProvider

PROVIDERS = {p.key: p for p in (GoogleProvider, MicrosoftProvider, ImapProvider)}


def get_provider(key):
    try:
        return PROVIDERS[key]()
    except KeyError as exc:
        raise ValueError(f"Unknown mailbox provider {key!r}.") from exc


def available():
    """What this deployment can connect: OAuth providers only when their
    client is configured; IMAP always."""
    return [
        {"key": cls.key, "label": cls.label, "uses_oauth": cls.uses_oauth}
        for cls in PROVIDERS.values()
        if cls.configured()
    ]
