from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from core.test_utils import DERPTestCase

from .models import Checkout
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
