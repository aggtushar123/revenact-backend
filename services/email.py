"""Outbound email. A thin wrapper around django.core.mail.send_mail — the
actual delivery mechanism (real SMTP vs. the console backend that just
prints to the runserver terminal) is controlled entirely by EMAIL_BACKEND
in config/settings.py; nothing here knows or cares which one is active.

Pulled out of accounts/serializers.py into its own module so "how do we
build and send this email" has one home per email, not re-implemented
inline on whichever serializer happens to trigger it first — the next
transactional email (e.g. a CSM invite) adds a function here, not a new
ad-hoc send_mail() call somewhere else.
"""

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode


def send_password_reset_email(user):
    """Emails `user` a link to {FRONTEND_URL}/reset-password?uid=...&token=....
    Used by accounts.serializers.ForgotPasswordSerializer — see there for
    why the uid/token pair is safe to email out (Django's built-in
    PasswordResetTokenGenerator, no separate token model)."""

    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    reset_link = f"{settings.FRONTEND_URL}/reset-password?uid={uid}&token={token}"

    send_mail(
        subject="Reset your Revenact password",
        message=(
            f"Hi {user.name},\n\n"
            "Someone requested a password reset for your Revenact account. "
            "If this was you, click the link below to choose a new one "
            "(it expires in 1 hour):\n\n"
            f"{reset_link}\n\n"
            "If you didn't request this, you can safely ignore this email — "
            "your password hasn't been changed."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
    )
