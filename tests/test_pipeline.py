import pytest

from bookwatch import pipeline, storage
from bookwatch.net import FetchError
from conftest import PAGE_1, PAGE_2, fixture_html


def test_full_crawl_follows_pagination_and_marks_run_complete(conn, make_client, site):
    result = pipeline.scrape(conn, make_client(), PAGE_1)

    assert result.pages == 2
    assert result.items == 3  # 2 + 1; the malformed card on page 2 is skipped
    assert result.complete is True
    run = storage.get_run(conn, result.run_id)
    assert (run.status, run.complete, run.items) == ("ok", True, 3)
    assert site.urls().count(PAGE_2) == 1


def test_page_limit_produces_a_partial_run(conn, make_client):
    result = pipeline.scrape(conn, make_client(), PAGE_1, max_pages=1)
    assert (result.pages, result.items, result.complete) == (1, 2, False)
    assert storage.get_run(conn, result.run_id).status == "ok"


def test_pagination_loop_is_detected(conn, make_client, site):
    looping = fixture_html("page-2.html").replace(
        "</ol>", '</ol><ul class="pager"><li class="next"><a href="page-1.html">next</a></li></ul>'
    )
    site.route(PAGE_2, (200, looping, {}))

    result = pipeline.scrape(conn, make_client(), PAGE_1)
    assert (result.pages, result.complete) == (2, False)


def test_failure_mid_crawl_marks_run_failed_and_reraises(conn, make_client, site):
    site.route(PAGE_2, (404, "", {}))

    with pytest.raises(FetchError):
        pipeline.scrape(conn, make_client(), PAGE_1)

    (run,) = storage.runs(conn)
    assert run.status == "failed"
    assert "HTTP 404" in run.error
    assert storage.latest(conn) == []  # partial data never reaches reports


def test_zero_products_is_treated_as_a_broken_scraper(conn, make_client, site):
    site.route(PAGE_1, (200, "<html><body>We moved!</body></html>", {}))

    with pytest.raises(pipeline.EmptyScrapeError, match="selectors"):
        pipeline.scrape(conn, make_client(), PAGE_1)
    assert storage.runs(conn)[0].status == "failed"


def test_relative_links_resolve_against_the_url_after_redirects(conn, make_client, site):
    start = "https://books.toscrape.com/catalogue"  # no trailing slash: server redirects
    site.route(start, (301, "", {"Location": "/catalogue/"}))
    site.route("https://books.toscrape.com/catalogue/", (200, fixture_html("page.html"), {}))

    result = pipeline.scrape(conn, make_client(), start)

    assert result.pages == 2  # page-2.html resolved under /catalogue/, not the site root
    urls = {row["url"] for row in storage.latest(conn)}
    assert "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html" in urls
