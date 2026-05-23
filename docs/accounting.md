# Accounting

Accounting is the integrity center of DERP. Business workflows should post through `accounting.services.post_transaction()` instead of creating journal entries directly from views.

## Core rules

- Every posted transaction must balance debits and credits.
- Posted journal entries are immutable.
- Corrections and voids should create reversing entries.
- Numbering should be gap-free for successful postings.
- Workflow code should use `transaction.atomic()` when stock and GL updates happen together.

## Important models

- `Account`: chart of accounts row with code, name, type, and postable flag.
- `JournalEntry`: transaction header with date, memo, number, status, and optional source document link.
- `JournalLine`: debit or credit line tied to an account.

## Reports

- Trial Balance: account balances as of a date.
- Income Statement: revenue and expense activity across a date range.
- Balance Sheet: assets, liabilities, equity, and current earnings as of a date.
- General Ledger: account-level activity with drill-down context.

## Service layer

Use `LineSpec` to describe posting lines and `post_transaction()` to validate, number, and persist the final entry.

```python
post_transaction(
    date=invoice.date,
    memo="Post invoice",
    lines=[
        LineSpec(account_code="1200", debit=invoice.total()),
        LineSpec(account_code="4100", credit=invoice.subtotal()),
    ],
)
```
