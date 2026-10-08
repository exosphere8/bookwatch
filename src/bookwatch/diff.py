"""Change detection: what a successful run saw that differs from what we knew before it."""

from __future__ import annotations

import math
import sqlite3
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from . import storage

# Display / sort order of change kinds.
KINDS = ("price_drop", "price_rise", "back_in_stock", "sold_out", "new", "removed")


@dataclass(frozen=True)
class Change:
    kind: str
    title: str
    url: str
    currency: str
    old_price_cents: int | None = None
    new_price_cents: int | None = None

    @property
    def pct(self) -> float | None:
        """Relative price move in percent; None when undefined (no old price, or old was 0)."""
        if not self.old_price_cents or self.new_price_cents is None:
            return None
        return (self.new_price_cents - self.old_price_cents) / self.old_price_cents * 100

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["pct"] = None if self.pct is None else round(self.pct, 2)
        return d


def _moves_at_least(old: int, new: int, min_pct: float) -> bool:
    """Exact check that |new - old| / old >= min_pct %, in integer/decimal arithmetic.

    Floats would turn a 29 % move into 28.999999999999996 % and drop it at --min-pct 29.
    A move away from a price of 0 is unbounded, so it always passes.
    """
    return Decimal(abs(new - old)) * 100 >= Decimal(str(min_pct)) * old


def _sort_key(c: Change) -> tuple[int, float, str]:
    if c.kind in ("price_drop", "price_rise"):
        pct = c.pct if c.pct is not None else math.inf  # rising from 0 is the biggest rise
        magnitude = pct if c.kind == "price_drop" else -pct
    else:
        magnitude = 0.0
    # Biggest drops first, then biggest rises, then everything else alphabetically.
    return KINDS.index(c.kind), magnitude, c.title.casefold()


def compare(conn: sqlite3.Connection, new: storage.Run, min_pct: float = 0.0) -> list[Change]:
    """Everything that changed in run ``new``, each change reported exactly once.

    Every product in ``new`` is compared with *its own* most recent observation in an
    earlier successful run, which is not necessarily the previous run. A product a short
    crawl did not reach is still compared correctly the next time it is seen.

    * ``new``: never observed in any earlier successful run.
    * ``removed``: only when ``new`` crawled the whole catalogue, the product is missing,
      and no complete crawl since its last sighting has reported it already.
    """
    after = storage.snapshot(conn, new.id)
    before = storage.previous_observations(conn, new.id)
    out: list[Change] = []

    for url, now in after.items():
        prev = before.get(url)
        if prev is None:
            out.append(Change("new", now["title"], url, now["currency"], None, now["price_cents"]))
            continue
        old_cents, new_cents = prev["price_cents"], now["price_cents"]
        if (
            new_cents != old_cents
            and now["currency"] == prev["currency"]
            and _moves_at_least(old_cents, new_cents, min_pct)
        ):
            kind = "price_drop" if new_cents < old_cents else "price_rise"
            out.append(Change(kind, now["title"], url, now["currency"], old_cents, new_cents))
        if now["in_stock"] != prev["in_stock"]:
            kind = "back_in_stock" if now["in_stock"] else "sold_out"
            out.append(Change(kind, now["title"], url, now["currency"], old_cents, new_cents))

    if new.complete:
        last_complete = storage.last_complete_ok_run_before(conn, new.id)
        for url, prev in before.items():
            already_reported = last_complete is not None and last_complete > prev["run_id"]
            if url not in after and not already_reported:
                out.append(
                    Change("removed", prev["title"], url, prev["currency"], prev["price_cents"])
                )

    return sorted(out, key=_sort_key)


def latest_changes(
    conn: sqlite3.Connection, min_pct: float = 0.0
) -> tuple[storage.Run | None, storage.Run | None, list[Change]]:
    """Changes found by the most recent successful run: ``(previous_ok, latest_ok, changes)``."""
    recent = storage.last_ok_runs(conn, 2)
    if len(recent) < 2:
        return None, (recent[0] if recent else None), []
    new, old = recent
    return old, new, compare(conn, new, min_pct)
