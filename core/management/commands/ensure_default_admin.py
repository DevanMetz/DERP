import os
import secrets

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from core.models import Role


class Command(BaseCommand):
    help = "Create a bootstrap admin account when the database has no users."

    def handle(self, *args, **options):
        User = get_user_model()
        user_count = User.objects.count()
        if user_count:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Default admin skipped; {user_count} user account(s) already exist."
                )
            )
            return

        email = os.environ.get("DERP_DEFAULT_ADMIN_EMAIL", "").strip() or "admin@example.com"
        username = os.environ.get("DERP_DEFAULT_ADMIN_USERNAME", "").strip() or "admin"
        password = os.environ.get("DERP_DEFAULT_ADMIN_PASSWORD", "").strip()
        generated_password = False

        if not password:
            password = secrets.token_urlsafe(18)
            generated_password = True

        user = User.objects.create_superuser(
            username=username,
            email=email,
            password=password,
            role=Role.ADMIN,
        )

        try:
            from allauth.account.models import EmailAddress

            EmailAddress.objects.get_or_create(
                user=user,
                email=email,
                defaults={"verified": True, "primary": True},
            )
        except Exception as exc:  # pragma: no cover - defensive for auth package changes
            self.stdout.write(
                self.style.WARNING(f"Admin user created, but email metadata was not seeded: {exc}")
            )

        self.stdout.write(self.style.SUCCESS("Created default admin login."))
        self.stdout.write(f"Email: {email}")
        self.stdout.write(f"Username: {username}")
        if generated_password:
            self.stdout.write(f"Password: {password}")
            self.stdout.write("Save this password now; it is only printed during this bootstrap run.")
        else:
            self.stdout.write("Password: value from DERP_DEFAULT_ADMIN_PASSWORD")
