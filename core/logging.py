"""Structured application logging (SOC2:LOG-03, LOG-04).

Two pieces, both wired up by LOGGING in config/settings.py:

- RedactFilter scrubs anything that looks like a credential out of every
  log message before it reaches a handler, so a careless call that logs
  a whole request or its headers can't ship a bearer token or password
  to wherever the container logs end up. It is a safety net,
  not permission to log secrets.
- JSONFormatter writes one JSON object per line (timestamp in UTC, level,
  logger, message, request id, optional exception and audit payload) so
  the container's stdout can be shipped to a log store and queried without
  a parsing step. Dev keeps the plain format for readability.
"""

import json
import logging
import re
from datetime import datetime, timezone

from core.middleware import get_request_id

_KEY_VALUE = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|authorization|refresh|access)"
    r"([\"']?\s*[:=]\s*[\"']?)"
    r"(?!\[REDACTED\]|bearer\b)"  # already handled by the two patterns above
    r"([^\s\"',;&}]+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")

REDACTED = "[REDACTED]"


def redact(text: str) -> str:
    text = _BEARER.sub("Bearer " + REDACTED, text)
    text = _JWT.sub(REDACTED, text)
    return _KEY_VALUE.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)


class RedactFilter(logging.Filter):
    # SOC2:LOG-03 redact before the record reaches any handler.
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # bad format args — let logging report it as usual
            return True
        record.msg = redact(message)
        record.args = ()
        return True


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": get_request_id() or None,
        }
        audit = getattr(record, "audit", None)
        if audit is not None:
            payload["audit"] = audit
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)
