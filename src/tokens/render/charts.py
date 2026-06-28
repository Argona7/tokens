"""plotext bar chart embedded inside a rich Panel via the JupyterMixin bridge."""

from __future__ import annotations

import plotext as plt
from rich.ansi import AnsiDecoder
from rich.console import Group
from rich.jupyter import JupyterMixin

from ..config import PALETTE
from ..util import humanize_tokens

_RGB = (122, 162, 247)  # cursor blue, reads well as bars on dark bg


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def scale_unit(values: list[int]) -> tuple[float, str]:
    """Pick a divisor + label so the y-axis reads in K/M/B instead of raw ints."""
    hi = max(values) if values else 0
    if hi >= 1e9:
        return 1e9, "B"
    if hi >= 1e6:
        return 1e6, "M"
    if hi >= 1e3:
        return 1e3, "K"
    return 1.0, ""


class ActivityChart(JupyterMixin):
    """Daily token bars that auto-fit the Layout cell they're placed in."""

    def __init__(self, daily: list[tuple[str, int]], color: str = PALETTE["accent"],
                 divisor: float = 1.0):
        self.daily = daily
        self.rgb = _hex_to_rgb(color)
        self.divisor = divisor or 1.0
        self.decoder = AnsiDecoder()

    def __rich_console__(self, console, options):
        labels = [d[5:] for d, _ in self.daily]      # MM-DD
        values = [v / self.divisor for _, v in self.daily]
        width = options.max_width or console.width
        height = options.height or 12
        plt.clf()
        plt.plotsize(width, height)
        plt.theme("clear")
        if any(values):
            plt.bar(labels, values, color=self.rgb)
        # thin x labels so they don't overlap on narrow terminals
        step = max(1, len(labels) // 10)
        plt.xticks(range(0, len(labels), step), [labels[i] for i in range(0, len(labels), step)])
        plt.yfrequency(4)
        rendered = plt.build()
        yield Group(*self.decoder.decode(rendered))


def peak_label(daily: list[tuple[str, int]]) -> str:
    if not daily:
        return ""
    top = max(daily, key=lambda x: x[1])
    if top[1] <= 0:
        return ""
    return f"peak {top[0][5:]} · {humanize_tokens(top[1])}"
