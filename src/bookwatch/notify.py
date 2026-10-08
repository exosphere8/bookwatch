"""Webhook alerts for detected changes (Slack, Discord, or raw JSON).

The webhook URL is a secret: pass it with ``--notify`` or, better, the
``BOOKWATCH_WEBHOOK_URL`` environment variable. Never commit it, and never log it:
errors raised here name only the webhook's host.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

import requests

from .diff import Change
from .report import format_change, summarize

FORMATS = ("slack", "discord", "json")
MAX_LINES = 20
DISCORD_LIMIT = 2000


class NotifyError(RuntimeError):
    """The webhook call failed. The message never contains the (secret) URL path."""


def _message(changes: Sequence[Change], limit: int | None = None) -> str:
    """Summary plus a code block of changes, built line by line so it never exceeds ``limit``."""
    head = f"BookWatch: {summarize(changes)}\n```\n"
    tail = "```"
    lines: list[str] = []
    for index, change in enumerate(changes):
        remaining = len(changes) - index
        line = format_change(change)
        more = f"... and {remaining - 1} more\n" if remaining > 1 else ""
        candidate = head + "".join(lines) + line + "\n" + more + tail
        if index >= MAX_LINES or (limit is not None and len(candidate) > limit):
            lines.append(f"... and {remaining} more\n")
            break
        lines.append(line + "\n")
    return head + "".join(lines) + tail


def build_payload(changes: Sequence[Change], fmt: str = "slack") -> dict[str, Any]:
    if fmt not in FORMATS:
        raise ValueError(f"unknown webhook format {fmt!r}; choose from {FORMATS}")
    if fmt == "slack":
        return {"text": _message(changes)}
    if fmt == "discord":
        return {"content": _message(changes, DISCORD_LIMIT)}
    return {"summary": summarize(changes), "changes": [c.to_dict() for c in changes]}


def send(
    url: str,
    payload: dict[str, Any],
    session: requests.Session | None = None,
    timeout: float = 10.0,
) -> None:
    host = urlsplit(url).netloc or "webhook"
    try:
        resp = (session or requests.Session()).post(url, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise NotifyError(f"webhook POST to {host} failed: {type(exc).__name__}") from None
    if not resp.ok:
        raise NotifyError(f"webhook POST to {host} failed: HTTP {resp.status_code}")
