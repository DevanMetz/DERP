from decimal import Decimal as D
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from accounting.models import Account, AccountType, Payment
from core.test_utils import DERPTestCase
from inventory.models import Product, StockMovement, StockOnHand
from inventory.services import post_stock_movement
from sales.models import Invoice, SalesOrder

from .models import Checkout, ProductStorefront
from .services import complete_checkout
from . import stripe_service


class StripeServiceTests(SimpleTestCase):
    @override_settings(STRIPE_SECRET_KEY="sk_test_direct")
    @patch("webstore.stripe_service.get_client")
    def test_checkout_session_includes_local_checkout_token(self, get_client):
        client = Mock()
        client.v1.checkout.sessions.create.return_value = Mock(id="cs_test_1")
        get_client.return_value = client

        stripe_service.create_checkout_session(
            product_name="Order #123",
            unit_amount=1250,
            currency="usd",
            quantity=1,
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
            checkout_token="checkout-token",
            customer_email="buyer@example.com",
        )

        params = client.v1.checkout.sessions.create.call_args.kwargs["params"]
        self.assertEqual(params["metadata"]["checkout_token"], "checkout-token")
        self.assertEqual(params["customer_email"], "buyer@example.com")
        self.assertEqual(params["line_items"][0]["price_data"]["unit_amount"], 1250)


class StripeWebhookTests(DERPTestCase):
    @patch("webstore.views.services.complete_checkout")
    @patch("webstore.views.stripe_service.verify_webhook_event")
    @patch("webstore.views.stripe_service.is_configured", return_value=True)
    def test_paid_checkout_webhook_completes_local_order(
        self, is_configured, verify_webhook_event, complete_checkout
    ):
        checkout = Checkout.objects.create(email="buyer@example.com")
        verify_webhook_event.return_value = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "metadata": {"checkout_token": str(checkout.token)},
                    "payment_intent": "pi_test_1",
                }
            },
        }

        response = self.client.post(
            reverse("shop_stripe_webhook"),
            data=b"event",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="signed",
        )

        self.assertEqual(response.status_code, 200)
        complete_checkout.assert_called_once_with(
            checkout,
            stripe_payment_intent="pi_test_1",
        )


class CompleteCheckoutTests(DERPTestCase):
    def setUp(self):
        self.cash = Account.objects.create(code="1010", name="Checking", type=AccountType.ASSET)
        self.ar = Account.objects.create(code="1200", name="Accounts Receivable", type=AccountType.ASSET)
        self.revenue = Account.objects.create(code="4100", name="Product Sales", type=AccountType.REVENUE)
        self.inventory = Account.objects.create(code="1300", name="Inventory", type=AccountType.ASSET)
        self.cogs = Account.objects.create(code="5100", name="COGS - Materials", type=AccountType.EXPENSE)
        self.product = Product.objects.create(
            sku="WIDGET",
            name="Widget",
            price=D("25.00"),
            cost=D("10.00"),
            default_revenue_account=self.revenue,
        )
        self.storefront = ProductStorefront.objects.create(
            product=self.product,
            slug="widget",
            online_price=D("25.00"),
        )
        post_stock_movement(
            product=self.product,
            movement_type=StockMovement.MovementType.RECEIPT,
            qty=D("5.0000"),
            unit_cost=D("10.00"),
        )

    def test_complete_checkout_reuses_auto_created_sales_order_invoice(self):
        checkout = Checkout.objects.create(
            email="buyer@example.com",
            cart_items=[
                {
                    "product_id": self.product.pk,
                    "storefront_id": self.storefront.pk,
                    "sku": self.product.sku,
                    "name": self.product.name,
                    "qty": "2",
                    "unit_price": "25.00",
                    "line_total": "50.00",
                }
            ],
            subtotal=D("50.00"),
            grand_total=D("50.00"),
            status=Checkout.Status.AWAITING_PAYMENT,
        )

        complete_checkout(checkout, stripe_payment_intent="pi_test_1")

        checkout.refresh_from_db()
        self.assertEqual(checkout.status, Checkout.Status.PAID)
        self.assertEqual(checkout.stripe_payment_intent, "pi_test_1")
        self.assertIsNotNone(checkout.sales_order)
        self.assertEqual(SalesOrder.objects.count(), 1)
        self.assertEqual(Invoice.objects.count(), 1)
        invoice = checkout.sales_order.invoices.get()
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(StockOnHand.objects.get(product=self.product).qty, D("3.0000"))
