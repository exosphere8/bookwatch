import json
import logging

import pytest
import requests

from bookwatch import cli, net, notify
from conftest import PAGE_1, fixture_html


@pytest.fixture
def run(tmp_path, monkeypatch, make_client, capsys):
    """Invoke the CLI against the fake site with a throwaway database."""
    monkeypatch.setattr(cli, "PoliteClient", lambda **kw: make_client(**kw))
    db = str(tmp_path / "bw.db")

    def invoke(*argv):
        code = cli.main(["--db", db, *argv])
        out = capsys.readouterr()
        return code, out.out, out.err

    return invoke


def test_scrape_whole_catalogue(run):
    code, out, err = run("scrape", "--pages", "0")
    assert code == 0
    assert out == ""  # status goes to stderr so stdout stays clean for reports
    assert "Run #1: 3 products from 2 page(s) (full catalogue)" in err


def test_quiet_scrape_prints_nothing(run):
    assert run("-q", "scrape") == (0, "", "")


def test_changes_after_a_price_drop(run, site):
    run("scrape", "--pages", "0")
    site.route(PAGE_1, (200, fixture_html("page.html").replace("£51.77", "£40.00"), {}))
    run("scrape", "--pages", "0")

    code, out, _ = run("changes")
    assert code == 0
    assert "DROP" in out
    assert "£51.77 -> £40.00" in out

    _, out, _ = run("changes", "--json")
    payload = json.loads(out)
    assert (payload["from_run"], payload["to_run"]) == (1, 2)
    assert payload["changes"][0]["new_price_cents"] == 4000


def test_changes_sends_webhook_from_environment(run, site, monkeypatch):
    hook = "https://hooks.example.test/services/T000/B000/XXXX"
    site.route(hook, (200, "ok", {}))

    real_session = requests.Session

    def session():
        s = real_session()
        s.mount("https://", site)
        return s

    monkeypatch.setattr(notify.requests, "Session", session)
    monkeypatch.setenv("BOOKWATCH_WEBHOOK_URL", hook)
    run("scrape", "--pages", "0")
    site.route(PAGE_1, (200, fixture_html("page.html").replace("£51.77", "£40.00"), {}))
    run("scrape", "--pages", "0")

    assert run("changes", "--notify-format", "discord")[0] == 0
    sent = [r for r in site.requests if r.url == hook]
    assert len(sent) == 1
    assert "1 drop" in json.loads(sent[0].body)["content"]


def test_no_webhook_call_when_nothing_changed(run, site, monkeypatch):
    monkeypatch.setenv("BOOKWATCH_WEBHOOK_URL", "https://hooks.example.test/x")
    run("scrape")
    run("scrape")
    code, out, _ = run("changes")
    assert code == 0
    assert "No changes." in out
    assert not [r for r in site.requests if "hooks" in (r.url or "")]


def test_history_single_multiple_and_missing(run):
    run("scrape", "--pages", "0")

    code, out, _ = run("history", "attic")
    assert code == 0
    assert "A Light in the Attic" in out
    assert "£51.77" in out
    assert "trend" in out

    code, out, _ = run("history", "--json", "soumission")
    assert json.loads(out)["history"][0]["price_cents"] == 5010

    code, _, err = run("history", "t")
    assert code == 1
    assert "products match" in err

    code, _, err = run("history", "no such book")
    assert code == 1
    assert "No product matches" in err


def test_history_json_errors_are_json(run):
    run("scrape", "--pages", "0")
    code, out, _ = run("history", "--json", "t")
    payload = json.loads(out)
    assert code == 1
    assert "products match" in payload["error"]
    assert len(payload["matches"]) > 1


def test_runs_and_export(run, tmp_path):
    run("scrape", "--pages", "0")

    _, out, _ = run("runs")
    assert "full catalogue" in out

    _, out, _ = run("export")
    assert out.splitlines()[0].startswith("title,url,price,price_cents,currency")
    assert "A Light in the Attic" in out

    target = tmp_path / "latest.json"
    code, _, err = run("export", "--format", "json", "--out", str(target))
    assert code == 0
    assert "Exported 3 products" in err
    rows = json.loads(target.read_text(encoding="utf-8"))
    assert {r["price"] for r in rows} >= {"51.77", "50.1"}


def test_fetch_errors_exit_nonzero_with_a_clean_message(run, site, caplog):
    site.route(PAGE_1, (403, "", {}))
    with caplog.at_level(logging.ERROR):
        code, _, _ = run("scrape")
    assert code == 1
    assert "HTTP 403" in caplog.text


def test_robots_disallowed_start_url_is_refused(run, caplog):
    with caplog.at_level(logging.ERROR):
        code, _, _ = run("scrape", "--start-url", "https://books.toscrape.com/admin/list.html")
    assert code == 1
    assert "robots.txt disallows" in caplog.text


def test_negative_pages_rejected():
    with pytest.raises(SystemExit):
        cli.main(["scrape", "--pages", "-1"])


def test_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert net.USER_AGENT.split("/")[1].split()[0] in capsys.readouterr().out


def test_unusable_database_is_a_clean_error(run, tmp_path, caplog):
    with caplog.at_level(logging.ERROR):
        code = cli.main(["--db", str(tmp_path), "runs"])  # a directory, not a database
    assert code == 1
    assert "database" in caplog.text


@pytest.mark.parametrize(
    "argv",
    [["scrape", "--timeout", "0"], ["scrape", "--delay", "-1"], ["changes", "--min-pct", "nan"]],
)
def test_invalid_numbers_are_rejected_by_the_parser(argv):
    with pytest.raises(SystemExit):
        cli.main(argv)


def test_webhook_failure_is_reported_without_the_secret(run, site, monkeypatch, caplog):
    hook = "https://hooks.example.test/services/T0SECRET/B0SECRET/XXSECRETXX"
    site.route(hook, (500, "", {}))
    real_session = requests.Session

    def session():
        s = real_session()
        s.mount("https://", site)
        return s

    monkeypatch.setattr(notify.requests, "Session", session)
    run("scrape", "--pages", "0")
    site.route(PAGE_1, (200, fixture_html("page.html").replace("£51.77", "£40.00"), {}))
    run("scrape", "--pages", "0")

    with caplog.at_level(logging.ERROR):
        code, _, _ = run("changes", "--notify", hook)
    assert code == 1
    assert "HTTP 500" in caplog.text
    assert "SECRET" not in caplog.text


def test_redirected_output_never_fails_on_encoding(tmp_path):
    """Windows writes redirected stdout in the ANSI code page unless told otherwise."""
    import subprocess
    import sys

    from bookwatch import storage
    from test_storage import book, ok_run

    db = str(tmp_path / "bw.db")
    conn = storage.connect(db)
    ok_run(conn, [book("Café ☕", 1000)])
    ok_run(conn, [book("Café ☕", 900)])
    conn.close()

    proc = subprocess.run(
        [sys.executable, "-m", "bookwatch", "--db", db, "history", "Café"],
        capture_output=True,
        check=False,
        env={k: v for k, v in __import__("os").environ.items() if not k.startswith("PYTHONIO")},
    )
    assert proc.returncode == 0, proc.stderr
    text = proc.stdout.decode("utf-8")
    assert "Café ☕" in text
    assert "█" in text or "▁" in text


def test_non_console_streams_are_switched_to_utf8(monkeypatch):
    import io
    import sys

    fake = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", fake)
    cli._configure_streams()
    assert fake.encoding == "utf-8"
