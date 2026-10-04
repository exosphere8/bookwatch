# BookWatch

![CI](https://github.com/exosphere8/bookwatch/actions/workflows/ci.yml/badge.svg)

A small, production-style **web scraping and price-monitoring tool** in Python. It scrapes product listings,
stores every run as a snapshot in SQLite, and reports what changed in price since the last run.
The demo target is [books.toscrape.com](https://books.toscrape.com), a sandbox site made for scraping practice.

## Features

- Pagination-aware scraper (requests + BeautifulSoup) with retries, exponential backoff, a custom User-Agent and rate limiting
- Full price history in SQLite, one snapshot per run
- Price-change report between the two latest runs, sorted by biggest drop
- Export of the latest data to CSV or JSON
- Clean CLI, offline unit tests (HTML fixtures, no network needed) and GitHub Actions CI

## Quick start

```bash
pip install -e ".[dev]"

bookwatch scrape --pages 5          # take a snapshot
bookwatch changes                   # price changes vs. the previous snapshot
bookwatch export --format csv --out books.csv
```

Example `changes` output:

```
A Light in the Attic                                51.77 -> 40.00  (-22.7%)
```

## Project layout

```
src/bookwatch/
  scraper.py   fetching, parsing, pagination
  storage.py   SQLite history + change detection
  cli.py       argparse commands: scrape / changes / export
tests/         offline tests using an HTML fixture
```

## Adapting it to your site

Only `parse_page()` in `scraper.py` is site-specific: swap the CSS selectors and the
rest (history, diffing, export, CLI) works unchanged. Typical extensions: proxy rotation,
JavaScript-rendered pages (Playwright), email/Slack alerts on price drops, scheduled runs via cron.

## Tests

```bash
pytest -q
```

## License

MIT
