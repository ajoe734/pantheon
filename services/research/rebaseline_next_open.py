"""Re-baseline run for the shared daily next-open fill convention.

Every recorded backtest/replication proof computed with a daily decision filled at the close of its own
bar is re-run through the existing gate entry points (``run_replication_backtest_protocol`` and
``ReplicationGate.evaluate_candidate``) under the new convention (decision from the close of session D,
fill at the open of D+1) and recorded side by side with the old metrics.  Gate thresholds are never touched.

A proof that no longer passes is reported as ``no_longer_passes_operator_decision_required``; this module
neither keeps it silently nor removes it.

CLI::

    python -m services.research.rebaseline_next_open [--write-fixtures] [--evidence PATH]
"""
from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from services.execution.lean_runtime.member_virtual_book import FILL_CONVENTION, LEGACY_FILL_CONVENTION

FIXTURE_DIR = Path(__file__).resolve().parent / "replication" / "fixtures"
BACKTEST_FIXTURES = ("recorded_backtest_tw_2330.json", "recorded_backtest_tw_2330_2317.json")
STATUS_PASSES = "passes"
STATUS_NO_LONGER_PASSES = "no_longer_passes_operator_decision_required"
STATUS_LEGACY_BASELINE = "legacy_close_fill_baseline_rebaseline_required"


def _headline(evaluation: Mapping[str, Any]) -> dict[str, Any]:
    oos = evaluation.get("out_of_sample") or {}
    return {
        "fill_convention": evaluation.get("fill_convention", LEGACY_FILL_CONVENTION),
        "oos_sharpe_ratio": oos.get("sharpe_ratio"),
        "oos_max_drawdown": oos.get("max_drawdown"),
        "oos_total_return": oos.get("total_return"),
        "oos_round_trips": oos.get("round_trips"),
        "in_sample_sharpe_ratio": (evaluation.get("in_sample") or {}).get("sharpe_ratio"),
        "walk_forward_fold_sharpes": [f.get("sharpe_ratio") for f in evaluation.get("walk_forward_folds") or []],
    }


def run_backtest(symbols: Sequence[str], run_id: str, convention: str, **dataset_kwargs: Any) -> dict[str, Any]:
    # The legacy convention reproduces the old series; the new convention uses the re-timed default.
    if convention == LEGACY_FILL_CONVENTION:
        dataset_kwargs.setdefault("series_profile", "decision_close")
    """The existing replication backtest entry point under one fill convention (real vectorbt)."""
    from services.research.vectorbt.adapter.vectorbt_adapter import (
        BacktestConfig, build_canonical_tw_strategy_artifact, build_governed_tw_replication_dataset,
        run_replication_backtest_protocol,
    )

    previous = os.environ.get("PANTHEON_VECTORBT_BACKEND")
    os.environ["PANTHEON_VECTORBT_BACKEND"] = "real"
    try:
        artifact = build_canonical_tw_strategy_artifact(symbols=list(symbols))
        dataset = build_governed_tw_replication_dataset(symbols=list(symbols), **dataset_kwargs)
        return run_replication_backtest_protocol(
            strategy_artifact=artifact, dataset=dataset, run_id=run_id,
            config=BacktestConfig(strategy_artifact=dict(artifact), fill_convention=convention))
    finally:
        if previous is None:
            os.environ.pop("PANTHEON_VECTORBT_BACKEND", None)
        else:
            os.environ["PANTHEON_VECTORBT_BACKEND"] = previous


