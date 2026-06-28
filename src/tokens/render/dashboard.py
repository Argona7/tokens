"""Assemble the full dashboard renderable from three ToolUsage objects."""

from __future__ import annotations

import time
from datetime import datetime

from rich import box
from rich.align import Align
from rich.columns import Columns
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..config import PALETTE, TOOL_COLORS, TOOL_LABELS
from ..models import ToolUsage, RELIABLE, ESTIMATE, UNAVAILABLE
from ..util import fmt_ago, fmt_reset, humanize_dollars, humanize_tokens
from .charts import ActivityChart, peak_label
from .theme import color_for_pct, gauge, heat_strip, kv, sparkline

_SRC_MARK = {
    RELIABLE: ("●", PALETTE["good"]),
    ESTIMATE: ("◐", PALETTE["warn"]),
    UNAVAILABLE: ("○", PALETTE["dim"]),
}


def _bucket_rows(u: ToolUsage, now: float) -> list:
    rows = []
    for b in u.buckets:
        c = color_for_pct(b.pct)
        line1 = Text()
        line1.append(f"{b.name:<14}", style=PALETTE["dim"])
        line1.append_text(gauge(b.pct, 12, c))
        line1.append(f" {b.pct:>3.0f}%", style=f"bold {c}")
        rows.append(line1)
        if b.stale:
            rows.append(Text("  stale · " + fmt_ago(u.last_activity, now), style=PALETTE["dim"]))
        elif b.resets_at:
            rows.append(Text("  " + fmt_reset(b.resets_at, now), style=PALETTE["dim"]))
    return rows


def _tool_panel(u: ToolUsage, now: float, show_cost: bool) -> Panel:
    color = TOOL_COLORS.get(u.tool, PALETTE["fg"])
    body = Table.grid(padding=(0, 0))
    body.add_column()

    body.add_row(kv("plan", u.plan, value_style=f"bold {color}"))
    body.add_row(Text(""))

    if u.buckets:
        for r in _bucket_rows(u, now):
            body.add_row(r)
    else:
        body.add_row(Text("limits  n/a", style=PALETTE["dim"]))
    body.add_row(Text(""))

    # spend (cursor) / credits (codex)
    if u.spend_limit is not None:
        body.add_row(kv("spend", f"{humanize_dollars(u.spend_used)} / {humanize_dollars(u.spend_limit)}"))
    if u.credits is not None:
        body.add_row(kv("credits", f"{u.credits:,.0f}"))
    if u.ctx_pct is not None:
        body.add_row(kv("ctx window", f"{u.ctx_pct:.0f}%"))

    # token breakdown over the window
    tk = u.tokens
    if u.tool == "cursor" and tk.total == 0:
        # Cursor's API reports dollars spent, not tokens — say so instead of "0".
        body.add_row(kv("tokens", "n/a", value_style=PALETTE["dim"]))
        body.add_row(Text("  Cursor reports spend, not tokens", style=PALETTE["dim"]))
    else:
        body.add_row(kv(f"tokens {u.window_days}d", humanize_tokens(tk.total), value_style="bold"))
        detail = Text()
        detail.append(f"  in {humanize_tokens(tk.input)}  out {humanize_tokens(tk.output)}",
                      style=PALETTE["dim"])
        body.add_row(detail)
        if tk.cache_read or tk.cache_write:
            body.add_row(Text(f"  cache rd {humanize_tokens(tk.cache_read)} wr {humanize_tokens(tk.cache_write)}",
                              style=PALETTE["dim"]))
        if tk.reasoning:
            body.add_row(Text(f"  reasoning {humanize_tokens(tk.reasoning)}", style=PALETTE["dim"]))
    if show_cost and u.cost_usd is not None and not (u.tool == "cursor" and tk.total == 0):
        body.add_row(kv("cost", "~" + humanize_dollars(u.cost_usd), value_style=f"bold {PALETTE['gold']}"))

    # cursor: the sparkline is local AI-edit activity, label it so it's not mistaken for tokens
    body.add_row(Text(""))
    if u.tool == "cursor" and tk.total == 0:
        body.add_row(Text("local AI-edit activity", style=PALETTE["dim"]))
    body.add_row(sparkline([v for _, v in u.daily], color))

    mark, mcol = _SRC_MARK.get(u.source, ("○", PALETTE["dim"]))
    foot = Text()
    foot.append(f"{mark} ", style=mcol)
    foot.append(u.source.lower(), style=mcol)
    if u.note:
        foot.append(f" · {u.note}", style=PALETTE["dim"])
    body.add_row(Text(""))
    body.add_row(foot)

    title = Text(TOOL_LABELS.get(u.tool, u.tool), style=f"bold {color}")
    return Panel(body, title=title, border_style=color, box=box.ROUNDED,
                 padding=(0, 1))


