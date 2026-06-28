"""Codex CLI source — fully local, real limits.

Each rollout file (~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl) carries
`token_count` events whose payload has cumulative `total_token_usage` and a
`rate_limits` snapshot (primary=5h, secondary=weekly). We read the newest rollout
with a non-null snapshot for limits, and aggregate per-session totals for activity.
"""

from __future__ import annotations

import time
from pathlib import Path

from ..config import codex_sessions_dir
from ..cache import FileCache
from ..models import Bucket, Tokens, ToolUsage, RELIABLE, UNAVAILABLE
from ..pricing import cost_from_parts
from ..util import day_key, last_n_days, safe_json_loads


def _rollout_files() -> list[Path]:
    d = codex_sessions_dir()
    if not d.is_dir():
        return []
    return sorted(d.rglob("rollout-*.jsonl"))


def _parse_rollout(path: Path) -> dict:
    """Final cumulative usage + last rate-limit snapshot for one session."""
    last_usage = None
    last_rl = None
    last_ts = 0.0
    model = None
    ctx_window = None
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                rec = safe_json_loads(line)
                if not rec:
                    continue
                payload = rec.get("payload") or {}
                if rec.get("type") == "turn_context":
                    model = payload.get("model") or model
                if payload.get("type") == "token_count":
                    info = payload.get("info") or {}
                    tu = info.get("total_token_usage")
                    if isinstance(tu, dict):
                        last_usage = tu
                    if info.get("model_context_window"):
                        ctx_window = info.get("model_context_window")
                    rl = payload.get("rate_limits")
                    if isinstance(rl, dict):
                        last_rl = rl
                    ts = _epoch(rec.get("timestamp"))
                    if ts:
                        last_ts = max(last_ts, ts)
    except OSError:
        pass
    cost = 0.0
    if last_usage:
        cost = cost_from_parts(
            model,
            input=int(last_usage.get("input_tokens") or 0),
            output=int(last_usage.get("output_tokens") or 0),
            cache_read=int(last_usage.get("cached_input_tokens") or 0),
            reasoning=int(last_usage.get("reasoning_output_tokens") or 0),
        )
    return {
        "usage": last_usage, "rate_limits": last_rl, "last_ts": last_ts,
        "model": model, "ctx_window": ctx_window, "cost": round(cost, 6),
    }


def _epoch(ts: str | None) -> float:
    from ..util import iso_to_epoch
    return iso_to_epoch(ts) or 0.0


def _plan_label(plan_type: str | None) -> str:
    if not plan_type:
        return "—"
    return {"plus": "Plus", "pro": "Pro", "team": "Team",
            "business": "Business"}.get(plan_type.lower(), plan_type.title())


def _bucket(rl_part: dict | None, name: str, now: float) -> Bucket | None:
    if not isinstance(rl_part, dict):
        return None
    pct = rl_part.get("used_percent")
    if pct is None:
        return None
    resets_at = rl_part.get("resets_at")
    stale = bool(resets_at and resets_at < now)
    sev = "critical" if pct >= 90 else "warning" if pct >= 80 else "normal"
    return Bucket(name=name, pct=float(pct), resets_at=resets_at,
                  severity=sev, stale=stale)


def collect(window_days: int = 7, on_progress=None) -> ToolUsage:
    files = _rollout_files()
    usage = ToolUsage(tool="codex", window_days=window_days)
    if not files:
        usage.note = "no Codex sessions found"
        return usage

    cache = FileCache("codex")
    payloads = cache.process(files, _parse_rollout, on_progress)

    now = time.time()
    wanted = set(last_n_days(window_days))
    totals = Tokens()
    cost = 0.0
    per_day: dict[str, int] = {}
    last_ts = 0.0
    newest_rl = None
    newest_rl_ts = 0.0
    ctx_pct = None

    for p in payloads:
        if not p:
            continue
        ts = p.get("last_ts") or 0.0
        last_ts = max(last_ts, ts)
        tu = p.get("usage")
        if isinstance(tu, dict):
            tot = int(tu.get("total_tokens") or 0)
            d = day_key(ts) if ts else "unknown"
            per_day[d] = per_day.get(d, 0) + tot
            if d in wanted:
                totals.input += int(tu.get("input_tokens") or 0)
                totals.output += int(tu.get("output_tokens") or 0)
                totals.cache_read += int(tu.get("cached_input_tokens") or 0)
                totals.reasoning += int(tu.get("reasoning_output_tokens") or 0)
                cost += p.get("cost", 0.0)
        rl = p.get("rate_limits")
        if isinstance(rl, dict) and ts >= newest_rl_ts:
            newest_rl, newest_rl_ts = rl, ts
            cw = p.get("ctx_window")
            if cw and tu and tu.get("total_tokens"):
                ctx_pct = min(100.0, int(tu["total_tokens"]) / cw * 100)

    usage.tokens = totals
    usage.cost_usd = round(cost, 2)
    usage.daily = [(d, per_day.get(d, 0)) for d in last_n_days(window_days)]
    usage.last_activity = last_ts or None
    usage.source = RELIABLE

    if isinstance(newest_rl, dict):
        usage.plan = _plan_label(newest_rl.get("plan_type"))
        if newest_rl.get("credits") is not None:
            usage.credits = newest_rl.get("credits")
        for part, label in (("primary", "5-hour"), ("secondary", "weekly")):
            b = _bucket(newest_rl.get(part), label, now)
            if b:
                usage.buckets.append(b)
        stale = any(b.stale for b in usage.buckets)
        # context-window fill is only meaningful for a recent session
        usage.ctx_pct = None if stale else ctx_pct
        if stale:
            from ..util import fmt_ago
            usage.note = f"limits as of {fmt_ago(newest_rl_ts, now)}"
    else:
        usage.note = "no rate-limit data in sessions"
    return usage
