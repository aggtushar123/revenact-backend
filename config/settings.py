"""
Django settings for the Revenact backend.

Docs: https://docs.djangoproject.com/en/5.2/topics/settings/
"""

import sys
from datetime import timedelta
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# SOC2:SEC-06 secure defaults: DEBUG is off unless the environment says
# otherwise (.env.example turns it on for local dev).
env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    CORS_ALLOWED_ORIGINS=(list, ["http://localhost:5173"]),
    CSRF_TRUSTED_ORIGINS=(list, []),
)
# Reads .env if present; real environment variables always take precedence.
environ.Env.read_env(BASE_DIR / ".env")

# --- Core -------------------------------------------------------------------

DEBUG = env("DEBUG")
# True under `manage.py test`: swaps external services (Redis) for in-memory
# ones and disables auth rate limits so the suite isn't throttled.
TESTING = "test" in sys.argv

# SOC2:SEC-06 no default credentials in production. Dev gets a throwaway key
# when DEBUG is on; anything else must set SECRET_KEY explicitly.
SECRET_KEY = env("SECRET_KEY", default="")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "django-insecure-dev-key-change-in-production"  # soc2:ignore dev-only
    else:
        raise ImproperlyConfigured("SECRET_KEY must be set when DEBUG is off.")
elif SECRET_KEY.startswith("django-insecure") and not DEBUG:
    raise ImproperlyConfigured("SECRET_KEY is the insecure dev placeholder; set a real one.")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# --- Applications -------------------------------------------------------------

INSTALLED_APPS = [
    # Multiplayer Copilot Phase 2b's own real-time push (see
    # services/copilot/consumers.py) — "daphne" first is required by
    # Channels itself: it auto-patches `manage.py runserver` to serve
    # ASGI (HTTP + WebSocket both) instead of plain WSGI, so the exact
    # same dev command already in use keeps working, no new process to
    # start by hand.
    "daphne",
    "channels",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "drf_spectacular",
    # Local
    "core",
    "services.accounts",
    "services.customers",
    "services.scenarios",
    "services.webhooks",
    "services.fx_rates",
    "services.campaigns",
    "services.copilot",
    "services.notifications",
    "services.custom_objects",
    "services.connectors",
    "services.mail",
    "services.metrics",
    "services.knowledge",
    "services.identity",
    "services.platform",
    "services.billing",
    "services.attributes",
    "services.requests",
    "services.anomalies",
    "services.translation",
]

# Custom user model — Organisation-scoped, email as USERNAME_FIELD. The app
# is named `accounts` (not `auth`) to avoid colliding with django.contrib.auth's
# app label, but it's mounted at /api/v1/auth/ to match the frontend's
# features/auth/ domain — see docs/API_CONTRACTS.md. Lives at
# services/accounts/ (see services/accounts/apps.py's explicit `label`),
# but the app label itself — and so AUTH_USER_MODEL, migration
# dependencies, and every ForeignKey("accounts.X")/("customers.X") string
# elsewhere — is unaffected by where the package physically lives.
AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    "core.middleware.RequestIDMiddleware",  # SOC2:API-05 first, so every log line has the id
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",  # must sit above CommonMiddleware
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Multiplayer Copilot Phase 2b's own real-time push — config.asgi routes
# WebSocket connections through Channels (HTTP still goes through the
# exact same Django app WSGI_APPLICATION above does; nothing about REST
# changed). See services/copilot/consumers.py's own docstring.
ASGI_APPLICATION = "config.asgi.application"

# Real Redis-backed channel layer — required once more than one server
# process exists (a single `manage.py runserver` would work fine with
# the in-memory layer too, but that silently stops working the moment a
# second process joins, with no error — see services/copilot/realtime.py's
# own docstring). Runs via Docker locally (`docker compose up -d redis`,
# see docker-compose.yml), not installed on this machine directly — the
# user's own explicit choice.
#
# `manage.py test` swaps this for the in-memory backend instead — same
# "don't make the real test suite depend on a real external service"
# discipline as mocking the real Anthropic API call (see
# anthropic_client.py) or the real embedding model in most copilot
# tests: real Redis reachability isn't what these tests are about, and
# a broken/unreachable Redis would otherwise fail unrelated tests all
# over the suite (any view that calls
# services.copilot.realtime.broadcast_session_update), not just the
# ones actually exercising real-time push (see test_consumers.py, which
# re-overrides this to the exact same in-memory backend deliberately,
# self-documenting the choice for its own module rather than relying on
# this one).
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": (
            "channels.layers.InMemoryChannelLayer"
            if "test" in sys.argv
            else "channels_redis.core.RedisChannelLayer"
        ),
        "CONFIG": {}
        if "test" in sys.argv
        else {"hosts": [env("REDIS_URL", default="redis://localhost:6379/0")]},
    },
}

