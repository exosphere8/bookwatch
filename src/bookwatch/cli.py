"""Command-line interface."""
from __future__ import annotations

import argparse
import csv
import json
import sys

from . import __version__, scraper, storage


def cmd_scrape(args: argparse.Namespace) -> int:
    conn = storage.connect(args.db)
    books = list(scraper.crawl(max_pages=args.pages, delay=args.delay))
    n = storage.save(conn, books)
    print(f"Saved {n} products to {args.db}")
    return 0


def cmd_changes(args: argparse.Namespace) -> int:
    found = storage.changes(storage.connect(args.db))
    if not found:
        print("No price changes between the last two scrapes.")
        return 0
    for c in found:
        print(f"{c.title[:50]:<50}  {c.old_price:.2f} -> {c.new_price:.2f}  ({c.pct:+.1f}%)")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    rows = [dict(r) for r in storage.latest(storage.connect(args.db))]
    out = open(args.out, "w", newline="", encoding="utf-8") if args.out else sys.stdout
    try:
        if args.format == "json":
            json.dump(rows, out, indent=2)
        elif rows:
            writer = csv.DictWriter(out, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
    finally:
        if args.out:
            out.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bookwatch", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--db", default="bookwatch.db", help="SQLite database path")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scrape", help="scrape listings and store a snapshot")
    s.add_argument("--pages", type=int, default=3)
    s.add_argument("--delay", type=float, default=0.5, help="seconds between requests")
    s.set_defaults(func=cmd_scrape)

    c = sub.add_parser("changes", help="show price changes since the previous scrape")
    c.set_defaults(func=cmd_changes)

    e = sub.add_parser("export", help="export the latest snapshot")
    e.add_argument("--format", choices=["csv", "json"], default="csv")
    e.add_argument("--out", help="output file (default: stdout)")
    e.set_defaults(func=cmd_export)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
