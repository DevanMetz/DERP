from django.core.management.base import BaseCommand
from tenants.models import SignupAttempt


class Command(BaseCommand):
    help = "Clear all signup rate-limit attempts (use when testing or to unblock an IP)"

    def add_arguments(self, parser):
        parser.add_argument("--ip", help="Clear only this IP address")

    def handle(self, *args, **options):
        qs = SignupAttempt.objects.all()
        if options["ip"]:
            qs = qs.filter(ip=options["ip"])
        count, _ = qs.delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {count} signup attempt record(s)."))
