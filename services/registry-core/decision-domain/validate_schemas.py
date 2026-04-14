#!/usr/bin/env python3
"""Validate decision-domain schemas and example payloads."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft7Validator

SCHEMA_DIR = Path(__file__).resolve().parent
EXAMPLE_DIR = SCHEMA_DIR / "examples"
CHAIN_FILE = EXAMPLE_DIR / "five_stage_chain.json"
SCHEMA_VERSION = "http://json-schema.org/draft-07/schema#"
COMMON_FIELDS = {
    "strategy_id",
    "artifact_id",
    "version",
    "evaluated_at",
    "input_refs",
    "output_refs",
    "decision_reasoning",
}
STAGES = [
    {
        "name": "RegimeState",
        "schema": "regime_state.schema.json",
        "example": "regime_state_example.json",
        "id_field": "regime_id",
        "schema_ref": "../regime_state.schema.json",
    },
    {
        "name": "UniverseSelection",
        "schema": "universe_selection.schema.json",
        "example": "universe_selection_example.json",
        "id_field": "universe_id",
        "schema_ref": "../universe_selection.schema.json",
    },
    {
        "name": "SignalInference",
        "schema": "signal_inference.schema.json",
        "example": "signal_inference_example.json",
        "id_field": "signal_id",
        "schema_ref": "../signal_inference.schema.json",
    },
    {
        "name": "AllocationDecision",
        "schema": "allocation_decision.schema.json",
        "example": "allocation_decision_example.json",
        "id_field": "allocation_id",
        "schema_ref": "../allocation_decision.schema.json",
    },
    {
        "name": "RiskAdjudication",
        "schema": "risk_adjudication.schema.json",
        "example": "risk_adjudication_example.json",
        "id_field": "adjudication_id",
        "schema_ref": "../risk_adjudication.schema.json",
    },
]


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def format_path(path_parts) -> str:
    rendered = "$"
    for part in path_parts:
        if isinstance(part, int):
            rendered += f"[{part}]"
        else:
            rendered += f".{part}"
    return rendered


def contains_ref(refs: list[dict], ref_type: str, ref_id: str) -> bool:
    return any(ref.get("ref_type") == ref_type and ref.get("ref_id") == ref_id for ref in refs)


def validate_schema(schema_name: str, schema: dict) -> list[str]:
    errors: list[str] = []
    if schema.get("$schema") != SCHEMA_VERSION:
        errors.append(f"$schema must be {SCHEMA_VERSION}")
    try:
        Draft7Validator.check_schema(schema)
    except Exception as exc:  # pragma: no cover - depends on validator internals
        errors.append(str(exc))

    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    for field in sorted(COMMON_FIELDS):
        if field not in properties:
            errors.append(f"missing common field {field}")
    if "model_ref" not in properties:
        errors.append("missing model_ref field")
    if not required.issuperset(COMMON_FIELDS):
        missing_required = sorted(COMMON_FIELDS - required)
        for field in missing_required:
            errors.append(f"common field {field} is not required")
    return errors


def validate_instance(schema: dict, instance: dict) -> list[str]:
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda err: list(err.path))
    return [f"{format_path(error.path)}: {error.message}" for error in errors]


def validate_chain(
    chain_doc: dict,
    schemas: dict[str, dict],
    individual_examples: dict[str, dict],
) -> tuple[list[str], dict[str, dict]]:
    errors: list[str] = []
    chain_entries: dict[str, dict] = {}
    chain = chain_doc.get("chain")

    if not isinstance(chain, list):
        return ["$.chain must be an array"], chain_entries
    if len(chain) != len(STAGES):
        errors.append(f"$.chain must contain {len(STAGES)} stages")
        return errors, chain_entries

    for index, stage in enumerate(STAGES):
        entry = chain[index]
        expected_stage = index + 1
        if entry.get("stage") != expected_stage:
            errors.append(f"stage {expected_stage}: expected numeric stage {expected_stage}")
        if entry.get("object") != stage["name"]:
            errors.append(
                f"stage {expected_stage}: expected object {stage['name']}, got {entry.get('object')}"
            )
        if entry.get("schema_ref") != stage["schema_ref"]:
            errors.append(
                f"stage {expected_stage}: expected schema_ref {stage['schema_ref']}, got {entry.get('schema_ref')}"
            )
        example = entry.get("example")
        if not isinstance(example, dict):
            errors.append(f"stage {expected_stage}: example must be an object")
            continue
        chain_entries[stage["name"]] = example
        example_errors = validate_instance(schemas[stage["schema"]], example)
        for error in example_errors:
            errors.append(f"stage {expected_stage}: {error}")

        standalone = individual_examples.get(stage["name"])
        if standalone is not None and standalone != example:
            errors.append(
                f"stage {expected_stage}: {stage['example']} does not mirror five_stage_chain.json"
            )

    if errors:
        return errors, chain_entries

    regime = chain_entries["RegimeState"]
    universe = chain_entries["UniverseSelection"]
    signal = chain_entries["SignalInference"]
    allocation = chain_entries["AllocationDecision"]
    risk = chain_entries["RiskAdjudication"]

    if not contains_ref(regime["output_refs"], "universe_selection", universe["universe_id"]):
        errors.append("RegimeState.output_refs must point to UniverseSelection.universe_id")
    if universe["regime_ref"]["regime_id"] != regime["regime_id"]:
        errors.append("UniverseSelection.regime_ref.regime_id must match RegimeState.regime_id")
    if not contains_ref(universe["output_refs"], "signal_inference", signal["signal_id"]):
        errors.append("UniverseSelection.output_refs must point to SignalInference.signal_id")
    if signal["universe_ref"]["universe_id"] != universe["universe_id"]:
        errors.append("SignalInference.universe_ref.universe_id must match UniverseSelection.universe_id")
    if signal.get("regime_ref", {}).get("regime_id") != regime["regime_id"]:
        errors.append("SignalInference.regime_ref.regime_id must match RegimeState.regime_id")
    if not contains_ref(signal["output_refs"], "allocation_decision", allocation["allocation_id"]):
        errors.append("SignalInference.output_refs must point to AllocationDecision.allocation_id")
    if allocation["signal_ref"]["signal_id"] != signal["signal_id"]:
        errors.append("AllocationDecision.signal_ref.signal_id must match SignalInference.signal_id")
    if allocation.get("regime_ref", {}).get("regime_id") != regime["regime_id"]:
        errors.append("AllocationDecision.regime_ref.regime_id must match RegimeState.regime_id")
    if not contains_ref(allocation["output_refs"], "risk_adjudication", risk["adjudication_id"]):
        errors.append("AllocationDecision.output_refs must point to RiskAdjudication.adjudication_id")
    if risk["allocation_ref"]["allocation_id"] != allocation["allocation_id"]:
        errors.append("RiskAdjudication.allocation_ref.allocation_id must match AllocationDecision.allocation_id")
    if not contains_ref(risk["output_refs"], "approval_decision", "approval-20260413-001"):
        errors.append("RiskAdjudication.output_refs must link to approval-20260413-001")

    return errors, chain_entries


def main() -> int:
    schemas: dict[str, dict] = {}
    individual_examples: dict[str, dict] = {}
    all_ok = True

    print("=== Schema Validation ===")
    for stage in STAGES:
        schema_path = SCHEMA_DIR / stage["schema"]
        schema = load_json(schema_path)
        schemas[stage["schema"]] = schema
        schema_errors = validate_schema(stage["schema"], schema)
        if schema_errors:
            all_ok = False
            print(f"FAIL {stage['schema']}")
            for error in schema_errors:
                print(f"  - {error}")
        else:
            print(f"PASS {stage['schema']}")

    print("\n=== Single-Stage Example Validation ===")
    for stage in STAGES:
        example_path = EXAMPLE_DIR / stage["example"]
        example = load_json(example_path)
        individual_examples[stage["name"]] = example
        example_errors = validate_instance(schemas[stage["schema"]], example)
        if example_errors:
            all_ok = False
            print(f"FAIL {stage['example']}")
            for error in example_errors:
                print(f"  - {error}")
        else:
            print(f"PASS {stage['example']}")

    print("\n=== Five-Stage Chain Validation ===")
    chain_doc = load_json(CHAIN_FILE)
    chain_errors, _ = validate_chain(chain_doc, schemas, individual_examples)
    if chain_errors:
        all_ok = False
        print(f"FAIL {CHAIN_FILE.name}")
        for error in chain_errors:
            print(f"  - {error}")
    else:
        print(f"PASS {CHAIN_FILE.name}")

    print("\n=== Summary ===")
    if all_ok:
        print("ALL SCHEMAS AND EXAMPLES VALID")
        return 0

    print("DECISION-DOMAIN VALIDATION FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
