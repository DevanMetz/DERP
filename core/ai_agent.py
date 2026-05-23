import json
import re
import urllib.error
import urllib.request
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from accounting.models import Account
from inventory.models import Product
from purchasing.models import PurchaseOrder, PurchaseOrderLine, Vendor
from purchasing.services import resolve_expense_account
from sales.models import Customer, SalesOrder, SalesOrderLine
from sales.services import resolve_revenue_account
from manufacturing.models import BillOfMaterials, ManufacturingOrder

from .docs import list_doc_pages
from .models import Company, CopilotAuditEvent, Role


ACTION_SALT = "derp.ai-action.v1"
DEFAULT_MODEL = "gpt-5.4-mini"
STATE_KEY = "ai_copilot_state"


def run_copilot_turn(message: str, *, api_key: str = "", user=None, session=None, page_context=None) -> dict:
    state = _load_state(session)
    page_context = page_context or {}
    context = _domain_context(user=user, page_context=page_context)

    # Slot-fill from the message itself before we plan — captures qty/cost the user
    # mentioned in passing so the next turn can use them as defaults.
    _absorb_message_into_pending(message, state)

    plan = _plan_with_ai(message, state, context, api_key) if api_key else _plan_without_ai(message, state)
    tools = _execute_tool_plan(plan, state, user=user)

    # Update pending PO state from anything the searches just found.
    _absorb_tools_into_pending(tools, state)

    # Decide whether to attempt an autofill, and which doc to draft.
    doc_intent = _detect_doc_intent(message, state)
    asked_for_doc = bool(re.search(
        r"\b(make|create|draft|build|generate|do|produce|manufacture)\s+(?:a|the)?\s*"
        r"(po|so|mo|purchase\s+order|sales\s+order|manufacturing\s+order|work\s+order)\b",
        (message or ""), re.IGNORECASE,
    ))
    pending = state.get(f"pending_{doc_intent}") or {} if doc_intent else {}
    if doc_intent == "po":
        has_enough = bool(
            pending.get("vendor_name") and pending.get("product_label")
            and pending.get("qty") and pending.get("unit_cost")
        )
    elif doc_intent == "so":
        has_enough = bool(
            pending.get("customer_name") and pending.get("product_label")
            and pending.get("qty") and pending.get("unit_price")
        )
    elif doc_intent == "mo":
        has_enough = bool(pending.get("bom_id") and pending.get("qty_target"))
    else:
        has_enough = False

    should_autofill = _user_wants_best_effort(message) or (asked_for_doc and has_enough)

    if should_autofill and doc_intent:
        tool_name = {
            "po": "draft_purchase_order_preview",
            "so": "draft_sales_order_preview",
            "mo": "draft_manufacturing_order_preview",
        }[doc_intent]
        existing = _tool_result(tools, tool_name)
        if not existing or not existing.get("preview"):
            auto = _autofill_preview(state, user=user, doc_type=doc_intent)
            if auto is not None and auto.get("preview"):
                tools = [t for t in tools if t["name"] != tool_name]
                tools.append({"name": tool_name, "arguments": {"auto": True}, "result": auto})

    response = _build_reply(message, plan, tools, state, user=user)
    response["state"] = {
        "last_vendor": state.get("last_vendor_name", ""),
        "last_customer": state.get("last_customer_name", ""),
        "last_product": state.get("last_product_label", ""),
        "pending_po": _public_pending_po(state),
        "pending_so": _public_pending_so(state),
        "pending_mo": _public_pending_mo(state),
    }
    _save_state(session, state)
    _audit(
        user=user,
        event_type=CopilotAuditEvent.EventType.CHAT,
        message=message,
        tool_names=[tool["name"] for tool in tools],
        metadata={"page": page_context, "intent": plan.get("intent", "")},
    )
    if response.get("preview"):
        _audit(
            user=user,
            event_type=CopilotAuditEvent.EventType.PREVIEW,
            message=message,
            tool_names=[tool["name"] for tool in tools],
            metadata={"preview": _preview_audit_payload(response["preview"])},
        )
    return response


def confirm_action_token(action_token: str, user, session=None) -> dict:
    """Dispatch a signed action token to the right creator. Clears matching
    pending_* state in session after a successful create."""
    try:
        payload = signing.loads(action_token, salt=ACTION_SALT, max_age=60 * 30)
    except signing.BadSignature as exc:
        raise ValidationError("This AI action preview expired or was modified. Please ask again.") from exc

    action = payload.get("action")
    if action == "create_purchase_order_draft":
        result = _confirm_create_purchase_order(payload, user)
        _clear_pending(session, "pending_po")
        return result
    if action == "create_sales_order_draft":
        result = _confirm_create_sales_order(payload, user)
        _clear_pending(session, "pending_so")
        return result
    if action == "create_manufacturing_order_draft":
        result = _confirm_create_manufacturing_order(payload, user)
        _clear_pending(session, "pending_mo")
        return result
    raise ValidationError("Unsupported AI action.")


def _clear_pending(session, slot: str) -> None:
    if session is None:
        return
    state = session.get(STATE_KEY) or {}
    if slot in state:
        state[slot] = {}
        session[STATE_KEY] = state
        session.modified = True


# Backwards-compatible alias — older imports / callers still work.
def confirm_purchase_order_action(action_token: str, user, session=None) -> dict:
    return confirm_action_token(action_token, user, session=session)


