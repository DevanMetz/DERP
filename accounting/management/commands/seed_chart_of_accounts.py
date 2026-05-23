"""
Seed a sensible default chart of accounts for a US small business.

This is opinionated but conventional. Codes use the standard 1000/2000/3000
buckets so anyone familiar with QuickBooks/Xero defaults will recognize them.

Run: python manage.py seed_chart_of_accounts
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from accounting.models import Account, AccountType

# (code, name, type, parent_code or None, is_postable)
DEFAULT_COA = [
    # ------------------------------ Assets ------------------------------
    ("1000", "Assets",                       AccountType.ASSET, None,   False),
    ("1100", "Current Assets",               AccountType.ASSET, "1000", False),
    ("1110", "Cash - Operating",             AccountType.ASSET, "1100", True),
    ("1120", "Cash - Savings",               AccountType.ASSET, "1100", True),
    ("1200", "Accounts Receivable",          AccountType.ASSET, "1100", True),
    ("1300", "Inventory",                    AccountType.ASSET, "1100", True),
    ("1400", "Prepaid Expenses",             AccountType.ASSET, "1100", True),
    ("1500", "Fixed Assets",                 AccountType.ASSET, "1000", False),
    ("1510", "Equipment",                    AccountType.ASSET, "1500", True),
    ("1520", "Accumulated Depreciation",     AccountType.ASSET, "1500", True),

    # ---------------------------- Liabilities ---------------------------
    ("2000", "Liabilities",                  AccountType.LIABILITY, None,   False),
    ("2100", "Current Liabilities",          AccountType.LIABILITY, "2000", False),
    ("2110", "Accounts Payable",             AccountType.LIABILITY, "2100", True),
    ("2120", "Sales Tax Payable",            AccountType.LIABILITY, "2100", True),
    ("2130", "Accrued Expenses",             AccountType.LIABILITY, "2100", True),
    ("2200", "Long-Term Liabilities",        AccountType.LIABILITY, "2000", False),
    ("2210", "Loans Payable",                AccountType.LIABILITY, "2200", True),

    # ------------------------------ Equity ------------------------------
    ("3000", "Equity",                       AccountType.EQUITY, None,   False),
    ("3100", "Owner's Capital",              AccountType.EQUITY, "3000", True),
    ("3200", "Retained Earnings",            AccountType.EQUITY, "3000", True),
    ("3300", "Owner's Draws",                AccountType.EQUITY, "3000", True),

    # ------------------------------ Revenue -----------------------------
    ("4000", "Revenue",                      AccountType.REVENUE, None,   False),
    ("4100", "Product Sales",                AccountType.REVENUE, "4000", True),
    ("4200", "Service Revenue",              AccountType.REVENUE, "4000", True),
    ("4900", "Sales Discounts",              AccountType.REVENUE, "4000", True),

    # ------------------------------ Expenses ----------------------------
    ("5000", "Cost of Goods Sold",           AccountType.EXPENSE, None,   False),
    ("5100", "COGS - Materials",             AccountType.EXPENSE, "5000", True),
    ("5200", "COGS - Labor",                 AccountType.EXPENSE, "5000", True),
    ("5300", "COGS - Overhead",              AccountType.EXPENSE, "5000", True),

    ("6000", "Operating Expenses",           AccountType.EXPENSE, None,   False),
    ("6100", "Rent",                         AccountType.EXPENSE, "6000", True),
    ("6200", "Utilities",                    AccountType.EXPENSE, "6000", True),
    ("6300", "Office Supplies",              AccountType.EXPENSE, "6000", True),
    ("6400", "Software & Subscriptions",     AccountType.EXPENSE, "6000", True),
    ("6500", "Professional Services",        AccountType.EXPENSE, "6000", True),
    ("6600", "Bank Fees",                    AccountType.EXPENSE, "6000", True),
    ("6700", "Depreciation Expense",         AccountType.EXPENSE, "6000", True),
    ("6800", "Insurance",                    AccountType.EXPENSE, "6000", True),
    ("6900", "Miscellaneous",                AccountType.EXPENSE, "6000", True),
]


class Command(BaseCommand):
    help = "Seed the chart of accounts with a sensible default for a US small business."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-seed even if accounts already exist (will skip existing codes).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        existing_count = Account.objects.count()
        if existing_count and not options["force"]:
            self.stdout.write(self.style.WARNING(
                f"Chart of accounts already has {existing_count} accounts. "
                f"Use --force to add missing defaults without touching existing rows."
            ))
            return

        created = 0
        skipped = 0
        # Two passes so parents exist before children reference them.
        # Within our list this is already topologically ordered, but be explicit.
        code_to_account = {a.code: a for a in Account.objects.all()}

        for code, name, atype, parent_code, postable in DEFAULT_COA:
            if code in code_to_account:
                skipped += 1
                continue
            parent = code_to_account.get(parent_code) if parent_code else None
            acct = Account.objects.create(
                code=code, name=name, type=atype, parent=parent,
                is_postable=postable,
            )
            code_to_account[code] = acct
            created += 1

        self.stdout.write(self.style.SUCCESS(
            f"Seeded chart of accounts: {created} created, {skipped} already existed."
        ))
