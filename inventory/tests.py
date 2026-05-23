from decimal import Decimal
import os
import shutil
import tempfile

from django.core.exceptions import ValidationError
from django.test import override_settings
from core.test_utils import DERPTenantTestCase as TestCase

from inventory.forms import ProductForm
from inventory.models import Product, ProductType, StockMovement, StockOnHand
from inventory.services import post_stock_movement


D = Decimal


class ProductFormTests(TestCase):
    def test_product_form_exposes_order_availability_toggles(self):
        form = ProductForm()

        self.assertIn("is_purchasable", form.fields)
        self.assertIn("is_sellable", form.fields)
        self.assertIn("is_manufacturable", form.fields)


class StockMovementTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(sku="PART", name="Part")
        self.service = Product.objects.create(
            sku="SERV", name="Service", type=ProductType.SERVICE,
        )

    def test_receipt_increases_on_hand(self):
        movement = post_stock_movement(
            product=self.product,
            movement_type=StockMovement.MovementType.RECEIPT,
            qty=D("4.0000"),
            unit_cost=D("2.50"),
            ref_doc_type="Test",
            ref_doc_id=1,
        )

        self.assertEqual(movement.qty, D("4.0000"))
        self.assertEqual(StockOnHand.objects.get(product=self.product).qty, D("4.0000"))

    def test_issue_cannot_overdraw_stock(self):
        with self.assertRaises(ValidationError):
            post_stock_movement(
                product=self.product,
                movement_type=StockMovement.MovementType.ISSUE,
                qty=D("1.0000"),
            )

    def test_service_items_do_not_have_stock(self):
        with self.assertRaises(ValidationError):
            post_stock_movement(
                product=self.service,
                movement_type=StockMovement.MovementType.RECEIPT,
                qty=D("1.0000"),
            )

    def test_weighted_average_cost_recalculation(self):
        # 1. First receipt: 10 units @ $5.00
        post_stock_movement(
            product=self.product,
            movement_type=StockMovement.MovementType.RECEIPT,
            qty=D("10.0000"),
            unit_cost=D("5.00"),
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.cost, D("5.00"))

        # 2. Second receipt: 5 units @ $8.00
        post_stock_movement(
            product=self.product,
            movement_type=StockMovement.MovementType.RECEIPT,
            qty=D("5.0000"),
            unit_cost=D("8.00"),
        )
        self.product.refresh_from_db()
        # New cost should be ((10 * 5.00) + (5 * 8.00)) / 15 = 90 / 15 = $6.00
        self.assertEqual(self.product.cost, D("6.00"))

        # 3. Stock issue: 3 units @ current average cost ($6.00) should NOT change average cost
        post_stock_movement(
            product=self.product,
            movement_type=StockMovement.MovementType.ISSUE,
            qty=D("3.0000"),
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.cost, D("6.00"))

    def test_stock_movement_list_view(self):
        from django.urls import reverse
        from core.models import User, Role
        user = User.objects.create_user(
            username="inventory_user",
            email="inv@example.com",
            password="password",
            role=Role.ADMIN,
        )
        self.client.login(username="inventory_user", password="password")
        
        post_stock_movement(
            product=self.product,
            movement_type=StockMovement.MovementType.RECEIPT,
            qty=D("5.0000"),
            unit_cost=D("10.00"),
        )
        
        response = self.client.get(reverse("stock_movement_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Inventory Ledger")
        self.assertContains(response, "PART")


TEMP_MEDIA_ROOT = tempfile.mkdtemp()

@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class ProductViewsTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        from core.models import User, Role
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.user = User.objects.create_user(
            username="inv_user",
            email="inv@example.com",
            password="password",
            role=Role.ADMIN,
        )
        self.readonly_user = User.objects.create_user(
            username="inv_readonly",
            email="inv-readonly@example.com",
            password="password",
            role=Role.READONLY,
        )
        self.product = Product.objects.create(
            sku="TPRODUCT",
            name="Test Product",
            price=D("100.00"),
            cost=D("60.00"),
        )
        post_stock_movement(
            product=self.product,
            movement_type=StockMovement.MovementType.RECEIPT,
            qty=D("10.0000"),
            unit_cost=D("60.00"),
            memo="Initial seed",
        )

    def test_readonly_user_cannot_open_product_write_forms(self):
        from django.urls import reverse
        self.client.force_login(self.readonly_user)

        create_response = self.client.get(reverse("product_create"))
        edit_response = self.client.get(reverse("product_edit", args=[self.product.pk]))

        self.assertEqual(create_response.status_code, 403)
        self.assertEqual(edit_response.status_code, 403)

    def test_product_detail_view(self):
        from django.urls import reverse
        self.client.force_login(self.user)
        response = self.client.get(reverse("product_detail", args=[self.product.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Product: TPRODUCT")
        self.assertContains(response, "Test Product")
        self.assertContains(response, "40.0%")  # margin
        self.assertContains(response, "Initial seed")

    def test_product_detail_movements_log(self):
        from django.urls import reverse
        post_stock_movement(
            product=self.product,
            movement_type=StockMovement.MovementType.ISSUE,
            qty=D("2.0000"),
            memo="Test shipment issue",
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("product_detail", args=[self.product.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test shipment issue")
        self.assertContains(response, "-2.0000")

    def test_product_image_upload(self):
        from django.urls import reverse
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image
        import io

        # Create a simple image in memory using Pillow
        image = Image.new('RGB', (100, 100), color='red')
        file_obj = io.BytesIO()
        image.save(file_obj, format='JPEG')
        file_obj.seek(0)
        
        uploaded_image = SimpleUploadedFile(
            name="test_product.jpg",
            content=file_obj.read(),
            content_type="image/jpeg"
        )

        self.client.force_login(self.user)
        response = self.client.post(
            reverse("product_create"),
            data={
                "sku": "NEWPROD",
                "name": "New Product with Image",
                "type": "stock",
                "uom": "ea",
                "cost": "10.00",
                "price": "20.00",
                "low_stock_threshold": "1.0000",
                "image": uploaded_image,
                "is_active": True,
            }
        )
        self.assertEqual(response.status_code, 302)
        new_prod = Product.objects.get(sku="NEWPROD")
        self.assertTrue(new_prod.image)
        self.assertTrue(new_prod.image.name.endswith(".jpg"))

    def test_product_detail_view_renders_image(self):
        from django.urls import reverse
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image
        import io

        # Create a simple image in memory using Pillow
        image = Image.new('RGB', (100, 100), color='blue')
        file_obj = io.BytesIO()
        image.save(file_obj, format='JPEG')
        file_obj.seek(0)
        
        self.product.image = SimpleUploadedFile(
            name="product_detail.jpg",
            content=file_obj.read(),
            content_type="image/jpeg"
        )
        self.product.save()

        self.client.force_login(self.user)
        response = self.client.get(reverse("product_detail", args=[self.product.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.product.image.url)

    def test_stock_movement_list_renders_source_doc_links(self):
        from django.urls import reverse
        # Create a stock movement that has ref_doc_type and ref_doc_id
        move = post_stock_movement(
            product=self.product,
            movement_type=StockMovement.MovementType.RECEIPT,
            qty=Decimal("5.0000"),
            unit_cost=Decimal("10.00"),
            ref_doc_type="GoodsReceipt",
            ref_doc_id=123,
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("stock_movement_list"))
        self.assertEqual(response.status_code, 200)
        # Verify the Ref Doc has a clickable link to Goods Receipt #123
        self.assertContains(response, '/goods-receipts/123/')

    def test_product_detail_renders_source_doc_links(self):
        from django.urls import reverse
        # Create a stock movement that has ref_doc_type and ref_doc_id
        move = post_stock_movement(
            product=self.product,
            movement_type=StockMovement.MovementType.ISSUE,
            qty=Decimal("1.0000"),
            ref_doc_type="Invoice",
            ref_doc_id=456,
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("product_detail", args=[self.product.pk]))
        self.assertEqual(response.status_code, 200)
        # Verify the movement list links to Invoice #456
        self.assertContains(response, '/invoices/456/')


class LotAndSerialTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(sku="SERIALPROD", name="Serial Product")

    def test_serial_qty_must_be_one(self):
        with self.assertRaises(ValidationError):
            post_stock_movement(
                product=self.product,
                movement_type=StockMovement.MovementType.RECEIPT,
                qty=Decimal("2.0000"),
                serial_no="SN1001",
            )

    def test_lot_auto_creation_and_linking(self):
        from inventory.models import Lot, SerialNumber
        move = post_stock_movement(
            product=self.product,
            movement_type=StockMovement.MovementType.RECEIPT,
            qty=Decimal("1.0000"),
            lot_id="LOT-ABC",
            serial_no="SN1001",
        )
        self.assertEqual(move.lot_id, "LOT-ABC")
        self.assertEqual(move.serial_no, "SN1001")
        
        lot = Lot.objects.get(product=self.product, lot_number="LOT-ABC")
        self.assertIsNotNone(lot)
        
        sn = SerialNumber.objects.get(product=self.product, serial_number="SN1001")
        self.assertEqual(sn.status, SerialNumber.Status.IN_STOCK)
        self.assertEqual(sn.lot, lot)

    def test_duplicate_serial_receipt_fails(self):
        post_stock_movement(
            product=self.product,
            movement_type=StockMovement.MovementType.RECEIPT,
            qty=Decimal("1.0000"),
            serial_no="SN-DUPE",
        )
        with self.assertRaises(ValidationError):
            post_stock_movement(
                product=self.product,
                movement_type=StockMovement.MovementType.RECEIPT,
                qty=Decimal("1.0000"),
                serial_no="SN-DUPE",
            )

    def test_issue_missing_serial_fails(self):
        with self.assertRaises(ValidationError):
            post_stock_movement(
                product=self.product,
                movement_type=StockMovement.MovementType.ISSUE,
                qty=Decimal("1.0000"),
                serial_no="SN-MISSING",
            )

    def test_successful_issue_and_rereceipt(self):
        from inventory.models import SerialNumber
        # 1. Receive SN-123
        post_stock_movement(
            product=self.product,
            movement_type=StockMovement.MovementType.RECEIPT,
            qty=Decimal("1.0000"),
            serial_no="SN-123",
        )
        sn = SerialNumber.objects.get(product=self.product, serial_number="SN-123")
        self.assertEqual(sn.status, SerialNumber.Status.IN_STOCK)

        # 2. Issue SN-123
        post_stock_movement(
            product=self.product,
            movement_type=StockMovement.MovementType.ISSUE,
            qty=Decimal("1.0000"),
            serial_no="SN-123",
        )
        sn.refresh_from_db()
        self.assertEqual(sn.status, SerialNumber.Status.ISSUED)

        # 3. Receive SN-123 again (rereceipt/return)
        post_stock_movement(
            product=self.product,
            movement_type=StockMovement.MovementType.RECEIPT,
            qty=Decimal("1.0000"),
            serial_no="SN-123",
        )
        sn.refresh_from_db()
        self.assertEqual(sn.status, SerialNumber.Status.IN_STOCK)


class WarehouseAndTransferTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(sku="LOCATIONPROD", name="Location Product")

    def test_default_warehouse_creation(self):
        from inventory.models import Location, LocationStock
        # 1. Receipt without location automatically creates and resolves "Main Warehouse"
        move = post_stock_movement(
            product=self.product,
            movement_type=StockMovement.MovementType.RECEIPT,
            qty=Decimal("10.0000"),
            unit_cost=Decimal("5.00"),
        )
        self.assertEqual(move.location.name, "Main Warehouse")
        
        loc_stock = LocationStock.objects.get(product=self.product, location=move.location)
        self.assertEqual(loc_stock.qty, Decimal("10.0000"))
        
        on_hand = StockOnHand.objects.get(product=self.product)
        self.assertEqual(on_hand.qty, Decimal("10.0000"))

    def test_stock_transfers(self):
        from inventory.models import Location, LocationStock
        # 1. Seed stock at source location (Warehouse A)
        wh_a, _ = Location.objects.get_or_create(name="Warehouse A")
        wh_b, _ = Location.objects.get_or_create(name="Warehouse B")
        
        post_stock_movement(
            product=self.product,
            movement_type=StockMovement.MovementType.RECEIPT,
            qty=Decimal("10.0000"),
            location=wh_a,
        )
        
        # 2. Transfer 4 units to Warehouse B
        move = post_stock_movement(
            product=self.product,
            movement_type=StockMovement.MovementType.TRANSFER,
            qty=Decimal("4.0000"),
            location=wh_a,
            to_location=wh_b,
        )
        self.assertEqual(move.location, wh_a)
        self.assertEqual(move.to_location, wh_b)
        
        stock_a = LocationStock.objects.get(product=self.product, location=wh_a)
        stock_b = LocationStock.objects.get(product=self.product, location=wh_b)
        self.assertEqual(stock_a.qty, Decimal("6.0000"))
        self.assertEqual(stock_b.qty, Decimal("4.0000"))
        
        # Global StockOnHand remains 10
        on_hand = StockOnHand.objects.get(product=self.product)
        self.assertEqual(on_hand.qty, Decimal("10.0000"))

    def test_transfer_validation_failures(self):
        from inventory.models import Location
        wh_a, _ = Location.objects.get_or_create(name="Warehouse A")
        
        # Fails if locations are identical
        with self.assertRaises(ValidationError):
            post_stock_movement(
                product=self.product,
                movement_type=StockMovement.MovementType.TRANSFER,
                qty=Decimal("1.0000"),
                location=wh_a,
                to_location=wh_a,
            )

        # Fails if insufficient stock at source location
        wh_b, _ = Location.objects.get_or_create(name="Warehouse B")
        with self.assertRaises(ValidationError):
            post_stock_movement(
                product=self.product,
                movement_type=StockMovement.MovementType.TRANSFER,
                qty=Decimal("50.0000"),
                location=wh_a,
                to_location=wh_b,
            )

    def test_serialized_transfers(self):
        from inventory.models import Location, SerialNumber
        wh_a, _ = Location.objects.get_or_create(name="Warehouse A")
        wh_b, _ = Location.objects.get_or_create(name="Warehouse B")
        
        # Receive serialized item at Warehouse A
        post_stock_movement(
            product=self.product,
            movement_type=StockMovement.MovementType.RECEIPT,
            qty=Decimal("1.0000"),
            serial_no="SN-WH",
            location=wh_a,
        )
        sn = SerialNumber.objects.get(product=self.product, serial_number="SN-WH")
        self.assertEqual(sn.location, wh_a)
        
        # Fails if transferring from wrong source location
        with self.assertRaises(ValidationError):
            post_stock_movement(
                product=self.product,
                movement_type=StockMovement.MovementType.TRANSFER,
                qty=Decimal("1.0000"),
                serial_no="SN-WH",
                location=wh_b,
                to_location=wh_a,
            )

        # Succeeds and updates SN location to WH B
        post_stock_movement(
            product=self.product,
            movement_type=StockMovement.MovementType.TRANSFER,
            qty=Decimal("1.0000"),
            serial_no="SN-WH",
            location=wh_a,
            to_location=wh_b,
        )
        sn.refresh_from_db()
        self.assertEqual(sn.location, wh_b)

    def test_stock_transfer_list_view(self):
        from django.urls import reverse
        from core.models import User, Role
        user = User.objects.create_user(
            username="transfer_operator",
            email="transfers@example.com",
            password="password",
            role=Role.ADMIN,
        )
        self.client.login(username="transfer_operator", password="password")
        
        # Create transfer
        from inventory.models import Location
        wh_a, _ = Location.objects.get_or_create(name="Warehouse A")
        wh_b, _ = Location.objects.get_or_create(name="Warehouse B")
        
        post_stock_movement(
            product=self.product,
            movement_type=StockMovement.MovementType.RECEIPT,
            qty=Decimal("10.0000"),
            location=wh_a,
        )
        
        post_stock_movement(
            product=self.product,
            movement_type=StockMovement.MovementType.TRANSFER,
            qty=Decimal("4.0000"),
            location=wh_a,
            to_location=wh_b,
            memo="Inter-wh transfer 01",
        )
        
        # Test view resolution & rendering
        response = self.client.get(reverse("stock_transfer_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Inventory Transfers")
        self.assertContains(response, "Inter-wh transfer 01")
        self.assertContains(response, "Warehouse A")
        self.assertContains(response, "Warehouse B")


class LocationViewsTests(TestCase):
    def setUp(self):
        from core.models import User, Role
        self.user = User.objects.create_user(
            username="loc_admin",
            email="loc@example.com",
            password="password",
            role=Role.ADMIN,
        )
        from inventory.models import Location
        self.location = Location.objects.create(
            name="North Facility",
            description="Our primary Northern hub",
            is_active=True
        )

    def test_location_list_view(self):
        from django.urls import reverse
        self.client.force_login(self.user)
        response = self.client.get(reverse("location_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Warehouses")
        self.assertContains(response, "North Facility")
        self.assertContains(response, "Our primary Northern hub")

    def test_location_create_view_get(self):
        from django.urls import reverse
        self.client.force_login(self.user)
        response = self.client.get(reverse("location_create"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "New Warehouse")

    def test_location_create_view_post(self):
        from django.urls import reverse
        from inventory.models import Location
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("location_create"),
            data={
                "name": "East Facility",
                "description": "Secondary Eastern depot",
                "is_active": True,
            }
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Location.objects.filter(name="East Facility").exists())

    def test_location_edit_view_get(self):
        from django.urls import reverse
        self.client.force_login(self.user)
        response = self.client.get(reverse("location_edit", args=[self.location.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Edit Warehouse")
        self.assertContains(response, "North Facility")

    def test_location_edit_view_post(self):
        from django.urls import reverse
        from inventory.models import Location
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("location_edit", args=[self.location.pk]),
            data={
                "name": "North Facility - Updated",
                "description": "Updated description",
                "is_active": False,
            }
        )
        self.assertEqual(response.status_code, 302)
        self.location.refresh_from_db()
        self.assertEqual(self.location.name, "North Facility - Updated")
        self.assertEqual(self.location.description, "Updated description")
        self.assertFalse(self.location.is_active)