@transaction.atomic
def _confirm_create_purchase_order(payload: dict, user) -> dict:
    if not _can_create_purchase_documents(user):
        raise ValidationError("Your role can review AI previews, but cannot create purchasing documents.")

    vendor = Vendor.objects.get(pk=payload["vendor_id"], is_active=True)
    order = PurchaseOrder.objects.create(
        vendor=vendor,
        date=timezone.localdate(),
        expected_date=payload.get("expected_date") or None,
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

    _audit(
        user=user,
        event_type=CopilotAuditEvent.EventType.CONFIRM,
        tool_names=["create_purchase_order_draft"],
        metadata={"vendor": vendor.name, "line_count": len(payload.get("lines") or [])},
        object_type="purchasing.PurchaseOrder",
        object_id=order.pk,
    )
    return {
        "reply": f"Created draft purchase order {order}.",
        "purchase_order_id": order.pk,
        "url": reverse("purchase_order_detail", args=[order.pk]),
    }


@transaction.atomic
def _confirm_create_sales_order(payload: dict, user) -> dict:
    if not _can_create_sales_documents(user):
        raise ValidationError("Your role can review AI previews, but cannot create sales documents.")

    customer = Customer.objects.get(pk=payload["customer_id"], is_active=True)
    order = SalesOrder.objects.create(
        customer=customer,
        date=timezone.localdate(),
        requested_date=payload.get("requested_date") or None,
        status=SalesOrder.Status.DRAFT,
        notes="Draft created from AI copilot preview.",
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )

    for line in payload.get("lines") or []:
        product = Product.objects.filter(pk=line.get("product_id")).first() if line.get("product_id") else None
        qty = Decimal(str(line["qty"]))
        unit_price = Decimal(str(line["unit_price"]))
        description = (line.get("description") or "").strip()
        SalesOrderLine.objects.create(
            order=order,
            product=product,
            description=description or (f"{product.sku} {product.name}" if product else "AI-created line"),
            qty=qty,
            unit_price=unit_price,
            revenue_account=resolve_revenue_account(product=product, customer=customer),
        )

    _audit(
        user=user,
        event_type=CopilotAuditEvent.EventType.CONFIRM,
        tool_names=["create_sales_order_draft"],
        metadata={"customer": customer.name, "line_count": len(payload.get("lines") or [])},
        object_type="sales.SalesOrder",
        object_id=order.pk,
    )
    return {
        "reply": f"Created draft sales order {order}.",
        "sales_order_id": order.pk,
        "url": reverse("sales_order_detail", args=[order.pk]),
    }


@transaction.atomic
def _confirm_create_manufacturing_order(payload: dict, user) -> dict:
    if not _can_create_manufacturing_documents(user):
        raise ValidationError("Your role can review AI previews, but cannot create manufacturing documents.")

    bom = BillOfMaterials.objects.select_related("product").get(pk=payload["bom_id"], is_active=True)
    mo = ManufacturingOrder.objects.create(
        product=bom.product,
        bom=bom,
        qty_target=Decimal(str(payload["qty_target"])),
        date_planned=payload.get("date_planned") or timezone.localdate(),
        status=ManufacturingOrder.Status.DRAFT,
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )

    _audit(
        user=user,
        event_type=CopilotAuditEvent.EventType.CONFIRM,
        tool_names=["create_manufacturing_order_draft"],
        metadata={"product": bom.product.sku, "qty_target": str(mo.qty_target)},
        object_type="manufacturing.ManufacturingOrder",
        object_id=mo.pk,
    )
    return {
        "reply": f"Created draft manufacturing order {mo}.",
        "manufacturing_order_id": mo.pk,
        "url": reverse("mo_detail", args=[mo.pk]),
    }


def build_purchase_order_preview(message: str, api_key: str = "") -> dict:
    return run_copilot_turn(message, api_key=api_key)


def _plan_without_ai(message: str, state: dict) -> dict:
    text = message.strip()
    lower = text.lower()
    if any(term in lower for term in ["doc", "docs", "how do i", "how to", "reverse", "explain"]):
        return {"intent": "answer_docs", "tool_calls": [{"name": "search_docs", "arguments": {"query": text}}]}
    if "vendor" in lower or lower.startswith("find "):
        return {"intent": "lookup", "tool_calls": [{"name": "search_vendors", "arguments": {"query": _query_after_keyword(text, "vendor")}}]}
    if any(term in lower for term in ["product", "sku", "stock"]):
        query = _query_after_keyword(text, "product")
        return {
            "intent": "lookup",
            "tool_calls": [
                {"name": "search_products", "arguments": {"query": query}},
                {"name": "get_stock_levels", "arguments": {"query": query}},
            ],
        }

    update = _stateful_purchase_update(text, state)
    if update:
        return {"intent": "draft_purchase", "tool_calls": [{"name": "draft_purchase_order_preview", "arguments": update}]}

    parsed = _parse_purchase_request(text)
    if parsed:
        return {"intent": "draft_purchase", "tool_calls": [
            {"name": "search_vendors", "arguments": {"query": parsed["vendor"]}},
            {"name": "search_products", "arguments": {"query": parsed["lines"][0]["product"]}},
            {"name": "get_recent_purchase_prices", "arguments": {"product": parsed["lines"][0]["product"], "vendor": parsed["vendor"]}},
            {"name": "draft_purchase_order_preview", "arguments": parsed},
        ]}

    return {
        "intent": "unknown",
        "tool_calls": [{"name": "search_docs", "arguments": {"query": text}}],
        "reply": "I can search ERP data, answer from docs, or draft purchase orders when you give me a vendor, product, quantity, and cost.",
    }


def _plan_with_ai(message: str, state: dict, context: dict, api_key: str) -> dict:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "intent": {"type": "string"},
            "reply": {"type": "string"},
            "tool_calls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string", "enum": [
                            "search_vendors", "search_customers", "search_products",
                            "search_boms", "get_stock_levels", "get_open_purchase_orders",
                            "get_recent_purchase_prices", "search_accounts", "search_docs",
                            "draft_purchase_order_preview", "draft_sales_order_preview",
                            "draft_manufacturing_order_preview",
                        ]},
                        "arguments": {"type": "object"},
                    },
                    "required": ["name", "arguments"],
                },
            },
        },
        "required": ["intent", "reply", "tool_calls"],
    }
    system_prompt = (
        "You are the AI copilot for DERP, an open-source ERP. Your job is to interpret messy natural-language "
        "requests and translate them into structured tool calls. You are lazy and forgiving — accept any phrasing.\n"
        "\n"
        "TOOLS (with argument shapes):\n"
        '  search_vendors({"query": "<name>"}) — fuzzy match; tolerates misspellings and missing spaces.\n'
        '  search_customers({"query": "<name>"}) — fuzzy match.\n'
        '  search_products({"query": "<sku-or-name>"}) — fuzzy match. Returns product.cost (purchase) and product.price (sell).\n'
        '  get_stock_levels({"query": "<product>"})\n'
        '  get_open_purchase_orders({"vendor": "<name>"})\n'
        '  get_recent_purchase_prices({"product": "<name>", "vendor": "<name>"})\n'
        '  search_accounts({"query": "<name-or-code>"})\n'
        '  search_docs({"query": "<topic>"})\n'
        '  draft_purchase_order_preview({\n'
        '     "vendor": "<vendor name as user said it>",\n'
        '     "lines": [{"product": "<product>", "qty": "<number>", "unit_cost": "<number>", "description": "<optional>"}],\n'
        '     "expected_date": "<YYYY-MM-DD or empty>"\n'
        '  }) — preview of a buy. Use for "bought", "purchased", "order from <vendor>", "make a PO".\n'
        '  draft_sales_order_preview({\n'
        '     "customer": "<customer name as user said it>",\n'
        '     "lines": [{"product": "<product>", "qty": "<number>", "unit_price": "<number>", "description": "<optional>"}],\n'
        '     "requested_date": "<YYYY-MM-DD or empty>"\n'
        '  }) — preview of a sale. Use for "sold", "selling", "ship to <customer>", "make an SO".\n'
        '  search_boms({"query": "<product-or-bom-name>"}) — fuzzy search BOMs.\n'
        '  draft_manufacturing_order_preview({\n'
        '     "bom": "<product or BOM name as user said it>",\n'
        '     "qty": "<number to produce>",\n'
        '     "date_planned": "<YYYY-MM-DD or empty>"\n'
        '  }) — preview of a production run. Use for "manufacture", "produce", "build N of X", "make an MO".\n'
        "\n"
        "RULES:\n"
        "  • Pick the right tool from the user's verb. 'bought / purchased / order from <vendor>' → "
        "draft_purchase_order_preview. 'sold / selling / ship to <customer>' → draft_sales_order_preview. "
        "'manufacture / produce / build / make N of <product>' → draft_manufacturing_order_preview.\n"
        "  • Extract party, product, qty, and unit cost/price from ANY phrasing. 'make a po for pla filament "
        "from bambulab 5 pieces $20 each' → draft_purchase_order_preview with vendor='bambulab', "
        "lines=[{product:'pla filament', qty:'5', unit_cost:'20'}]. 'sold 3 widgets to acme for $50 each' → "
        "draft_sales_order_preview with customer='acme', lines=[{product:'widgets', qty:'3', unit_price:'50'}]. "
        "'produce 50 widgets' → draft_manufacturing_order_preview with bom='widgets', qty='50'. "
        "Trust the fuzzy search to resolve names.\n"
        "  • state.pending_po, pending_so, and pending_mo carry partial info across turns. Use whichever "
        "matches the "
        "current intent. If a previous turn established a vendor/customer or product, do NOT re-ask — carry it.\n"
        "  • 'try your best', 'just do it', 'go ahead', 'any', 'whatever' = user authorizes defaults. Fill "
        "missing slots: qty='1', unit_cost=product_cost (PO) or unit_price=product_price (SO). Always call "
        "the appropriate draft tool.\n"
        "  • If only a search is needed (e.g. 'find bambu lab' or 'list products'), call the search tool. The "
        "result feeds the pending state automatically — don't worry about restating it.\n"
        "  • NEVER claim a document was created. The preview goes to the user; they confirm separately.\n"
        "  • Reply text should be short and useful — describe what you did, what's still needed, or 'here is "
        "the preview'.\n"
        "\n"
        "EXAMPLES:\n"
        "  User: 'make a po for pla filament from bambulab 5 pieces $20 each'\n"
        "  → tool_calls: [{name: 'draft_purchase_order_preview', arguments: {vendor: 'bambulab', lines: "
        "[{product: 'pla filament', qty: '5', unit_cost: '20'}]}}]\n"
        "\n"
        "  User: 'bambulab vendor'   (pending_po.product_label was already set)\n"
        "  → tool_calls: [{name: 'search_vendors', arguments: {query: 'bambulab'}}]\n"
        "\n"
        "  User: 'try your best'   (pending_po has vendor + product)\n"
        "  → tool_calls: [{name: 'draft_purchase_order_preview', arguments: {vendor: <pending.vendor_name>, "
        "lines: [{product: <pending.product_sku>, qty: '1', unit_cost: <pending.product_cost or '0'>}]}}]\n"
        "\n"
        "  User: 'sold 3 pla filament to acme corp for $30 each'\n"
        "  → tool_calls: [{name: 'draft_sales_order_preview', arguments: {customer: 'acme corp', lines: "
        "[{product: 'pla filament', qty: '3', unit_price: '30'}]}}]\n"
        "\n"
        "  User: 'build 50 widgets'\n"
        "  → tool_calls: [{name: 'draft_manufacturing_order_preview', arguments: {bom: 'widgets', qty: '50'}}]\n"
        "\n"
        "  User: 'do any BOMs use pla filament?' or 'what BOMs use pla filament?'\n"
        "  → tool_calls: [{name: 'search_boms', arguments: {query: 'pla filament'}}]\n"
        "\n"
        "  User: 'what can we do with pla filament?'\n"
        "  → Exploratory. Call multiple searches to give a useful answer: search_boms (does it appear in any\n"
        "    recipe?), get_stock_levels (how much do we have?), get_recent_purchase_prices (what do we pay?).\n"
        "  → tool_calls: [\n"
        "       {name: 'search_boms', arguments: {query: 'pla filament'}},\n"
        "       {name: 'get_stock_levels', arguments: {query: 'pla filament'}},\n"
        "       {name: 'get_recent_purchase_prices', arguments: {product: 'pla filament', vendor: ''}}\n"
        "     ]"
    )
    payload = {
        "model": DEFAULT_MODEL,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps({"context": context, "state": state, "message": message})},
        ],
        "text": {"format": {"type": "json_schema", "name": "derp_copilot_plan", "strict": False, "schema": schema}},
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
        return json.loads(_extract_response_text(data))
    except (urllib.error.HTTPError, json.JSONDecodeError) as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300] if hasattr(exc, "read") else str(exc)
        _audit(event_type=CopilotAuditEvent.EventType.ERROR, message=message, metadata={"error": detail})
        fallback = _plan_without_ai(message, state)
        fallback["reply"] = f"I could not use the model, so I used local tools instead. {fallback.get('reply', '')}".strip()
        return fallback
    except Exception as exc:
        _audit(event_type=CopilotAuditEvent.EventType.ERROR, message=message, metadata={"error": str(exc)})
        return _plan_without_ai(message, state)


