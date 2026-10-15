"""Test-only builders for a Persona that owns a canonical momentum artifact.

Mirrors the shape of a real Persona ``provisioning_authoritative_readback``
(runtime binding + Object Store projection) so consumers can be exercised
against the same ``ArtifactLoader.load_exact`` authority as production.
"""
from __future__ import annotations

import copy
import json
from typing import Any

from services.registry.strategy_artifact import strategy_artifact_checksum

PERSONA_ID = "persona-132519698e6199e80326"
TENANT_ID = "tenant-dev"
ARTIFACT_ID = "artifact-persona-paper-aa060d53ff712cbba704"
STRATEGY_ID = "strategy-persona-aa060d53ff712cbba704"


def momentum_artifact(
    *, lookback: int = 2, threshold: float = 0.0, symbols: tuple[str, ...] = ("2330.TW",)
) -> dict[str, Any]:
    return {
        "artifact_id": ARTIFACT_ID,
        "strategy_id": STRATEGY_ID,
        "version": "1.0.0",
        "artifact_schema_version": "1.0",
        "lineage": {
            "source_run_ids": ["persona-provisioning-forward-artifact-aa060d53ff712cbba704"],
            "parent_registry_ids": ["artifact-persona-paper-baseline-aa060d53ff712cbba704"],
            "source_strategy_spec_id": "reg-strategy-spec-persona-aa060d53ff712cbba704",
        },
        "algorithm_ref": {
            "path": "services/registry/strategy_artifact.py",
            "commit": "1cfaf933fbcc119605a455ebea483d54d2521ebb",
            "engine": "lean",
            "entrypoint": "services.registry.strategy_artifact:evaluate_strategy_action",
            "repository": "ajoe734/pantheon",
            "signal_interface": "services.execution.lean_runtime.paper_signal_producer:Strategy",
            "logic_interpreter": "services.registry.strategy_artifact:evaluate_strategy_action",
            "signal_schema_version": "1.0",
        },
        "strategy_logic": {
            "kind": "close_to_close_momentum",
            "positive_action": "BUY",
            "non_positive_action": "SELL",
            "lookback_parameter": "lookback_bars",
            "threshold_parameter": "momentum_threshold",
        },
        "parameters": {
            "market": "TW",
            "symbols": list(symbols),
            "data_source": "source-ingest:paper/persona-bootstrap",
            "bar_frequency": "1d",
            "lookback_bars": lookback,
            "quantity_type": "SHARES",
            "order_quantity": 1,
            "momentum_threshold": threshold,
            "zero_momentum_action": "SELL",
        },
        "provenance_refs": ["task:LOOP-PROD-PER-001"],
        "mutation_surface": {
            "controls": [
                {"parameter_key": "lookback_bars", "value_type": "integer", "current_value": lookback,
                 "allowed_range": {"min": 2, "max": 60}, "step": 1},
                {"parameter_key": "momentum_threshold", "value_type": "number", "current_value": threshold,
                 "allowed_range": {"min": 0.0, "max": 0.05}, "step": 0.001},
            ],
            "immutable_parameters": [
                "symbols", "bar_frequency", "data_source", "order_quantity",
                "quantity_type", "zero_momentum_action", "market",
            ],
        },
    }


def runtime_binding(artifact: dict[str, Any] | None = None, *, tenant_id: str = TENANT_ID) -> dict[str, Any]:
    artifact = copy.deepcopy(artifact or momentum_artifact())
    payload = json.dumps(artifact, sort_keys=True, separators=(",", ":"))
    checksum = strategy_artifact_checksum(artifact)
    metadata = {
        "version": artifact["version"],
        "checksum": checksum,
        "registry_id": artifact["artifact_id"],
        "strategy_id": artifact["strategy_id"],
        "artifact_type": "execution_bundle",
        "artifact_state": "approved",
        "runtime_action": "deploy_new_binding",
        "promotion_state": "paper",
        "deployment_stage": "paper",
        "created_at": "2026-10-15T19:19:47Z",
        "approved_at": "2026-10-15T19:19:48Z",
        "lineage": copy.deepcopy(artifact["lineage"]),
        "rollback": {
            "action_type": "pause_then_replace",
            "target_version": "0.0.1",
            "target_registry_id": "artifact-persona-paper-baseline-aa060d53ff712cbba704",
        },
        "capital_pool_id": "pool-persona-paper-aa060d53ff712cbba704",
        "gross_scale_pct": 100.0,
        "capital_scale_pct": 0.0,
        "deployment_plan_id": "plan-persona-paper-aa060d53ff712cbba704",
        "sponsor_persona_id": PERSONA_ID,
        "approval_decision_id": "apv-persona-paper-artifact-aa060d53ff712cbba704",
    }
    base = f"openclaw/registry/{artifact['strategy_id']}/{artifact['version']}"
    return {
        "status": "active",
        "binding_id": "rb-ece1ab6cfcdc4c518c40874a97a58a66",
        "runtime_id": "rt-a47d6729",
        "artifact_id": artifact["artifact_id"],
        "artifact_version": artifact["version"],
        "execution_mode": "paper",
        "metadata": {
            "tenant_id": tenant_id,
            "persona_id": PERSONA_ID,
            "strategy_id": artifact["strategy_id"],
            "artifact_checksum": checksum,
            "object_store": {f"{base}/artifact.bin": payload, f"{base}/metadata.json": metadata},
        },
    }


def persona_with_model(artifact: dict[str, Any] | None = None, *, tenant_id: str = TENANT_ID) -> dict[str, Any]:
    return {
        "persona_id": PERSONA_ID,
        "tenant_id": tenant_id,
        "metadata": {
            "tenant_id": tenant_id,
            "provisioning_authoritative_readback": {"runtime_binding": runtime_binding(artifact, tenant_id=tenant_id)},
        },
    }
