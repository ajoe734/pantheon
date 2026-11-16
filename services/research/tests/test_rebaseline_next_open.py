"""Re-baseline run: old and new metrics side by side, nothing kept or removed silently."""
from __future__ import annotations

import importlib.util
import json
import shutil

import pytest

from services.execution.lean_runtime.member_virtual_book import FILL_CONVENTION, LEGACY_FILL_CONVENTION
from services.research import rebaseline_next_open as rb

HAS_VECTORBT = all(importlib.util.find_spec(m) for m in ("vectorbt", "pandas", "numpy"))


def test_committed_fixtures_are_regenerated_under_the_new_convention():
    for name in rb.BACKTEST_FIXTURES:
        recorded = json.loads((rb.FIXTURE_DIR / name).read_text(encoding="utf-8"))
        assert recorded["fill_convention"] == FILL_CONVENTION
        assert recorded["registry_entry"]["aggregate_metrics"]["fill_convention"] == FILL_CONVENTION
        assert all(m["fill_convention"] == FILL_CONVENTION
                   for m in recorded["out_of_sample"]["per_instrument_metrics"].values())


def test_legacy_baselines_are_identified_not_edited_or_removed():
    entries = [{"registry_id": "old", "checksum": "c1", "evaluation_summary": {"backtest_run_id": "r1"}},
               {"registry_id": "new", "checksum": "c2",
                "evaluation_summary": {"backtest_run_id": "r2", "fill_convention": FILL_CONVENTION}}]
    snapshot = json.dumps(entries, sort_keys=True)
    found = rb.identify_legacy_baselines(entries)
    assert [(f["registry_id"], f["status"], f["recorded_fill_convention"]) for f in found] == [
        ("old", rb.STATUS_LEGACY_BASELINE, LEGACY_FILL_CONVENTION)]
    assert json.dumps(entries, sort_keys=True) == snapshot


@pytest.mark.skipif(not HAS_VECTORBT, reason="real vectorbt is not installed")
def test_rebaseline_records_old_and_new_side_by_side_and_reports_the_artifact_that_no_longer_passes(tmp_path):
    from services.research.replication.gate_config import GateConfig

    name = rb.BACKTEST_FIXTURES[0]
    shutil.copy(rb.FIXTURE_DIR / name, tmp_path / name)
    before = (tmp_path / name).read_text(encoding="utf-8")
    row = rb.rebaseline_fixture(name, fixture_dir=tmp_path, write=False)
    assert (tmp_path / name).read_text(encoding="utf-8") == before  # a dry run touches nothing
    # the committed fixture is already regenerated; the legacy run is re-derived, not read from it
    assert row["old_recorded_matches_legacy_rerun"] is None
    assert row["old"]["fill_convention"] == LEGACY_FILL_CONVENTION and row["old"]["gate"]["passed"] is True
    assert row["new_same_series"]["fill_convention"] == FILL_CONVENTION
    # same recorded series, same unchanged thresholds, next-open fills: the old proof no longer passes
    assert row["new_same_series"]["gate"]["passed"] is False
    assert row["new_same_series"]["gate"]["failed_checks"]
    assert row["status"] == rb.STATUS_NO_LONGER_PASSES
    assert row["new_same_series"]["oos_sharpe_ratio"] < 0 < row["old"]["oos_sharpe_ratio"]
    # the regenerated fixture (re-timed series) is what the committed file records
    committed = json.loads((rb.FIXTURE_DIR / name).read_text(encoding="utf-8"))
    assert committed["out_of_sample"]["sharpe_ratio"] == row["new_retimed_series"]["oos_sharpe_ratio"]
    assert row["new_retimed_series"]["gate"]["passed"] is True
    # gate thresholds are untouched by the re-baseline
    config = GateConfig()
    assert (config.min_oos_round_trips, config.in_sample_ratio, config.walk_forward_folds) == (20, 0.7, 3)
