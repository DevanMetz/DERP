import json
import re
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from accounting.models import Account, AccountType
from inventory.models import Product
from purchasing.models import PurchaseOrder, PurchaseOrderLine, Vendor
from purchasing.services import resolve_expense_account


ACTION_SALT = "derp.ai-action.v1"
DEFAULT_MODEL = "gpt-4.1-mini"


def build_purchase_order_preview(message: str, api_key: str = "") -> dict:
    parsed = _parse_purchase_request_with_ai(message, api_key) if api_key else _parse_purchase_request(message)
    if parsed.get("action") != "create_purchase_order_draft":
        return {
            "reply": parsed.get("reply") or "I can help create draft purchase orders. Try: purchased 20 units of WIDGET from Supply Co at $5 each.",
            "preview": None,
        }

    vendor_name = (parsed.get("vendor") or "").strip()
    raw_lines = parsed.get("lines") or []
    if not vendor_name:
        return {"reply": "Which vendor did you purchase from?", "preview": None}
    if not raw_lines:
        return {"reply": "Which product, quantity, and unit cost should go on the draft PO?", "preview": None}

    vendor = _find_one(Vendor.objects.filter(is_active=True), vendor_name, ["name"])
    if vendor is None:
        return {
            "reply": f"I could not find an active vendor matching \"{vendor_name}\". Create the vendor first, then ask me again.",
            "preview": None,
        }

    resolved_lines = []
    missing = []
    for line in raw_lines:
        qty = _decimal_or_none(line.get("qty"), scale="0.0001")
        unit_cost = _decimal_or_none(line.get("unit_cost"), scale="0.01")
        product_name = (line.get("product") or "").strip()
        description = (line.get("description") or product_name).strip()
        product = _find_one(Product.objects.filter(is_active=True), product_name, ["sku", "name"]) if product_name else None

        if product_name and product is None:
            missing.append(product_name)
            continue
        if qty is None or qty <= 0:
            return {"reply": f"Quantity for {product_name or description or 'the line'} must be greater than zero.", "preview": None}
        if unit_cost is None or unit_cost < 0:
            return {"reply": f"Unit cost for {product_name or description or 'the line'} must be zero or greater.", "preview": None}
        if product is None and not description:
            return {"reply": "Each purchase order line needs a product or description.", "preview": None}

        resolved_lines.append({
            "product_id": product.pk if product else None,
            "product": product.sku if product else "",
            "description": description or f"{product.sku} {product.name}",
            "qty": str(qty),
            "unit_cost": str(unit_cost),
            "line_total": str((qty * unit_cost).quantize(Decimal("0.01"))),
        })

    if missing:
        return {
            "reply": "I could not find these products: " + ", ".join(f"\"{item}\"" for item in missing) + ".",
            "preview": None,
        }
    if not resolved_lines:
        return {"reply": "I could not resolve any purchase order lines.", "preview": None}

    total = sum((Decimal(line["line_total"]) for line in resolved_lines), Decimal("0.00"))
    payload = {
        "action": "create_purchase_order_draft",
        "vendor_id": vendor.pk,
        "vendor": vendor.name,
        "lines": resolved_lines,
    }
    token = signing.dumps(payload, salt=ACTION_SALT)

    return {
        "reply": f"Ready to create a draft PO for {vendor.name} totaling ${total}.",
        "preview": {
            "title": "Create draft purchase order",
            "vendor": vendor.name,
            "lines": resolved_lines,
            "total": str(total.quantize(Decimal("0.01"))),
            "action_token": token,
        },
    }


