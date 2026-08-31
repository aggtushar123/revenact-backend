"""
Django settings for the Revenact backend.

Docs: https://docs.djangoproject.com/en/5.2/topics/settings/
"""

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
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    "rest_framework_simplejwt",
    "corsheaders",
    "drf_spectacular",
    # Local
    "accounts",
    "core",
]

# Custom user model — Organisation-scoped, email as USERNAME_FIELD. The app
# is named `accounts` (not `auth`) to avoid colliding with django.contrib.auth's
# app label, but it's mounted at /api/v1/auth/ to match the frontend's
# features/auth/ domain — see docs/API_CONTRACTS.md.
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
