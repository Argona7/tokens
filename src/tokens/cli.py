"""tokens — unified usage dashboard CLI.

    tokens                 one-shot dashboard (all three tools)
    tokens --watch         live-refreshing dashboard
    tokens --json          machine-readable JSON, no colour
    tokens claude|cursor|codex   focus one tool
    tokens --no-cost       hide dollar estimates
    tokens --estimate      skip Claude Keychain/OAuth, estimate limits
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from rich.console import Console

from . import __version__
from .models import ToolUsage
from .pricing import refresh_litellm
from .sources import claude as claude_src
from .sources import codex as codex_src
from .sources import cursor as cursor_src

TOOLS = ("claude", "cursor", "codex")


def _collect(tools, *, use_auth: bool, console: Console | None, window: int) -> list[ToolUsage]:
    out: list[ToolUsage] = []
    status = None
    cold = {"n": 0}

    def progress_factory(label):
        def cb(done, total, parsed):
            if parsed and status is not None:
                status.update(f"[dim]scanning {label}: {done}/{total} files…[/dim]")
        return cb

    ctx = console.status("[dim]reading usage…[/dim]") if console else None
    if ctx:
        ctx.__enter__()
        status = ctx
    try:
        for t in tools:
            if t == "claude":
                out.append(claude_src.collect(window, use_auth, progress_factory("Claude")))
            elif t == "codex":
                out.append(codex_src.collect(window, progress_factory("Codex")))
            elif t == "cursor":
                out.append(cursor_src.collect(window, use_auth))
    finally:
        if ctx:
            ctx.__exit__(None, None, None)
    return out


def _print_dashboard(usages, *, show_cost: bool, watch: bool, interval: int,
                     console: Console, tools, use_auth, window):
    from .render.dashboard import build
    if not watch:
        console.print(build(usages, show_cost=show_cost))
        return
    from rich.live import Live
    with Live(build(usages, show_cost=show_cost, watch=True), console=console,
              screen=True, refresh_per_second=4, transient=False) as live:
        try:
            while True:
                for _ in range(max(1, interval)):
                    time.sleep(1)
                fresh = _collect(tools, use_auth=use_auth, console=None, window=window)
                live.update(build(fresh, show_cost=show_cost, watch=True))
        except KeyboardInterrupt:
            pass


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="tokens", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("tool", nargs="?", choices=TOOLS, help="focus a single tool")
    p.add_argument("--watch", "-w", action="store_true", help="live-refreshing view")
    p.add_argument("--json", "-j", action="store_true", help="machine-readable output")
    p.add_argument("--no-cost", action="store_true", help="hide dollar estimates")
    p.add_argument("--estimate", action="store_true",
                   help="skip Claude Keychain/OAuth, estimate limits")
    p.add_argument("--days", type=int, default=14, help="token window in days (default 14)")
    p.add_argument("--interval", type=int, default=30, help="watch refresh seconds")
    p.add_argument("--refresh-prices", action="store_true",
                   help="re-download the LiteLLM price card and exit")
    p.add_argument("--version", "-V", action="version", version=f"tokens {__version__}")
    args = p.parse_args(argv)

    if args.refresh_prices:
        ok = refresh_litellm()
        print("price card refreshed" if ok else "price refresh failed", file=sys.stderr)
        return 0 if ok else 1

    tools = (args.tool,) if args.tool else TOOLS
    use_auth = not args.estimate
    show_cost = not args.no_cost
    window = max(1, min(365, args.days))

    if args.json:
        usages = _collect(tools, use_auth=use_auth, console=None, window=window)
        payload = {u.tool: u.to_dict() for u in usages}
        payload["generated_at"] = int(time.time())
        json.dump(payload, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
        return 0

    console = Console()
    usages = _collect(tools, use_auth=use_auth, console=console, window=window)
    _print_dashboard(usages, show_cost=show_cost, watch=args.watch,
                     interval=args.interval, console=console, tools=tools,
                     use_auth=use_auth, window=window)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
