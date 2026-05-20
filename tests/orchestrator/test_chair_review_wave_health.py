"""Tests for chair_review_wave_health.check_wave_health.

Covers all four violation types:
  - short_cycle: open→close interval below MIN_CYCLE_SECONDS
  - skipped_wave: non-sequential wave IDs between consecutive opens
  - actor_mismatch: open actor differs from close/freeze actor
  - missing_freeze: wave closed without prior freeze event
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR_DIR = REPO_ROOT / ".orchestrator"
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

from chair_review_wave_health import (
    MIN_CYCLE_SECONDS,
    _extract_wave_cycles,
    check_wave_health,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_LONG_AGO = "2026-01-01T00:00:00Z"
_LONG_AGO_PLUS_2H = "2026-01-01T02:00:00Z"
_LONG_AGO_PLUS_30M = "2026-01-01T00:30:00Z"


def _ts_offset(base: str, seconds: int) -> str:
    """Return an ISO-8601 string *seconds* after *base*."""
    dt = datetime.fromisoformat(base.replace("Z", "+00:00"))
    return (dt + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _open(wave_id: str, ts: str = _LONG_AGO, actor: str = "Codex") -> dict:
    return {"event": "open", "wave_id": wave_id, "ts": ts, "actor": actor}


def _freeze(wave_id: str, ts: str, actor: str = "Codex") -> dict:
    return {"event": "freeze", "wave_id": wave_id, "ts": ts, "actor": actor}


def _close(wave_id: str, ts: str, actor: str = "Codex") -> dict:
    return {"event": "close", "wave_id": wave_id, "ts": ts, "actor": actor}


def _healthy_cycle(wave_id: str, open_ts: str, actor: str = "Codex") -> list:
    """Return [open, freeze, close] events for a healthy 2-hour wave."""
    freeze_ts = _ts_offset(open_ts, MIN_CYCLE_SECONDS // 2)
    close_ts = _ts_offset(open_ts, MIN_CYCLE_SECONDS + 600)
    return [
        _open(wave_id, open_ts, actor),
        _freeze(wave_id, freeze_ts, actor),
        _close(wave_id, close_ts, actor),
    ]


# ---------------------------------------------------------------------------
# _extract_wave_cycles
# ---------------------------------------------------------------------------

class TestExtractWaveCycles:
    def test_empty_history(self):
        assert _extract_wave_cycles([]) == []

    def test_single_complete_cycle(self):
        history = [
            _open("2026-W01", _LONG_AGO),
            _freeze("2026-W01", _LONG_AGO_PLUS_30M),
            _close("2026-W01", _LONG_AGO_PLUS_2H),
        ]
        cycles = _extract_wave_cycles(history)
        assert len(cycles) == 1
        c = cycles[0]
        assert c["wave_id"] == "2026-W01"
        assert c["freeze_ts"] == _LONG_AGO_PLUS_30M
        assert c["close_ts"] == _LONG_AGO_PLUS_2H
        assert not c["incomplete"]

    def test_incomplete_wave_still_in_open(self):
        history = [_open("2026-W25", _LONG_AGO)]
        cycles = _extract_wave_cycles(history)
        assert len(cycles) == 1
        assert cycles[0]["incomplete"]
        assert cycles[0]["close_ts"] is None

    def test_second_open_finalises_first_as_incomplete(self):
        history = [
            _open("2026-W01", _LONG_AGO),
            _open("2026-W02", _LONG_AGO_PLUS_2H),
        ]
        cycles = _extract_wave_cycles(history)
        assert len(cycles) == 2
        assert cycles[0]["wave_id"] == "2026-W01"
        assert cycles[0]["incomplete"]
        assert cycles[1]["wave_id"] == "2026-W02"
        assert cycles[1]["incomplete"]

    def test_only_first_freeze_recorded(self):
        history = [
            _open("2026-W01", _LONG_AGO),
            _freeze("2026-W01", _LONG_AGO_PLUS_30M),
            _freeze("2026-W01", _LONG_AGO_PLUS_2H),
            _close("2026-W01", _ts_offset(_LONG_AGO, MIN_CYCLE_SECONDS + 600)),
        ]
        cycles = _extract_wave_cycles(history)
        assert len(cycles) == 1
        assert cycles[0]["freeze_ts"] == _LONG_AGO_PLUS_30M

    def test_close_without_preceding_open_ignored(self):
        history = [_close("2026-W01", _LONG_AGO)]
        cycles = _extract_wave_cycles(history)
        assert cycles == []


# ---------------------------------------------------------------------------
# Healthy path
# ---------------------------------------------------------------------------

class TestHealthyWaves:
    def test_empty_history_is_healthy(self):
        report = check_wave_health({})
        assert report["healthy"] is True
        assert report["findings"] == []
        assert report["waves_checked"] == 0

    def test_single_healthy_cycle(self):
        history = _healthy_cycle("2026-W01", _LONG_AGO)
        report = check_wave_health({"history": history})
        assert report["healthy"] is True
        assert report["findings"] == []

    def test_two_sequential_healthy_cycles(self):
        h1 = _healthy_cycle("2026-W01", "2026-01-01T00:00:00Z")
        h2 = _healthy_cycle("2026-W02", "2026-01-08T00:00:00Z")
        report = check_wave_health({"history": h1 + h2})
        assert report["healthy"] is True
        assert report["waves_checked"] == 2

    def test_year_rollover_sequential_is_healthy(self):
        # 2025 has 52 ISO weeks → W52 successor is 2026-W01
        h1 = _healthy_cycle("2025-W52", "2025-12-22T00:00:00Z")
        h2 = _healthy_cycle("2026-W01", "2026-01-05T00:00:00Z")
        report = check_wave_health({"history": h1 + h2})
        assert report["healthy"] is True
        assert not any(
            f["finding_type"] == "skipped_wave" for f in report["findings"]
        )

    def test_currently_open_wave_no_violations(self):
        """An open (incomplete) wave that has not yet been closed should not
        trigger missing_freeze or short_cycle."""
        history = [_open("2026-W25", _LONG_AGO)]
        report = check_wave_health({"history": history})
        assert report["healthy"] is True
        assert report["findings"] == []


# ---------------------------------------------------------------------------
# short_cycle
# ---------------------------------------------------------------------------

class TestShortCycle:
    def test_19_minute_wave_is_flagged(self):
        open_ts = "2026-05-17T05:23:05Z"
        close_ts = _ts_offset(open_ts, 19 * 60)  # 1140 s
        history = [
            _open("2026-W21", open_ts),
            _close("2026-W21", close_ts),
        ]
        report = check_wave_health({"history": history})
        findings = [f for f in report["findings"] if f["finding_type"] == "short_cycle"]
        assert len(findings) == 1
        assert findings[0]["wave_id"] == "2026-W21"
        assert findings[0]["duration_seconds"] == 19 * 60
        assert findings[0]["severity"] == "warn"

    def test_40_second_wave_is_flagged(self):
        open_ts = "2026-05-17T05:46:59Z"
        close_ts = _ts_offset(open_ts, 40)
        history = [
            _open("2026-W22", open_ts),
            _close("2026-W22", close_ts),
        ]
        report = check_wave_health({"history": history})
        findings = [f for f in report["findings"] if f["finding_type"] == "short_cycle"]
        assert len(findings) == 1
        assert findings[0]["duration_seconds"] == 40

    def test_exactly_min_cycle_seconds_is_not_flagged(self):
        open_ts = "2026-01-01T00:00:00Z"
        close_ts = _ts_offset(open_ts, MIN_CYCLE_SECONDS)
        history = [
            _open("2026-W01", open_ts),
            _freeze("2026-W01", _ts_offset(open_ts, MIN_CYCLE_SECONDS // 2)),
            _close("2026-W01", close_ts),
        ]
        report = check_wave_health({"history": history})
        sc = [f for f in report["findings"] if f["finding_type"] == "short_cycle"]
        assert sc == []

    def test_one_second_below_min_is_flagged(self):
        open_ts = "2026-01-01T00:00:00Z"
        close_ts = _ts_offset(open_ts, MIN_CYCLE_SECONDS - 1)
        history = [
            _open("2026-W01", open_ts),
            _close("2026-W01", close_ts),
        ]
        report = check_wave_health({"history": history})
        sc = [f for f in report["findings"] if f["finding_type"] == "short_cycle"]
        assert len(sc) == 1

    def test_unparseable_timestamps_are_skipped(self):
        history = [
            {"event": "open", "wave_id": "2026-W01", "ts": "not-a-date", "actor": "Codex"},
            {"event": "close", "wave_id": "2026-W01", "ts": "not-a-date", "actor": "Codex"},
        ]
        report = check_wave_health({"history": history})
        sc = [f for f in report["findings"] if f["finding_type"] == "short_cycle"]
        assert sc == []


# ---------------------------------------------------------------------------
# skipped_wave
# ---------------------------------------------------------------------------

class TestSkippedWave:
    def test_w22_to_w25_skips_two(self):
        h1 = _healthy_cycle("2026-W22", "2026-01-01T00:00:00Z")
        h2 = _healthy_cycle("2026-W25", "2026-01-08T00:00:00Z")
        report = check_wave_health({"history": h1 + h2})
        sw = [f for f in report["findings"] if f["finding_type"] == "skipped_wave"]
        assert len(sw) == 1
        assert sw[0]["wave_id"] == "2026-W25"
        assert sw[0]["prev_wave_id"] == "2026-W22"
        assert sw[0]["expected_wave_id"] == "2026-W23"
        assert sw[0]["severity"] == "error"

    def test_sequential_waves_no_skip(self):
        h1 = _healthy_cycle("2026-W22", "2026-01-01T00:00:00Z")
        h2 = _healthy_cycle("2026-W23", "2026-01-08T00:00:00Z")
        report = check_wave_health({"history": h1 + h2})
        sw = [f for f in report["findings"] if f["finding_type"] == "skipped_wave"]
        assert sw == []

    def test_single_wave_no_skip(self):
        history = _healthy_cycle("2026-W01", _LONG_AGO)
        report = check_wave_health({"history": history})
        sw = [f for f in report["findings"] if f["finding_type"] == "skipped_wave"]
        assert sw == []

    def test_year_rollover_w52_to_w01_no_skip(self):
        h1 = _healthy_cycle("2025-W52", "2025-12-22T00:00:00Z")
        h2 = _healthy_cycle("2026-W01", "2026-01-05T00:00:00Z")
        report = check_wave_health({"history": h1 + h2})
        sw = [f for f in report["findings"] if f["finding_type"] == "skipped_wave"]
        assert sw == []

    def test_w22_to_w23_then_w25_two_cycles(self):
        h1 = _healthy_cycle("2026-W22", "2026-01-01T00:00:00Z")
        h2 = _healthy_cycle("2026-W23", "2026-01-08T00:00:00Z")
        h3 = _healthy_cycle("2026-W25", "2026-01-15T00:00:00Z")
        report = check_wave_health({"history": h1 + h2 + h3})
        sw = [f for f in report["findings"] if f["finding_type"] == "skipped_wave"]
        assert len(sw) == 1
        assert sw[0]["prev_wave_id"] == "2026-W23"


# ---------------------------------------------------------------------------
# actor_mismatch
# ---------------------------------------------------------------------------

class TestActorMismatch:
    def test_different_close_actor_is_flagged(self):
        open_ts = "2026-01-01T00:00:00Z"
        close_ts = _ts_offset(open_ts, MIN_CYCLE_SECONDS + 600)
        freeze_ts = _ts_offset(open_ts, MIN_CYCLE_SECONDS // 2)
        history = [
            _open("2026-W01", open_ts, actor="Codex"),
            _freeze("2026-W01", freeze_ts, actor="Codex"),
            _close("2026-W01", close_ts, actor="Claude"),
        ]
        report = check_wave_health({"history": history})
        am = [f for f in report["findings"] if f["finding_type"] == "actor_mismatch"]
        assert len(am) == 1
        assert am[0]["wave_id"] == "2026-W01"
        assert am[0]["open_actor"] == "Codex"
        assert am[0]["close_actor"] == "Claude"
        assert am[0]["severity"] == "warn"

    def test_different_freeze_actor_is_flagged(self):
        open_ts = "2026-01-01T00:00:00Z"
        freeze_ts = _ts_offset(open_ts, MIN_CYCLE_SECONDS // 2)
        close_ts = _ts_offset(open_ts, MIN_CYCLE_SECONDS + 600)
        history = [
            _open("2026-W01", open_ts, actor="Codex"),
            _freeze("2026-W01", freeze_ts, actor="Gemini"),
            _close("2026-W01", close_ts, actor="Codex"),
        ]
        report = check_wave_health({"history": history})
        am = [f for f in report["findings"] if f["finding_type"] == "actor_mismatch"]
        assert len(am) == 1
        assert am[0]["freeze_actor"] == "Gemini"

    def test_same_actor_throughout_is_ok(self):
        history = _healthy_cycle("2026-W01", _LONG_AGO, actor="Codex")
        report = check_wave_health({"history": history})
        am = [f for f in report["findings"] if f["finding_type"] == "actor_mismatch"]
        assert am == []

    def test_no_open_actor_skips_check(self):
        open_ts = "2026-01-01T00:00:00Z"
        close_ts = _ts_offset(open_ts, MIN_CYCLE_SECONDS + 600)
        history = [
            {"event": "open", "wave_id": "2026-W01", "ts": open_ts},  # no actor
            _close("2026-W01", close_ts, actor="Claude"),
        ]
        report = check_wave_health({"history": history})
        am = [f for f in report["findings"] if f["finding_type"] == "actor_mismatch"]
        assert am == []


# ---------------------------------------------------------------------------
# missing_freeze
# ---------------------------------------------------------------------------

class TestMissingFreeze:
    def test_closed_without_freeze_is_flagged(self):
        open_ts = "2026-05-17T05:23:05Z"
        close_ts = _ts_offset(open_ts, MIN_CYCLE_SECONDS + 600)
        history = [
            _open("2026-W21", open_ts),
            _close("2026-W21", close_ts),
        ]
        report = check_wave_health({"history": history})
        mf = [f for f in report["findings"] if f["finding_type"] == "missing_freeze"]
        assert len(mf) == 1
        assert mf[0]["wave_id"] == "2026-W21"
        assert mf[0]["severity"] == "error"

    def test_wave_with_freeze_is_ok(self):
        history = _healthy_cycle("2026-W01", _LONG_AGO)
        report = check_wave_health({"history": history})
        mf = [f for f in report["findings"] if f["finding_type"] == "missing_freeze"]
        assert mf == []

    def test_still_open_wave_not_flagged(self):
        history = [_open("2026-W25", _LONG_AGO)]
        report = check_wave_health({"history": history})
        mf = [f for f in report["findings"] if f["finding_type"] == "missing_freeze"]
        assert mf == []

    def test_frozen_but_not_closed_not_flagged(self):
        open_ts = "2026-01-01T00:00:00Z"
        freeze_ts = _ts_offset(open_ts, MIN_CYCLE_SECONDS // 2)
        history = [
            _open("2026-W25", open_ts),
            _freeze("2026-W25", freeze_ts),
            # no close yet
        ]
        report = check_wave_health({"history": history})
        mf = [f for f in report["findings"] if f["finding_type"] == "missing_freeze"]
        assert mf == []


# ---------------------------------------------------------------------------
# Combined: real-world violation scenario from WAVE_CADENCE_ADJUSTMENT_PROPOSAL.md
# ---------------------------------------------------------------------------

class TestRealWorldScenario:
    """Reproduce the actual bad history from the proposal."""

    def _build_history(self) -> list:
        return [
            {"ts": "2026-05-17T05:23:05Z", "event": "open",  "wave_id": "2026-W21", "actor": "Codex", "branch": "wave/2026-W21"},
            {"ts": "2026-05-17T05:42:47Z", "event": "close", "wave_id": "2026-W21", "actor": "Codex"},
            {"ts": "2026-05-17T05:46:59Z", "event": "open",  "wave_id": "2026-W22", "actor": "Codex", "branch": "wave/2026-W22"},
            {"ts": "2026-05-17T05:47:39Z", "event": "close", "wave_id": "2026-W22", "actor": "Codex"},
            {"ts": "2026-05-17T07:02:39Z", "event": "open",  "wave_id": "2026-W25", "actor": "Codex", "branch": "wave/2026-W25"},
        ]

    def test_detects_short_cycles_for_w21_and_w22(self):
        report = check_wave_health({"history": self._build_history()})
        sc = [f for f in report["findings"] if f["finding_type"] == "short_cycle"]
        wave_ids = {f["wave_id"] for f in sc}
        assert "2026-W21" in wave_ids
        assert "2026-W22" in wave_ids

    def test_detects_skipped_wave_w22_to_w25(self):
        report = check_wave_health({"history": self._build_history()})
        sw = [f for f in report["findings"] if f["finding_type"] == "skipped_wave"]
        assert any(f["wave_id"] == "2026-W25" for f in sw)

    def test_detects_missing_freeze_for_w21_and_w22(self):
        report = check_wave_health({"history": self._build_history()})
        mf = [f for f in report["findings"] if f["finding_type"] == "missing_freeze"]
        wave_ids = {f["wave_id"] for f in mf}
        assert "2026-W21" in wave_ids
        assert "2026-W22" in wave_ids

    def test_report_is_not_healthy(self):
        report = check_wave_health({"history": self._build_history()})
        assert report["healthy"] is False

    def test_waves_checked_count(self):
        report = check_wave_health({"history": self._build_history()})
        # W21 (closed), W22 (closed), W25 (open/incomplete)
        assert report["waves_checked"] == 3

    def test_summary_is_non_empty_string(self):
        report = check_wave_health({"history": self._build_history()})
        assert isinstance(report["summary"], str)
        assert len(report["summary"]) > 0

    def test_no_actor_mismatch_when_same_actor(self):
        """All events by Codex — no actor mismatch expected."""
        report = check_wave_health({"history": self._build_history()})
        am = [f for f in report["findings"] if f["finding_type"] == "actor_mismatch"]
        assert am == []


# ---------------------------------------------------------------------------
# Report structure
# ---------------------------------------------------------------------------

class TestReportStructure:
    def test_healthy_report_keys(self):
        report = check_wave_health({})
        assert set(report.keys()) >= {"healthy", "waves_checked", "findings", "summary"}

    def test_unhealthy_report_has_findings(self):
        open_ts = "2026-01-01T00:00:00Z"
        close_ts = _ts_offset(open_ts, 60)  # 60 s — very short
        history = [_open("2026-W01", open_ts), _close("2026-W01", close_ts)]
        report = check_wave_health({"history": history})
        assert report["healthy"] is False
        assert len(report["findings"]) > 0
        for f in report["findings"]:
            assert "finding_type" in f
            assert "wave_id" in f
            assert "detail" in f
            assert f["severity"] in ("warn", "error")

    def test_summary_mentions_violation_count_for_errors(self):
        open_ts = "2026-01-01T00:00:00Z"
        close_ts = _ts_offset(open_ts, MIN_CYCLE_SECONDS + 600)
        # missing_freeze → error
        history = [_open("2026-W01", open_ts), _close("2026-W01", close_ts)]
        report = check_wave_health({"history": history})
        assert "error" in report["summary"]
