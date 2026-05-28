import os
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounting.management.commands.seed_chart_of_accounts import DEFAULT_COA
from accounting.models import Account, Payment
from accounting.services import LineSpec, post_transaction
from core.models import Company
from inventory.models import Location, Product, ProductType, StockMovement
from inventory.services import post_stock_movement
from manufacturing.models import BOMComponent, BillOfMaterials, ManufacturingOrder
from manufacturing.services import complete_manufacturing_order, confirm_manufacturing_order
from purchasing.models import Bill, PurchaseOrder, PurchaseOrderLine, Vendor
from purchasing.services import (
    create_bill_from_receipt,
    issue_purchase_order,
    post_bill,
    receive_purchase_order,
)
from sales.models import Customer, Invoice, InvoiceLine, SalesOrder, SalesOrderLine
from sales.services import confirm_sales_order, post_invoice, receive_payment


def _enabled(value: str | None) -> bool:
    return (value or "true").strip().lower() not in {"0", "false", "no", "off"}


class Command(BaseCommand):
    help = "Seed a fresh installation with demo ERP data when business tables are empty."

    def handle(self, *args, **options):
        if not _enabled(os.environ.get("DERP_SEED_DEMO_DATA")):
            self.stdout.write(self.style.WARNING("Demo data seeding is disabled."))
            return

        if not self._business_data_is_empty():
            self.stdout.write(self.style.SUCCESS("Demo data skipped; ERP business data already exists."))
            return

        with transaction.atomic():
            created_accounts = self._ensure_chart_of_accounts()
            summary = self._seed_demo_data()

        self.stdout.write(self.style.SUCCESS(f"Seeded demo ERP data ({created_accounts} accounts created)."))
        self.stdout.write(
            "Created: "
            + ", ".join(f"{label}={count}" for label, count in summary.items())
        )

    def _business_data_is_empty(self) -> bool:
        checks = [
            Product,
            Location,
            Customer,
            Vendor,
            PurchaseOrder,
            Bill,
            SalesOrder,
            Invoice,
            BillOfMaterials,
            ManufacturingOrder,
            StockMovement,
            Payment,
        ]
        return not any(model.objects.exists() for model in checks)

    def _ensure_chart_of_accounts(self) -> int:
        created = 0
        code_to_account = {account.code: account for account in Account.objects.all()}

        for code, name, account_type, parent_code, is_postable in DEFAULT_COA:
            if code in code_to_account:
                continue
            parent = code_to_account.get(parent_code) if parent_code else None
            account = Account.objects.create(
                code=code,
                name=name,
                type=account_type,
                parent=parent,
                is_postable=is_postable,
            )
            code_to_account[code] = account
            created += 1

        return created

    def _seed_demo_data(self) -> dict[str, int]:
        today = timezone.localdate()
        user = get_user_model().objects.filter(is_superuser=True).order_by("id").first()

        company = Company.get()
        company.name = "Northwind Demo Manufacturing"
        company.legal_name = "Northwind Demo Manufacturing LLC"
        company.email = "hello@northwind-demo.example"
        company.phone = "555-0100"
        company.address = "100 Demo Way\nSpringfield, IL 62701"
        company.save()

        cash = Account.objects.get(code="1110")
        inventory = Account.objects.get(code="1300")
        owner_capital = Account.objects.get(code="3100")
        product_sales = Account.objects.get(code="4100")
        service_revenue = Account.objects.get(code="4200")
        materials = Account.objects.get(code="5100")
        subscriptions = Account.objects.get(code="6400")

        main_location = Location.objects.create(
            name="Main Warehouse",
            description="Primary receiving, storage, and production location.",
        )
        showroom = Location.objects.create(
            name="Showroom",
            description="Finished goods display and customer pickup area.",
        )

        vendor = Vendor.objects.create(
            name="Midwest Components Co.",
            email="orders@midwest-components.example",
            phone="555-0115",
            address="22 Supplier Park\nPeoria, IL 61602",
            default_expense_account=materials,
            notes="Demo vendor for raw materials and stock replenishment.",
        )
        software_vendor = Vendor.objects.create(
            name="LedgerCloud Apps",
            email="billing@ledgercloud.example",
            default_expense_account=subscriptions,
            notes="Demo vendor for operating expenses.",
        )

        customer = Customer.objects.create(
            name="Acme Solar Cooperative",
            email="ap@acme-solar.example",
            phone="555-0198",
            billing_address="450 Market Street\nMadison, WI 53703",
            shipping_address="450 Market Street\nMadison, WI 53703",
            tax_rate=Decimal("7.500"),
            default_revenue_account=product_sales,
            notes="Demo customer with open sales and payment history.",
        )
        walk_in = Customer.objects.create(
            name="Walk-in Retail",
            email="retail@example.com",
            payment_terms_days=0,
            tax_rate=Decimal("7.500"),
            default_revenue_account=product_sales,
        )

        aluminum = Product.objects.create(
            sku="RM-ALUM",
            name="Aluminum Sheet",
            description="Raw aluminum sheet used in utility cart production.",
            type=ProductType.STOCK,
            uom="sheet",
            cost=Decimal("12.00"),
            price=Decimal("0.00"),
            low_stock_threshold=Decimal("20.0000"),
            default_expense_account=inventory,
            is_sellable=False,
            is_manufacturable=False,
        )
        fasteners = Product.objects.create(
            sku="RM-BOLT",
            name="Fastener Kit",
            description="Hardware pack consumed by assemblies.",
            type=ProductType.STOCK,
            uom="kit",
            cost=Decimal("2.00"),
            price=Decimal("0.00"),
            low_stock_threshold=Decimal("80.0000"),
            default_expense_account=inventory,
            is_sellable=False,
            is_manufacturable=False,
        )
        cart = Product.objects.create(
            sku="FG-CART",
            name="Utility Cart",
            description="Finished utility cart assembled from sheet metal and fasteners.",
            type=ProductType.STOCK,
            uom="ea",
            cost=Decimal("28.00"),
            price=Decimal("189.00"),
            low_stock_threshold=Decimal("5.0000"),
            default_revenue_account=product_sales,
            default_expense_account=inventory,
            is_purchasable=False,
        )
        install = Product.objects.create(
            sku="SVC-INSTALL",
            name="On-site Installation",
            description="Installation and setup service.",
            type=ProductType.SERVICE,
            uom="hr",
            price=Decimal("95.00"),
            default_revenue_account=service_revenue,
            default_expense_account=materials,
            is_purchasable=False,
            is_manufacturable=False,
        )

        post_transaction(
            date=today,
            memo="Demo opening cash balance",
            lines=[
                LineSpec(account_code=cash.code, debit=Decimal("10000.00"), memo="Opening bank balance"),
                LineSpec(account_code=owner_capital.code, credit=Decimal("10000.00"), memo="Owner contribution"),
            ],
            user=user,
            source_doc_type="DemoSeed",
        )

        purchase_order = PurchaseOrder.objects.create(
            vendor=vendor,
            date=today,
            expected_date=today,
            notes="Demo raw material replenishment order.",
            created_by=user,
        )
        PurchaseOrderLine.objects.create(
            order=purchase_order,
            product=aluminum,
            description="Aluminum Sheet",
            qty=Decimal("100.0000"),
            unit_cost=Decimal("12.00"),
            expense_account=inventory,
        )
        PurchaseOrderLine.objects.create(
            order=purchase_order,
            product=fasteners,
            description="Fastener Kit",
            qty=Decimal("500.0000"),
            unit_cost=Decimal("2.00"),
            expense_account=inventory,
        )
        issue_purchase_order(purchase_order, user=user)
        receipt = receive_purchase_order(
            order=purchase_order,
            date=today,
            receipts=[(line, line.qty, main_location) for line in purchase_order.lines.all()],
            notes="Demo receipt for raw materials.",
            user=user,
        )
        bill = create_bill_from_receipt(receipt, user=user)
        bill.vendor_ref = "MC-1001"
        bill.save(update_fields=["vendor_ref"])
        post_bill(bill, user=user)

        expense_bill = Bill.objects.create(
            vendor=software_vendor,
            date=today,
            due_date=today,
            vendor_ref="LC-2026-001",
            notes="Demo monthly software subscription.",
            created_by=user,
        )
        expense_bill.lines.create(
            description="ERP hosting and productivity apps",
            qty=Decimal("1.0000"),
            unit_cost=Decimal("149.00"),
            expense_account=subscriptions,
        )
        post_bill(expense_bill, user=user)

        bom = BillOfMaterials.objects.create(
            product=cart,
            name="Utility Cart Assembly",
            created_by=user,
        )
        BOMComponent.objects.create(bom=bom, product=aluminum, qty=Decimal("1.0000"))
        BOMComponent.objects.create(bom=bom, product=fasteners, qty=Decimal("8.0000"))

        mo = ManufacturingOrder.objects.create(
            product=cart,
            bom=bom,
            qty_target=Decimal("15.0000"),
            date_planned=today,
            production_location=main_location,
            created_by=user,
        )
        confirm_manufacturing_order(mo, user)
        complete_manufacturing_order(mo, user)

        post_stock_movement(
            product=cart,
            movement_type=StockMovement.MovementType.TRANSFER,
            qty=Decimal("2.0000"),
            location=main_location,
            to_location=showroom,
            ref_doc_type="DemoSeed",
            memo="Demo showroom stocking transfer.",
            user=user,
        )

        sales_order = SalesOrder.objects.create(
            customer=customer,
            date=today,
            requested_date=today,
            notes="Demo order for finished carts and setup services.",
            created_by=user,
        )
        SalesOrderLine.objects.create(
            order=sales_order,
            product=cart,
            description="Utility Cart",
            qty=Decimal("3.0000"),
            unit_price=Decimal("189.00"),
            revenue_account=product_sales,
        )
        SalesOrderLine.objects.create(
            order=sales_order,
            product=install,
            description="On-site Installation",
            qty=Decimal("3.0000"),
            unit_price=Decimal("95.00"),
            revenue_account=service_revenue,
        )
        confirm_sales_order(sales_order, user=user)
        invoice = sales_order.invoices.get()
        post_invoice(invoice, user=user)
        receive_payment(
            customer=customer,
            date=today,
            amount=Decimal("500.00"),
            cash_account=cash,
            method=Payment.Method.ACH,
            reference="ACH-DEMO-001",
            applications=[(invoice, Decimal("500.00"))],
            notes="Demo partial customer payment.",
            user=user,
        )

        draft_invoice = Invoice.objects.create(
            customer=walk_in,
            date=today,
            due_date=today,
            tax_rate=walk_in.tax_rate,
            notes="Demo draft invoice awaiting posting.",
            created_by=user,
        )
        InvoiceLine.objects.create(
            invoice=draft_invoice,
            product=install,
            description="Quick consultation",
            qty=Decimal("1.0000"),
            unit_price=Decimal("95.00"),
            revenue_account=service_revenue,
        )

        return {
            "locations": 2,
            "vendors": 2,
            "customers": 2,
            "products": 4,
            "purchase_orders": 1,
            "bills": 2,
            "bom": 1,
            "manufacturing_orders": 1,
            "sales_orders": 1,
            "invoices": 2,
            "payments": 1,
        }
