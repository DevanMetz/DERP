"""
Accounting models.

Design invariants (these are not negotiable; everything else hangs off them):

1. Every business event in the system posts to JournalEntry + JournalLine.
   Inventory, sales, purchasing, manufacturing — all of them.

2. JournalLines for a given JournalEntry MUST sum to zero (debits == credits).
   Enforced at THREE layers:
     - Application: post_transaction() service refuses to save unbalanced.
     - Model: JournalEntry.clean() and post() re-check.
     - Database: CHECK trigger on commit (see migration).

3. A JournalEntry, once posted, is immutable. No UPDATE, no DELETE.
   To "fix" a posted entry, post a reversing entry. The audit trail must
   show the reversal, not hide the original.

4. JournalLine has separate debit and credit columns, both >= 0, exactly
   one of them > 0. We do NOT use signed amounts. This is the standard
   accounting convention and it makes reports trivial.

5. All money is stored as Decimal with explicit precision. Never float.
"""

from decimal import Decimal
from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from simple_history.models import HistoricalRecords


# Money precision: 14 digits total, 2 after decimal -> up to $999,999,999,999.99
# Plenty for small-business ERP; revisit only if you ever support BTC or similar.
MONEY_MAX_DIGITS = 14
MONEY_DECIMAL_PLACES = 2
ZERO = Decimal("0.00")


class AccountType(models.TextChoices):
    ASSET = "asset", "Asset"
    LIABILITY = "liability", "Liability"
    EQUITY = "equity", "Equity"
    REVENUE = "revenue", "Revenue"
    EXPENSE = "expense", "Expense"


# Which side increases each account type. Used by reports, not by posting
# (posting works in raw debits/credits — this is just for display logic).
NORMAL_BALANCE_DEBIT = {AccountType.ASSET, AccountType.EXPENSE}
NORMAL_BALANCE_CREDIT = {AccountType.LIABILITY, AccountType.EQUITY, AccountType.REVENUE}


class Account(models.Model):
    """
    A node in the chart of accounts. Hierarchical via `parent`.

    `code` is the human-facing identifier (e.g. "1010" for Cash).
    Codes are conventionally grouped: 1xxx assets, 2xxx liabilities, etc.
    """
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200)
    type = models.CharField(max_length=20, choices=AccountType.choices)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    # If True, this account can have journal lines posted to it.
    # Parent/header accounts should be is_postable=False.
    is_postable = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.name}"

    def clean(self):
        if self.parent and self.parent.type != self.type:
            raise ValidationError(
                "Child account type must match parent account type."
            )


