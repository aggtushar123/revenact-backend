"""Request correlation (SOC2:API-05, LOG-02).

Every request gets an id — the caller's own `X-Request-ID` if it sent a
sane one, otherwise a fresh uuid — that is echoed back in the response
header, stamped on every log line written while the request is being
handled (see core.logging) and stored on every audit event (core.audit).
An error report from a user can then be matched to the exact server-side
log lines and audit rows without exposing anything internal in the body.
"""

import contextvars
import re
import uuid

_request_id = contextvars.ContextVar("request_id", default="")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def get_request_id() -> str:
    """The id of the request currently being handled, or "" outside one
    (management commands, the scheduler loop)."""
    return _request_id.get()


class RequestIDMiddleware:
    header = "HTTP_X_REQUEST_ID"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.META.get(self.header, "")
        # SOC2:API-05 accept only a plain token from the client — anything
        # else is replaced so a crafted header can't inject into log lines.
        request_id = incoming if _SAFE_ID.match(incoming) else uuid.uuid4().hex
        request.request_id = request_id
        token = _request_id.set(request_id)
        try:
            response = self.get_response(request)
        finally:
            _request_id.reset(token)
        response["X-Request-ID"] = request_id
        return response
