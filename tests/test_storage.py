import sqlite3

import pytest

from bookwatch import storage
from bookwatch.scraper import Book


def book(title="A", price=1000, in_stock=True, url=None):
    return Book(title, url or f"https://x.test/{title}", price, "GBP", 3, in_stock)


def ok_run(conn, books, complete=True, when=None):
    run_id = storage.start_run(conn, when)
    storage.record_page(conn, run_id, books)
    storage.finish_run(conn, run_id, complete=complete, when=when)
    return run_id


def failed_run(conn, books):
    run_id = storage.start_run(conn)
    storage.record_page(conn, run_id, books)
    storage.finish_run(conn, run_id, complete=False, error="FetchError: boom")
    return run_id


def test_fresh_database_is_created_at_current_schema(conn):
    assert conn.execute("PRAGMA user_version").fetchone()[0] == storage.SCHEMA_VERSION
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"products", "runs", "observations"} <= tables


def test_v1_database_is_migrated_in_place(tmp_path):
    path = str(tmp_path / "v1.db")
    legacy = sqlite3.connect(path)
    legacy.executescript(
        """
        CREATE TABLE prices (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL,
            title TEXT NOT NULL, price REAL NOT NULL, rating INTEGER NOT NULL,
            in_stock INTEGER NOT NULL, scraped_at TEXT NOT NULL);
        INSERT INTO prices (url, title, price, rating, in_stock, scraped_at) VALUES
            ('u1', 'Old title', 51.77, 3, 1, '2026-01-01T00:00:00+00:00'),
            ('u2', 'Cheap', 0.29, 1, 0, '2026-01-01T00:00:00+00:00'),
            ('u1', 'New title', 40.0, 3, 1, '2026-01-02T00:00:00+00:00');
        """
    )
    legacy.commit()
    legacy.close()

    conn = storage.connect(path)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert "prices" not in {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    runs = storage.runs(conn)
    assert [(r.status, r.items) for r in reversed(runs)] == [("ok", 2), ("ok", 1)]
    (u1,) = storage.find_products(conn, "u1")
    assert u1["title"] == "New title"
    assert [h["price_cents"] for h in storage.price_history(conn, u1["id"])] == [5177, 4000]
    (u2,) = storage.find_products(conn, "u2")
    assert storage.price_history(conn, u2["id"])[0]["price_cents"] == 29  # not 28
    conn.close()


def test_database_from_a_newer_version_is_refused(tmp_path):
    path = str(tmp_path / "future.db")
    future = sqlite3.connect(path)
    future.execute("PRAGMA user_version = 99")
    future.close()
    with pytest.raises(RuntimeError, match="newer"):
        storage.connect(path)


def test_failed_migration_leaves_database_untouched(tmp_path, monkeypatch):
    path = str(tmp_path / "v1.db")
    legacy = sqlite3.connect(path)
    legacy.execute(
        "CREATE TABLE prices (id INTEGER PRIMARY KEY, url TEXT, title TEXT, "
        "price REAL, rating INTEGER, in_stock INTEGER, scraped_at TEXT)"
    )
    legacy.commit()
    legacy.close()

    def boom(conn):
        conn.execute("CREATE TABLE half_done (x)")
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setitem(storage.MIGRATIONS, 1, boom)
    with pytest.raises(sqlite3.OperationalError):
        storage.connect(path)

    check = sqlite3.connect(path)
    tables = {r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"prices"}
    assert check.execute("PRAGMA user_version").fetchone()[0] == 0
    check.close()


def test_file_database_persists_across_connections(tmp_path):
    path = str(tmp_path / "bw.db")
    first = storage.connect(path)
    ok_run(first, [book()])
    first.close()
    second = storage.connect(path)
    assert len(storage.latest(second)) == 1
    assert second.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    second.close()


def test_record_page_is_idempotent_within_a_run(conn):
    run_id = storage.start_run(conn)
    storage.record_page(conn, run_id, [book(price=1000)])
    storage.record_page(conn, run_id, [book(price=900)])
    storage.finish_run(conn, run_id, complete=True)

    (row,) = storage.latest(conn)
    assert row["price_cents"] == 900
    assert storage.get_run(conn, run_id).items == 1
    assert storage.get_run(conn, run_id).pages == 2


def test_failed_runs_are_ignored_by_reports(conn):
    ok_run(conn, [book(price=1000)])
    bad = storage.start_run(conn)
    storage.record_page(conn, bad, [book(price=1)])
    storage.finish_run(conn, bad, complete=False, error="FetchError: boom")

    (row,) = storage.latest(conn)
    assert row["price_cents"] == 1000
    (product,) = storage.find_products(conn, "A")
    assert [h["price_cents"] for h in storage.price_history(conn, product["id"])] == [1000]
    assert [r.status for r in storage.last_ok_runs(conn, 5)] == ["ok"]
    assert storage.get_run(conn, bad).error == "FetchError: boom"
    assert storage.get_run(conn, 999) is None


def test_find_products_by_url_or_title_substring(conn):
    ok_run(conn, [book("Sapiens"), book("Sapiens 2"), book("100% Dune")])

    assert len(storage.find_products(conn, "sapiens")) == 2
    assert len(storage.find_products(conn, "https://x.test/Sapiens")) == 1
    assert [r["title"] for r in storage.find_products(conn, "100%")] == ["100% Dune"]
    assert storage.find_products(conn, "_") == []


def test_failed_run_cannot_rename_products_or_invent_new_ones(conn):
    ok_run(conn, [book("A", 1000)], when="2026-01-01T00:00:00+00:00")
    failed_run(conn, [book("A RENAMED", 1, url="https://x.test/A"), book("Ghost")])

    (row,) = storage.latest(conn)
    assert row["title"] == "A"
    assert row["price_cents"] == 1000
    assert row["last_seen"] == "2026-01-01T00:00:00+00:00"
    assert storage.find_products(conn, "Ghost") == []
    assert storage.find_products(conn, "RENAMED") == []


def test_first_and_last_seen_come_from_ok_runs(conn):
    ok_run(conn, [book("A")], when="2026-01-01T00:00:00+00:00")
    ok_run(conn, [book("A")], when="2026-01-08T00:00:00+00:00")
    (row,) = storage.latest(conn)
    assert (row["first_seen"], row["last_seen"]) == (
        "2026-01-01T00:00:00+00:00",
        "2026-01-08T00:00:00+00:00",
    )


def test_migration_counts_distinct_products_per_run(tmp_path):
    path = str(tmp_path / "dupes.db")
    legacy = sqlite3.connect(path)
    legacy.executescript(
        """
        CREATE TABLE prices (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL,
            title TEXT NOT NULL, price REAL NOT NULL, rating INTEGER NOT NULL,
            in_stock INTEGER NOT NULL, scraped_at TEXT NOT NULL);
        INSERT INTO prices (url, title, price, rating, in_stock, scraped_at) VALUES
            ('u1', 'A', 1.0, 3, 1, 't1'),
            ('u1', 'A', 1.0, 3, 1, 't1'),
            ('u2', 'B', 2.0, 3, 1, 't1');
        """
    )
    legacy.commit()
    legacy.close()

    conn = storage.connect(path)
    (run,) = storage.runs(conn)
    assert run.items == 2
    conn.close()


def test_concurrent_migration_is_not_attempted_twice(tmp_path, monkeypatch):
    path = str(tmp_path / "race.db")
    storage.connect(path).close()  # another process already migrated the database

    real = storage._detect_version
    calls = []

    def stale_then_real(conn):
        calls.append(1)
        return 0 if len(calls) == 1 else real(conn)  # first read raced the other process

    monkeypatch.setattr(storage, "_detect_version", stale_then_real)
    conn = sqlite3.connect(path)
    assert storage.migrate(conn) == 2  # re-checked under the lock: nothing to do
    conn.close()
