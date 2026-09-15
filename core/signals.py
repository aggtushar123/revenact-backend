"""Audit hooks for the auth events Django raises itself (SOC2:LOG-01).

- `user_login_failed` fires from django.contrib.auth.authenticate() when no
  backend accepts the credentials — which is exactly what SimpleJWT's login
  serializer calls, so every failed API login lands here without the view
  having to catch anything. Django has already replaced the password in
  `credentials` with asterisks before sending the signal; only the email
  is kept.
- `user_logged_in` only fires for session logins (the Django admin) — API
  logins are recorded by LoginView itself.
"""

from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.dispatch import receiver

from core import audit
from core.models import AuditEvent


@receiver(user_login_failed)
def _record_login_failure(sender, credentials, request=None, **kwargs):
    email = str(credentials.get("email") or credentials.get("username") or "")[:254]
    audit.record(
        "auth.login",
        request=request,
        outcome=AuditEvent.Outcome.FAILURE,
        metadata={"email": email.lower()},
    )


@receiver(user_logged_in)
def _record_session_login(sender, request, user, **kwargs):
    audit.record("auth.login", request=request, actor=user, metadata={"via": "session"})
