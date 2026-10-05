# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [2.0.0] - 2026-10-05

### Added
- `PoliteClient`: robots.txt enforcement on every request and redirect hop, `Crawl-delay`,
  per-host rate limiting, and retries for timeouts, truncated bodies, 429 and 5xx with exponential
  backoff and `Retry-After`.
- A built-in RFC 9309 robots.txt parser (longest match, Allow wins ties, `*` and `$`
  wildcards, fractional `Crawl-delay`) that behaves the same on every Python version.
- Run tracking: every scrape is a run with status, page and item counts, completeness and error.
- Change detection for price drops and rises, restocks, sell-outs, new and delisted products.
  Each product is compared with its own last successful sighting and each change is reported
  once. Includes an exact `--min-pct` noise filter and JSON output.
- `history` command with a sparkline, and a `runs` command.
- Slack, Discord and raw-JSON webhook alerts (`--notify` / `BOOKWATCH_WEBHOOK_URL`). Errors
  never reveal the secret webhook URL.
- Data-quality gate: a crawl that parses zero products fails instead of storing nothing.
- Twice-weekly live-scrape GitHub Actions workflow that publishes its report to the run summary.
- Strict typing (`py.typed`, `mypy --strict`), ruff lint and format, and 129 offline tests with a
  90% coverage gate and a no-network guard. CI runs on Linux, Windows and macOS for
  Python 3.10 to 3.13.

### Changed
- **Breaking:** normalised schema v2 (products, runs, observations) with prices stored as
  integer cents. Existing v1 databases are migrated in place, transactionally, and safely
  even when two processes open the database at once.
- `export` now emits exact decimal prices plus `price_cents`, `currency`, `first_seen` and
  `last_seen`.
- A malformed listing card is logged and skipped instead of aborting the page. Ambiguous
  prices are rejected rather than guessed.
- Status lines go to stderr (silenced by `-q`), so stdout carries only reports and exports.
  Output is UTF-8 when redirected, and CSV on stdout no longer gains blank lines on Windows.
- Requires Python 3.10+.

## [1.0.0] - 2026-10-04

### Added
- Initial release: paginated scraper, SQLite price snapshots, change report, CSV/JSON export.

[2.0.0]: https://github.com/exosphere8/bookwatch/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/exosphere8/bookwatch/releases/tag/v1.0.0