def _execute_tool_plan(plan: dict, state: dict, *, user=None) -> list[dict]:
    results = []
    for call in plan.get("tool_calls", [])[:6]:
        name = call.get("name")
        args = call.get("arguments") or {}
        if name == "search_vendors":
            result = _tool_search_vendors(args.get("query", ""))
        elif name == "search_products":
            result = _tool_search_products(args.get("query", ""))
        elif name == "get_stock_levels":
            result = _tool_get_stock_levels(args.get("query", ""))
        elif name == "get_open_purchase_orders":
            result = _tool_get_open_purchase_orders(args.get("vendor", ""))
        elif name == "get_recent_purchase_prices":
            result = _tool_recent_purchase_prices(args.get("product", ""), args.get("vendor", ""))
        elif name == "search_accounts":
            result = _tool_search_accounts(args.get("query", ""))
        elif name == "search_docs":
            result = _tool_search_docs(args.get("query", ""))
        elif name == "draft_purchase_order_preview":
            result = _tool_draft_purchase_order_preview(args, state, user=user)
        elif name == "search_customers":
            result = _tool_search_customers(args.get("query", ""))
        elif name == "draft_sales_order_preview":
            result = _tool_draft_sales_order_preview(args, state, user=user)
        elif name == "search_boms":
            result = _tool_search_boms(args.get("query", ""))
        elif name == "draft_manufacturing_order_preview":
            result = _tool_draft_manufacturing_order_preview(args, state, user=user)
        else:
            result = {"error": f"Unknown tool: {name}"}
        results.append({"name": name, "arguments": args, "result": result})
    return results


def _build_reply(message: str, plan: dict, tools: list[dict], state: dict, *, user=None) -> dict:
    # Whichever preview tool succeeded wins — return its result immediately.
    for tool_name in (
        "draft_purchase_order_preview",
        "draft_sales_order_preview",
        "draft_manufacturing_order_preview",
    ):
        preview_tool = _tool_result(tools, tool_name)
        if preview_tool:
            return preview_tool

    docs = _tool_result(tools, "search_docs")
    if docs and docs.get("matches"):
        top = docs["matches"][0]
        return {"reply": f"{top['title']}: {top['summary']}", "preview": None, "tool_results": tools}

    vendors = _tool_result(tools, "search_vendors")
    customers = _tool_result(tools, "search_customers")
    products = _tool_result(tools, "search_products")
    boms = _tool_result(tools, "search_boms")
    stock = _tool_result(tools, "get_stock_levels")
    recent = _tool_result(tools, "get_recent_purchase_prices")
    open_pos = _tool_result(tools, "get_open_purchase_orders")

    lines = []
    if vendors is not None:
        lines.append(_format_matches("vendors", vendors.get("matches", []), "name"))
    if customers is not None:
        lines.append(_format_matches("customers", customers.get("matches", []), "name"))
    if products is not None:
        lines.append(_format_matches("products", products.get("matches", []), "label"))
    if boms is not None:
        if boms.get("matches"):
            lines.append("BOMs: " + "; ".join(
                f"{item['name']} → {item['product_label']} (rollup ${item['rollup_cost']})"
                for item in boms["matches"]
            ))
        else:
            lines.append("I found no matching BOMs.")
    if stock is not None and stock.get("matches"):
        lines.append("Stock: " + "; ".join(f"{item['label']} has {item['qty']} on hand" for item in stock["matches"]))
    if recent is not None and recent.get("matches"):
        lines.append("Recent purchase prices: " + "; ".join(f"{item['vendor']} {item['product']} at ${item['unit_cost']}" for item in recent["matches"]))
    if open_pos is not None and open_pos.get("matches"):
        lines.append("Open POs: " + "; ".join(f"{item['number']} with {item['vendor']} ({item['status']})" for item in open_pos["matches"]))
    reply = " ".join(line for line in lines if line)

    # Only surface the pending-so-far summary when the user is clearly continuing
    # a doc draft. Exploratory questions ("what can we do with X", "list boms")
    # should not be hijacked by a stale PO summary.
    intent = _detect_doc_intent(message, state)
    if intent == "so":
        reply += _format_pending_so(state)
    elif intent == "mo":
        reply += _format_pending_mo(state)
    elif intent == "po":
        reply += _format_pending_po(state)

    return {
        "reply": reply.strip() or plan.get("reply") or "I checked the available tools, but need a little more detail.",
        "preview": None,
        "tool_results": tools,
    }


