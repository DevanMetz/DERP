"""
Django settings.

Keep this file boring. Real config lives in .env.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-do-not-use-in-prod")
DEBUG = os.environ.get("DEBUG", "False").lower() == "true"

BASE_DOMAIN = os.environ.get("BASE_DOMAIN", "localhost")

# Accept any subdomain of BASE_DOMAIN plus localhost helpers
_static_hosts = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
ALLOWED_HOSTS = _static_hosts + [BASE_DOMAIN, f".{BASE_DOMAIN}"]
if railway_domain := os.environ.get("RAILWAY_PUBLIC_DOMAIN"):
    ALLOWED_HOSTS.append(railway_domain)

CSRF_TRUSTED_ORIGINS = [
    f"https://{h}" for h in ALLOWED_HOSTS
    if h not in ("localhost", "127.0.0.1") and not h.startswith(".")
] + ([f"https://*.{BASE_DOMAIN}"] if BASE_DOMAIN != "localhost" else [])

# HTTPS security — only active when not in debug mode
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True


SHARED_APPS = [
    # django-tenants must be first
    "django_tenants",
    "tenants",

    # Django core shared apps
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "django.contrib.sites",
]

TENANT_APPS = [
    "django.contrib.auth",
    "django.contrib.admin",
    "django.contrib.sessions",
    "django.contrib.messages",

    # Third-party
    "allauth",
    "allauth.account",
    "allauth.mfa",
    "simple_history",
    "django_htmx",

    # Local
    "core",
    "accounting",
    "inventory",
    "sales",
    "purchasing",
    "manufacturing",
    "projects",
    "webstore",
]

INSTALLED_APPS = list(dict.fromkeys(SHARED_APPS + TENANT_APPS))

TENANT_MODEL = "tenants.TenantCompany"
TENANT_DOMAIN_MODEL = "tenants.Domain"
PUBLIC_SCHEMA_URLCONF = "config.public_urls"
DEFAULT_NOT_FOUND_TENANT_VIEW = "tenants.views.tenant_not_found"

MIDDLEWARE = [
    "django_tenants.middleware.main.TenantMainMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "core.middleware.CurrentRequestMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
SITE_ID = 1

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {
        "context_processors": [
            "django.template.context_processors.debug",
            "django.template.context_processors.request",
            "django.contrib.auth.context_processors.auth",
            "django.contrib.messages.context_processors.messages",
            "core.context_processors.website_context",
            "webstore.context_processors.cart_summary",
        ],
    },
}]

# Database: parse DATABASE_URL minimally to avoid an extra dependency.
# Format: postgres://USER:PASS@HOST:PORT/DBNAME
def _parse_db_url(url: str) -> dict:
    from urllib.parse import urlparse
    u = urlparse(url)
    return {
        "ENGINE": "django_tenants.postgresql_backend",
        "NAME": u.path.lstrip("/"),
        "USER": u.username or "",
        "PASSWORD": u.password or "",
        "HOST": u.hostname or "",
        "PORT": str(u.port or ""),
    }

DATABASES = {
    "default": _parse_db_url(os.environ.get("DATABASE_URL", "postgres://erp:erp@localhost:5432/erp")),
}
DATABASE_ROUTERS = ["django_tenants.routers.TenantSyncRouter"]

AUTH_USER_MODEL = "core.User"

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_ALLOW_REGISTRATION = False  # tenant users created at signup or via admin
ACCOUNT_RATE_LIMITS = {"login_failed": "5/5m"}
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
LANGUAGE_CODE = "en-us"
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Cloudflare Turnstile CAPTCHA
TURNSTILE_SITE_KEY = os.environ.get("TURNSTILE_SITE_KEY", "")
TURNSTILE_SECRET_KEY = os.environ.get("TURNSTILE_SECRET_KEY", "")

# Email (Resend SMTP relay)
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "smtp.resend.com"
EMAIL_PORT = 465
EMAIL_USE_SSL = True
EMAIL_HOST_USER = "resend"
EMAIL_HOST_PASSWORD = os.environ.get("RESEND_API_KEY", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "noreply@inventorymanager.xyz")
EMAIL_TIMEOUT = 10

# Currency is locked to USD for v1.
DEFAULT_CURRENCY = "USD"

# Password hashing — prefer Argon2, fall back to PBKDF2 for existing hashes.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# File upload limits (5 MB)
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# Security headers
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

# --- Webstore / Stripe Connect (V2 Accounts) ---
# Platform-level credentials. Every charge is routed to a tenant's
# connected acct_… account via the Stripe-Account header, so tenant
# funds settle into the tenant's Stripe balance — never the platform's
# (except for `application_fee_amount`, which is platform revenue).
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")

# Webhook signing secrets. Stripe forbids mixing V1 (snapshot) and V2
# (thin) events on a single destination, so the platform needs two:
#
#   STRIPE_WEBHOOK_SECRET      = the THIN destination (V2 account events)
#   STRIPE_WEBHOOK_SECRET_V1   = the SNAPSHOT destination (checkout.session.completed
#                                and any other V1 events you subscribe to)
#
# Both can be set to the same value during local dev with the Stripe CLI;
# in production they will differ since each destination has its own
# `whsec_…`.
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_WEBHOOK_SECRET_V1 = os.environ.get("STRIPE_WEBHOOK_SECRET_V1", "")

# Key used to derive Fernet-encrypted column values. Rotate via a custom
# command that re-encrypts each row. Falls back to SECRET_KEY if unset,
# but a dedicated value is strongly preferred so the two can rotate
# independently.
FIELD_ENCRYPTION_KEY = os.environ.get("FIELD_ENCRYPTION_KEY", "")

# Chart-of-accounts code for the cash account that receives online sales.
# Defaults to "1010" (seeded default checking).
WEBSTORE_CASH_ACCOUNT_CODE = os.environ.get("WEBSTORE_CASH_ACCOUNT_CODE", "1010")
