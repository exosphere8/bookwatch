"""Fetching and parsing of listing pages."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterator
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://books.toscrape.com/catalogue/page-1.html"
RATINGS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}
USER_AGENT = "BookWatch/1.0 (+https://github.com/exosphere8/bookwatch)"


@dataclass(frozen=True)
class Book:
    title: str
    url: str
    price: float
    rating: int
    in_stock: bool


def parse_page(html: str, page_url: str) -> tuple[list[Book], str | None]:
    """Parse one listing page. Returns the books and the next page URL (or None)."""
    soup = BeautifulSoup(html, "html.parser")
    books = []
    for card in soup.select("article.product_pod"):
        link = card.select_one("h3 a")
        price_text = card.select_one("p.price_color").get_text(strip=True)
        rating_cls = next(
            (c for c in card.select_one("p.star-rating")["class"] if c in RATINGS), None
        )
        books.append(
            Book(
                title=link["title"],
                url=urljoin(page_url, link["href"]),
                price=float("".join(ch for ch in price_text if ch.isdigit() or ch == ".")),
                rating=RATINGS.get(rating_cls, 0),
                in_stock="in stock" in card.select_one("p.availability").get_text().lower(),
            )
        )
    nxt = soup.select_one("li.next a")
    return books, urljoin(page_url, nxt["href"]) if nxt else None


def fetch(url: str, session: requests.Session, retries: int = 3) -> str:
    """GET with simple exponential backoff."""
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
            resp.encoding = "utf-8"
            return resp.text
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def crawl(
    start_url: str = BASE_URL, max_pages: int = 3, delay: float = 0.5
) -> Iterator[Book]:
    """Yield books from up to `max_pages` pages, politely rate-limited."""
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    url: str | None = start_url
    for _ in range(max_pages):
        if not url:
            break
        books, url = parse_page(fetch(url, session), url)
        yield from books
        time.sleep(delay)
