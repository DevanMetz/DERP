"""Session-based shopping cart.

The cart lives entirely in request.session under the key "cart" as:
    {"<product_id>": {"qty": int, "added_at": iso8601}}

Quantities are integers. Pricing is resolved at read time from the
ProductStorefront / Product, never trusted from the client.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

from .models import ProductStorefront


CART_KEY = "cart"
MAX_QTY_PER_LINE = 99


@dataclass
class CartLine:
    storefront: ProductStorefront
    qty: int

    @property
    def unit_price(self) -> Decimal:
        return self.storefront.effective_price

    @property
    def line_total(self) -> Decimal:
        return (self.unit_price * self.qty).quantize(Decimal("0.01"))


class Cart:
    def __init__(self, request):
        self.request = request
        self._data = request.session.get(CART_KEY) or {}

    # ---------- mutation ----------
    def set_qty(self, product_id: int, qty: int) -> None:
        pid = str(product_id)
        qty = max(0, min(int(qty), MAX_QTY_PER_LINE))
        if qty == 0:
            self._data.pop(pid, None)
        else:
            existing = self._data.get(pid, {})
            self._data[pid] = {
                "qty": qty,
                "added_at": existing.get("added_at") or datetime.now(timezone.utc).isoformat(),
            }
        self._persist()

    def add(self, product_id: int, qty: int = 1) -> None:
        pid = str(product_id)
        current = self._data.get(pid, {}).get("qty", 0)
        self.set_qty(product_id, current + qty)

    def remove(self, product_id: int) -> None:
        self.set_qty(product_id, 0)

    def clear(self) -> None:
        self._data = {}
        self._persist()

    def _persist(self) -> None:
        self.request.session[CART_KEY] = self._data
        self.request.session.modified = True

    # ---------- reads ----------
    def _resolve(self) -> list[CartLine]:
        if not self._data:
            return []
        ids = [int(pid) for pid in self._data.keys()]
        storefronts = ProductStorefront.objects.filter(
            product_id__in=ids, is_online_active=True,
        ).select_related("product")
        by_id = {sf.product_id: sf for sf in storefronts}
        lines = []
        stale_ids = []
        for pid_str, entry in self._data.items():
            pid = int(pid_str)
            sf = by_id.get(pid)
            if not sf:
                stale_ids.append(pid_str)
                continue
            lines.append(CartLine(storefront=sf, qty=int(entry.get("qty", 0))))
        # Auto-prune deactivated/deleted lines
        if stale_ids:
            for sid in stale_ids:
                self._data.pop(sid, None)
            self._persist()
        return lines

    @property
    def lines(self) -> list[CartLine]:
        if not hasattr(self, "_cached_lines"):
            self._cached_lines = self._resolve()
        return self._cached_lines

    @property
    def item_count(self) -> int:
        return sum(line.qty for line in self.lines)

    @property
    def subtotal(self) -> Decimal:
        return sum((line.line_total for line in self.lines), Decimal("0.00")).quantize(Decimal("0.01"))

    def __iter__(self) -> Iterable[CartLine]:
        return iter(self.lines)

    def __len__(self) -> int:
        return len(self.lines)

    def __bool__(self) -> bool:
        return bool(self.lines)
