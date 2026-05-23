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

from accounting.models import Account, JournalEntry
from inventory.models import Product, ProductType, StockMovement
from inventory.services import post_stock_movement
from purchasing.models import Bill, GoodsReceipt, PurchaseOrder, PurchaseOrderLine, Vendor
from purchasing.services import resolve_expense_account
from sales.models import Customer, Invoice, SalesOrder, SalesOrderLine
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

    # If the user is sitting on a record page (e.g. /customers/123/), pre-fill the
    # matching pending slot so questions like "draft a PO" / "what did they buy?"
    # don't require re-typing the name.
    _absorb_page_record_into_pending(context.get("page", {}).get("record"), state)

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
    elif doc_intent == "sm":
        has_enough = bool(pending.get("product_id") and pending.get("movement_type") and pending.get("qty"))
    else:
        has_enough = False

    should_autofill = _user_wants_best_effort(message) or (asked_for_doc and has_enough)

    if should_autofill and doc_intent:
        tool_name = {
            "po": "draft_purchase_order_preview",
            "so": "draft_sales_order_preview",
            "mo": "draft_manufacturing_order_preview",
            "sm": "draft_stock_movement_preview",
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
    if action == "post_stock_movement":
        result = _confirm_post_stock_movement(payload, user)
        _clear_pending(session, "pending_sm")
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


def _confirm_post_stock_movement(payload: dict, user) -> dict:
    """Post a stock movement immediately. Unlike PO/SO/MO/invoice this is not a
    draft — the movement service is itself atomic and the result is a single
    posted row plus an updated stock-on-hand balance."""
    if not _can_post_stock_movements(user):
        raise ValidationError("Your role can review AI previews, but cannot post stock movements.")

    product = Product.objects.get(pk=payload["product_id"], is_active=True)
    movement_type = payload["movement_type"]
    qty = Decimal(str(payload["qty"]))
    unit_cost = Decimal(str(payload.get("unit_cost") or "0"))
    memo = payload.get("memo", "") or "Posted from AI copilot."

    movement = post_stock_movement(
        product=product,
        movement_type=movement_type,
        qty=qty,
        unit_cost=unit_cost,
        memo=memo,
        user=user if getattr(user, "is_authenticated", False) else None,
    )

    _audit(
        user=user,
        event_type=CopilotAuditEvent.EventType.CONFIRM,
        tool_names=["post_stock_movement"],
        metadata={"product": product.sku, "movement_type": movement_type, "qty": str(qty)},
        object_type="inventory.StockMovement",
        object_id=movement.pk,
    )
    label = movement.get_movement_type_display()
    return {
        "reply": f"Posted {label} of {qty} x {product.sku}.",
        "stock_movement_id": movement.pk,
        "url": reverse("stock_movement_list"),
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
                            "draft_manufacturing_order_preview", "get_record_details",
                            "draft_stock_movement_preview",
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
        '  get_record_details({"type": "<customer|vendor|product|sales_order|invoice|purchase_order|'
        'manufacturing_order|bom>", "id": <int>}) — return full info on a specific record, including '
        'recent sales orders/invoices for a customer, recent POs/bills for a vendor, or lines for a doc. '
        'Call this when the user references "this", "them", "it" while the context.page.record is set, '
        'or asks for activity history.\n'
        '  draft_manufacturing_order_preview({\n'
        '     "bom": "<product or BOM name as user said it>",\n'
        '     "qty": "<number to produce>",\n'
        '     "date_planned": "<YYYY-MM-DD or empty>"\n'
        '  }) — preview of a production run. Use for "manufacture", "produce", "build N of X", "make an MO".\n'
        '  draft_stock_movement_preview({\n'
        '     "product": "<product as user said it>",\n'
        '     "movement_type": "receipt|issue|adjustment",\n'
        '     "qty": "<number>",\n'
        '     "unit_cost": "<number or empty for receipts>",\n'
        '     "memo": "<short reason>"\n'
        '  }) — post a stock change. Use for "received N of X", "wrote off N damaged X", '
        '"shipped N to demo", "found N extra X". Map verbs: received/got/restock → receipt; '
        'wrote off/damaged/scrap/lost/shipped/used → issue; found extra/count adjustment → adjustment.\n'
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
        "  • context.page.record (when set) tells you what the user is looking at. e.g. "
        "{type:'customer', id:5, label:'Acme Corp'} means they're on Acme's page. References like 'them', "
        "'this customer', 'what did they buy?' should use that record's id. Call get_record_details({type, "
        "id}) to fetch full info including recent activity.\n"
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
        "  User: 'received 100 of WIDGET at $5 each'\n"
        "  → tool_calls: [{name: 'draft_stock_movement_preview', arguments: {product: 'WIDGET', "
        "movement_type: 'receipt', qty: '100', unit_cost: '5'}}]\n"
        "\n"
        "  User: 'wrote off 5 damaged widgets'\n"
        "  → tool_calls: [{name: 'draft_stock_movement_preview', arguments: {product: 'widgets', "
        "movement_type: 'issue', qty: '5', memo: 'damaged'}}]\n"
        "\n"
        "  User: 'do any BOMs use pla filament?' or 'what BOMs use pla filament?'\n"
        "  → tool_calls: [{name: 'search_boms', arguments: {query: 'pla filament'}}]\n"
        "\n"
        "  User: 'what did they buy last month?'   (context.page.record = {type:'customer', id:5, label:'Acme'})\n"
        "  → tool_calls: [{name: 'get_record_details', arguments: {type: 'customer', id: 5}}]\n"
        "\n"
        "  User: 'what's on this PO?'   (context.page.record = {type:'purchase_order', id:12, label:'PO-001'})\n"
        "  → tool_calls: [{name: 'get_record_details', arguments: {type: 'purchase_order', id: 12}}]\n"
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
        elif name == "get_record_details":
            result = _tool_get_record_details(args.get("type", ""), args.get("id"))
        elif name == "draft_stock_movement_preview":
            result = _tool_draft_stock_movement_preview(args, state, user=user)
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
        "draft_stock_movement_preview",
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

    details = _tool_result(tools, "get_record_details")
    if details and not details.get("error"):
        lines.append(_format_record_details(details))

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


def _format_record_details(d: dict) -> str:
    t = d.get("type")
    if t == "customer":
        bits = [f"{d['name']} (customer)."]
        if d.get("outstanding_ar") and d["outstanding_ar"] != "0.00":
            bits.append(f"Outstanding AR: ${d['outstanding_ar']}.")
        if d.get("recent_invoices"):
            bits.append("Recent invoices: " + "; ".join(
                f"{i['number']} {i['date']} ${i['total']} ({i['status']})"
                for i in d["recent_invoices"]))
        if d.get("recent_sales_orders"):
            bits.append("Recent SOs: " + "; ".join(
                f"{s['number']} {s['date']} ${s['total']} ({s['status']})"
                for s in d["recent_sales_orders"]))
        return " ".join(bits)
    if t == "vendor":
        bits = [f"{d['name']} (vendor)."]
        if d.get("recent_purchase_orders"):
            bits.append("Recent POs: " + "; ".join(
                f"{p['number']} {p['date']} ({p['status']})"
                for p in d["recent_purchase_orders"]))
        if d.get("recent_bills"):
            bits.append("Recent bills: " + "; ".join(
                f"{b['number']} {b['date']} ({b['status']})"
                for b in d["recent_bills"]))
        return " ".join(bits)
    if t == "product":
        return (f"{d['sku']} — {d['name']}. Cost ${d['cost']}, price ${d['price']}, "
                f"qty on hand {d['qty_on_hand']}.")
    if t in ("purchase_order", "sales_order"):
        party = d.get("vendor") or d.get("customer", "")
        unit_key = "unit_cost" if t == "purchase_order" else "unit_price"
        line_str = "; ".join(
            f"{l['qty']} x {l['product']} @ ${l[unit_key]} = ${l['line_total']}"
            for l in d.get("lines", [])
        ) or "no lines"
        return f"{d['number']} for {party} ({d['status']}). Lines: {line_str}."
    if t == "invoice":
        line_str = "; ".join(
            f"{l['qty']} x {l['product']} @ ${l['unit_price']} = ${l['line_total']}"
            for l in d.get("lines", [])
        ) or "no lines"
        return (f"{d['number']} for {d['customer']} ({d['status']}). "
                f"Total ${d['total']} (due ${d['amount_due']}). Lines: {line_str}.")
    if t == "manufacturing_order":
        return (f"{d['number']} producing {d['qty_target']} x {d['product']} "
                f"({d['status']}, planned {d.get('date_planned') or 'TBD'}).")
    if t == "bom":
        comps = "; ".join(f"{c['qty']} x {c['product']}" for c in d.get("components", []))
        return f"{d['name']} (rollup ${d['rollup_cost']}). Components: {comps or 'none'}."
    return ""


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


def _tool_get_record_details(kind: str, record_id) -> dict:
    """Return rich details for a record the user is viewing, plus recent activity.

    Used to answer questions like 'what did they buy last month?' on a customer
    page, 'what's on this PO?' on a PO page, etc.
    """
    try:
        pk = int(record_id)
    except (TypeError, ValueError):
        return {"error": "id must be an integer"}

    if kind == "customer":
        obj = Customer.objects.filter(pk=pk).first()
        if not obj:
            return {"error": f"customer {pk} not found"}
        sos = list(SalesOrder.objects.filter(customer=obj).order_by("-date")[:5])
        invs = list(Invoice.objects.filter(customer=obj).order_by("-date")[:5])
        outstanding = sum((i.amount_due() for i in Invoice.objects.filter(customer=obj).exclude(status__in=["draft", "void"])), Decimal("0.00"))
        return {
            "type": "customer",
            "id": obj.pk,
            "name": obj.name,
            "email": obj.email,
            "outstanding_ar": str(outstanding.quantize(Decimal("0.01"))),
            "recent_sales_orders": [
                {"id": s.pk, "number": s.number or f"SO-DRAFT-{s.pk}", "date": s.date.isoformat(),
                 "status": s.get_status_display(), "total": str(s.subtotal())}
                for s in sos
            ],
            "recent_invoices": [
                {"id": i.pk, "number": i.number or f"DRAFT-{i.pk}", "date": i.date.isoformat(),
                 "status": i.get_status_display(), "total": str(i.total()), "due": str(i.amount_due())}
                for i in invs
            ],
        }

    if kind == "vendor":
        obj = Vendor.objects.filter(pk=pk).first()
        if not obj:
            return {"error": f"vendor {pk} not found"}
        pos = list(PurchaseOrder.objects.filter(vendor=obj).order_by("-date")[:5])
        bills = list(Bill.objects.filter(vendor=obj).order_by("-date")[:5])
        return {
            "type": "vendor",
            "id": obj.pk,
            "name": obj.name,
            "email": obj.email,
            "recent_purchase_orders": [
                {"id": p.pk, "number": p.number or f"PO-DRAFT-{p.pk}", "date": p.date.isoformat(),
                 "status": p.get_status_display()} for p in pos
            ],
            "recent_bills": [
                {"id": b.pk, "number": b.number or f"BILL-DRAFT-{b.pk}", "date": b.date.isoformat(),
                 "status": b.get_status_display()} for b in bills
            ],
        }

    if kind == "product":
        obj = Product.objects.filter(pk=pk).first()
        if not obj:
            return {"error": f"product {pk} not found"}
        qty_on_hand = obj.stock_on_hand.qty if hasattr(obj, "stock_on_hand") else Decimal("0")
        return {
            "type": "product",
            "id": obj.pk,
            "sku": obj.sku,
            "name": obj.name,
            "cost": str(obj.cost),
            "price": str(obj.price),
            "qty_on_hand": str(qty_on_hand),
            "low_stock_threshold": str(obj.low_stock_threshold) if hasattr(obj, "low_stock_threshold") else None,
        }

    if kind in ("purchase_order",):
        obj = PurchaseOrder.objects.select_related("vendor").prefetch_related("lines__product").filter(pk=pk).first()
        if not obj:
            return {"error": f"purchase order {pk} not found"}
        return {
            "type": "purchase_order", "id": obj.pk,
            "number": obj.number or f"PO-DRAFT-{obj.pk}",
            "vendor": obj.vendor.name,
            "date": obj.date.isoformat(),
            "status": obj.get_status_display(),
            "lines": [
                {"product": (l.product.sku if l.product else l.description),
                 "qty": str(l.qty), "unit_cost": str(l.unit_cost),
                 "line_total": str((l.qty * l.unit_cost).quantize(Decimal("0.01")))}
                for l in obj.lines.all()
            ],
        }

    if kind == "sales_order":
        obj = SalesOrder.objects.select_related("customer").prefetch_related("lines__product").filter(pk=pk).first()
        if not obj:
            return {"error": f"sales order {pk} not found"}
        return {
            "type": "sales_order", "id": obj.pk,
            "number": obj.number or f"SO-DRAFT-{obj.pk}",
            "customer": obj.customer.name,
            "date": obj.date.isoformat(),
            "status": obj.get_status_display(),
            "lines": [
                {"product": (l.product.sku if l.product else l.description),
                 "qty": str(l.qty), "unit_price": str(l.unit_price),
                 "line_total": str(l.line_total())}
                for l in obj.lines.all()
            ],
        }

    if kind == "invoice":
        obj = Invoice.objects.select_related("customer").prefetch_related("lines__product").filter(pk=pk).first()
        if not obj:
            return {"error": f"invoice {pk} not found"}
        return {
            "type": "invoice", "id": obj.pk,
            "number": obj.number or f"DRAFT-{obj.pk}",
            "customer": obj.customer.name,
            "date": obj.date.isoformat(),
            "due_date": obj.due_date.isoformat() if obj.due_date else None,
            "status": obj.get_status_display(),
            "subtotal": str(obj.subtotal()),
            "tax": str(obj.tax_total()),
            "total": str(obj.total()),
            "amount_due": str(obj.amount_due()),
            "lines": [
                {"product": (l.product.sku if l.product else l.description),
                 "qty": str(l.qty), "unit_price": str(l.unit_price),
                 "line_total": str(l.line_total())}
                for l in obj.lines.all()
            ],
        }

    if kind == "manufacturing_order":
        obj = ManufacturingOrder.objects.select_related("product", "bom").filter(pk=pk).first()
        if not obj:
            return {"error": f"manufacturing order {pk} not found"}
        return {
            "type": "manufacturing_order", "id": obj.pk,
            "number": obj.number or f"MO-DRAFT-{obj.pk}",
            "product": f"{obj.product.sku} - {obj.product.name}",
            "qty_target": str(obj.qty_target),
            "qty_produced": str(obj.qty_produced),
            "status": obj.get_status_display(),
            "date_planned": obj.date_planned.isoformat() if obj.date_planned else None,
        }

    if kind == "bom":
        obj = BillOfMaterials.objects.select_related("product").prefetch_related("components__product").filter(pk=pk).first()
        if not obj:
            return {"error": f"BOM {pk} not found"}
        return {
            "type": "bom", "id": obj.pk,
            "name": obj.name or f"BOM - {obj.product.sku}",
            "product": f"{obj.product.sku} - {obj.product.name}",
            "rollup_cost": str(obj.total_cost_rollup),
            "components": [
                {"product": f"{c.product.sku} - {c.product.name}",
                 "qty": str(c.qty), "extended_cost": str(c.extended_cost)}
                for c in obj.components.all()
            ],
        }

    return {"error": f"Unknown record type: {kind}"}


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


_STOCK_MOVEMENT_TYPES = {
    "receipt", "issue", "adjustment",
    # Friendly aliases that LLM might use; mapped in the resolver below.
    "in", "out", "increase", "decrease", "writeoff", "write-off", "scrap",
}


def _resolve_movement_type(raw: str, message: str = "") -> str | None:
    """Map a free-form movement type hint to one of receipt|issue|adjustment."""
    if not raw:
        # Fall back to inferring from message verbs.
        lower = (message or "").lower()
        if re.search(r"\b(receiv|got|incoming|restock|delivered|stock\s*in)\b", lower):
            return "receipt"
        if re.search(r"\b(wrote?\s*off|write[\s-]?off|damaged?|scrap|destroy|lost|issued?|used|consumed|shipped\s+to)\b", lower):
            return "issue"
        if re.search(r"\b(found|extra|count(?:ed)?\s+(?:up|extra)|adjustment|adjust)\b", lower):
            return "adjustment"
        return None
    r = raw.strip().lower()
    if r in ("receipt", "in", "increase"):
        return "receipt"
    if r in ("issue", "out", "writeoff", "write-off", "scrap", "decrease"):
        return "issue"
    if r == "adjustment":
        return "adjustment"
    return None


def _tool_draft_stock_movement_preview(args: dict, state: dict, *, user=None) -> dict:
    """Build a preview of a stock movement (receipt, issue, adjustment) the user
    must confirm. Unlike PO/SO/MO this posts to inventory immediately on confirm —
    there is no DRAFT intermediate state for movements."""
    if not _can_post_stock_movements(user):
        return {"reply": "Your role can search and review ERP data, but cannot post stock movements.", "preview": None}

    pending = state.setdefault("pending_sm", {})
    product_hint = (args.get("product") or pending.get("product_label") or pending.get("product_sku") or "").strip()
    qty = _decimal_or_none(args.get("qty") or pending.get("qty"), scale="0.0001")
    unit_cost = _decimal_or_none(args.get("unit_cost") or pending.get("unit_cost"), scale="0.01")
    memo = (args.get("memo") or pending.get("memo") or "").strip()
    movement_type = _resolve_movement_type(args.get("movement_type", ""), args.get("_user_message", ""))

    if not product_hint:
        return {"reply": "Which product is moving?", "preview": None}
    if not movement_type:
        return {"reply": "Is this a receipt (stock in), issue (stock out), or adjustment?", "preview": None}
    if qty is None or qty <= 0:
        return {"reply": "How many units?", "preview": None}

    product = _find_one(Product.objects.filter(is_active=True), product_hint, ["sku", "name", "description"])
    if product is None:
        return {"reply": f"I could not find a product matching \"{product_hint}\".", "preview": None}
    if product.type != ProductType.STOCK:
        return {"reply": f"{product.sku} is not a stock-tracked product — only stock items can have movements.", "preview": None}

    # For RECEIPT, default unit_cost to the current product cost if not given.
    if unit_cost is None:
        unit_cost = Decimal(str(product.cost))

    # For ISSUE, surface a stock-shortage warning early (the service also validates).
    on_hand = getattr(product, "stock_on_hand", None)
    on_hand_qty = on_hand.qty if on_hand else Decimal("0")
    if movement_type == "issue" and qty > on_hand_qty:
        return {
            "reply": (f"Cannot issue {qty} x {product.sku}: only {on_hand_qty} on hand. "
                      "Adjust the quantity or receive more first."),
            "preview": None,
        }

    payload = {
        "action": "post_stock_movement",
        "product_id": product.pk,
        "product_sku": product.sku,
        "product_label": f"{product.sku} - {product.name}",
        "movement_type": movement_type,
        "qty": str(qty),
        "unit_cost": str(unit_cost),
        "memo": memo,
    }
    pending["product_id"] = product.pk
    pending["product_sku"] = product.sku
    pending["product_label"] = payload["product_label"]
    pending["movement_type"] = movement_type
    pending["qty"] = str(qty)
    pending["unit_cost"] = str(unit_cost)
    pending["memo"] = memo

    type_label = movement_type.capitalize()
    extended = (qty * unit_cost).quantize(Decimal("0.01"))
    preview = {
        "title": f"Post {type_label}",
        "product": payload["product_label"],
        "movement_type": type_label,
        "qty": str(qty),
        "unit_cost": str(unit_cost),
        "memo": memo,
        "on_hand_before": str(on_hand_qty),
        "extended": str(extended),
        "action_token": signing.dumps(payload, salt=ACTION_SALT),
    }
    cost_phrase = f" at ${unit_cost} each" if movement_type == "receipt" else ""
    memo_phrase = f" — {memo}" if memo else ""
    return {
        "reply": f"Ready to post {type_label} of {qty} x {product.sku}{cost_phrase}{memo_phrase}.",
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
    enriched_page = dict(page_context or {})
    record = _resolve_page_record(enriched_page)
    if record:
        enriched_page["record"] = record
    return {
        "company": company.name,
        "user_role": getattr(user, "role", ""),
        "can_create_purchase_documents": _can_create_purchase_documents(user),
        "page": enriched_page,
        "rules": [
            "All draft purchase orders must be previewed before creation.",
            "Posted accounting records are immutable; corrections use reversing entries.",
            "Goods receipts update inventory and should only be posted from issued POs.",
        ],
    }


# Map URL path prefixes → record type. Order matters: more specific first.
_PAGE_ROUTES = [
    (re.compile(r"^/customers/(\d+)/?"), "customer"),
    (re.compile(r"^/vendors/(\d+)/?"), "vendor"),
    (re.compile(r"^/products/(\d+)/?"), "product"),
    (re.compile(r"^/sales-orders/(\d+)/?"), "sales_order"),
    (re.compile(r"^/invoices/(\d+)/?"), "invoice"),
    (re.compile(r"^/purchase-orders/(\d+)/?"), "purchase_order"),
    (re.compile(r"^/goods-receipts/(\d+)/?"), "goods_receipt"),
    (re.compile(r"^/bills/(\d+)/?"), "bill"),
    (re.compile(r"^/manufacturing-orders/(\d+)/?"), "manufacturing_order"),
    (re.compile(r"^/boms/(\d+)/?"), "bom"),
    (re.compile(r"^/journal/(\d+)/?"), "journal_entry"),
]


def _resolve_page_record(page_context: dict) -> dict | None:
    """Parse the path and resolve the record the user is looking at.

    Returns a dict like {type, id, label, summary} that the LLM can use, or None
    if the path doesn't reference a specific record.
    """
    path = (page_context.get("path") or "").rstrip("/") + "/"
    for pattern, kind in _PAGE_ROUTES:
        match = pattern.match(path)
        if not match:
            continue
        try:
            return _summarize_record(kind, int(match.group(1)))
        except Exception:
            return None
    return None


def _summarize_record(kind: str, pk: int) -> dict | None:
    if kind == "customer":
        obj = Customer.objects.filter(pk=pk, is_active=True).first()
        if not obj:
            return None
        return {"type": "customer", "id": obj.pk, "label": obj.name,
                "summary": f"Customer {obj.name} (id={obj.pk}). Email: {obj.email or 'n/a'}."}
    if kind == "vendor":
        obj = Vendor.objects.filter(pk=pk, is_active=True).first()
        if not obj:
            return None
        return {"type": "vendor", "id": obj.pk, "label": obj.name,
                "summary": f"Vendor {obj.name} (id={obj.pk}). Email: {obj.email or 'n/a'}."}
    if kind == "product":
        obj = Product.objects.filter(pk=pk, is_active=True).first()
        if not obj:
            return None
        return {"type": "product", "id": obj.pk, "label": f"{obj.sku} - {obj.name}",
                "summary": f"Product {obj.sku} ({obj.name}). Cost ${obj.cost}, price ${obj.price}."}
    if kind == "sales_order":
        obj = SalesOrder.objects.select_related("customer").filter(pk=pk).first()
        if not obj:
            return None
        return {"type": "sales_order", "id": obj.pk,
                "label": obj.number or f"SO-DRAFT-{obj.pk}",
                "summary": f"Sales order {obj} for {obj.customer.name}, status={obj.get_status_display()}."}
    if kind == "invoice":
        obj = Invoice.objects.select_related("customer").filter(pk=pk).first()
        if not obj:
            return None
        return {"type": "invoice", "id": obj.pk,
                "label": obj.number or f"DRAFT-{obj.pk}",
                "summary": f"Invoice {obj} for {obj.customer.name}, status={obj.get_status_display()}, total ${obj.total()}."}
    if kind == "purchase_order":
        obj = PurchaseOrder.objects.select_related("vendor").filter(pk=pk).first()
        if not obj:
            return None
        return {"type": "purchase_order", "id": obj.pk,
                "label": obj.number or f"PO-DRAFT-{obj.pk}",
                "summary": f"Purchase order {obj} from {obj.vendor.name}, status={obj.get_status_display()}."}
    if kind == "goods_receipt":
        obj = GoodsReceipt.objects.select_related("po__vendor").filter(pk=pk).first()
        if not obj:
            return None
        vendor = obj.po.vendor.name if obj.po else "n/a"
        return {"type": "goods_receipt", "id": obj.pk, "label": f"GR-{obj.pk}",
                "summary": f"Goods receipt #{obj.pk} from {vendor}, date {obj.date}."}
    if kind == "bill":
        obj = Bill.objects.select_related("vendor").filter(pk=pk).first()
        if not obj:
            return None
        return {"type": "bill", "id": obj.pk,
                "label": obj.number or f"BILL-DRAFT-{obj.pk}",
                "summary": f"Bill {obj} from {obj.vendor.name}, status={obj.get_status_display()}."}
    if kind == "manufacturing_order":
        obj = ManufacturingOrder.objects.select_related("product", "bom").filter(pk=pk).first()
        if not obj:
            return None
        return {"type": "manufacturing_order", "id": obj.pk,
                "label": obj.number or f"MO-DRAFT-{obj.pk}",
                "summary": (f"MO {obj} producing {obj.qty_target} x {obj.product.sku}, "
                            f"status={obj.get_status_display()}.")}
    if kind == "bom":
        obj = BillOfMaterials.objects.select_related("product").filter(pk=pk, is_active=True).first()
        if not obj:
            return None
        return {"type": "bom", "id": obj.pk,
                "label": obj.name or f"BOM - {obj.product.sku}",
                "summary": f"BOM for {obj.product.sku} ({obj.product.name}), rollup cost ${obj.total_cost_rollup}."}
    if kind == "journal_entry":
        obj = JournalEntry.objects.filter(pk=pk).first()
        if not obj:
            return None
        return {"type": "journal_entry", "id": obj.pk,
                "label": obj.number or f"JE-{obj.pk}",
                "summary": f"Journal entry {obj}, date {obj.date}."}
    return None


def _can_create_purchase_documents(user) -> bool:
    return getattr(user, "is_authenticated", False) and getattr(user, "role", None) in {Role.ADMIN, Role.MANAGER, Role.STAFF}


def _can_create_sales_documents(user) -> bool:
    return getattr(user, "is_authenticated", False) and getattr(user, "role", None) in {Role.ADMIN, Role.MANAGER, Role.STAFF}


def _can_create_manufacturing_documents(user) -> bool:
    return getattr(user, "is_authenticated", False) and getattr(user, "role", None) in {Role.ADMIN, Role.MANAGER, Role.STAFF}


def _can_post_stock_movements(user) -> bool:
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
        "pending_sm": state.get("pending_sm", {}),
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


def _absorb_page_record_into_pending(record: dict | None, state: dict) -> None:
    """If the user is on a record page, seed the right pending slot.

    Only fills empty slots — never overwrites something the user explicitly set in
    a previous turn.
    """
    if not record:
        return
    kind = record.get("type")
    rid = record.get("id")
    label = record.get("label", "")

    if kind == "vendor":
        po = state.setdefault("pending_po", {})
        if not po.get("vendor_id"):
            po["vendor_id"] = rid
            po["vendor_name"] = label
            state["last_vendor_id"] = rid
            state["last_vendor_name"] = label
    elif kind == "customer":
        so = state.setdefault("pending_so", {})
        if not so.get("customer_id"):
            so["customer_id"] = rid
            so["customer_name"] = label
            state["last_customer_id"] = rid
            state["last_customer_name"] = label
    elif kind == "product":
        obj = Product.objects.filter(pk=rid).first()
        if obj:
            for slot in ("pending_po", "pending_so", "pending_mo"):
                p = state.setdefault(slot, {})
                if not p.get("product_id"):
                    p["product_id"] = obj.pk
                    p["product_sku"] = obj.sku
                    p["product_label"] = f"{obj.sku} - {obj.name}"
                    p["product_cost"] = str(obj.cost)
                    p["product_price"] = str(obj.price)
    elif kind == "bom":
        mo = state.setdefault("pending_mo", {})
        if not mo.get("bom_id"):
            mo["bom_id"] = rid
            mo["bom_label"] = label
            obj = BillOfMaterials.objects.select_related("product").filter(pk=rid).first()
            if obj:
                mo["product_id"] = obj.product.pk
                mo["product_sku"] = obj.product.sku
                mo["product_label"] = f"{obj.product.sku} - {obj.product.name}"


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
    """Return 'po', 'so', 'mo', 'sm', or '' depending on what the user seems to want."""
    lower = (message or "").lower()
    # Stock movements (receipt/issue/adjustment) — these are NOT POs even though
    # 'received' is involved, so check first.
    sm_signals = re.search(
        r"\b(receiv\w+|stock\s*in|restock|wrote?\s*off|write[\s-]?off|damaged?|scrap|"
        r"destroy|lost|found\s+\d|adjust\s+stock|inventory\s+count)\b",
        lower,
    )
    mo_signals = re.search(r"\b(manufacture|manufacturing|produce|production|assemble|build|mo\b|m\.?o\.?|work\s*order|bom)\b", lower)
    so_signals = re.search(r"\b(sold|sale|selling|sells|customer|invoice|ship\s+to|so\b|sales\s*order)\b", lower)
    po_signals = re.search(r"\b(bought|buy|buying|buys|purchase|purchased|vendor|supplier|po\b|order\s+from)\b", lower)

    signals = [(s, name) for s, name in [
        (sm_signals, "sm"), (mo_signals, "mo"), (so_signals, "so"), (po_signals, "po"),
    ] if s]
    if len(signals) == 1:
        return signals[0][1]

    po_slots = sum(1 for k in ("vendor_name", "product_label", "qty", "unit_cost") if (state.get("pending_po") or {}).get(k))
    so_slots = sum(1 for k in ("customer_name", "product_label", "qty", "unit_price") if (state.get("pending_so") or {}).get(k))
    mo_slots = sum(1 for k in ("bom_id", "product_label", "qty_target") if (state.get("pending_mo") or {}).get(k))
    sm_slots = sum(1 for k in ("product_id", "movement_type", "qty") if (state.get("pending_sm") or {}).get(k))
    best = max((po_slots, "po"), (so_slots, "so"), (mo_slots, "mo"), (sm_slots, "sm"), key=lambda x: x[0])
    if best[0] > 0:
        return best[1]
    return signals[0][1] if signals else ""


def _autofill_preview(state: dict, *, user, doc_type: str = "po") -> dict | None:
    """Best-effort draft: fill missing slots with defaults and call the right preview tool."""
    if doc_type == "so":
        return _autofill_sales_preview(state, user=user)
    if doc_type == "mo":
        return _autofill_manufacturing_preview(state, user=user)
    if doc_type == "sm":
        return _autofill_stock_movement_preview(state, user=user)
    return _autofill_purchase_preview(state, user=user)


def _autofill_stock_movement_preview(state: dict, *, user) -> dict | None:
    pending = state.setdefault("pending_sm", {})
    # Default to a receipt of qty=1 of the pending product (or first stock product).
    if not pending.get("product_id"):
        first = Product.objects.filter(is_active=True, type=ProductType.STOCK).order_by("sku").first()
        if not first:
            return {"reply": "There are no stock-tracked products in this workspace yet.", "preview": None}
        pending["product_id"] = first.pk
        pending["product_sku"] = first.sku
        pending["product_label"] = f"{first.sku} - {first.name}"
        pending["unit_cost"] = str(first.cost)

    return _tool_draft_stock_movement_preview({
        "product": pending.get("product_sku") or pending.get("product_label", ""),
        "movement_type": pending.get("movement_type") or "receipt",
        "qty": pending.get("qty") or "1",
        "unit_cost": pending.get("unit_cost") or "",
        "memo": pending.get("memo") or "",
    }, state, user=user)


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
