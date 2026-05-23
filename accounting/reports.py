"""
Core accounting reports.

These exist primarily as data-access functions; views render them.
Keeping the logic out of views means the test suite can hammer them
directly with thousands of synthetic transactions.

Performance note: at small-business scale (millions of journal lines total,
hundreds of thousands per year), Postgres handles this without help. Don't
add materialized views or denormalized snapshots until profiling proves
they're needed. They will not be.
"""

from dataclasses import dataclass
from datetime import date as date_cls
from decimal import Decimal
from typing import Iterable

from django.db.models import Sum, Q

from .models import (
    Account, JournalEntry, JournalLine,
    AccountType, NORMAL_BALANCE_DEBIT, ZERO,
)


@dataclass
class TrialBalanceRow:
    account: Account
    debit_total: Decimal
    credit_total: Decimal

    @property
    def balance_debit(self) -> Decimal:
        diff = self.debit_total - self.credit_total
        return diff if diff > 0 else ZERO

    @property
    def balance_credit(self) -> Decimal:
        diff = self.credit_total - self.debit_total
        return diff if diff > 0 else ZERO


def trial_balance(*, as_of: date_cls) -> list[TrialBalanceRow]:
    """
    Trial balance as of a date (inclusive). Includes only postable accounts
    with any activity, plus header accounts are excluded.

    Verifies that total debits == total credits across all rows. If they
    don't, raise — that means a posted entry got into the DB unbalanced,
    which is a bug, not a user-facing condition.
    """
    qs = (
        JournalLine.objects
        .filter(entry__status=JournalEntry.Status.POSTED, entry__date__lte=as_of)
        .values("account_id")
        .annotate(
            debit_total=Sum("debit"),
            credit_total=Sum("credit"),
        )
    )
    accounts_by_id = {a.id: a for a in Account.objects.filter(is_postable=True)}

    rows: list[TrialBalanceRow] = []
    for r in qs:
        acct = accounts_by_id.get(r["account_id"])
        if acct is None:
            continue  # postings to non-postable accounts shouldn't exist; skip if they do
        rows.append(TrialBalanceRow(
            account=acct,
            debit_total=r["debit_total"] or ZERO,
            credit_total=r["credit_total"] or ZERO,
        ))

    rows.sort(key=lambda r: r.account.code)

    total_d = sum((r.debit_total for r in rows), ZERO)
    total_c = sum((r.credit_total for r in rows), ZERO)
    if total_d != total_c:
        raise AssertionError(
            f"Trial balance is unbalanced: debits={total_d}, credits={total_c}. "
            f"This means an unbalanced JournalEntry was persisted, which "
            f"should be impossible. Check the constraint logic."
        )
    return rows


@dataclass
class GLLine:
    date: date_cls
    entry_number: str
    memo: str
    debit: Decimal
    credit: Decimal
    running_balance: Decimal  # signed; positive = debit-side balance


def general_ledger(
    *, account: Account, start: date_cls, end: date_cls,
) -> list[GLLine]:
    """
    All postings to a single account between start and end (inclusive),
    in chronological order, with a running balance.

    Running balance convention: positive numbers indicate a debit-side
    balance, negative indicate credit-side. The report renderer should
    flip the sign for natural-credit accounts (liabilities, equity, revenue)
    when displaying — that's a presentation concern.
    """
    # Opening balance: all activity before `start`.
    opening_qs = (
        JournalLine.objects
        .filter(
            account=account,
            entry__status=JournalEntry.Status.POSTED,
            entry__date__lt=start,
        )
        .aggregate(d=Sum("debit"), c=Sum("credit"))
    )
    opening = (opening_qs["d"] or ZERO) - (opening_qs["c"] or ZERO)

    lines = (
        JournalLine.objects
        .filter(
            account=account,
            entry__status=JournalEntry.Status.POSTED,
            entry__date__gte=start,
            entry__date__lte=end,
        )
        .select_related("entry")
        .order_by("entry__date", "entry__id", "id")
    )

    running = opening
    out = [GLLine(
        date=start,
        entry_number="OPENING",
        memo="Opening balance",
        debit=ZERO,
        credit=ZERO,
        running_balance=running,
    )]
    for line in lines:
        running += line.debit - line.credit
        out.append(GLLine(
            date=line.entry.date,
            entry_number=line.entry.number or "",
            memo=line.entry.memo,
            debit=line.debit,
            credit=line.credit,
            running_balance=running,
        ))
    return out