@transaction.atomic
def confirm_purchase_order_action(action_token: str, user) -> dict:
    try:
        payload = signing.loads(action_token, salt=ACTION_SALT, max_age=60 * 30)
    except signing.BadSignature as exc:
        raise ValidationError("This AI action preview expired or was modified. Please ask again.") from exc

    if payload.get("action") != "create_purchase_order_draft":
        raise ValidationError("Unsupported AI action.")

    vendor = Vendor.objects.get(pk=payload["vendor_id"], is_active=True)
    order = PurchaseOrder.objects.create(
        vendor=vendor,
        date=timezone.localdate(),
        status=PurchaseOrder.Status.DRAFT,
        notes="Draft created from AI copilot preview.",
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )

    for line in payload.get("lines") or []:
        product = Product.objects.filter(pk=line.get("product_id")).first() if line.get("product_id") else None
        qty = Decimal(str(line["qty"]))
        unit_cost = Decimal(str(line["unit_cost"]))
        description = (line.get("description") or "").strip()
        PurchaseOrderLine.objects.create(
            order=order,
            product=product,
            description=description or (f"{product.sku} {product.name}" if product else "AI-created line"),
            qty=qty,
            unit_cost=unit_cost,
            expense_account=resolve_expense_account(product=product, vendor=vendor),
        )

    return {
        "reply": f"Created draft purchase order {order}.",
        "purchase_order_id": order.pk,
        "url": reverse("purchase_order_detail", args=[order.pk]),
    }


def _parse_purchase_request(message: str) -> dict:
    text = message.strip()
    match = re.search(
        r"(?P<qty>\d+(?:\.\d+)?)\s*(?:units?|pcs?|pieces?|ea|each)?\s+(?:of\s+)?(?P<product>.+?)\s+from\s+(?P<vendor>.+?)(?:\s+(?:at|for)\s+\$?(?P<cost>\d+(?:\.\d+)?))?(?:\s*(?:each|ea|per unit))?$",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return {
            "action": "unknown",
            "reply": "I can create draft POs from messages like: purchased 20 units of WIDGET from Supply Co at $5 each.",
        }

    return {
        "action": "create_purchase_order_draft",
        "vendor": _clean_name(match.group("vendor")),
        "lines": [{
            "product": _clean_name(match.group("product")),
            "description": "",
            "qty": match.group("qty"),
            "unit_cost": match.group("cost") or "0",
        }],
    }


def _parse_purchase_request_with_ai(message: str, api_key: str) -> dict:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {"type": "string", "enum": ["create_purchase_order_draft", "unknown"]},
            "vendor": {"type": "string"},
            "reply": {"type": "string"},
            "lines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "product": {"type": "string"},
                        "description": {"type": "string"},
                        "qty": {"type": "string"},
                        "unit_cost": {"type": "string"},
                    },
                    "required": ["product", "description", "qty", "unit_cost"],
                },
            },
        },
        "required": ["action", "vendor", "reply", "lines"],
    }
    payload = {
        "model": DEFAULT_MODEL,
        "input": [
            {
                "role": "system",
                "content": (
                    "You convert ERP purchasing requests into JSON. Only use "
                    "create_purchase_order_draft when the user wants a draft purchase order. "
                    "Do not invent vendor, product, quantity, or cost. Use strings for numbers."
                ),
            },
            {"role": "user", "content": message},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "derp_ai_action",
                "strict": True,
                "schema": schema,
            }
        },
    }

    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"action": "unknown", "reply": f"OpenAI request failed: {body[:300]}", "lines": [], "vendor": ""}
    except Exception as exc:
        return {"action": "unknown", "reply": f"OpenAI request failed: {exc}", "lines": [], "vendor": ""}

    text = _extract_response_text(data)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"action": "unknown", "reply": "The AI response was not valid JSON. Try again or use a more specific request.", "lines": [], "vendor": ""}


def _extract_response_text(data: dict) -> str:
    if data.get("output_text"):
        return data["output_text"]
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                return content["text"]
    return ""


def _find_one(queryset, value: str, fields: list[str]):
    value = (value or "").strip()
    if not value:
        return None
    exact_qs = queryset.none()
    contains_qs = queryset.none()
    for field in fields:
        exact_qs = exact_qs | queryset.filter(**{f"{field}__iexact": value})
        contains_qs = contains_qs | queryset.filter(**{f"{field}__icontains": value})
    return exact_qs.first() or contains_qs.first()


def _decimal_or_none(value, *, scale: str):
    try:
        return Decimal(str(value)).quantize(Decimal(scale))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _clean_name(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().strip("."))
