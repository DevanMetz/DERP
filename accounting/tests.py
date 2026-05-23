"""
Tests for the posting layer.

These are the most important tests in the codebase. If any of them fail,
the financial integrity of the entire ERP is suspect. Run them on every
commit.

Run: python manage.py test accounting
"""

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from core.test_utils import DERPTenantTestCase as TestCase

from accounting.models import Account, AccountType, JournalEntry, JournalLine
from accounting.reports import balance_sheet, income_statement
from accounting.services import LineSpec, post_transaction, reverse_entry


D = Decimal


def make_accounts():
    cash = Account.objects.create(code="1110", name="Cash", type=AccountType.ASSET)
    revenue = Account.objects.create(code="4100", name="Sales", type=AccountType.REVENUE)
    expense = Account.objects.create(code="6100", name="Rent", type=AccountType.EXPENSE)
    return cash, revenue, expense


class LineSpecTests(TestCase):
    def test_rejects_both_sides(self):
        with self.assertRaises(ValueError):
            LineSpec(account_code="1110", debit=D("10"), credit=D("10"))

    def test_rejects_neither_side(self):
        with self.assertRaises(ValueError):
            LineSpec(account_code="1110")

    def test_rejects_negative(self):
        with self.assertRaises(ValueError):
            LineSpec(account_code="1110", debit=D("-1"))


class PostTransactionTests(TestCase):
    def setUp(self):
        self.cash, self.revenue, self.expense = make_accounts()

    def test_happy_path_posts_balanced_entry(self):
        entry = post_transaction(
            date=date(2026, 1, 15),
            memo="Cash sale",
            lines=[
                LineSpec(account_code="1110", debit=D("100.00")),
                LineSpec(account_code="4100", credit=D("100.00")),
            ],
        )
        self.assertEqual(entry.status, JournalEntry.Status.POSTED)
        self.assertIsNotNone(entry.posted_at)
        self.assertTrue(entry.is_balanced())
        self.assertTrue(entry.number.startswith("JE-2026-"))

    def test_unbalanced_raises_and_rolls_back(self):
        with self.assertRaises(ValidationError):
            post_transaction(
                date=date(2026, 1, 15),
                memo="Bad entry",
                lines=[
                    LineSpec(account_code="1110", debit=D("100.00")),
                    LineSpec(account_code="4100", credit=D("99.00")),
                ],
            )
        # Nothing should have been persisted.
        self.assertFalse(JournalEntry.objects.exists())
        self.assertFalse(JournalLine.objects.exists())

    def test_unknown_account_raises(self):
        with self.assertRaises(ValidationError):
            post_transaction(
                date=date(2026, 1, 15),
                memo="Bad account",
                lines=[
                    LineSpec(account_code="9999", debit=D("100")),
                    LineSpec(account_code="4100", credit=D("100")),
                ],
            )

    def test_non_postable_account_rejected(self):
        header = Account.objects.create(
            code="1000", name="Assets", type=AccountType.ASSET, is_postable=False,
        )
        with self.assertRaises(ValidationError):
            post_transaction(
                date=date(2026, 1, 15),
                memo="To header",
                lines=[
                    LineSpec(account_code="1000", debit=D("10")),
                    LineSpec(account_code="4100", credit=D("10")),
                ],
            )

    def test_zero_total_rejected(self):
        with self.assertRaises(ValueError):
            LineSpec(account_code="1110", debit=D("0.00"))

    def test_numbering_is_sequential_and_gap_free(self):
        for _ in range(5):
            post_transaction(
                date=date(2026, 3, 1),
                memo="seq",
                lines=[
                    LineSpec(account_code="1110", debit=D("1")),
                    LineSpec(account_code="4100", credit=D("1")),
                ],
            )
        numbers = list(
            JournalEntry.objects.order_by("id").values_list("number", flat=True)
        )
        self.assertEqual(numbers, [
            "JE-2026-000001", "JE-2026-000002", "JE-2026-000003",
            "JE-2026-000004", "JE-2026-000005",
        ])

    def test_rollback_does_not_burn_number(self):
        # Post one good one to seed counter at 2.
        post_transaction(
            date=date(2026, 4, 1), memo="ok",
            lines=[
                LineSpec(account_code="1110", debit=D("1")),
                LineSpec(account_code="4100", credit=D("1")),
            ],
        )
        # Now try to post a bad one. It should rollback and NOT burn the next number.
        with self.assertRaises(ValidationError):
            post_transaction(
                date=date(2026, 4, 2), memo="bad",
                lines=[
                    LineSpec(account_code="1110", debit=D("1")),
                    LineSpec(account_code="4100", credit=D("2")),  # unbalanced
                ],
            )
        # Next successful post should be JE-2026-000002, not 000003.
        entry = post_transaction(
            date=date(2026, 4, 3), memo="ok2",
            lines=[
                LineSpec(account_code="1110", debit=D("1")),
                LineSpec(account_code="4100", credit=D("1")),
            ],
        )
        self.assertEqual(entry.number, "JE-2026-000002")


