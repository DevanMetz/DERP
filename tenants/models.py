import uuid
from datetime import timedelta

from django.contrib.auth.hashers import make_password
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


class PendingTenant(models.Model):
    token = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)
    company_name = models.CharField(max_length=200)
    subdomain = models.SlugField(max_length=63)
    email = models.EmailField()
    password_hash = models.CharField(max_length=256)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "tenants"

    @classmethod
    def create_for(cls, company_name, subdomain, email, raw_password):
        # Remove any previous pending record for this subdomain or email
        cls.objects.filter(models.Q(subdomain=subdomain) | models.Q(email=email)).delete()
        return cls.objects.create(
            company_name=company_name,
            subdomain=subdomain,
            email=email,
            password_hash=make_password(raw_password),
        )

    def is_expired(self):
        return timezone.now() > self.created_at + timedelta(hours=24)


class TenantCompany(TenantMixin):
    name = models.CharField(max_length=200)
    created_on = models.DateField(auto_now_add=True)

    # django-tenants will auto-create the schema when this is saved
    auto_create_schema = True

    def __str__(self):
        return self.name


class Domain(DomainMixin):
    pass
