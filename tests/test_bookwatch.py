from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from bookwatch import cli, scraper, storage

HTML = (Path(__file__).parent / "fixtures" / "page.html").read_text(encoding="utf-8")
URL = "https://books.toscrape.com/catalogue/page-1.html"


def test_parse_page():
    books, nxt = scraper.parse_page(HTML, URL)
    assert len(books) == 2
    assert books[0].title == "A Light in the Attic"
    assert books[0].price == 51.77
    assert books[0].rating == 3
    assert books[0].in_stock is True
    assert books[1].in_stock is False
    assert books[0].url == "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
    assert nxt == "https://books.toscrape.com/catalogue/page-2.html"


def test_change_detection():
    conn = storage.connect(":memory:")
    books, _ = scraper.parse_page(HTML, URL)
    storage.save(conn, books, datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert storage.changes(conn) == []
    cheaper = [replace(b, price=40.00) if b.rating == 3 else b for b in books]
    storage.save(conn, cheaper, datetime(2026, 1, 2, tzinfo=timezone.utc))
    (change,) = storage.changes(conn)
    assert change.old_price == 51.77 and change.new_price == 40.00
    assert change.pct < 0
    assert len(storage.latest(conn)) == 2


def test_export_csv(tmp_path, capsys):
    db = str(tmp_path / "t.db")
    books, _ = scraper.parse_page(HTML, URL)
    storage.save(storage.connect(db), books)
    assert cli.main(["--db", db, "export"]) == 0
    out = capsys.readouterr().out
    assert "A Light in the Attic" in out and out.startswith("id,url,title")
