"""What every mailbox provider looks like from the inside.

Three implementations (google, microsoft, imap) behind one small surface,
so the sync and the compose endpoint never know which one they talk to.
Plain HTTP through urllib and the standard library's imaplib/smtplib —
no vendor SDKs to pin, patch or explain to an auditor.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime


class ProviderError(Exception):
    """The provider refused or failed; `reauth` says whether the person
    must reconnect (expired or revoked grant) rather than retry."""

    def __init__(self, message, *, reauth=False):
        super().__init__(message)
        self.reauth = reauth


@dataclass
class Message:
    provider_id: str
    thread_id: str
    subject: str
    from_address: str
    from_name: str
    to: list[tuple[str, str]]  # (name, address)
    date: datetime
    body: str
    headers: dict = field(default_factory=dict)


@dataclass
class Credentials:
    """Whatever the provider needs, plus the mailbox's own address."""

    address: str
    display_name: str = ""
    data: dict = field(default_factory=dict)


class MailProvider:
    key = ""
    label = ""
    uses_oauth = True

    @classmethod
    def configured(cls) -> bool:
        return True

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        raise NotImplementedError

    def exchange_code(self, code: str, redirect_uri: str) -> Credentials:
        raise NotImplementedError

    def connect_with_password(self, form: dict) -> Credentials:
        raise NotImplementedError

    def fetch_messages(
        self, creds: Credentials, cursor: str
    ) -> tuple[list[Message], str, Credentials]:
        """Messages newer than `cursor` (provider-specific), the new cursor,
        and the credentials (refreshed tokens included)."""
        raise NotImplementedError

    def send(self, creds: Credentials, *, to: list[str], subject: str, body: str) -> Message:
        raise NotImplementedError


#: The only hosts a provider may talk to. Every URL — including the paging
#: links a provider hands back — is checked against this before a request.
ALLOWED_HOSTS = {
    "accounts.google.com",
    "oauth2.googleapis.com",
    "gmail.googleapis.com",
    "login.microsoftonline.com",
    "graph.microsoft.com",
}


def http_json(method, url, *, headers=None, data=None, form=None, timeout=30):
    """One JSON round-trip. `form` posts urlencoded (OAuth token endpoints),
    `data` posts JSON. Raises ProviderError with the body's message."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
        raise ProviderError(f"refusing to call {parts.hostname or url!r}: not a mail provider host")
    body = None
    headers = dict(headers or {})
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif data is not None:
        body = json.dumps(data).encode()
        headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        # https only, host allow-listed above (semgrep: dynamic-urllib-use)
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosemgrep
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise ProviderError(
            f"{exc.code} from {urllib.parse.urlsplit(url).netloc}: {detail}",
            reauth=exc.code in (401, 403),
        ) from exc
    except urllib.error.URLError as exc:
        raise ProviderError(
            f"could not reach {urllib.parse.urlsplit(url).netloc}: {exc.reason}"
        ) from exc


def parse_address(value: str) -> tuple[str, str]:
    """'Ada Lovelace <ada@example.com>' → ('Ada Lovelace', 'ada@example.com')."""
    from email.utils import parseaddr

    name, address = parseaddr(value or "")
    return name.strip(), address.strip().lower()


def parse_address_list(value: str) -> list[tuple[str, str]]:
    from email.utils import getaddresses

    return [
        (name.strip(), address.strip().lower())
        for name, address in getaddresses([value or ""])
        if address
    ]
