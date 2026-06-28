"""Claude Code source.

Tokens & activity: parse ~/.claude/projects/**/*.jsonl (cached incrementally).
Real limits: read the OAuth token from the macOS Keychain (or Linux creds file)
and GET the Anthropic usage endpoint. Falls back to ESTIMATE if auth is absent.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from pathlib import Path

from ..config import (
    CLAUDE_CREDENTIALS_FILE,
    CLAUDE_KEYCHAIN_SERVICE,
    CLAUDE_OAUTH_BETA,
    CLAUDE_USAGE_ENDPOINT,
    claude_project_roots,
)
from ..cache import FileCache
from ..models import Bucket, Tokens, ToolUsage, RELIABLE, ESTIMATE, UNAVAILABLE
from ..pricing import cost_from_parts
from ..util import day_key, iso_to_epoch, last_n_days, safe_json_loads

# Approximate per-5h token allowances by plan tier (used only in estimate mode).
_ESTIMATE_5H = {"pro": 44_000, "max_5x": 880_000, "max_20x": 2_200_000}


# --------------------------------------------------------------------------- #
# Token / activity from local JSONL                                            #
# --------------------------------------------------------------------------- #
def _parse_file(path: Path) -> dict:
    """One row per assistant message: [mid, ts, total, in, out, cr, cw, model].

    Dedup is done globally by the caller keyed on `mid` (Anthropic message ids are
    unique; branched/resumed transcripts repeat the same id across many files, so
    naive summing over-counts by orders of magnitude). Cost is NOT baked in here —
    the model is stored so prices can change without re-parsing.
    """
    msgs: list[list] = []
    seen_local: set[str] = set()
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for lineno, line in enumerate(fh):
                rec = safe_json_loads(line)
                if not rec or rec.get("type") != "assistant":
                    continue
                msg = rec.get("message") or {}
                usage = msg.get("usage")
                if not isinstance(usage, dict):
                    continue
                mid = msg.get("id") or rec.get("requestId") or f"{path.name}:{lineno}"
                if mid in seen_local:
                    continue
                seen_local.add(mid)
                ts = iso_to_epoch(rec.get("timestamp")) or 0.0
                inp = int(usage.get("input_tokens") or 0)
                out = int(usage.get("output_tokens") or 0)
                cr = int(usage.get("cache_read_input_tokens") or 0)
                cw = int(usage.get("cache_creation_input_tokens") or 0)
                msgs.append([mid, ts, inp + out + cr + cw, inp, out, cr, cw,
                             msg.get("model") or ""])
    except OSError:
        return {"msgs": []}
    return {"msgs": msgs}


def _collect_local(window_days: int, on_progress=None):
    files: list[Path] = []
    for root in claude_project_roots():
        files.extend(root.rglob("*.jsonl"))
    cache = FileCache("claude", version=3)
    payloads = cache.process(files, _parse_file, on_progress)

    wanted = set(last_n_days(window_days))
    seen: set[str] = set()
    totals = Tokens()
    cost = 0.0
    per_day: dict[str, int] = {}
    last_ts = 0.0
    for p in payloads:
        for row in p.get("msgs", []) if p else []:
            mid, ts, total, inp, out, cr, cw, model = row
            if mid in seen:
                continue
            seen.add(mid)
            if ts:
                last_ts = max(last_ts, ts)
            d = day_key(ts) if ts else "unknown"
            per_day[d] = per_day.get(d, 0) + int(total)
            if d in wanted:
                totals.input += inp
                totals.output += out
                totals.cache_read += cr
                totals.cache_write += cw
                cost += cost_from_parts(model, input=inp, output=out,
                                        cache_read=cr, cache_write=cw)
    daily = [(d, per_day.get(d, 0)) for d in last_n_days(window_days)]
    return totals, daily, cost, last_ts


# --------------------------------------------------------------------------- #
# Real limits via OAuth                                                         #
# --------------------------------------------------------------------------- #
def _read_oauth() -> dict | None:
    """Return the claudeAiOauth blob from Keychain (macOS) or creds file (Linux)."""
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", CLAUDE_KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            blob = json.loads(out.stdout.strip())
            return blob.get("claudeAiOauth", blob)
    except (FileNotFoundError, json.JSONDecodeError, subprocess.SubprocessError):
        pass
    if CLAUDE_CREDENTIALS_FILE.is_file():
        try:
            blob = json.loads(CLAUDE_CREDENTIALS_FILE.read_text())
            return blob.get("claudeAiOauth", blob)
        except (OSError, json.JSONDecodeError):
            return None
    return None


def _plan_label(oauth: dict | None) -> str:
    if not oauth:
        return "—"
    tier = (oauth.get("rateLimitTier") or "").lower()
    sub = (oauth.get("subscriptionType") or "").lower()
    if "20x" in tier:
        return "Max 20x"
    if "5x" in tier:
        return "Max 5x"
    if sub == "max":
        return "Max"
    if sub == "pro":
        return "Pro"
    return sub.title() or "—"


def _fetch_limits(token: str) -> dict | None:
    req = urllib.request.Request(
        CLAUDE_USAGE_ENDPOINT,
        headers={"Authorization": f"Bearer {token}", "anthropic-beta": CLAUDE_OAUTH_BETA},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _severity(pct: float) -> str:
    return "critical" if pct >= 90 else "warning" if pct >= 80 else "normal"


def _buckets_from_limits(data: dict) -> list[Bucket]:
    out: list[Bucket] = []
    mapping = [
        ("five_hour", "5-hour"),
        ("seven_day", "weekly"),
        ("seven_day_sonnet", "weekly · Sonnet"),
        ("seven_day_opus", "weekly · Opus"),
    ]
    for key, label in mapping:
        obj = data.get(key)
        if not isinstance(obj, dict):
            continue
        pct = obj.get("utilization")
        if pct is None:
            continue
        # hide per-model sub-limits that are unused (just noise)
        if key in ("seven_day_sonnet", "seven_day_opus") and float(pct) == 0:
            continue
        out.append(Bucket(
            name=label, pct=float(pct),
            resets_at=iso_to_epoch(obj.get("resets_at")),
            severity=_severity(float(pct)),
        ))
    return out


def collect(window_days: int = 7, use_auth: bool = True, on_progress=None) -> ToolUsage:
    totals, daily, cost, last_ts = _collect_local(window_days, on_progress)
    usage = ToolUsage(
        tool="claude", window_days=window_days, tokens=totals,
        cost_usd=round(cost, 2), daily=daily, last_activity=last_ts or None,
    )

    oauth = _read_oauth() if use_auth else None
    usage.plan = _plan_label(oauth)
    limits = _fetch_limits(oauth["accessToken"]) if oauth and oauth.get("accessToken") else None

    if limits:
        usage.source = RELIABLE
        usage.buckets = _buckets_from_limits(limits)
        extra = limits.get("extra_usage") or {}
        if extra.get("is_enabled") and extra.get("used_credits") is not None:
            usage.note = "extra usage on"
    else:
        # estimate mode: reconstruct 5h window from local tokens vs plan tier
        usage.source = ESTIMATE
        tier = "max_20x" if "20x" in usage.plan else "max_5x" if "5x" in usage.plan \
            else "pro" if usage.plan == "Pro" else "max_20x"
        allow = _ESTIMATE_5H.get(tier, 880_000)
        recent = _recent_window_tokens(daily)
        pct = min(100.0, recent / allow * 100) if allow else 0.0
        usage.buckets = [Bucket(name="5-hour (est)", pct=pct, severity=_severity(pct))]
        usage.note = "estimate — no OAuth token" if use_auth else "estimate mode"
    return usage


def _recent_window_tokens(daily: list[tuple[str, int]]) -> int:
    """Rough 5h proxy: today's tokens (best we can do without per-message recency)."""
    return daily[-1][1] if daily else 0
