"""
Posting service.

This is the ONLY function in the codebase that should create JournalEntry
records. Every module — sales, purchasing, inventory, manufacturing — calls
post_transaction() to record the financial side of whatever it just did.

If you find yourself wanting to bypass this — don't. Add the use case here.
A single chokepoint is what lets you audit financial integrity in one place.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from django.core.exceptions import ValidationError
from django.db import transaction

from core.numbering import next_document_number
from .models import Account, JournalEntry, JournalLine, ZERO


@dataclass(frozen=True)
class LineSpec:
    """Caller-facing description of one journal line. Use debit XOR credit."""
    account_code: str
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    memo: str = ""

    def __post_init__(self):
        # Validate at construction time so bugs surface near the call site.
        if self.debit < 0 or self.credit < 0:
            raise ValueError("debit and credit must be non-negative")
        if (self.debit > 0) == (self.credit > 0):
            # Both zero, or both positive — both wrong.
            raise ValueError(
                f"LineSpec must have exactly one of debit/credit > 0 "
                f"(got debit={self.debit}, credit={self.credit})"
            )


@transaction.atomic
def post_transaction(
    *,
    date,
    memo: str,
    lines: Sequence[LineSpec],
    user=None,
    source_doc_type: str = "",
    source_doc_id: int | None = None,
) -> JournalEntry:
    """
    Create and post a balanced journal entry in one atomic transaction.

    On any failure (unbalanced, unknown account, DB constraint violation)
    the entire transaction rolls back and nothing is persisted.

    Arguments:
        date:             effective accounting date
        memo:             human-readable description of the transaction
        lines:            sequence of LineSpec — must balance
        user:             the User who initiated this (for audit)
        source_doc_type:  e.g. "Invoice", "Bill", "ManufacturingOrder"
        source_doc_id:    PK of the source document

    Returns the posted JournalEntry. After return, the entry is immutable.
    """
    if len(lines) < 2:
        raise ValidationError("A journal entry needs at least two lines.")

    total_debit = sum((l.debit for l in lines), ZERO)
    total_credit = sum((l.credit for l in lines), ZERO)
    if total_debit != total_credit:
        raise ValidationError(
            f"Unbalanced: total debits {total_debit} != total credits {total_credit}"
        )
    if total_debit == ZERO:
        raise ValidationError("Journal entry has zero total amount.")

    # Resolve account codes once, up-front. Fail loudly on unknown codes.
    codes = {l.account_code for l in lines}
    accounts = {
        a.code: a
        for a in Account.objects.filter(code__in=codes, is_active=True, is_postable=True)
    }
    missing = codes - accounts.keys()
    if missing:
        raise ValidationError(
            f"Unknown, inactive, or non-postable account codes: {sorted(missing)}"
        )

    # Build the entry in DRAFT first, attach lines, then flip to POSTED.
    # This sequencing matters: the immutability guard on JournalEntry.save()
    # only forbids edits AFTER posting, so we set status=POSTED last.
    entry = JournalEntry.objects.create(
        date=date,
        memo=memo,
        status=JournalEntry.Status.DRAFT,
        source_doc_type=source_doc_type,
        source_doc_id=source_doc_id,
        created_by=user,
    )

    JournalLine.objects.bulk_create([
        JournalLine(
            entry=entry,
            account=accounts[l.account_code],
            debit=l.debit,
            credit=l.credit,
            memo=l.memo,
        )
        for l in lines
    ])

    # Re-verify balance from the DB rows we just wrote. Paranoid? Yes. Worth it.
    entry.assert_balanced()

    # Assign document number and flip to POSTED in one step. We allocate the
    # number here (not at creation) so that abandoned drafts don't burn numbers.
    from django.utils import timezone
    entry.number = next_document_number("JE", year=date.year)
    entry.status = JournalEntry.Status.POSTED
    entry.posted_at = timezone.now()
    entry.posted_by = user
    # Bypass the immutability check on this final save — we're going FROM draft,
    # which is allowed. The guard fires on edits to already-posted entries.
    JournalEntry.objects.filter(pk=entry.pk).update(
        number=entry.number,
        status=entry.status,
        posted_at=entry.posted_at,
        posted_by=entry.posted_by,
    )
    entry.refresh_from_db()
    return entry


@transaction.atomic
def reverse_entry(original: JournalEntry, *, date, memo: str, user=None) -> JournalEntry:
    """
    Post a new entry that exactly reverses an existing posted entry.
    Use this instead of trying to delete or edit a posted entry.
    """
    if original.status != JournalEntry.Status.POSTED:
        raise ValidationError("Can only reverse a posted entry.")

    reversed_lines = [
        LineSpec(
            account_code=line.account.code,
            debit=line.credit,    # swap sides
            credit=line.debit,
            memo=f"Reversal of {original.number}",
        )
        for line in original.lines.select_related("account")
    ]
    return post_transaction(
        date=date,
        memo=memo or f"Reversal of {original.number}",
        lines=reversed_lines,
        user=user,
        source_doc_type="JournalEntry.Reversal",
        source_doc_id=original.pk,
    )
