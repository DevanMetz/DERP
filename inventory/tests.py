from decimal import Decimal
import os
import shutil
import tempfile

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from inventory.models import Product, ProductType, StockMovement, StockOnHand
from inventory.services import post_stock_movement


D = Decimal


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

