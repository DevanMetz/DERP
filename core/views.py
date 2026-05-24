from django.contrib.auth.decorators import login_required
from django.contrib import messages
import json

from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django import forms
from django.utils import timezone
from datetime import date
from decimal import Decimal
from django.db.models import Sum, Q

from .models import Company, Role
from .permissions import write_required
from accounting.models import Account, AccountType, JournalEntry, JournalLine
from inventory.models import Product, ProductType
from purchasing.models import Vendor
from sales.models import Customer
from .ai_agent import confirm_purchase_order_action, run_copilot_turn
from .docs import get_doc_page, list_doc_pages, render_doc_markdown

ZERO = Decimal("0.00")


# ---------------------------------------------------------------------------
# Shared filter / sort helper
# ---------------------------------------------------------------------------

def apply_filters(qs, request, sort_fields=None):
    """
    Generic helper used by every list view.
    Reads GET params and applies:
      - ?q=<text>  → search across `search_fields` if provided via qs.filter
      - ?sort=<field>&dir=<asc|desc>  → ordering
      - ?date_from / ?date_to  → filter on a date field (default: "date")
      - ?status=<value>         → exact match on "status" field
      - ?name=<text>            → icontains on a name/reference field (passed as `name_field`)
    Returns (filtered_qs, sort_field, sort_dir) so the template can render indicators.
    """
    sort_fields = sort_fields or []
    sort = request.GET.get("sort", "")
    direction = request.GET.get("dir", "desc")
    if direction not in ("asc", "desc"):
        direction = "desc"

    if sort and sort in sort_fields:
        order_by = sort if direction == "asc" else f"-{sort}"
        qs = qs.order_by(order_by)

    return qs, sort, direction




@login_required
def home(request):
    company = Company.get()
    all_stock_products = Product.objects.filter(type=ProductType.STOCK, is_active=True).select_related("stock_on_hand")
    low_stock_products = [p for p in all_stock_products if p.is_low_stock]
    return render(request, "core/home.html", {
        "company": company,
        "low_stock_products": low_stock_products,
    })


@login_required
def docs_index(request):
    return render(request, "core/docs_index.html", {
        "pages": list_doc_pages(),
    })


@login_required
def docs_page(request, slug):
    page = get_doc_page(slug)
    if page is None:
        raise Http404("Documentation page not found")

    return render(request, "core/docs_page.html", {
        "page": page,
        "pages": list_doc_pages(),
        "content": render_doc_markdown(page),
    })


