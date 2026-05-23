from django.conf import settings
from django.core.management.base import BaseCommand

from tenants.models import Domain, TenantCompany


class Command(BaseCommand):
    help = "Ensure the public tenant and its domain records exist."

    def handle(self, *args, **options):
        base = settings.BASE_DOMAIN

        tenant, created = TenantCompany.objects.get_or_create(
            schema_name="public",
            defaults={"name": "Public"},
        )
        if created:
            self.stdout.write("Created public tenant.")

        for hostname in (base, f"www.{base}"):
            _, dom_created = Domain.objects.get_or_create(
                domain=hostname,
                defaults={"tenant": tenant, "is_primary": hostname == base},
            )
            if dom_created:
                self.stdout.write(f"Registered domain: {hostname}")

        self.stdout.write(self.style.SUCCESS("Public tenant ready."))
