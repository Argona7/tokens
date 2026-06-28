"""Unit tests for the pure-logic parts. External APIs are mocked; parsing uses
on-disk fixtures so nothing touches the real network or the user's Keychain."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tokens import util, pricing
from tokens.models import Tokens, Bucket, ToolUsage, RELIABLE, UNAVAILABLE
from tokens.render import theme


# --------------------------------------------------------------------------- util
def test_humanize_tokens():
    assert util.humanize_tokens(0) == "0"
    assert util.humanize_tokens(None) == "0"
    assert util.humanize_tokens(950) == "950"
    assert util.humanize_tokens(1500) == "1.5K"
    assert util.humanize_tokens(4_210_000) == "4.2M"
    assert util.humanize_tokens(2_000_000_000) == "2.0B"


def test_humanize_dollars():
    assert util.humanize_dollars(None) == "—"
    assert util.humanize_dollars(0) == "$0"
    assert util.humanize_dollars(0.004) == "<$0.01"
    assert util.humanize_dollars(3.4) == "$3.40"
    assert util.humanize_dollars(1500) == "$1,500"


def test_fmt_duration():
    assert util.fmt_duration(30) == "<1m"
    assert util.fmt_duration(90) == "1m"
    assert util.fmt_duration(3700) == "1h 1m"
    assert util.fmt_duration(90000) == "1d 1h"


def test_fmt_reset_rolled_over():
    now = 1000.0
    assert "rolled over" in util.fmt_reset(500.0, now)
    assert "resets in" in util.fmt_reset(4600.0, now)
    assert util.fmt_reset(None) == "—"


def test_iso_to_epoch_roundtrip():
    e = util.iso_to_epoch("2026-06-28T20:19:59.428876+00:00")
    assert e is not None and e > 1_700_000_000
    assert util.iso_to_epoch("Z-garbage") is None
    assert util.iso_to_epoch(None) is None


def test_last_n_days_len():
    assert len(util.last_n_days(30)) == 30
    assert util.last_n_days(1)


def test_safe_json_loads():
    assert util.safe_json_loads('{"a":1}') == {"a": 1}
    assert util.safe_json_loads("not json") is None


# --------------------------------------------------------------------------- pricing
def test_cost_for_family_match():
    t = Tokens(input=1_000_000, output=1_000_000)
    # opus 4.x = 5 in + 25 out per 1M (real 2026 rates)
    assert pricing.cost_for(t, "claude-opus-4-8") == pytest.approx(30.0)
    # sonnet = 3 + 15
    assert pricing.cost_for(t, "claude-sonnet-4-6") == pytest.approx(18.0)


def test_cost_unknown_model_uses_default():
    t = Tokens(input=1_000_000)
    assert pricing.cost_for(t, "totally-unknown") == pytest.approx(3.0)


def test_cost_reasoning_billed_as_output():
    assert pricing.cost_from_parts("gpt-5-codex", reasoning=1_000_000) == pytest.approx(10.0)


# --------------------------------------------------------------------------- models
def test_tokens_total_and_dict():
    t = Tokens(input=10, output=20, cache_read=5, cache_write=2, reasoning=3)
    assert t.total == 40
    assert t.to_dict()["total"] == 40


def test_tool_usage_to_dict():
    u = ToolUsage(tool="codex", plan="Plus", source=RELIABLE,
                  buckets=[Bucket("5-hour", 12.0, resets_at=123.0)])
    d = u.to_dict()
    assert d["tool"] == "codex"
    assert d["buckets"][0]["name"] == "5-hour"
    assert d["tokens"]["total"] == 0


# --------------------------------------------------------------------------- theme
def test_gauge_widths():
    g = theme.gauge(50, width=10)
    assert g.plain.count("█") == 5
    assert g.plain.count("░") == 5
    full = theme.gauge(100, width=10)
    assert full.plain.count("█") == 10


def test_sparkline_and_heatstrip():
    assert theme.sparkline([]).plain == "—"
    s = theme.sparkline([1, 5, 9])
    assert len(s.plain) == 3
    h = theme.heat_strip([0, 5, 10])
    assert h.plain == "■ ■ ■ "


def test_color_for_pct():
    from tokens.config import PALETTE
    assert theme.color_for_pct(10) == PALETTE["good"]
    assert theme.color_for_pct(60) == PALETTE["warn"]
    assert theme.color_for_pct(95) == PALETTE["bad"]


# --------------------------------------------------------------------------- claude parse
def test_claude_parse_file_and_dedup(tmp_path: Path):
    from tokens.sources import claude
    ts = "2026-06-28T10:00:00+00:00"
    rows = [
        {"type": "assistant", "timestamp": ts, "requestId": "r1",
         "message": {"id": "m1", "model": "claude-opus-4-8",
                     "usage": {"input_tokens": 100, "output_tokens": 50,
                               "cache_read_input_tokens": 10,
                               "cache_creation_input_tokens": 20}}},
        # duplicate of m1 (branch) — must not double-count across files
        {"type": "assistant", "timestamp": ts, "requestId": "r1",
         "message": {"id": "m1", "model": "claude-opus-4-8",
                     "usage": {"input_tokens": 100, "output_tokens": 50}}},
        {"type": "user", "timestamp": ts, "message": {"content": "hi"}},
    ]
    f = tmp_path / "s.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in rows))
    out = claude._parse_file(f)
    # m1 appears twice in-file (branch) -> deduped to one row; user row ignored
    assert len(out["msgs"]) == 1
    mid, mts, total, inp, out_t, cr, cw, model = out["msgs"][0]
    assert mid == "m1"
    assert inp == 100 and out_t == 50 and cr == 10 and cw == 20
    assert total == 180 and model == "claude-opus-4-8"


def test_claude_collect_dedup_across_files(tmp_path, monkeypatch):
    from tokens.sources import claude
    ts = "2026-06-28T10:00:00+00:00"
    row = {"type": "assistant", "timestamp": ts, "requestId": "r1",
           "message": {"id": "m1", "model": "claude-opus-4-8",
                       "usage": {"input_tokens": 100, "output_tokens": 50}}}
    # same message duplicated across two session files must count once
    (tmp_path / "a.jsonl").write_text(json.dumps(row))
    (tmp_path / "b.jsonl").write_text(json.dumps(row))
    monkeypatch.setattr(claude, "claude_project_roots", lambda: [tmp_path])
    totals, daily, cost, last_ts = claude._collect_local(window_days=7)
    assert totals.input == 100 and totals.output == 50  # NOT 200


def test_claude_estimate_mode(monkeypatch, tmp_path):
    from tokens.sources import claude
    monkeypatch.setattr(claude, "claude_project_roots", lambda: [tmp_path])
    monkeypatch.setattr(claude, "_read_oauth", lambda: None)
    u = claude.collect(window_days=7, use_auth=True)
    assert u.source == "ESTIMATE"
    assert u.buckets  # has at least the estimated 5-hour bucket


# --------------------------------------------------------------------------- codex parse
def test_codex_parse_rollout(tmp_path: Path):
    from tokens.sources import codex
    now = time.time()
    rows = [
        {"type": "turn_context", "payload": {"model": "gpt-5-codex"}},
        {"type": "event_msg", "timestamp": "2026-06-20T14:39:44.837Z",
         "payload": {"type": "token_count",
                     "info": {"total_token_usage": {"input_tokens": 100, "output_tokens": 20,
                              "cached_input_tokens": 5, "reasoning_output_tokens": 7,
                              "total_tokens": 132}, "model_context_window": 258400},
                     "rate_limits": {"primary": {"used_percent": 8.0, "window_minutes": 300,
                                     "resets_at": now + 3600},
                                     "secondary": {"used_percent": 31.0, "window_minutes": 10080,
                                     "resets_at": now + 86400},
                                     "plan_type": "plus", "credits": 1840}}},
    ]
    f = tmp_path / "rollout-x.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in rows))
    out = codex._parse_rollout(f)
    assert out["usage"]["total_tokens"] == 132
    assert out["rate_limits"]["plan_type"] == "plus"
    b = codex._bucket(out["rate_limits"]["primary"], "5-hour", now)
    assert b.pct == 8.0 and not b.stale


def test_codex_bucket_stale():
    from tokens.sources import codex
    now = time.time()
    b = codex._bucket({"used_percent": 8.0, "resets_at": now - 100}, "5-hour", now)
    assert b.stale is True


# --------------------------------------------------------------------------- cursor
def test_cursor_plan_and_cycle():
    from tokens.sources import cursor
    assert cursor._plan_label("ultra") == "Ultra"
    assert cursor._plan_label(None) == "—"
    assert cursor._cycle_end({"billingCycleEnd": "1784588350000"}) == pytest.approx(1784588350.0)
    assert cursor._cycle_end({}) is None


def test_cursor_collect_with_mocked_api(monkeypatch):
    from tokens.sources import cursor
    monkeypatch.setattr(cursor, "cursor_state_db", lambda: None)
    monkeypatch.setattr(cursor, "_local_activity", lambda w: ([("2026-06-28", 3)], None))
    monkeypatch.setattr(cursor, "_read_membership", lambda db: "ultra")
    monkeypatch.setattr(cursor, "_read_token", lambda db: "jwt")
    monkeypatch.setattr(cursor, "_fetch_usage", lambda t: {
        "billingCycleEnd": "1784588350000",
        "planUsage": {"limit": 40000, "remaining": 30000, "totalPercentUsed": 25.0},
        "displayMessage": "You've used 25% of your included usage",
    })
    # db None short-circuits token read; force-call path via direct injection
    monkeypatch.setattr(cursor, "cursor_state_db", lambda: Path("/x"))
    u = cursor.collect(window_days=7, use_auth=True)
    assert u.source == RELIABLE
    assert u.spend_limit == 400.0
    assert u.spend_used == 100.0
    assert u.buckets[0].pct == 25.0


def test_cursor_no_auth(monkeypatch):
    from tokens.sources import cursor
    monkeypatch.setattr(cursor, "cursor_state_db", lambda: None)
    monkeypatch.setattr(cursor, "_local_activity", lambda w: ([("2026-06-28", 0)], None))
    u = cursor.collect(window_days=7, use_auth=True)
    assert u.source == UNAVAILABLE
    assert "local activity" in u.note


# --------------------------------------------------------------------------- cli json
def test_cli_json(monkeypatch, capsys):
    from tokens import cli
    fake = ToolUsage(tool="codex", plan="Plus", source=RELIABLE, tokens=Tokens(input=5))
    monkeypatch.setattr(cli.codex_src, "collect", lambda *a, **k: fake)
    monkeypatch.setattr(cli.claude_src, "collect", lambda *a, **k: ToolUsage(tool="claude"))
    monkeypatch.setattr(cli.cursor_src, "collect", lambda *a, **k: ToolUsage(tool="cursor"))
    rc = cli.main(["--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["codex"]["plan"] == "Plus"
    assert "generated_at" in out