# --- Cache --------------------------------------------------------------------
# Only consumer today is the auth rate limiting (core/throttling.py). Redis in
# production (the same one the channel layer uses) so the counters survive a
# restart; in-memory under test and in DEBUG, so a local login doesn't need
# Redis to be up. Throttling still works in dev, per process.

REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    if TESTING or DEBUG
    else {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": REDIS_URL}
}

# --- Database -----------------------------------------------------------------
# Defaults to the docker-compose Postgres instance; override via DATABASE_URL.

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://revenact:revenact@localhost:5432/revenact",  # soc2:ignore dev-only
    )
}

# --- Auth -----------------------------------------------------------------------

# SOC2:AUTH-04 password policy: 12+ characters, not a common password, not
# derived from the user's own name/email, no composition rules (NIST 800-63B).
# Enforced by services/accounts/serializers.py on signup, admin-set,
# self-service change and reset — not only by createsuperuser.
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# SOC2:AUTH-04 / SEC-07 Argon2id for new hashes; PBKDF2 stays listed so
# existing hashes still verify and are upgraded on the user's next login.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
if TESTING:
    # Argon2 is deliberately slow; hundreds of test users made the suite take
    # 13+ minutes on CI. The one test that checks the production hasher
    # overrides this back (test_password_policy.test_new_hashes_use_argon2).
    PRODUCTION_PASSWORD_HASHERS = PASSWORD_HASHERS
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# --- i18n -----------------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --- Static -----------------------------------------------------------------------

STATIC_URL = "static/"
# Collected at image build (Dockerfile) and served by the reverse proxy in a
# deployment; unused by the dev server, which serves the admin's assets itself.
STATIC_ROOT = BASE_DIR / "staticfiles"

# Uploaded files (Files tab, call transcripts). A private directory — the
# deployment mounts a volume here — served only through the authenticated
# download view (services/customers/files.py), never by Caddy or MEDIA_URL.
MEDIA_ROOT = Path(env("MEDIA_ROOT", default=str(BASE_DIR / "media")))
ATTACHMENT_MAX_BYTES = env.int("ATTACHMENT_MAX_BYTES", default=25 * 1024 * 1024)
FILE_UPLOAD_PERMISSIONS = 0o640
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- CORS -----------------------------------------------------------------------
# The Vite dev server (localhost:5173) is a different origin from the API.

CORS_ALLOWED_ORIGINS = env("CORS_ALLOWED_ORIGINS")

# Behind a TLS-terminating reverse proxy (the deployment's Caddy): trust its
# scheme header so Django builds https links and the admin's CSRF check
# accepts the public origin. Harmless in dev, where nothing sets the header.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS")
CORS_ALLOW_CREDENTIALS = True

# --- Transport security headers (SOC2:API-06, DATA-03, AUTH-05) ------------------
# Applied whenever DEBUG is off. TLS itself terminates at Caddy, which is why
# SECURE_SSL_REDIRECT stays off (the container health check speaks plain
# HTTP to 127.0.0.1). The API is bearer-token based; the cookie settings
# protect the session the Django admin uses.

if not DEBUG:
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = False
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 30 * 60  # admin idle timeout, 30 min
SESSION_SAVE_EVERY_REQUEST = True  # ...measured from the last request, not login
X_FRAME_OPTIONS = "DENY"

# SOC2:API-04 request body ceiling (Django default made explicit).
DATA_UPLOAD_MAX_MEMORY_SIZE = 2_621_440  # 2.5 MB

# --- Django REST Framework -------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    # Auth landed (accounts app) — endpoints require a valid JWT by default
    # now. Views that must stay public (signup, login) set AllowAny
    # explicitly; see accounts/views.py.
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    # Behind Caddy the client address is the last X-Forwarded-For hop it
    # appended; 0 means "no proxy, trust REMOTE_ADDR". Used by the auth
    # throttles and audit log to identify the caller.
    "NUM_PROXIES": env.int("NUM_PROXIES", default=1),
    "DEFAULT_THROTTLE_RATES": {},  # filled in below from AUTH_THROTTLE_RATES
}