def _format_pending_po(state: dict) -> str:
    pending = state.get("pending_po") or {}
    if not (pending.get("vendor_name") or pending.get("product_label")):
        return ""
    have, missing = [], []
    for label, key, prefix in [
        ("vendor", "vendor_name", ""), ("product", "product_label", ""),
        ("qty", "qty", ""), ("unit cost", "unit_cost", "$"),
    ]:
        if pending.get(key):
            have.append(f"{label}: {prefix}{pending[key]}")
        else:
            missing.append(label)
    out = " Pending PO so far — " + ", ".join(have) + "."
    if missing:
        out += " Still need: " + ", ".join(missing) + ". (Say 'try your best' and I'll use defaults.)"
    return out


def _format_pending_so(state: dict) -> str:
    pending = state.get("pending_so") or {}
    if not (pending.get("customer_name") or pending.get("product_label")):
        return ""
    have, missing = [], []
    for label, key, prefix in [
        ("customer", "customer_name", ""), ("product", "product_label", ""),
        ("qty", "qty", ""), ("unit price", "unit_price", "$"),
    ]:
        if pending.get(key):
            have.append(f"{label}: {prefix}{pending[key]}")
        else:
            missing.append(label)
    out = " Pending SO so far — " + ", ".join(have) + "."
    if missing:
        out += " Still need: " + ", ".join(missing) + ". (Say 'try your best' and I'll use defaults.)"
    return out


def _format_pending_mo(state: dict) -> str:
    pending = state.get("pending_mo") or {}
    if not (pending.get("bom_id") or pending.get("product_label")):
        return ""
    have, missing = [], []
    for label, key, prefix in [
        ("product", "product_label", ""),
        ("BOM", "bom_label", ""),
        ("qty", "qty_target", ""),
    ]:
        if pending.get(key):
            have.append(f"{label}: {prefix}{pending[key]}")
        else:
            missing.append(label)
    out = " Pending MO so far — " + ", ".join(have) + "."
    if missing:
        out += " Still need: " + ", ".join(missing) + ". (Say 'try your best' and I'll use defaults.)"
    return out


def _normalize_for_match(value: str) -> str:
    """Lowercase, strip non-alphanumerics — so 'Bambu Lab' and 'bambulab' both become 'bambulab'."""
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _fuzzy_post_filter(objects, query: str, name_fields: list[str]):
    """When DB icontains found nothing, retry by normalizing whitespace/punct on both sides."""
    nq = _normalize_for_match(query)
    if not nq or len(nq) < 2:
        return []
    scored = []
    for obj in objects:
        best = 0.0
        for field in name_fields:
            haystack = _normalize_for_match(getattr(obj, field, "") or "")
            if not haystack:
                continue
            if nq in haystack or haystack in nq:
                # closer-in-size matches rank higher
                ratio = min(len(nq), len(haystack)) / max(len(nq), len(haystack))
                best = max(best, ratio)
        if best > 0:
            scored.append((best, obj))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [obj for _, obj in scored[:5]]


def _tool_search_vendors(query: str) -> dict:
    base = Vendor.objects.filter(is_active=True)
    qs = base
    if query:
        qs = base.filter(Q(name__icontains=query) | Q(email__icontains=query) | Q(phone__icontains=query))
    vendors = list(qs.order_by("name")[:5])
    if query and not vendors:
        vendors = _fuzzy_post_filter(list(base), query, ["name", "email"])
    matches = [{"id": v.pk, "name": v.name, "email": v.email, "phone": v.phone} for v in vendors]
    return {"matches": matches, "count": len(matches)}


def _tool_search_customers(query: str) -> dict:
    base = Customer.objects.filter(is_active=True)
    qs = base
    if query:
        qs = base.filter(Q(name__icontains=query) | Q(email__icontains=query) | Q(phone__icontains=query))
    customers = list(qs.order_by("name")[:5])
    if query and not customers:
        customers = _fuzzy_post_filter(list(base), query, ["name", "email"])
    matches = [{"id": c.pk, "name": c.name, "email": c.email, "phone": c.phone} for c in customers]
    return {"matches": matches, "count": len(matches)}


def _tool_search_products(query: str) -> dict:
    base = Product.objects.filter(is_active=True)
    qs = base
    if query:
        qs = base.filter(Q(sku__icontains=query) | Q(name__icontains=query) | Q(description__icontains=query))
    products = list(qs.order_by("sku")[:5])
    if query and not products:
        products = _fuzzy_post_filter(list(base), query, ["sku", "name", "description"])
    matches = []
    for product in products:
        matches.append({
            "id": product.pk,
            "sku": product.sku,
            "name": product.name,
            "label": f"{product.sku} - {product.name}",
            "cost": str(product.cost),
            "price": str(product.price),
            "type": product.get_type_display(),
        })
    return {"matches": matches, "count": len(matches)}


def _tool_search_boms(query: str) -> dict:
    """Find BOMs by name, product SKU, or product name."""
    base = BillOfMaterials.objects.filter(is_active=True).select_related("product")
    if query:
        qs = base.filter(
            Q(name__icontains=query)
            | Q(product__sku__icontains=query)
            | Q(product__name__icontains=query)
        )
    else:
        qs = base
    boms = list(qs.order_by("product__sku")[:5])
    if query and not boms:
        # Fuzzy fallback uses normalized whitespace on product SKU/name and BOM name.
        candidates = list(base)
        # Build score from any of (bom.name, product.sku, product.name)
        nq = _normalize_for_match(query)
        if nq and len(nq) >= 2:
            scored = []
            for b in candidates:
                hay_strs = [b.name or "", b.product.sku, b.product.name]
                best = 0.0
                for s in hay_strs:
                    h = _normalize_for_match(s)
                    if h and (nq in h or h in nq):
                        best = max(best, min(len(nq), len(h)) / max(len(nq), len(h)))
                if best > 0:
                    scored.append((best, b))
            scored.sort(key=lambda i: i[0], reverse=True)
            boms = [b for _, b in scored[:5]]

    matches = []
    for b in boms:
        matches.append({
            "id": b.pk,
            "name": b.name or f"BOM - {b.product.sku}",
            "product_id": b.product.pk,
            "product_sku": b.product.sku,
            "product_label": f"{b.product.sku} - {b.product.name}",
            "rollup_cost": str(b.total_cost_rollup),
        })
    return {"matches": matches, "count": len(matches)}


def _tool_get_stock_levels(query: str) -> dict:
    products = _matching_products(query)[:5]
    rows = []
    for product in products:
        qty = product.stock_on_hand.qty if hasattr(product, "stock_on_hand") else Decimal("0.0000")
        rows.append({"id": product.pk, "label": f"{product.sku} - {product.name}", "qty": str(qty)})
    return {"matches": rows, "count": len(rows)}


def _tool_get_open_purchase_orders(vendor: str = "") -> dict:
    qs = PurchaseOrder.objects.exclude(status__in=[PurchaseOrder.Status.CANCELLED, PurchaseOrder.Status.RECEIVED, PurchaseOrder.Status.BILLED]).select_related("vendor")
    if vendor:
        qs = qs.filter(vendor__name__icontains=vendor)
    matches = [{
        "id": po.pk,
        "number": po.number or f"PO-DRAFT-{po.pk}",
        "vendor": po.vendor.name,
        "status": po.get_status_display(),
        "date": po.date.isoformat(),
        "url": reverse("purchase_order_detail", args=[po.pk]),
    } for po in qs.order_by("-date", "-id")[:5]]
    return {"matches": matches, "count": len(matches)}


