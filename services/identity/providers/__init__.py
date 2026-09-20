"""The login providers this deployment can offer.

A provider with no credentials configured is not offered at all, so the sign-in
page never shows a button that cannot work.
"""

from .base import LoginProvider, ProviderError, VerifiedIdentity
from .google import GoogleLoginProvider
from .microsoft import MicrosoftLoginProvider

PROVIDERS: dict[str, LoginProvider] = {
    provider.key: provider for provider in (GoogleLoginProvider(), MicrosoftLoginProvider())
}


def get(key: str) -> LoginProvider | None:
    """The provider by key, or None if unknown or unconfigured."""
    provider = PROVIDERS.get((key or "").lower())
    if provider is None or not provider.is_configured():
        return None
    return provider


def available() -> list[dict]:
    """What the sign-in page should render, newest config wins."""
    return [
        {"key": provider.key, "label": provider.label}
        for provider in PROVIDERS.values()
        if provider.is_configured()
    ]


__all__ = ["PROVIDERS", "LoginProvider", "ProviderError", "VerifiedIdentity", "available", "get"]
