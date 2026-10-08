"""A small RFC 9309 robots.txt parser.

The standard library's ``urllib.robotparser`` only gained longest-match semantics in recent
Python versions and still ignores ``*`` / ``$`` wildcards and fractional ``Crawl-delay``
values. Politeness should not depend on the interpreter version, so BookWatch implements
the rules itself:

* groups are selected by case-insensitive product-token match (``BookWatch``), falling back
  to ``*``; several groups for the same agent are merged;
* the most specific (longest) matching rule wins, and ``Allow`` wins a tie;
* ``*`` matches any sequence of characters and a trailing ``$`` anchors the end;
* ``/robots.txt`` itself is always allowed;
* ``Crawl-delay`` (non-standard but widely used) is read as a non-negative number.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import urlsplit

MAX_CRAWL_DELAY = 120.0


@dataclass
class _Group:
    agents: list[str] = field(default_factory=list)
    rules: list[tuple[bool, str]] = field(default_factory=list)  # (allow, path pattern)
    crawl_delay: float | None = None


@lru_cache(maxsize=1024)
def _compile(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = ".*".join(re.escape(part) for part in body.split("*"))
    return re.compile(regex + (r"\Z" if anchored else ""))


def _parse_delay(value: str) -> float | None:
    try:
        delay = float(value)
    except ValueError:
        return None
    if not math.isfinite(delay) or delay < 0:
        return None
    return min(delay, MAX_CRAWL_DELAY)


def product_token(user_agent: str) -> str:
    """``"BookWatch/2.0 (+https://...)"`` -> ``"bookwatch"``."""
    return user_agent.split("/", 1)[0].split()[0].strip().lower()


class RobotsTxt:
    def __init__(self, groups: list[_Group], *, default_allow: bool = True) -> None:
        self._groups = groups
        self._default_allow = default_allow

    @classmethod
    def allow_all(cls) -> RobotsTxt:
        return cls([], default_allow=True)

    @classmethod
    def disallow_all(cls) -> RobotsTxt:
        return cls([], default_allow=False)

    @classmethod
    def parse(cls, text: str) -> RobotsTxt:
        groups: list[_Group] = []
        current: _Group | None = None
        in_agent_lines = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (part.strip() for part in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if current is None or not in_agent_lines:
                    current = _Group()
                    groups.append(current)
                current.agents.append(value.lower())
                in_agent_lines = True
                continue
            in_agent_lines = False
            if current is None:
                continue  # rules before any user-agent line belong to no group
            if key in ("allow", "disallow") and value:
                current.rules.append((key == "allow", value))
            elif key == "crawl-delay":
                current.crawl_delay = _parse_delay(value)
        return cls(groups)

    def _matching_groups(self, token: str) -> list[_Group]:
        specific = [g for g in self._groups if token in g.agents]
        return specific or [g for g in self._groups if "*" in g.agents]

    def can_fetch(self, url: str, user_agent: str) -> bool:
        parts = urlsplit(url)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        if path == "/robots.txt":
            return True
        groups = self._matching_groups(product_token(user_agent))
        if not groups:
            return self._default_allow
        best_len, best_allow = -1, True
        for group in groups:
            for allow, pattern in group.rules:
                if _compile(pattern).match(path):
                    length = len(pattern)
                    if length > best_len or (length == best_len and allow):
                        best_len, best_allow = length, allow
        return best_allow

    def crawl_delay(self, user_agent: str) -> float | None:
        delays = [
            g.crawl_delay
            for g in self._matching_groups(product_token(user_agent))
            if g.crawl_delay is not None
        ]
        return max(delays) if delays else None
