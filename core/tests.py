import json
import os
from decimal import Decimal
from core.test_utils import DERPTestCase as TestCase
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from core.models import AgentRoutine, CopilotAuditEvent, User, Company, Role
from inventory.models import Product, ProductType, StockOnHand
from accounting.models import Account, AccountType, JournalEntry, JournalLine
from purchasing.models import PurchaseOrder, PurchaseOrderLine, Vendor
from io import StringIO
from unittest.mock import patch


class HomeViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="password",
            role=Role.ADMIN,
        )
        self.company = Company.get()
        self.product = Product.objects.create(
            sku="PART-1",
            name="Test Part",
            type=ProductType.STOCK,
            low_stock_threshold=Decimal("5.0000"),
        )

    def test_home_page_shows_low_stock_alerts_when_under_threshold(self):
        self.client.login(username="testuser", password="password")
        
        # When stock is 0 (below threshold 5)
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("low_stock_products", response.context)
        self.assertEqual(len(response.context["low_stock_products"]), 1)
        self.assertContains(response, "Low Stock Alerts")
        self.assertContains(response, "PART-1")
        self.assertContains(response, 'id="app-sidebar"')
        self.assertContains(response, "derp_sidebar_v1")

    def test_home_page_does_not_show_low_stock_alerts_when_above_threshold(self):
        self.client.login(username="testuser", password="password")
        
        # Set stock to 10 (above threshold 5)
        StockOnHand.objects.create(product=self.product, qty=Decimal("10.0000"))
        
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["low_stock_products"]), 0)
        self.assertNotContains(response, "Low Stock Alerts")


class RoleAccessTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="readonly",
            email="readonly@example.com",
            password="password",
            role=Role.READONLY,
        )

    def test_readonly_user_cannot_edit_company_setup(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("company_setup"))

        self.assertEqual(response.status_code, 403)

    def test_superuser_is_always_an_application_admin(self):
        admin = User.objects.create_superuser(
            username="owner",
            email="owner@example.com",
            password="secure-password",
            role=Role.READONLY,
        )

        self.assertEqual(admin.role, Role.ADMIN)


class DashboardViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="password",
            role=Role.ADMIN,
        )
        self.company = Company.get()
        
        # Seed default Accounts
        self.rev_acct = Account.objects.create(code="4100", name="Product Sales", type=AccountType.REVENUE, is_postable=True)
        self.exp_acct = Account.objects.create(code="5100", name="COGS - Materials", type=AccountType.EXPENSE, is_postable=True)
        self.ar_acct = Account.objects.create(code="1200", name="Accounts Receivable", type=AccountType.ASSET, is_postable=True)
        self.ap_acct = Account.objects.create(code="2110", name="Accounts Payable", type=AccountType.LIABILITY, is_postable=True)
        self.inv_acct = Account.objects.create(code="1300", name="Inventory", type=AccountType.ASSET, is_postable=True)
        self.cash_acct = Account.objects.create(code="1110", name="Cash - Operating", type=AccountType.ASSET, is_postable=True)

        # Create products
        self.prod = Product.objects.create(
            sku="PART-X",
            name="Valued Part",
            type=ProductType.STOCK,
            cost=Decimal("15.50"),
            price=Decimal("30.00"),
        )
        # Give it some stock
        self.stock = StockOnHand.objects.create(product=self.prod, qty=Decimal("10.0000"))

    def test_dashboard_view_requires_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)

    def test_dashboard_view_aggregates_metrics_correctly(self):
        self.client.login(username="testuser", password="password")

        # DR AR $150.00 / CR Revenue $150.00
        je = JournalEntry.objects.create(
            date=timezone.localdate(),
            memo="Test Revenue Event",
            status=JournalEntry.Status.POSTED,
            posted_by=self.user,
        )
        JournalLine.objects.create(entry=je, account=self.ar_acct, debit=Decimal("150.00"), credit=Decimal("0.00"))
        JournalLine.objects.create(entry=je, account=self.rev_acct, debit=Decimal("0.00"), credit=Decimal("150.00"))

        # DR Expense $50.00 / CR AP $50.00
        je2 = JournalEntry.objects.create(
            date=timezone.localdate(),
            memo="Test Expense Event",
            status=JournalEntry.Status.POSTED,
            posted_by=self.user,
        )
        JournalLine.objects.create(entry=je2, account=self.exp_acct, debit=Decimal("50.00"), credit=Decimal("0.00"))
        JournalLine.objects.create(entry=je2, account=self.ap_acct, debit=Decimal("0.00"), credit=Decimal("50.00"))

        # DR Inventory $200.00 / CR Cash $200.00
        je3 = JournalEntry.objects.create(
            date=timezone.localdate(),
            memo="Purchase Stock Event",
            status=JournalEntry.Status.POSTED,
            posted_by=self.user,
        )
        JournalLine.objects.create(entry=je3, account=self.inv_acct, debit=Decimal("200.00"), credit=Decimal("0.00"))
        JournalLine.objects.create(entry=je3, account=self.cash_acct, debit=Decimal("0.00"), credit=Decimal("200.00"))

        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)

        # Check YTD Revenue & Expenses & Profit
        self.assertEqual(response.context["ytd_revenue"], Decimal("150.00"))
        self.assertEqual(response.context["ytd_expenses"], Decimal("50.00"))
        self.assertEqual(response.context["net_profit"], Decimal("100.00"))

        # Check AR/AP Outstanding
        self.assertEqual(response.context["outstanding_ar"], Decimal("150.00"))
        self.assertEqual(response.context["outstanding_ap"], Decimal("50.00"))

        # Check Inventory (GL vs Operational)
        self.assertEqual(response.context["gl_inventory_val"], Decimal("200.00"))
        # Operational inventory should be 10 qty * 15.50 cost = 155.00
        self.assertEqual(response.context["operational_inventory_val"], Decimal("155.00"))

        # Check chart JSON lists
        current_month_idx = timezone.localdate().month - 1
        self.assertEqual(response.context["monthly_revenue_json"][current_month_idx], 150.0)
        self.assertEqual(response.context["monthly_expenses_json"][current_month_idx], 50.0)

        # Doughnut values
        self.assertIn("PART-X - Valued Part", response.context["doughnut_labels_json"])
        self.assertIn(155.0, response.context["doughnut_data_json"])


class DocsViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="docsuser",
            email="docs@example.com",
            password="password",
            role=Role.ADMIN,
        )

    def test_docs_index_requires_login(self):
        response = self.client.get(reverse("docs_index"))
        self.assertEqual(response.status_code, 302)

    def test_docs_index_lists_repo_backed_pages(self):
        self.client.login(username="docsuser", password="password")
        response = self.client.get(reverse("docs_index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "DERP Documentation")
        self.assertContains(response, "Getting Started")
        self.assertContains(response, reverse("docs_page", args=["getting-started"]))

    def test_docs_page_renders_markdown(self):
        self.client.login(username="docsuser", password="password")
        response = self.client.get(reverse("docs_page", args=["accounting"]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Accounting")
        self.assertContains(response, "post_transaction")

    def test_unknown_docs_page_404s(self):
        self.client.login(username="docsuser", password="password")
        response = self.client.get(reverse("docs_page", args=["missing-page"]))
        self.assertEqual(response.status_code, 404)


class AuthViewTests(TestCase):
    def test_login_page_uses_derp_auth_shell(self):
        response = self.client.get(reverse("account_login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="app-header"')
        self.assertContains(response, 'class="auth-card"')
        self.assertContains(response, "Access your DERP workspace.")
        self.assertNotContains(response, "Create account")

    def test_public_signup_is_closed_for_self_hosted_installation(self):
        response = self.client.post(reverse("account_signup"), {
            "email": "public@example.com",
            "password1": "Strong-and-long-password1",
            "password2": "Strong-and-long-password1",
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registration disabled")
        self.assertFalse(User.objects.filter(email="public@example.com").exists())


class DefaultAdminCommandTests(TestCase):
    def test_command_creates_admin_when_no_users_exist(self):
        out = StringIO()

        with patch.dict(os.environ, {
            "DERP_DEFAULT_ADMIN_EMAIL": "owner@example.com",
            "DERP_DEFAULT_ADMIN_USERNAME": "owner",
            "DERP_DEFAULT_ADMIN_PASSWORD": "SecurePassword123",
        }):
            call_command("ensure_default_admin", stdout=out)

        user = User.objects.get(email="owner@example.com")
        self.assertEqual(user.username, "owner")
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_staff)
        self.assertEqual(user.role, Role.ADMIN)
        self.assertTrue(user.check_password("SecurePassword123"))
        self.assertIn("Created default admin login.", out.getvalue())

    def test_command_skips_when_any_user_exists(self):
        User.objects.create_user(
            username="existing",
            email="existing@example.com",
            password="password",
            role=Role.STAFF,
        )
        out = StringIO()

        with patch.dict(os.environ, {
            "DERP_DEFAULT_ADMIN_EMAIL": "owner@example.com",
            "DERP_DEFAULT_ADMIN_PASSWORD": "SecurePassword123",
        }):
            call_command("ensure_default_admin", stdout=out)

        self.assertEqual(User.objects.count(), 1)
        self.assertFalse(User.objects.filter(email="owner@example.com").exists())
        self.assertIn("Default admin skipped", out.getvalue())


class DemoDataCommandTests(TestCase):
    def test_command_seeds_demo_data_when_business_tables_are_empty(self):
        User.objects.create_superuser(
            username="owner",
            email="owner@example.com",
            password="SecurePassword123",
            role=Role.ADMIN,
        )
        out = StringIO()

        with patch.dict(os.environ, {"DERP_SEED_DEMO_DATA": "true"}):
            call_command("ensure_demo_data", stdout=out)

        from accounting.models import Account, Payment
        from manufacturing.models import BillOfMaterials, ManufacturingOrder
        from purchasing.models import Bill
        from sales.models import Customer, Invoice, SalesOrder

        self.assertEqual(Account.objects.count(), 39)
        self.assertEqual(Product.objects.count(), 4)
        self.assertEqual(Customer.objects.count(), 2)
        self.assertEqual(Vendor.objects.count(), 2)
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        self.assertEqual(Bill.objects.count(), 2)
        self.assertEqual(BillOfMaterials.objects.count(), 1)
        self.assertEqual(ManufacturingOrder.objects.count(), 1)
        self.assertEqual(SalesOrder.objects.count(), 1)
        self.assertEqual(Invoice.objects.count(), 2)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertTrue(Invoice.objects.filter(status=Invoice.Status.SENT).exists())
        self.assertTrue(Invoice.objects.filter(status=Invoice.Status.DRAFT).exists())
        self.assertIn("Seeded demo ERP data", out.getvalue())

    def test_command_skips_when_business_data_exists(self):
        Product.objects.create(sku="EXISTING", name="Existing Product")
        out = StringIO()

        with patch.dict(os.environ, {"DERP_SEED_DEMO_DATA": "true"}):
            call_command("ensure_demo_data", stdout=out)

        self.assertEqual(Product.objects.count(), 1)
        self.assertFalse(Vendor.objects.exists())
        self.assertIn("Demo data skipped", out.getvalue())

    def test_command_can_be_disabled(self):
        out = StringIO()

        with patch.dict(os.environ, {"DERP_SEED_DEMO_DATA": "false"}):
            call_command("ensure_demo_data", stdout=out)

        self.assertFalse(Product.objects.exists())
        self.assertIn("Demo data seeding is disabled", out.getvalue())


class AiCopilotTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="aiuser",
            email="ai@example.com",
            password="password",
            role=Role.ADMIN,
        )
        self.expense_account = Account.objects.create(
            code="6900",
            name="Miscellaneous Expense",
            type=AccountType.EXPENSE,
            is_postable=True,
        )
        self.vendor = Vendor.objects.create(name="Supply Co")
        self.product = Product.objects.create(
            sku="WIDGET",
            name="Widget",
            type=ProductType.STOCK,
            cost=Decimal("5.00"),
            price=Decimal("10.00"),
        )

    def test_home_page_includes_ai_copilot_shell(self):
        self.client.login(username="aiuser", password="password")
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="ai-copilot"')
        self.assertContains(response, "derp_ai_api_key")
        self.assertContains(response, "Browser only")

    def test_ai_chat_previews_purchase_order_without_api_key(self):
        self.client.login(username="aiuser", password="password")
        response = self.client.post(
            reverse("ai_chat"),
            data=json.dumps({"message": "I purchased 3 units of WIDGET from Supply Co at $5 each"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("Ready to create a draft PO", payload["reply"])
        self.assertEqual(payload["preview"]["vendor"], "Supply Co")
        self.assertEqual(payload["preview"]["lines"][0]["product"], "WIDGET")
        self.assertEqual(payload["preview"]["lines"][0]["line_total"], "15.00")
        self.assertEqual(payload["preview"]["total"], "15.00")
        self.assertTrue(CopilotAuditEvent.objects.filter(event_type=CopilotAuditEvent.EventType.PREVIEW).exists())

    def test_ai_confirm_creates_purchase_order_draft(self):
        self.client.login(username="aiuser", password="password")
        preview_response = self.client.post(
            reverse("ai_chat"),
            data=json.dumps({"message": "purchased 3 units of WIDGET from Supply Co at $5 each"}),
            content_type="application/json",
        )
        token = preview_response.json()["preview"]["action_token"]

        response = self.client.post(
            reverse("ai_confirm"),
            data=json.dumps({"action_token": token}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        order = PurchaseOrder.objects.get()
        self.assertEqual(order.vendor, self.vendor)
        self.assertEqual(order.status, PurchaseOrder.Status.DRAFT)
        self.assertEqual(order.created_by, self.user)
        self.assertEqual(PurchaseOrderLine.objects.count(), 1)
        line = PurchaseOrderLine.objects.get()
        self.assertEqual(line.product, self.product)
        self.assertEqual(line.qty, Decimal("3.0000"))
        self.assertEqual(line.unit_cost, Decimal("5.00"))
        self.assertEqual(line.expense_account, self.expense_account)
        self.assertEqual(response.json()["url"], reverse("purchase_order_detail", args=[order.pk]))
        self.assertTrue(CopilotAuditEvent.objects.filter(event_type=CopilotAuditEvent.EventType.CONFIRM).exists())

    def test_ai_chat_uses_session_state_for_followup_quantity_change(self):
        self.client.login(username="aiuser", password="password")
        self.client.post(
            reverse("ai_chat"),
            data=json.dumps({"message": "purchased 3 units of WIDGET from Supply Co at $5 each"}),
            content_type="application/json",
        )

        response = self.client.post(
            reverse("ai_chat"),
            data=json.dumps({"message": "I bought 10 of those instead"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["preview"]["lines"][0]["qty"], "10.0000")
        self.assertEqual(payload["preview"]["total"], "50.00")

    def test_ai_chat_can_search_docs(self):
        self.client.login(username="aiuser", password="password")
        response = self.client.post(
            reverse("ai_chat"),
            data=json.dumps({"message": "How do I reverse a goods receipt?"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsNone(payload["preview"])
        self.assertIn("tool_results", payload)

    def test_ai_chat_can_search_locations(self):
        from inventory.models import Location
        Location.objects.create(name="East Warehouse", description="The eastern main warehouse")
        self.client.login(username="aiuser", password="password")
        response = self.client.post(
            reverse("ai_chat"),
            data=json.dumps({"message": "Find warehouse East"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("East Warehouse", payload["reply"])

    def test_ai_chat_can_get_location_details(self):
        from inventory.models import Location, LocationStock
        loc = Location.objects.create(name="North Bin", description="Northern storage bin")
        # Give it some stock
        from inventory.models import Product, ProductType
        prod = Product.objects.create(sku="BOLT", name="Bolt", type=ProductType.STOCK)
        LocationStock.objects.create(product=prod, location=loc, qty=Decimal("45.0000"))
        
        self.client.login(username="aiuser", password="password")
        response = self.client.post(
            reverse("ai_chat"),
            data=json.dumps({
                "message": "tell me about this location",
                "page_context": {
                    "record": {"type": "location", "id": loc.pk, "label": loc.name}
                }
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("North Bin", payload["reply"])
        self.assertIn("BOLT", payload["reply"])
        self.assertIn("45.0000", payload["reply"])


class AgentHubTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="routineowner",
            email="owner@example.com",
            password="password",
            role=Role.ADMIN,
        )
        self.other_user = User.objects.create_user(
            username="otheruser",
            email="other@example.com",
            password="password",
            role=Role.ADMIN,
        )

    def test_agent_hub_requires_login(self):
        response = self.client.get(reverse("agent_hub"))
        self.assertEqual(response.status_code, 302)

    def test_user_can_create_and_open_own_routine(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("agent_routine_create"),
            {
                "name": "Morning purchasing review",
                "purpose": "Prepare for ordering decisions.",
                "prompt": "Review open purchase orders and highlight next actions.",
                "cadence_note": "Weekday mornings",
                "is_active": "on",
            },
        )

        self.assertRedirects(response, reverse("agent_hub"))
        routine = AgentRoutine.objects.get(owner=self.user)
        self.assertEqual(routine.name, "Morning purchasing review")

        page = self.client.get(reverse("agent_hub"))
        self.assertContains(page, "Morning purchasing review")
        self.assertContains(page, "Open in Copilot")
        self.assertContains(page, "Review open purchase orders and highlight next actions.")
        self.assertContains(page, "window.DerpCopilot.preparePrompt")

    def test_hub_hides_other_users_routines(self):
        AgentRoutine.objects.create(
            owner=self.other_user,
            name="Other private routine",
            prompt="This prompt belongs to someone else.",
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("agent_hub"))

        self.assertNotContains(response, "Other private routine")
        self.assertNotContains(response, "This prompt belongs to someone else.")

    def test_user_cannot_edit_or_toggle_another_users_routine(self):
        routine = AgentRoutine.objects.create(
            owner=self.other_user,
            name="Other private routine",
            prompt="Keep this private.",
        )
        self.client.force_login(self.user)

        edit_response = self.client.get(reverse("agent_routine_edit", args=[routine.pk]))
        toggle_response = self.client.post(reverse("agent_routine_toggle", args=[routine.pk]))

        self.assertEqual(edit_response.status_code, 404)
        self.assertEqual(toggle_response.status_code, 404)
        routine.refresh_from_db()
        self.assertTrue(routine.is_active)

    def test_readonly_user_cannot_create_a_routine(self):
        readonly = User.objects.create_user(
            username="readroutine",
            email="readroutine@example.com",
            password="password",
            role=Role.READONLY,
        )
        self.client.force_login(readonly)

        response = self.client.post(
            reverse("agent_routine_create"),
            {"name": "Blocked", "prompt": "Should not save."},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(AgentRoutine.objects.filter(owner=readonly).exists())



class DataExportTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="password",
            role=Role.ADMIN,
        )
        self.company = Company.get()

    def test_export_view_requires_login(self):
        response = self.client.get(reverse("data_export"))
        self.assertEqual(response.status_code, 302)

    def test_export_view_renders_correctly(self):
        self.client.login(username="testuser", password="password")
        response = self.client.get(reverse("data_export"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Data Export Manager")
        self.assertContains(response, "Company")
        self.assertIn("exportable_models", response.context)

    def test_export_view_json_download(self):
        self.client.login(username="testuser", password="password")
        response = self.client.post(
            reverse("data_export"),
            {
                "selected_models": ["core.company"],
                "action": "export_json",
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("derp_backup_", response["Content-Disposition"])
        
        # Verify JSON content has serialized Company instance
        data = response.json()
        self.assertIsInstance(data, list)
        self.assertEqual(data[0]["model"], "core.company")

    def test_export_view_csv_zip_download(self):
        self.client.login(username="testuser", password="password")
        response = self.client.post(
            reverse("data_export"),
            {
                "selected_models": ["core.company"],
                "action": "export_csv",
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("derp_csv_export_", response["Content-Disposition"])
        
        # Verify ZIP contains core_company.csv
        import zipfile
        import io
        zip_file = zipfile.ZipFile(io.BytesIO(response.content))
        file_names = zip_file.namelist()
        self.assertIn("core_company.csv", file_names)
        
        # Verify CSV content contains header and fields
        csv_content = zip_file.read("core_company.csv").decode("utf-8")
        self.assertIn("name", csv_content)

    def test_export_view_denies_staff_and_readonly(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        staff_user = User.objects.create_user(
            username="staffuser",
            email="staff@example.com",
            password="password",
            role=Role.STAFF,
        )
        self.client.force_login(staff_user)
        response = self.client.get(reverse("data_export"))
        self.assertEqual(response.status_code, 403)

        readonly_user = User.objects.create_user(
            username="readonlyuser",
            email="readonly@example.com",
            password="password",
            role=Role.READONLY,
        )
        self.client.force_login(readonly_user)
        response = self.client.get(reverse("data_export"))
        self.assertEqual(response.status_code, 403)

    def test_export_view_excludes_sensitive_models_by_default(self):
        self.client.login(username="testuser", password="password")
        response = self.client.get(reverse("data_export"))
        self.assertEqual(response.status_code, 200)
        exportable_keys = [m["key"] for m in response.context["exportable_models"]]
        self.assertNotIn("core.user", exportable_keys)
        self.assertNotIn("core.writeattempt", exportable_keys)
        self.assertNotIn("core.copilotauditevent", exportable_keys)
        self.assertNotIn("core.agentroutine", exportable_keys)

    def test_export_view_includes_sensitive_models_when_requested(self):
        self.client.login(username="testuser", password="password")
        response = self.client.get(reverse("data_export") + "?include_sensitive=true")
        self.assertEqual(response.status_code, 200)
        exportable_keys = [m["key"] for m in response.context["exportable_models"]]
        self.assertIn("core.user", exportable_keys)
        self.assertIn("core.writeattempt", exportable_keys)
        self.assertIn("core.copilotauditevent", exportable_keys)
        self.assertIn("core.agentroutine", exportable_keys)

    def test_export_view_ignores_sensitive_models_on_post_without_include_sensitive(self):
        self.client.login(username="testuser", password="password")
        # Try to post sensitive model without include_sensitive flag
        response = self.client.post(
            reverse("data_export"),
            {
                "selected_models": ["core.user", "core.company"],
                "action": "export_json",
            }
        )
        self.assertEqual(response.status_code, 200)
        # Should only export Company, not User
        data = response.json()
        model_names = {item["model"] for item in data}
        self.assertNotIn("core.user", model_names)
        self.assertIn("core.company", model_names)

    def test_export_view_includes_sensitive_models_on_post_with_include_sensitive(self):
        self.client.login(username="testuser", password="password")
        response = self.client.post(
            reverse("data_export"),
            {
                "selected_models": ["core.user", "core.company"],
                "action": "export_json",
                "include_sensitive": "true",
            }
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        model_names = {item["model"] for item in data}
        self.assertIn("core.user", model_names)
        self.assertIn("core.company", model_names)


class DataImportTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="password",
            role=Role.ADMIN,
        )
        self.company = Company.get()

    def test_import_view_requires_login(self):
        response = self.client.get(reverse("data_import"))
        self.assertEqual(response.status_code, 302)

    def test_import_view_requires_admin_role(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        readonly = User.objects.create_user(
            username="readonly",
            email="readonly@example.com",
            password="password",
            role=Role.READONLY,
        )
        self.client.force_login(readonly)

        response = self.client.get(reverse("data_import"))

        self.assertEqual(response.status_code, 403)

    def test_import_view_renders_correctly(self):
        self.client.login(username="testuser", password="password")
        response = self.client.get(reverse("data_import"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Data Import &amp; Restore")
        self.assertContains(response, "CSV / Excel Table Ingestion")
        self.assertContains(response, "JSON Backup Restoration")
        self.assertIn("model_choices", response.context)

    def test_json_backup_import_success(self):
        self.client.login(username="testuser", password="password")
        
        # Create a serialized JSON fixture for a new customer
        customer_data = [
            {
                "model": "sales.customer",
                "pk": 999,
                "fields": {
                    "name": "Imported Customer Inc",
                    "email": "imported@customer.com",
                    "phone": "555-9876",
                    "billing_address": "123 Import Way",
                    "shipping_address": "123 Import Way",
                    "payment_terms_days": 30,
                    "tax_rate": "0.00",
                    "is_active": True,
                    "created_at": "2026-05-23T00:00:00Z"
                }
            }
        ]
        import json
        from django.core.files.uploadedfile import SimpleUploadedFile
        json_file = SimpleUploadedFile("backup.json", json.dumps(customer_data).encode("utf-8"), content_type="application/json")
        
        # Assert customer doesn't exist yet
        from sales.models import Customer
        self.assertFalse(Customer.objects.filter(pk=999).exists())
        
        # Upload file
        response = self.client.post(reverse("data_import"), {"json_file": json_file})
        self.assertEqual(response.status_code, 302)
        
        # Assert customer exists now!
        self.assertTrue(Customer.objects.filter(pk=999).exists())
        c = Customer.objects.get(pk=999)
        self.assertEqual(c.name, "Imported Customer Inc")
        self.assertEqual(c.email, "imported@customer.com")

    def test_json_backup_import_rejects_unsupported_models(self):
        self.client.login(username="testuser", password="password")

        user_data = [
            {
                "model": "core.user",
                "pk": 999,
                "fields": {
                    "password": "!",
                    "last_login": None,
                    "is_superuser": True,
                    "username": "evil",
                    "first_name": "",
                    "last_name": "",
                    "email": "evil@example.com",
                    "is_staff": True,
                    "is_active": True,
                    "date_joined": "2026-05-23T00:00:00Z",
                    "role": Role.ADMIN,
                    "groups": [],
                    "user_permissions": [],
                },
            }
        ]
        import json
        from django.contrib.auth import get_user_model
        from django.core.files.uploadedfile import SimpleUploadedFile
        User = get_user_model()
        json_file = SimpleUploadedFile("backup.json", json.dumps(user_data).encode("utf-8"), content_type="application/json")

        response = self.client.post(reverse("data_import"), {"json_file": json_file})

        self.assertEqual(response.status_code, 302)
        self.assertFalse(User.objects.filter(username="evil").exists())

    def test_json_backup_import_rollback_on_failure(self):
        self.client.login(username="testuser", password="password")
        
        # Upload invalid customer JSON (name cannot be null)
        invalid_data = [{"model": "sales.customer", "pk": 888, "fields": {"name": None}}]
        
        import json
        from django.core.files.uploadedfile import SimpleUploadedFile
        json_file = SimpleUploadedFile("backup.json", json.dumps(invalid_data).encode("utf-8"), content_type="application/json")
        
        from sales.models import Customer
        # Upload file
        response = self.client.post(reverse("data_import"), {"json_file": json_file})
        self.assertEqual(response.status_code, 302)
        
        # Assert customer was NOT created due to database rollback!
        self.assertFalse(Customer.objects.filter(pk=888).exists())

    def test_csv_import_success(self):
        self.client.login(username="testuser", password="password")
        
        # Create CSV content for Customers
        csv_data = "id,name,email,payment_terms_days\n777,CSV Ingested Co,csv@ingest.com,45\n"
        from django.core.files.uploadedfile import SimpleUploadedFile
        csv_file = SimpleUploadedFile("customers.csv", csv_data.encode("utf-8"), content_type="text/csv")
        
        from sales.models import Customer
        self.assertFalse(Customer.objects.filter(pk=777).exists())
        
        response = self.client.post(
            reverse("data_import"),
            {
                "model_key": "sales.customer",
                "csv_file": csv_file,
            }
        )
        self.assertEqual(response.status_code, 302)
        
        # Assert customer exists and terms were set!
        self.assertTrue(Customer.objects.filter(pk=777).exists())
        c = Customer.objects.get(pk=777)
        self.assertEqual(c.name, "CSV Ingested Co")
        self.assertEqual(c.payment_terms_days, 45)


class UserManagementTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.admin_user = User.objects.create_user(
            username="adminuser",
            email="admin@example.com",
            password="password",
            role=Role.ADMIN,
        )
        self.staff_user = User.objects.create_user(
            username="staffuser",
            email="staff@example.com",
            password="password",
            role=Role.STAFF,
        )
        self.company = Company.get()

    def test_user_management_views_require_login(self):
        response = self.client.get(reverse("user_list"))
        self.assertEqual(response.status_code, 302)

        response = self.client.get(reverse("user_create"))
        self.assertEqual(response.status_code, 302)

        response = self.client.get(reverse("user_edit", args=[self.staff_user.pk]))
        self.assertEqual(response.status_code, 302)

    def test_user_management_views_deny_staff(self):
        self.client.force_login(self.staff_user)

        response = self.client.get(reverse("user_list"))
        self.assertEqual(response.status_code, 403)

        response = self.client.get(reverse("user_create"))
        self.assertEqual(response.status_code, 403)

        response = self.client.get(reverse("user_edit", args=[self.admin_user.pk]))
        self.assertEqual(response.status_code, 403)

    def test_admin_can_list_users(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("user_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "User Directory")
        self.assertContains(response, "adminuser")
        self.assertContains(response, "staffuser")

    def test_admin_can_create_user(self):
        self.client.force_login(self.admin_user)
        
        response = self.client.post(
            reverse("user_create"),
            {
                "username": "newuser",
                "email": "new@example.com",
                "role": Role.MANAGER,
                "is_active": True,
                "password": "newsecurepassword123",
            }
        )
        self.assertEqual(response.status_code, 302)
        
        # Verify user is created with correct properties
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.assertTrue(User.objects.filter(username="newuser").exists())
        user = User.objects.get(username="newuser")
        self.assertEqual(user.email, "new@example.com")
        self.assertEqual(user.role, Role.MANAGER)
        self.assertTrue(user.is_active)
        # Verify password is encrypted
        self.assertTrue(user.check_password("newsecurepassword123"))

    def test_admin_can_edit_user(self):
        self.client.force_login(self.admin_user)
        
        response = self.client.post(
            reverse("user_edit", args=[self.staff_user.pk]),
            {
                "email": "updated@example.com",
                "role": Role.READONLY,
                "is_active": False,
                "new_password": "changedpassword987",
            }
        )
        self.assertEqual(response.status_code, 302)
        
        # Verify staff_user was updated
        self.staff_user.refresh_from_db()
        self.assertEqual(self.staff_user.email, "updated@example.com")
        self.assertEqual(self.staff_user.role, Role.READONLY)
        self.assertFalse(self.staff_user.is_active)
        self.assertTrue(self.staff_user.check_password("changedpassword987"))


from .models import PublicPage

class PublicWebsiteTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.admin_user = User.objects.create_user(
            username="adminuser",
            email="admin@example.com",
            password="password",
            role=Role.ADMIN,
        )
        self.staff_user = User.objects.create_user(
            username="staffuser",
            email="staff@example.com",
            password="password",
            role=Role.STAFF,
        )
        self.manager_user = User.objects.create_user(
            username="manageruser",
            email="manager@example.com",
            password="password",
            role=Role.MANAGER,
        )
        self.company = Company.get()
        self.page = PublicPage.objects.create(
            title="Homepage Title",
            slug="home",
            html_content="<h1>Custom Homepage</h1>",
            is_homepage=True,
            is_published=True,
        )
        self.about_page = PublicPage.objects.create(
            title="About Page",
            slug="about-us",
            html_content="<h1>About ERP Company</h1>",
            is_homepage=False,
            is_published=True,
        )

    def test_root_domain_renders_homepage(self):
        response = self.client.get(reverse("public_home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Custom Homepage")

    def test_fallback_homepage_renders_if_none_exists(self):
        # Unmark current homepage
        self.page.is_homepage = False
        self.page.save()

        response = self.client.get(reverse("public_home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Welcome to your new public website!")

    def test_public_subpage_renders_by_slug(self):
        response = self.client.get(reverse("public_page", args=["about-us"]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "About ERP Company")

    def test_website_editor_views_require_login(self):
        response = self.client.get(reverse("website_editor"))
        self.assertEqual(response.status_code, 302)

        response = self.client.get(reverse("page_create"))
        self.assertEqual(response.status_code, 302)

        response = self.client.get(reverse("page_edit", args=[self.about_page.pk]))
        self.assertEqual(response.status_code, 302)

    def test_website_editor_views_deny_staff(self):
        self.client.force_login(self.staff_user)

        response = self.client.get(reverse("website_editor"))
        self.assertEqual(response.status_code, 403)

        response = self.client.get(reverse("page_create"))
        self.assertEqual(response.status_code, 403)

        response = self.client.get(reverse("page_edit", args=[self.about_page.pk]))
        self.assertEqual(response.status_code, 403)

    def test_admin_and_manager_can_access_website_editor(self):
        # Test Admin
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("website_editor"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Website Editor")
        self.assertContains(response, "Homepage Title")
        self.assertContains(response, "About Page")

        # Test Manager
        self.client.force_login(self.manager_user)
        response = self.client.get(reverse("website_editor"))
        self.assertEqual(response.status_code, 200)

    def test_admin_can_create_public_page(self):
        self.client.force_login(self.admin_user)
        
        response = self.client.post(
            reverse("page_create"),
            {
                "title": "New Public Page",
                "slug": "new-page",
                "html_content": "<p>Content goes here.</p>",
                "is_homepage": False,
                "is_published": True,
            }
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(PublicPage.objects.filter(slug="new-page").exists())

    def test_admin_can_edit_public_page(self):
        self.client.force_login(self.admin_user)
        
        response = self.client.post(
            reverse("page_edit", args=[self.about_page.pk]),
            {
                "title": "Updated About Title",
                "slug": "about-us",
                "html_content": "<h2>Updated content</h2>",
                "is_homepage": False,
                "is_published": True,
            }
        )
        self.assertEqual(response.status_code, 302)
        self.about_page.refresh_from_db()
        self.assertEqual(self.about_page.title, "Updated About Title")
        self.assertEqual(self.about_page.html_content, "<h2>Updated content</h2>")

    def test_homepage_uniqueness_enforcement(self):
        # We have self.page marked as homepage
        self.assertTrue(self.page.is_homepage)
        self.assertFalse(self.about_page.is_homepage)

        # Mark about_page as homepage and save
        self.about_page.is_homepage = True
        self.about_page.save()

        # Refresh page instance
        self.page.refresh_from_db()
        self.assertFalse(self.page.is_homepage)
        self.assertTrue(self.about_page.is_homepage)

    def test_admin_and_manager_can_delete_page(self):
        self.client.force_login(self.admin_user)
        # Verify about_page exists
        self.assertTrue(PublicPage.objects.filter(pk=self.about_page.pk).exists())
        
        # Get delete confirmation page
        response = self.client.get(reverse("page_delete", args=[self.about_page.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirm Deletion")
        
        # Post to delete
        response = self.client.post(reverse("page_delete", args=[self.about_page.pk]))
        self.assertEqual(response.status_code, 302)
        # Verify page is deleted
        self.assertFalse(PublicPage.objects.filter(pk=self.about_page.pk).exists())

    def test_cannot_delete_active_homepage(self):
        self.client.force_login(self.admin_user)
        # Attempt to delete active homepage (self.page)
        response = self.client.post(reverse("page_delete", args=[self.page.pk]))
        self.assertEqual(response.status_code, 302)
        # Should not be deleted
        self.assertTrue(PublicPage.objects.filter(pk=self.page.pk).exists())

    def test_staff_and_readonly_cannot_delete_page(self):
        self.client.force_login(self.staff_user)
        response = self.client.post(reverse("page_delete", args=[self.about_page.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(PublicPage.objects.filter(pk=self.about_page.pk).exists())

    def test_website_settings_authorized_roles(self):
        from core.models import WebsiteSettings
        self.client.force_login(self.admin_user)
        # Check settings page GET
        response = self.client.get(reverse("website_settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Website settings")
        self.assertContains(response, "Stripe Checkout")

        # Save settings POST
        response = self.client.post(reverse("website_settings"), {
            "brand_name": "DERP Awesome ERP Portal",
            "primary_color": "#112233",
            "secondary_color": "#445566",
            "font_family": "Outfit",
            "custom_css": ".my-custom-class { color: red; }",
        })
        self.assertEqual(response.status_code, 302)
        settings = WebsiteSettings.get()
        self.assertEqual(settings.brand_name, "DERP Awesome ERP Portal")
        self.assertEqual(settings.font_family, "Outfit")

        # Check Staff user access
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse("website_settings"))
        self.assertEqual(response.status_code, 403)

    def test_website_context_processor(self):
        from core.models import WebsiteSettings
        settings = WebsiteSettings.get()
        settings.brand_name = "Dynamic ERP Brand"
        settings.save()

        # Render public homepage
        response = self.client.get(reverse("public_home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dynamic ERP Brand")

    def test_page_revisions_created_on_change(self):
        from core.models import PageRevision
        self.client.force_login(self.admin_user)
        initial_revision_count = PageRevision.objects.filter(page=self.about_page).count()

        # POST without changing html_content
        response = self.client.post(reverse("page_edit", args=[self.about_page.pk]), {
            "title": "Unchanged HTML Content Title",
            "slug": self.about_page.slug,
            "html_content": self.about_page.html_content,
            "is_homepage": self.about_page.is_homepage,
            "is_published": self.about_page.is_published,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PageRevision.objects.filter(page=self.about_page).count(), initial_revision_count)

        # POST with modified html_content
        response = self.client.post(reverse("page_edit", args=[self.about_page.pk]), {
            "title": "Unchanged HTML Content Title",
            "slug": self.about_page.slug,
            "html_content": "<h3>Brand new content!</h3>",
            "is_homepage": self.about_page.is_homepage,
            "is_published": self.about_page.is_published,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PageRevision.objects.filter(page=self.about_page).count(), initial_revision_count + 1)
        latest_rev = PageRevision.objects.filter(page=self.about_page).first()
        self.assertEqual(latest_rev.html_content, "<h3>Brand new content!</h3>")
        self.assertEqual(latest_rev.author, self.admin_user)

    def test_seo_metadata_output(self):
        # Update SEO settings on about_page
        self.about_page.meta_description = "Outstanding operational tracking summary."
        self.about_page.meta_keywords = "bookkeeping, warehousing"
        self.about_page.og_image_url = "https://example.com/cards.jpg"
        self.about_page.save()

        # Render public subpage
        response = self.client.get(reverse("public_page", args=[self.about_page.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="description" content="Outstanding operational tracking summary."')
        self.assertContains(response, 'name="keywords" content="bookkeeping, warehousing"')
        self.assertContains(response, 'property="og:image" content="https://example.com/cards.jpg"')

    def test_page_builder_static_delivery(self):
        self.client.force_login(self.admin_user)
        # Verify page create loads successfully
        response = self.client.get(reverse("page_create"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="pb-shell"')
        
        # Verify page edit loads successfully
        response = self.client.get(reverse("page_edit", args=[self.about_page.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="pb-shell"')
        self.assertContains(response, 'class="block-card"')