def _header(usages: list[ToolUsage], now: float, show_cost: bool) -> Panel:
    total_today = sum((u.daily[-1][1] if u.daily else 0) for u in usages)
    total_cost = sum((u.cost_usd or 0) for u in usages) if show_cost else None
    # worst active bucket across tools for the alert
    worst = None
    for u in usages:
        for b in u.buckets:
            if b.stale:
                continue
            if worst is None or b.pct > worst[1]:
                worst = (u.tool, b.pct, b.name)

    left = Text()
    left.append("◆ TOKENS", style=f"bold {PALETTE['accent']}")
    left.append("   across Claude · Cursor · Codex", style=PALETTE["dim"])

    line2 = Text()
    line2.append(f"today {humanize_tokens(total_today)} tok", style="bold")
    if total_cost is not None:
        line2.append("   ·   ", style=PALETTE["dim"])
        line2.append("~" + humanize_dollars(total_cost), style=PALETTE["gold"])
    if worst:
        c = color_for_pct(worst[1])
        line2.append("   ·   ", style=PALETTE["dim"])
        flag = "⚠ " if worst[1] >= 80 else ""
        line2.append(f"{flag}{worst[0]} {worst[2]} {worst[1]:.0f}%", style=f"bold {c}")

    stamp = Text(datetime.fromtimestamp(now).strftime("%d %b %H:%M"), style=PALETTE["dim"])
    grid = Table.grid(expand=True)
    grid.add_column(ratio=1)
    grid.add_column(justify="right")
    grid.add_row(left, stamp)
    grid.add_row(line2, Text(""))
    return Panel(grid, box=box.HEAVY, border_style=PALETTE["accent"], padding=(0, 1))


def _activity(usages: list[ToolUsage]) -> Panel:
    # combined daily totals across tools
    days = usages[0].daily if usages else []
    combined = []
    for i, (d, _) in enumerate(days):
        tot = sum((u.daily[i][1] if i < len(u.daily) else 0) for u in usages)
        combined.append((d, tot))
    from .charts import scale_unit
    divisor, unit = scale_unit([v for _, v in combined])
    inner = Group(
        ActivityChart(combined, PALETTE["accent"], divisor),
        heat_strip([v for _, v in combined]),
    )
    sub = peak_label(combined)
    unit_lbl = f" ({unit} tokens)" if unit else " (tokens)"
    title = Text(f"Activity — daily{unit_lbl} · last {len(days)}d", style=f"bold {PALETTE['fg']}")
    if sub:
        title.append(f"   {sub}", style=PALETTE["dim"])
    return Panel(inner, title=title, title_align="left",
                 border_style=PALETTE["dim"], box=box.ROUNDED, padding=(0, 1))


def _footer(usages: list[ToolUsage], now: float, show_cost: bool) -> Text:
    total = sum(u.tokens.total for u in usages)
    cost = sum((u.cost_usd or 0) for u in usages) if show_cost else None
    t = Text()
    wd = usages[0].window_days if usages else 7
    t.append(f"  TOTAL  {humanize_tokens(total)} tokens {wd}d", style="bold")
    if cost is not None:
        t.append("   ·   ", style=PALETTE["dim"])
        t.append("~" + humanize_dollars(cost), style=PALETTE["gold"])
    # next reset across tools
    resets = [(b.resets_at, u.tool) for u in usages for b in u.buckets
              if b.resets_at and not b.stale and b.resets_at > now]
    if resets:
        soonest = min(resets, key=lambda x: x[0])
        t.append("   ·   ", style=PALETTE["dim"])
        t.append(f"next reset: {soonest[1]} {fmt_reset(soonest[0], now)[10:]}", style=PALETTE["dim"])
    return t


def build(usages: list[ToolUsage], show_cost: bool = True, watch: bool = False) -> Group:
    now = time.time()
    panels = Columns([_tool_panel(u, now, show_cost) for u in usages],
                     expand=True, equal=True)
    parts = [
        _header(usages, now, show_cost),
        panels,
        _activity(usages),
        _footer(usages, now, show_cost),
    ]
    if watch:
        parts.append(Align.center(Text("q quit · refreshing", style=PALETTE["dim"])))
    return Group(*parts)
