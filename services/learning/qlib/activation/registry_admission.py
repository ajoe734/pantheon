"""Build a review-only Qlib registry admission packet.

The packet ties together the dataset manifest, StrategySpec packet, and Qlib
activation artifacts. It never writes registry truth, deploys a model, opens a
broker session, or authorizes order routing.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft7Validator


TASK_ID = "MGMT-QLIB-005"
SCHEMA_VERSION = "1.0"
ADMISSION_VERSION = "1.0.0"
ACTIVATION_CRITERIA_REF = "services/learning/qlib/ACTIVATION_CRITERIA.md"
QLIB_ACTIVATION_PACKET_REF = "integrations/qlib/activation_packet.md"
REGISTRY_SCHEMA_REF = "services/registry/registry_entry_schema.json"
REQUIRED_ACTIVATION_FILES = {
    "artifact_bundle": "artifact_bundle.json",
    "registry_entry": "registry_entry.json",
    "candidate_packet": "candidate_packet.json",
    "artifact_refs": "artifact_refs.json",
    "manifest": "manifest.json",
    "production_activation_packet": "production_activation_packet.json",
}

REPO_ROOT = Path(__file__).resolve().parents[4]


class RegistryAdmissionError(ValueError):
    """Raised when a Qlib registry admission packet would be unsafe or incomplete."""


def load_activation_artifacts(activation_dir: str | Path) -> dict[str, Any]:
    """Load persisted Qlib activation artifacts from a production smoke output dir."""
    base = Path(activation_dir)
    artifacts: dict[str, Any] = {}
    missing: list[str] = []
    for key, filename in REQUIRED_ACTIVATION_FILES.items():
        path = base / filename
        if not path.exists():
            missing.append(str(path))
            continue
        artifacts[key] = json.loads(path.read_text(encoding="utf-8"))
    if missing:
        raise RegistryAdmissionError(
            "Qlib admission activation artifacts missing: " + ", ".join(missing)
        )
    return artifacts


def build_registry_admission_packet(
    dataset_manifest: Mapping[str, Any],
    strategy_spec_packet: Mapping[str, Any],
    activation_artifacts: Mapping[str, Any],
    *,
    task_id: str = TASK_ID,
    created_at: str | None = None,
    created_by: str = "Codex",
    version: str = ADMISSION_VERSION,
    dataset_manifest_ref: str,
    strategy_spec_packet_ref: str,
    activation_artifact_ref: str,
) -> dict[str, Any]:
    """Build and validate a non-writing registry admission packet."""
    timestamp = created_at or _utc_now()
    dataset_ref = _dataset_ref(dataset_manifest)
    strategy_spec_id = _required_text(dataset_manifest, "source_strategy_spec_id")
    strategy_id = _required_text(dataset_manifest, "strategy_id")

    registry_entry = copy.deepcopy(_mapping(activation_artifacts.get("registry_entry")))
    candidate_packet = copy.deepcopy(_mapping(activation_artifacts.get("candidate_packet")))
    artifact_refs = copy.deepcopy(_mapping(activation_artifacts.get("artifact_refs")))
    artifact_manifest = copy.deepcopy(_mapping(activation_artifacts.get("manifest")))
    artifact_bundle = copy.deepcopy(_mapping(activation_artifacts.get("artifact_bundle")))
    production_packet = copy.deepcopy(
        _mapping(activation_artifacts.get("production_activation_packet"))
    )

    _validate_inputs(
        dataset_manifest=dataset_manifest,
        strategy_spec_packet=strategy_spec_packet,
        registry_entry=registry_entry,
        candidate_packet=candidate_packet,
        artifact_refs=artifact_refs,
        artifact_manifest=artifact_manifest,
        artifact_bundle=artifact_bundle,
        production_packet=production_packet,
        dataset_ref=dataset_ref,
        strategy_spec_id=strategy_spec_id,
        strategy_id=strategy_id,
    )

    entry_criteria = _entry_criteria(
        dataset_manifest=dataset_manifest,
        strategy_spec_packet=strategy_spec_packet,
        artifact_bundle=artifact_bundle,
        production_packet=production_packet,
    )

    packet = {
        "schema_version": SCHEMA_VERSION,
        "packet_id": f"qlib-registry-admission:{registry_entry['registry_id']}:{version}",
        "task_id": task_id,
        "created_at": timestamp,
        "created_by": created_by,
        "admission_disposition": "ready_for_registry_candidate_review",
        "registry_request": {
            "request_type": "artifact_state_transition",
            "artifact_type": "model_artifact",
            "registry_id": registry_entry["registry_id"],
            "strategy_id": strategy_id,
            "version": registry_entry["version"],
            "current_artifact_state": "draft",
            "requested_artifact_state": "candidate",
            "requested_transition": "draft_to_candidate",
            "deployment_stage": "none",
            "approval_scope": "candidate_admission_review_only",
            "registry_write_authority": "registry_service_only",
            "registry_write_performed": False,
        },
        "source_refs": {
            "dataset_manifest_ref": dataset_manifest_ref,
            "strategy_spec_packet_ref": strategy_spec_packet_ref,
            "activation_artifact_ref": activation_artifact_ref,
            "activation_criteria_ref": ACTIVATION_CRITERIA_REF,
            "qlib_activation_packet_ref": QLIB_ACTIVATION_PACKET_REF,
        },
        "source_packet_ids": {
            "dataset_manifest_id": dataset_manifest.get("manifest_id"),
            "strategy_spec_packet_id": strategy_spec_packet.get("packet_id"),
            "production_activation_packet_id": production_packet.get("packet_id"),
        },
        "candidate_artifact": registry_entry,
        "candidate_handoff": production_packet["candidate_handoff"],
        "model_artifact_ref": artifact_refs["model_artifact_ref"],
        "evaluation_report_ref": artifact_refs["evaluation_report_ref"],
        "artifact_manifest": artifact_manifest,
        "activation_artifact_refs": artifact_refs["refs"],
        "production_dataset_proof": production_packet["production_dataset_proof"],
        "gate_evidence": {
            "rs003_candidate": copy.deepcopy(strategy_spec_packet["rs003_candidate"]),
            "governed_dataset": copy.deepcopy(dataset_manifest["governed_dataset"]),
            "strategy_spec_binding": copy.deepcopy(
                strategy_spec_packet["strategy_spec_binding"]
            ),
            "dataset_floor_summary": copy.deepcopy(
                production_packet["dataset_floor_summary"]
            ),
            "backend_smoke": copy.deepcopy(production_packet["backend_smoke"]),
            "entry_criteria_satisfied": entry_criteria,
        },
        "lineage": {
            "source_run_ids": _source_run_ids(
                task_id,
                _text(dataset_manifest.get("task_id")),
                _text(strategy_spec_packet.get("task_id")),
                _text(registry_entry.get("producer_run_id")),
            ),
            "source_dataset_refs": [dataset_ref],
            "source_strategy_spec_id": strategy_spec_id,
            "parent_registry_ids": [strategy_spec_id],
        },
        "downstream_scope": {
            "registry_admission_packet_only": True,
            "registry_write_authority": "registry_service_only",
            "registry_write_performed": False,
            "deployment_stage": "none",
            "training_performed_in_this_task": False,
            "broker_session_opened": False,
            "order_route": "none",
            "capital_binding": "none",
        },
        "safety_assertions": {
            "no_registry_write": True,
            "no_order_route": True,
            "no_broker_session": True,
            "no_capital_binding": True,
            "deployment_stage_remains_none": True,
            "artifact_state_request_limited_to_candidate": True,
            "scoring_only_not_direct_action": True,
        },
        "review_checklist": [
            "RS-003 StrategySpec candidate evidence attached",
            "Governed TWSE/TPEx dataset proof attached",
            "Qlib LightGBM model artifact ref attached",
            "Evaluation report ref attached",
            "Candidate handoff requests only draft_to_candidate",
            "Deployment stage remains none and order_route remains none",
        ],
    }
    packet["checksum"] = f"sha256:{_sha256_json(packet)}"
    validate_registry_admission_packet(packet)
    return packet


def validate_registry_admission_packet(packet: Mapping[str, Any]) -> list[str]:
    """Validate the admission packet's core safety and review semantics."""
    errors: list[str] = []
    request = _mapping(packet.get("registry_request"))
    candidate = _mapping(packet.get("candidate_artifact"))
    handoff = _mapping(packet.get("candidate_handoff"))
    model_ref = _mapping(packet.get("model_artifact_ref"))
    eval_ref = _mapping(packet.get("evaluation_report_ref"))
    scope = _mapping(packet.get("downstream_scope"))
    safety = _mapping(packet.get("safety_assertions"))
    gates = _mapping(packet.get("gate_evidence"))

    if packet.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version must be 1.0")
    if request.get("requested_artifact_state") != "candidate":
        errors.append("registry_request.requested_artifact_state must be candidate")
    if request.get("current_artifact_state") != "draft":
        errors.append("registry_request.current_artifact_state must be draft")
    if request.get("deployment_stage") != "none":
        errors.append("registry_request.deployment_stage must be none")
    if request.get("registry_write_performed") is not False:
        errors.append("registry_request.registry_write_performed must be false")
    if candidate.get("artifact_state") != "draft":
        errors.append("candidate_artifact.artifact_state must remain draft")
    if _deployment_stage(candidate) != "none":
        errors.append("candidate_artifact.deployment_summary.current_stage must be none")
    if handoff.get("requested_artifact_state") != "candidate":
        errors.append("candidate_handoff must request candidate")
    if handoff.get("deployment_stage") != "none":
        errors.append("candidate_handoff.deployment_stage must be none")
    if model_ref.get("artifact_type") != "model_artifact":
        errors.append("model_artifact_ref.artifact_type must be model_artifact")
    if eval_ref.get("artifact_type") != "evaluation_result":
        errors.append("evaluation_report_ref.artifact_type must be evaluation_result")
    if model_ref.get("registry_id") != candidate.get("registry_id"):
        errors.append("model_artifact_ref.registry_id must match candidate_artifact")
    if request.get("registry_id") != candidate.get("registry_id"):
        errors.append("registry_request.registry_id must match candidate_artifact")
    for key in (
        "no_registry_write",
        "no_order_route",
        "no_broker_session",
        "no_capital_binding",
        "deployment_stage_remains_none",
        "artifact_state_request_limited_to_candidate",
        "scoring_only_not_direct_action",
    ):
        if safety.get(key) is not True:
            errors.append(f"safety_assertions.{key} must be true")
    if scope.get("registry_write_performed") is not False:
        errors.append("downstream_scope.registry_write_performed must be false")
    if scope.get("order_route") != "none":
        errors.append("downstream_scope.order_route must be none")
    if scope.get("deployment_stage") != "none":
        errors.append("downstream_scope.deployment_stage must be none")
    criteria = _mapping(gates.get("entry_criteria_satisfied"))
    for key, value in criteria.items():
        if value is not True:
            errors.append(f"gate_evidence.entry_criteria_satisfied.{key} must be true")

    if errors:
        raise RegistryAdmissionError(
            "Qlib registry admission packet failed: " + "; ".join(errors)
        )
    return []


