"""Filesystem paths, env overrides, and the colour palette.

Everything that depends on *where things live* on disk is centralised here so the
source modules stay focused on parsing, not path-hunting.
"""

from __future__ import annotations

import os
from pathlib import Path

HOME = Path.home()

# ---------------------------------------------------------------------------
# Claude Code
# ---------------------------------------------------------------------------
def claude_project_roots() -> list[Path]:
    """All directories that may hold Claude Code session JSONL files.

    Honours CLAUDE_CONFIG_DIR (comma-separated, like Claude Code itself) and
    checks both the classic ~/.claude and the newer ~/.config/claude location.
    """
    roots: list[Path] = []
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        for part in env.split(","):
            part = part.strip()
            if part:
                roots.append(Path(part).expanduser() / "projects")
    roots.append(HOME / ".claude" / "projects")
    roots.append(HOME / ".config" / "claude" / "projects")
    # de-dup while preserving order, keep only existing
    seen: set[str] = set()
    out: list[Path] = []
    for r in roots:
        key = str(r)
        if key not in seen and r.is_dir():
            seen.add(key)
            out.append(r)
    return out


# macOS Keychain item that stores the Claude Code OAuth blob.
CLAUDE_KEYCHAIN_SERVICE = "Claude Code-credentials"
# Linux fallback file (same JSON shape under "claudeAiOauth").
CLAUDE_CREDENTIALS_FILE = HOME / ".claude" / ".credentials.json"
CLAUDE_USAGE_ENDPOINT = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_OAUTH_BETA = "oauth-2025-04-20"

# ---------------------------------------------------------------------------
# Codex CLI
# ---------------------------------------------------------------------------
def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", HOME / ".codex")).expanduser()


def codex_sessions_dir() -> Path:
    return codex_home() / "sessions"


# ---------------------------------------------------------------------------
# Cursor
# ---------------------------------------------------------------------------
def cursor_state_db() -> Path | None:
    """Path to Cursor's globalStorage SQLite DB, per-platform."""
    candidates = [
        HOME / "Library" / "Application Support" / "Cursor" / "User"
        / "globalStorage" / "state.vscdb",
        HOME / ".config" / "Cursor" / "User" / "globalStorage" / "state.vscdb",
        Path(os.environ.get("APPDATA", "")) / "Cursor" / "User"
        / "globalStorage" / "state.vscdb",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


CURSOR_USAGE_ENDPOINT = (
    "https://api2.cursor.sh/aiserver.v1.DashboardService/GetCurrentPeriodUsage"
)

# ---------------------------------------------------------------------------
# Our own cache
# ---------------------------------------------------------------------------
def cache_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", HOME / ".cache"))
    d = base / "tokens"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Palette — Tokyo Night, truecolor. Tool accent colours kept distinct.
# ---------------------------------------------------------------------------
PALETTE = {
    "bg": "#1a1b26",
    "fg": "#c0caf5",
    "dim": "#565f89",
    "claude": "#bb9af7",   # purple
    "cursor": "#7aa2f7",   # blue
    "codex": "#9ece6a",    # green
    "good": "#9ece6a",
    "warn": "#e0af68",
    "bad": "#f7768e",
    "accent": "#7dcfff",
    "gold": "#e0af68",
}

TOOL_COLORS = {
    "claude": PALETTE["claude"],
    "cursor": PALETTE["cursor"],
    "codex": PALETTE["codex"],
}

TOOL_LABELS = {
    "claude": "Claude Code",
    "cursor": "Cursor",
    "codex": "Codex",
}
