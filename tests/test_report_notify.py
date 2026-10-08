import json

import pytest
import requests

from bookwatch import notify, report
from bookwatch.diff import Change
from bookwatch.storage import Run


def change(kind="price_drop", title="A Light in the Attic", old=5177, new=4000):
    return Change(kind, title, f"https://x.test/{title}", "GBP", old, new)


@pytest.mark.parametrize(
    ("cents", "currency", "expected"),
    [
        (5177, "GBP", "£51.77"),
        (123456, "USD", "$1,234.56"),
        (1200, "CHF", "12.00 CHF"),
        (None, "GBP", "-"),
    ],
)
def test_money(cents, currency, expected):
    assert report.money(cents, currency) == expected


def test_sparkline():
    assert report.sparkline([100, 200, 300]) == "▁▅█"
    assert report.sparkline([5, 5]) == "▄▄"
    assert report.sparkline([]) == ""


def test_format_change_for_each_kind():
    assert report.format_change(change()).startswith("DROP")
    assert "(-22.7%)" in report.format_change(change())
    assert "last seen at £51.77" in report.format_change(change("removed", new=None))
    assert report.format_change(change("new", old=None)).startswith("NEW")
    long_title = report.format_change(change(title="x" * 80))
    assert "…" in long_title


def test_format_changes_needs_two_runs_and_reports_no_changes():
    run = Run(1, "2026-01-01T00:00:00+00:00", None, "ok", True, 1, 1, None)
    assert "at least two" in report.format_changes(None, run, [])
    assert report.format_changes(run, run, []).endswith("No changes.")


def test_summarize_counts_by_kind():
    changes = [change(), change(), change("new", old=None)]
    assert report.summarize(changes) == "2 drop, 1 new"
    assert report.summarize([]) == "no changes"


def test_format_runs_shows_status_and_notes():
    runs = [
        Run(2, "t2", "t2", "failed", False, 1, 2, "FetchError: HTTP 503"),
        Run(1, "t1", "t1", "ok", True, 50, 1000, None),
    ]
    text = report.format_runs(runs)
    assert "FetchError: HTTP 503" in text
    assert "full catalogue" in text
    assert report.format_runs([]) == "No runs yet."


def test_slack_payload():
    payload = notify.build_payload([change()], "slack")
    assert payload["text"].startswith("BookWatch: 1 drop")


def test_discord_payload_respects_message_limit():
    payload = notify.build_payload([change(title=str(i)) for i in range(30)], "discord")
    assert len(payload["content"]) <= 2000
    assert "... and 10 more" in payload["content"]


def test_json_payload_is_structured():
    payload = notify.build_payload([change()], "json")
    assert payload["changes"][0]["pct"] == -22.74  # (4000 - 5177) / 5177
    json.dumps(payload)


def test_unknown_format_is_rejected():
    with pytest.raises(ValueError, match="unknown webhook format"):
        notify.build_payload([change()], "teams")


def test_send_posts_json(site):
    session = requests.Session()
    session.mount("https://", site)
    hook = "https://hooks.example.test/T000/B000"
    site.route(hook, (200, "ok", {}))

    notify.send(hook, {"text": "hi"}, session=session)
    assert json.loads(site.requests[-1].body) == {"text": "hi"}


@pytest.mark.parametrize("reply", [(500, "", {}), requests.ConnectionError("down")])
def test_send_errors_never_reveal_the_secret_url(site, reply):
    session = requests.Session()
    session.mount("https://", site)
    hook = "https://hooks.example.test/services/T0SECRET/B0SECRET/XXSECRETXX"
    site.route(hook, reply)

    with pytest.raises(notify.NotifyError) as excinfo:
        notify.send(hook, {"text": "hi"}, session=session)
    message = str(excinfo.value)
    assert "hooks.example.test" in message
    assert "SECRET" not in message
    assert excinfo.value.__cause__ is None


def test_discord_message_is_cut_on_line_boundaries():
    long_titles = [
        Change("price_drop", "x" * 60 + str(i), f"https://x.test/{i}", "CHF", 10000, 8100)
        for i in range(30)
    ]
    content = notify.build_payload(long_titles, "discord")["content"]
    assert len(content) <= 2000
    assert content.endswith("```")
    assert content.count("```") == 2
    assert "more" in content.splitlines()[-2]
