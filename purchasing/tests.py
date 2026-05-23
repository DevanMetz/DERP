from datetime import date
from decimal import Decimal

from django.test import TestCase

from accounting.models import Account, AccountType
from inventory.models import Product
from purchasing.models import Bill, PurchaseOrder, PurchaseOrderLine, Vendor
from purchasing.services import (
    create_bill_from_purchase_order, issue_purchase_order, receive_purchase_order,
    reverse_goods_receipt, undo_bill_from_purchase_order,
    undo_issue_purchase_order, create_bill_from_receipt,
)
from inventory.models import StockOnHand, StockMovement


D = Decimal


class PurchaseOrderWorkflowTests(TestCase):
    def setUp(self):
        self.ap = Account.objects.create(code="2110", name="Accounts Payable", type=AccountType.LIABILITY)
        self.expense = Account.objects.create(code="6900", name="Miscellaneous", type=AccountType.EXPENSE)
        self.vendor = Vendor.objects.create(name="Supply Co", payment_terms_days=20)
        self.product = Product.objects.create(
            sku="PART",
            name="Part",
            cost=D("5.00"),
            default_expense_account=self.expense,
        )

    def test_issue_and_bill_purchase_order(self):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor,
            date=date(2026, 5, 1),
        )
        PurchaseOrderLine.objects.create(
            order=order,
            product=self.product,
            description="Part",
            qty=D("3"),
            unit_cost=D("5.00"),
            expense_account=self.expense,
        )

        issue_purchase_order(order)
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.ISSUED)
        self.assertEqual(order.number, "PO-2026-000001")

        bill = create_bill_from_purchase_order(order)
        order.refresh_from_db()

        self.assertEqual(order.status, PurchaseOrder.Status.BILLED)
        self.assertEqual(bill.purchase_order, order)
        self.assertEqual(bill.status, Bill.Status.DRAFT)
        self.assertEqual(bill.due_date, date(2026, 5, 21))
        self.assertEqual(bill.lines.count(), 1)
        self.assertEqual(bill.total(), D("15.00"))

    def test_undo_issue_and_draft_bill(self):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor,
            date=date(2026, 5, 1),
        )
        PurchaseOrderLine.objects.create(
            order=order,
            product=self.product,
            description="Part",
            qty=D("3"),
            unit_cost=D("5.00"),
            expense_account=self.expense,
        )

        issue_purchase_order(order)
        undo_issue_purchase_order(order)
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.DRAFT)

        issue_purchase_order(order)
        create_bill_from_purchase_order(order)
        undo_bill_from_purchase_order(order)
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.ISSUED)
        self.assertFalse(order.bills.exists())

    def test_receive_purchase_order_updates_stock(self):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor,
            date=date(2026, 5, 1),
        )
        line = PurchaseOrderLine.objects.create(
            order=order,
            product=self.product,
            description="Part",
            qty=D("3.0000"),
            unit_cost=D("5.00"),
            expense_account=self.expense,
        )
        issue_purchase_order(order)

        receipt = receive_purchase_order(
            order=order,
            date=date(2026, 5, 2),
            receipts=[(line, D("2.0000"))],
        )
        order.refresh_from_db()

        self.assertEqual(receipt.number, "GR-2026-000001")
        self.assertEqual(order.status, PurchaseOrder.Status.PARTIALLY_RECEIVED)
        self.assertEqual(StockOnHand.objects.get(product=self.product).qty, D("2.0000"))
        self.assertEqual(StockMovement.objects.count(), 1)

        receive_purchase_order(
            order=order,
            date=date(2026, 5, 3),
            receipts=[(line, D("1.0000"))],
        )
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.RECEIVED)
        self.assertEqual(StockOnHand.objects.get(product=self.product).qty, D("3.0000"))

    def test_can_receive_after_bill_and_reverse_receipt(self):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor,
            date=date(2026, 5, 1),
        )
        line = PurchaseOrderLine.objects.create(
            order=order,
            product=self.product,
            description="Part",
            qty=D("3.0000"),
            unit_cost=D("5.00"),
            expense_account=self.expense,
        )
        issue_purchase_order(order)
        create_bill_from_purchase_order(order)
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.BILLED)

        receipt = receive_purchase_order(
            order=order,
            date=date(2026, 5, 2),
            receipts=[(line, D("3.0000"))],
        )
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.BILLED)
        self.assertEqual(StockOnHand.objects.get(product=self.product).qty, D("3.0000"))

        reverse_goods_receipt(receipt)
        receipt.refresh_from_db()
        self.assertTrue(receipt.is_reversed)
        self.assertEqual(StockOnHand.objects.get(product=self.product).qty, D("0.0000"))


