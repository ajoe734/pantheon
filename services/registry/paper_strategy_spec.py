"""
paper_strategy_spec - MGMT-PAPER-001

Factory and evidence writer for the paper-loop StrategySpec candidate used by
the Management Paper Loop Proof (Track E / EPIC-02).

The produced object is intentionally paper-only. It gives downstream
ApprovalDecision, DeploymentPlan, RuntimeBinding, telemetry, and OODA packet
tasks a stable target id without creating a production registry write.
"""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from jsonschema import Draft7Validator


TASK_ID = "MGMT-PAPER-001"
EPIC = "EPIC-02 Management Paper Loop Proof"
PAPER_ENVIRONMENT = "paper"
PAPER_STRATEGY_SPEC_ID = "strategy-spec-paper-qlib-lgbm-001"
PAPER_STRATEGY_ID = "paper-qlib-lgbm-tw-equity-alpha"
PAPER_STRATEGY_VERSION = "1.0.0"
PAPER_SPEC_VERSION = "1.0"
PAPER_APPROVAL_DECISION_ID = "approval-paper-strategy-001"
PAPER_EVALUATOR_REF_ID = f"eval-{PAPER_STRATEGY_SPEC_ID}-paper"
PAPER_DATASET_REF = "dataset:tw-equity-paper-fixture-v1"
SOURCE_STRATEGY_SPEC_ID = "qlib-tw-cross-sectional-alpha-spec-v1"
CREATED_AT = "2026-05-15T15:00:00Z"

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_PATH = REPO_ROOT / "support" / "evidence" / f"{TASK_ID}-paper-strategy-spec.json"


PAPER_UNIVERSE_SYMBOLS = [
    "TWSE:1101",
    "TWSE:1216",
    "TWSE:1301",
    "TWSE:1303",
    "TWSE:1326",
    "TWSE:1402",
    "TWSE:2002",
    "TWSE:2105",
    "TWSE:2207",
    "TWSE:2301",
    "TWSE:2303",
    "TWSE:2308",
    "TWSE:2317",
    "TWSE:2327",
    "TWSE:2330",
    "TWSE:2345",
    "TWSE:2357",
    "TWSE:2379",
    "TWSE:2382",
    "TWSE:2395",
    "TWSE:2408",
    "TWSE:2412",
    "TWSE:2454",
    "TWSE:2603",
    "TWSE:2609",
    "TWSE:2615",
    "TWSE:2801",
    "TWSE:2880",
    "TWSE:2881",
    "TWSE:2882",
    "TWSE:2883",
    "TWSE:2884",
    "TWSE:2885",
    "TWSE:2886",
    "TWSE:2887",
    "TWSE:2890",
    "TWSE:2891",
    "TWSE:2892",
    "TWSE:2912",
    "TWSE:3008",
    "TWSE:3034",
    "TWSE:3045",
    "TWSE:3711",
    "TWSE:4904",
    "TWSE:4938",
    "TWSE:5871",
    "TWSE:5876",
    "TWSE:5880",
    "TPEx:3105",
    "TPEx:3227",
    "TPEx:3264",
    "TPEx:3293",
    "TPEx:4123",
    "TPEx:5483",
    "TPEx:6488",
]


@dataclass(frozen=True)
class PaperStrategySpecContext:
    """Stable paper-loop identifiers for the candidate StrategySpec."""

    registry_id: str = PAPER_STRATEGY_SPEC_ID
    strategy_id: str = PAPER_STRATEGY_ID
    version: str = PAPER_STRATEGY_VERSION
    created_at: str = CREATED_AT
    created_by: str = "Codex2"
    dataset_ref: str = PAPER_DATASET_REF
    source_strategy_spec_id: str = SOURCE_STRATEGY_SPEC_ID


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_schema(relative_path: str) -> Dict[str, Any]:
    return json.loads((REPO_ROOT / relative_path).read_text(encoding="utf-8"))


def _validation_errors(payload: Dict[str, Any], schema_path: str) -> List[str]:
    validator = Draft7Validator(_load_schema(schema_path))
    return [
        f"{'.'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
        for error in sorted(validator.iter_errors(payload), key=lambda item: list(item.path))
    ]