@login_required
def ai_chat(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required."}, status=405)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    message = (payload.get("message") or "").strip()
    if not message:
        return JsonResponse({"error": "Message is required."}, status=400)

    result = run_copilot_turn(
        message,
        api_key=(payload.get("api_key") or "").strip(),
        user=request.user,
        session=request.session,
        page_context=payload.get("page_context") or {},
    )
    return JsonResponse(result)


@login_required
@write_required
def ai_confirm(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required."}, status=405)
    try:
        payload = json.loads(request.body.decode("utf-8"))
        result = confirm_purchase_order_action(payload.get("action_token") or "", request.user, session=request.session)
    except (json.JSONDecodeError, ValidationError, Vendor.DoesNotExist, Customer.DoesNotExist) as exc:
        message = exc.messages[0] if hasattr(exc, "messages") else str(exc)
        return JsonResponse({"error": message}, status=400)
    return JsonResponse(result)


@login_required
def dashboard_view(request):
    company = Company.get()
    today = timezone.localdate()
    current_year = today.year
    start_date = date(current_year, 1, 1)
    end_date = date(current_year, 12, 31)

    # 1. YTD Revenue: Sum credit minus debit for REVENUE in current year
    rev_totals = JournalLine.objects.filter(
        entry__status=JournalEntry.Status.POSTED,
        entry__date__gte=start_date,
        entry__date__lte=end_date,
        account__type=AccountType.REVENUE
    ).aggregate(d=Sum("debit"), c=Sum("credit"))
    ytd_revenue = (rev_totals["c"] or ZERO) - (rev_totals["d"] or ZERO)

    # 2. YTD Expenses: Sum debit minus credit for EXPENSE in current year
    exp_totals = JournalLine.objects.filter(
        entry__status=JournalEntry.Status.POSTED,
        entry__date__gte=start_date,
        entry__date__lte=end_date,
        account__type=AccountType.EXPENSE
    ).aggregate(d=Sum("debit"), c=Sum("credit"))
    ytd_expenses = (exp_totals["d"] or ZERO) - (exp_totals["c"] or ZERO)

    # 3. Net Profit
    net_profit = ytd_revenue - ytd_expenses

    # 4. Outstanding AR (Asset: Debit - Credit for code '1200')
    ar_totals = JournalLine.objects.filter(
        entry__status=JournalEntry.Status.POSTED,
        entry__date__lte=today,
        account__code="1200"
    ).aggregate(d=Sum("debit"), c=Sum("credit"))
    outstanding_ar = (ar_totals["d"] or ZERO) - (ar_totals["c"] or ZERO)

    # 5. Outstanding AP (Liability: Credit - Debit for code '2110')
    ap_totals = JournalLine.objects.filter(
        entry__status=JournalEntry.Status.POSTED,
        entry__date__lte=today,
        account__code="2110"
    ).aggregate(d=Sum("debit"), c=Sum("credit"))
    outstanding_ap = (ap_totals["c"] or ZERO) - (ap_totals["d"] or ZERO)

    # 6. GL Inventory Valuation (Asset: Debit - Credit for code '1300')
    gl_inv_totals = JournalLine.objects.filter(
        entry__status=JournalEntry.Status.POSTED,
        entry__date__lte=today,
        account__code="1300"
    ).aggregate(d=Sum("debit"), c=Sum("credit"))
    gl_inventory_val = (gl_inv_totals["d"] or ZERO) - (gl_inv_totals["c"] or ZERO)

    # 7. Operational Inventory Valuation
    products = Product.objects.filter(type=ProductType.STOCK, is_active=True).select_related("stock_on_hand")
    operational_inventory_val = ZERO
    for p in products:
        qty = p.stock_on_hand.qty if hasattr(p, "stock_on_hand") else ZERO
        operational_inventory_val += qty * p.cost

    # 8. Monthly Revenue & Expenses
    monthly_revenue = [ZERO] * 12
    monthly_expenses = [ZERO] * 12

    rev_by_month = JournalLine.objects.filter(
        entry__status=JournalEntry.Status.POSTED,
        entry__date__gte=start_date,
        entry__date__lte=end_date,
        account__type=AccountType.REVENUE
    ).values("entry__date__month").annotate(d=Sum("debit"), c=Sum("credit"))

    for item in rev_by_month:
        m = item["entry__date__month"]
        if m and 1 <= m <= 12:
            monthly_revenue[m - 1] = (item["c"] or ZERO) - (item["d"] or ZERO)

    exp_by_month = JournalLine.objects.filter(
        entry__status=JournalEntry.Status.POSTED,
        entry__date__gte=start_date,
        entry__date__lte=end_date,
        account__type=AccountType.EXPENSE
    ).values("entry__date__month").annotate(d=Sum("debit"), c=Sum("credit"))

    for item in exp_by_month:
        m = item["entry__date__month"]
        if m and 1 <= m <= 12:
            monthly_expenses[m - 1] = (item["d"] or ZERO) - (item["c"] or ZERO)

    monthly_revenue_float = [float(v) for v in monthly_revenue]
    monthly_expenses_float = [float(v) for v in monthly_expenses]

    # 9. Top 5 Products for Doughnut Chart
    product_valuations = []
    for p in products:
        qty = p.stock_on_hand.qty if hasattr(p, "stock_on_hand") else ZERO
        val = qty * p.cost
        if val > ZERO:
            product_valuations.append({
                "sku": p.sku,
                "name": p.name,
                "val": float(val)
            })

    product_valuations.sort(key=lambda x: x["val"], reverse=True)
    top_products = product_valuations[:5]
    other_sum = sum(x["val"] for x in product_valuations[5:])

    doughnut_labels = [f"{x['sku']} - {x['name']}" for x in top_products]
    doughnut_data = [x["val"] for x in top_products]

    if other_sum > 0:
        doughnut_labels.append("Other Items")
        doughnut_data.append(other_sum)

    return render(request, "core/dashboard.html", {
        "company": company,
        "ytd_revenue": ytd_revenue,
        "ytd_expenses": ytd_expenses,
        "net_profit": net_profit,
        "outstanding_ar": outstanding_ar,
        "outstanding_ap": outstanding_ap,
        "gl_inventory_val": gl_inventory_val,
        "operational_inventory_val": operational_inventory_val,
        "monthly_revenue_json": monthly_revenue_float,
        "monthly_expenses_json": monthly_expenses_float,
        "doughnut_labels_json": doughnut_labels,
        "doughnut_data_json": doughnut_data,
        "current_year": current_year,
    })


class CompanyForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = [
            "name", "legal_name", "email", "phone", "address", "tax_id",
            "fiscal_year_start_month", "fiscal_year_start_day",
        ]


@login_required
@write_required
def company_setup(request):
    company = Company.get()
    if request.method == "POST":
        form = CompanyForm(request.POST, instance=company)
        if form.is_valid():
            form.save()
            messages.success(request, "Company saved.")
            return redirect("company_setup")
    else:
        form = CompanyForm(instance=company)
    return render(request, "core/company_setup.html", {"form": form})


@login_required
def export_view(request):
    if request.user.role not in {Role.ADMIN, Role.MANAGER}:
        return HttpResponseForbidden("Only administrators and managers can export data.")

    from django.apps import apps
    from django.http import HttpResponse
    from django.core import serializers
    import csv
    import io
    import zipfile

    # Check if sensitive models are intentionally requested
    include_sensitive = request.GET.get("include_sensitive") == "true" or request.POST.get("include_sensitive") == "true"

    sensitive_models = {
        "core.user",
        "core.writeattempt",
        "core.copilotauditevent",
    }

    # Get all local models to export
    local_apps = ["core", "accounting", "inventory", "sales", "purchasing", "manufacturing"]
    
    # Gather models and their metadata
    exportable_models = []
    for model in apps.get_models():
        if model._meta.app_label in local_apps:
            # Exclude simple history models
            if model._meta.model_name.startswith("historical"):
                continue
            
            key = f"{model._meta.app_label}.{model._meta.model_name}"
            # Filter out sensitive models if not intentionally requested
            if key in sensitive_models and not include_sensitive:
                continue

            # Count current rows
            row_count = model.objects.count()
            
            exportable_models.append({
                "app_label": model._meta.app_label,
                "model_name": model._meta.model_name,
                "verbose_name": model._meta.verbose_name.capitalize(),
                "row_count": row_count,
                "key": key,
            })
            
    # Sort models by app label and model name
    exportable_models.sort(key=lambda x: (x["app_label"], x["model_name"]))

    if request.method == "POST":
        selected_keys = request.POST.getlist("selected_models")
        action = request.POST.get("action")
        
        # Filter models to export based on user selection
        models_to_export = []
        for model in apps.get_models():
            key = f"{model._meta.app_label}.{model._meta.model_name}"
            if key in selected_keys:
                if key in sensitive_models and not include_sensitive:
                    continue
                models_to_export.append(model)
                
        if not models_to_export:
            messages.error(request, "No models were selected for export.")
            return redirect("data_export")

        if action == "export_json":
            # Package all selected model instances into a single list
            objects = []
            for model in models_to_export:
                objects.extend(model.objects.all())
                
            # Serialize the objects to JSON
            serialized_data = serializers.serialize("json", objects, indent=2)
            
            response = HttpResponse(serialized_data, content_type="application/json")
            response["Content-Disposition"] = f'attachment; filename="derp_backup_{timezone.localdate()}.json"'
            return response
            
        elif action == "export_csv":
            # Package each selected model as a CSV inside a ZIP archive
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                for model in models_to_export:
                    csv_buffer = io.StringIO()
                    writer = csv.writer(csv_buffer)
                    
                    # Fields header
                    fields = [field.name for field in model._meta.fields]
                    writer.writerow(fields)
                    
                    # Records data
                    for obj in model.objects.all():
                        row = []
                        for field in fields:
                            val = getattr(obj, field)
                            if val is None:
                                row.append("")
                            elif hasattr(val, "pk"): # ForeignKey relation
                                row.append(val.pk)
                            else:
                                row.append(str(val))
                        writer.writerow(row)
                        
                    file_name = f"{model._meta.app_label}_{model._meta.model_name}.csv"
                    zip_file.writestr(file_name, csv_buffer.getvalue())
                    
            response = HttpResponse(zip_buffer.getvalue(), content_type="application/zip")
            response["Content-Disposition"] = f'attachment; filename="derp_csv_export_{timezone.localdate()}.zip"'
            return response

    return render(request, "core/export.html", {
        "exportable_models": exportable_models,
        "company": Company.get(),
        "include_sensitive": include_sensitive,
    })


@login_required
def import_view(request):
    from django.apps import apps
    from django.db import transaction
    from django.core.exceptions import ValidationError
    from django.core import serializers
    import csv
    import io

    if request.user.role != Role.ADMIN:
        return HttpResponseForbidden("Only administrators can import data.")

    IMPORTABLE_MODELS = {
        "accounting.account": "GL Accounts",
        "inventory.product": "Products",
        "sales.customer": "Customers",
        "purchasing.vendor": "Vendors",
    }

    if request.method == "POST":
        # 1. Handle JSON Backup Restoration
        if "json_file" in request.FILES:
            json_file = request.FILES["json_file"]
            try:
                data = json_file.read()
                # Deserialize and validate structure before applying transaction.
                deserialized_objects = list(serializers.deserialize("json", data))
                blocked_models = sorted({
                    obj.object._meta.label_lower
                    for obj in deserialized_objects
                    if obj.object._meta.label_lower not in IMPORTABLE_MODELS
                })
                if blocked_models:
                    raise ValidationError(
                        "JSON restore includes unsupported model(s): "
                        + ", ".join(blocked_models)
                    )
                
                with transaction.atomic():
                    count = 0
                    for obj in deserialized_objects:
                        obj.save()
                        count += 1
                        
                messages.success(request, f"Backup Restoration Succeeded: Restored {count} records.")
                return redirect("data_import")
            except Exception as e:
                messages.error(request, f"Backup Restoration Failed (All changes rolled back): {str(e)}")
                return redirect("data_import")

        # 2. Handle CSV Data Ingestion
        elif "csv_file" in request.FILES:
            csv_file = request.FILES["csv_file"]
            model_key = request.POST.get("model_key")
            
            if model_key not in IMPORTABLE_MODELS:
                messages.error(request, "Invalid or unsupported target model selected.")
                return redirect("data_import")
                
            try:
                model = apps.get_model(model_key)
                
                # Ingest CSV file with BOM preservation
                decoded_file = csv_file.read().decode("utf-8-sig")
                io_string = io.StringIO(decoded_file)
                reader = csv.reader(io_string)
                
                headers = next(reader)
                headers = [h.strip().lower() for h in headers]
                
                model_fields = {field.name.lower(): field for field in model._meta.fields}
                valid_headers = [h for h in headers if h in model_fields]
                
                if not valid_headers:
                    raise ValidationError("No matching model fields found in CSV headers. Make sure column names match exact database field names.")
                
                with transaction.atomic():
                    created_count = 0
                    updated_count = 0
                    for row_idx, row in enumerate(reader):
                        if not row or not any(row):
                            continue
                        
                        data = {}
                        for col_idx, col_val in enumerate(row):
                            if col_idx >= len(headers):
                                continue
                            header = headers[col_idx]
                            if header in model_fields:
                                field = model_fields[header]
                                col_val_str = col_val.strip()
                                
                                if col_val_str == "":
                                    if field.null:
                                        data[field.name] = None
                                    elif field.blank:
                                        data[field.name] = ""
                                    else:
                                        if field.has_default():
                                            data[field.name] = field.get_default()
                                        else:
                                            raise ValidationError(f"Row {row_idx + 2}: Field '{field.name}' cannot be blank.")
                                elif field.is_relation:
                                    target_model = field.related_model
                                    try:
                                        data[field.name] = target_model.objects.get(pk=col_val_str)
                                    except target_model.DoesNotExist:
                                        raise ValidationError(f"Row {row_idx + 2}: Related '{field.name}' with ID '{col_val_str}' does not exist.")
                                elif field.get_internal_type() in ["BooleanField", "NullBooleanField"]:
                                    data[field.name] = col_val_str.lower() in ["true", "1", "yes", "t", "y"]
                                else:
                                    data[field.name] = col_val_str
                                    
                        # Ingest/Update
                        pk_field_name = model._meta.pk.name
                        pk_val = data.get(pk_field_name)
                        if pk_val:
                            try:
                                instance = model.objects.get(pk=pk_val)
                                for k, v in data.items():
                                    setattr(instance, k, v)
                                instance.save()
                                updated_count += 1
                            except model.DoesNotExist:
                                model.objects.create(**data)
                                created_count += 1
                        else:
                            model.objects.create(**data)
                            created_count += 1
                            
                messages.success(request, f"CSV Ingestion Succeeded: Imported {created_count} new records and updated {updated_count} existing records.")
                return redirect("data_import")
            except Exception as e:
                messages.error(request, f"CSV Ingestion Failed (All changes rolled back): {str(e)}")
                return redirect("data_import")

    # Prepare model selections for dropdown
    model_choices = [{"key": k, "label": v} for k, v in IMPORTABLE_MODELS.items()]
    
    return render(request, "core/import.html", {
        "model_choices": model_choices,
        "company": Company.get(),
    })


@login_required
def search_view(request):
    """
    Global search across all major models.
    GET ?q=<query> — returns grouped results with up to 10 per category.
    """
    from sales.models import Customer, SalesOrder, Invoice
    from purchasing.models import Vendor, PurchaseOrder, Bill
    from manufacturing.models import ManufacturingOrder

    q = request.GET.get("q", "").strip()
    results = []
    total_count = 0

    if q:
        # Customers
        customers = Customer.objects.filter(
            Q(name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q)
        )[:10]
        if customers:
            results.append({
                "category": "Customers",
                "icon": "👤",
                "url_name": "customer_detail",
                "items": [{"pk": c.pk, "label": c.name, "sub": c.email or c.phone} for c in customers],
            })
            total_count += len(customers)

        # Vendors
        vendors = Vendor.objects.filter(
            Q(name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q)
        )[:10]
        if vendors:
            results.append({
                "category": "Vendors",
                "icon": "🏢",
                "url_name": "vendor_detail",
                "items": [{"pk": v.pk, "label": v.name, "sub": v.email or v.phone} for v in vendors],
            })
            total_count += len(vendors)

        # Products
        products = Product.objects.filter(
            Q(sku__icontains=q) | Q(name__icontains=q) | Q(description__icontains=q)
        )[:10]
        if products:
            results.append({
                "category": "Products",
                "icon": "📦",
                "url_name": "product_detail",
                "items": [{"pk": p.pk, "label": f"{p.sku} — {p.name}", "sub": p.get_type_display()} for p in products],
            })
            total_count += len(products)

        # Sales Orders
        sales_orders = SalesOrder.objects.filter(
            Q(number__icontains=q) | Q(customer__name__icontains=q)
        ).select_related("customer")[:10]
        if sales_orders:
            results.append({
                "category": "Sales Orders",
                "icon": "🛒",
                "url_name": "sales_order_detail",
                "items": [
                    {"pk": o.pk, "label": o.number or f"DRAFT-{o.pk}", "sub": o.customer.name}
                    for o in sales_orders
                ],
            })
            total_count += len(sales_orders)

        # Invoices
        invoices = Invoice.objects.filter(
            Q(number__icontains=q) | Q(customer__name__icontains=q)
        ).select_related("customer")[:10]
        if invoices:
            results.append({
                "category": "Invoices",
                "icon": "🧾",
                "url_name": "invoice_detail",
                "items": [
                    {"pk": i.pk, "label": i.number or f"DRAFT-{i.pk}", "sub": i.customer.name}
                    for i in invoices
                ],
            })
            total_count += len(invoices)

        # Purchase Orders
        purchase_orders = PurchaseOrder.objects.filter(
            Q(number__icontains=q) | Q(vendor__name__icontains=q)
        ).select_related("vendor")[:10]
        if purchase_orders:
            results.append({
                "category": "Purchase Orders",
                "icon": "📋",
                "url_name": "purchase_order_detail",
                "items": [
                    {"pk": o.pk, "label": o.number or f"DRAFT-{o.pk}", "sub": o.vendor.name}
                    for o in purchase_orders
                ],
            })
            total_count += len(purchase_orders)

        # Bills
        bills = Bill.objects.filter(
            Q(number__icontains=q) | Q(vendor__name__icontains=q) | Q(vendor_ref__icontains=q)
        ).select_related("vendor")[:10]
        if bills:
            results.append({
                "category": "Bills",
                "icon": "💳",
                "url_name": "bill_detail",
                "items": [
                    {"pk": b.pk, "label": b.number or f"DRAFT-{b.pk}", "sub": b.vendor.name}
                    for b in bills
                ],
            })
            total_count += len(bills)

        # Manufacturing Orders
        mos = ManufacturingOrder.objects.filter(
            Q(number__icontains=q) | Q(product__name__icontains=q) | Q(product__sku__icontains=q)
        ).select_related("product")[:10]
        if mos:
            results.append({
                "category": "Manufacturing Orders",
                "icon": "🏭",
                "url_name": "mo_detail",
                "items": [
                    {"pk": m.pk, "label": m.number or f"MO-DRAFT-{m.pk}", "sub": m.product.name}
                    for m in mos
                ],
            })
            total_count += len(mos)

        # Journal Entries
        journal_entries = JournalEntry.objects.filter(
            Q(number__icontains=q) | Q(memo__icontains=q)
        )[:10]
        if journal_entries:
            results.append({
                "category": "Journal Entries",
                "icon": "📒",
                "url_name": "journal_detail",
                "items": [
                    {"pk": e.pk, "label": e.number or f"JE-{e.pk}", "sub": e.memo or ""}
                    for e in journal_entries
                ],
            })
            total_count += len(journal_entries)

    return render(request, "core/search.html", {
        "q": q,
        "results": results,
        "total_count": total_count,
    })


from django.contrib.auth import get_user_model
User = get_user_model()

class UserCreateForm(forms.ModelForm):
    email = forms.EmailField(required=True)
    password = forms.CharField(widget=forms.PasswordInput(), required=True)

    class Meta:
        model = User
        fields = ["username", "email", "role", "is_active", "password"]

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
        return user

class UserEditForm(forms.ModelForm):
    email = forms.EmailField(required=True)
    new_password = forms.CharField(widget=forms.PasswordInput(), required=False, label="Change Password", help_text="Leave blank to keep existing password.")

    class Meta:
        model = User
        fields = ["email", "role", "is_active"]

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("new_password")
        if password:
            user.set_password(password)
        if commit:
            user.save()
        return user


@login_required
def user_list(request):
    if request.user.role != Role.ADMIN:
        return HttpResponseForbidden("Only administrators can manage users.")

    users = User.objects.all().order_by("username")
    return render(request, "core/user_list.html", {
        "users": users,
        "company": Company.get(),
    })


@login_required
def user_create(request):
    if request.user.role != Role.ADMIN:
        return HttpResponseForbidden("Only administrators can manage users.")

    if request.method == "POST":
        form = UserCreateForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "User account created successfully.")
            return redirect("user_list")
    else:
        form = UserCreateForm()

    return render(request, "core/user_form.html", {
        "form": form,
        "is_create": True,
        "company": Company.get(),
    })


@login_required
def user_edit(request, pk):
    from django.shortcuts import get_object_or_404
    if request.user.role != Role.ADMIN:
        return HttpResponseForbidden("Only administrators can manage users.")

    user = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        form = UserEditForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            messages.success(request, f"User '{user.username}' updated successfully.")
            return redirect("user_list")
    else:
        form = UserEditForm(instance=user)

    return render(request, "core/user_form.html", {
        "form": form,
        "is_create": False,
        "target_user": user,
        "company": Company.get(),
    })


from .models import PublicPage

class PageForm(forms.ModelForm):
    class Meta:
        model = PublicPage
        fields = ["title", "slug", "html_content", "is_homepage", "is_published"]
        widgets = {
            "html_content": forms.Textarea(attrs={"style": "font-family: monospace; font-size: 13px; height: 350px;"}),
        }


# --- Public Site Views ---

def public_home(request):
    try:
        # Auto-create a default template website with a few pages if database is empty
        if PublicPage.objects.count() == 0:
            PublicPage.objects.create(
                title="Leading the Future of Modern Business",
                slug="home",
                html_content="""<section style="text-align: center; padding: 40px 0;">
  <h1>Leading the Future of Modern Business</h1>
  <p>We provide state-of-the-art enterprise resource planning, logistics management, and financial analytics solutions tailored for your business growth.</p>
  <div style="margin-top: 30px;">
    <a href="/p/about-us/" class="btn" style="padding: 12px 24px; font-size: 16px; margin-right: 12px; border-radius: 8px;">Learn About Us</a>
    <a href="/p/contact/" class="btn secondary" style="padding: 12px 24px; font-size: 16px; border: 1px solid var(--line); border-radius: 8px;">Get in Touch</a>
  </div>
</section>

<section style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 24px; margin-top: 60px;">
  <div style="background: var(--surface); padding: 24px; border: 1px solid var(--line); border-radius: 12px; box-shadow: var(--shadow);">
    <span style="font-size: 32px;">📊</span>
    <h3>Financial Intelligence</h3>
    <p style="font-size: 14px; margin: 10px 0 0;">Gain absolute control over your general ledger, income statements, and balance sheets in real time.</p>
  </div>
  <div style="background: var(--surface); padding: 24px; border: 1px solid var(--line); border-radius: 12px; box-shadow: var(--shadow);">
    <span style="font-size: 32px;">📦</span>
    <h3>Inventory & Operations</h3>
    <p style="font-size: 14px; margin: 10px 0 0;">Automate warehouse logistics, lot tracking, transfers, and real-time stock valuation reporting.</p>
  </div>
  <div style="background: var(--surface); padding: 24px; border: 1px solid var(--line); border-radius: 12px; box-shadow: var(--shadow);">
    <span style="font-size: 32px;">🚀</span>
    <h3>Production & Manufacturing</h3>
    <p style="font-size: 14px; margin: 10px 0 0;">Streamline your bill of materials, schedule production orders, and optimize shop floor execution.</p>
  </div>
</section>""",
                is_homepage=True,
                is_published=True
            )
            PublicPage.objects.create(
                title="About Us",
                slug="about-us",
                html_content="""<section style="max-width: 800px; margin: 0 auto;">
  <h1>About Our Company</h1>
  <p>Founded on a vision of absolute operational efficiency, we have spent the past decade pioneering integrated software solutions that connect every department—from accounting to the manufacturing floor.</p>
  
  <h2>Our Mission</h2>
  <p>To empower businesses around the globe with real-time analytics, automated workflows, and robust financial tracking systems that remove friction and drive sustainable growth.</p>
  
  <h2>Our Core Values</h2>
  <ul>
    <li><strong>Relational Integrity</strong>: Absolute accuracy in transaction records and relational data.</li>
    <li><strong>Innovation</strong>: Modern tools and AI-assisted copiloting to accelerate business execution.</li>
    <li><strong>Operational Excellence</strong>: Seamless coordination between warehouses, suppliers, and buyers.</li>
  </ul>
</section>""",
                is_homepage=False,
                is_published=True
            )
            PublicPage.objects.create(
                title="Contact Us",
                slug="contact",
                html_content="""<section style="max-width: 600px; margin: 0 auto;">
  <h1>Contact Us</h1>
  <p>Have questions about our enterprise solutions or want to request a custom demonstration? We would love to hear from you!</p>
  
  <div style="background: var(--surface); padding: 24px; border: 1px solid var(--line); border-radius: 12px; box-shadow: var(--shadow); margin-top: 30px;">
    <h2>Send a Message</h2>
    <form onsubmit="event.preventDefault(); alert('Thank you! Your message has been submitted. A sales associate will contact you shortly.'); this.reset();" style="display: grid; gap: 16px; margin-top: 20px;">
      <div>
        <label style="display: block; font-weight: bold; margin-bottom: 6px; font-size: 14px;">Your Name</label>
        <input type="text" required style="width: 100%; padding: 10px; border: 1px solid var(--line); border-radius: 8px;">
      </div>
      <div>
        <label style="display: block; font-weight: bold; margin-bottom: 6px; font-size: 14px;">Email Address</label>
        <input type="email" required style="width: 100%; padding: 10px; border: 1px solid var(--line); border-radius: 8px;">
      </div>
      <div>
        <label style="display: block; font-weight: bold; margin-bottom: 6px; font-size: 14px;">Message</label>
        <textarea required style="width: 100%; padding: 10px; border: 1px solid var(--line); border-radius: 8px; min-height: 120px;"></textarea>
      </div>
      <button type="submit" class="btn" style="width: 100%; padding: 12px; border: 0; cursor: pointer; border-radius: 8px;">Send Message</button>
    </form>
  </div>
</section>""",
                is_homepage=False,
                is_published=True
            )
        page = PublicPage.objects.filter(is_homepage=True, is_published=True).first()
    except Exception:
        page = None

    public_pages = PublicPage.objects.filter(is_published=True).exclude(is_homepage=True).order_by("title")

    if page:
        return render(request, "public_page.html", {
            "page": page,
            "public_pages": public_pages,
        })
    
    # Fallback default welcome page
    return render(request, "public_page.html", {
        "page": {
            "title": "Welcome to your new public website!",
            "html_content": "<h1>Welcome!</h1><p>This is your public-facing homepage. You can configure and edit this page by logging into your <a href='/derp/'>ERP Workspace</a> and visiting the <strong>Website Editor</strong>.</p>"
        },
        "public_pages": public_pages,
    })


def public_page(request, slug):
    from django.shortcuts import get_object_or_404
    page = get_object_or_404(PublicPage, slug=slug, is_published=True)
    public_pages = PublicPage.objects.filter(is_published=True).exclude(is_homepage=True).order_by("title")
    return render(request, "public_page.html", {
        "page": page,
        "public_pages": public_pages,
    })


# --- Administrative Website Editor Views ---

@login_required
def website_editor(request):
    if request.user.role not in {Role.ADMIN, Role.MANAGER}:
        return HttpResponseForbidden("Only administrators and managers can access the Website Editor.")

    pages = PublicPage.objects.all()
    return render(request, "core/website_editor.html", {
        "pages": pages,
        "company": Company.get(),
    })


@login_required
def page_create(request):
    if request.user.role not in {Role.ADMIN, Role.MANAGER}:
        return HttpResponseForbidden("Only administrators and managers can modify public pages.")

    if request.method == "POST":
        form = PageForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Public page created successfully.")
            return redirect("website_editor")
    else:
        form = PageForm()

    return render(request, "core/page_form.html", {
        "form": form,
        "is_create": True,
        "company": Company.get(),
    })


@login_required
def page_edit(request, pk):
    from django.shortcuts import get_object_or_404
    if request.user.role not in {Role.ADMIN, Role.MANAGER}:
        return HttpResponseForbidden("Only administrators and managers can modify public pages.")

    page = get_object_or_404(PublicPage, pk=pk)
    if request.method == "POST":
        form = PageForm(request.POST, instance=page)
        if form.is_valid():
            form.save()
            messages.success(request, f"Page '{page.title}' updated successfully.")
            return redirect("website_editor")
    else:
        form = PageForm(instance=page)

    return render(request, "core/page_form.html", {
        "form": form,
        "is_create": False,
        "page_instance": page,
        "company": Company.get(),
    })


@login_required
def page_delete(request, pk):
    from django.shortcuts import get_object_or_404
    if request.user.role not in {Role.ADMIN, Role.MANAGER}:
        return HttpResponseForbidden("Only administrators and managers can delete public pages.")

    page = get_object_or_404(PublicPage, pk=pk)
    title = page.title
    
    if page.is_homepage:
        messages.error(request, "Cannot delete the active Homepage. Please designate another page as the homepage first.")
        return redirect("website_editor")
        
    if request.method == "POST":
        page.delete()
        messages.success(request, f"Page '{title}' deleted successfully.")
        return redirect("website_editor")

    return render(request, "core/page_confirm_delete.html", {
        "page_instance": page,
        "company": Company.get(),
    })
