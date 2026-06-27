"""Unit tests for services.source_ingestion.scheduler_worker."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.source_ingestion.scheduler_worker import (
    SchedulerState,
    _utc_now,
    main,
    run_tick,
)


# ---------------------------------------------------------------------------
# SchedulerState
# ---------------------------------------------------------------------------


class TestSchedulerStateNoPath:
    def test_initial_state_is_empty(self) -> None:
        s = SchedulerState()
        assert s.last_success_at is None
        assert s.last_failure_at is None
        assert s.last_failure_error is None
        assert s.missed_tick_count == 0
        assert s.total_successes == 0
        assert s.total_failures == 0

    def test_record_success_updates_fields(self) -> None:
        s = SchedulerState()
        s.record_success()
        assert s.last_success_at is not None
        assert s.missed_tick_count == 0
        assert s.total_successes == 1
        assert s.total_failures == 0

    def test_record_failure_increments_missed(self) -> None:
        s = SchedulerState()
        s.record_failure("URLError: connection refused")
        assert s.last_failure_at is not None
        assert s.last_failure_error == "URLError: connection refused"
        assert s.missed_tick_count == 1
        assert s.total_failures == 1

    def test_success_resets_missed_tick_count(self) -> None:
        s = SchedulerState()
        s.record_failure("err1")
        s.record_failure("err2")
        assert s.missed_tick_count == 2
        s.record_success()
        assert s.missed_tick_count == 0

    def test_to_dict_includes_all_metrics(self) -> None:
        s = SchedulerState()
        s.record_success()
        d = s.to_dict()
        assert "last_success_at" in d
        assert "last_failure_at" in d
        assert "missed_tick_count" in d
        assert "total_successes" in d
        assert "total_failures" in d

    def test_compute_startup_missed_without_last_success_returns_zero(self) -> None:
        s = SchedulerState()
        assert s.compute_startup_missed(interval_seconds=60) == 0

    def test_compute_startup_missed_recent_success_returns_zero(self) -> None:
        s = SchedulerState()
        s.last_success_at = _utc_now()
        assert s.compute_startup_missed(interval_seconds=60) == 0

    def test_compute_startup_missed_counts_elapsed_intervals(self) -> None:
        s = SchedulerState()
        past = (datetime.now(timezone.utc) - timedelta(seconds=300)).replace(microsecond=0)
        s.last_success_at = past.isoformat().replace("+00:00", "Z")
        # 300s elapsed / 60s interval = 5 raw intervals; subtract 1 for the imminent tick
        missed = s.compute_startup_missed(interval_seconds=60)
        assert missed >= 3  # at least 3 (allows for small timing slop)

    def test_compute_startup_missed_subtracts_one_for_imminent_tick(self) -> None:
        s = SchedulerState()
        # Exactly one interval ago → 0 missed (the coming tick covers it)
        past = (datetime.now(timezone.utc) - timedelta(seconds=65)).replace(microsecond=0)
        s.last_success_at = past.isoformat().replace("+00:00", "Z")
        missed = s.compute_startup_missed(interval_seconds=60)
        assert missed == 0

    def test_compute_startup_missed_anchors_on_latest_failure_not_last_success(self) -> None:
        # Regression: if failures occurred after last_success_at they are already
        # counted in missed_tick_count.  compute_startup_missed must not re-count
        # them by anchoring on last_success_at.  Anchor must be the latest tick
        # attempt (max of last_success_at and last_failure_at).
        s = SchedulerState()
        # Last success was 10 minutes ago; 9 failures were recorded since then.
        s.last_success_at = (datetime.now(timezone.utc) - timedelta(seconds=600)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        # Most recent failure was 30s ago — well within one interval.
        s.last_failure_at = (datetime.now(timezone.utc) - timedelta(seconds=30)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        s.missed_tick_count = 9
        # Only 30s elapsed since last tick attempt → 0 additional missed ticks.
        # The old code anchored on last_success_at (600s ago) and would return ~8.
        assert s.compute_startup_missed(interval_seconds=60) == 0

    def test_save_is_noop_without_path(self) -> None:
        s = SchedulerState()
        s.record_success()  # Should not raise even without a path


class TestSchedulerStateWithPath:
    def test_state_is_persisted_and_reloaded(self, tmp_path: Path) -> None:
        p = tmp_path / "state.json"
        s = SchedulerState(p)
        s.record_success()
        s.record_failure("oops")

        s2 = SchedulerState(p)
        assert s2.last_success_at == s.last_success_at
        assert s2.last_failure_at == s.last_failure_at
        assert s2.last_failure_error == "oops"
        assert s2.total_successes == 1
        assert s2.total_failures == 1
        assert s2.missed_tick_count == 1

    def test_missing_state_file_is_tolerated(self, tmp_path: Path) -> None:
        p = tmp_path / "nonexistent.json"
        s = SchedulerState(p)
        assert s.last_success_at is None
        assert s.missed_tick_count == 0

    def test_corrupt_state_file_is_tolerated(self, tmp_path: Path) -> None:
        p = tmp_path / "state.json"
        p.write_text("not json{{{", encoding="utf-8")
        s = SchedulerState(p)
        assert s.last_success_at is None

    def test_missed_tick_persists_across_restart(self, tmp_path: Path) -> None:
        p = tmp_path / "state.json"
        s = SchedulerState(p)
        s.record_failure("conn refused")
        s.record_failure("conn refused")

        s2 = SchedulerState(p)
        assert s2.missed_tick_count == 2

    def test_success_resets_persisted_missed_count(self, tmp_path: Path) -> None:
        p = tmp_path / "state.json"
        s = SchedulerState(p)
        s.record_failure("err")
        s.record_success()

        s2 = SchedulerState(p)
        assert s2.missed_tick_count == 0

    def test_compute_startup_missed_from_persisted_state(self, tmp_path: Path) -> None:
        p = tmp_path / "state.json"
        s = SchedulerState(p)
        past = (datetime.now(timezone.utc) - timedelta(seconds=250)).replace(microsecond=0)
        s.last_success_at = past.isoformat().replace("+00:00", "Z")
        s.save()

        s2 = SchedulerState(p)
        missed = s2.compute_startup_missed(interval_seconds=60)
        # 250s / 60s = 4 intervals, minus 1 = 3 missed
        assert missed >= 2

    def test_alive_file_is_written(self, tmp_path: Path) -> None:
        alive = tmp_path / "scheduler_alive"
        state_path = tmp_path / "state.json"

        with (
            patch("services.source_ingestion.scheduler_worker.run_tick", return_value={"ran": 0}),
            patch("services.source_ingestion.scheduler_worker.time") as mock_time,
        ):
            mock_time.sleep = MagicMock()
            import os

            env_overrides = {
                "SOURCE_INGEST_API_URL": "http://localhost:9999",
                "SOURCE_INGEST_SCHEDULER_INTERVAL_SECONDS": "60",
                "SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY": "1",
                "SOURCE_INGEST_SCHEDULER_MAX_TICKS": "1",
                "SOURCE_INGEST_SCHEDULER_STATE_PATH": str(state_path),
                "SOURCE_INGEST_SCHEDULER_ALIVE_PATH": str(alive),
            }
            with patch.dict(os.environ, env_overrides):
                from services.source_ingestion import scheduler_worker

                result = scheduler_worker.main()

        assert result == 0
        assert alive.exists()


# ---------------------------------------------------------------------------
# main() integration
# ---------------------------------------------------------------------------


class TestMain:
    def _run_main(self, tmp_path: Path, tick_results: list) -> tuple[int, list[dict]]:
        """Run main() with mocked tick results; return (exit_code, log_lines)."""
        import os
        from io import StringIO

        state_path = tmp_path / "state.json"
        log_lines: list[dict] = []

        tick_iter = iter(tick_results)

        def fake_run_tick(**kwargs: object) -> dict:
            val = next(tick_iter)
            if isinstance(val, Exception):
                raise val
            return val  # type: ignore[return-value]

        env_overrides = {
            "SOURCE_INGEST_API_URL": "http://localhost:9999",
            "SOURCE_INGEST_SCHEDULER_INTERVAL_SECONDS": "60",
            "SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY": "1",
            "SOURCE_INGEST_SCHEDULER_MAX_TICKS": str(len(tick_results)),
            "SOURCE_INGEST_SCHEDULER_STATE_PATH": str(state_path),
            "SOURCE_INGEST_SCHEDULER_ALIVE_PATH": "",
        }

        with (
            patch("services.source_ingestion.scheduler_worker.run_tick", side_effect=fake_run_tick),
            patch("services.source_ingestion.scheduler_worker.time") as mock_time,
            patch("builtins.print", side_effect=lambda msg, **_: log_lines.append(json.loads(msg))),
            patch.dict(os.environ, env_overrides),
        ):
            mock_time.sleep = MagicMock()
            from services.source_ingestion import scheduler_worker

            code = scheduler_worker.main()

        return code, log_lines

    def test_success_tick_emits_last_success_at(self, tmp_path: Path) -> None:
        code, lines = self._run_main(tmp_path, [{"ran": 1}])
        assert code == 0
        tick_line = next(l for l in lines if "tick" in l)
        assert tick_line["last_success_at"] is not None
        assert tick_line["missed_tick_count"] == 0

    def test_failed_tick_emits_error_and_missed_count(self, tmp_path: Path) -> None:
        import urllib.error

        code, lines = self._run_main(tmp_path, [urllib.error.URLError("refused")])
        assert code == 0
        tick_line = next(l for l in lines if "tick" in l)
        assert "error" in tick_line
        assert tick_line["missed_tick_count"] == 1
        assert tick_line["last_failure_at"] is not None

    def test_failure_then_success_resets_missed_count(self, tmp_path: Path) -> None:
        import urllib.error

        code, lines = self._run_main(
            tmp_path,
            [urllib.error.URLError("refused"), {"ran": 1}],
        )
        assert code == 0
        tick_lines = [l for l in lines if "tick" in l]
        assert tick_lines[0]["missed_tick_count"] == 1
        assert tick_lines[1]["missed_tick_count"] == 0

    def test_startup_line_is_emitted(self, tmp_path: Path) -> None:
        code, lines = self._run_main(tmp_path, [{"ran": 0}])
        assert code == 0
        startup = next((l for l in lines if l.get("event") == "startup"), None)
        assert startup is not None
        assert "startup_missed_ticks" in startup

    def test_startup_missed_ticks_reported_on_restart(self, tmp_path: Path) -> None:
        import os

        state_path = tmp_path / "state.json"
        # Simulate a previous run that succeeded 5 minutes ago
        past = (datetime.now(timezone.utc) - timedelta(seconds=310)).replace(microsecond=0)
        state_path.write_text(
            json.dumps({"last_success_at": past.isoformat().replace("+00:00", "Z"), "missed_tick_count": 0}),
            encoding="utf-8",
        )

        env_overrides = {
            "SOURCE_INGEST_API_URL": "http://localhost:9999",
            "SOURCE_INGEST_SCHEDULER_INTERVAL_SECONDS": "60",
            "SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY": "1",
            "SOURCE_INGEST_SCHEDULER_MAX_TICKS": "1",
            "SOURCE_INGEST_SCHEDULER_STATE_PATH": str(state_path),
            "SOURCE_INGEST_SCHEDULER_ALIVE_PATH": "",
        }
        log_lines: list[dict] = []
        with (
            patch("services.source_ingestion.scheduler_worker.run_tick", return_value={"ran": 0}),
            patch("services.source_ingestion.scheduler_worker.time") as mock_time,
            patch("builtins.print", side_effect=lambda msg, **_: log_lines.append(json.loads(msg))),
            patch.dict(os.environ, env_overrides),
        ):
            mock_time.sleep = MagicMock()
            from services.source_ingestion import scheduler_worker

            scheduler_worker.main()

        startup = next(l for l in log_lines if l.get("event") == "startup")
        assert startup["startup_missed_ticks"] >= 3