# SOC2:AUTH-06 rate limits on the public auth endpoints (core/throttling.py,
# applied per view in services/accounts/views.py). None under test.
AUTH_THROTTLE_RATES = {
    "login": env("THROTTLE_LOGIN", default="10/min"),  # per source IP
    "login_account": env("THROTTLE_LOGIN_ACCOUNT", default="5/min"),  # per email
    "signup": env("THROTTLE_SIGNUP", default="5/hour"),
    "password_reset": env("THROTTLE_PASSWORD_RESET", default="5/hour"),
    "token_refresh": env("THROTTLE_TOKEN_REFRESH", default="30/min"),
}
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {
    scope: (None if TESTING else rate) for scope, rate in AUTH_THROTTLE_RATES.items()
}

if DEBUG:
    # Browsable API is convenient in dev only.
    REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"].append(
        "rest_framework.renderers.BrowsableAPIRenderer"
    )

# --- JWT (djangorestframework-simplejwt) -----------------------------------------

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "UPDATE_LAST_LOGIN": True,
    # SOC2:AUTH-05 every refresh issues a new refresh token and blacklists
    # the one just used, so a stolen refresh token is only good once (the
    # frontend stores the replacement — see authSlice.refreshSession).
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}

# --- Email (forgot/reset password) -----------------------------------------------
# SMTP creds are optional: with none set, mail falls back to Django's console
# backend (prints to the runserver terminal instead of sending) so local dev
# still works without real credentials. Set EMAIL_HOST_USER/EMAIL_HOST_PASSWORD
# in .env to send for real — see .env.example for the Gmail app-password setup.

EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_BACKEND = (
    "django.core.mail.backends.smtp.EmailBackend"
    if EMAIL_HOST_USER
    else "django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = env("EMAIL_HOST", default="smtp.gmail.com")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default=EMAIL_HOST_USER or "noreply@revenact.local")

# Origin the emailed reset link points at (a frontend route, not this API).
FRONTEND_URL = env("FRONTEND_URL", default="http://localhost:5173")

# --- Copilot (Anthropic Claude, direct or via AWS Bedrock) --------------------
# The first real LLM integration in this codebase (services.copilot). No real
# credentials for whichever provider is selected means no fake fallback —
# SendMessageView returns a clear 503 instead of pretending to work. Never set
# any of these to a real value here or commit one — see .env.example.
#
# COPILOT_LLM_PROVIDER picks which real backend services.copilot.anthropic_client
# calls: "anthropic" (default) hits Anthropic's own API directly with
# ANTHROPIC_API_KEY (get one at https://console.anthropic.com/); "bedrock" calls
# the exact same Claude model through AWS Bedrock instead, using real AWS
# credentials — requires Bedrock model access to have already been requested/
# approved for that model in your own AWS account and region (a one-time
# AWS Console step this app can't do for you) and BEDROCK_MODEL_ID to be the
# real model id from that same console, not the "anthropic"-provider one above
# (they use different id formats).
COPILOT_LLM_PROVIDER = env("COPILOT_LLM_PROVIDER", default="anthropic")
# Tokens (input + output) any one purpose may spend per organisation per
# calendar month before get_completion refuses the call, unless a
# copilot.ModelBudget row says otherwise. Generous by default — the point is
# that a runaway agent stops, not that a busy team is throttled.
MODEL_BUDGET_DEFAULT_TOKENS = env.int("MODEL_BUDGET_DEFAULT_TOKENS", default=2_000_000)
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", default="")
ANTHROPIC_MODEL = env("ANTHROPIC_MODEL", default="claude-sonnet-5")
AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID", default="")
AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY", default="")
AWS_REGION = env("AWS_REGION", default="")
BEDROCK_MODEL_ID = env("BEDROCK_MODEL_ID", default="")
if TESTING:
    import tempfile

    # Uploads from the test suite land in a scratch directory, never in the
    # developer's media folder.
    MEDIA_ROOT = Path(tempfile.mkdtemp(prefix="revenact-test-media-"))
    # Never a live model call from the test suite: filing mail and the daily
    # job classify records, and a developer's key must not be spent by tests.
    # Tests that need an answer patch get_completion.
    ANTHROPIC_API_KEY = ""
    AWS_ACCESS_KEY_ID = ""
    AWS_SECRET_ACCESS_KEY = ""

# How long a password-reset link stays valid. Consumed by
# django.contrib.auth.tokens.default_token_generator, which accounts/serializers.py
# uses directly — see ForgotPasswordSerializer/ResetPasswordSerializer.
PASSWORD_RESET_TIMEOUT = 60 * 60  # 1 hour

# --- drf-spectacular (OpenAPI schema + Swagger/Redoc UI) -------------------------

SPECTACULAR_SETTINGS = {
    "TITLE": "Revenact API",
    "DESCRIPTION": (
        "Backend API for Revenact, a customer-success intelligence platform. "
        "Built feature-by-feature to mirror the react-ts-app frontend; see "
        "docs/API_CONTRACTS.md in this repo for the running log of decisions."
    ),
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
}

# --- Logging (SOC2:LOG-03, LOG-04) ---------------------------------------------
# JSON lines on stdout in production (shipped by whatever collects container
# logs), plain text in dev. Every record passes core.logging.RedactFilter
# first. The audit trail (core.audit) has its own table; the `core.audit`
# logger is its second copy in the log stream.

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"redact": {"()": "core.logging.RedactFilter"}},
    "formatters": {
        "json": {"()": "core.logging.JSONFormatter"},
        "plain": {"format": "%(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "plain" if DEBUG else "json",
            "filters": ["redact"],
        }
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING" if TESTING else env("LOG_LEVEL", default="INFO"),
    },
    "loggers": {
        "django.request": {"level": "WARNING"},
        "django.server": {"level": "INFO"},
        "core.audit": {"level": "INFO"},
        "daphne": {"level": "WARNING"},
    },
}

