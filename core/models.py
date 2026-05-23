"""
Core models: User and Company.

Company is a singleton — single-tenant deployments means there is exactly
one Company row per database. We enforce that with a unique-on-constant
trick so the constraint shows up in migrations.
"""

from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords

from .numbering import DocumentCounter  # noqa: F401 — register model with the app


class Role(models.TextChoices):
    ADMIN = "admin", "Admin"
    MANAGER = "manager", "Manager"
    STAFF = "staff", "Staff"
    READONLY = "readonly", "Read-only"


class User(AbstractUser):
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STAFF)
    history = HistoricalRecords()

    @property
    def can_post_journal(self) -> bool:
        return self.role in {Role.ADMIN, Role.MANAGER}

    @property
    def can_void(self) -> bool:
        return self.role == Role.ADMIN


class Company(models.Model):
    """
    Singleton: enforced by the `singleton_key` unique constant.
    """
    singleton_key = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)

    name = models.CharField(max_length=200)
    legal_name = models.CharField(max_length=200, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    address = models.TextField(blank=True)
    tax_id = models.CharField(max_length=50, blank=True)

    # Default currency is locked to USD per the spec. Stored as a field
    # anyway so a future multi-currency migration has somewhere to land.
    default_currency = models.CharField(max_length=3, default="USD", editable=False)

    fiscal_year_start_month = models.PositiveSmallIntegerField(default=1)  # January
    fiscal_year_start_day = models.PositiveSmallIntegerField(default=1)

    history = HistoricalRecords()

    class Meta:
        verbose_name = "Company"
        verbose_name_plural = "Company"

    def __str__(self):
        return self.name

    @classmethod
    def get(cls) -> "Company":
        """Get the singleton Company, creating a stub if missing."""
        obj, _ = cls.objects.get_or_create(
            singleton_key=1,
            defaults={"name": "My Company"},
        )
        return obj

    def save(self, *args, **kwargs):
        self.singleton_key = 1  # force
        super().save(*args, **kwargs)


class WriteAttempt(models.Model):
    """Per-tenant write rate limit ledger. One row per high-volume create."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["ip", "created_at"]),
        ]

    @classmethod
    def is_limited(cls, user=None, ip=None, max_per_minute=100):
        cutoff = timezone.now() - timedelta(minutes=1)
        qs = cls.objects.filter(created_at__gte=cutoff)
        if user is not None and getattr(user, "is_authenticated", False):
            return qs.filter(user=user).count() >= max_per_minute
        if ip:
            return qs.filter(ip=ip).count() >= max_per_minute
        return False

    @classmethod
    def record(cls, user=None, ip=None):
        cls.objects.create(
            user=user if user is not None and getattr(user, "is_authenticated", False) else None,
            ip=ip or None,
        )

    @classmethod
    def prune(cls, older_than_minutes=10):
        cutoff = timezone.now() - timedelta(minutes=older_than_minutes)
        cls.objects.filter(created_at__lt=cutoff).delete()
