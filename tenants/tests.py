from datetime import timedelta

from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from .models import PendingTenant
from .views import _pending_subdomain_from_hostname, tenant_not_found


class PendingWorkspaceViewTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(BASE_DOMAIN="inventorymanager.test", ALLOWED_HOSTS=[".inventorymanager.test"], DEBUG=False)
    def test_extracts_pending_subdomain_from_host(self):
        self.assertEqual(
            _pending_subdomain_from_hostname("acme.inventorymanager.test"),
            "acme",
        )
        self.assertIsNone(_pending_subdomain_from_hostname("inventorymanager.test"))
        self.assertIsNone(_pending_subdomain_from_hostname("x.y.inventorymanager.test"))

    @override_settings(BASE_DOMAIN="inventorymanager.test", ALLOWED_HOSTS=[".inventorymanager.test"])
    def test_pending_subdomain_renders_verification_page(self):
        PendingTenant.create_for(
            company_name="Acme",
            subdomain="acme",
            email="owner@example.com",
            raw_password="not-used-in-test",
        )

        request = self.factory.get("/", HTTP_HOST="acme.inventorymanager.test")
        response = tenant_not_found(request)

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "not verified yet", status_code=404)
        self.assertContains(response, "acme.inventorymanager.test", status_code=404)
        self.assertContains(response, 'href="https://inventorymanager.test/signup/"', status_code=404)
        self.assertContains(response, 'href="https://inventorymanager.test/"', status_code=404)

    @override_settings(BASE_DOMAIN="inventorymanager.test", ALLOWED_HOSTS=[".inventorymanager.test"], DEBUG=False)
    def test_expired_pending_subdomain_renders_expired_message(self):
        pending = PendingTenant.create_for(
            company_name="Acme",
            subdomain="acme",
            email="owner@example.com",
            raw_password="not-used-in-test",
        )
        PendingTenant.objects.filter(pk=pending.pk).update(
            created_at=timezone.now() - timedelta(hours=25),
        )

        request = self.factory.get("/", HTTP_HOST="acme.inventorymanager.test")
        response = tenant_not_found(request)

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Verification expired", status_code=404)

    @override_settings(BASE_DOMAIN="inventorymanager.test", ALLOWED_HOSTS=[".inventorymanager.test"])
    def test_unknown_subdomain_renders_workspace_not_found(self):
        request = self.factory.get("/", HTTP_HOST="missing.inventorymanager.test")
        response = tenant_not_found(request)

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Workspace not found", status_code=404)
        self.assertContains(response, 'href="https://inventorymanager.test/"', status_code=404)