def gate_verdict(evaluation: Mapping[str, Any], symbols: Sequence[str]) -> dict[str, Any]:
    """Pass/fail of the existing ``ReplicationGate`` on a recorded evaluation (no thresholds changed)."""
    from services.registry.strategy_artifact import strategy_artifact_checksum
    from services.research.replication.gate import ReplicationGate
    from services.research.replication.gate_schema import ReplicationRequest
    from services.research.vectorbt.adapter.vectorbt_adapter import build_canonical_tw_strategy_artifact

    artifact = build_canonical_tw_strategy_artifact(strategy_id="strat-tw-replication-001", symbols=list(symbols))
    checksum = strategy_artifact_checksum(artifact)
    request = ReplicationRequest(
        candidate_id="rebaseline-" + str(evaluation.get("backtest_run_id")), source_task_id="RS-002",
        research_handoff={
            "source_metadata": {"api_endpoint": "https://api.openalex.org/works/W123",
                                "retrieved_at": "2026-11-10T00:00:00Z",
                                "governance_context": "Approved structured source"},
            "normalized_findings": {"strategy_spec": {"name": "tw_momentum"},
                                    "replication_notes": "Real TW backtest",
                                    "evaluation_hypotheses": "H1: OOS Sharpe > deflated threshold"}},
        proposed_strategy_spec={"name": "tw_momentum", "description": "Taiwan momentum",
                                "signals": ["momentum"], "parameters": {"lookback": 2},
                                "governance": {"approval_required": True,
                                               "policy_id": "oc-001-research-normalization"}},
        backtest_evaluation=dict(evaluation),
        metadata={"strategy_artifact_checksum": checksum, "dataset_version_id": evaluation.get("dataset_id")})
    gate = ReplicationGate()
    gate.backtest_run_store.record(dict(evaluation))
    response = gate.evaluate_candidate(request)
    failed = [r.criterion_id for r in getattr(response, "results", []) or [] if not getattr(r, "passed", True)]
    backtest = next((r for r in getattr(response, "results", []) or []
                     if getattr(r, "criterion_id", None) == "real_backtest_validation"), None)
    details = getattr(backtest, "details", None) or {}
    return {"passed": bool(response.passed), "failed_criteria": failed,
            "failed_checks": list(details.get("failed_checks") or [])}


def rebaseline_fixture(name: str, *, fixture_dir: Path = FIXTURE_DIR, write: bool = False) -> dict[str, Any]:
    """Old and new metrics side by side for one recorded backtest fixture.

    ``old``: the recorded close-fill run (reproduced).  ``new_same_series``: the same recorded series under
    the new convention - the faithful re-baseline; if it no longer passes the artifact is reported for an
    operator decision.  ``new_retimed_series``: the fixture regenerated from the re-timed default series
    (``series_profile="next_open"``), the sample used by suites that need an admitted run.
    """
    path = fixture_dir / name
    recorded = json.loads(path.read_text(encoding="utf-8"))
    params = recorded["registry_entry"]["metadata"]["strategy_artifact"]["parameters"]
    symbols, run_id = list(params["symbols"]), recorded["backtest_run_id"]
    old = run_backtest(symbols, run_id, LEGACY_FILL_CONVENTION)
    # ``None`` once the fixture has already been regenerated: the old recording is then only in the evidence.
    reproduced = (None if recorded.get("fill_convention") == FILL_CONVENTION
                  else _headline(old)["oos_sharpe_ratio"] == _headline(recorded)["oos_sharpe_ratio"])
    same = run_backtest(symbols, run_id, FILL_CONVENTION, series_profile="decision_close")
    retimed = run_backtest(symbols, run_id, FILL_CONVENTION)
    verdicts = {key: gate_verdict(ev, symbols) for key, ev in (("old", old), ("same", same), ("retimed", retimed))}
    if write:
        path.write_text(json.dumps(retimed, indent=1, sort_keys=True, default=str), encoding="utf-8")
    return {
        "fixture": name, "symbols": symbols, "backtest_run_id": run_id,
        "old_recorded_matches_legacy_rerun": reproduced,
        "old": {**_headline(old), "gate": verdicts["old"]},
        "new_same_series": {**_headline(same), "gate": verdicts["same"]},
        "new_retimed_series": {**_headline(retimed), "gate": verdicts["retimed"]},
        "status": STATUS_PASSES if verdicts["same"]["passed"] else STATUS_NO_LONGER_PASSES,
        "wrote_fixture": bool(write),
    }


def identify_legacy_baselines(entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Approved registry entries whose persisted gate proof predates the next-open convention.

    Reported, never edited or removed: the operator decides per artifact.
    """
    found = []
    for entry in entries:
        summary = entry.get("evaluation_summary") if isinstance(entry.get("evaluation_summary"), Mapping) else {}
        if summary.get("fill_convention") != FILL_CONVENTION:
            found.append({"registry_id": entry.get("registry_id"), "checksum": entry.get("checksum"),
                          "backtest_run_id": summary.get("backtest_run_id"),
                          "recorded_fill_convention": summary.get("fill_convention") or LEGACY_FILL_CONVENTION,
                          "status": STATUS_LEGACY_BASELINE})
    return found


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-fixtures", action="store_true")
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args(argv)
    rows = [rebaseline_fixture(name, write=args.write_fixtures) for name in BACKTEST_FIXTURES]
    report = {"fill_convention": FILL_CONVENTION, "replaces": LEGACY_FILL_CONVENTION, "fixtures": rows}
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
