"""TRL activation-ready worker entry point for the governed learning container."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adapter import (
    ActivationReadyGate,
    StubDPOBackend,
    TRLDPOBackend,
    TrainingConfig,
    persist_trl_run_artifacts,
    run_trl_dpo_workflow,
)


def main() -> int:
    try:
        ActivationReadyGate.require_env()
    except EnvironmentError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    dataset_path = os.environ.get("TRL_PREFERENCE_EVENTS_PATH")
    if not dataset_path:
        print(
            "TRL_PREFERENCE_EVENTS_PATH not set; using sample preference pairs for gated smoke mode",
            file=sys.stderr,
        )
        dataset_path = str(SERVICE_DIR / "examples" / "preference_pair_sample.json")

    payload = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    events = _events_from_payload(payload)
    if not isinstance(events, list):
        print("TRL preference event payload must be a list or an object with an events list", file=sys.stderr)
        return 3

    backend_name = os.environ.get("TRL_BACKEND", "stub").strip().lower()
    backend = TRLDPOBackend() if backend_name == "real" else StubDPOBackend()
    output_dir = Path(os.environ.get("TRL_OUTPUT_DIR", "/tmp/pantheon/learning/trl/activation-ready"))

    result = run_trl_dpo_workflow(
        events,
        dataset_id=os.environ.get("TRL_DATASET_ID", "trl-worker-dataset"),
        strategy_id=os.environ.get("TRL_STRATEGY_ID", "trl-worker-strategy"),
        source_dataset_refs=[
            os.environ.get("TRL_SOURCE_DATASET_REF", f"file://{Path(dataset_path).resolve()}")
        ],
        backend=backend,
        config=TrainingConfig(
            version=os.environ.get("TRL_ARTIFACT_VERSION", "1.0.0"),
            requested_by=os.environ.get("TRL_REQUESTED_BY", "worker"),
        ),
        enforce_activation_ready=os.environ.get("TRL_ENFORCE_DATA_FLOORS", "true").lower()
        in {"1", "true", "yes", "on"},
    )
    manifest = persist_trl_run_artifacts(result, output_dir)
    output = {
        "registry_id": result.registry_entry["registry_id"],
        "artifact_state": result.registry_entry["artifact_state"],
        "deployment_stage": result.registry_entry["deployment_summary"]["current_stage"],
        "candidate_next_state": result.candidate_packet["requested_artifact_state"],
        "storage_path": result.registry_entry["storage_ref"]["path"],
        "checksum": result.registry_entry["checksum"],
        "backend": result.training_result.backend,
        "metrics": result.training_result.metrics,
        "artifact_manifest": manifest,
    }
    print(json.dumps(output, indent=2))
    return 0


def _events_from_payload(payload: object) -> object:
    if isinstance(payload, dict) and "events" in payload:
        return payload["events"]
    if isinstance(payload, dict) and "preference_pairs" in payload:
        return [_event_from_legacy_pair(pair, index=i) for i, pair in enumerate(payload["preference_pairs"], start=1)]
    return payload


def _event_from_legacy_pair(pair: object, *, index: int) -> dict:
    if not isinstance(pair, dict):
        raise ValueError("preference_pairs entries must be objects")
    context = pair.get("context") if isinstance(pair.get("context"), dict) else {}
    action = str(context.get("feedback_action") or "approve")
    chosen = pair.get("artifact_a")
    rejected = pair.get("artifact_b")
    artifact = rejected if action == "reject" else chosen
    artifact_edited = chosen if action == "edit" else None
    if not isinstance(artifact, dict):
        artifact = {"artifact_id": f"legacy-null-{index}"}
    event = {
        "feedback_event_id": str(pair.get("source_event_id") or f"legacy-pair-{index}"),
        "actor_role": "operator",
        "promotion_state": str(context.get("promotion_state") or artifact.get("promotion_state") or "candidate"),
        "action": action,
        "strategy_family": str(artifact.get("strategy_id") or "legacy-preference-pair"),
        "operator_id": str(context.get("operator_id") or "unknown"),
        "artifact": _artifact_with_id(artifact, index=index, suffix="original"),
    }
    if action == "edit" and isinstance(artifact_edited, dict):
        event["artifact_edited"] = _artifact_with_id(artifact_edited, index=index, suffix="edited")
    return event


def _artifact_with_id(artifact: dict, *, index: int, suffix: str) -> dict:
    result = dict(artifact)
    result.setdefault(
        "artifact_id",
        result.get("registry_id")
        or result.get("artifact_version")
        or f"legacy-pair-{index}-{suffix}",
    )
    return result


if __name__ == "__main__":
    raise SystemExit(main())