def _validate_inputs(
    *,
    dataset_manifest: Mapping[str, Any],
    strategy_spec_packet: Mapping[str, Any],
    registry_entry: Mapping[str, Any],
    candidate_packet: Mapping[str, Any],
    artifact_refs: Mapping[str, Any],
    artifact_manifest: Mapping[str, Any],
    artifact_bundle: Mapping[str, Any],
    production_packet: Mapping[str, Any],
    dataset_ref: str,
    strategy_spec_id: str,
    strategy_id: str,
) -> None:
    errors: list[str] = []
    errors.extend(_registry_entry_schema_errors(registry_entry))
    strategy_binding = _mapping(strategy_spec_packet.get("strategy_spec_binding"))
    rs003_candidate = _mapping(strategy_spec_packet.get("rs003_candidate"))
    floor = _mapping(dataset_manifest.get("activation_floor"))
    proof = _mapping(dataset_manifest.get("production_dataset_proof"))
    proof_controls = _mapping(proof.get("controls"))
    lineage = _mapping(registry_entry.get("lineage"))
    metadata = _mapping(registry_entry.get("metadata"))
    bundle_governance = _mapping(artifact_bundle.get("governance"))

    if floor.get("dataset_gate_satisfied") is not True:
        errors.append("dataset_manifest.activation_floor.dataset_gate_satisfied must be true")
    if proof_controls.get("no_order_route") is not True:
        errors.append("dataset_manifest.production_dataset_proof.controls.no_order_route must be true")
    if rs003_candidate.get("artifact_state") != "candidate":
        errors.append("strategy_spec_packet.rs003_candidate.artifact_state must be candidate")
    if rs003_candidate.get("registry_id") != strategy_spec_id:
        errors.append("strategy_spec_packet.rs003_candidate.registry_id mismatch")
    if strategy_binding.get("strategy_spec_id") != strategy_spec_id:
        errors.append("strategy_spec_packet.strategy_spec_binding.strategy_spec_id mismatch")
    if strategy_binding.get("dataset_ref") != dataset_ref:
        errors.append("strategy_spec_packet.strategy_spec_binding.dataset_ref mismatch")
    if strategy_binding.get("framework") != "qlib":
        errors.append("strategy_spec_packet.strategy_spec_binding.framework must be qlib")
    if strategy_binding.get("model_family") != "lightgbm":
        errors.append("strategy_spec_packet.strategy_spec_binding.model_family must be lightgbm")
    if registry_entry.get("artifact_type") != "model_artifact":
        errors.append("activation registry_entry.artifact_type must be model_artifact")
    if registry_entry.get("artifact_state") != "draft":
        errors.append("activation registry_entry.artifact_state must be draft")
    if registry_entry.get("strategy_id") != strategy_id:
        errors.append("activation registry_entry.strategy_id mismatch")
    if _deployment_stage(registry_entry) != "none":
        errors.append("activation registry_entry deployment stage must be none")
    if lineage.get("source_strategy_spec_id") != strategy_spec_id:
        errors.append("activation registry_entry.lineage.source_strategy_spec_id mismatch")
    if dataset_ref not in _strings(lineage.get("source_dataset_refs")):
        errors.append("activation registry_entry.lineage.source_dataset_refs missing dataset_ref")
    if metadata.get("framework") != "qlib":
        errors.append("activation registry_entry.metadata.framework must be qlib")
    if metadata.get("model_family") != "lightgbm":
        errors.append("activation registry_entry.metadata.model_family must be lightgbm")
    if candidate_packet.get("requested_artifact_state") != "candidate":
        errors.append("activation candidate_packet.requested_artifact_state must be candidate")
    if candidate_packet.get("deployment_stage") != "none":
        errors.append("activation candidate_packet.deployment_stage must be none")
    if production_packet.get("artifact_state") != "draft":
        errors.append("production_activation_packet.artifact_state must be draft")
    if production_packet.get("requested_artifact_state") != "candidate":
        errors.append("production_activation_packet.requested_artifact_state must be candidate")
    if production_packet.get("deployment_stage") != "none":
        errors.append("production_activation_packet.deployment_stage must be none")
    if production_packet.get("order_route") != "none":
        errors.append("production_activation_packet.order_route must be none")
    if production_packet.get("registry_write_authority") != "registry_service_only":
        errors.append("production_activation_packet.registry_write_authority invalid")
    production_proof = _mapping(production_packet.get("production_dataset_proof"))
    if _mapping(production_proof.get("storage")).get("dataset_ref") != dataset_ref:
        errors.append("production_activation_packet production proof dataset_ref mismatch")
    if _mapping(production_proof.get("controls")).get("no_order_route") is not True:
        errors.append("production_activation_packet controls.no_order_route must be true")
    packet_safety = _mapping(production_packet.get("safety_assertions"))
    for key in ("no_registry_write", "no_order_route", "no_broker_session"):
        if packet_safety.get(key) is not True:
            errors.append(f"production_activation_packet.safety_assertions.{key} must be true")
    if artifact_refs.get("registry_id") != registry_entry.get("registry_id"):
        errors.append("artifact_refs.registry_id must match registry_entry")
    if artifact_refs.get("registry_write_authority") != "registry_service_only":
        errors.append("artifact_refs.registry_write_authority invalid")
    if _mapping(artifact_refs.get("safety_assertions")).get("no_registry_write") is not True:
        errors.append("artifact_refs.safety_assertions.no_registry_write must be true")
    refs = artifact_refs.get("refs")
    if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes)) or len(refs) < 5:
        errors.append("artifact_refs.refs must include model/eval/bundle/registry/candidate refs")
    if artifact_manifest.get("registry_id") != registry_entry.get("registry_id"):
        errors.append("artifact manifest registry_id must match registry_entry")
    if artifact_manifest.get("checksum") != registry_entry.get("checksum"):
        errors.append("artifact manifest checksum must match registry_entry")
    if artifact_bundle.get("artifact_family") != "qlib_alpha":
        errors.append("artifact_bundle.artifact_family must be qlib_alpha")
    if artifact_bundle.get("model_family") != "lightgbm":
        errors.append("artifact_bundle.model_family must be lightgbm")
    if bundle_governance.get("output_type") != "alpha_score":
        errors.append("artifact_bundle.governance.output_type must be alpha_score")
    if bundle_governance.get("direct_live_influence") is not False:
        errors.append("artifact_bundle.governance.direct_live_influence must be false")

    if errors:
        raise RegistryAdmissionError(
            "Qlib registry admission inputs failed: " + "; ".join(errors)
        )


