"""Cost estimation from token counts.

Two price sources, in priority order:
  1. A cached LiteLLM price card (exact per-model rates — same table ccusage uses),
     populated by `tokens --refresh-prices`. Preferred when present.
  2. A small embedded card matched by model-family substring (offline fallback).

Embedded rates are the real mid-2026 list prices (USD per 1M tokens), verified
against LiteLLM, so out-of-the-box cost is accurate even with no network. Dollar
figures are still labelled '~' in the UI because cache 1h/5m splits and Cursor's
Auto model are approximated.
"""

from __future__ import annotations

import json
import time
import urllib.request

from .config import cache_dir
from .models import Tokens

# (input, output, cache_read, cache_write) USD per 1M tokens. First substring hit wins.
_CARD: list[tuple[str, tuple[float, float, float, float]]] = [
    ("opus", (5.0, 25.0, 0.50, 6.25)),
    ("sonnet", (3.0, 15.0, 0.30, 3.75)),
    ("haiku", (1.0, 5.0, 0.10, 1.25)),
    ("gpt-5.5", (5.0, 30.0, 0.50, 0.50)),
    ("gpt-5-codex", (1.25, 10.0, 0.125, 0.125)),
    ("codex", (1.25, 10.0, 0.125, 0.125)),
    ("gpt-5", (1.25, 10.0, 0.125, 0.125)),
    ("o4", (1.10, 4.40, 0.275, 0.275)),
    ("gpt-4.1", (2.0, 8.0, 0.5, 0.5)),
    ("composer", (3.0, 15.0, 0.30, 3.75)),
]
_DEFAULT = (3.0, 15.0, 0.30, 3.75)

_LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)

_litellm_cache: dict | None = None
_litellm_loaded = False


def _load_litellm() -> dict | None:
    """Lazily load the cached LiteLLM card (no network here)."""
    global _litellm_cache, _litellm_loaded
    if _litellm_loaded:
        return _litellm_cache
    _litellm_loaded = True
    path = cache_dir() / "litellm_prices.json"
    if path.is_file():
        try:
            _litellm_cache = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            _litellm_cache = None
    return _litellm_cache


def _rates_from_litellm(model: str) -> tuple[float, float, float, float] | None:
    card = _load_litellm()
    if not card:
        return None
    entry = card.get(model)
    if entry is None:
        m = model.lower()
        for key, val in card.items():
            if isinstance(val, dict) and key.lower() in m and "input_cost_per_token" in val:
                entry = val
                break
    if not isinstance(entry, dict):
        return None
    inp = entry.get("input_cost_per_token")
    out = entry.get("output_cost_per_token")
    if inp is None or out is None:
        return None
    cr = entry.get("cache_read_input_token_cost") or inp / 10
    cw = entry.get("cache_creation_input_token_cost") or inp
    return (inp * 1e6, out * 1e6, cr * 1e6, cw * 1e6)


def _rates_for(model: str | None) -> tuple[float, float, float, float]:
    if not model:
        return _DEFAULT
    lit = _rates_from_litellm(model)
    if lit:
        return lit
    m = model.lower()
    for key, rates in _CARD:
        if key in m:
            return rates
    return _DEFAULT


def cost_for(tokens: Tokens, model: str | None) -> float:
    inp, out, cr, cw = _rates_for(model)
    return (
        tokens.input * inp
        + tokens.output * out
        + tokens.cache_read * cr
        + tokens.cache_write * cw
        + tokens.reasoning * out  # reasoning billed at output rate
    ) / 1_000_000


def cost_from_parts(model: str | None, *, input=0, output=0, cache_read=0,
                    cache_write=0, reasoning=0) -> float:
    return cost_for(
        Tokens(input=input, output=output, cache_read=cache_read,
               cache_write=cache_write, reasoning=reasoning),
        model,
    )


def refresh_litellm(timeout: float = 15.0) -> bool:
    """Download and cache LiteLLM's price card. Returns success."""
    global _litellm_loaded, _litellm_cache
    try:
        req = urllib.request.Request(_LITELLM_URL, headers={"User-Agent": "tokens-cli"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        json.loads(data)  # validate
        (cache_dir() / "litellm_prices.json").write_bytes(data)
        (cache_dir() / "litellm_prices.meta").write_text(str(int(time.time())))
        _litellm_loaded = False  # force reload next lookup
        _litellm_cache = None
        return True
    except Exception:
        return False
