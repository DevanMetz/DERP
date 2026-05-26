from .models import Company, WebsiteSettings


def website_context(request):
    """Expose this installation's website settings and company."""
    return {
        "website_settings": WebsiteSettings.get(),
        "company": Company.get(),
    }
