"""SQLite storage: products, scrape runs and price observations, with versioned migrations.

Data model (schema version 2)::

    products(id, url UNIQUE)
    runs(id, started_at, finished_at, status, complete, pages, items, error)
    observations(run_id -> runs, product_id -> products,
                 title, price_cents, currency, rating, in_stock)

Every scrape is a *run*. Observations are written page by page, and a run is only marked
``ok`` once the crawl finishes. Everything a report shows (titles, prices, first/last seen)
is derived from observations of ``ok`` runs, so a run that crashes halfway can never leak
half-written data into reports.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

from .scraper import Book

SCHEMA_VERSION = 2

_SCHEMA_V2 = """
CREATE TABLE products (
    id   INTEGER PRIMARY KEY,
    url  TEXT NOT NULL UNIQUE
);

CREATE TABLE runs (
    id           INTEGER PRIMARY KEY,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'ok', 'failed')),
    complete     INTEGER NOT NULL DEFAULT 0 CHECK (complete IN (0, 1)),
    pages        INTEGER NOT NULL DEFAULT 0,
    items        INTEGER NOT NULL DEFAULT 0,
    error        TEXT
);

CREATE TABLE observations (
    run_id       INTEGER NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    product_id   INTEGER NOT NULL REFERENCES products (id),
    title        TEXT NOT NULL,
    price_cents  INTEGER NOT NULL CHECK (price_cents >= 0),
    currency     TEXT NOT NULL,
    rating       INTEGER NOT NULL CHECK (rating BETWEEN 0 AND 5),
    in_stock     INTEGER NOT NULL CHECK (in_stock IN (0, 1)),
    PRIMARY KEY (run_id, product_id)
);

CREATE INDEX observations_product_idx ON observations (product_id, run_id);
CREATE INDEX runs_status_idx ON runs (status, id);
"""

# The latest observation of each product among ok runs before :before (exclusive).
_LATEST_OK_OBSERVATIONS = """
    SELECT o.product_id, p.url, o.title, o.price_cents, o.currency, o.rating, o.in_stock,
           o.run_id
    FROM observations o
    JOIN products p ON p.id = o.product_id
    WHERE o.run_id = (
        SELECT MAX(o2.run_id) FROM observations o2 JOIN runs r ON r.id = o2.run_id
        WHERE o2.product_id = o.product_id AND r.status = 'ok' AND o2.run_id < :before
    )
"""


def _execute_script(conn: sqlite3.Connection, script: str) -> None:
    # sqlite3's executescript() COMMITs first, which would break migration atomicity.
    for statement in script.split(";"):
        if statement.strip():
            conn.execute(statement)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# -- migrations ------------------------------------------------------------------------------


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """v1 stored one flat ``prices`` row per product per scrape, with float prices.

    Each distinct ``scraped_at`` becomes a successful run; prices become integer cents.
    """
    _execute_script(conn, _SCHEMA_V2)
    conn.execute(
        "INSERT INTO runs (started_at, finished_at, status, complete) "
        "SELECT scraped_at, scraped_at, 'ok', 0 FROM prices "
        "GROUP BY scraped_at ORDER BY MIN(id)"
    )
    conn.execute("INSERT INTO products (url) SELECT url FROM prices GROUP BY url ORDER BY MIN(id)")
    conn.execute(
        "INSERT OR REPLACE INTO observations "
        "(run_id, product_id, title, price_cents, currency, rating, in_stock) "
        "SELECT r.id, pr.id, p.title, CAST(ROUND(p.price * 100) AS INTEGER), 'GBP', "
        "p.rating, p.in_stock "
        "FROM prices p JOIN runs r ON r.started_at = p.scraped_at "
        "JOIN products pr ON pr.url = p.url ORDER BY p.id"
    )
    conn.execute(
        "UPDATE runs SET items = (SELECT COUNT(*) FROM observations o WHERE o.run_id = runs.id), "
        "pages = 1"
    )
    conn.execute("DROP TABLE prices")


MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    # from_version -> migration to from_version + 1
    1: _migrate_v1_to_v2,
}


def _detect_version(conn: sqlite3.Connection) -> int:
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version:
        return version
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    return 1 if "prices" in tables else 0


def migrate(conn: sqlite3.Connection) -> int:
    """Bring the database to :data:`SCHEMA_VERSION`. Returns the version it started at.

    The version is re-checked after taking the write lock, so two processes opening an
    old database at the same moment cannot both try to migrate it.
    """
    start = _detect_version(conn)
    if start == SCHEMA_VERSION:
        return start
    if start > SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema v{start} is newer than this BookWatch (v{SCHEMA_VERSION}); upgrade"
        )
    conn.execute("BEGIN IMMEDIATE")  # DDL is transactional in SQLite: all or nothing
    try:
        start = _detect_version(conn)
        if start == 0:
            _execute_script(conn, _SCHEMA_V2)
        else:
            for version in range(start, SCHEMA_VERSION):
                MIGRATIONS[version](conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
    return start


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
        migrate(conn)
    except BaseException:
        conn.close()
        raise
    return conn


# -- runs ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Run:
    id: int
    started_at: str
    finished_at: str | None
    status: str
    complete: bool
    pages: int
    items: int
    error: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Run:
        return cls(
            id=row["id"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            status=row["status"],
            complete=bool(row["complete"]),
            pages=row["pages"],
            items=row["items"],
            error=row["error"],
        )


def start_run(conn: sqlite3.Connection, when: str | None = None) -> int:
    with conn:
        cur = conn.execute("INSERT INTO runs (started_at) VALUES (?)", (when or _now(),))
    return int(cur.lastrowid or 0)


_INSERT_PRODUCT = "INSERT INTO products (url) VALUES (?) ON CONFLICT (url) DO NOTHING"

_UPSERT_OBSERVATION = """
    INSERT INTO observations
        (run_id, product_id, title, price_cents, currency, rating, in_stock)
    SELECT ?, id, ?, ?, ?, ?, ? FROM products WHERE url = ?
    ON CONFLICT (run_id, product_id) DO UPDATE SET
        title = excluded.title, price_cents = excluded.price_cents,
        currency = excluded.currency, rating = excluded.rating, in_stock = excluded.in_stock
