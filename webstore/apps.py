from django.apps import AppConfig


class WebstoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "webstore"

    def ready(self):
        # Register startup checks (FIELD_ENCRYPTION_KEY, Stripe Connect)
        from . import checks  # noqa: F401
