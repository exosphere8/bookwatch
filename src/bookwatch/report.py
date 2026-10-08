"""Plain-text rendering for the CLI."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from decimal import Decimal

from .diff import Change
from .storage import Run

CURRENCY_PREFIX = {"GBP": "£", "USD": "$", "EUR": "€", "JPY": "¥"}
SPARK = "▁▂▃▄▅▆▇█"
LABELS = {
    "price_drop": "DROP",
    "price_rise": "RISE",
    "back_in_stock": "IN STOCK",
    "sold_out": "SOLD OUT",
    "new": "NEW",
    "removed": "DELISTED",
}


def money(cents: int | None, currency: str = "GBP") -> str:
    if cents is None:
        return "-"
    amount = Decimal(cents) / 100
    prefix = CURRENCY_PREFIX.get(currency)
    return f"{prefix}{amount:,.2f}" if prefix else f"{amount:,.2f} {currency}"


def sparkline(values: Sequence[int]) -> str:
    if not values:
        return ""
    lo, hi = min(values), max(values)
    if lo == hi:
        return SPARK[3] * len(values)
    return "".join(SPARK[round((v - lo) / (hi - lo) * (len(SPARK) - 1))] for v in values)


def _truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def format_change(c: Change) -> str:
    label = LABELS[c.kind]
    if c.kind in ("price_drop", "price_rise"):
        move = f"({c.pct:+.1f}%)" if c.pct is not None else "(from free)"
        detail = (
            f"{money(c.old_price_cents, c.currency)} -> "
            f"{money(c.new_price_cents, c.currency)}  {move}"
        )
    elif c.kind == "removed":
        detail = f"last seen at {money(c.old_price_cents, c.currency)}"
    else:
        detail = money(c.new_price_cents, c.currency)
    return f"{label:<9} {_truncate(c.title, 48):<48}  {detail}"


def format_changes(old: Run | None, new: Run | None, changes: Sequence[Change]) -> str:
    if old is None or new is None:
        return (
            "Need at least two successful scrapes to compare. Run `bookwatch scrape` again later."
        )
    header = (
        f"Changes found by run #{new.id} ({new.started_at}); previous successful run "
        f"#{old.id} ({old.started_at})"
    )
    if not changes:
        return f"{header}\nNo changes."
    return "\n".join([header, "", *(format_change(c) for c in changes)])


def summarize(changes: Sequence[Change]) -> str:
    counts = {k: sum(c.kind == k for c in changes) for k in LABELS}
    parts = [f"{n} {LABELS[k].lower()}" for k, n in counts.items() if n]
    return ", ".join(parts) if parts else "no changes"


def format_runs(runs: Sequence[Run]) -> str:
    if not runs:
        return "No runs yet."
    lines = [f"{'RUN':>5}  {'STARTED (UTC)':<25} {'STATUS':<7} {'PAGES':>5} {'ITEMS':>6}  NOTE"]
    for r in runs:
        note = r.error or ("full catalogue" if r.complete else "partial crawl")
        lines.append(
            f"{r.id:>5}  {r.started_at:<25} {r.status:<7} {r.pages:>5} {r.items:>6}  "
            f"{_truncate(note, 60)}"
        )
    return "\n".join(lines)


def format_history(product: sqlite3.Row, history: Sequence[sqlite3.Row]) -> str:
    lines = [product["title"], product["url"], ""]
    if not history:
        return "\n".join([*lines, "No successful observations yet."])
    prices = [h["price_cents"] for h in history]
    currency = history[-1]["currency"]
    lines.append(f"{'RUN':>5}  {'SCRAPED (UTC)':<25} {'PRICE':>10}  STOCK")
    for h in history:
        lines.append(
            f"{h['run_id']:>5}  {h['started_at']:<25} "
            f"{money(h['price_cents'], h['currency']):>10}  "
            f"{'in stock' if h['in_stock'] else 'out of stock'}"
        )
    lines += [
        "",
        f"trend  {sparkline(prices)}",
        f"low {money(min(prices), currency)}  high {money(max(prices), currency)}  "
        f"now {money(prices[-1], currency)}",
    ]
    return "\n".join(lines)
