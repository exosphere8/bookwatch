"""BookWatch: track product prices over time, detect changes, and send alerts.

Commands:
  scrape    crawl the catalogue and store a new run
  changes   what the latest successful run found that changed
  history   price history of one product
  runs      recent runs and their status
  export    latest known state of every product (CSV or JSON)
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Any, TextIO

from . import __version__, diff, notify, pipeline, report, storage
from .net import FetchError, PoliteClient
from .scraper import DEFAULT_START_URL

log = logging.getLogger("bookwatch")

EXIT_OK, EXIT_ERROR, EXIT_INTERRUPTED = 0, 1, 130


@contextmanager
def _connect(args: argparse.Namespace) -> Iterator[sqlite3.Connection]:
    conn = storage.connect(args.db)
    try:
        yield conn
    finally:
        conn.close()


def _say(args: argparse.Namespace, message: str) -> None:
    """Human-oriented status line on stderr; silenced by --quiet."""
    if not args.quiet:
        print(message, file=sys.stderr)


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2))


def cmd_scrape(args: argparse.Namespace) -> int:
    client = PoliteClient(delay=args.delay, timeout=args.timeout)
    with _connect(args) as conn:
        result = pipeline.scrape(conn, client, args.start_url, args.pages or None)
    scope = "full catalogue" if result.complete else f"first {result.pages} page(s)"
    _say(
        args,
        f"Run #{result.run_id}: {result.items} products from {result.pages} page(s) "
        f"({scope}) saved to {args.db}",
    )
    return EXIT_OK


def cmd_changes(args: argparse.Namespace) -> int:
    with _connect(args) as conn:
        old, new, changes = diff.latest_changes(conn, args.min_pct)
    if args.json:
        _print_json(
            {
                "from_run": old.id if old else None,
                "to_run": new.id if new else None,
                "summary": report.summarize(changes),
                "changes": [c.to_dict() for c in changes],
            }
        )
    else:
        print(report.format_changes(old, new, changes))

    webhook = args.notify or os.environ.get("BOOKWATCH_WEBHOOK_URL")
    if webhook and changes:
        notify.send(webhook, notify.build_payload(changes, args.notify_format))
        log.info("sent %d change(s) to webhook", len(changes))
    return EXIT_OK


def cmd_history(args: argparse.Namespace) -> int:
    with _connect(args) as conn:
        matches = storage.find_products(conn, args.query)
        history = storage.price_history(conn, matches[0]["id"]) if len(matches) == 1 else []

    if len(matches) != 1:
        error = (
            f"no product matches {args.query!r}"
            if not matches
            else f"{len(matches)} products match {args.query!r}; be more specific or pass the URL"
        )
        if args.json:
            _print_json(
                {
                    "error": error,
                    "matches": [{"title": m["title"], "url": m["url"]} for m in matches],
                }
            )
        else:
            print(error[0].upper() + error[1:] + ("." if not matches else ":"), file=sys.stderr)
            for m in matches[:25]:
                print(f"  {m['title']}\n    {m['url']}", file=sys.stderr)
        return EXIT_ERROR

    product = matches[0]
    if args.json:
        _print_json(
            {
                "title": product["title"],
                "url": product["url"],
                "history": [dict(h) for h in history],
            }
        )
    else:
        print(report.format_history(product, history))
    return EXIT_OK


def cmd_runs(args: argparse.Namespace) -> int:
    with _connect(args) as conn:
        print(report.format_runs(storage.runs(conn, args.limit)))
    return EXIT_OK


def _export_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        {
            "title": r["title"],
            "url": r["url"],
            "price": str(Decimal(r["price_cents"]) / 100),
            "price_cents": r["price_cents"],
            "currency": r["currency"],
            "rating": r["rating"],
            "in_stock": bool(r["in_stock"]),
            "first_seen": r["first_seen"],
            "last_seen": r["last_seen"],
        }
        for r in storage.latest(conn)
    ]


def _write_export(rows: list[dict[str, Any]], fmt: str, out: TextIO) -> None:
    if fmt == "json":
        json.dump(rows, out, indent=2)
        out.write("\n")
    elif rows:
        writer = csv.DictWriter(out, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def cmd_export(args: argparse.Namespace) -> int:
    with _connect(args) as conn:
        rows = _export_rows(conn)
    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            _write_export(rows, args.format, f)
        _say(args, f"Exported {len(rows)} products to {args.out}")
    else:
        if isinstance(sys.stdout, io.TextIOWrapper):
            sys.stdout.reconfigure(newline="")  # the csv module writes its own \r\n
        _write_export(rows, args.format, sys.stdout)
    return EXIT_OK


def _non_negative_int(value: str) -> int:
    n = int(value)
    if n < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return n


def _non_negative_float(value: str) -> float:
    x = float(value)
    if not x >= 0:  # also rejects NaN
        raise argparse.ArgumentTypeError("must be >= 0")
    return x


def _positive_float(value: str) -> float:
    x = float(value)
    if not x > 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return x


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bookwatch", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument(
        "--db",
        default=os.environ.get("BOOKWATCH_DB", "bookwatch.db"),
        help="SQLite database path (env: BOOKWATCH_DB; default: bookwatch.db)",
    )
    verbosity = p.add_mutually_exclusive_group()
    verbosity.add_argument("-v", "--verbose", action="store_true", help="log progress")
    verbosity.add_argument(
        "-q", "--quiet", action="store_true", help="no status lines; only reports and errors"
    )
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    s = sub.add_parser("scrape", help="crawl the catalogue and store a new run")
    s.add_argument(
        "--pages",
        type=_non_negative_int,
        default=3,
        help="maximum pages to crawl; 0 = the whole catalogue (default: 3)",
    )
    s.add_argument(
        "--delay",
        type=_non_negative_float,
        default=1.0,
        help="minimum seconds between requests; robots.txt Crawl-delay wins if "
        "larger (default: 1.0)",
    )
    s.add_argument(
        "--timeout", type=_positive_float, default=15.0, help="per-request timeout in seconds"
    )
    s.add_argument("--start-url", default=DEFAULT_START_URL, help="first listing page")
    s.set_defaults(func=cmd_scrape)

    c = sub.add_parser("changes", help="what the latest successful run found that changed")
    c.add_argument(
        "--min-pct",
        type=_non_negative_float,
        default=0.0,
        metavar="PCT",
        help="ignore price moves smaller than PCT percent",
    )
    c.add_argument("--json", action="store_true", help="machine-readable output")
    c.add_argument(
        "--notify",
        metavar="WEBHOOK_URL",
        help="POST changes to a webhook (env: BOOKWATCH_WEBHOOK_URL)",
    )
    c.add_argument("--notify-format", choices=notify.FORMATS, default="slack")
    c.set_defaults(func=cmd_changes)

    h = sub.add_parser("history", help="price history of one product")
    h.add_argument("query", help="product URL, or part of its title")
    h.add_argument("--json", action="store_true", help="machine-readable output")
    h.set_defaults(func=cmd_history)

    r = sub.add_parser("runs", help="recent runs and their status")
    r.add_argument("--limit", type=_non_negative_int, default=20)
    r.set_defaults(func=cmd_runs)

    e = sub.add_parser("export", help="latest known state of every product")
    e.add_argument("--format", choices=["csv", "json"], default="csv")
    e.add_argument("--out", help="output file (default: stdout)")
    e.set_defaults(func=cmd_export)
    return p


def _is_console(stream: io.TextIOWrapper) -> bool:
    """A real interactive console. On Windows the NUL device also claims to be a TTY."""
    if sys.platform == "win32":
        raw = getattr(stream.buffer, "raw", None)
        return type(raw).__name__ == "_WindowsConsoleIO"
    return stream.isatty()


def _configure_streams() -> None:
    """Never crash on output encoding.

    Redirected output on Windows defaults to the ANSI code page, which cannot encode the
    sparkline or many titles: write UTF-8 to files, pipes and NUL, and replace unencodable
    characters on interactive consoles.
    """
    for stream in (sys.stdout, sys.stderr):
        if not isinstance(stream, io.TextIOWrapper):
            continue
        if _is_console(stream):
            stream.reconfigure(errors="replace")
        else:
            stream.reconfigure(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_streams()
    level = logging.INFO if args.verbose else logging.ERROR if args.quiet else logging.WARNING
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
    try:
        code: int = args.func(args)
    except (FetchError, notify.NotifyError, RuntimeError, OSError) as exc:
        log.error("%s", exc)
        return EXIT_ERROR
    except sqlite3.Error as exc:
        log.error("database %s: %s", args.db, exc)
        return EXIT_ERROR
    except KeyboardInterrupt:
        log.error("interrupted")
        return EXIT_INTERRUPTED
    return code


if __name__ == "__main__":
    sys.exit(main())
