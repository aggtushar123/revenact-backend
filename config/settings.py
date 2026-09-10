"""
Django settings for the Revenact backend.

Docs: https://docs.djangoproject.com/en/5.2/topics/settings/
"""

import sys
from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, True),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    CORS_ALLOWED_ORIGINS=(list, ["http://localhost:5173"]),
)
# Reads .env if present; real environment variables always take precedence.
environ.Env.read_env(BASE_DIR / ".env")

# --- Core -------------------------------------------------------------------

SECRET_KEY = env("SECRET_KEY", default="django-insecure-dev-key-change-in-production")
DEBUG = env("DEBUG")
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

# --- Database -----------------------------------------------------------------
# Defaults to the docker-compose Postgres instance; override via DATABASE_URL.

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://revenact:revenact@localhost:5432/revenact",
    )
}

# --- Auth -----------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- i18n -----------------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --- Static -----------------------------------------------------------------------

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- CORS -----------------------------------------------------------------------
# The Vite dev server (localhost:5173) is a different origin from the API.

CORS_ALLOWED_ORIGINS = env("CORS_ALLOWED_ORIGINS")
CORS_ALLOW_CREDENTIALS = True

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
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", default="")
ANTHROPIC_MODEL = env("ANTHROPIC_MODEL", default="claude-sonnet-5")
AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID", default="")
AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY", default="")
AWS_REGION = env("AWS_REGION", default="")
BEDROCK_MODEL_ID = env("BEDROCK_MODEL_ID", default="")

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