def _entry_criteria(
    *,
    dataset_manifest: Mapping[str, Any],
    strategy_spec_packet: Mapping[str, Any],
    artifact_bundle: Mapping[str, Any],
    production_packet: Mapping[str, Any],
) -> dict[str, bool]:
    governed = _mapping(dataset_manifest.get("governed_dataset"))
    binding = _mapping(strategy_spec_packet.get("strategy_spec_binding"))
    floor = _mapping(production_packet.get("dataset_floor_summary"))
    return {
        "baseline_strategy_exists": (
            _mapping(strategy_spec_packet.get("rs003_candidate")).get("artifact_state")
            == "candidate"
        ),
        "feature_engineering_need": bool(_strings(binding.get("feature_set"))),
        "data_sufficiency": (
            _positive_int(floor.get("num_instruments")) >= _positive_int(
                floor.get("required_min_instruments")
            )
            and _float(floor.get("history_years")) >= _float(
                floor.get("required_min_history_years")
            )
            and _positive_int(floor.get("min_periods_per_instrument"))
            >= _positive_int(floor.get("required_min_daily_periods"))
            and _text(governed.get("data_frequency")) == "daily"
        ),
        "supervised_learning_appropriate": (
            _text(binding.get("problem_type")) in {"prediction", "ranking", "regression"}
            and _text(binding.get("model_family")) == "lightgbm"
        ),
        "no_upstream_conflicts": (
            _text(artifact_bundle.get("framework")) == "qlib"
            and bool(_text(artifact_bundle.get("framework_version")))
        ),
        "registry_admission_review_only": (
            production_packet.get("requested_artifact_state") == "candidate"
            and production_packet.get("deployment_stage") == "none"
            and production_packet.get("order_route") == "none"
        ),
    }


