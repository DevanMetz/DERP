from django.db import connection
from django.db.utils import DatabaseError

from .models import Company, WebsiteSettings


def website_context(request):
    """Expose WebsiteSettings + Company on every page.

    Both models are in TENANT_APPS so their tables only exist in tenant
    schemas. Querying them on the public schema (the platform landing /
    signup pages) raises ProgrammingError. Wrap each call so the public
    schema renders fall back to None / a stub Company without crashing.
    """
    tenant = getattr(connection, "tenant", None)
    on_public = tenant is None or getattr(tenant, "schema_name", "public") == "public"

    try:
        settings = None if on_public else WebsiteSettings.get()
    except DatabaseError:
        settings = None
    except Exception:
        settings = None

    try:
        company = None if on_public else Company.get()
    except DatabaseError:
        company = None
    except Exception:
        company = None

    return {
        "website_settings": settings,
        "company": company,
    }
