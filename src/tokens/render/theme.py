"""Visual primitives: colour ramps, gauges, sparklines, contribution strips."""

from __future__ import annotations

from rich.text import Text

from ..config import PALETTE

SPARKS = "▁▂▃▄▅▆▇█"
# 5-step GitHub-green contribution ramp.
HEAT = ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"]


def color_for_pct(pct: float) -> str:
    if pct >= 80:
        return PALETTE["bad"]
    if pct >= 50:
        return PALETTE["warn"]
    return PALETTE["good"]


def gauge(pct: float, width: int = 18, color: str | None = None) -> Text:
    """A solid block gauge: filled portion coloured, remainder dim."""
    pct = max(0.0, min(100.0, float(pct)))
    color = color or color_for_pct(pct)
    filled = round(width * pct / 100)
    t = Text()
    t.append("█" * filled, style=color)
    t.append("░" * (width - filled), style=PALETTE["dim"])
    return t


def sparkline(values: list[int | float], color: str = PALETTE["accent"]) -> Text:
    if not values:
        return Text("—", style=PALETTE["dim"])
    hi = max(values)
    t = Text()
    if hi <= 0:
        return Text(SPARKS[0] * len(values), style=PALETTE["dim"])
    for v in values:
        idx = int(v / hi * (len(SPARKS) - 1))
        t.append(SPARKS[idx], style=color)
    return t


def heat_strip(values: list[int | float]) -> Text:
    """GitHub-style coloured blocks, one per day, intensity = relative volume."""
    if not values:
        return Text("")
    hi = max(values) or 1
    t = Text()
    for v in values:
        if v <= 0:
            level = 0
        else:
            level = min(4, 1 + int(v / hi * 3.999))
        t.append("■ ", style=HEAT[level])
    return t


def kv(label: str, value, *, value_style: str = "bold",
       label_style: str | None = None) -> Text:
    label_style = label_style or PALETTE["dim"]
    t = Text()
    t.append(f"{label} ", style=label_style)
    t.append(str(value), style=value_style)
    return t
