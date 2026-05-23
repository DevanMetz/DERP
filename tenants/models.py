from django.db import models
from django_tenants.models import TenantMixin, DomainMixin


class TenantCompany(TenantMixin):
    name = models.CharField(max_length=200)
    created_on = models.DateField(auto_now_add=True)

    # django-tenants will auto-create the schema when this is saved
    auto_create_schema = True

    def __str__(self):
        return self.name


class Domain(DomainMixin):
    pass