def _registry_entry_schema_errors(payload: Mapping[str, Any]) -> list[str]:
    schema = json.loads((REPO_ROOT / REGISTRY_SCHEMA_REF).read_text(encoding="utf-8"))
    validator = Draft7Validator(schema)
    return [
        f"{REGISTRY_SCHEMA_REF}:{'.'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
        for error in sorted(validator.iter_errors(dict(payload)), key=lambda item: list(item.path))
    ]


def _dataset_ref(manifest: Mapping[str, Any]) -> str:
    governed = _mapping(manifest.get("governed_dataset"))
    refs = _strings(governed.get("source_dataset_refs"))
    if refs:
        return refs[0]
    return _required_text(manifest, "dataset_id")


def _deployment_stage(registry_entry: Mapping[str, Any]) -> str:
    return _text(_mapping(registry_entry.get("deployment_summary")).get("current_stage"))


def _source_run_ids(*values: str) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = _text(payload.get(key))
    if not value:
        raise RegistryAdmissionError(f"{key} missing")
    return value


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return []


def _positive_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, float) and value > 0:
        return int(value)
    return 0


def _float(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _sha256_json(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a Qlib registry admission packet.")
    parser.add_argument("--dataset-manifest", required=True)
    parser.add_argument("--strategy-spec-packet", required=True)
    parser.add_argument("--activation-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--task-id", default=TASK_ID)
    parser.add_argument("--created-at")
    parser.add_argument("--created-by", default="Codex")
    parser.add_argument("--version", default=ADMISSION_VERSION)
    args = parser.parse_args(argv)

    dataset_manifest = _read_json(args.dataset_manifest)
    strategy_spec_packet = _read_json(args.strategy_spec_packet)
    activation_artifacts = load_activation_artifacts(args.activation_dir)
    packet = build_registry_admission_packet(
        dataset_manifest,
        strategy_spec_packet,
        activation_artifacts,
        task_id=args.task_id,
        created_at=args.created_at,
        created_by=args.created_by,
        version=args.version,
        dataset_manifest_ref=args.dataset_manifest,
        strategy_spec_packet_ref=args.strategy_spec_packet,
        activation_artifact_ref=args.activation_dir,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
