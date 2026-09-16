"""Mailbox credentials at rest (SOC2:DATA-02, SEC-07).

OAuth refresh tokens and IMAP passwords are the keys to someone's whole
mailbox, so they never sit in a column in clear. Fernet (AES-128-CBC +
HMAC, from `cryptography`) with a key from MAIL_TOKEN_KEY; when that is
not set the key is derived from SECRET_KEY, so a development box works
without ceremony and production is told to set its own.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _key() -> bytes:
    configured = getattr(settings, "MAIL_TOKEN_KEY", "")
    if configured:
        return configured.encode()
    # SOC2:SEC-07 derived, never stored: the same SECRET_KEY always yields it.
    return base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())


def encrypt(text: str) -> str:
    return Fernet(_key()).encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return Fernet(_key()).decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Stored mailbox credentials cannot be read with this key.") from exc
