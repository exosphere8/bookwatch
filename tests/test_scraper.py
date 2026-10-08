import logging
from decimal import Decimal

import pytest

from bookwatch.scraper import ParseError, parse_page, parse_price
from conftest import PAGE_1, PAGE_2, fixture_html


def test_parse_page_extracts_typed_books_and_next_link():
    books, nxt = parse_page(fixture_html("page.html"), PAGE_1)

    assert [b.title for b in books] == ["A Light in the Attic", "Tipping the Velvet"]
    first = books[0]
    assert first.price_cents == 5177
    assert first.price == Decimal("51.77")
    assert first.currency == "GBP"
    assert first.rating == 3
    assert first.in_stock is True
    assert first.url == "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
    assert books[1].in_stock is False
    assert books[1].rating == 1
    assert nxt == PAGE_2


def test_malformed_card_is_skipped_not_fatal(caplog):
    with caplog.at_level(logging.WARNING):
        books, nxt = parse_page(fixture_html("page-2.html"), PAGE_2)

    assert [b.title for b in books] == ["Soumission"]
    assert nxt is None
    assert "skipping card 2" in caplog.text


def test_page_without_products_returns_empty_list():
    assert parse_page("<html><body>maintenance</body></html>", PAGE_1) == ([], None)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("£51.77", (5177, "GBP")),
        ("Â£12.50", (1250, "GBP")),  # mis-decoded pound sign
        ("$1,299.99", (129999, "USD")),
        ("€0.99", (99, "EUR")),
        ("$.99", (99, "USD")),
        ("12", (1200, "GBP")),
        (" £ 7.1 ", (710, "GBP")),
        ("USD 12.00", (1200, "USD")),
        ("12.00 €", (1200, "EUR")),
    ],
)
def test_parse_price_returns_integer_minor_units(text, expected):
    assert parse_price(text) == expected


def test_parse_price_is_exact_where_floats_are_not():
    # 0.29 * 100 == 28.999999999999996 in binary floating point
    assert parse_price("£0.29") == (29, "GBP")


@pytest.mark.parametrize(
    "text",
    [
        "free",
        "",
        "£",
        "12.",
        "£1.999",  # more precision than the currency has
        "12,50 €",  # decimal comma: 12.50 or 1250? refuse to guess
        "€1.234,56",
        "CA$5.00",  # unsupported currency
        "R$ 10,00",
        "CHF 12.00",
        "£12 €",
        "£" + "9" * 30,
    ],
)
def test_parse_price_rejects_ambiguous_or_unsupported_text(text):
    with pytest.raises(ParseError):
        parse_price(text)
