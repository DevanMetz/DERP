from django_tenants.test.cases import TenantTestCase
from django_tenants.test.client import TenantClient


class DERPTenantTestCase(TenantTestCase):
    @classmethod
    def setup_tenant(cls, tenant):
        tenant.name = "Test Tenant"

    @classmethod
    def setup_domain(cls, domain):
        domain.is_primary = True

    def _pre_setup(self):
        super()._pre_setup()
        self.client = TenantClient(self.tenant)