class ImmutabilityTests(TestCase):
    def setUp(self):
        self.cash, self.revenue, self.expense = make_accounts()
        self.entry = post_transaction(
            date=date(2026, 1, 1), memo="Original",
            lines=[
                LineSpec(account_code="1110", debit=D("50")),
                LineSpec(account_code="4100", credit=D("50")),
            ],
        )

    def test_cannot_save_posted_entry(self):
        self.entry.memo = "Changed"
        with self.assertRaises(ValidationError):
            self.entry.save()

    def test_cannot_modify_lines_of_posted_entry(self):
        line = self.entry.lines.first()
        line.memo = "Edited line"
        with self.assertRaises(ValidationError):
            line.save()

    def test_db_check_constraint_blocks_unbalanced_line(self):
        # Try to sneak in a line with BOTH sides — this should fail at the DB.
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                JournalLine.objects.create(
                    entry=self.entry,
                    account=self.cash,
                    debit=D("10"),
                    credit=D("10"),
                )


class ReversalTests(TestCase):
    def setUp(self):
        self.cash, self.revenue, self.expense = make_accounts()

    def test_reversal_zeroes_out_account_balances(self):
        original = post_transaction(
            date=date(2026, 1, 1), memo="To reverse",
            lines=[
                LineSpec(account_code="1110", debit=D("200")),
                LineSpec(account_code="4100", credit=D("200")),
            ],
        )
        reverse_entry(original, date=date(2026, 1, 2), memo="Reversal")

        # Sum of debits/credits per account across both entries should net to zero.
        from django.db.models import Sum
        cash_d = JournalLine.objects.filter(account=self.cash).aggregate(s=Sum("debit"))["s"]
        cash_c = JournalLine.objects.filter(account=self.cash).aggregate(s=Sum("credit"))["s"]
        self.assertEqual(cash_d, cash_c)


class FinancialReportTests(TestCase):
    def setUp(self):
        self.cash, self.revenue, self.expense = make_accounts()
        self.ap = Account.objects.create(
            code="2110", name="Accounts Payable", type=AccountType.LIABILITY,
        )
        self.equity = Account.objects.create(
            code="3100", name="Owner's Capital", type=AccountType.EQUITY,
        )

    def test_income_statement_uses_range_and_normal_balances(self):
        post_transaction(
            date=date(2026, 1, 1),
            memo="Before period",
            lines=[
                LineSpec(account_code="1110", debit=D("25.00")),
                LineSpec(account_code="4100", credit=D("25.00")),
            ],
        )
        post_transaction(
            date=date(2026, 2, 1),
            memo="Sale in period",
            lines=[
                LineSpec(account_code="1110", debit=D("100.00")),
                LineSpec(account_code="4100", credit=D("100.00")),
            ],
        )
        post_transaction(
            date=date(2026, 2, 2),
            memo="Expense in period",
            lines=[
                LineSpec(account_code="6100", debit=D("40.00")),
                LineSpec(account_code="1110", credit=D("40.00")),
            ],
        )

        report = income_statement(start=date(2026, 2, 1), end=date(2026, 2, 28))

        self.assertEqual(report.total_revenue, D("100.00"))
        self.assertEqual(report.total_expenses, D("40.00"))
        self.assertEqual(report.net_income, D("60.00"))

    def test_balance_sheet_includes_current_earnings(self):
        post_transaction(
            date=date(2026, 1, 1),
            memo="Owner investment",
            lines=[
                LineSpec(account_code="1110", debit=D("50.00")),
                LineSpec(account_code="3100", credit=D("50.00")),
            ],
        )
        post_transaction(
            date=date(2026, 2, 1),
            memo="Cash sale",
            lines=[
                LineSpec(account_code="1110", debit=D("100.00")),
                LineSpec(account_code="4100", credit=D("100.00")),
            ],
        )
        post_transaction(
            date=date(2026, 2, 2),
            memo="Vendor bill",
            lines=[
                LineSpec(account_code="6100", debit=D("30.00")),
                LineSpec(account_code="2110", credit=D("30.00")),
            ],
        )

        report = balance_sheet(as_of=date(2026, 2, 28))

        self.assertEqual(report.total_assets, D("150.00"))
        self.assertEqual(report.total_liabilities, D("30.00"))
        self.assertEqual(report.total_equity, D("120.00"))
        self.assertEqual(report.total_assets, report.total_liabilities_and_equity)


