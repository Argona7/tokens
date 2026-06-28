"""Cursor source.

Real spend/limit comes from an undocumented Connect-RPC (GetCurrentPeriodUsage)
authed by the session JWT stored in Cursor's state.vscdb. Values are in cents and
reset on the billing date. Local per-day activity is read from ai-code-tracking.db
as a best-effort signal (AI-authored lines/edits, not tokens).
"""

from __future__ import annotations

import json
import sqlite3
import time
import urllib.request
from pathlib import Path

from ..config import CURSOR_USAGE_ENDPOINT, HOME, cursor_state_db
from ..models import Bucket, ToolUsage, RELIABLE, UNAVAILABLE
from ..util import last_n_days


def _read_token(db: Path) -> str | None:
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            row = conn.execute(
                "SELECT value FROM ItemTable WHERE key='cursorAuth/accessToken'"
            ).fetchone()
        finally:
            conn.close()
        if row and row[0]:
            return str(row[0]).strip()
    except sqlite3.Error:
        return None
    return None


def _read_membership(db: Path) -> str | None:
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            row = conn.execute(
                "SELECT value FROM ItemTable WHERE key='cursorAuth/stripeMembershipType'"
            ).fetchone()
        finally:
            conn.close()
        if row and row[0]:
            return str(row[0]).strip().strip('"')
    except sqlite3.Error:
        return None
    return None


def _plan_label(membership: str | None) -> str:
    if not membership:
        return "—"
    return {"ultra": "Ultra", "pro": "Pro", "pro_plus": "Pro+",
            "free": "Free", "business": "Business", "team": "Team",
            "enterprise": "Enterprise"}.get(membership.lower(), membership.title())


def _fetch_usage(token: str) -> dict | None:
    req = urllib.request.Request(
        CURSOR_USAGE_ENDPOINT,
        data=b"{}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _local_activity(window_days: int) -> tuple[list[tuple[str, int]], float | None]:
    """Per-day count of AI-authored code rows from ai-code-tracking.db (best-effort)."""
    db = HOME / ".cursor" / "ai-tracking" / "ai-code-tracking.db"
    days = last_n_days(window_days)
    per_day = {d: 0 for d in days}
    last_ts = None
    if not db.is_file():
        return [(d, 0) for d in days], None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            rows = conn.execute(
                "SELECT createdAt FROM ai_code_hashes ORDER BY createdAt DESC LIMIT 20000"
            ).fetchall()
        finally:
            conn.close()
        from datetime import datetime
        for (created,) in rows:
            if not created:
                continue
            secs = created / 1000 if created > 1e12 else created
            last_ts = max(last_ts or 0, secs)
            d = datetime.fromtimestamp(secs).strftime("%Y-%m-%d")
            if d in per_day:
                per_day[d] += 1
    except sqlite3.Error:
        pass
    return [(d, per_day[d]) for d in days], last_ts


def collect(window_days: int = 7, use_auth: bool = True, on_progress=None) -> ToolUsage:
    usage = ToolUsage(tool="cursor", window_days=window_days)
    daily, last_ts = _local_activity(window_days)
    usage.daily = daily
    usage.last_activity = last_ts

    db = cursor_state_db()
    membership = _read_membership(db) if db else None
    usage.plan = _plan_label(membership)

    token = _read_token(db) if (db and use_auth) else None
    data = _fetch_usage(token) if token else None

    if data and isinstance(data.get("planUsage"), dict):
        pu = data["planUsage"]
        usage.source = RELIABLE
        limit_c = pu.get("limit")
        used_c = None
        if limit_c is not None and pu.get("remaining") is not None:
            used_c = limit_c - pu["remaining"]
        usage.spend_limit = (limit_c / 100) if limit_c is not None else None
        usage.spend_used = (used_c / 100) if used_c is not None else None
        pct = pu.get("totalPercentUsed")
        if pct is not None:
            sev = "critical" if pct >= 90 else "warning" if pct >= 80 else "normal"
            usage.buckets.append(Bucket(name="billing period", pct=float(pct),
                                        resets_at=_cycle_end(data), severity=sev))
        msg = data.get("displayMessage")
        if msg:
            usage.note = msg
    else:
        usage.source = UNAVAILABLE
        if not token:
            usage.note = "no auth token — local activity only"
        else:
            usage.note = "usage API unavailable — local activity only"
    return usage


def _cycle_end(data: dict) -> float | None:
    end = data.get("billingCycleEnd")
    if end is None:
        return None
    try:
        v = float(end)
        return v / 1000 if v > 1e12 else v
    except (ValueError, TypeError):
        return None
