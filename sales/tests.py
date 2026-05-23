from datetime import date
from decimal import Decimal
from core.test_utils import DERPTenantTestCase as TestCase

from accounting.models import Account, AccountType
from inventory.models import Product, StockOnHand, StockMovement
from inventory.services import post_stock_movement
from sales.models import Customer, Invoice, SalesOrder, SalesOrderLine
from sales.services import (
    confirm_sales_order, create_invoice_from_sales_order,
    undo_confirm_sales_order, undo_invoice_from_sales_order,
    post_invoice, void_invoice
)

D = Decimal


class SalesOrderWorkflowTests(TestCase):
    def setUp(self):
        self.cash = Account.objects.create(code="1110", name="Cash", type=AccountType.ASSET)
        self.ar = Account.objects.create(code="1200", name="Accounts Receivable", type=AccountType.ASSET)
        self.tax = Account.objects.create(code="2120", name="Sales Tax Payable", type=AccountType.LIABILITY)
        self.revenue = Account.objects.create(code="4100", name="Product Sales", type=AccountType.REVENUE)
        self.inventory = Account.objects.create(code="1300", name="Inventory", type=AccountType.ASSET)
        self.cogs = Account.objects.create(code="5100", name="COGS - Materials", type=AccountType.EXPENSE)
        self.customer = Customer.objects.create(name="Acme", payment_terms_days=15)
        self.product = Product.objects.create(
            sku="WIDGET",
            name="Widget",
            price=D("20.00"),
            cost=D("10.00"),
            default_revenue_account=self.revenue,
        )
        # Seed stock
        post_stock_movement(
            product=self.product,
            movement_type=StockMovement.MovementType.RECEIPT,
            qty=D("10.0000"),
            unit_cost=D("10.00"),
        )

    def test_confirm_and_invoice_sales_order(self):
        order = SalesOrder.objects.create(
            customer=self.customer,
            date=date(2026, 5, 1),
        )
        SalesOrderLine.objects.create(
            order=order,
            product=self.product,
            description="Widget",
            qty=D("2"),
            unit_price=D("20.00"),
            revenue_account=self.revenue,
        )

        confirm_sales_order(order)
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.Status.INVOICED) # Auto-invoiced on confirm!
        self.assertEqual(order.number, "SO-2026-000001")

        invoice = order.invoices.first()
        self.assertEqual(invoice.sales_order, order)
        self.assertEqual(invoice.status, Invoice.Status.DRAFT)
        self.assertEqual(invoice.due_date, date(2026, 5, 16))
        self.assertEqual(invoice.lines.count(), 1)
        self.assertEqual(invoice.subtotal(), D("40.00"))

        # Verify stock was shipped
        self.assertEqual(StockOnHand.objects.get(product=self.product).qty, D("8.0000"))

    def test_undo_confirm_and_draft_invoice(self):
        order = SalesOrder.objects.create(
            customer=self.customer,
            date=date(2026, 5, 1),
        )
        SalesOrderLine.objects.create(
            order=order,
            product=self.product,
            description="Widget",
            qty=D("1"),
            unit_price=D("20.00"),
            revenue_account=self.revenue,
        )

        confirm_sales_order(order)
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.Status.INVOICED)

        # Deleting the draft invoice returns stock and moves PO to CONFIRMED
        undo_invoice_from_sales_order(order)
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.Status.CONFIRMED)
        self.assertFalse(order.invoices.exists())
        self.assertEqual(StockOnHand.objects.get(product=self.product).qty, D("10.0000"))

        # Now we can undo confirmation
        undo_confirm_sales_order(order)
        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.Status.DRAFT)

    def test_confirm_sales_order_insufficient_stock_rollback(self):
        from django.core.exceptions import ValidationError
        order = SalesOrder.objects.create(
            customer=self.customer,
            date=date(2026, 5, 1),
        )
        SalesOrderLine.objects.create(
            order=order,
            product=self.product,
            description="Widget",
            qty=D("15"),  # Exceeds available stock (10)
            unit_price=D("20.00"),
            revenue_account=self.revenue,
        )

        with self.assertRaises(ValidationError):
            confirm_sales_order(order)

        order.refresh_from_db()
        self.assertEqual(order.status, SalesOrder.Status.DRAFT)
        self.assertEqual(StockOnHand.objects.get(product=self.product).qty, D("10.0000"))
        self.assertFalse(order.invoices.exists())

    def test_post_invoice_does_not_duplicate_stock_issue(self):
        order = SalesOrder.objects.create(
            customer=self.customer,
            date=date(2026, 5, 1),
        )
        SalesOrderLine.objects.create(
            order=order,
            product=self.product,
            description="Widget",
            qty=D("2"),
            unit_price=D("20.00"),
            revenue_account=self.revenue,
        )

        confirm_sales_order(order)
        invoice = order.invoices.first()

        # Check stock was issued once
        self.assertEqual(StockOnHand.objects.get(product=self.product).qty, D("8.0000"))
        self.assertEqual(StockMovement.objects.filter(ref_doc_type="Invoice", ref_doc_id=invoice.pk, movement_type="issue").count(), 1)

        # Post the invoice
        post_invoice(invoice)
        invoice.refresh_from_db()

        self.assertEqual(invoice.status, Invoice.Status.SENT)
        # Check stock was NOT issued again
        self.assertEqual(StockOnHand.objects.get(product=self.product).qty, D("8.0000"))
        self.assertEqual(StockMovement.objects.filter(ref_doc_type="Invoice", ref_doc_id=invoice.pk, movement_type="issue").count(), 1)

        # Check that COGS journal entries were posted correctly
        self.assertIsNotNone(invoice.journal_entry)
        from accounting.models import JournalLine
        cogs_lines = JournalLine.objects.filter(account=self.cogs)
        self.assertEqual(cogs_lines.count(), 1)
        self.assertEqual(cogs_lines.first().debit, D("20.00"))  # 2 * cost (10.00) = 20.00


