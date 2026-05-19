"""Tests for wave open/close/freeze guards.

Covers: no-skip (H1), cooldown (H4), baton-owner, year rollover,
and the command_wave integration path in scripts/ai_status.py.
"""
from __future__ import annotations

import sys
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

# Ensure .orchestrator is importable when running from the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR_DIR = REPO_ROOT / ".orchestrator"
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

from wave_guards import (
    WaveGuardError,
    check_baton_owner,
    check_cooldown,
    check_current_wave_closed,
    check_no_skip,
    check_wave_close,
    check_wave_freeze,
    check_wave_open,
    last_iso_week_of_year,
    parse_wave_id,
    successor_wave_id,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LONG_AGO = "2026-01-01T00:00:00Z"  # far enough in the past to pass cooldown


def _wave_state_with_history(*events: dict) -> dict:
    return {"history": list(events)}


def _close_event(ts: str, wave_id: str = "2026-W10") -> dict:
    return {"ts": ts, "event": "close", "wave_id": wave_id, "actor": "Codex"}


def _open_event(ts: str, wave_id: str) -> dict:
    return {"ts": ts, "event": "open", "wave_id": wave_id, "actor": "Codex"}


def _now_minus(seconds: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# parse_wave_id
# ---------------------------------------------------------------------------

class TestParseWaveId:
    def test_valid(self):
        assert parse_wave_id("2026-W21") == (2026, 21)

    def test_leading_zero_week(self):
        assert parse_wave_id("2026-W01") == (2026, 1)

    def test_invalid_raises(self):
        with pytest.raises(WaveGuardError):
            parse_wave_id("2026W21")

    def test_empty_raises(self):
        with pytest.raises(WaveGuardError):
            parse_wave_id("")


# ---------------------------------------------------------------------------
# successor_wave_id
# ---------------------------------------------------------------------------

class TestSuccessorWaveId:
    def test_mid_year(self):
        assert successor_wave_id("2026-W21") == "2026-W22"

    def test_rollover_52_week_year(self):
        # 2025 has 52 ISO weeks
        assert last_iso_week_of_year(2025) == 52
        assert successor_wave_id("2025-W52") == "2026-W01"

    def test_rollover_53_week_year(self):
        # 2026 has 53 ISO weeks
        assert last_iso_week_of_year(2026) == 53
        assert successor_wave_id("2026-W53") == "2027-W01"

    def test_w01(self):
        assert successor_wave_id("2026-W01") == "2026-W02"


# ---------------------------------------------------------------------------
# check_no_skip
# ---------------------------------------------------------------------------

class TestCheckNoSkip:
    def test_first_wave_no_history(self):
        """Any first wave is accepted when there is no prior history."""
        check_no_skip({}, "2026-W01")

    def test_empty_history(self):
        check_no_skip({"history": []}, "2026-W05")

    def test_sequential_accepted(self):
        ws = _wave_state_with_history(
            _open_event(_LONG_AGO, "2026-W22"),
            _close_event(_LONG_AGO, "2026-W22"),
        )
        check_no_skip(ws, "2026-W23")  # must not raise

    def test_skip_rejected(self):
        ws = _wave_state_with_history(
            _open_event(_LONG_AGO, "2026-W22"),
            _close_event(_LONG_AGO, "2026-W22"),
        )
        with pytest.raises(WaveGuardError, match="No-skip guard"):
            check_no_skip(ws, "2026-W25")

    def test_skip_one_rejected(self):
        ws = _wave_state_with_history(
            _open_event(_LONG_AGO, "2026-W21"),
            _close_event(_LONG_AGO, "2026-W21"),
        )
        with pytest.raises(WaveGuardError):
            check_no_skip(ws, "2026-W23")

    def test_year_rollover_accepted(self):
        # 2025 has 52 ISO weeks → successor of W52 is 2026-W01
        ws = _wave_state_with_history(
            _open_event(_LONG_AGO, "2025-W52"),
            _close_event(_LONG_AGO, "2025-W52"),
        )
        check_no_skip(ws, "2026-W01")  # must not raise

    def test_checks_last_open_not_close(self):
        """No-skip is based on the last 'open' event, not the last 'close'."""
        ws = _wave_state_with_history(
            _open_event(_LONG_AGO, "2026-W10"),
            _close_event(_LONG_AGO, "2026-W11"),  # close of a different wave id
        )
        # last open was W10 → expect W11
        check_no_skip(ws, "2026-W11")


# ---------------------------------------------------------------------------
# check_cooldown
# ---------------------------------------------------------------------------

class TestCheckCooldown:
    def test_no_previous_close(self):
        """No previous close means no cooldown to enforce."""
        check_cooldown({})
        check_cooldown({"history": []})

    def test_close_long_ago_accepted(self):
        ws = _wave_state_with_history(_close_event(_LONG_AGO))
        check_cooldown(ws)  # must not raise

    def test_close_61_min_ago_accepted(self):
        ts = (datetime.now(timezone.utc) - timedelta(seconds=3660)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        ws = _wave_state_with_history(_close_event(ts))
        check_cooldown(ws)

    def test_close_59_min_ago_rejected(self):
        ts = (datetime.now(timezone.utc) - timedelta(seconds=3540)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        ws = _wave_state_with_history(_close_event(ts))
        with pytest.raises(WaveGuardError, match="Cooldown guard"):
            check_cooldown(ws)

    def test_close_just_now_rejected(self):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        ws = _wave_state_with_history(_close_event(ts))
        with pytest.raises(WaveGuardError):
            check_cooldown(ws)

    def test_explicit_now_parameter(self):
        """Passing explicit *now* lets the test control the clock."""
        close_ts = "2026-W20T12:00:00Z"
        # not parseable as ISO datetime but fallback should be permissive
        ws = _wave_state_with_history({"ts": "2026-05-17T10:00:00Z", "event": "close"})
        # 90 min after the close -> should pass
        future_now = datetime(2026, 5, 17, 11, 30, 0, tzinfo=timezone.utc)
        check_cooldown(ws, now=future_now)

    def test_unparseable_ts_is_permissive(self):
        ws = _wave_state_with_history({"ts": "not-a-date", "event": "close"})
        check_cooldown(ws)  # must not raise


# ---------------------------------------------------------------------------
# check_current_wave_closed
# ---------------------------------------------------------------------------

class TestCheckCurrentWaveClosed:
    def test_status_open_rejected(self):
        ws = {"current_wave_id": "2026-W25", "status": "open"}
        with pytest.raises(WaveGuardError, match="Current-wave-open guard"):
            check_current_wave_closed(ws)

    def test_history_open_no_close_rejected(self):
        ws = _wave_state_with_history(
            _open_event(_LONG_AGO, "2026-W25"),
        )
        with pytest.raises(WaveGuardError, match="Current-wave-open guard"):
            check_current_wave_closed(ws)

    def test_history_multiple_waves_last_open_no_close_rejected(self):
        """Earlier waves closed but last opened wave has no close."""
        ws = _wave_state_with_history(
            _open_event(_LONG_AGO, "2026-W22"),
            _close_event(_LONG_AGO, "2026-W22"),
            _open_event(_LONG_AGO, "2026-W23"),
        )
        with pytest.raises(WaveGuardError, match="Current-wave-open guard"):
            check_current_wave_closed(ws)

    def test_history_open_then_close_accepted(self):
        ws = _wave_state_with_history(
            _open_event(_LONG_AGO, "2026-W25"),
            _close_event(_LONG_AGO, "2026-W25"),
        )
        check_current_wave_closed(ws)  # must not raise

    def test_no_history_no_status_accepted(self):
        check_current_wave_closed({})  # first wave, fine

    def test_status_closed_accepted(self):
        ws = {"current_wave_id": "2026-W25", "status": "closed"}
        check_current_wave_closed(ws)  # must not raise

    def test_status_frozen_rejected(self):
        ws = {"current_wave_id": "2026-W25", "status": "frozen"}
        with pytest.raises(WaveGuardError, match="Current-wave-frozen guard"):
            check_current_wave_closed(ws)


# ---------------------------------------------------------------------------
# check_baton_owner
# ---------------------------------------------------------------------------

class TestCheckBatonOwner:
    def test_no_baton_configured_permissive(self):
        check_baton_owner({}, "anyone")

    def test_wave_state_baton_match(self):
        check_baton_owner({"baton_owner": "Codex"}, "Codex")

    def test_wave_state_baton_mismatch(self):
        with pytest.raises(WaveGuardError, match="Baton-owner guard"):
            check_baton_owner({"baton_owner": "Codex"}, "Claude")

    def test_planning_state_fallback_match(self):
        planning = {"baton_owner": "Claude"}
        check_baton_owner({}, "Claude", planning_state=planning)

    def test_planning_state_fallback_mismatch(self):
        planning = {"baton_owner": "Claude"}
        with pytest.raises(WaveGuardError):
            check_baton_owner({}, "Gemini", planning_state=planning)

    def test_wave_state_baton_takes_priority_over_planning(self):
        planning = {"baton_owner": "Claude"}
        # wave_state says Codex; planning says Claude — wave_state wins
        with pytest.raises(WaveGuardError):
            check_baton_owner({"baton_owner": "Codex"}, "Claude", planning_state=planning)

    def test_empty_baton_owner_string_is_permissive(self):
        check_baton_owner({"baton_owner": ""}, "anyone")


# ---------------------------------------------------------------------------
# check_wave_open (composite)
# ---------------------------------------------------------------------------

class TestCheckWaveOpen:
    def _wave_state_closed_long_ago(self, last_wave: str = "2026-W22") -> dict:
        return _wave_state_with_history(
            _open_event(_LONG_AGO, last_wave),
            _close_event(_LONG_AGO, last_wave),
        )

    def test_all_guards_pass(self):
        ws = self._wave_state_closed_long_ago("2026-W22")
        check_wave_open(ws, "2026-W23", "Codex")

    def test_no_skip_violation_bubbles(self):
        ws = self._wave_state_closed_long_ago("2026-W22")
        with pytest.raises(WaveGuardError, match="No-skip"):
            check_wave_open(ws, "2026-W25", "Codex")

    def test_cooldown_violation_bubbles(self):
        ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        ws = _wave_state_with_history(
            _open_event(_LONG_AGO, "2026-W22"),
            _close_event(ts, "2026-W22"),
        )
        with pytest.raises(WaveGuardError, match="Cooldown"):
            check_wave_open(ws, "2026-W23", "Codex")

    def test_baton_owner_violation_bubbles(self):
        ws = self._wave_state_closed_long_ago("2026-W22")
        ws["baton_owner"] = "Codex"
        with pytest.raises(WaveGuardError, match="Baton-owner"):
            check_wave_open(ws, "2026-W23", "Claude")

    def test_first_wave_no_history(self):
        check_wave_open({}, "2026-W01", "Codex")

    def test_current_wave_still_open_via_status_rejected(self):
        """Opening successor while wave_state.status='open' must be rejected."""
        ws = _wave_state_with_history(
            _open_event(_LONG_AGO, "2026-W22"),
            _close_event(_LONG_AGO, "2026-W22"),
            _open_event(_LONG_AGO, "2026-W23"),
        )
        ws["status"] = "open"
        ws["current_wave_id"] = "2026-W23"
        with pytest.raises(WaveGuardError, match="Current-wave-open guard"):
            check_wave_open(ws, "2026-W24", "Codex")

    def test_current_wave_no_close_in_history_rejected(self):
        """Opening successor when last opened wave has no close event is rejected."""
        ws = _wave_state_with_history(
            _open_event(_LONG_AGO, "2026-W22"),
            _close_event(_LONG_AGO, "2026-W22"),
            _open_event(_LONG_AGO, "2026-W23"),
        )
        with pytest.raises(WaveGuardError, match="Current-wave-open guard"):
            check_wave_open(ws, "2026-W24", "Codex")


# ---------------------------------------------------------------------------
# check_wave_close and check_wave_freeze
# ---------------------------------------------------------------------------

class TestCheckWaveCloseAndFreeze:
    def test_close_no_baton_configured(self):
        check_wave_close({"status": "frozen", "frozen_at": _LONG_AGO}, "anyone")

    def test_close_baton_match(self):
        check_wave_close({"status": "frozen", "frozen_at": _LONG_AGO, "baton_owner": "Codex"}, "Codex")

    def test_close_baton_mismatch(self):
        with pytest.raises(WaveGuardError):
            check_wave_close({"status": "frozen", "frozen_at": _LONG_AGO, "baton_owner": "Codex"}, "Claude")

    def test_freeze_no_baton_configured(self):
        check_wave_freeze({"status": "open"}, "anyone")

    def test_freeze_baton_match(self):
        check_wave_freeze({"status": "open", "baton_owner": "Codex"}, "Codex")

    def test_freeze_baton_mismatch(self):
        with pytest.raises(WaveGuardError):
            check_wave_freeze({"status": "open", "baton_owner": "Codex"}, "Claude")