def _tool_recent_purchase_prices(product: str = "", vendor: str = "") -> dict:
    qs = PurchaseOrderLine.objects.select_related("order__vendor", "product").order_by("-order__date", "-id")
    if product:
        qs = qs.filter(Q(product__sku__icontains=product) | Q(product__name__icontains=product) | Q(description__icontains=product))
    if vendor:
        qs = qs.filter(order__vendor__name__icontains=vendor)
    matches = []
    for line in qs[:5]:
        matches.append({
            "product": line.product.sku if line.product else line.description,
            "vendor": line.order.vendor.name,
            "qty": str(line.qty),
            "unit_cost": str(line.unit_cost),
            "date": line.order.date.isoformat(),
        })
    return {"matches": matches, "count": len(matches)}


def _tool_search_accounts(query: str) -> dict:
    qs = Account.objects.filter(is_active=True, is_postable=True)
    if query:
        qs = qs.filter(Q(code__icontains=query) | Q(name__icontains=query) | Q(type__icontains=query))
    matches = [{"id": a.pk, "code": a.code, "name": a.name, "type": a.get_type_display()} for a in qs.order_by("code")[:8]]
    return {"matches": matches, "count": len(matches)}


def _tool_search_docs(query: str) -> dict:
    terms = [term for term in re.findall(r"[a-z0-9]+", query.lower()) if len(term) > 2]
    scored = []
    for page in list_doc_pages():
        text = page.path.read_text(encoding="utf-8")
        haystack = f"{page.title} {page.summary} {text}".lower()
        score = sum(haystack.count(term) for term in terms)
        if score:
            scored.append((score, page))
    scored.sort(key=lambda item: item[0], reverse=True)
    matches = [{
        "slug": page.slug,
        "title": page.title,
        "summary": page.summary,
        "url": reverse("docs_page", args=[page.slug]),
    } for _, page in scored[:3]]
    return {"matches": matches, "count": len(matches)}


def _tool_draft_purchase_order_preview(args: dict, state: dict, *, user=None) -> dict:
    if not _can_create_purchase_documents(user):
        return {"reply": "Your role can search and review ERP data, but cannot create draft purchase orders.", "preview": None}
    vendor_name = (args.get("vendor") or state.get("last_vendor_name") or "").strip()
    raw_lines = args.get("lines") or state.get("last_po_lines") or []
    expected_date = _parse_expected_date(args.get("expected_date") or "")
    if not vendor_name:
        return {"reply": "Which vendor did you purchase from?", "preview": None}
    if not raw_lines:
        return {"reply": "Which product, quantity, and unit cost should go on the draft PO?", "preview": None}

    vendor_matches = _tool_search_vendors(vendor_name)["matches"]
    if not vendor_matches:
        return {"reply": f"I could not find an active vendor matching \"{vendor_name}\".", "preview": None}
    if len(vendor_matches) > 1 and not any(item["name"].lower() == vendor_name.lower() for item in vendor_matches):
        return {"reply": "I found multiple matching vendors: " + ", ".join(item["name"] for item in vendor_matches) + ". Which one?", "preview": None}
    vendor = Vendor.objects.get(pk=vendor_matches[0]["id"])

    resolved_lines = []
    for line in raw_lines:
        product_name = (line.get("product") or "").strip()
        description = (line.get("description") or product_name).strip()
        product = _find_one(Product.objects.filter(is_active=True), product_name, ["sku", "name"]) if product_name else None
        qty = _decimal_or_none(line.get("qty"), scale="0.0001")
        unit_cost = _decimal_or_none(line.get("unit_cost"), scale="0.01")
        if product_name and product is None:
            return {"reply": f"I could not find a product matching \"{product_name}\". Give me a SKU or use a description-only line.", "preview": None}
        if qty is None or qty <= 0:
            return {"reply": f"Quantity for {product_name or description or 'the line'} must be greater than zero.", "preview": None}
        if unit_cost is None or unit_cost < 0:
            return {"reply": f"Unit cost for {product_name or description or 'the line'} must be zero or greater.", "preview": None}
        resolved_lines.append({
            "product_id": product.pk if product else None,
            "product": product.sku if product else "",
            "description": description or f"{product.sku} {product.name}",
            "qty": str(qty),
            "unit_cost": str(unit_cost),
            "line_total": str((qty * unit_cost).quantize(Decimal("0.01"))),
        })

    total = sum((Decimal(line["line_total"]) for line in resolved_lines), Decimal("0.00"))
    payload = {
        "action": "create_purchase_order_draft",
        "vendor_id": vendor.pk,
        "vendor": vendor.name,
        "expected_date": expected_date.isoformat() if expected_date else "",
        "lines": resolved_lines,
    }
    state["last_vendor_id"] = vendor.pk
    state["last_vendor_name"] = vendor.name
    state["last_po_lines"] = resolved_lines
    state["last_product_label"] = resolved_lines[0]["product"] or resolved_lines[0]["description"]
    state["last_expected_date"] = payload["expected_date"]
    # Sync pending_po with the values actually used in this draft so the summary,
    # if shown later, matches reality.
    state.setdefault("pending_po", {})
    state["pending_po"]["vendor_id"] = vendor.pk
    state["pending_po"]["vendor_name"] = vendor.name
    state["pending_po"]["qty"] = resolved_lines[0]["qty"]
    state["pending_po"]["unit_cost"] = resolved_lines[0]["unit_cost"]

    preview = {
        "title": "Create draft purchase order",
        "vendor": vendor.name,
        "expected_date": payload["expected_date"],
        "lines": resolved_lines,
        "total": str(total.quantize(Decimal("0.01"))),
        "action_token": signing.dumps(payload, salt=ACTION_SALT),
    }
    date_phrase = f" expected {payload['expected_date']}" if payload["expected_date"] else ""
    return {"reply": f"Ready to create a draft PO for {vendor.name}{date_phrase} totaling ${preview['total']}.", "preview": preview}


