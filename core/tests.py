from decimal import Decimal
from django.test import TestCase
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
