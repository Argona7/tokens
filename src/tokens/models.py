"""Uniform data model returned by every source, so the renderer is source-agnostic."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

# Reliability of a source's data, used to colour/label honestly.
RELIABLE = "RELIABLE"      # real numbers, straight from the tool
ESTIMATE = "ESTIMATE"      # reconstructed/approximated — labelled as such
UNAVAILABLE = "UNAVAILABLE"  # could not read it


@dataclass
class Bucket:
    """One rate-limit window (e.g. a 5-hour or weekly quota)."""
    name: str
    pct: float                       # 0..100 utilisation
    resets_at: float | None = None   # unix epoch seconds
    severity: str = "normal"         # normal | warning | critical
    stale: bool = False              # window already rolled over (data is old)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Tokens:
    """Token counts for a window. All optional; missing => 0/None."""
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    reasoning: int = 0

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_read + self.cache_write + self.reasoning

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["total"] = self.total
        return d


@dataclass
class ToolUsage:
    """Everything the dashboard needs to render one tool's column."""
    tool: str                                  # claude | cursor | codex
    plan: str = "—"
    source: str = UNAVAILABLE
    buckets: list[Bucket] = field(default_factory=list)
    tokens: Tokens = field(default_factory=Tokens)     # token totals over `window_days`
    window_days: int = 7
    cost_usd: float | None = None
    daily: list[tuple[str, int]] = field(default_factory=list)  # [(YYYY-MM-DD, tokens)]
    spend_used: float | None = None            # cursor: dollars spent this cycle
    spend_limit: float | None = None           # cursor: dollar limit this cycle
    credits: float | None = None               # codex: credit balance, if any
    ctx_pct: float | None = None               # codex: last context-window fill %
    last_activity: float | None = None         # unix epoch of newest record
    note: str = ""                             # human-facing caveat

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "plan": self.plan,
            "source": self.source,
            "buckets": [b.to_dict() for b in self.buckets],
            "tokens": self.tokens.to_dict(),
            "window_days": self.window_days,
            "cost_usd": self.cost_usd,
            "daily": self.daily,
            "spend_used": self.spend_used,
            "spend_limit": self.spend_limit,
            "credits": self.credits,
            "ctx_pct": self.ctx_pct,
            "last_activity": self.last_activity,
            "note": self.note,
        }