def _tool_draft_sales_order_preview(args: dict, state: dict, *, user=None) -> dict:
    if not _can_create_sales_documents(user):
        return {"reply": "Your role can search and review ERP data, but cannot create draft sales orders.", "preview": None}

    pending = state.get("pending_so") or {}
    customer_name = (args.get("customer") or pending.get("customer_name") or "").strip()
    raw_lines = args.get("lines") or pending.get("lines") or []
    requested_date = _parse_expected_date(args.get("requested_date") or args.get("expected_date") or "")

    if not customer_name:
        return {"reply": "Which customer is this sale for?", "preview": None}
    if not raw_lines:
        return {"reply": "Which product, quantity, and unit price should go on the draft SO?", "preview": None}

    customer_matches = _tool_search_customers(customer_name)["matches"]
    if not customer_matches:
        return {"reply": f"I could not find an active customer matching \"{customer_name}\".", "preview": None}
    if len(customer_matches) > 1 and not any(item["name"].lower() == customer_name.lower() for item in customer_matches):
        return {"reply": "I found multiple matching customers: " + ", ".join(item["name"] for item in customer_matches) + ". Which one?", "preview": None}
    customer = Customer.objects.get(pk=customer_matches[0]["id"])

    resolved_lines = []
    for line in raw_lines:
        product_name = (line.get("product") or "").strip()
        description = (line.get("description") or product_name).strip()
        product = _find_one(Product.objects.filter(is_active=True), product_name, ["sku", "name"]) if product_name else None
        qty = _decimal_or_none(line.get("qty"), scale="0.0001")
        unit_price = _decimal_or_none(line.get("unit_price") or line.get("unit_cost"), scale="0.01")
        if product_name and product is None:
            return {"reply": f"I could not find a product matching \"{product_name}\". Give me a SKU or use a description-only line.", "preview": None}
        if qty is None or qty <= 0:
            return {"reply": f"Quantity for {product_name or description or 'the line'} must be greater than zero.", "preview": None}
        if unit_price is None or unit_price < 0:
            return {"reply": f"Unit price for {product_name or description or 'the line'} must be zero or greater.", "preview": None}
        resolved_lines.append({
            "product_id": product.pk if product else None,
            "product": product.sku if product else "",
            "description": description or f"{product.sku} {product.name}",
            "qty": str(qty),
            "unit_price": str(unit_price),
            "line_total": str((qty * unit_price).quantize(Decimal("0.01"))),
        })

    total = sum((Decimal(line["line_total"]) for line in resolved_lines), Decimal("0.00"))
    payload = {
        "action": "create_sales_order_draft",
        "customer_id": customer.pk,
        "customer": customer.name,
        "requested_date": requested_date.isoformat() if requested_date else "",
        "lines": resolved_lines,
    }
    state.setdefault("pending_so", {})
    state["pending_so"]["customer_id"] = customer.pk
    state["pending_so"]["customer_name"] = customer.name
    state["pending_so"]["lines"] = resolved_lines
    state["pending_so"]["product_label"] = resolved_lines[0]["product"] or resolved_lines[0]["description"]
    state["pending_so"]["qty"] = resolved_lines[0]["qty"]
    state["pending_so"]["unit_price"] = resolved_lines[0]["unit_price"]
    state["pending_so"]["requested_date"] = payload["requested_date"]

    preview = {
        "title": "Create draft sales order",
        "customer": customer.name,
        "requested_date": payload["requested_date"],
        "lines": resolved_lines,
        "total": str(total.quantize(Decimal("0.01"))),
        "action_token": signing.dumps(payload, salt=ACTION_SALT),
    }
    date_phrase = f" requested {payload['requested_date']}" if payload["requested_date"] else ""
    return {"reply": f"Ready to create a draft SO for {customer.name}{date_phrase} totaling ${preview['total']}.", "preview": preview}


def _tool_draft_manufacturing_order_preview(args: dict, state: dict, *, user=None) -> dict:
    if not _can_create_manufacturing_documents(user):
        return {"reply": "Your role can search and review ERP data, but cannot create manufacturing orders.", "preview": None}

    pending = state.get("pending_mo") or {}
    bom_query = (args.get("bom") or args.get("product") or pending.get("bom_label") or pending.get("product_label") or "").strip()
    qty = _decimal_or_none(args.get("qty") or args.get("qty_target") or pending.get("qty_target"), scale="0.0001")
    date_planned = _parse_expected_date(args.get("date_planned") or args.get("planned_date") or "")

    if not bom_query and not pending.get("bom_id"):
        return {"reply": "Which product (or BOM) should be manufactured?", "preview": None}
    if qty is None or qty <= 0:
        return {"reply": "How many units should be produced?", "preview": None}

    # Resolve BOM. If user gave the finished product, find a BOM whose product matches.
    bom = None
    if pending.get("bom_id"):
        bom = BillOfMaterials.objects.filter(pk=pending["bom_id"], is_active=True).select_related("product").first()
    if bom is None and bom_query:
        bom_matches = _tool_search_boms(bom_query)["matches"]
        if not bom_matches:
            return {"reply": f"I could not find an active BOM matching \"{bom_query}\". Create a BOM for that product first.", "preview": None}
        if len(bom_matches) > 1 and not any(m["product_sku"].lower() == bom_query.lower() for m in bom_matches):
            return {"reply": "I found multiple matching BOMs: " + ", ".join(m["product_label"] for m in bom_matches) + ". Which one?", "preview": None}
        bom = BillOfMaterials.objects.select_related("product").get(pk=bom_matches[0]["id"])

    if bom is None:
        return {"reply": "Could not resolve the BOM. Try giving the product SKU or BOM name.", "preview": None}

    unit_cost = bom.total_cost_rollup
    estimated_total = (qty * unit_cost).quantize(Decimal("0.01"))

    payload = {
        "action": "create_manufacturing_order_draft",
        "bom_id": bom.pk,
        "product_id": bom.product.pk,
        "product_sku": bom.product.sku,
        "product_label": f"{bom.product.sku} - {bom.product.name}",
        "qty_target": str(qty),
        "date_planned": date_planned.isoformat() if date_planned else "",
    }
    state.setdefault("pending_mo", {})
    state["pending_mo"]["bom_id"] = bom.pk
    state["pending_mo"]["bom_label"] = bom.name or f"BOM - {bom.product.sku}"
    state["pending_mo"]["product_id"] = bom.product.pk
    state["pending_mo"]["product_label"] = payload["product_label"]
    state["pending_mo"]["product_sku"] = bom.product.sku
    state["pending_mo"]["qty_target"] = str(qty)
    state["pending_mo"]["date_planned"] = payload["date_planned"]

    preview = {
        "title": "Create draft manufacturing order",
        "product": payload["product_label"],
        "bom": bom.name or f"BOM - {bom.product.sku}",
        "qty_target": str(qty),
        "date_planned": payload["date_planned"],
        "unit_cost": str(unit_cost),
        "estimated_total": str(estimated_total),
        "lines": [{
            "qty": str(qty),
            "product": bom.product.sku,
            "description": payload["product_label"],
            "unit_cost": str(unit_cost),
            "line_total": str(estimated_total),
        }],
        "total": str(estimated_total),
        "action_token": signing.dumps(payload, salt=ACTION_SALT),
    }
    date_phrase = f" planned for {payload['date_planned']}" if payload["date_planned"] else ""
    return {
        "reply": (
            f"Ready to create a draft MO for {qty} x {payload['product_label']}"
            f"{date_phrase}. Estimated material cost ${estimated_total}."
        ),
        "preview": preview,
    }


