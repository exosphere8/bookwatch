"""A polite HTTP client: robots.txt, crawl-delay, per-host rate limiting and retries.

Politeness is not optional here. Every request goes through :meth:`PoliteClient.get`, which

* checks the site's ``robots.txt`` (RFC 9309, see :mod:`bookwatch.robots`): a missing file
  (4xx) allows everything, an unreachable one (5xx / network error) disallows everything;
* follows redirects itself, so every hop is checked against robots.txt and rate-limited,
  including hops to another host;
* waits at least ``max(delay, Crawl-delay)`` seconds between requests to the same host;
* retries transient failures (connection errors, timeouts, truncated bodies, 429 and 5xx)
  with exponential backoff, honouring ``Retry-After`` when the server sends it;
* fails fast on other 4xx responses, which retrying cannot fix.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

import requests

from . import __version__
from .robots import RobotsTxt

log = logging.getLogger(__name__)

USER_AGENT = f"BookWatch/{__version__} (+https://github.com/exosphere8/bookwatch)"
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
RETRYABLE_ERRORS = (
    requests.ConnectionError,
    requests.Timeout,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ContentDecodingError,
)
MAX_RETRY_AFTER = 60.0
MAX_REDIRECTS = 5


class FetchError(RuntimeError):
    """A page could not be fetched."""


class RobotsDisallowed(FetchError):
    """robots.txt does not allow this URL for our user agent."""


@dataclass(frozen=True)
class Page:
    url: str  # final URL after redirects: the base for resolving relative links
    text: str


def _retry_after(response: requests.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        seconds = when.timestamp() - time.time()
    return max(0.0, min(seconds, MAX_RETRY_AFTER))


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


class PoliteClient:
    def __init__(
        self,
        *,
        delay: float = 1.0,
        retries: int = 4,
        timeout: float = 15.0,
        user_agent: str = USER_AGENT,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.delay = delay
        self.retries = retries
        self.timeout = timeout
        self.user_agent = user_agent
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self._sleep = sleep
        self._clock = clock
        self._robots: dict[str, RobotsTxt] = {}
        self._last_request: dict[str, float] = {}
        self.requests_made = 0

    # -- robots.txt -------------------------------------------------------------------------

    def _robots_for(self, url: str) -> RobotsTxt:
        origin = _origin(url)
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(origin)
        return self._robots[origin]

    def _load_robots(self, origin: str) -> RobotsTxt:
        robots_url = f"{origin}/robots.txt"
        try:
            resp = self.session.get(robots_url, timeout=self.timeout)
        except requests.RequestException as exc:
            log.warning("robots.txt unreachable for %s (%s): disallowing", origin, exc)
            return RobotsTxt.disallow_all()
        if resp.status_code >= 500:
            log.warning("robots.txt returned %d for %s: disallowing", resp.status_code, origin)
            return RobotsTxt.disallow_all()
        if resp.status_code >= 400:
            return RobotsTxt.allow_all()
        return RobotsTxt.parse(resp.text)

    def crawl_delay(self, url: str) -> float:
        return max(self.delay, self._robots_for(url).crawl_delay(self.user_agent) or 0.0)

    # -- requests ---------------------------------------------------------------------------

    def _wait_turn(self, url: str) -> None:
        host = urlsplit(url).netloc
        last = self._last_request.get(host)
        if last is not None:
            remaining = self.crawl_delay(url) - (self._clock() - last)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request[host] = self._clock()

    def _request(self, url: str) -> requests.Response:
        """One logical GET with retries. Returns a 2xx or 3xx response."""
        for attempt in range(self.retries + 1):
            self._wait_turn(url)
            self.requests_made += 1
            backoff = float(2**attempt)
            try:
                resp = self.session.get(url, timeout=self.timeout, allow_redirects=False)
                if resp.status_code < 400:
                    _ = resp.content  # read the body now so truncation is retried here
                    return resp
            except RETRYABLE_ERRORS as exc:
                problem = f"{type(exc).__name__}: {exc}"
            except requests.RequestException as exc:
                raise FetchError(f"GET {url} failed: {type(exc).__name__}: {exc}") from exc
            else:
                if resp.status_code not in RETRYABLE_STATUS:
                    raise FetchError(f"GET {url} -> HTTP {resp.status_code}")
                problem = f"HTTP {resp.status_code}"
                backoff = _retry_after(resp) or backoff

            if attempt == self.retries:
                raise FetchError(f"GET {url} failed after {attempt + 1} attempts ({problem})")
            log.info("GET %s: %s, retrying in %.1fs", url, problem, backoff)
            self._sleep(backoff)
        raise AssertionError("unreachable")

    def get(self, url: str) -> Page:
        """Fetch ``url`` politely, following redirects. Returns the final URL and body."""
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            if not self._robots_for(current).can_fetch(current, self.user_agent):
                raise RobotsDisallowed(f"robots.txt disallows {current}")
            resp = self._request(current)
            if resp.is_redirect:
                current = urljoin(current, resp.headers["Location"])
                log.info("redirected to %s", current)
                continue
            if "charset" not in resp.headers.get("Content-Type", "").lower():
                resp.encoding = "utf-8"  # requests would otherwise assume ISO-8859-1
            return Page(current, resp.text)
        raise FetchError(f"GET {url}: more than {MAX_REDIRECTS} redirects")
