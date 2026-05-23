from datetime import timedelta
from unittest.mock import patch

from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from .models import PendingTenant
from .views import (
    _pending_subdomain_from_hostname,
    features,
    landing,
    robots_txt,
    signup,
    sitemap_xml,
    tenant_not_found,
)


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


class PublicSiteUrlTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(
        BASE_DOMAIN="workspaces.example",
        ALLOWED_HOSTS=["public.example", "workspaces.example", ".workspaces.example"],
        DEBUG=False,
    )
    def test_landing_metadata_uses_request_host_and_workspace_links_use_base_domain(self):
        request = self.factory.get("/", HTTP_HOST="public.example", secure=True)
        response = landing(request)
        content = response.content.decode()

        self.assertIn('rel="canonical" href="https://public.example/"', content)
        self.assertIn('property="og:url" content="https://public.example/"', content)
        self.assertIn('"url": "https://public.example/"', content)
        self.assertIn("<span>.workspaces.example</span>", content)
        self.assertIn("const workspaceLoginBaseUrl = 'https://workspaces.example/';", content)
        self.assertNotIn("inventorymanager.xyz", content)

    @override_settings(
        BASE_DOMAIN="workspaces.example",
        ALLOWED_HOSTS=["public.example", "workspaces.example", ".workspaces.example"],
        DEBUG=False,
    )
    def test_features_metadata_uses_request_host(self):
        request = self.factory.get("/features/", HTTP_HOST="public.example", secure=True)
        response = features(request)
        content = response.content.decode()

        self.assertIn('rel="canonical" href="https://public.example/features/"', content)
        self.assertIn('property="og:url" content="https://public.example/features/"', content)
        self.assertNotIn("inventorymanager.xyz", content)

    @override_settings(
        BASE_DOMAIN="workspaces.example",
        ALLOWED_HOSTS=["public.example", "workspaces.example", ".workspaces.example"],
        DEBUG=False,
    )
    def test_robots_and_sitemap_use_request_host(self):
        request = self.factory.get("/robots.txt", HTTP_HOST="public.example", secure=True)
        response = robots_txt(request)
        self.assertContains(response, "Sitemap: https://public.example/sitemap.xml")

        request = self.factory.get("/sitemap.xml", HTTP_HOST="public.example", secure=True)
        response = sitemap_xml(request)
        content = response.content.decode()
        self.assertIn("<loc>https://public.example/</loc>", content)
        self.assertIn("<loc>https://public.example/features/</loc>", content)
        self.assertIn("<loc>https://public.example/signup/</loc>", content)
        self.assertNotIn("inventorymanager.xyz", content)

    @override_settings(
        BASE_DOMAIN="workspaces.example",
        ALLOWED_HOSTS=["public.example", "workspaces.example", ".workspaces.example"],
        DEBUG=False,
    )
    def test_signup_uses_configured_domain_and_request_public_url(self):
        request = self.factory.get("/signup/", HTTP_HOST="public.example", secure=True)
        response = signup(request)
        content = response.content.decode()

        self.assertIn('<span class="subdomain-suffix">.workspaces.example</span>', content)
        self.assertIn('href="https://public.example/"', content)
        self.assertNotIn("inventorymanager.xyz", content)

    @override_settings(
        BASE_DOMAIN="workspaces.example",
        ALLOWED_HOSTS=["public.example", "workspaces.example", ".workspaces.example"],
        DEBUG=False,
    )
    @patch("tenants.views._send_email_via_resend")
    def test_signup_confirmation_link_uses_request_host(self, send_email):
        request = self.factory.post(
            "/signup/",
            {
                "company_name": "Acme",
                "subdomain": "acme",
                "email": "owner@example.com",
                "password1": "correct horse battery staple 123",
                "password2": "correct horse battery staple 123",
            },
            HTTP_HOST="public.example",
            secure=True,
        )

        response = signup(request)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(send_email.called)
        text_body = send_email.call_args.kwargs["text_body"]
        self.assertIn("https://public.example/signup/confirm/", text_body)
        self.assertIn("You requested a workspace at acme.workspaces.example.", text_body)
        self.assertNotIn("inventorymanager.xyz", text_body)
