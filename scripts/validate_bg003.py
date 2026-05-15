#!/usr/bin/env python3
"""Validate BG-003 acceptance criteria for decision-domain schemas."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCHEMA_DIR = Path("services/registry-core/decision-domain")
README_PATH = SCHEMA_DIR / "README.md"
VALIDATOR_PATH = SCHEMA_DIR / "validate_schemas.py"
SCHEMA_FILES = [
    "regime_state.schema.json",
    "universe_selection.schema.json",
    "signal_inference.schema.json",
    "allocation_decision.schema.json",
    "risk_adjudication.schema.json",
]
COMMON_FIELDS = {
    "strategy_id",
    "artifact_id",
    "version",
    "evaluated_at",
    "input_refs",
    "output_refs",
    "decision_reasoning",
}
README_EXPECTED_TERMS = [
    "regime_class",
    "selected_universe",
    "signal_ref",
    "allocation_ref",
    "risk_assessment",
    "verdict",
]
README_LEGACY_TERMS = [
    "regime_classification",
    "selected_instruments",
    "signal_inference_id",
    "allocation_decision_id",
    "risk_adjudication_id",
    "risk_outcome",
]


def load_schema(name: str) -> dict | None:
    path = SCHEMA_DIR / name
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_draft07(schema: dict) -> bool:
    return schema.get("$schema") == "http://json-schema.org/draft-07/schema#"


def check_common_fields(schema: dict) -> list[str]:
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    missing = [field for field in COMMON_FIELDS if field not in props]
    missing_required = [field for field in COMMON_FIELDS if field not in required]
    if "model_ref" not in props:
        missing.append("model_ref")
    return sorted(set(missing + [f"{field} (required)" for field in missing_required]))


def check_bg000_vocab(schema: dict) -> bool:
    text = json.dumps(schema)
    hints = ["market_scope", "asset_class", "asset_classes", "market_class", "instrument_type", "markets"]
    return any(hint in text for hint in hints)


def check_bg001_refs(schema: dict) -> bool:
    text = json.dumps(schema)
    hints = ["security_master", "contract_master", "dataset_version", "SecurityMaster", "ContractMaster"]
    return any(hint in text for hint in hints)


def check_output_refs_links_to_approval(schema: dict) -> bool:
    output_refs = schema.get("properties", {}).get("output_refs", {})
    items = output_refs.get("items", {})
    props = items.get("properties", {})
    ref_type_enum = props.get("ref_type", {}).get("enum", [])
    return "approval_decision" in ref_type_enum


def check_research_vs_provenance(schema: dict) -> bool:
    text = json.dumps(schema).lower()
    return "provenance" in text or "research" in text or "model_ref" in schema.get("properties", {})


def check_readme_alignment(readme_text: str) -> tuple[list[str], list[str]]:
    missing = [term for term in README_EXPECTED_TERMS if term not in readme_text]
    legacy = [term for term in README_LEGACY_TERMS if term in readme_text]
    return missing, legacy


def main() -> int:
    results: dict[str, str] = {}
    schemas: dict[str, dict] = {}

    print("=== Criteria 1-5: Schema existence and draft-07 validation ===")
    all_exist = True
    for name in SCHEMA_FILES:
        schema = load_schema(name)
        if schema is None:
            print(f"  FAIL: {name} — file not found")
            results[f"schema_exists_{name}"] = "FAIL: file not found"
            all_exist = False
            continue
        if not validate_draft07(schema):
            print(f"  FAIL: {name} — $schema is not draft-07")
            results[f"schema_exists_{name}"] = "FAIL: not draft-07"
            all_exist = False
            continue
        schemas[name] = schema
        print(f"  PASS: {name} — exists, draft-07")
        results[f"schema_exists_{name}"] = "PASS"
    results["criteria_1_5"] = "PASS" if all_exist else "FAIL"

    print("\n=== Criterion 6: Common fields present ===")
    all_common = True
    for name, schema in schemas.items():
        missing = check_common_fields(schema)
        if missing:
            print(f"  FAIL: {name} — missing {missing}")
            results[f"common_fields_{name}"] = f"FAIL: missing {missing}"
            all_common = False
        else:
            print(f"  PASS: {name} — all common fields present")
            results[f"common_fields_{name}"] = "PASS"
    results["criterion_6"] = "PASS" if all_common else "FAIL"

    print("\n=== Criterion 7: Real example chain validates end-to-end ===")
    validator = subprocess.run(
        [sys.executable, str(VALIDATOR_PATH)],
        capture_output=True,
        text=True,
        check=False,
    )
    if validator.returncode == 0:
        print("  PASS: validate_schemas.py confirms schemas, single-stage examples, and five-stage chain all validate")
        results["criterion_7"] = "PASS"
    else:
        print("  FAIL: validate_schemas.py reported validation failures")
        if validator.stdout.strip():
            print(validator.stdout.rstrip())
        if validator.stderr.strip():
            print(validator.stderr.rstrip())
        results["criterion_7"] = "FAIL"

    print("\n=== Criterion 8: Chain links to ApprovalDecision downstream ===")
    risk_schema = schemas.get("risk_adjudication.schema.json")
    if risk_schema and check_output_refs_links_to_approval(risk_schema):
        print("  PASS: RiskAdjudication output_refs includes approval_decision")
        results["criterion_8"] = "PASS"
    else:
        print("  FAIL: RiskAdjudication output_refs does not include approval_decision")
        results["criterion_8"] = "FAIL"

    print("\n=== Criterion 9: BG-000 vocabulary consumed ===")
    bg000_ok = True
    for name, schema in schemas.items():
        if check_bg000_vocab(schema):
            print(f"  PASS: {name} references BG-000 vocabulary")
        else:
            print(f"  FAIL: {name} does not reference BG-000 vocabulary")
            bg000_ok = False
    results["criterion_9"] = "PASS" if bg000_ok else "FAIL"

    print("\n=== Criterion 10: BG-001 object refs used ===")
    bg001_ok = True
    for name, schema in schemas.items():
        if check_bg001_refs(schema):
            print(f"  PASS: {name} references BG-001 objects")
        else:
            print(f"  FAIL: {name} does not reference BG-001 objects")
            bg001_ok = False
    results["criterion_10"] = "PASS" if bg001_ok else "FAIL"

    print("\n=== Criterion 11: Research vs provenance documented ===")
    provenance_ok = True
    for name, schema in schemas.items():
        if check_research_vs_provenance(schema):
            print(f"  PASS: {name} distinguishes research from provenance")
        else:
            print(f"  FAIL: {name} does not distinguish research from provenance")
            provenance_ok = False
    results["criterion_11"] = "PASS" if provenance_ok else "FAIL"

    print("\n=== Criterion 12: README matches the canonical schema keys ===")
    if not README_PATH.exists():
        print("  FAIL: README.md not found")
        results["criterion_12"] = "FAIL"
    else:
        readme_text = README_PATH.read_text(encoding="utf-8")
        missing_terms, legacy_terms = check_readme_alignment(readme_text)
        if missing_terms or legacy_terms:
            print(
                f"  FAIL: README alignment mismatch; missing={missing_terms or '[]'}, legacy={legacy_terms or '[]'}"
            )
            results["criterion_12"] = "FAIL"
        else:
            print("  PASS: README uses canonical field names and omits the legacy contract keys")
            results["criterion_12"] = "PASS"

    print("\n" + "=" * 60)
    print("BG-003 VALIDATION SUMMARY")
    print("=" * 60)
    fail_count = 0
    pass_count = 0
    for key, value in results.items():
        if value == "PASS":
            marker = "PASS"
            pass_count += 1
        else:
            marker = "FAIL"
            fail_count += 1
        print(f"  {marker} {key}: {value}")

    print(f"\nTotal: {pass_count} PASS, {fail_count} FAIL")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