def _checksum(payload: Dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_strategy_spec(ctx: PaperStrategySpecContext | None = None) -> Dict[str, Any]:
    """Build a schema-valid paper-only StrategySpec payload."""
    ctx = ctx or PaperStrategySpecContext()
    return {
        "spec_version": PAPER_SPEC_VERSION,
        "strategy_id": ctx.strategy_id,
        "title": "Paper Qlib LightGBM TW equity alpha candidate",
        "hypothesis": (
            "A supervised LightGBM ranker over point-in-time daily TWSE and TPEx "
            "OHLCV features can produce a paper-stage cross-sectional alpha score "
            "without broker, order, or live capital side effects."
        ),
        "objective": (
            "Provide the Management paper-loop proof with a governed StrategySpec "
            "target that can be approved, planned for paper deployment, bound by "
            "runtime-manager, observed by telemetry, and replayed in OODA evidence."
        ),
        "market_scope": {
            "symbols": PAPER_UNIVERSE_SYMBOLS,
            "asset_classes": ["equity"],
            "venues": ["TWSE", "TPEx"],
            "frequency": "1d",
        },
        "data_dependencies": [
            {
                "ref": ctx.dataset_ref,
                "kind": "dataset",
            },
            {
                "ref": "services/registry/strategy-specs/qlib-tw-cross-sectional-alpha-v1.md",
                "kind": "repo",
            },
            {
                "ref": (
                    "docs/04/pantheon_sa_supplemental_2026-05-15/"
                    "SD_management_console_multi_persona_ooda.md#epic-02"
                ),
                "kind": "note",
            },
        ],
        "execution_profile": {
            "signal_schema_version": "paper-alpha-score-v1",
            "quantity_type": "PERCENT_PORTFOLIO",
            "rebalance_cadence": "daily close, paper replay only",
            "execution_mode_hint": PAPER_ENVIRONMENT,
        },
        "evaluation_plan": {
            "metrics": [
                "information_coefficient",
                "information_ratio",
                "paper_sharpe_ratio",
                "max_drawdown",
                "turnover_rate",
                "no_live_capital_side_effects",
            ],
            "candidate_gate": (
                "StrategySpec validates against services/control-plane/specs/"
                "strategy_spec.schema.json and carries a candidate registry entry."
            ),
            "paper_gate": (
                "ApprovalDecision may approve this target only for paper-stage "
                "deployment with live_capital_side_effects=false."
            ),
            "live_gate": (
                "Live and capital-binding activation remain fail-closed until a "
                "separate canary/live activation gate is approved."
            ),
        },
        "governance": {
            "approval_required": True,
            "policy_id": "paper-canary-live-policy",
            "risk_profile": "medium: paper-only candidate, no live capital side effects",
        },
        "provenance": {
            "source_kind": "workflow",
            "created_at": ctx.created_at,
            "source_refs": [
                f"strategy-spec:{ctx.source_strategy_spec_id}",
                "MGMT-PAPER-001",
                "MGMT-PAPER-002",
            ],
            "created_by": ctx.created_by,
        },
    }


def build_registry_entry(
    strategy_spec: Dict[str, Any],
    ctx: PaperStrategySpecContext | None = None,
) -> Dict[str, Any]:
    """Build the non-writing candidate registry entry for the StrategySpec."""
    ctx = ctx or PaperStrategySpecContext()
    return {
        "registry_id": ctx.registry_id,
        "artifact_type": "strategy_spec",
        "strategy_id": ctx.strategy_id,
        "version": ctx.version,
        "artifact_state": "candidate",
        "created_at": ctx.created_at,
        "lineage": {
            "parent_registry_ids": [ctx.source_strategy_spec_id],
            "source_run_ids": [TASK_ID],
            "source_dataset_refs": [ctx.dataset_ref],
            "source_strategy_spec_id": ctx.source_strategy_spec_id,
        },
        "storage_ref": {
            "backend": "inline",
            "path": "$.strategy_spec",
        },
        "checksum": _checksum(strategy_spec),
        "producer_run_id": TASK_ID,
        "evaluation_summary": {
            "evaluation_kind": "paper_fixture_schema_and_gate_check",
            "status": "pass",
            "evaluator_result_ref": PAPER_EVALUATOR_REF_ID,
            "live_capital_side_effects": False,
        },
        "deployment_summary": {
            "current_stage": "none",
        },
        "metadata": {
            "environment": PAPER_ENVIRONMENT,
            "framework": "qlib",
            "model_family": "lightgbm",
            "market": "TW",
            "universe": "TWSE + TPEx paper fixture",
            "paper_loop_candidate": True,
            "approval_decision_id_expected": PAPER_APPROVAL_DECISION_ID,
            "live_capital_side_effects": False,
        },
    }


def build_paper_evaluator_result(
    strategy_spec: Dict[str, Any],
    registry_entry: Dict[str, Any],
) -> Dict[str, Any]:
    """Build the paper evaluator reference consumed by MGMT-PAPER-002."""
    return {
        "ref_type": "evaluator_result",
        "ref_id": PAPER_EVALUATOR_REF_ID,
        "target_type": "strategy_spec",
        "target_id": registry_entry["registry_id"],
        "target_version": registry_entry["version"],
        "status": "pass",
        "evaluation_kind": "paper_fixture_schema_and_gate_check",
        "checks": {
            "strategy_spec_schema_valid": True,
            "registry_entry_schema_valid": True,
            "artifact_state_candidate": registry_entry["artifact_state"] == "candidate",
            "deployment_stage_none": registry_entry["deployment_summary"]["current_stage"] == "none",
            "execution_mode_paper": (
                strategy_spec["execution_profile"]["execution_mode_hint"] == PAPER_ENVIRONMENT
            ),
            "live_capital_side_effects": False,
        },
        "storage_ref": {
            "backend": "inline",
            "path": "$.paper_evaluator_result",
        },
        "note": "Paper StrategySpec candidate is admissible for paper approval only.",
    }


def validate_strategy_spec(strategy_spec: Dict[str, Any]) -> List[str]:
    return _validation_errors(
        strategy_spec,
        "services/control-plane/specs/strategy_spec.schema.json",
    )


def validate_registry_entry(registry_entry: Dict[str, Any]) -> List[str]:
    return _validation_errors(
        registry_entry,
        "services/registry/registry_entry_schema.json",
    )


def validate_paper_invariants(
    strategy_spec: Dict[str, Any],
    registry_entry: Dict[str, Any],
    evaluator_result: Dict[str, Any],
) -> List[str]:
    errors: List[str] = []
    if registry_entry.get("registry_id") != PAPER_STRATEGY_SPEC_ID:
        errors.append("registry_id must match MGMT-PAPER-002 target_id")
    if registry_entry.get("version") != PAPER_STRATEGY_VERSION:
        errors.append("registry version must match MGMT-PAPER-002 target_version")
    if registry_entry.get("artifact_state") != "candidate":
        errors.append("paper StrategySpec must remain artifact_state=candidate before approval")
    if registry_entry.get("deployment_summary", {}).get("current_stage") != "none":
        errors.append("candidate StrategySpec deployment_summary.current_stage must be none")
    if strategy_spec.get("execution_profile", {}).get("execution_mode_hint") != PAPER_ENVIRONMENT:
        errors.append("StrategySpec execution_mode_hint must be paper")
    if evaluator_result.get("checks", {}).get("live_capital_side_effects") is not False:
        errors.append("paper evaluator must assert live_capital_side_effects=false")
    if len(strategy_spec.get("market_scope", {}).get("symbols", [])) < 50:
        errors.append("paper universe fixture must carry at least 50 symbols")
    return errors


def build_paper_strategy_spec_packet(
    ctx: PaperStrategySpecContext | None = None,
    generated_at: str | None = None,
) -> Dict[str, Any]:
    """Build the full MGMT-PAPER-001 evidence packet."""
    ctx = ctx or PaperStrategySpecContext()
    strategy_spec = build_strategy_spec(ctx)
    registry_entry = build_registry_entry(strategy_spec, ctx)
    evaluator_result = build_paper_evaluator_result(strategy_spec, registry_entry)
    validation = {
        "strategy_spec_errors": validate_strategy_spec(strategy_spec),
        "registry_entry_errors": validate_registry_entry(registry_entry),
        "semantic_errors": validate_paper_invariants(
            strategy_spec,
            registry_entry,
            evaluator_result,
        ),
    }
    return {
        "task_id": TASK_ID,
        "epic": EPIC,
        "environment": PAPER_ENVIRONMENT,
        "generated_at": generated_at or _utc_now(),
        "live_capital_side_effects": False,
        "strategy_spec": strategy_spec,
        "registry_entry": registry_entry,
        "paper_evaluator_result": evaluator_result,
        "validation": validation,
        "approval_target_ref": {
            "approval_decision_id_expected": PAPER_APPROVAL_DECISION_ID,
            "target_type": "strategy_spec",
            "target_id": PAPER_STRATEGY_SPEC_ID,
            "target_version": PAPER_STRATEGY_VERSION,
        },
        "ooda_observe_orient_refs": {
            "strategy_spec_ref": f"strategy-spec:{PAPER_STRATEGY_SPEC_ID}",
            "registry_entry_ref": f"registry:{PAPER_STRATEGY_SPEC_ID}",
            "dataset_ref": ctx.dataset_ref,
            "evaluator_result_ref": PAPER_EVALUATOR_REF_ID,
        },
        "downstream_contract": {
            "MGMT-PAPER-002": {
                "uses": "approval_target_ref",
                "expected_decision_id": PAPER_APPROVAL_DECISION_ID,
            },
            "MGMT-PAPER-003": {
                "uses": "approval_decision_id and registry_entry.registry_id",
                "target_stage": "paper",
            },
            "MGMT-PAPER-004": {
                "uses": "DeploymentPlan artifact_id to create paper RuntimeBinding",
                "runtime_side_effects": "paper_only",
            },
            "MGMT-PAPER-007": {
                "uses": "ooda_observe_orient_refs for complete OODA packet",
            },
        },
        "paper_loop_chain": [
            "MGMT-PAPER-001: candidate StrategySpec  <- this artifact",
            "MGMT-PAPER-002: ApprovalDecision packet",
            "MGMT-PAPER-003: DeploymentPlan packet",
            "MGMT-PAPER-004: paper RuntimeBinding packet",
            "MGMT-PAPER-005: telemetry packet",
            "MGMT-PAPER-006: EvolutionDecision review packet",
            "MGMT-PAPER-007: complete OODA packet",
        ],
    }


def packet_validation_errors(packet: Dict[str, Any]) -> List[str]:
    validation = packet.get("validation", {})
    return [
        error
        for key in ("strategy_spec_errors", "registry_entry_errors", "semantic_errors")
        for error in validation.get(key, [])
    ]


def write_evidence_packet(
    packet: Dict[str, Any],
    out_path: Path = EVIDENCE_PATH,
) -> Dict[str, Any]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return packet


def main() -> int:
    print("=== MGMT-PAPER-001: paper candidate StrategySpec ===\n")
    packet = build_paper_strategy_spec_packet()
    errors = packet_validation_errors(packet)
    if errors:
        print(f"FAIL: validation errors: {errors}")
        return 1

    write_evidence_packet(packet)
    registry_entry = packet["registry_entry"]
    print(f"  registry_id   : {registry_entry['registry_id']}")
    print(f"  strategy_id   : {registry_entry['strategy_id']}")
    print(f"  version       : {registry_entry['version']}")
    print(f"  artifact_state: {registry_entry['artifact_state']}")
    print(f"  current_stage : {registry_entry['deployment_summary']['current_stage']}")
    print(f"  evaluator_ref : {packet['paper_evaluator_result']['ref_id']}")
    print(f"  validation    : PASS (no errors)")
    print(f"\n  evidence packet written to: {EVIDENCE_PATH}")
    print("\n=== PASS ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
