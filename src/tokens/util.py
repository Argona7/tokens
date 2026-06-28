"""Small shared helpers: number/time formatting and safe JSON parsing."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone, timedelta


def humanize_tokens(n: float | int | None) -> str:
    """1234 -> '1.2K', 4_210_000 -> '4.2M'. None/0 handled."""
    if not n:
        return "0"
    n = float(n)
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= div:
            v = n / div
            return f"{v:.1f}{unit}" if v < 100 else f"{v:.0f}{unit}"
    return str(int(n))


def humanize_dollars(d: float | None) -> str:
    if d is None:
        return "—"
    if d == 0:
        return "$0"
    if abs(d) < 0.01:
        return "<$0.01"
    if abs(d) < 100:
        return f"${d:,.2f}"
    return f"${d:,.0f}"


def fmt_reset(resets_at: float | None, now: float | None = None) -> str:
    """Human countdown to a unix-epoch reset. 'resets in 2h 14m' / 'since reset'."""
    if not resets_at:
        return "—"
    now = now if now is not None else time.time()
    delta = resets_at - now
    if delta <= 0:
        return "window rolled over"
    return f"resets in {fmt_duration(delta)}"


def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    mins, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    if mins:
        return f"{mins}m"
    return "<1m"


def fmt_ago(ts: float | None, now: float | None = None) -> str:
    if not ts:
        return "—"
    now = now if now is not None else time.time()
    delta = now - ts
    if delta < 0:
        return "just now"
    if delta < 90:
        return "just now"
    return f"{fmt_duration(delta)} ago"


def iso_to_epoch(s: str | None) -> float | None:
    """Parse an ISO8601 timestamp (with or without tz) to unix epoch seconds."""
    if not s:
        return None
    try:
        s2 = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def day_key(epoch: float) -> str:
    """Local-date YYYY-MM-DD for an epoch — used as activity-graph bucket."""
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d")


def last_n_days(n: int) -> list[str]:
    today = datetime.now()
    return [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n - 1, -1, -1)]


def safe_json_loads(line: str):
    try:
        return json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
