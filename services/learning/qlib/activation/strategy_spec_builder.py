"""Build a review-only Qlib StrategySpec packet for admission.

The builder consumes a governed dataset manifest and emits a schema-valid
StrategySpec plus the review projections needed by the Qlib admission lane. It
does not write registry truth, run Qlib, open broker sessions, or route orders.
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


TASK_ID = "MGMT-QLIB-002"
SPEC_VERSION = "1.0"
REGISTRY_VERSION = "1.0.0"
SOURCE_STRATEGY_SPEC_ID = "qlib-tw-cross-sectional-alpha-spec-v1"
SOURCE_STRATEGY_DOC = "services/registry/strategy-specs/qlib-tw-cross-sectional-alpha-v1.md"
DATASET_MANIFEST_REF = "support/evidence/MGMT-QLIB-001/dataset_manifest.json"
RS003_REVIEW_REF = "docs/reviews/2026-05-12-qlib-act-001-codex2-review.md"
ACTIVATION_CRITERIA_REF = "services/learning/qlib/ACTIVATION_CRITERIA.md"
STRATEGY_SCHEMA_REF = "services/control-plane/specs/strategy_spec.schema.json"
REGISTRY_SCHEMA_REF = "services/registry/registry_entry_schema.json"
MIN_INSTRUMENTS = 50
MIN_HISTORY_YEARS = 2.0
MIN_DAILY_PERIODS = 504
REQUIRED_OHLCV_FIELDS = ("open", "high", "low", "close", "volume")

REPO_ROOT = Path(__file__).resolve().parents[4]


class StrategySpecBuilderError(ValueError):
    """Raised when a Qlib StrategySpec packet cannot be built safely."""


def build_strategy_spec_packet(
    manifest: Mapping[str, Any],
    *,
    task_id: str = TASK_ID,
    created_at: str | None = None,
    created_by: str = "Codex2",
    version: str = REGISTRY_VERSION,
    dataset_manifest_ref: str = DATASET_MANIFEST_REF,
) -> dict[str, Any]:
    """Build the Qlib admission StrategySpec packet without side effects."""
    normalized = _normalize_manifest(manifest)
    timestamp = created_at or _utc_now()
    strategy_spec = build_strategy_spec(
        normalized,
        created_at=timestamp,
        created_by=created_by,
        dataset_manifest_ref=dataset_manifest_ref,
    )
    registry_entry = build_registry_entry(
        strategy_spec,
        normalized,
        task_id=task_id,
        version=version,
        created_at=timestamp,
    )
    binding = build_strategy_spec_binding(
        strategy_spec,
        registry_entry,
        normalized,
        task_id=task_id,
    )
    candidate = build_rs003_candidate_probe(registry_entry)
    governed_dataset = copy.deepcopy(
        normalized.get("qlib_preflight_governed_dataset")
        or normalized["governed_dataset"]
    )
    preflight_packet = {
        "rs003_candidate": candidate,
        "governed_dataset": governed_dataset,
        "strategy_spec_binding": binding,
    }
    packet = {
        "schema_version": "1.0",
        "packet_id": f"qlib-strategy-spec:{registry_entry['registry_id']}:{version}",
        "task_id": task_id,
        "created_at": timestamp,
        "created_by": created_by,
        "strategy_spec": strategy_spec,
        "registry_entry": registry_entry,
        "rs003_candidate": candidate,
        "strategy_spec_binding": binding,
        "preflight_packet": preflight_packet,
        "source_refs": {
            "dataset_manifest_ref": dataset_manifest_ref,
            "source_strategy_spec_doc": SOURCE_STRATEGY_DOC,
            "rs003_review_ref": RS003_REVIEW_REF,
            "activation_criteria_ref": ACTIVATION_CRITERIA_REF,
        },
        "downstream_scope": {
            "strategy_spec_builder_only": True,
            "registry_write_authority": "registry_service_only",
            "registry_write_performed": False,
            "training_performed": False,
            "broker_session_opened": False,
            "order_route": "none",
            "deployment_stage": "none",
        },
        "safety_assertions": {
            "no_registry_write": True,
            "no_training_run": True,
            "no_broker_session": True,
            "no_order_route": True,
            "deployment_stage_remains_none": (
                registry_entry["deployment_summary"]["current_stage"] == "none"
            ),
            "scoring_only_not_direct_action": True,
        },
    }
    validate_strategy_spec_packet(packet)
    return packet


def build_strategy_spec(
    normalized_manifest: Mapping[str, Any],
    *,
    created_at: str,
    created_by: str,
    dataset_manifest_ref: str,
) -> dict[str, Any]:
    """Build a canonical StrategySpec payload from the normalized manifest."""
    governed = _mapping(normalized_manifest["governed_dataset"])
    dataset_ref = _dataset_ref(normalized_manifest)
    strategy_id = _required_text(normalized_manifest, "strategy_id")
    registry_id = _required_text(normalized_manifest, "source_strategy_spec_id")
    venues = _strings(governed.get("exchange_segments"))
    frequency = _text(governed.get("data_frequency")) or "daily"
    return {
        "spec_version": SPEC_VERSION,
        "strategy_id": strategy_id,
        "title": "Qlib LightGBM TW cross-sectional equity alpha",
        "hypothesis": (
            "A supervised LightGBM model over governed point-in-time TWSE and "
            "TPEx daily OHLCV features can rank instruments by expected 5-day "
            "forward return without broker, order, or live capital side effects."
        ),
        "objective": (
            "Provide the Qlib admission lane with a machine-readable StrategySpec "
            "that binds the RS-003 baseline alpha target to the governed TW OHLCV "
            "dataset manifest and can be reviewed by registry admission."
        ),
        "market_scope": {
            "symbols": [_universe_symbol(dataset_ref)],
            "asset_classes": ["equity"],
            "venues": venues or ["TWSE", "TPEx"],
            "frequency": frequency,
        },
        "data_dependencies": [
            {
                "ref": dataset_ref,
                "kind": "dataset",
            },
            {
                "ref": SOURCE_STRATEGY_DOC,
                "kind": "repo",
            },
            {
                "ref": dataset_manifest_ref,
                "kind": "note",
            },
            {
                "ref": ACTIVATION_CRITERIA_REF,
                "kind": "repo",
            },
        ],
        "execution_profile": {
            "signal_schema_version": "qlib-alpha-score-v1",
            "quantity_type": "PERCENT_PORTFOLIO",
            "rebalance_cadence": "daily close research scoring; optimizer owns allocation",
            "execution_mode_hint": "research",
        },
        "evaluation_plan": {
            "metrics": [
                "information_coefficient",
                "information_ratio",
                "test_sharpe_ratio",
                "max_drawdown",
                "annualized_return",
                "turnover_rate",
                "feature_stability",
                "no_order_route",
            ],
            "candidate_gate": (
                "RS-003 StrategySpec review, governed >=50 instrument / >=2 year "
                "dataset manifest, and supervised StrategySpec binding must all "
                "be attached before registry admission."
            ),
            "paper_gate": (
                "Paper or canary deployment is out of scope for this builder and "
                "requires a separate approval decision and deployment plan."
            ),
            "live_gate": (
                "Live and capital-binding activation remain fail-closed until the "
                "paper/canary/live policy admits a separate governed request."
            ),
        },
        "governance": {
            "approval_required": True,
            "policy_id": "paper-canary-live-policy",
            "risk_profile": "medium: research candidate, scoring-only, no order route",
        },
        "provenance": {
            "source_kind": "workflow",
            "created_at": created_at,
            "source_refs": [
                f"strategy-spec:{registry_id}",
                normalized_manifest["manifest_id"],
                dataset_ref,
                RS003_REVIEW_REF,
                TASK_ID,
            ],
            "created_by": created_by,
        },
    }


def build_registry_entry(
    strategy_spec: Mapping[str, Any],
    normalized_manifest: Mapping[str, Any],
    *,
    task_id: str,
    version: str,
    created_at: str,
) -> dict[str, Any]:
    """Build a non-writing candidate registry projection for the StrategySpec."""
    registry_id = _required_text(normalized_manifest, "source_strategy_spec_id")
    dataset_ref = _dataset_ref(normalized_manifest)
    governed = _mapping(normalized_manifest["governed_dataset"])
    checksum = _sha256_json(strategy_spec)
    return {
        "registry_id": registry_id,
        "artifact_type": "strategy_spec",
        "strategy_id": _required_text(strategy_spec, "strategy_id"),
        "version": version,
        "artifact_state": "candidate",
        "created_at": created_at,
        "lineage": {
            "parent_registry_ids": [],
            "source_run_ids": _source_run_ids(task_id, _text(normalized_manifest.get("task_id"))),
            "source_dataset_refs": [dataset_ref],
        },
        "storage_ref": {
            "backend": "inline",
            "path": "$.strategy_spec",
        },
        "checksum": f"sha256:{checksum}",
        "producer_run_id": task_id,
        "evaluation_summary": {
            "status": "pass",
            "evaluation_kind": "strategy_spec_schema_and_binding_check",
            "strategy_spec_schema_valid": True,
            "dataset_gate_satisfied": True,
            "training_performed": False,
        },
        "deployment_summary": {
            "current_stage": "none",
        },
        "metadata": {
            "framework": "qlib",
            "model_family": "lightgbm",
            "market": _text(governed.get("market")) or "Taiwan",
            "universe": "TWSE + TPEx governed dataset manifest",
            "label": "5d_forward_return_zscored",
            "horizon": "5 trading days",
            "problem_type": "ranking",
            "source_strategy_spec_doc": SOURCE_STRATEGY_DOC,
            "rs003_review_ref": RS003_REVIEW_REF,
            "candidate_projection_only": True,
            "live_capital_side_effects": False,
        },
    }


def build_strategy_spec_binding(
    strategy_spec: Mapping[str, Any],
    registry_entry: Mapping[str, Any],
    normalized_manifest: Mapping[str, Any],
    *,
    task_id: str,
) -> dict[str, Any]:
    """Build the preflight-compatible StrategySpec binding probe."""
    dataset_ref = _dataset_ref(normalized_manifest)
    return {
        "schema_version": "1.0",
        "task_id": task_id,
        "strategy_spec_id": registry_entry["registry_id"],
        "canonical_strategy_spec_id": registry_entry["registry_id"],
        "source_strategy_spec_id": registry_entry["registry_id"],
        "strategy_id": strategy_spec["strategy_id"],
        "dataset_manifest_id": normalized_manifest["manifest_id"],
        "dataset_ref": dataset_ref,
        "source_dataset_refs": [dataset_ref],
        "label_definition": (
            "5 trading day forward return z-scored cross-sectionally: "
            "label_5d = (close[t+5] - close[t]) / close[t]."
        ),
        "supervised_target": (
            "Cross-sectional ranking/regression of expected forward return; "
            "output is a scoring-only alpha signal, not an action policy."
        ),
        "problem_type": "ranking",
        "model_family": "lightgbm",
        "framework": "qlib",
        "feature_set": [
            "momentum",
            "volatility",
            "volume",
            "technical",
            "cross_sectional_rank",
        ],
        "execution_boundary": {
            "output_type": "alpha_score",
            "direct_order_route": False,
            "deployment_stage": "none",
            "registry_write_authority": "registry_service_only",
        },
    }


def build_rs003_candidate_probe(registry_entry: Mapping[str, Any]) -> dict[str, Any]:
    """Build the preflight-compatible RS-003 candidate probe."""
    registry_id = _required_text(registry_entry, "registry_id")
    return {
        "candidate_registry_id": registry_id,
        "registry_id": registry_id,
        "artifact_id": registry_id,
        "artifact_state": "candidate",
        "rs003_evidence_ref": RS003_REVIEW_REF,
        "replication_evidence_ref": RS003_REVIEW_REF,
        "pass_evidence_ref": RS003_REVIEW_REF,
        "source_strategy_spec_ref": SOURCE_STRATEGY_DOC,
        "deployment_stage": "none",
        "candidate_projection_only": True,
    }


def validate_strategy_spec_packet(packet: Mapping[str, Any]) -> list[str]:
    """Return validation errors for a built packet, raising on unsafe semantics."""
    errors: list[str] = []
    strategy_spec = _mapping(packet.get("strategy_spec"))
    registry_entry = _mapping(packet.get("registry_entry"))
    binding = _mapping(packet.get("strategy_spec_binding"))
    scope = _mapping(packet.get("downstream_scope"))
    safety = _mapping(packet.get("safety_assertions"))

    errors.extend(_validation_errors(strategy_spec, STRATEGY_SCHEMA_REF))
    errors.extend(_validation_errors(registry_entry, REGISTRY_SCHEMA_REF))

    expected_checksum = f"sha256:{_sha256_json(strategy_spec)}"
    if registry_entry.get("checksum") != expected_checksum:
        errors.append("registry_entry.checksum must match strategy_spec")
    if registry_entry.get("artifact_state") != "candidate":
        errors.append("registry_entry.artifact_state must be candidate")
    if _mapping(registry_entry.get("deployment_summary")).get("current_stage") != "none":
        errors.append("registry_entry.deployment_summary.current_stage must be none")
    if strategy_spec.get("strategy_id") != registry_entry.get("strategy_id"):
        errors.append("strategy_id mismatch between strategy_spec and registry_entry")
    if binding.get("strategy_spec_id") != registry_entry.get("registry_id"):
        errors.append("strategy_spec_binding.strategy_spec_id mismatch")
    if binding.get("problem_type") not in {"prediction", "ranking", "regression"}:
        errors.append("strategy_spec_binding.problem_type must be supervised")
    if scope.get("registry_write_performed") is not False:
        errors.append("downstream_scope.registry_write_performed must be false")
    if scope.get("training_performed") is not False:
        errors.append("downstream_scope.training_performed must be false")
    if scope.get("broker_session_opened") is not False:
        errors.append("downstream_scope.broker_session_opened must be false")
    if scope.get("order_route") != "none":
        errors.append("downstream_scope.order_route must be none")
    for key in ("no_registry_write", "no_training_run", "no_broker_session", "no_order_route"):
        if safety.get(key) is not True:
            errors.append(f"safety_assertions.{key} must be true")

    if errors:
        raise StrategySpecBuilderError(
            "Qlib StrategySpec packet failed: " + "; ".join(errors)
        )
    return []


def _normalize_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(manifest, Mapping):
        raise StrategySpecBuilderError("dataset manifest must be a mapping")
    governed = _mapping(manifest.get("governed_dataset"))
    floor = _mapping(manifest.get("activation_floor"))
    scope = _mapping(manifest.get("downstream_scope"))
    proof = _mapping(manifest.get("production_dataset_proof"))
    controls = _mapping(proof.get("controls"))

    problems: list[str] = []
    normalized = copy.deepcopy(dict(manifest))
    for key in ("manifest_id", "strategy_id", "source_strategy_spec_id", "dataset_id"):
        if not _text(normalized.get(key)):
            problems.append(f"{key} missing")
    if governed.get("governed") is not True:
        problems.append("governed_dataset.governed=True not proven")
    if not _strings(governed.get("source_dataset_refs")):
        problems.append("governed_dataset.source_dataset_refs missing")
    if _positive_int(governed.get("num_instruments")) < MIN_INSTRUMENTS:
        problems.append("governed_dataset.num_instruments below Qlib floor")
    if _float(governed.get("history_years")) < MIN_HISTORY_YEARS:
        problems.append("governed_dataset.history_years below Qlib floor")
    if _positive_int(governed.get("min_periods_per_instrument")) < MIN_DAILY_PERIODS:
        problems.append("governed_dataset.min_periods_per_instrument below Qlib floor")
    if _strings(governed.get("ohlcv_fields")) != list(REQUIRED_OHLCV_FIELDS):
        problems.append("governed_dataset.ohlcv_fields must be open/high/low/close/volume")
    if _text(governed.get("data_frequency")).lower() != "daily":
        problems.append("governed_dataset.data_frequency must be daily for v1")
    if floor.get("dataset_gate_satisfied") is not True:
        problems.append("activation_floor.dataset_gate_satisfied=True not proven")
    if scope.get("registry_write_performed") is not False:
        problems.append("downstream_scope.registry_write_performed must be false")
    if scope.get("training_performed") is not False:
        problems.append("downstream_scope.training_performed must be false")
    if scope.get("broker_session_opened") is not False:
        problems.append("downstream_scope.broker_session_opened must be false")
    if scope.get("order_route") != "none":
        problems.append("downstream_scope.order_route must be none")
    if controls and controls.get("no_order_route") is not True:
        problems.append("production_dataset_proof.controls.no_order_route=True not proven")
    if problems:
        raise StrategySpecBuilderError(
            "Qlib StrategySpec manifest failed: " + "; ".join(problems)
        )
    return normalized


def _load_schema(relative_path: str) -> dict[str, Any]:
    return json.loads((REPO_ROOT / relative_path).read_text(encoding="utf-8"))


def _validation_errors(payload: Mapping[str, Any], schema_path: str) -> list[str]:
    validator = Draft7Validator(_load_schema(schema_path))
    return [
        f"{schema_path}:{'.'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
        for error in sorted(validator.iter_errors(dict(payload)), key=lambda item: list(item.path))
    ]


def _dataset_ref(manifest: Mapping[str, Any]) -> str:
    governed = _mapping(manifest.get("governed_dataset"))
    refs = _strings(governed.get("source_dataset_refs"))
    if refs:
        return refs[0]
    return _required_text(manifest, "dataset_id")


def _universe_symbol(dataset_ref: str) -> str:
    raw = dataset_ref.split(":", 1)[1] if dataset_ref.startswith("dataset:") else dataset_ref
    return f"universe:{raw}"


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
        raise StrategySpecBuilderError(f"{key} missing")
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a Qlib StrategySpec admission packet.")
    parser.add_argument("manifest", help="Path to a governed Qlib dataset manifest JSON.")
    parser.add_argument("--output", required=True, help="StrategySpec packet output path.")
    parser.add_argument("--task-id", default=TASK_ID)
    parser.add_argument("--created-at")
    parser.add_argument("--created-by", default="Codex2")
    parser.add_argument("--version", default=REGISTRY_VERSION)
    parser.add_argument("--dataset-manifest-ref", default=DATASET_MANIFEST_REF)
    args = parser.parse_args(argv)

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    packet = build_strategy_spec_packet(
        manifest,
        task_id=args.task_id,
        created_at=args.created_at,
        created_by=args.created_by,
        version=args.version,
        dataset_manifest_ref=args.dataset_manifest_ref,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
