"""RFC 9309 semantics, independent of the Python version's urllib.robotparser."""

import pytest

from bookwatch.robots import MAX_CRAWL_DELAY, RobotsTxt, product_token

UA = "BookWatch/2.0.0 (+https://github.com/exosphere8/bookwatch)"
SITE = "https://shop.test"


def allowed(robots: str, path: str, ua: str = UA) -> bool:
    return RobotsTxt.parse(robots).can_fetch(SITE + path, ua)


@pytest.mark.parametrize(
    ("robots", "path", "expected"),
    [
        # Longest match wins, regardless of rule order.
        ("User-agent: *\nAllow: /\nDisallow: /admin/", "/admin/x.html", False),
        ("User-agent: *\nDisallow: /\nAllow: /catalogue/", "/catalogue/p.html", True),
        ("User-agent: *\nDisallow: /\nAllow: /catalogue/", "/basket", False),
        # Allow wins a tie of equal length.
        ("User-agent: *\nDisallow: /page\nAllow: /page", "/page-1.html", True),
        # '*' wildcard and '$' end anchor.
        ("User-agent: *\nDisallow: /*secret", "/admin/secret.html", False),
        ("User-agent: *\nDisallow: /*.html$", "/a.html", False),
        ("User-agent: *\nDisallow: /*.html$", "/a.html?x=1", True),
        ("User-agent: *\nDisallow: /*?sort=", "/catalogue/page-1.html?sort=price", False),
        # An empty Disallow means nothing is disallowed.
        ("User-agent: *\nDisallow:", "/anything", True),
        # Comments and unknown lines are ignored.
        (
            "# hi\nSitemap: /s.xml\nUser-agent: * # all\nDisallow: /private # no",
            "/private/x",
            False,
        ),
    ],
)
def test_rule_matching(robots, path, expected):
    assert allowed(robots, path) is expected


def test_specific_group_beats_wildcard_group():
    robots = "User-agent: *\nDisallow: /\n\nUser-agent: bookwatch\nAllow: /\n"
    assert allowed(robots, "/x") is True
    assert allowed(robots, "/x", ua="OtherBot/1.0") is False


def test_agent_matching_is_case_insensitive_and_groups_merge():
    robots = (
        "User-agent: BOOKWATCH\nDisallow: /a/\n\n"
        "User-agent: googlebot\nUser-agent: BookWatch\nDisallow: /b/\n"
    )
    assert allowed(robots, "/a/1") is False
    assert allowed(robots, "/b/1") is False
    assert allowed(robots, "/c/1") is True


def test_rules_before_any_user_agent_are_ignored():
    assert allowed("Disallow: /\nUser-agent: *\nDisallow: /x", "/y") is True


def test_robots_txt_itself_is_always_allowed():
    assert RobotsTxt.disallow_all().can_fetch(f"{SITE}/robots.txt", UA) is True
    assert RobotsTxt.disallow_all().can_fetch(f"{SITE}/", UA) is False
    assert RobotsTxt.allow_all().can_fetch(f"{SITE}/", UA) is True
    assert allowed("User-agent: other\nDisallow: /", "/x") is True  # no group applies


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2.5", 2.5),
        ("10", 10.0),
        ("-1", None),
        ("nan", None),
        ("soon", None),
        ("100000", MAX_CRAWL_DELAY),
    ],
)
def test_crawl_delay_parsing(value, expected):
    assert RobotsTxt.parse(f"User-agent: *\nCrawl-delay: {value}").crawl_delay(UA) == expected


def test_product_token():
    assert product_token(UA) == "bookwatch"
    assert product_token("curl") == "curl"
