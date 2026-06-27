from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "docs" / "deployment" / "loop-catalog.schema.json"
REGISTRY_PATH = ROOT / "docs" / "deployment" / "loop-catalog.registry.json"
POLICY_PATH = ROOT / "LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md"

EXPECTED_LOOP_IDS = [
    "source_ingestion",
    "strategy_distillation",
    "alpha_replication",
    "persona_teaching",
    "agora_interaction_evidence",
    "human_imitation_shadow_evaluation",
    "consultation",
    "promotion_deployment",
    "capital_pool_execution",
    "telemetry_reconciliation",
    "evolution",
    "bff_health_monitoring",
]

EXPECTED_MATURITY_LEVELS = [
    "manual",
    "api-only",
    "scheduled",
    "reconciled",
    "proven-live",
]

EXPECTED_TRUTH_LEVELS = [
    "seed_fixture",
    "registry_metadata",
    "scheduled_tick",
    "reconciled_live_proof",
    "proven_live_evidence",
]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema() -> dict:
    return _load_json(SCHEMA_PATH)


def _registry() -> dict:
    return _load_json(REGISTRY_PATH)


def _validation_errors(payload: dict) -> list[str]:
    validator = jsonschema.Draft7Validator(_schema(), format_checker=jsonschema.FormatChecker())
    return sorted(error.message for error in validator.iter_errors(payload))


def test_loop_catalog_schema_is_valid_draft7() -> None:
    jsonschema.Draft7Validator.check_schema(_schema())


def test_loop_catalog_registry_matches_schema() -> None:
    assert _validation_errors(_registry()) == []


def test_registry_has_one_stable_id_for_each_l1_policy_loop() -> None:
    policy_titles = re.findall(r"^### 3\.\d+ (.+)$", POLICY_PATH.read_text(encoding="utf-8"), flags=re.MULTILINE)
    registry = _registry()
    loops = registry["loops"]

    assert [loop["loop_id"] for loop in loops] == EXPECTED_LOOP_IDS
    assert len({loop["loop_id"] for loop in loops}) == len(EXPECTED_LOOP_IDS)
    assert [loop["policy_ref"]["section_title"] for loop in loops] == policy_titles


def test_catalog_uses_sa21_maturity_and_truth_vocabularies() -> None:
    registry = _registry()

    assert [entry["level"] for entry in registry["maturity_levels"]] == EXPECTED_MATURITY_LEVELS
    assert [entry["level"] for entry in registry["truth_levels"]] == EXPECTED_TRUTH_LEVELS

    for loop in registry["loops"]:
        assert set(loop["evidence_profile"]) == set(EXPECTED_TRUTH_LEVELS)
        assert loop["evidence_profile"]["registry_metadata"]["status"] == "present"
        assert loop["maturity"]["current"] in EXPECTED_MATURITY_LEVELS
        assert loop["maturity"]["target"] in EXPECTED_MATURITY_LEVELS
        assert isinstance(loop["maturity"]["current"], str)
        assert isinstance(loop["maturity"]["target"], str)


def test_reconciled_claim_requires_controller_queries_restart_and_live_proof() -> None:
    registry = _registry()
    mutated = copy.deepcopy(registry)
    loop = mutated["loops"][0]
    loop["maturity"]["current"] = "reconciled"

    errors = _validation_errors(mutated)

    assert "'not_implemented' is not one of ['implemented', 'proven_live']" in errors
    assert "None is not of type 'string'" in errors
    assert "'planned' was expected" not in errors
    assert "'present' was expected" in errors


def test_proven_live_claim_requires_proven_live_controller_and_evidence() -> None:
    registry = _registry()
    mutated = copy.deepcopy(registry)
    loop = mutated["loops"][0]
    loop["maturity"]["current"] = "proven-live"
    loop["controller_contract"].update(
        {
            "status": "implemented",
            "controller_name": "source-provisioning-reconciler",
            "desired_state_query": "list required data sources",
            "actual_state_query": "list connector and schedule state",
            "restart_behavior": "restart resumes by idempotency key",
            "liveness_metric": "last_successful_reconciliation_at",
        }
    )

    errors = _validation_errors(mutated)

    assert "'proven_live' was expected" in errors
    assert "'present' was expected" in errors


def test_each_loop_has_desired_actual_owner_target_and_task_path() -> None:
    for loop in _registry()["loops"]:
        assert loop["owner"]["authoritative_write_owner"]
        assert loop["desired_state"]["sources"]
        assert loop["actual_state"]["sources"]
        assert loop["maturity"]["target"] in EXPECTED_MATURITY_LEVELS
        assert loop["execution_tasks"][0] == {
            "task_id": "LOOP-AUTO-000",
            "role": "foundation",
        }
        assert any(task["task_id"] != "LOOP-AUTO-000" for task in loop["execution_tasks"])
