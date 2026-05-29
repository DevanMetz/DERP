from decimal import Decimal
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import (
    TriggerType, FieldType, InspectionTemplate, InspectionFieldTemplate,
    QualityInspection, InspectionValue, NonConformance, CAPA
)
from .services import (
    create_pending_inspections_for_receipt, create_pending_inspections_for_mo,
    complete_inspection, quarantine_lot, resolve_ncr
)
from inventory.models import Product, ProductType, Lot, Location, StockMovement
from purchasing.models import GoodsReceipt, GoodsReceiptLine, PurchaseOrder, PurchaseOrderLine, Vendor
from manufacturing.models import ManufacturingOrder, BillOfMaterials
from accounting.models import Account

User = get_user_model()


class QMSTests(TestCase):
    def setUp(self):
        # 1. Create a User
        self.user = User.objects.create_user(
            username="quality_mgr",
            email="mgr@example.com",
            password="test-secure-pass",
            role="manager"
        )
        
        # 2. Create Accounts needed for products/lines
        self.rev_acc = Account.objects.create(code="4010", name="Revenue Account", type="revenue", is_postable=True)
        self.exp_acc = Account.objects.create(code="5010", name="Expense Account", type="expense", is_postable=True)
        self.asset_acc = Account.objects.create(code="1300", name="Inventory Asset", type="asset", is_postable=True)
        
        # 3. Create a Product
        self.product = Product.objects.create(
            sku="WIDGET-001",
            name="Super Quality Widget",
            type=ProductType.STOCK,
            cost=Decimal("10.00"),
            price=Decimal("20.00"),
            default_revenue_account=self.rev_acc,
            default_expense_account=self.exp_acc,
            is_active=True
        )
        
        # 4. Create an Inspection Template for Receiving WIDGET-001
        self.rcv_template = InspectionTemplate.objects.filter(name="Widget Receiving Specs").first()
        if not self.rcv_template:
            self.rcv_template = InspectionTemplate.objects.create(
                name="Widget Receiving Specs",
                product=self.product,
                trigger_type=TriggerType.RECEIVING,
                is_active=True,
                created_by=self.user
            )
            # Create a Pass/Fail field
            self.f1 = InspectionFieldTemplate.objects.create(
                template=self.rcv_template,
                name="Visual Crack Inspection",
                field_type=FieldType.BOOLEAN,
                is_required=True,
                sort_order=10
            )
            # Create a Numeric range field
            self.f2 = InspectionFieldTemplate.objects.create(
                template=self.rcv_template,
                name="Length (cm)",
                field_type=FieldType.NUMERIC,
                min_value=Decimal("5.0000"),
                max_value=Decimal("5.5000"),
                is_required=True,
                sort_order=20
            )

    def test_inspection_template_and_fields_creation(self):
        """Verifies template and field templates register correct relations."""
        self.assertEqual(self.rcv_template.fields.count(), 2)
        self.assertEqual(self.f2.min_value, Decimal("5.0000"))
        self.assertEqual(self.f2.max_value, Decimal("5.5000"))

    def test_complete_inspection_passed(self):
        """Tests that a checklist with valid values completes as a PASS."""
        # Create a lot
        lot = Lot.objects.create(product=self.product, lot_number="LOT-ABC")
        
        # Create a manual quality inspection
        qc = QualityInspection.objects.create(
            template=self.rcv_template,
            status=QualityInspection.Status.DRAFT,
            lot=lot
        )
        
        # Initialize inspection values
        v1 = InspectionValue.objects.create(inspection=qc, field_template=self.f1)
        v2 = InspectionValue.objects.create(inspection=qc, field_template=self.f2)
        
        # Simulating passing inputs: f1=True (checkbox checked), f2=5.25 (inside min/max bounds)
        values_data = {
            str(self.f1.pk): "on", # checkbox
            str(self.f2.pk): "5.25"
        }
        
        completed_qc = complete_inspection(qc, notes="Everything is correct", values_data=values_data, user=self.user)
        
        self.assertEqual(completed_qc.status, QualityInspection.Status.PASS)
        self.assertFalse(completed_qc.lot.is_quarantined)
        self.assertIsNotNone(completed_qc.number)
        self.assertEqual(completed_qc.non_conformances.count(), 0)

    def test_complete_inspection_failed_and_auto_ncr(self):
        """Tests that out-of-tolerance bounds checklist completes as a FAIL, quarantining the lot and auto-raising an NCR."""
        lot = Lot.objects.create(product=self.product, lot_number="LOT-XYZ")
        
        qc = QualityInspection.objects.create(
            template=self.rcv_template,
            status=QualityInspection.Status.DRAFT,
            lot=lot
        )
        
        v1 = InspectionValue.objects.create(inspection=qc, field_template=self.f1)
        v2 = InspectionValue.objects.create(inspection=qc, field_template=self.f2)
        
        # Simulating failing inputs: f1=False, f2=4.8 (below min 5.0)
        values_data = {
            str(self.f1.pk): "",
            str(self.f2.pk): "4.8"
        }
        
        completed_qc = complete_inspection(qc, notes="Fails length specs", values_data=values_data, user=self.user)
        
        self.assertEqual(completed_qc.status, QualityInspection.Status.FAIL)
        # Verify lot is quarantined
        self.assertTrue(completed_qc.lot.is_quarantined)
        
        # Verify NonConformance was automatically raised
        ncrs = completed_qc.non_conformances.all()
        self.assertEqual(ncrs.count(), 1)
        ncr = ncrs.first()
        self.assertEqual(ncr.status, NonConformance.Status.OPEN)
        self.assertEqual(ncr.severity, NonConformance.Severity.MAJOR)
        self.assertEqual(ncr.lot, lot)

    def test_manual_quarantine_and_resolve_ncr(self):
        """Tests manually quarantining a lot and resolving the NCR to release the lot."""
        lot = Lot.objects.create(product=self.product, lot_number="LOT-DEF")
        self.assertFalse(lot.is_quarantined)
        
        # Manual quarantine
        ncr = quarantine_lot(lot, reason="Scratched glass housing", user=self.user)
        
        # Check lot status
        lot.refresh_from_db()
        self.assertTrue(lot.is_quarantined)
        self.assertEqual(ncr.lot, lot)
        self.assertEqual(ncr.status, NonConformance.Status.OPEN)
        
        # Resolve the NCR as USE_AS_IS
        resolved_ncr = resolve_ncr(
            ncr=ncr,
            disposition=NonConformance.Disposition.USE_AS_IS,
            notes="Engineer approved deviation for light minor scratch.",
            user=self.user
        )
        
        self.assertEqual(resolved_ncr.status, NonConformance.Status.CLOSED)
        # Lot should be released (is_quarantined = False)
        lot.refresh_from_db()
        self.assertFalse(lot.is_quarantined)
