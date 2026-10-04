"""SQLite price history and change detection."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from .scraper import Book

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    price REAL NOT NULL,
    rating INTEGER NOT NULL,
    in_stock INTEGER NOT NULL,
    scraped_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prices_url ON prices(url, id);
"""


@dataclass(frozen=True)
class Change:
    title: str
    url: str
    old_price: float
    new_price: float

    @property
    def pct(self) -> float:
        return (self.new_price - self.old_price) / self.old_price * 100


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def save(conn: sqlite3.Connection, books: Iterable[Book], when: datetime | None = None) -> int:
    stamp = (when or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    rows = [(b.url, b.title, b.price, b.rating, int(b.in_stock), stamp) for b in books]
    with conn:
        conn.executemany(
            "INSERT INTO prices (url, title, price, rating, in_stock, scraped_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
    return len(rows)


def latest(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Most recent snapshot of every product."""
    return conn.execute(
        "SELECT p.* FROM prices p JOIN (SELECT url, MAX(id) AS mid FROM prices GROUP BY url) m "
        "ON p.id = m.mid ORDER BY p.title"
    ).fetchall()


def changes(conn: sqlite3.Connection) -> list[Change]:
    """Products whose price differs between the two most recent snapshots."""
    out = []
    for (url,) in conn.execute("SELECT DISTINCT url FROM prices").fetchall():
        rows = conn.execute(
            "SELECT title, price FROM prices WHERE url = ? ORDER BY id DESC LIMIT 2", (url,)
        ).fetchall()
        if len(rows) == 2 and rows[0]["price"] != rows[1]["price"]:
            out.append(Change(rows[0]["title"], url, rows[1]["price"], rows[0]["price"]))
    return sorted(out, key=lambda c: c.pct)
