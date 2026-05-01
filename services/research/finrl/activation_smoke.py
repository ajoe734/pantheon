"""Activation-ready FinRL evidence harness.

Governance boundary:
- Input: synthetic bounded OHLCV dataset (3 instruments, 30 periods)
- Output: research evidence with evaluator packet, registry entry, candidate packet
- This is offline-only. It does not authorize paper/live/canary execution.
- Real FinRL backend is attempted; failure is recorded as explicit evidence.
- No broker session, order routing, capital binding, or deployment promotion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.research.finrl.adapter import (
    DeferredPrepGate,
    FinRLDeferredPrepError,
    FinRLPPOBackend,
    GovernedFinRLPolicyAdapter,
    PolicyTrainingConfig,
    StubFinRLBackend,
    run_finrl_workflow,
)

TASK_ID = "P2-RL-UPSTREAM-RUNTIME-SMOKE-001"
MIN_ACTIVATION_STEPS = 20
INSTRUMENTS = ("EQ_AAA", "EQ_BBB", "EQ_CCC")
NUM_PERIODS = 30


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FinRL activation-ready evidence smoke")
    parser.add_argument("--enable-activation-ready", action="store_true")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/tmp/pantheon/research/finrl/activation"),
    )
    parser.add_argument("--backend", choices=["real", "stub"], default="real")
    parser.add_argument("--dataset-id", default="finrl-activation-smoke-20260501")
    parser.add_argument("--strategy-id", default="rl-exit-timing-activation")
    parser.add_argument("--artifact-version", default="2026.05.01")
    parser.add_argument("--requested-by", default="Claude")
    parser.add_argument(
        "--no-stub-handoff-on-real-failure",
        action="store_true",
        help="Record real backend failure without producing a stub-backed handoff artifact.",
    )
    args = parser.parse_args(argv)

    try:
        DeferredPrepGate.require_cli_flag(
            args.enable_activation_ready, flag_name="--enable-activation-ready"
        )
    except EnvironmentError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    dataset_payload = _build_synthetic_dataset(
        dataset_id=args.dataset_id,
        strategy_id=args.strategy_id,
    )
    source_ref = f"synthetic-ohlcv://bounded-fixture/{NUM_PERIODS}-periods"

    config = PolicyTrainingConfig(
        version=args.artifact_version,
        requested_by=args.requested_by,
    )

    real_attempt: dict[str, Any] | None = None
    result = None

    if args.backend == "real":
        try:
            result = run_finrl_workflow(
                dataset_payload,
                backend=FinRLPPOBackend(),
                config=config,
            )
            real_attempt = {
                "status": "ok",
                "backend": result.training_result.backend,
                "run_id": result.training_result.run_id,
                "metrics": result.training_result.metrics,
            }
        except Exception as exc:  # noqa: BLE001 - evidence must preserve explicit failure
            real_attempt = _backend_failure_evidence(exc, "finrl_ppo")
            if not args.no_stub_handoff_on_real_failure:
                result = run_finrl_workflow(
                    dataset_payload,
                    backend=StubFinRLBackend(),
                    config=config,
                )
    else:
        result = run_finrl_workflow(
            dataset_payload,
            backend=StubFinRLBackend(),
            config=config,
        )
        real_attempt = {"status": "not_attempted", "reason": "--backend stub was requested"}

    dataset_evidence = _build_dataset_evidence(dataset_payload, source_ref)
    evaluator_packet = _build_evaluator_packet(result, args.strategy_id, args.artifact_version)

    manifest = _persist_artifacts(result, evaluator_packet, output_dir) if result is not None else None
    evidence = {
        "task_id": TASK_ID,
        "framework": "finrl",
        "created_at": _utc_now(),
        "source_ref": source_ref,
        "dataset_evidence": dataset_evidence,
        "real_backend_attempt": real_attempt,
        "handoff_artifact_manifest": manifest,
        "handoff_backend": result.training_result.backend if result is not None else None,
        "governance_boundary": {
            "direct_governance_write": False,
            "order_routing_enabled": False,
            "broker_session_enabled": False,
            "deployment_stage": (
                result.registry_entry["deployment_summary"]["current_stage"]
                if result is not None
                else "none"
            ),
            "paper_canary_live_promotion": False,
            "capital_binding": False,
            "raw_secrets_persisted": False,
            "gate_state": "closed",
        },
    }

    _write_json(output_dir / "dataset_evidence.json", dataset_evidence)
    _write_json(output_dir / "real_backend_attempt.json", real_attempt)
    _write_json(output_dir / "evaluator_packet.json", evaluator_packet)
    _write_json(output_dir / "activation_evidence_summary.json", evidence)
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0


def _build_synthetic_dataset(*, dataset_id: str, strategy_id: str) -> dict[str, Any]:
    base_prices = {"EQ_AAA": 100.0, "EQ_BBB": 50.0, "EQ_CCC": 75.0}
    base_volumes = {"EQ_AAA": 1_000_000, "EQ_BBB": 600_000, "EQ_CCC": 800_000}
    records: list[dict[str, Any]] = []
    for period in range(NUM_PERIODS):
        date = f"2026-01-{period + 1:02d}"
        for instrument in INSTRUMENTS:
            seed_val = (period * 7 + hash(instrument)) % 100
            drift = 0.002 * (seed_val - 50) / 50.0
            prev_close = base_prices[instrument] * (1.0 + drift * period)
            open_price = round(prev_close * (1.0 + 0.001 * ((seed_val % 10) - 5)), 4)
            high_price = round(open_price * (1.0 + 0.005 * ((seed_val % 7) + 1) / 7.0), 4)
            low_price = round(open_price * (1.0 - 0.004 * ((seed_val % 5) + 1) / 5.0), 4)
            close_price = round((open_price + high_price + low_price) / 3.0, 4)
            volume = int(base_volumes[instrument] * (1.0 + 0.05 * ((seed_val % 11) - 5) / 5.0))
            records.append(
                {
                    "instrument": instrument,
                    "date": date,
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price,
                    "volume": volume,
                }
            )
    return {
        "dataset_id": dataset_id,
        "strategy_id": strategy_id,
        "source_dataset_refs": [f"synthetic-ohlcv://bounded-fixture/{NUM_PERIODS}-periods"],
        "source_strategy_spec_id": "strategy-spec:rl-exit-timing-v1",
        "decision_focus": "exit_timing",
        "data_frequency": "daily",
        "position_ratio": 0.35,
        "cash_ratio": 0.65,
        "records": records,
    }


def _build_dataset_evidence(dataset: dict[str, Any], source_ref: str) -> dict[str, Any]:
    records = dataset.get("records", [])
    instruments = sorted({r["instrument"] for r in records})
    periods_per_instrument = len(records) // max(len(instruments), 1)
    return {
        "source_ref": source_ref,
        "num_instruments": len(instruments),
        "instruments": instruments,
        "periods_per_instrument": periods_per_instrument,
        "total_records": len(records),
        "decision_focus": dataset.get("decision_focus"),
        "data_frequency": dataset.get("data_frequency"),
        "ohlcv_fields_present": all(
            field in records[0] for field in ("open", "high", "low", "close", "volume")
        ) if records else False,
        "activation_floor_check": {
            "min_steps_required": MIN_ACTIVATION_STEPS,
            "estimated_steps": max(periods_per_instrument - 3, 0),
            "passes": max(periods_per_instrument - 3, 0) >= MIN_ACTIVATION_STEPS,
        },
    }


def _build_evaluator_packet(
    result: Any | None, strategy_id: str, version: str
) -> dict[str, Any]:
    packet_id = f"EV-001-finrl-{strategy_id}-{version}"
    if result is None:
        return {
            "packet_id": packet_id,
            "evaluator": "EV-001",
            "task_id": TASK_ID,
            "framework": "finrl",
            "artifact_type": "model_artifact",
            "model_family": "rl_policy",
            "strategy_id": strategy_id,
            "version": version,
            "scoring": {"status": "no_artifact_produced", "advisory_note": "Real backend failed and no stub fallback."},
            "governance": {"deployment_stage": "none", "gate_state": "closed"},
            "created_at": _utc_now(),
        }
    metrics = result.training_result.metrics
    checksum = result.registry_entry.get("checksum", "")
    return {
        "packet_id": packet_id,
        "evaluator": "EV-001",
        "task_id": TASK_ID,
        "framework": "finrl",
        "artifact_type": "model_artifact",
        "model_family": "rl_policy",
        "strategy_id": strategy_id,
        "version": version,
        "backend_used": result.training_result.backend,
        "run_id": result.training_result.run_id,
        "scoring": {
            "components": [
                {
                    "name": "mean_reward_proxy",
                    "value": metrics.get("mean_reward_proxy"),
                    "weight": 0.4,
                },
                {
                    "name": "reward_proxy_stddev",
                    "value": metrics.get("reward_proxy_stddev"),
                    "weight": 0.2,
                    "note": "lower is better",
                },
                {
                    "name": "max_action_prior",
                    "value": metrics.get("max_action_prior"),
                    "weight": 0.2,
                },
                {
                    "name": "num_steps",
                    "value": metrics.get("num_steps"),
                    "weight": 0.2,
                    "note": "data coverage",
                },
            ],
            "advisory_note": (
                "Stub-baseline RL policy evidence. Real FinRL backend requires install. "
                "Gate remains closed; offline review only."
            ),
        },
        "governance": {
            "deployment_stage": result.registry_entry["deployment_summary"]["current_stage"],
            "gate_state": result.candidate_packet.get("gate_state", "closed"),
            "order_routing_enabled": False,
            "direct_governance_write": False,
            "paper_canary_live_promotion": False,
            "capital_binding": False,
        },
        "artifact_checksum": checksum,
        "auditable_fields": ["backend_used", "run_id", "artifact_checksum", "governance"],
        "created_at": _utc_now(),
    }


def _persist_artifacts(result: Any, evaluator_packet: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    artifacts: dict[str, str] = {}
    _write_json(output_dir / "artifact_bundle.json", result.artifact_bundle)
    _write_json(output_dir / "registry_entry.json", result.registry_entry)
    _write_json(output_dir / "candidate_packet.json", result.candidate_packet)
    artifacts["artifact_bundle"] = str(output_dir / "artifact_bundle.json")
    artifacts["registry_entry"] = str(output_dir / "registry_entry.json")
    artifacts["candidate_packet"] = str(output_dir / "candidate_packet.json")
    artifacts["evaluator_packet"] = str(output_dir / "evaluator_packet.json")
    checksums = {
        name: f"sha256:{_sha256_file(path)}" for name, path in artifacts.items()
        if Path(path).exists()
    }
    manifest = {
        "run_id": result.training_result.run_id,
        "backend": result.training_result.backend,
        "output_dir": str(output_dir),
        "artifacts": artifacts,
        "checksums": checksums,
        "created_at": _utc_now(),
    }
    _write_json(output_dir / "manifest.json", manifest)
    artifacts["manifest"] = str(output_dir / "manifest.json")
    return manifest


def _backend_failure_evidence(exc: Exception, backend: str) -> dict[str, Any]:
    cause = exc.__cause__
    return {
        "status": "dependency_or_config_error",
        "backend": backend,
        "error_type": type(exc).__name__,
        "message": str(exc),
        "cause_type": type(cause).__name__ if cause is not None else None,
        "cause_message": str(cause) if cause is not None else None,
        "traceback_tail": traceback.format_exception_only(type(exc), exc),
        "silent_stub_fallback": False,
    }


def _sha256_file(path: str) -> str:
    try:
        data = Path(path).read_bytes()
        return hashlib.sha256(data).hexdigest()
    except Exception:
        return "unreadable"


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(_jsonable(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "value"):
        return value.value
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
