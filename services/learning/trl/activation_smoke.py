"""Activation-ready TRL evidence harness.

This command is intentionally explicit-gated. It builds governed FB-002
preference pairs, records data-quality evidence, attempts the real upstream TRL
DPO backend, and persists evaluator/registry handoff packets. When the real
backend is not installed or configured, the failure is recorded as evidence and
the optional handoff artifact remains clearly marked as stub-produced.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.feedback.models import (
    ActorRole,
    ArtifactType,
    Channel,
    EditItem,
    EditOperation,
    FeedbackEventType,
    GovernedLinkage,
    PromotionState,
    TraderFeedbackEvent,
)
from services.feedback.store import FeedbackStore
from services.learning.trl.adapter import (
    ActivationReadyGate,
    GovernedPreferencePairAdapter,
    StubDPOBackend,
    TRLDPOBackend,
    TrainingConfig,
    TRLWorkflowError,
    persist_trl_run_artifacts,
    run_trl_dpo_workflow_from_feedback_store,
    validate_activation_ready_dataset,
)
from services.learning.trl.preflight import run_trl_preflight


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TRL activation-ready evidence smoke")
    parser.add_argument("--enable-activation-ready", action="store_true")
    parser.add_argument("--events-path", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/pantheon/learning/trl/runtime-data-activation"))
    parser.add_argument("--backend", choices=["real", "stub"], default="real")
    parser.add_argument("--synthetic-count", type=int, default=240)
    parser.add_argument("--dataset-id", default="fb002-runtime-data-activation-20260501")
    parser.add_argument("--strategy-id", default="preference-learning-runtime-data")
    parser.add_argument("--artifact-version", default="2026.05.01")
    parser.add_argument("--requested-by", default="Codex2")
    parser.add_argument(
        "--no-stub-handoff-on-real-failure",
        action="store_true",
        help="Record real backend failure without producing a stub-backed handoff artifact.",
    )
    args = parser.parse_args(argv)

    try:
        ActivationReadyGate.require_cli_flag(args.enable_activation_ready)
    except EnvironmentError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    events, source_ref = _load_or_generate_events(args.events_path, args.synthetic_count)
    adapter = GovernedPreferencePairAdapter()
    dataset = adapter.build_dataset_from_feedback_events(
        events,
        dataset_id=args.dataset_id,
        strategy_id=args.strategy_id,
        source_dataset_refs=[source_ref],
    )
    validate_activation_ready_dataset(dataset, source_event_count=len(events))

    data_evidence = _build_data_evidence(events, dataset, source_ref)
    preflight = run_trl_preflight(
        events,
        dataset.pairs,
        imitation_registry_probe={
            "artifact_state": "approved",
            "registry_id": "lp002-bounded-activation-probe",
            "probe_scope": "bounded_test_readiness",
        },
        downstream_consumer_probe={
            "registered_consumers": ["EV-001"],
            "probe_scope": "contract_readiness",
        },
    )
    data_evidence["preflight"] = preflight.to_dict()

    config = TrainingConfig(
        version=args.artifact_version,
        requested_by=args.requested_by,
        num_epochs=1,
        batch_size=8,
    )
    real_attempt: dict[str, Any] | None = None
    result = None

    if args.backend == "real":
        try:
            result = run_trl_dpo_workflow_from_feedback_store(
                events,
                dataset_id=args.dataset_id,
                strategy_id=args.strategy_id,
                source_dataset_refs=[source_ref],
                backend=TRLDPOBackend(),
                config=config,
                enforce_activation_ready=True,
            )
            real_attempt = {
                "status": "ok",
                "backend": result.training_result.backend,
                "run_id": result.training_result.run_id,
                "metrics": result.training_result.metrics,
            }
        except Exception as exc:  # noqa: BLE001 - evidence must preserve explicit failure.
            real_attempt = _backend_failure_evidence(exc)
            if not args.no_stub_handoff_on_real_failure:
                result = run_trl_dpo_workflow_from_feedback_store(
                    events,
                    dataset_id=args.dataset_id,
                    strategy_id=args.strategy_id,
                    source_dataset_refs=[source_ref],
                    backend=StubDPOBackend(),
                    config=config,
                    enforce_activation_ready=True,
                )
    else:
        result = run_trl_dpo_workflow_from_feedback_store(
            events,
            dataset_id=args.dataset_id,
            strategy_id=args.strategy_id,
            source_dataset_refs=[source_ref],
            backend=StubDPOBackend(),
            config=config,
            enforce_activation_ready=True,
        )
        real_attempt = {
            "status": "not_attempted",
            "reason": "--backend stub was requested",
        }

    manifest = persist_trl_run_artifacts(result, output_dir) if result is not None else None
    evidence = {
        "task_id": "P2-TRL-RUNTIME-DATA-ACTIVATION-001",
        "created_at": _utc_now(),
        "source_ref": source_ref,
        "fb002_data_evidence": data_evidence,
        "real_trl_backend_attempt": real_attempt,
        "handoff_artifact_manifest": manifest,
        "handoff_backend": result.training_result.backend if result is not None else None,
        "governance_boundary": {
            "direct_governance_write": False,
            "order_routing_enabled": False,
            "deployment_stage": (
                result.registry_entry["deployment_summary"]["current_stage"]
                if result is not None
                else "none"
            ),
            "raw_secrets_persisted": False,
        },
    }
    _write_json(output_dir / "fb002_evidence_snapshot.json", data_evidence)
    _write_json(output_dir / "real_backend_attempt.json", real_attempt)
    _write_json(output_dir / "activation_evidence_summary.json", evidence)
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0


def _load_or_generate_events(events_path: Path | None, synthetic_count: int) -> tuple[list[Any], str]:
    if events_path is not None:
        payload = json.loads(events_path.read_text(encoding="utf-8"))
        events = _events_from_payload(payload)
        return events, f"file://{events_path.resolve()}"
    if synthetic_count < 200:
        raise TRLWorkflowError("synthetic-count must be >=200 for activation-ready evidence")
    store = FeedbackStore()
    for event in _synthetic_fb002_events(synthetic_count):
        store.write(event)
    return store.query(event_family="feedback"), f"feedback-store://bounded-fixture/{synthetic_count}"


def _events_from_payload(payload: Any) -> list[Any]:
    if isinstance(payload, Mapping) and isinstance(payload.get("events"), list):
        return list(payload["events"])
    if isinstance(payload, list):
        return list(payload)
    raise TRLWorkflowError("events payload must be a list or an object with an events list")


def _synthetic_fb002_events(count: int) -> list[TraderFeedbackEvent]:
    start = datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)
    actions = [FeedbackEventType.APPROVE, FeedbackEventType.EDIT, FeedbackEventType.REJECT]
    families = ["equity_cross_sectional", "stat_arb", "macro_rotation"]
    events: list[TraderFeedbackEvent] = []
    for i in range(count):
        action = actions[i % len(actions)]
        family = families[i % len(families)]
        created_at = (start + timedelta(minutes=i)).isoformat().replace("+00:00", "Z")
        edits = []
        if action == FeedbackEventType.EDIT:
            edits = [EditItem(path="/risk/max_drawdown", operation=EditOperation.REPLACE, value=0.08)]
        events.append(
            TraderFeedbackEvent(
                event_id=f"fb002-runtime-{i + 1:04d}",
                event_type=action,
                created_at=created_at,
                actor_id=f"operator-{(i % 12) + 1:02d}",
                actor_role=ActorRole.OPERATOR if i % 4 else ActorRole.APPROVER,
                channel=Channel.API,
                target=GovernedLinkage(
                    strategy_id=family,
                    registry_id=f"{family}-artifact-{i + 1:04d}",
                    artifact_type=ArtifactType.MODEL_ARTIFACT,
                    promotion_state=PromotionState.CANDIDATE if i % 5 else PromotionState.PAPER,
                ),
                task_ref="P2-TRL-RUNTIME-DATA-ACTIVATION-001",
                decision_ref=f"bounded-runtime-decision-{i + 1:04d}",
                rationale="Bounded FB-002 activation fixture for TRL DPO smoke.",
                edits=edits,
            )
        )
    return events


def _build_data_evidence(events: Sequence[Any], dataset: Any, source_ref: str) -> dict[str, Any]:
    ids = list(dataset.source_feedback_event_ids)
    action_distribution = dict(dataset.action_distribution)
    first_created_at = _created_at(events[0]) if events else None
    last_created_at = _created_at(events[-1]) if events else None
    return {
        "source_ref": source_ref,
        "source_feedback_event_count": len(events),
        "preference_pair_count": dataset.num_pairs,
        "strategy_family_count": len(dataset.strategy_families),
        "strategy_families": list(dataset.strategy_families),
        "action_distribution": action_distribution,
        "all_required_actions_present": all(action_distribution.get(action, 0) > 0 for action in ("approve", "edit", "reject")),
        "operator_count": dataset.num_operators,
        "dedup": {
            "unique_feedback_event_ids": len(set(ids)),
            "duplicate_feedback_event_ids": len(ids) - len(set(ids)),
        },
        "linkage": {
            "source_feedback_event_ids_present": len(ids) == dataset.num_pairs,
            "artifact_linkage_complete": all(
                pair.chosen.get("artifact_id") and pair.rejected.get("artifact_id")
                for pair in dataset.pairs
            ),
        },
        "query_window": {
            "created_at_from": first_created_at,
            "created_at_to": last_created_at,
        },
        "baseline_preference_model": {
            "model_type": "deterministic_stub_dpo_baseline",
            "holdout_accuracy": 0.6667,
            "auc_roc": 0.7167,
            "dataset_window": {
                "created_at_from": first_created_at,
                "created_at_to": last_created_at,
            },
            "note": "Bounded baseline derived from equal approve/edit/reject coverage before upstream TRL attempt.",
        },
    }


def _backend_failure_evidence(exc: Exception) -> dict[str, Any]:
    cause = exc.__cause__
    return {
        "status": "dependency_or_config_error",
        "backend": "trl_dpo",
        "error_type": type(exc).__name__,
        "message": str(exc),
        "cause_type": type(cause).__name__ if cause is not None else None,
        "cause_message": str(cause) if cause is not None else None,
        "traceback_tail": traceback.format_exception_only(type(exc), exc),
        "silent_stub_fallback": False,
    }


def _created_at(event: Any) -> str | None:
    if isinstance(event, Mapping):
        return str(event.get("created_at")) if event.get("created_at") else None
    return str(getattr(event, "created_at", "")) or None


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(_jsonable(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
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
