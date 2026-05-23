from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from django.core.exceptions import ValidationError

from core.models import User, Role, Company
from inventory.models import Product, ProductType, StockOnHand, StockMovement
from accounting.models import Account, AccountType, JournalEntry, JournalLine
from .models import BillOfMaterials, BOMComponent, ManufacturingOrder
from .services import confirm_manufacturing_order, complete_manufacturing_order, cancel_manufacturing_order


class ManufacturingModuleTests(TestCase):
    def setUp(self):
        # Create user & company
        self.user = User.objects.create_user(
            username="operator",
            email="operator@example.com",
            password="password",
            role=Role.ADMIN
        )
        self.company = Company.get()

        # Create necessary ledger accounts
        self.inv_acct = Account.objects.create(
            code="1300", name="Inventory", type=AccountType.ASSET, is_postable=True
        )
        self.cash_acct = Account.objects.create(
            code="1110", name="Cash - Operating", type=AccountType.ASSET, is_postable=True
        )

        # Create raw materials (STOCK)
        self.rm1 = Product.objects.create(
            sku="RM-1",
            name="Raw component 1",
            type=ProductType.STOCK,
            cost=Decimal("10.00")
        )
        self.rm2 = Product.objects.create(
            sku="RM-2",
            name="Raw component 2",
            type=ProductType.STOCK,
            cost=Decimal("5.00")
        )

        # Seed raw stock
        StockOnHand.objects.create(product=self.rm1, qty=Decimal("100.0000"))
        StockOnHand.objects.create(product=self.rm2, qty=Decimal("50.0000"))

        # Create finished goods assembly (STOCK)
        self.fp = Product.objects.create(
            sku="FP-1",
            name="Finished Assembly",
            type=ProductType.STOCK,
            cost=Decimal("0.00")
        )

        # Create Bill of Materials
        self.bom = BillOfMaterials.objects.create(
            product=self.fp,
            name="Primary Assembly Recipe",
            created_by=self.user
        )
        self.comp1 = BOMComponent.objects.create(
            bom=self.bom,
            product=self.rm1,
            qty=Decimal("2.0000") # requires 2 units
        )
        self.comp2 = BOMComponent.objects.create(
            bom=self.bom,
            product=self.rm2,
            qty=Decimal("3.0000") # requires 3 units
        )

    def test_bom_cost_rollup_calculation(self):
        # (2 * $10.00) + (3 * $5.00) = $35.00
        self.assertEqual(self.bom.total_cost_rollup, Decimal("35.00"))
        self.assertEqual(self.comp1.extended_cost, Decimal("20.00"))
        self.assertEqual(self.comp2.extended_cost, Decimal("15.00"))

    def test_bom_validation_prevents_finished_goods_as_component(self):
        # A recipe cannot contain the finished product itself as a component
        comp = BOMComponent(bom=self.bom, product=self.fp, qty=Decimal("1.0000"))
        with self.assertRaises(ValidationError):
            comp.clean()

    def test_mo_creation_and_confirmation(self):
        mo = ManufacturingOrder.objects.create(
            product=self.fp,
            bom=self.bom,
            qty_target=Decimal("10.0000"),
            date_planned=timezone.localdate(),
            created_by=self.user
        )
        self.assertEqual(mo.status, ManufacturingOrder.Status.DRAFT)
        self.assertIsNone(mo.number)

        confirm_manufacturing_order(mo, self.user)
        self.assertEqual(mo.status, ManufacturingOrder.Status.CONFIRMED)
        self.assertIsNotNone(mo.number)
        self.assertTrue(mo.number.startswith("MO-"))

    def test_mo_completion_successfully_adjusts_stock_and_posts_gl(self):
        mo = ManufacturingOrder.objects.create(
            product=self.fp,
            bom=self.bom,
            qty_target=Decimal("10.0000"), # produces 10 finished goods
            date_planned=timezone.localdate(),
            created_by=self.user
        )
        confirm_manufacturing_order(mo, self.user)

        # Complete MO
        complete_manufacturing_order(mo, self.user)

        # 1. Check status
        self.assertEqual(mo.status, ManufacturingOrder.Status.COMPLETED)
        self.assertEqual(mo.qty_produced, Decimal("10.0000"))
        self.assertIsNotNone(mo.date_completed)

        # 2. Check component stock deductions
        # RM-1 required: 2 * 10 = 20. Remaining: 100 - 20 = 80
        self.rm1.stock_on_hand.refresh_from_db()
        self.assertEqual(self.rm1.stock_on_hand.qty, Decimal("80.0000"))
        
        # RM-2 required: 3 * 10 = 30. Remaining: 50 - 30 = 20
        self.rm2.stock_on_hand.refresh_from_db()
        self.assertEqual(self.rm2.stock_on_hand.qty, Decimal("20.0000"))

        # 3. Check finished stock receipt
        self.fp.refresh_from_db()
        self.assertEqual(self.fp.stock_on_hand.qty, Decimal("10.0000"))

        # 4. Check finished product cost update
        self.fp.refresh_from_db()
        self.assertEqual(self.fp.cost, Decimal("35.00")) # Cost basis updated correctly to rollup cost

        # 5. Check balanced GL Posting
        self.assertIsNotNone(mo.journal_entry)
        je = mo.journal_entry
        self.assertEqual(je.status, JournalEntry.Status.POSTED)
        
        # Debits must equal credits
        self.assertEqual(je.total_debit(), Decimal("350.00")) # 10 units * $35.00 rollup cost
        self.assertEqual(je.total_credit(), Decimal("350.00"))

        # Double check line distribution
        # Line 1: DR Inventory FP-1 $350.00
        # Line 2: CR Inventory RM-1 $200.00
        # Line 3: CR Inventory RM-2 $150.00
        lines = je.lines.all().order_by("-debit")
        self.assertEqual(lines.count(), 3)
        self.assertEqual(lines[0].account, self.inv_acct)
        self.assertEqual(lines[0].debit, Decimal("350.00"))
        
        credit_sum = sum(l.credit for l in lines[1:])
        self.assertEqual(credit_sum, Decimal("350.00"))

    def test_mo_completion_shortage_validation_rolls_back_everything(self):
        mo = ManufacturingOrder.objects.create(
            product=self.fp,
            bom=self.bom,
            qty_target=Decimal("30.0000"), # requires 30*3 = 90 RM-2 units, but we only have 50
            date_planned=timezone.localdate(),
            created_by=self.user
        )
        confirm_manufacturing_order(mo, self.user)

        # Complete MO should raise ValidationError due to shortage
        with self.assertRaises(ValidationError) as ctx:
            complete_manufacturing_order(mo, self.user)
        
        self.assertIn("Insufficient raw materials in stock", str(ctx.exception))
        self.assertIn("RM-2", str(ctx.exception))

        # Check that stock was not modified (rollback intact)
        self.rm1.refresh_from_db()
        self.rm2.refresh_from_db()
        self.assertEqual(self.rm1.stock_on_hand.qty, Decimal("100.0000"))
        self.assertEqual(self.rm2.stock_on_hand.qty, Decimal("50.0000"))
        self.assertFalse(hasattr(self.fp, "stock_on_hand") or StockOnHand.objects.filter(product=self.fp).exists())

    def test_mo_cancelation_draft_or_confirmed(self):
        mo = ManufacturingOrder.objects.create(
            product=self.fp,
            bom=self.bom,
            qty_target=Decimal("10.0000"),
            date_planned=timezone.localdate(),
            created_by=self.user
        )
        
        # Cancel draft MO
        cancel_manufacturing_order(mo, self.user)
        self.assertEqual(mo.status, ManufacturingOrder.Status.CANCELLED)

        # Confirm order can't be completed if cancelled
        with self.assertRaises(ValidationError):
            confirm_manufacturing_order(mo, self.user)
