"""Brute-force protection for the public auth endpoints (SOC2:AUTH-06, API-04).

Rates come from REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] (set from
AUTH_THROTTLE_RATES in config/settings.py; `None` under `manage.py test`
so the suite is not rate-limited — the throttle tests set a rate
explicitly). Counters live in the default cache (Redis outside tests) so
they survive a restart and are shared if a second process ever appears.

Two keys for login on purpose: per source IP (a bot hammering many
accounts) and per account email (a distributed attack on one account).
The email key is hashed so the cache never holds the address in clear.
"""

import hashlib

from rest_framework.throttling import SimpleRateThrottle


class _PerIPThrottle(SimpleRateThrottle):
    rate = None  # resolved from DEFAULT_THROTTLE_RATES[scope]; tests patch this

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class LoginIPThrottle(_PerIPThrottle):
    scope = "login"


class LoginAccountThrottle(SimpleRateThrottle):
    scope = "login_account"
    rate = None

    def get_cache_key(self, request, view):
        email = str(request.data.get("email", "") or "").strip().lower()
        if not email:
            return None
        digest = hashlib.sha256(email.encode("utf-8")).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}


class SignupThrottle(_PerIPThrottle):
    scope = "signup"


class PasswordResetThrottle(_PerIPThrottle):
    scope = "password_reset"


class TokenRefreshThrottle(_PerIPThrottle):
    scope = "token_refresh"


class McpTokenThrottle(SimpleRateThrottle):
    """Per MCP token, not per IP: an agent is one key, wherever it runs
    from, and the thing worth limiting is how fast one key can spend the
    organisation's model budget."""

    scope = "mcp"

    def get_cache_key(self, request, view):
        header = request.headers.get("Authorization", "")
        raw = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if not raw:
            return None
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}
