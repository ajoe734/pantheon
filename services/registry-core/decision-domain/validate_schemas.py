#!/usr/bin/env python3
"""Validate all 5 decision-domain schemas against JSON Schema draft-07 and verify common fields."""
import json
import sys
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parent
SCHEMAS = [
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
    "model_ref",
}


def validate_draft07(schema: dict) -> list[str]:
    """Basic structural checks for JSON Schema draft-07 compliance."""
    errors = []
    if "$schema" not in schema:
        errors.append("Missing $schema declaration")
    elif schema["$schema"] != "http://json-schema.org/draft-07/schema#":
        errors.append(f"Expected draft-07, got: {schema['$schema']}")
    if "type" not in schema:
        errors.append("Missing top-level 'type'")
    elif schema["type"] != "object":
        errors.append(f"Expected type 'object', got: {schema['type']}")
    if "properties" not in schema:
        errors.append("Missing 'properties'")
    if "required" not in schema:
        errors.append("Missing 'required'")
    return errors


def check_common_fields(schema: dict, name: str) -> dict[str, str]:
    """Check which common fields are present and whether they're required."""
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    results = {}
    for field in COMMON_FIELDS:
        if field in properties:
            status = "present"
            if field in required:
                status += " (required)"
            results[field] = status
        else:
            # Check if it appears in anyOf/allOf conditional branches
            found_in_condition = False
            for keyword in ("allOf", "anyOf", "oneOf"):
                for condition in schema.get(keyword, []):
                    if "then" in condition:
                        then_req = condition["then"].get("required", [])
                        if field in then_req:
                            found_in_condition = True
                            break
                    if "required" in condition and field in condition["required"]:
                        found_in_condition = True
                        break
            if found_in_condition:
                results[field] = "present (conditional)"
            else:
                results[field] = "MISSING"
    return results


def main():
    all_ok = True
    for schema_file in SCHEMAS:
        path = SCHEMA_DIR / schema_file
        print(f"\n{'='*60}")
        print(f"Validating: {schema_file}")
        print(f"{'='*60}")

        if not path.exists():
            print(f"  ERROR: File not found: {path}")
            all_ok = False
            continue

        with open(path, "r") as f:
            schema = json.load(f)

        # Draft-07 validation
        draft_errors = validate_draft07(schema)
        if draft_errors:
            print(f"  Draft-07 validation FAILED:")
            for err in draft_errors:
                print(f"    - {err}")
            all_ok = False
        else:
            print(f"  Draft-07 validation: PASS")

        # Common fields check
        field_results = check_common_fields(schema, schema_file)
        print(f"  Common fields:")
        for field, status in sorted(field_results.items()):
            marker = "✓" if "MISSING" not in status else "✗"
            print(f"    {marker} {field}: {status}")
            if "MISSING" in status:
                all_ok = False

        # Title and description
        title = schema.get("title", "N/A")
        desc = schema.get("description", "N/A")[:100]
        print(f"  Title: {title}")
        print(f"  Description: {desc}...")

    print(f"\n{'='*60}")
    if all_ok:
        print("ALL SCHEMAS VALID")
        sys.exit(0)
    else:
        print("SOME SCHEMAS FAILED VALIDATION")
        sys.exit(1)


if __name__ == "__main__":
    main()