class GLDrillDownTests(TestCase):
    def setUp(self):
        from core.models import User, Role
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.user = User.objects.create_user(
            username="acct_user",
            email="acct@example.com",
            password="password",
            role=Role.ADMIN,
        )
        self.cash, self.revenue, self.expense = make_accounts()
        self.entry = post_transaction(
            date=date(2026, 1, 15),
            memo="Test entry",
            lines=[
                LineSpec(account_code="1110", debit=Decimal("100.00")),
                LineSpec(account_code="4100", credit=Decimal("100.00")),
            ],
        )

    def test_gl_autofill_dates_on_drilldown(self):
        from django.urls import reverse
        self.client.force_login(self.user)
        # Query GL with ONLY account pk
        response = self.client.get(reverse("general_ledger"), data={"account": self.cash.pk})
        self.assertEqual(response.status_code, 200)
        # Check that dates were pre-populated and ledger rendered lines
        self.assertContains(response, "Opening balance")
        self.assertContains(response, "JE-2026-000001")
        self.assertContains(response, "Test entry")

    def test_trial_balance_renders_ledger_links(self):
        from django.urls import reverse
        self.client.force_login(self.user)
        response = self.client.get(reverse("trial_balance"), data={"as_of": "2026-12-31"})
        self.assertEqual(response.status_code, 200)
        # Verify account code has a link to general ledger
        expected_link = f'/reports/general-ledger/?account={self.cash.pk}'
        self.assertContains(response, expected_link)

    def test_income_statement_renders_ledger_links(self):
        from django.urls import reverse
        self.client.force_login(self.user)
        response = self.client.get(reverse("income_statement"), data={"start": "2026-01-01", "end": "2026-12-31"})
        self.assertEqual(response.status_code, 200)
        # Verify revenue account has a link to general ledger
        expected_link = f'/reports/general-ledger/?account={self.revenue.pk}'
        self.assertContains(response, expected_link)

    def test_balance_sheet_renders_ledger_links(self):
        from django.urls import reverse
        self.client.force_login(self.user)
        response = self.client.get(reverse("balance_sheet"), data={"as_of": "2026-12-31"})
        self.assertEqual(response.status_code, 200)
        # Verify asset account has a link to general ledger
        expected_link = f'/reports/general-ledger/?account={self.cash.pk}'
        self.assertContains(response, expected_link)

    def test_journal_detail_renders_source_doc_link(self):
        from django.urls import reverse
        self.client.force_login(self.user)
        
        # Link source doc to self.entry by using update to bypass the save-method immutability check
        JournalEntry.objects.filter(pk=self.entry.pk).update(
            source_doc_type="Invoice",
            source_doc_id=99
        )
        
        response = self.client.get(reverse("journal_detail", args=[self.entry.pk]))
        self.assertEqual(response.status_code, 200)
        # Verify link to Invoice #99 exists
        self.assertContains(response, '/invoices/99/')