"""


def record_page(conn: sqlite3.Connection, run_id: int, books: Iterable[Book]) -> int:
    """Store one page of observations atomically. Returns the number of books recorded.

    Only observations carry data; a product row is just a stable id for a URL, so
    nothing here can change what reports show until the run is marked ``ok``.
    """
    count = 0
    with conn:
        for b in books:
            conn.execute(_INSERT_PRODUCT, (b.url,))
            conn.execute(
                _UPSERT_OBSERVATION,
                (run_id, b.title, b.price_cents, b.currency, b.rating, int(b.in_stock), b.url),
            )
            count += 1
        conn.execute(
            "UPDATE runs SET pages = pages + 1, "
            "items = (SELECT COUNT(*) FROM observations WHERE run_id = ?) WHERE id = ?",
            (run_id, run_id),
        )
    return count


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    complete: bool,
    error: str | None = None,
    when: str | None = None,
) -> None:
    with conn:
        conn.execute(
            "UPDATE runs SET status = ?, complete = ?, error = ?, finished_at = ? WHERE id = ?",
            ("failed" if error else "ok", int(complete), error, when or _now(), run_id),
        )


def runs(conn: sqlite3.Connection, limit: int = 20) -> list[Run]:
    rows = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [Run.from_row(r) for r in rows]


def last_ok_runs(conn: sqlite3.Connection, n: int = 2) -> list[Run]:
    """The ``n`` most recent successful runs, newest first."""
    rows = conn.execute(
        "SELECT * FROM runs WHERE status = 'ok' ORDER BY id DESC LIMIT ?", (n,)
    ).fetchall()
    return [Run.from_row(r) for r in rows]


def get_run(conn: sqlite3.Connection, run_id: int) -> Run | None:
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return Run.from_row(row) if row else None


def last_complete_ok_run_before(conn: sqlite3.Connection, run_id: int) -> int | None:
    row = conn.execute(
        "SELECT MAX(id) FROM runs WHERE status = 'ok' AND complete = 1 AND id < ?", (run_id,)
    ).fetchone()
    return row[0] if row and row[0] is not None else None


# -- queries ---------------------------------------------------------------------------------


def snapshot(conn: sqlite3.Connection, run_id: int) -> dict[str, sqlite3.Row]:
    """Everything observed in one run, keyed by product URL."""
    rows = conn.execute(
        "SELECT o.product_id, p.url, o.title, o.price_cents, o.currency, o.rating, o.in_stock, "
        "o.run_id FROM observations o JOIN products p ON p.id = o.product_id WHERE o.run_id = ?",
        (run_id,),
    ).fetchall()
    return {r["url"]: r for r in rows}


def previous_observations(conn: sqlite3.Connection, before_run: int) -> dict[str, sqlite3.Row]:
    """Each product's most recent observation in an ok run older than ``before_run``."""
    rows = conn.execute(_LATEST_OK_OBSERVATIONS, {"before": before_run}).fetchall()
    return {r["url"]: r for r in rows}


def latest(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """The most recent successful observation of every product, ordered by title.

    ``first_seen`` / ``last_seen`` are the start times of the first and last ok runs
    that observed the product.
    """
    return conn.execute(
        f"""
        WITH latest AS ({_LATEST_OK_OBSERVATIONS}),
        seen AS (
            SELECT o.product_id, MIN(r.started_at) AS first_seen, MAX(r.started_at) AS last_seen
            FROM observations o JOIN runs r ON r.id = o.run_id
            WHERE r.status = 'ok' GROUP BY o.product_id
        )
        SELECT l.url, l.title, l.price_cents, l.currency, l.rating, l.in_stock,
               s.first_seen, s.last_seen
        FROM latest l JOIN seen s ON s.product_id = l.product_id
        ORDER BY l.title COLLATE NOCASE, l.url
        """,
        {"before": 2**62},
    ).fetchall()


def find_products(conn: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    """Products (seen in an ok run) whose URL equals ``query`` or whose title contains it."""
    candidates = conn.execute(
        f"SELECT product_id AS id, url, title FROM ({_LATEST_OK_OBSERVATIONS}) "
        "ORDER BY title COLLATE NOCASE",
        {"before": 2**62},
    ).fetchall()
    exact = [r for r in candidates if r["url"] == query]
    if exact:
        return exact
    needle = query.casefold()
    return [r for r in candidates if needle in r["title"].casefold()]


def price_history(conn: sqlite3.Connection, product_id: int) -> list[sqlite3.Row]:
    """One row per successful run that observed the product, oldest first."""
    return conn.execute(
        "SELECT r.id AS run_id, r.started_at, o.price_cents, o.currency, o.in_stock "
        "FROM observations o JOIN runs r ON r.id = o.run_id "
        "WHERE o.product_id = ? AND r.status = 'ok' ORDER BY r.id",
        (product_id,),
    ).fetchall()