class InvoiceVoidTests(TestCase):
    def setUp(self):
        self.ar = Account.objects.create(code="1200", name="Accounts Receivable", type=AccountType.ASSET)
        self.revenue = Account.objects.create(code="4100", name="Product Sales", type=AccountType.REVENUE)
        self.tax = Account.objects.create(code="2120", name="Sales Tax Payable", type=AccountType.LIABILITY)
        self.inventory = Account.objects.create(code="1300", name="Inventory", type=AccountType.ASSET)
        self.cogs = Account.objects.create(code="5100", name="COGS - Materials", type=AccountType.EXPENSE)
        self.customer = Customer.objects.create(name="Acme", tax_rate=Decimal("10.000"))
        self.product = Product.objects.create(sku="WIDGET", name="Widget", price=D("100.00"), default_revenue_account=self.revenue)
        # Seed stock
        post_stock_movement(
            product=self.product,
            movement_type=StockMovement.MovementType.RECEIPT,
            qty=D("10.0000"),
            unit_cost=D("50.00"),
        )

    def test_post_and_void_invoice(self):
        order = SalesOrder.objects.create(customer=self.customer, date=date(2026, 5, 1))
        SalesOrderLine.objects.create(
            order=order, product=self.product, description="Widget", qty=D("1"), unit_price=D("100.00"), revenue_account=self.revenue
        )
        confirm_sales_order(order)
        invoice = order.invoices.first()
        
        post_invoice(invoice)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.SENT)
        self.assertIsNotNone(invoice.journal_entry)
        self.assertEqual(invoice.total(), D("110.00"))
        
        void_invoice(invoice)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.VOID)
        
        from accounting.models import JournalLine
        from django.db.models import Sum
        ar_d = JournalLine.objects.filter(account=self.ar).aggregate(s=Sum("debit"))["s"] or D("0")
        ar_c = JournalLine.objects.filter(account=self.ar).aggregate(s=Sum("credit"))["s"] or D("0")
        self.assertEqual(ar_d, ar_c)
        self.assertEqual(ar_d, D("110.00"))


class SalesPDFViewsTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.user = User.objects.create_user(username="testuser", password="password")
        self.revenue = Account.objects.create(code="4100", name="Product Sales", type=AccountType.REVENUE)
        self.customer = Customer.objects.create(name="Acme", payment_terms_days=15)
        self.product = Product.objects.create(
            sku="WIDGET",
            name="Widget",
            price=D("20.00"),
            default_revenue_account=self.revenue,
        )

    def test_sales_order_pdf_download(self):
        order = SalesOrder.objects.create(
            customer=self.customer,
            date=date(2026, 5, 1),
        )
        SalesOrderLine.objects.create(
            order=order,
            product=self.product,
            description="Widget",
            qty=D("2"),
            unit_price=D("20.00"),
            revenue_account=self.revenue,
        )
        self.client.force_login(self.user)
        response = self.client.get(f"/sales-orders/{order.pk}/pdf/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn(f"SO-{order.pk}.pdf", response["Content-Disposition"])

    def test_invoice_pdf_download(self):
        invoice = Invoice.objects.create(
            customer=self.customer,
            date=date(2026, 5, 1),
            due_date=date(2026, 5, 16),
            tax_rate=D("8.25"),
        )
        from sales.models import InvoiceLine
        InvoiceLine.objects.create(
            invoice=invoice,
            product=self.product,
            description="Widget",
            qty=D("2"),
            unit_price=D("20.00"),
            revenue_account=self.revenue,
        )
        self.client.force_login(self.user)
        response = self.client.get(f"/invoices/{invoice.pk}/pdf/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/pdf", response["Content-Type"])
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn(f"INV-{invoice.pk}.pdf", response["Content-Disposition"])


class CustomerDetailViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.user = User.objects.create_user(username="testuser", password="password")
        self.revenue = Account.objects.create(code="4100", name="Product Sales", type=AccountType.REVENUE)
        self.customer = Customer.objects.create(name="Acme", payment_terms_days=15)
        self.product = Product.objects.create(
            sku="WIDGET",
            name="Widget",
            price=D("20.00"),
            default_revenue_account=self.revenue,
        )

    def test_customer_detail_view(self):
        order = SalesOrder.objects.create(
            customer=self.customer,
            date=date(2026, 5, 1),
        )
        SalesOrderLine.objects.create(
            order=order,
            product=self.product,
            description="Widget",
            qty=D("2"),
            unit_price=D("20.00"),
            revenue_account=self.revenue,
        )
        
        invoice = Invoice.objects.create(
            customer=self.customer,
            date=date(2026, 5, 1),
            due_date=date(2026, 5, 16),
            tax_rate=D("0.00"),
            status=Invoice.Status.SENT,
        )
        from sales.models import InvoiceLine
        InvoiceLine.objects.create(
            invoice=invoice,
            product=self.product,
            description="Widget",
            qty=D("2"),
            unit_price=D("20.00"),
            revenue_account=self.revenue,
        )

        self.client.force_login(self.user)
        response = self.client.get(f"/customers/{self.customer.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Acme")
        self.assertContains(response, "Lifetime Posted Revenue")
        self.assertContains(response, "$40.00")


class LocalizedInvoiceTests(TestCase):
    def setUp(self):
        self.ar = Account.objects.create(code="1200", name="Accounts Receivable", type=AccountType.ASSET)
        self.revenue = Account.objects.create(code="4100", name="Product Sales", type=AccountType.REVENUE)
        self.tax = Account.objects.create(code="2120", name="Sales Tax Payable", type=AccountType.LIABILITY)
        self.inventory = Account.objects.create(code="1300", name="Inventory", type=AccountType.ASSET)
        self.cogs = Account.objects.create(code="5100", name="COGS - Materials", type=AccountType.EXPENSE)
        self.customer = Customer.objects.create(name="Acme", tax_rate=Decimal("10.000"))
        self.product = Product.objects.create(sku="WIDGET", name="Widget", price=D("100.00"), cost=D("50.00"), default_revenue_account=self.revenue)

    def test_invoice_fulfillment_from_custom_location(self):
        from inventory.models import Location, LocationStock
        custom_loc = Location.objects.create(name="Aisle 12", is_active=True)

        # Seed stock at custom location
        post_stock_movement(
            product=self.product,
            movement_type=StockMovement.MovementType.RECEIPT,
            qty=D("10.0000"),
            unit_cost=D("50.00"),
            location=custom_loc,
        )

        invoice = Invoice.objects.create(
            customer=self.customer,
            date=date(2026, 5, 1),
            due_date=date(2026, 5, 16),
        )
        from sales.models import InvoiceLine
        line = InvoiceLine.objects.create(
            invoice=invoice,
            product=self.product,
            description="Widget",
            qty=D("3.0000"),
            unit_price=D("100.00"),
            revenue_account=self.revenue,
            location=custom_loc,
        )

        post_invoice(invoice)
        invoice.refresh_from_db()

        self.assertEqual(invoice.status, Invoice.Status.SENT)

        # Check stock decreased specifically at Aisle 12
        loc_stock = LocationStock.objects.get(product=self.product, location=custom_loc)
        self.assertEqual(loc_stock.qty, D("7.0000"))

        # Verify that default location has no stock
        wh_stock_exists = LocationStock.objects.filter(product=self.product, location__name="Main Warehouse").exists()
        self.assertFalse(wh_stock_exists)


