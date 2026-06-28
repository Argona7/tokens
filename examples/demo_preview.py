"""Render the README preview from MOCK data (no real accounts touched).

    uv run python examples/demo_preview.py            # print to terminal
    uv run python examples/demo_preview.py out.svg    # save an SVG screenshot
"""

from __future__ import annotations

import sys

from rich.console import Console

from tokens.models import Bucket, Tokens, ToolUsage, RELIABLE, UNAVAILABLE
from tokens.render.dashboard import build

# Fixed reset offsets (seconds from "now") so the demo is deterministic-ish.
H = 3600


def _ramp(seed, days=14):
    vals = []
    x = seed
    for i in range(days):
        x = (x * 1103515245 + 12345) % 2_000_000
        vals.append((f"2026-06-{15 + i:02d}", 200_000 + x))
    return vals


def demo() -> list[ToolUsage]:
    claude = ToolUsage(
        tool="claude", plan="Max 5x", source=RELIABLE, window_days=14,
        tokens=Tokens(input=8_100_000, output=5_400_000,
                      cache_read=612_000_000, cache_write=41_000_000),
        cost_usd=512.0, daily=_ramp(7),
        buckets=[
            Bucket("5-hour", 42.0, resets_at=_now() + 2 * H + 600, severity="normal"),
            Bucket("weekly", 67.0, resets_at=_now() + 3 * 86400, severity="normal"),
        ],
    )
    cursor = ToolUsage(
        tool="cursor", plan="Pro", source=RELIABLE, window_days=14,
        spend_used=7.60, spend_limit=20.0,
        daily=[(d, n % 40) for d, n in _ramp(3)],
        buckets=[Bucket("billing period", 38.0, resets_at=_now() + 12 * 86400)],
        note="You've used 38% of your included usage",
    )
    codex = ToolUsage(
        tool="codex", plan="Plus", source=RELIABLE, window_days=14,
        tokens=Tokens(input=980_000, output=22_000, cache_read=410_000, reasoning=31_000),
        cost_usd=1.9, ctx_pct=58.0, daily=[(d, n // 800) for d, n in _ramp(11)],
        buckets=[
            Bucket("5-hour", 12.0, resets_at=_now() + 4 * H, severity="normal"),
            Bucket("weekly", 24.0, resets_at=_now() + 5 * 86400, severity="normal"),
        ],
    )
    return [claude, cursor, codex]


def _now() -> float:
    # avoid importing time at module import in a way the linter flags; demo only
    import time
    return time.time()


def main() -> None:
    usages = demo()
    if len(sys.argv) > 1:
        c = Console(record=True, width=104, force_terminal=True, color_system="truecolor")
        c.print(build(usages, show_cost=True))
        c.save_svg(sys.argv[1], title="tokens")
        print(f"saved {sys.argv[1]}")
    else:
        Console().print(build(usages, show_cost=True))


if __name__ == "__main__":
    main()