def _parse_purchase_request(message: str) -> dict | None:
    match = re.search(
        r"(?:purchased|bought|buy|order(?:ed)?)?\s*(?P<qty>\d+(?:\.\d+)?)\s*(?:units?|pcs?|pieces?|ea|each)?\s+(?:of\s+)?(?P<product>.+?)\s+from\s+(?P<vendor>.+?)(?:\s+(?:at|for)\s+\$?(?P<cost>\d+(?:\.\d+)?))?(?:\s*(?:each|ea|per unit))?$",
        message.strip(),
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return {
        "vendor": _clean_name(match.group("vendor")),
        "lines": [{
            "product": _clean_name(match.group("product")),
            "description": "",
            "qty": match.group("qty"),
            "unit_cost": match.group("cost") or "0",
        }],
        "expected_date": _extract_expected_phrase(message),
    }


def _stateful_purchase_update(message: str, state: dict) -> dict | None:
    if not state.get("last_po_lines"):
        return None
    args = {
        "vendor": state.get("last_vendor_name", ""),
        "lines": state["last_po_lines"],
        "expected_date": state.get("last_expected_date", ""),
    }
    qty = re.search(r"\b(?:make it|change it|bought|purchased)?\s*(?P<qty>\d+(?:\.\d+)?)\s+(?:of\s+)?(?:those|that|them|units?)\b", message, re.IGNORECASE)
    if qty:
        args["lines"][0]["qty"] = qty.group("qty")
    cost = re.search(r"\$?(?P<cost>\d+(?:\.\d+)?)\s*(?:each|ea|per unit)", message, re.IGNORECASE)
    if cost:
        args["lines"][0]["unit_cost"] = cost.group("cost")
    expected = _extract_expected_phrase(message)
    if expected:
        args["expected_date"] = expected
    return args if qty or cost or expected else None


def _extract_expected_phrase(message: str) -> str:
    match = re.search(r"(?:expected|due|arriving|receive(?:d)? by|make the expected date)\s+(?P<date>next friday|tomorrow|today|\d{4}-\d{2}-\d{2})", message, re.IGNORECASE)
    return match.group("date") if match else ""


def _parse_expected_date(value: str):
    value = (value or "").strip().lower()
    today = timezone.localdate()
    if not value:
        return None
    if value == "today":
        return today
    if value == "tomorrow":
        return today + timedelta(days=1)
    if value == "next friday":
        days = (4 - today.weekday()) % 7
        return today + timedelta(days=days or 7)
    try:
        return timezone.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _domain_context(*, user, page_context: dict) -> dict:
    company = Company.get()
    return {
        "company": company.name,
        "user_role": getattr(user, "role", ""),
        "can_create_purchase_documents": _can_create_purchase_documents(user),
        "page": page_context,
        "rules": [
            "All draft purchase orders must be previewed before creation.",
            "Posted accounting records are immutable; corrections use reversing entries.",
            "Goods receipts update inventory and should only be posted from issued POs.",
        ],
    }


def _can_create_purchase_documents(user) -> bool:
    return getattr(user, "is_authenticated", False) and getattr(user, "role", None) in {Role.ADMIN, Role.MANAGER, Role.STAFF}


def _can_create_sales_documents(user) -> bool:
    return getattr(user, "is_authenticated", False) and getattr(user, "role", None) in {Role.ADMIN, Role.MANAGER, Role.STAFF}


def _can_create_manufacturing_documents(user) -> bool:
    return getattr(user, "is_authenticated", False) and getattr(user, "role", None) in {Role.ADMIN, Role.MANAGER, Role.STAFF}


def _load_state(session) -> dict:
    if session is None:
        return {}
    return dict(session.get(STATE_KEY, {}))


def _save_state(session, state: dict) -> None:
    if session is None:
        return
    session[STATE_KEY] = {
        "last_vendor_id": state.get("last_vendor_id"),
        "last_vendor_name": state.get("last_vendor_name", ""),
        "last_customer_id": state.get("last_customer_id"),
        "last_customer_name": state.get("last_customer_name", ""),
        "last_product_label": state.get("last_product_label", ""),
        "last_po_lines": state.get("last_po_lines", [])[:3],
        "last_expected_date": state.get("last_expected_date", ""),
        "pending_po": state.get("pending_po", {}),
        "pending_so": state.get("pending_so", {}),
        "pending_mo": state.get("pending_mo", {}),
    }
    session.modified = True


# --- Multi-turn slot filling ----------------------------------------------------

_BEST_EFFORT_PHRASES = (
    "try your best", "your best", "best effort", "just make", "just do",
    "go ahead", "make one", "make it", "any vendor", "any product",
    "available products", "available vendors", "default", "whatever",
)


def _user_wants_best_effort(message: str) -> bool:
    lower = (message or "").lower()
    return any(phrase in lower for phrase in _BEST_EFFORT_PHRASES)


def _pending(state: dict) -> dict:
    state.setdefault("pending_po", {})
    return state["pending_po"]


def _pending_so(state: dict) -> dict:
    state.setdefault("pending_so", {})
    return state["pending_so"]


def _public_pending_po(state: dict) -> dict:
    p = state.get("pending_po") or {}
    return {
        "vendor": p.get("vendor_name", ""),
        "product": p.get("product_label", ""),
        "qty": p.get("qty", ""),
        "unit_cost": p.get("unit_cost", ""),
    }


def _public_pending_so(state: dict) -> dict:
    p = state.get("pending_so") or {}
    return {
        "customer": p.get("customer_name", ""),
        "product": p.get("product_label", ""),
        "qty": p.get("qty", ""),
        "unit_price": p.get("unit_price", ""),
    }


def _public_pending_mo(state: dict) -> dict:
    p = state.get("pending_mo") or {}
    return {
        "product": p.get("product_label", ""),
        "bom": p.get("bom_label", ""),
        "qty_target": p.get("qty_target", ""),
        "date_planned": p.get("date_planned", ""),
    }


def _absorb_message_into_pending(message: str, state: dict) -> None:
    """Intentionally a no-op. The LLM parses messages; Python only carries state."""
    return


def _absorb_tools_into_pending(tools: list[dict], state: dict) -> None:
    """If a search came back with at least one match, remember the top hit."""
    pending_po = _pending(state)
    pending_so = _pending_so(state)
    pending_mo = state.setdefault("pending_mo", {})

    vendors = _tool_result(tools, "search_vendors")
    if vendors and vendors.get("matches"):
        top = vendors["matches"][0]
        pending_po["vendor_id"] = top["id"]
        pending_po["vendor_name"] = top["name"]
        state["last_vendor_id"] = top["id"]
        state["last_vendor_name"] = top["name"]

    customers = _tool_result(tools, "search_customers")
    if customers and customers.get("matches"):
        top = customers["matches"][0]
        pending_so["customer_id"] = top["id"]
        pending_so["customer_name"] = top["name"]
        state["last_customer_id"] = top["id"]
        state["last_customer_name"] = top["name"]

    boms = _tool_result(tools, "search_boms")
    if boms and boms.get("matches"):
        top = boms["matches"][0]
        pending_mo["bom_id"] = top["id"]
        pending_mo["bom_label"] = top["name"]
        pending_mo["product_id"] = top["product_id"]
        pending_mo["product_sku"] = top["product_sku"]
        pending_mo["product_label"] = top["product_label"]

    products = _tool_result(tools, "search_products")
    if products and products.get("matches"):
        top = products["matches"][0]
        # Shared product context — applies to whichever doc the user is building.
        for p in (pending_po, pending_so):
            p["product_id"] = top["id"]
            p["product_label"] = top["label"]
            p["product_sku"] = top["sku"]
            p["product_cost"] = top.get("cost", "0")
            p["product_price"] = top.get("price", "0")
        # For MO, only set product info — don't overwrite an existing bom_id since
        # the user might have selected a BOM separately.
        if not pending_mo.get("product_id"):
            pending_mo["product_id"] = top["id"]
            pending_mo["product_sku"] = top["sku"]
            pending_mo["product_label"] = top["label"]
        state["last_product_label"] = top["label"]


def _detect_doc_intent(message: str, state: dict) -> str:
    """Return 'po', 'so', 'mo', or '' depending on what the user seems to want."""
    lower = (message or "").lower()
    mo_signals = re.search(r"\b(manufacture|manufacturing|produce|production|assemble|build|mo\b|m\.?o\.?|work\s*order|bom)\b", lower)
    so_signals = re.search(r"\b(sold|sale|selling|sells|customer|invoice|ship\s+to|so\b|sales\s*order)\b", lower)
    po_signals = re.search(r"\b(bought|buy|buying|buys|purchase|purchased|vendor|supplier|po\b|order\s+from)\b", lower)

    # Exclusive signals win.
    signals = [(s, name) for s, name in [(mo_signals, "mo"), (so_signals, "so"), (po_signals, "po")] if s]
    if len(signals) == 1:
        return signals[0][1]

    # Fall back to whichever pending_* has the most filled slots.
    po_slots = sum(1 for k in ("vendor_name", "product_label", "qty", "unit_cost") if (state.get("pending_po") or {}).get(k))
    so_slots = sum(1 for k in ("customer_name", "product_label", "qty", "unit_price") if (state.get("pending_so") or {}).get(k))
    mo_slots = sum(1 for k in ("bom_id", "product_label", "qty_target") if (state.get("pending_mo") or {}).get(k))
    best = max((po_slots, "po"), (so_slots, "so"), (mo_slots, "mo"), key=lambda x: x[0])
    if best[0] > 0:
        return best[1]
    return signals[0][1] if signals else ""


def _autofill_preview(state: dict, *, user, doc_type: str = "po") -> dict | None:
    """Best-effort draft: fill missing slots with defaults and call the right preview tool."""
    if doc_type == "so":
        return _autofill_sales_preview(state, user=user)
    if doc_type == "mo":
        return _autofill_manufacturing_preview(state, user=user)
    return _autofill_purchase_preview(state, user=user)


def _autofill_manufacturing_preview(state: dict, *, user) -> dict | None:
    pending = state.setdefault("pending_mo", {})

    bom_id = pending.get("bom_id")
    if not bom_id:
        # Try by product first, then fall back to the first active BOM in the workspace.
        product_hint = pending.get("product_sku") or pending.get("product_label") or ""
        bom = None
        if product_hint:
            bom_matches = _tool_search_boms(product_hint)["matches"]
            if bom_matches:
                bom = BillOfMaterials.objects.filter(pk=bom_matches[0]["id"], is_active=True).select_related("product").first()
        if bom is None:
            bom = BillOfMaterials.objects.filter(is_active=True).select_related("product").order_by("product__sku").first()
        if bom is None:
            return {"reply": "There are no active BOMs in this workspace yet. Create a BOM before drafting an MO.", "preview": None}
        pending["bom_id"] = bom.pk
        pending["bom_label"] = bom.name or f"BOM - {bom.product.sku}"
        pending["product_id"] = bom.product.pk
        pending["product_sku"] = bom.product.sku
        pending["product_label"] = f"{bom.product.sku} - {bom.product.name}"

    args = {
        "bom": pending.get("product_sku") or pending.get("bom_label", ""),
        "qty": pending.get("qty_target") or "1",
        "date_planned": pending.get("date_planned", ""),
    }
    return _tool_draft_manufacturing_order_preview(args, state, user=user)


def _autofill_purchase_preview(state: dict, *, user) -> dict | None:
    pending = _pending(state)

    vendor_name = pending.get("vendor_name") or ""
    if not vendor_name:
        first_vendor = Vendor.objects.filter(is_active=True).order_by("name").first()
        if not first_vendor:
            return {"reply": "There are no active vendors in this workspace yet. Add a vendor first.", "preview": None}
        vendor_name = first_vendor.name
        pending["vendor_id"] = first_vendor.pk
        pending["vendor_name"] = first_vendor.name

    product_label = pending.get("product_label") or ""
    product_cost = pending.get("product_cost") or "0"
    if not product_label:
        first_product = Product.objects.filter(is_active=True).order_by("sku").first()
        if not first_product:
            return {"reply": "There are no active products yet. Add one to draft a purchase order.", "preview": None}
        product_label = f"{first_product.sku} - {first_product.name}"
        pending["product_id"] = first_product.pk
        pending["product_label"] = product_label
        pending["product_sku"] = first_product.sku
        pending["product_cost"] = str(first_product.cost)
        product_cost = str(first_product.cost)

    qty = pending.get("qty") or "1"
    unit_cost = pending.get("unit_cost") or product_cost or "0"

    args = {
        "vendor": vendor_name,
        "lines": [{
            "product": pending.get("product_sku", "") or product_label,
            "description": product_label,
            "qty": qty,
            "unit_cost": unit_cost,
        }],
        "expected_date": "",
    }
    return _tool_draft_purchase_order_preview(args, state, user=user)


def _autofill_sales_preview(state: dict, *, user) -> dict | None:
    pending = _pending_so(state)

    customer_name = pending.get("customer_name") or ""
    if not customer_name:
        first_customer = Customer.objects.filter(is_active=True).order_by("name").first()
        if not first_customer:
            return {"reply": "There are no active customers in this workspace yet. Add a customer first.", "preview": None}
        customer_name = first_customer.name
        pending["customer_id"] = first_customer.pk
        pending["customer_name"] = first_customer.name

    product_label = pending.get("product_label") or ""
    product_price = pending.get("product_price") or "0"
    if not product_label:
        first_product = Product.objects.filter(is_active=True).order_by("sku").first()
        if not first_product:
            return {"reply": "There are no active products yet. Add one to draft a sales order.", "preview": None}
        product_label = f"{first_product.sku} - {first_product.name}"
        pending["product_id"] = first_product.pk
        pending["product_label"] = product_label
        pending["product_sku"] = first_product.sku
        pending["product_price"] = str(first_product.price)
        product_price = str(first_product.price)

    qty = pending.get("qty") or "1"
    unit_price = pending.get("unit_price") or product_price or "0"

    args = {
        "customer": customer_name,
        "lines": [{
            "product": pending.get("product_sku", "") or product_label,
            "description": product_label,
            "qty": qty,
            "unit_price": unit_price,
        }],
        "requested_date": "",
    }
    return _tool_draft_sales_order_preview(args, state, user=user)


def _audit(*, event_type, user=None, message="", tool_names=None, metadata=None, object_type="", object_id=None) -> None:
    """Best-effort audit log. Wrapped in its own savepoint so a failure here
    (e.g. missing table in an un-migrated tenant) can't poison an outer
    transaction.atomic and roll back legitimate work like a PO creation."""
    try:
        with transaction.atomic():
            CopilotAuditEvent.objects.create(
                user=user if getattr(user, "is_authenticated", False) else None,
                event_type=event_type,
                message=message,
                tool_names=tool_names or [],
                metadata=metadata or {},
                object_type=object_type,
                object_id=object_id,
            )
    except Exception:
        pass


def _preview_audit_payload(preview: dict) -> dict:
    return {
        "title": preview.get("title", ""),
        "vendor": preview.get("vendor", ""),
        "total": preview.get("total", ""),
        "line_count": len(preview.get("lines") or []),
    }


def _matching_products(query: str):
    qs = Product.objects.filter(is_active=True).select_related("stock_on_hand")
    if query:
        qs = qs.filter(Q(sku__icontains=query) | Q(name__icontains=query) | Q(description__icontains=query))
    return list(qs.order_by("sku")[:8])


def _find_one(queryset, value: str, fields: list[str]):
    value = (value or "").strip()
    if not value:
        return None
    exact_qs = queryset.none()
    contains_qs = queryset.none()
    for field in fields:
        exact_qs = exact_qs | queryset.filter(**{f"{field}__iexact": value})
        contains_qs = contains_qs | queryset.filter(**{f"{field}__icontains": value})
    hit = exact_qs.first() or contains_qs.first()
    if hit is not None:
        return hit
    # Fuzzy fallback: normalize whitespace/punct on both sides
    fuzzy = _fuzzy_post_filter(list(queryset), value, fields)
    return fuzzy[0] if fuzzy else None


def _decimal_or_none(value, *, scale: str):
    try:
        return Decimal(str(value)).quantize(Decimal(scale))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _tool_result(tools: list[dict], name: str) -> dict | None:
    for tool in tools:
        if tool["name"] == name:
            return tool["result"]
    return None


def _format_matches(label: str, matches: list[dict], key: str) -> str:
    if not matches:
        return f"I found no matching {label}."
    if len(matches) > 1:
        return f"I found these {label}: " + ", ".join(item[key] for item in matches) + "."
    return f"I found {matches[0][key]}."


def _query_after_keyword(text: str, keyword: str) -> str:
    """Pull the name out of phrases like 'vendor X', 'X vendor', or 'find X'."""
    lower = text.lower()
    idx = lower.find(keyword)
    if idx < 0:
        return text
    before = text[:idx].strip(" ,:")
    after = text[idx + len(keyword):].strip(" ,:")
    return after or before


def _extract_response_text(data: dict) -> str:
    if data.get("output_text"):
        return data["output_text"]
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                return content["text"]
    return ""


def _clean_name(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().strip("."))
