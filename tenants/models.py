from datetime import timedelta

from django.db import models
from django.utils import timezone
from django_tenants.models import TenantMixin, DomainMixin


class SignupAttempt(models.Model):
    ip = models.GenericIPAddressField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "tenants"

    @classmethod
    def is_limited(cls, ip, max_attempts=5, window_hours=1):
        cutoff = timezone.now() - timedelta(hours=window_hours)
        return cls.objects.filter(ip=ip, created_at__gte=cutoff).count() >= max_attempts

    @classmethod
    def record(cls, ip):
        cls.objects.create(ip=ip)


class TenantCompany(TenantMixin):
    name = models.CharField(max_length=200)
    created_on = models.DateField(auto_now_add=True)

    # django-tenants will auto-create the schema when this is saved
    auto_create_schema = True

    def __str__(self):
        return self.name


class Domain(DomainMixin):
    pass