# --- Sign-in with an external provider (services.identity) -----------------
# Off by default. While it is off the endpoints answer as though the feature
# does not exist, so a half-configured deployment never shows a button that
# cannot work. Password sign-in is unaffected either way.
AUTH_V2_ENABLED = env.bool("AUTH_V2_ENABLED", default=False)

# Free and consumer mail providers, which can never establish or join a tenant
# by domain alone — an address there identifies a person, not a company. The
# default list lives in services/identity/domains.py; this only extends or
# replaces it without a release.
PERSONAL_EMAIL_DOMAINS = env.list("PERSONAL_EMAIL_DOMAINS", default=[])

# --- Billing (services.billing) -----------------------------------------------
# What a brand-new workspace gets before anyone pays, as decided in review:
# three seats and a fortnight of AI credits. Seats stay at three until a plan
# is bought, which is what keeps a self-made workspace small.
BILLING_TRIAL_SEATS = env.int("BILLING_TRIAL_SEATS", default=3)
BILLING_TRIAL_CREDITS = env.int("BILLING_TRIAL_CREDITS", default=200)
BILLING_TRIAL_DAYS = env.int("BILLING_TRIAL_DAYS", default=14)
# One model call costs this many credits. Charged before the call, refunded
# if the call fails.
BILLING_CREDITS_PER_MODEL_CALL = env.int("BILLING_CREDITS_PER_MODEL_CALL", default=1)
# Off, seats and credits are still recorded but never refuse anything: the
# ledger shows what would have been billed. For a beta, or an incident.
BILLING_ENFORCED = env.bool("BILLING_ENFORCED", default=True)

# --- Personal mailboxes (services.mail) ------------------------------------
# OAuth clients for the providers a company can connect. A provider is offered
# only when its client is configured; IMAP/SMTP needs nothing. Tokens and
# passwords are encrypted at rest with MAIL_TOKEN_KEY (a Fernet key:
# `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`);
# unset, the key is derived from SECRET_KEY. SOC2:DATA-02
MAIL_TOKEN_KEY = env("MAIL_TOKEN_KEY", default="")
GOOGLE_OAUTH_CLIENT_ID = env("GOOGLE_OAUTH_CLIENT_ID", default="")
GOOGLE_OAUTH_CLIENT_SECRET = env("GOOGLE_OAUTH_CLIENT_SECRET", default="")
MICROSOFT_OAUTH_CLIENT_ID = env("MICROSOFT_OAUTH_CLIENT_ID", default="")
MICROSOFT_OAUTH_CLIENT_SECRET = env("MICROSOFT_OAUTH_CLIENT_SECRET", default="")
MICROSOFT_OAUTH_TENANT = env("MICROSOFT_OAUTH_TENANT", default="common")
# Slack app for ticket connectors (services/connectors/providers/slack.py).
# Optional: a pasted bot token works without it.
SLACK_OAUTH_CLIENT_ID = env("SLACK_OAUTH_CLIENT_ID", default="")
SLACK_OAUTH_CLIENT_SECRET = env("SLACK_OAUTH_CLIENT_SECRET", default="")
# Where a provider sends the browser back after consent; the API's own
# public origin (Caddy fronts it), e.g. https://revenact.example.com.
MAIL_OAUTH_REDIRECT_BASE = env("MAIL_OAUTH_REDIRECT_BASE", default="")
