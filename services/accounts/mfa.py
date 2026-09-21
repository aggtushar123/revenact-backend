"""Time-based one-time passwords (RFC 6238) for the accounts that matter most.

Platform staff administer every tenant, so their sign-in gets a second factor.
The algorithm is HMAC-SHA1 over a 30-second counter with dynamic truncation to
six digits, exactly what authenticator apps implement; it is short enough to
own outright, so there is nothing to pin, patch or explain to an auditor.

Three properties are enforced here rather than left to callers:

- **A code is accepted once.** The counter it matched is remembered on the
  device and anything at or before it is refused, so a code seen over a
  shoulder cannot be replayed within its own thirty seconds.
- **The secret never sits in clear.** It is Fernet-encrypted at rest with the
  same key that protects mailbox credentials.
- **Recovery codes are hashed**, single use, and handed out exactly once, at
  enrolment.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time

from django.core import signing
from django.utils import timezone

from services.mail.crypto import decrypt, encrypt

#: Seconds per counter step; what every authenticator app assumes.
STEP = 30
DIGITS = 6
#: Steps of clock drift tolerated on either side.
WINDOW = 1

RECOVERY_CODE_COUNT = 8

#: The short-lived value a password login hands back when a second factor is
#: still owed. It names the person and nothing else, and it is not a session.
CHALLENGE_SALT = "accounts.mfa.challenge"
CHALLENGE_MAX_AGE = 5 * 60


def generate_secret() -> str:
    """A fresh base32 secret, 160 bits, as authenticator apps expect it."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def otpauth_uri(secret: str, *, email: str, issuer: str = "Revenact") -> str:
    """What the QR code encodes. Standard `otpauth://` so any app can read it."""
    from urllib.parse import quote

    label = quote(f"{issuer}:{email}", safe="")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={STEP}"


def _code_at(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % (10**DIGITS)).zfill(DIGITS)


def current_counter(at: float | None = None) -> int:
    return int((time.time() if at is None else at) // STEP)


def match_counter(secret: str, code: str, *, at: float | None = None) -> int | None:
    """The counter step `code` was minted for, within the drift window, or None.

    Constant-time comparison per candidate, so timing says nothing about how
    close a guess came.
    """
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != DIGITS:
        return None
    centre = current_counter(at)
    for counter in range(centre - WINDOW, centre + WINDOW + 1):
        if hmac.compare_digest(_code_at(secret, counter), code):
            return counter
    return None


def new_recovery_codes() -> list[str]:
    """Human-typeable, 10 characters, grouped for reading off a printout."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o/1/l/i
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = "".join(secrets.choice(alphabet) for _ in range(10))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


def hash_recovery_code(code: str) -> str:
    normalised = (code or "").strip().lower().replace("-", "").replace(" ", "")
    return hashlib.sha256(normalised.encode()).hexdigest()


# ── Device operations ────────────────────────────────────────────────────


def begin_enrolment(user):
    """Create (or replace) an unconfirmed device and return (device, secret).

    Replacing is the point: an abandoned enrolment must not leave a secret
    behind that a later attacker could confirm. A *confirmed* device is left
    alone; disabling it is a separate, code-checked act.
    """
    from .models import TOTPDevice

    existing = TOTPDevice.objects.filter(user=user).first()
    if existing and existing.confirmed_at:
        raise MFAError("MFA_ALREADY_ENROLLED", "Two-factor authentication is already on.")

    secret = generate_secret()
    if existing:
        existing.secret_encrypted = encrypt(secret)
        existing.last_counter = -1
        existing.recovery_codes = []
        existing.save()
        return existing, secret
    device = TOTPDevice.objects.create(user=user, secret_encrypted=encrypt(secret))
    return device, secret


def confirm_enrolment(user, code: str) -> list[str]:
    """Prove the app was set up correctly; returns the recovery codes, once."""
    from .models import TOTPDevice

    device = TOTPDevice.objects.filter(user=user).first()
    if device is None or device.confirmed_at:
        raise MFAError("MFA_NOT_PENDING", "There is no enrolment to confirm.")

    counter = match_counter(decrypt(device.secret_encrypted), code)
    if counter is None:
        raise MFAError("MFA_CODE_INVALID", "That code is not right. Check the app and try again.")

    codes = new_recovery_codes()
    device.confirmed_at = timezone.now()
    device.last_counter = counter
    device.recovery_codes = [hash_recovery_code(c) for c in codes]
    device.save()
    return codes


def verify(user, code: str) -> str:
    """Check a second factor for `user`. Returns "totp" or "recovery".

    Refuses replay of a TOTP code and spends a recovery code on use.
    """
    from .models import TOTPDevice

    device = (
        TOTPDevice.objects.select_for_update().filter(user=user, confirmed_at__isnull=False).first()
    )
    if device is None:
        raise MFAError("MFA_NOT_ENROLLED", "Two-factor authentication is not set up.")

    counter = match_counter(decrypt(device.secret_encrypted), code)
    if counter is not None:
        if counter <= device.last_counter:
            raise MFAError(
                "MFA_CODE_REUSED", "That code has already been used. Wait for the next one."
            )
        device.last_counter = counter
        device.save(update_fields=["last_counter"])
        return "totp"

    digest = hash_recovery_code(code)
    if digest in device.recovery_codes:
        device.recovery_codes = [c for c in device.recovery_codes if c != digest]
        device.save(update_fields=["recovery_codes"])
        return "recovery"

    raise MFAError("MFA_CODE_INVALID", "That code is not right.")


def disable(user, code: str) -> None:
    """Turn the second factor off. Needs a current code: possession of a
    signed-in session alone must not be enough to weaken the account."""
    from .models import TOTPDevice

    verify(user, code)
    TOTPDevice.objects.filter(user=user).delete()


def is_enrolled(user) -> bool:
    from .models import TOTPDevice

    return TOTPDevice.objects.filter(user=user, confirmed_at__isnull=False).exists()


# ── The login challenge ──────────────────────────────────────────────────


def issue_challenge(user) -> str:
    return signing.dumps({"u": user.pk}, salt=CHALLENGE_SALT)


def read_challenge(token: str):
    """The user a challenge names, or raise. Expired means start over."""
    from .models import User

    try:
        payload = signing.loads(token or "", salt=CHALLENGE_SALT, max_age=CHALLENGE_MAX_AGE)
    except signing.SignatureExpired as exc:
        raise MFAError("MFA_CHALLENGE_EXPIRED", "That sign-in took too long. Start again.") from exc
    except signing.BadSignature as exc:
        raise MFAError("MFA_CHALLENGE_INVALID", "That sign-in could not be verified.") from exc
    user = User.objects.filter(pk=payload.get("u"), is_active=True).first()
    if user is None:
        raise MFAError("ACCOUNT_DISABLED", "This account has been deactivated.")
    return user


class MFAError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message