class BillVoidTests(TestCase):
    def setUp(self):
        self.ap = Account.objects.create(code="2110", name="Accounts Payable", type=AccountType.LIABILITY)
        self.expense = Account.objects.create(code="6900", name="Miscellaneous", type=AccountType.EXPENSE)
        self.vendor = Vendor.objects.create(name="Supply Co", payment_terms_days=20)
        self.product = Product.objects.create(
            sku="PART",
            name="Part",
            cost=D("5.00"),
            default_expense_account=self.expense,
        )

    def test_post_and_void_bill(self):
        from purchasing.services import post_bill, void_bill
        order = PurchaseOrder.objects.create(
            vendor=self.vendor,
            date=date(2026, 5, 1),
        )
        PurchaseOrderLine.objects.create(
            order=order,
            product=self.product,
            description="Part",
            qty=D("3"),
            unit_cost=D("5.00"),
            expense_account=self.expense,
        )
        issue_purchase_order(order)
        bill = create_bill_from_purchase_order(order)
        
        post_bill(bill)
        bill.refresh_from_db()
        self.assertEqual(bill.status, Bill.Status.ENTERED)
        self.assertIsNotNone(bill.journal_entry)
        
        void_bill(bill)
        bill.refresh_from_db()
        self.assertEqual(bill.status, Bill.Status.VOID)
        
        # Verify that reversing journal entry was posted
        from accounting.models import JournalLine
        from django.db.models import Sum
        ap_d = JournalLine.objects.filter(account=self.ap).aggregate(s=Sum("debit"))["s"] or D("0")
        ap_c = JournalLine.objects.filter(account=self.ap).aggregate(s=Sum("credit"))["s"] or D("0")
        self.assertEqual(ap_d, ap_c)
        self.assertEqual(ap_d, D("15.00"))


class GoodsReceiptToBillTests(TestCase):
    def setUp(self):
        self.ap = Account.objects.create(code="2110", name="Accounts Payable", type=AccountType.LIABILITY)
        self.expense = Account.objects.create(code="6900", name="Miscellaneous", type=AccountType.EXPENSE)
        self.vendor = Vendor.objects.create(name="Supply Co", payment_terms_days=20)
        self.product = Product.objects.create(
            sku="PART",
            name="Part",
            cost=D("5.00"),
            default_expense_account=self.expense,
        )
        self.order = PurchaseOrder.objects.create(
            vendor=self.vendor,
            date=date(2026, 5, 1),
        )
        self.line = PurchaseOrderLine.objects.create(
            order=self.order,
            product=self.product,
            description="Part",
            qty=D("3.0000"),
            unit_cost=D("5.00"),
            expense_account=self.expense,
        )
        issue_purchase_order(self.order)
        self.receipt = receive_purchase_order(
            order=self.order,
            date=date(2026, 5, 2),
            receipts=[(self.line, D("2.0000"))],
        )

    def test_create_bill_from_goods_receipt(self):
        bill = create_bill_from_receipt(self.receipt)
        self.order.refresh_from_db()

        self.assertEqual(self.order.status, PurchaseOrder.Status.BILLED)
        self.assertEqual(bill.goods_receipt, self.receipt)
        self.assertEqual(bill.purchase_order, self.order)
        self.assertEqual(bill.status, Bill.Status.DRAFT)
        self.assertEqual(bill.date, date(2026, 5, 2))
        self.assertEqual(bill.due_date, date(2026, 5, 22))
        self.assertEqual(bill.lines.count(), 1)
        
        bill_line = bill.lines.first()
        self.assertEqual(bill_line.qty, D("2.0000"))
        self.assertEqual(bill_line.unit_cost, D("5.00"))
        self.assertEqual(bill_line.expense_account, self.expense)

    def test_duplicate_bill_prevention(self):
        from django.core.exceptions import ValidationError
        create_bill_from_receipt(self.receipt)
        with self.assertRaises(ValidationError):
            create_bill_from_receipt(self.receipt)


class PurchasingPDFViewsTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.user = User.objects.create_user(username="testuser", password="password")
        self.expense = Account.objects.create(code="6900", name="Miscellaneous", type=AccountType.EXPENSE)
        self.vendor = Vendor.objects.create(name="Supply Co", payment_terms_days=20)
        self.product = Product.objects.create(
            sku="PART",
            name="Part",
            cost=D("5.00"),
            default_expense_account=self.expense,
        )

    def test_purchase_order_pdf_download(self):
        order = PurchaseOrder.objects.create(
            vendor=self.vendor,
            date=date(2026, 5, 1),
        )
        PurchaseOrderLine.objects.create(
            order=order,
            product=self.product,
            description="Part",
            qty=D("3.0000"),
            unit_cost=D("5.00"),
            expense_account=self.expense,
        )
        self.client.force_login(self.user)
        response = self.client.get(f"/purchase-orders/{order.pk}/pdf/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn(f"PO-{order.pk}.pdf", response["Content-Disposition"])


