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
VERIFICATION_INDEX_PATH = ROOT / "docs" / "05" / "system-verification-rounds" / "INDEX.md"

EXPECTED_CANONICAL_LOOP_IDS = [
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

EXPECTED_COMPOSITE_OVERLAY_IDS = ["per_persona_ooda"]

EXPECTED_OODA_COMPOSITION = [
    "source_ingestion",
    "strategy_distillation",
    "alpha_replication",
    "persona_teaching",
    "agora_interaction_evidence",
    "human_imitation_shadow_evaluation",
    "consultation",
    "promotion_deployment",
    "telemetry_reconciliation",
    "evolution",
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


def test_schema_rejects_duplicate_canonical_loop_identity() -> None:
    mutated = copy.deepcopy(_registry())
    mutated["loops"][1]["loop_id"] = mutated["loops"][0]["loop_id"]

    errors = _validation_errors(mutated)

    assert len(errors) == 1
    assert errors[0].startswith("None of ")


def test_schema_rejects_missing_canonical_loop_identity() -> None:
    mutated = copy.deepcopy(_registry())
    mutated["loops"].pop()

    errors = _validation_errors(mutated)

    assert any("is too short" in error for error in errors)


def test_schema_rejects_replacement_canonical_loop_identity() -> None:
    mutated = copy.deepcopy(_registry())
    mutated["loops"][-1]["loop_id"] = "replacement_loop"

    errors = _validation_errors(mutated)

    assert len(errors) == 1
    assert errors[0].startswith("None of ")


def test_schema_rejects_wrong_composite_overlay_identity() -> None:
    mutated = copy.deepcopy(_registry())
    mutated["composite_overlays"][0]["loop_id"] = "persona_ooda"

    errors = _validation_errors(mutated)

    assert "'per_persona_ooda' was expected" in errors


@pytest.mark.parametrize(
    ("composition", "expected_error_fragment"),
    [
        pytest.param(
            EXPECTED_OODA_COMPOSITION[:-1],
            "is too short",
            id="missing-member",
        ),
        pytest.param(
            EXPECTED_OODA_COMPOSITION[:-1] + [EXPECTED_OODA_COMPOSITION[0]],
            "has non-unique elements",
            id="duplicate-member",
        ),
        pytest.param(
            EXPECTED_OODA_COMPOSITION[:-1] + ["capital_pool_execution"],
            "is not one of",
            id="excluded-canonical-loop",
        ),
    ],
)
def test_schema_rejects_invalid_ooda_composition(
    composition: list[str], expected_error_fragment: str
) -> None:
    mutated = copy.deepcopy(_registry())
    mutated["composite_overlays"][0]["composed_of"] = composition

    errors = _validation_errors(mutated)

    assert any(expected_error_fragment in error for error in errors)


def test_registry_has_one_stable_id_for_each_l1_policy_loop() -> None:
    policy_titles = re.findall(r"^### 3\.\d+ (.+)$", POLICY_PATH.read_text(encoding="utf-8"), flags=re.MULTILINE)
    registry = _registry()
    loops = registry["loops"]

    assert [loop["loop_id"] for loop in loops] == EXPECTED_CANONICAL_LOOP_IDS
    assert len({loop["loop_id"] for loop in loops}) == len(EXPECTED_CANONICAL_LOOP_IDS)
    assert [loop["policy_ref"]["section_title"] for loop in loops] == policy_titles
    assert {loop["classification"] for loop in loops} == {"canonical"}


def test_registry_has_one_separately_classified_per_persona_ooda_overlay() -> None:
    registry = _registry()
    overlays = registry["composite_overlays"]

    assert [overlay["loop_id"] for overlay in overlays] == EXPECTED_COMPOSITE_OVERLAY_IDS
    assert overlays[0]["classification"] == "composite_overlay"
    assert overlays[0]["composed_of"] == EXPECTED_OODA_COMPOSITION
    assert set(overlays[0]["composed_of"]) < set(EXPECTED_CANONICAL_LOOP_IDS)
    assert overlays[0]["policy_ref"] == {
        "document": "Pantheon_總索引版系統分析文件.md",
        "section": "6.4",
        "section_title": "Per-Persona OODA 回路",
    }


def test_only_capital_pool_execution_is_continuously_resident() -> None:
    registry = _registry()
    entries = registry["loops"] + registry["composite_overlays"]

    assert [
        entry["loop_id"]
        for entry in entries
        if entry["trigger_model"]["continuous"]
    ] == ["capital_pool_execution"]


def test_verification_index_uses_the_same_twelve_plus_overlay_classification() -> None:
    verification_index = VERIFICATION_INDEX_PATH.read_text(encoding="utf-8")

    assert "12 canonical L1 loops" in verification_index
    assert "`per_persona_ooda`" in verification_index
    assert "`composite_overlay`" in verification_index
    assert "11 main loops" not in verification_index


def test_catalog_uses_sa21_maturity_and_truth_vocabularies() -> None:
    registry = _registry()

    assert [entry["level"] for entry in registry["maturity_levels"]] == EXPECTED_MATURITY_LEVELS
    assert [entry["level"] for entry in registry["truth_levels"]] == EXPECTED_TRUTH_LEVELS

    for loop in registry["loops"] + registry["composite_overlays"]:
        assert set(loop["evidence_profile"]) == set(EXPECTED_TRUTH_LEVELS)
        assert loop["evidence_profile"]["registry_metadata"]["status"] == "present"
        assert loop["maturity"]["current"] in EXPECTED_MATURITY_LEVELS
        assert loop["maturity"]["target"] in EXPECTED_MATURITY_LEVELS
        assert isinstance(loop["maturity"]["current"], str)
        assert isinstance(loop["maturity"]["target"], str)


def test_reconciled_claim_requires_controller_queries_restart_and_live_proof() -> None:
    registry = _registry()
    mutated = copy.deepcopy(registry)
    # Loops whose controller is already implemented carry the required contract
    # fields, so the "no controller yet" rejection must be exercised on a loop
    # that still declares `not_implemented`.
    loop = next(
        candidate
        for candidate in mutated["loops"]
        if candidate["controller_contract"]["status"] == "not_implemented"
    )
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


def test_each_canonical_loop_has_desired_actual_owner_target_and_task_path() -> None:
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


def test_overlay_task_refs_are_reference_only_and_use_product_followups() -> None:
    overlay = _registry()["composite_overlays"][0]

    assert overlay["execution_tasks"][0] == {
        "task_id": "LOOP-PROD-000",
        "role": "foundation",
    }
    assert overlay["maturity"]["current"] == "api-only"
    assert overlay["controller_contract"]["status"] == "not_implemented"
    assert overlay["evidence_profile"]["scheduled_tick"]["status"] == "historical"
    assert "archive" in overlay["evidence_profile"]["scheduled_tick"]["note"].lower()


def test_schema_rejects_cross_classified_policy_authority() -> None:
    registry = _registry()
    mutated = copy.deepcopy(registry)
    mutated["composite_overlays"][0]["policy_ref"] = copy.deepcopy(
        mutated["loops"][0]["policy_ref"]
    )

    errors = _validation_errors(mutated)

    assert "'Pantheon_總索引版系統分析文件.md' was expected" in errors
    assert "'6.4' was expected" in errors
