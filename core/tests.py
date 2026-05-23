from decimal import Decimal
from core.test_utils import DERPTenantTestCase as TestCase
from django.urls import reverse
from django.utils import timezone
from core.models import User, Company, Role
from inventory.models import Product, ProductType, StockOnHand
from accounting.models import Account, AccountType, JournalEntry, JournalLine


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

    def test_home_page_does_not_show_low_stock_alerts_when_above_threshold(self):
        self.client.login(username="testuser", password="password")
        
        # Set stock to 10 (above threshold 5)
        StockOnHand.objects.create(product=self.product, qty=Decimal("10.0000"))
        
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["low_stock_products"]), 0)
        self.assertNotContains(response, "Low Stock Alerts")


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
