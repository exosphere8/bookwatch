"""Parsing of listing pages into typed records.

This module is the only site-specific part of BookWatch: to track another catalogue,
change the CSS selectors in :func:`parse_page`. Everything downstream (history,
diffing, export, alerts) works on :class:`Book` records and stays unchanged.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

log = logging.getLogger(__name__)

DEFAULT_START_URL = "https://books.toscrape.com/catalogue/page-1.html"
RATINGS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}
CURRENCY_SYMBOLS = {"£": "GBP", "$": "USD", "€": "EUR"}
CURRENCY_CODES = frozenset(CURRENCY_SYMBOLS.values())
DEFAULT_CURRENCY = "GBP"
# UTF-8 text mis-decoded as Latin-1/cp1252 is common on scraped pages: "£" arrives as
# U+00C2 U+00A3 and "€" as U+00E2 U+201A U+00AC.
_MOJIBAKE = {"\u00c2\u00a3": "£", "\u00e2\u201a\u00ac": "€"}

# Strict: an optional currency marker before *or* after an amount with at most 2 decimals.
# Thousands separators must be well-formed (1,299.99); anything else is rejected rather
# than guessed, because "12,50" means 12.50 in some locales and 1,250 in others.
_AMOUNT = r"(?:\d{1,3}(?:,\d{3}){1,4}|\d{1,12})?(?:\.\d{1,2})?"
_MARKER = r"[£$€]|[A-Z]{3}"
_PRICE = re.compile(rf"(?P<pre>{_MARKER})?\s*(?P<amount>{_AMOUNT})\s*(?P<post>{_MARKER})?")


@dataclass(frozen=True)
class Book:
    title: str
    url: str
    price_cents: int
    currency: str
    rating: int
    in_stock: bool

    @property
    def price(self) -> Decimal:
        return Decimal(self.price_cents) / 100


class ParseError(ValueError):
    """A listing card could not be turned into a :class:`Book`."""


def parse_price(text: str) -> tuple[int, str]:
    """Parse a display price such as ``"£51.77"`` into ``(5177, "GBP")``.

    Money is kept as integer minor units end to end: binary floats cannot represent
    most decimal prices exactly, and equality checks on them are unreliable. Prices that
    cannot be read unambiguously raise :class:`ParseError` instead of being guessed.
    """
    cleaned = text.strip()
    for bad, good in _MOJIBAKE.items():
        cleaned = cleaned.replace(bad, good)
    match = _PRICE.fullmatch(cleaned)
    if not match or not any(ch.isdigit() for ch in match["amount"]):
        raise ParseError(f"unrecognised price {text!r}")
    if match["pre"] and match["post"]:
        raise ParseError(f"two currency markers in {text!r}")
    marker = match["pre"] or match["post"]
    currency = CURRENCY_SYMBOLS.get(marker, marker) if marker else DEFAULT_CURRENCY
    if currency not in CURRENCY_CODES:
        raise ParseError(f"unsupported currency {marker!r} in {text!r}")
    try:
        cents = int(Decimal(match["amount"].replace(",", "")) * 100)
    except ArithmeticError as exc:  # pragma: no cover - the regex bounds the size
        raise ParseError(f"bad amount in {text!r}") from exc
    return cents, currency


def _parse_card(card: Tag, page_url: str) -> Book:
    link = card.select_one("h3 a")
    price = card.select_one("p.price_color")
    stars = card.select_one("p.star-rating")
    availability = card.select_one("p.availability")
    if link is None or price is None or not link.get("href"):
        raise ParseError("card is missing its link or price")

    title = str(link.get("title") or link.get_text(strip=True))
    classes: list[str] = list(stars.get("class") or []) if stars else []
    rating_class = next((c for c in classes if c in RATINGS), None)
    price_cents, currency = parse_price(price.get_text(strip=True))
    return Book(
        title=title,
        url=urljoin(page_url, str(link["href"])),
        price_cents=price_cents,
        currency=currency,
        rating=RATINGS.get(rating_class or "", 0),
        in_stock=availability is not None and "in stock" in availability.get_text().lower(),
    )


def parse_page(html: str, page_url: str) -> tuple[list[Book], str | None]:
    """Parse one listing page. Returns its books and the absolute next-page URL (or None).

    A malformed card is logged and skipped: one broken listing should not lose the page.
    """
    soup = BeautifulSoup(html, "html.parser")
    books: list[Book] = []
    for index, card in enumerate(soup.select("article.product_pod"), start=1):
        try:
            books.append(_parse_card(card, page_url))
        except ParseError as exc:
            log.warning("skipping card %d on %s: %s", index, page_url, exc)
    nxt = soup.select_one("li.next a")
    next_url = urljoin(page_url, str(nxt["href"])) if nxt and nxt.get("href") else None
    return books, next_url
