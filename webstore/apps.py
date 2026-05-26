from django.apps import AppConfig


class WebstoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "webstore"

    def ready(self):
        # Register direct Stripe Checkout configuration checks.
        from . import checks  # noqa: F401
