import pytest
import requests

from bookwatch.net import MAX_REDIRECTS, USER_AGENT, FetchError, RobotsDisallowed
from conftest import BASE, PAGE_1

ROBOTS = f"{BASE}/robots.txt"


def test_get_returns_utf8_text_and_identifies_itself(site, make_client):
    site.route(PAGE_1, (200, "<p>£51.77</p>", {"Content-Type": "text/html"}))
    client = make_client()

    page = client.get(PAGE_1)
    assert page.text == "<p>£51.77</p>"
    assert page.url == PAGE_1
    assert site.requests[-1].headers["User-Agent"] == USER_AGENT


def test_robots_disallow_is_enforced(site, make_client):
    with pytest.raises(RobotsDisallowed):
        make_client().get(f"{BASE}/admin/secret.html")
    assert f"{BASE}/admin/secret.html" not in site.urls()


def test_robots_is_fetched_once_per_origin(site, make_client):
    client = make_client()
    client.get(PAGE_1)
    client.get(PAGE_1)
    assert site.urls().count(ROBOTS) == 1


def test_missing_robots_allows_everything(site, make_client):
    site.route(ROBOTS, (404, "", {}))
    assert make_client().get(PAGE_1)


@pytest.mark.parametrize(
    "reply", [(503, "", {}), requests.ConnectionError("unreachable")], ids=["5xx", "network"]
)
def test_unreachable_robots_disallows_everything(site, make_client, reply):
    site.route(ROBOTS, reply)
    with pytest.raises(RobotsDisallowed):
        make_client().get(PAGE_1)


def test_minimum_delay_between_requests_to_same_host(make_client, fake_time):
    client = make_client(delay=2.0)
    client.get(PAGE_1)
    client.get(PAGE_1)
    assert fake_time.sleeps == [2.0]


def test_robots_crawl_delay_wins_when_larger(site, make_client, fake_time):
    site.route(ROBOTS, (200, "User-agent: *\nCrawl-delay: 5\n", {}))
    client = make_client(delay=1.0)
    client.get(PAGE_1)
    client.get(PAGE_1)
    assert fake_time.sleeps == [5.0]


def test_transient_errors_are_retried_with_backoff(site, make_client, fake_time):
    site.route(PAGE_1, (503, "", {}), requests.ConnectionError("reset"), (200, "ok", {}))
    client = make_client()

    assert client.get(PAGE_1).text == "ok"
    assert client.requests_made == 3
    assert fake_time.sleeps == [1.0, 2.0]


def test_retry_after_header_is_honoured_and_capped(site, make_client, fake_time):
    site.route(
        PAGE_1, (429, "", {"Retry-After": "7"}), (429, "", {"Retry-After": "999"}), (200, "ok", {})
    )
    make_client().get(PAGE_1)
    assert fake_time.sleeps == [7.0, 60.0]


def test_retry_after_http_date_in_the_past_means_no_wait(site, make_client, fake_time):
    site.route(PAGE_1, (503, "", {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}), (200, "ok", {}))
    make_client().get(PAGE_1)
    assert fake_time.sleeps == [1.0]  # 0 s from the header falls back to normal backoff


def test_client_errors_fail_fast(site, make_client):
    site.route(PAGE_1, (404, "", {}))
    client = make_client()
    with pytest.raises(FetchError, match="HTTP 404"):
        client.get(PAGE_1)
    assert client.requests_made == 1


def test_gives_up_after_configured_retries(site, make_client):
    site.route(PAGE_1, requests.Timeout("slow"))
    client = make_client(retries=2)
    with pytest.raises(FetchError, match="after 3 attempts"):
        client.get(PAGE_1)
    assert client.requests_made == 3


def test_truncated_body_is_retried(site, make_client):
    site.route(PAGE_1, requests.exceptions.ChunkedEncodingError("cut"), (200, "ok", {}))
    client = make_client()
    assert client.get(PAGE_1).text == "ok"
    assert client.requests_made == 2


def test_other_request_errors_become_fetch_errors(site, make_client):
    site.route(PAGE_1, requests.exceptions.InvalidURL("bad"))
    with pytest.raises(FetchError, match="InvalidURL"):
        make_client().get(PAGE_1)


def test_redirects_are_followed_and_final_url_reported(site, make_client):
    target = f"{BASE}/catalogue/index.html"
    site.route(PAGE_1, (301, "", {"Location": "/catalogue/index.html"}))
    site.route(target, (200, "here", {}))

    page = make_client().get(PAGE_1)
    assert (page.url, page.text) == (target, "here")


def test_redirect_into_a_disallowed_path_is_refused(site, make_client):
    site.route(PAGE_1, (302, "", {"Location": "/admin/secret.html"}))
    site.route(f"{BASE}/admin/secret.html", (200, "SECRET", {}))

    with pytest.raises(RobotsDisallowed):
        make_client().get(PAGE_1)
    assert f"{BASE}/admin/secret.html" not in site.urls()


def test_cross_host_redirect_checks_the_other_hosts_robots(site, make_client):
    other = "https://other.example"
    site.route(PAGE_1, (302, "", {"Location": f"{other}/private/x"}))
    site.route(f"{other}/robots.txt", (200, "User-agent: *\nDisallow: /\n", {}))

    with pytest.raises(RobotsDisallowed):
        make_client().get(PAGE_1)
    assert f"{other}/robots.txt" in site.urls()
    assert f"{other}/private/x" not in site.urls()


def test_redirect_loops_are_bounded(site, make_client):
    site.route(PAGE_1, (302, "", {"Location": PAGE_1}))
    client = make_client()
    with pytest.raises(FetchError, match="redirects"):
        client.get(PAGE_1)
    assert client.requests_made == MAX_REDIRECTS + 1


def test_fractional_crawl_delay_is_honoured(site, make_client, fake_time):
    site.route(ROBOTS, (200, "User-agent: *\nCrawl-delay: 2.5\n", {}))
    client = make_client(delay=1.0)
    client.get(PAGE_1)
    client.get(PAGE_1)
    assert fake_time.sleeps == [2.5]