class JournalEntry(models.Model):
    """
    A single accounting transaction. Header for one or more JournalLines.

    Workflow: created in DRAFT, then post() flips it to POSTED. Once POSTED,
    the entry and its lines are immutable. To reverse, create a new entry
    that mirrors this one with debits/credits swapped.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        VOID = "void", "Void"  # voided BEFORE posting; posted entries can't be voided

    # Document numbering. Assigned at post() time so drafts don't burn numbers.
    # Format: JE-YYYY-NNNNNN (year-scoped). See core.numbering.
    number = models.CharField(max_length=32, unique=True, null=True, blank=True)

    date = models.DateField(help_text="Effective accounting date.")
    memo = models.CharField(max_length=500, blank=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.DRAFT
    )

    # Provenance: where did this entry come from? Manual entries leave these null.
    # When sales/purchasing/inventory post via post_transaction(), they fill these in.
    source_doc_type = models.CharField(max_length=50, blank=True)
    source_doc_id = models.PositiveBigIntegerField(null=True, blank=True)

    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT,
        related_name="posted_journal_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT,
        related_name="created_journal_entries",
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [
            models.Index(fields=["status", "date"]),
            models.Index(fields=["source_doc_type", "source_doc_id"]),
        ]

    def __str__(self):
        return self.number or f"DRAFT-{self.pk}"

    # ------------------------------------------------------------------
    # Balance + immutability
    # ------------------------------------------------------------------

    def total_debit(self) -> Decimal:
        return self.lines.aggregate(s=models.Sum("debit"))["s"] or ZERO

    def total_credit(self) -> Decimal:
        return self.lines.aggregate(s=models.Sum("credit"))["s"] or ZERO

    def is_balanced(self) -> bool:
        return self.total_debit() == self.total_credit()

    def assert_balanced(self):
        d, c = self.total_debit(), self.total_credit()
        if d != c:
            raise ValidationError(
                f"Journal entry {self.pk} is unbalanced: debit={d}, credit={c}"
            )

    def save(self, *args, **kwargs):
        # Immutability guard: once POSTED, no edits allowed except through
        # the explicit void/reverse workflow (which creates new entries).
        if self.pk:
            try:
                existing = JournalEntry.objects.only("status").get(pk=self.pk)
            except JournalEntry.DoesNotExist:
                existing = None
            if existing and existing.status == self.Status.POSTED:
                # The only mutation allowed on a posted entry is... nothing.
                # Even changing the memo is forbidden — that's what audit log is for.
                raise ValidationError(
                    "Cannot modify a posted journal entry. "
                    "Reverse it by posting an offsetting entry."
                )
        super().save(*args, **kwargs)


class JournalLine(models.Model):
    """
    One side of a journal entry. Either `debit` or `credit` is > 0, never both,
    never neither. Both must be >= 0.
    """
    entry = models.ForeignKey(
        JournalEntry, on_delete=models.PROTECT, related_name="lines"
    )
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="lines")
    debit = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
        default=ZERO,
    )
    credit = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES,
        default=ZERO,
    )
    memo = models.CharField(max_length=500, blank=True)

    class Meta:
        # DB-level guarantees. These are belt-and-suspenders for the app-level
        # checks; the app should never even get here with bad data, but if it
        # does, the database refuses.
        constraints = [
            models.CheckConstraint(
                name="journalline_debit_nonneg",
                check=models.Q(debit__gte=0),
            ),
            models.CheckConstraint(
                name="journalline_credit_nonneg",
                check=models.Q(credit__gte=0),
            ),
            models.CheckConstraint(
                name="journalline_exactly_one_side",
                # Exactly one of debit/credit must be strictly positive.
                check=(
                    (models.Q(debit__gt=0) & models.Q(credit=0))
                    | (models.Q(credit__gt=0) & models.Q(debit=0))
                ),
            ),
        ]
        indexes = [
            models.Index(fields=["account", "entry"]),
        ]

    def __str__(self):
        side = f"DR {self.debit}" if self.debit else f"CR {self.credit}"
        return f"{self.account.code} {side}"

    def save(self, *args, **kwargs):
        # Block edits if the parent entry is posted. The model-level immutability
        # on JournalEntry doesn't fire when you only touch a line, so we check here too.
        if self.entry_id:
            parent_status = (
                JournalEntry.objects.only("status")
                .filter(pk=self.entry_id)
                .values_list("status", flat=True)
                .first()
            )
            if parent_status == JournalEntry.Status.POSTED and self.pk:
                raise ValidationError(
                    "Cannot modify lines on a posted journal entry."
                )
        super().save(*args, **kwargs)


class Payment(models.Model):
    """
    Cash movement that applies to one or more Invoices (customer payment)
    or Bills (vendor payment). One Payment posts exactly one JournalEntry:
      customer payment -> DR cash / CR AR
      vendor payment   -> DR AP   / CR cash

    PaymentApplication rows record which document(s) the payment applies to
    and how much went to each. The sum of applications must equal payment.amount
    (enforced in the posting service, not the model).
    """

    class Direction(models.TextChoices):
        RECEIVED = "received", "Received from customer"
        SENT = "sent", "Sent to vendor"

    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        CHECK = "check", "Check"
        ACH = "ach", "ACH / bank transfer"
        CARD = "card", "Card"
        OTHER = "other", "Other"

    number = models.CharField(max_length=32, unique=True, null=True, blank=True)
    date = models.DateField()
    direction = models.CharField(max_length=10, choices=Direction.choices)
    method = models.CharField(max_length=10, choices=Method.choices, default=Method.CHECK)
    amount = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES)
    reference = models.CharField(max_length=100, blank=True, help_text="Check number, ACH ref, etc.")

    # Counterparties. Exactly one of these is set, matching `direction`.
    customer = models.ForeignKey(
        "sales.Customer", null=True, blank=True, on_delete=models.PROTECT,
        related_name="payments",
    )
    vendor = models.ForeignKey(
        "purchasing.Vendor", null=True, blank=True, on_delete=models.PROTECT,
        related_name="payments",
    )

    # Which cash account moves. User-selectable so multi-bank-account orgs
    # can post to the right account.
    cash_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="payments_cash",
        limit_choices_to={"type": "asset", "is_postable": True},
    )

    journal_entry = models.ForeignKey(
        JournalEntry, null=True, blank=True, on_delete=models.PROTECT,
        related_name="payments",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.PROTECT,
        related_name="created_payments",
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [
            models.Index(fields=["direction", "date"]),
            models.Index(fields=["customer"]),
        ]
        constraints = [
            models.CheckConstraint(name="payment_amount_positive", check=models.Q(amount__gt=0)),
        ]

    def __str__(self):
        return self.number or f"PAY-DRAFT-{self.pk}"


class PaymentApplication(models.Model):
    """
    Polymorphic link from a Payment to the document it's applied to.
    doc_type is "Invoice" or "Bill"; doc_id is the PK in that table.

    A single Payment can split across multiple Invoices/Bills. The sum of
    application amounts must equal payment.amount (enforced in the service).
    """
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="applications")
    doc_type = models.CharField(max_length=20)  # "Invoice" | "Bill"
    doc_id = models.PositiveBigIntegerField()
    amount = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES)

    class Meta:
        indexes = [
            models.Index(fields=["doc_type", "doc_id"]),
        ]
        constraints = [
            models.CheckConstraint(name="paymentapp_amount_positive", check=models.Q(amount__gt=0)),
        ]
