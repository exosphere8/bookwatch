# Contributing

Thanks for taking the time to improve BookWatch.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Before opening a pull request

```bash
ruff check src tests
ruff format src tests
mypy
pytest -W error --cov
```

CI runs the same checks on Linux, Windows and macOS.

## Guidelines

- **Tests stay offline.** Use the `site` fixture (a fake HTTP transport) and `fake_time`
  (a fake clock) from `tests/conftest.py`. No test should touch the network or really sleep.
- **Money is integer cents.** Never store or compare prices as floats.
- **Schema changes need a migration.** Bump `SCHEMA_VERSION`, add a function to `MIGRATIONS`,
  and add a test that upgrades a database from the previous version.
- **Stay polite.** Changes must not weaken robots.txt handling, rate limiting or backoff.
- Keep pull requests focused, and describe the *why* in the commit message.
