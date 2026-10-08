"""The scrape pipeline: crawl -> parse -> store, recorded as a run."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from . import storage
from .net import PoliteClient
from .scraper import parse_page

log = logging.getLogger(__name__)


class EmptyScrapeError(RuntimeError):
    """The crawl succeeded but parsed nothing: the site's markup has probably changed."""


@dataclass(frozen=True)
class ScrapeResult:
    run_id: int
    pages: int
    items: int
    complete: bool


def scrape(
    conn: sqlite3.Connection, client: PoliteClient, start_url: str, max_pages: int | None = None
) -> ScrapeResult:
    """Crawl from ``start_url`` following "next" links, storing each page as it arrives.

    ``max_pages=None`` crawls until the last page. The run is marked ``complete`` only if
    the crawl reached the end of the catalogue. Any failure marks the run ``failed`` (with
    the error recorded) and re-raises; reports ignore failed runs.
    """
    run_id = storage.start_run(conn)
    pages = items = 0
    seen: set[str] = set()
    url: str | None = start_url
    complete = False
    try:
        while url:
            if max_pages is not None and pages >= max_pages:
                break
            if url in seen:
                log.warning("pagination loops back to %s; stopping", url)
                break
            seen.add(url)
            page = client.get(url)
            seen.add(page.url)  # after a redirect, links are relative to the final URL
            books, next_url = parse_page(page.text, page.url)
            items += storage.record_page(conn, run_id, books)
            pages += 1
            log.info("page %d: %d products (%s)", pages, len(books), url)
            url = next_url
        else:
            complete = True
        if items == 0:
            raise EmptyScrapeError(
                f"parsed 0 products from {pages} page(s): selectors in scraper.parse_page "
                "may no longer match the site"
            )
    except BaseException as exc:
        storage.finish_run(conn, run_id, complete=False, error=f"{type(exc).__name__}: {exc}")
        raise
    storage.finish_run(conn, run_id, complete=complete)
    return ScrapeResult(run_id, pages, items, complete)
