from .models import WebsiteSettings, Company

def website_context(request):
    try:
        settings = WebsiteSettings.get()
    except Exception:
        settings = None
    return {
        "website_settings": settings,
        "company": Company.get(),
    }
