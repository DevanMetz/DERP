"""
Document numbering.

We use a table with row-level locking (SELECT ... FOR UPDATE) rather than
Postgres sequences. Reason: Postgres sequences burn numbers on rollback,
which violates "gap-free" requirements that apply to invoices and bills
in many jurisdictions. A counter table inside the same transaction as the
document insert gives us gap-free numbering.

The cost: one extra row lock per document. At small-business volumes this
is invisible. If you ever scale beyond that, you can per-type these counters
to reduce lock contention — but you won't, because this is a small-business
ERP.

Year-scoping (numbers reset each calendar year) is configured via the
`year_scoped` flag on DocumentCounter. We're calendar-year only per the spec.
"""

from django.db import models, transaction


class DocumentCounter(models.Model):
    """
    One row per (doc_type, year) combination. `next_value` is the next
    number to issue. Take a row lock before incrementing.
    """
    doc_type = models.CharField(max_length=20)  # JE, INV, BILL, PO, SO, ...
    year = models.PositiveSmallIntegerField()
    prefix = models.CharField(max_length=20, default="")  # e.g. "JE-"
    next_value = models.PositiveIntegerField(default=1)
    pad_width = models.PositiveSmallIntegerField(default=6)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["doc_type", "year"], name="unique_doc_type_year",
            ),
        ]

    def format(self, value: int) -> str:
        return f"{self.prefix}{self.year}-{value:0{self.pad_width}d}"


# Default prefixes per doc type. Override in DocumentCounter rows if needed.
DEFAULT_PREFIXES = {
    "JE": "JE-",
    "INV": "INV-",
    "BILL": "BILL-",
    "PO": "PO-",
    "SO": "SO-",
    "MO": "MO-",   # manufacturing order
    "PAY": "PAY-",
}


@transaction.atomic
def next_document_number(doc_type: str, *, year: int) -> str:
    """
    Atomically issue the next gap-free document number for a given doc type
    and year. Must be called inside an outer transaction; the caller is
    expected to insert the document in the same transaction so that if the
    insert fails, the counter is rolled back too (preserving gap-free-ness).
    """
    counter, created = DocumentCounter.objects.select_for_update().get_or_create(
        doc_type=doc_type,
        year=year,
        defaults={"prefix": DEFAULT_PREFIXES.get(doc_type, f"{doc_type}-")},
    )
    value = counter.next_value
    counter.next_value = value + 1
    counter.save(update_fields=["next_value"])
    return counter.format(value)
