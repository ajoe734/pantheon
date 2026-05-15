#!/usr/bin/env python3
"""Evaluate research/OSS production-activation gates.

This verifier keeps activation truth separate from runnable baseline maturity.
It can emit a current repo report with no external evidence, or validate a
future evidence JSON packet before any matrix row is promoted to production.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MLFLOW_GOVERNED_SINCE = date(2026, 4, 15)
WANDB_EARLIEST_REOPEN = date(2026, 5, 15)


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def path_exists(rel_path: str) -> bool:
    return (ROOT / rel_path).exists()


def all_paths_exist(paths: list[str]) -> bool:
    return all(path_exists(path) for path in paths)


@dataclass(frozen=True)
class GateResult:
    row: str
    production_activated: bool
    repo_baseline_ready: bool
    status: str
    blockers: list[str]
    evidence: dict[str, Any]
    next_truthful_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "row": self.row,
            "production_activated": self.production_activated,
            "repo_baseline_ready": self.repo_baseline_ready,
            "status": self.status,
            "blockers": self.blockers,
            "evidence": self.evidence,
            "next_truthful_action": self.next_truthful_action,
        }


def evaluate_qlib(evidence: dict[str, Any]) -> GateResult:
    repo_ready = all_paths_exist(
        [
            "services/research/qlib/adapter/qlib_adapter.py",
            "services/research/qlib/smoke_test.py",
            "services/research/qlib/requirements.txt",
            "integrations/qlib/activation_packet.md",
        ]
    )
    row = evidence.get("qlib") or {}
    instruments = int(row.get("dataset_instruments") or 0)
    years = float(row.get("dataset_years") or 0.0)
    blockers: list[str] = []
    if not row.get("rs003_candidate_passed"):
        blockers.append("RS-003 candidate proof missing")
    if instruments < 50:
        blockers.append("governed dataset has fewer than 50 instruments")
    if years < 2.0:
        blockers.append("governed dataset covers fewer than 2 years")
    if not row.get("strategy_spec_binding"):
        blockers.append("target StrategySpec binding missing")
    if not row.get("activation_run_archived"):
        blockers.append("first governed LightGBM activation run not archived")
    activated = repo_ready and not blockers
    return GateResult(
        row="Qlib",
        production_activated=activated,
        repo_baseline_ready=repo_ready,
        status="production_activated" if activated else "smoke_tested_activation_blocked",
        blockers=blockers,
        evidence={
            "dataset_instruments": instruments,
            "dataset_years": years,
            "rs003_candidate_passed": bool(row.get("rs003_candidate_passed")),
            "strategy_spec_binding": bool(row.get("strategy_spec_binding")),
            "activation_run_archived": bool(row.get("activation_run_archived")),
        },
        next_truthful_action=(
            "Promote Qlib only after RS-003, >=50 instruments, >=2 years data, "
            "StrategySpec binding, and activation-run archive all pass."
        ),
    )


def evaluate_trl(evidence: dict[str, Any]) -> GateResult:
    repo_ready = all_paths_exist(
        [
            "services/learning/trl/adapter/trl_adapter.py",
            "services/learning/trl/smoke_test.py",
            "services/learning/trl/requirements.txt",
            "integrations/trl/activation_packet.md",
        ]
    )
    row = evidence.get("trl") or {}
    feedback_events = int(row.get("feedback_events") or 0)
    preference_pairs = int(row.get("preference_pairs") or 0)
    action_types = set(row.get("action_types") or [])
    strategy_families = int(row.get("strategy_families") or 0)
    blockers: list[str] = []
    if feedback_events < 200:
        blockers.append("fewer than 200 governed FB-002 events")
    if preference_pairs < 100:
        blockers.append("fewer than 100 valid preference pairs")
    if strategy_families < 2:
        blockers.append("fewer than 2 strategy families")
    if not {"approve", "edit", "reject"}.issubset(action_types):
        blockers.append("FB-002 evidence does not cover approve/edit/reject")
    if not row.get("imitation_approved_artifact"):
        blockers.append("approved LP-002 imitation artifact missing")
    if not row.get("baseline_model_metrics_pass"):
        blockers.append("baseline preference model metrics missing or below threshold")
    if not row.get("downstream_consumer_ready"):
        blockers.append("downstream consumer readiness missing")
    if not row.get("activation_run_archived"):
        blockers.append("first governed production DPO run not archived")
    activated = repo_ready and not blockers
    return GateResult(
        row="TRL",
        production_activated=activated,
        repo_baseline_ready=repo_ready,
        status="production_activated" if activated else "smoke_tested_runtime_data_blocked",
        blockers=blockers,
        evidence={
            "feedback_events": feedback_events,
            "preference_pairs": preference_pairs,
            "strategy_families": strategy_families,
            "action_types": sorted(action_types),
            "imitation_approved_artifact": bool(row.get("imitation_approved_artifact")),
            "baseline_model_metrics_pass": bool(row.get("baseline_model_metrics_pass")),
            "downstream_consumer_ready": bool(row.get("downstream_consumer_ready")),
            "activation_run_archived": bool(row.get("activation_run_archived")),
        },
        next_truthful_action=(
            "Promote TRL only after FB-002 volume, pair volume, imitation, baseline "
            "metrics, downstream consumer, and production DPO archive all pass."
        ),
    )


def evaluate_rl_stack(evidence: dict[str, Any], as_of: date) -> GateResult:
    repo_ready = all_paths_exist(
        [
            "services/research/finrl/adapter/finrl_adapter.py",
            "services/research/finrl/smoke_test.py",
            "services/research/rllib/adapter/rllib_adapter.py",
            "services/research/rllib/adapter/ray_tune_adapter.py",
            "services/learning/rl/RL_PATH_APPROVAL_GATE.md",
        ]
    )
    row = evidence.get("rl") or {}
    approved_at_raw = row.get("qlib_approved_at")
    stable_days = int(row.get("qlib_stable_days") or 0)
    if approved_at_raw:
        try:
            stable_days = max(stable_days, (as_of - parse_date(str(approved_at_raw))).days)
        except ValueError:
            stable_days = stable_days
    blockers: list[str] = []
    if not row.get("qlib_artifact_approved"):
        blockers.append("Qlib approved artifact missing")
    if stable_days < 90:
        blockers.append("Qlib stable evaluation history is below 90 days")
    if not row.get("sequential_decision_justification"):
        blockers.append("sequential-decision justification missing")
    if not row.get("intraday_dataset_ready"):
        blockers.append("2+ years intraday OHLCV/order-fill dataset missing")
    if not row.get("rl_gate_approved"):
        blockers.append("RL path approval gate remains closed")
    if not row.get("finrl_first_lane_archived"):
        blockers.append("FinRL first-lane governed proof missing")
    activated = repo_ready and not blockers
    return GateResult(
        row="RL stack",
        production_activated=activated,
        repo_baseline_ready=repo_ready,
        status="production_activated" if activated else "deferred_prep_gate_closed",
        blockers=blockers,
        evidence={
            "qlib_artifact_approved": bool(row.get("qlib_artifact_approved")),
            "qlib_stable_days": stable_days,
            "sequential_decision_justification": bool(row.get("sequential_decision_justification")),
            "intraday_dataset_ready": bool(row.get("intraday_dataset_ready")),
            "rl_gate_approved": bool(row.get("rl_gate_approved")),
            "finrl_first_lane_archived": bool(row.get("finrl_first_lane_archived")),
        },
        next_truthful_action=(
            "Keep RL deferred until Qlib is approved and stable for at least 90 days, "
            "then reopen the RL gate with FinRL first."
        ),
    )


def evaluate_wandb(evidence: dict[str, Any], as_of: date) -> GateResult:
    repo_ready = all_paths_exist(
        [
            "services/registry/experiments/adapter.py",
            "services/registry/experiments/config.py",
            "services/registry/experiments/smoke_test.py",
            "services/registry/experiments/WANDB_ACTIVATION.md",
        ]
    )
    row = evidence.get("wandb") or {}
    mlflow_history_days = max(0, (as_of - MLFLOW_GOVERNED_SINCE).days)
    blockers: list[str] = []
    if as_of < WANDB_EARLIEST_REOPEN or mlflow_history_days < 30:
        blockers.append(
            f"MLflow 30-day history not met; earliest eligible reopen is {WANDB_EARLIEST_REOPEN.isoformat()}"
        )
    if not row.get("operator_preference_on_file"):
        blockers.append("operator preference for W&B over MLflow missing")
    if not row.get("adapter_generalization_review_done"):
        blockers.append("adapter generalization review not closed")
    if not row.get("canonical_state_migration_done"):
        blockers.append("canonical artifact_state/deployment_stage migration not closed")
    if not row.get("wandb_sdk_pin_verified"):
        blockers.append("W&B SDK pin and compatibility proof missing")
    if not row.get("network_readiness_verified"):
        blockers.append("W&B network or self-hosted infrastructure readiness missing")
    if not row.get("activation_run_archived"):
        blockers.append("real W&B activation smoke not archived")
    activated = repo_ready and not blockers
    return GateResult(
        row="W&B",
        production_activated=activated,
        repo_baseline_ready=repo_ready,
        status="production_activated" if activated else "deferred_reentry_blocked",
        blockers=blockers,
        evidence={
            "as_of": as_of.isoformat(),
            "mlflow_governed_since": MLFLOW_GOVERNED_SINCE.isoformat(),
            "mlflow_history_days": mlflow_history_days,
            "earliest_reopen": WANDB_EARLIEST_REOPEN.isoformat(),
            "operator_preference_on_file": bool(row.get("operator_preference_on_file")),
            "adapter_generalization_review_done": bool(row.get("adapter_generalization_review_done")),
            "canonical_state_migration_done": bool(row.get("canonical_state_migration_done")),
            "wandb_sdk_pin_verified": bool(row.get("wandb_sdk_pin_verified")),
            "network_readiness_verified": bool(row.get("network_readiness_verified")),
            "activation_run_archived": bool(row.get("activation_run_archived")),
        },
        next_truthful_action=(
            "Prepare a W&B reopen packet only after 2026-05-15 and after all "
            "operator, state-migration, SDK, network, and smoke evidence exists."
        ),
    )


def build_report(evidence: dict[str, Any], as_of: date) -> dict[str, Any]:
    rows = [
        evaluate_qlib(evidence),
        evaluate_trl(evidence),
        evaluate_rl_stack(evidence, as_of),
        evaluate_wandb(evidence, as_of),
    ]
    production_activated = [row.row for row in rows if row.production_activated]
    blocked = [row.row for row in rows if not row.production_activated]
    return {
        "task_id": "RESEARCH-OSS-ACTIVATION-GATE-REPORT",
        "generated_at": iso_now(),
        "as_of": as_of.isoformat(),
        "status": "all_production_activated" if not blocked else "activation_gates_blocked",
        "production_activated_rows": production_activated,
        "blocked_rows": blocked,
        "rows": [row.to_dict() for row in rows],
    }


def command_report(args: argparse.Namespace) -> int:
    evidence = load_json(Path(args.evidence_json) if args.evidence_json else None)
    as_of = parse_date(args.as_of) if args.as_of else date.today()
    report = build_report(evidence, as_of)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    dump_json(output_dir / "research-oss-activation-gate-report.json", report)
    dump_json(
        output_dir / "summary.json",
        {
            "task_id": report["task_id"],
            "generated_at": report["generated_at"],
            "as_of": report["as_of"],
            "status": report["status"],
            "production_activated_rows": report["production_activated_rows"],
            "blocked_rows": report["blocked_rows"],
        },
    )
    print(json.dumps({"status": report["status"], "output_dir": str(output_dir)}, ensure_ascii=False))
    return 0 if report["status"] == "all_production_activated" else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate research/OSS activation gates.")
    parser.add_argument("--evidence-json", default=None)
    parser.add_argument("--as-of", default=None, help="YYYY-MM-DD; defaults to today's UTC-local date")
    parser.add_argument("--output-dir", required=True)
    parser.set_defaults(func=command_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
