"""Offline test harness: a fake website mounted into requests, and a fake clock."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

from bookwatch import storage
from bookwatch.net import PoliteClient

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://books.toscrape.com"
PAGE_1 = f"{BASE}/catalogue/page-1.html"
PAGE_2 = f"{BASE}/catalogue/page-2.html"

Reply = tuple[int, str, dict[str, str]] | Exception


def fixture_html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeSite(BaseAdapter):
    """A requests transport adapter serving canned responses.

    ``routes[url]`` is a reply ``(status, body, headers)``, an exception to raise, or a list of
    those consumed one per request (the last one repeats).
    """

    def __init__(self) -> None:
        super().__init__()
        self.routes: dict[str, Reply | list[Reply]] = {}
        self.requests: list[requests.PreparedRequest] = []

    def route(self, url: str, *replies: Reply) -> None:
        self.routes[url] = list(replies) if len(replies) > 1 else replies[0]

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        self.requests.append(request)
        entry = self.routes.get(request.url or "", (404, "not found", {}))
        queue = entry if isinstance(entry, list) else [entry]
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        status, body, headers = reply
        resp = requests.Response()
        resp.status_code = status
        resp._content = body.encode("utf-8")
        resp.headers = CaseInsensitiveDict({"Content-Type": "text/html", **headers})
        resp.url = request.url or ""
        resp.request = request
        return resp

    def close(self) -> None:
        pass

    def urls(self) -> list[str]:
        return [r.url or "" for r in self.requests]


@dataclass
class FakeTime:
    now: float = 1000.0
    sleeps: list[float] = field(default_factory=list)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def clock(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may read the developer's environment or touch the real network."""
    monkeypatch.delenv("BOOKWATCH_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("BOOKWATCH_DB", raising=False)

    def no_network(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("tests must not use the real network; mount FakeSite instead")

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", no_network)


@pytest.fixture
def site() -> FakeSite:
    s = FakeSite()
    s.route(f"{BASE}/robots.txt", (200, "User-agent: *\nDisallow: /admin/\n", {}))
    s.route(PAGE_1, (200, fixture_html("page.html"), {}))
    s.route(PAGE_2, (200, fixture_html("page-2.html"), {}))
    return s


@pytest.fixture
def fake_time() -> FakeTime:
    return FakeTime()


@pytest.fixture
def make_client(site: FakeSite, fake_time: FakeTime) -> Callable[..., PoliteClient]:
    def factory(**kwargs: Any) -> PoliteClient:
        session = requests.Session()
        session.mount("https://", site)
        session.mount("http://", site)
        kwargs.setdefault("delay", 0.0)
        return PoliteClient(session=session, sleep=fake_time.sleep, clock=fake_time.clock, **kwargs)

    return factory


@pytest.fixture
def conn() -> Any:
    c = storage.connect(":memory:")
    yield c
    c.close()
