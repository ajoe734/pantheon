#!/usr/bin/env python3
"""
normalize_handoff.py — Reference normalization script for OpenClaw workflow outputs.

Reads a raw OpenClaw workflow handoff payload and produces:
  1. A canonical StrategySpec (validated against strategy_spec.schema.json)
  2. A canonical WorkflowHandoff envelope (validated against workflow_handoff.schema.json)

Usage:
    python3 normalize_handoff.py <raw_handoff.json> <output_dir>

Output:
    <output_dir>/strategy_spec.json
    <output_dir>/workflow_handoff.json

Exit codes:
    0 — success, both artifacts written and schema-valid
    1 — normalization or validation failure
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from jsonschema import Draft7Validator, RefResolver, ValidationError
except ImportError:
    print("ERROR: jsonschema is required. Install with: pip install jsonschema", file=sys.stderr)
    sys.exit(1)


SCHEMA_DIR = Path(__file__).parent


def load_schema(name: str) -> dict:
    path = SCHEMA_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Schema not found: {path}")
    with open(path) as f:
        return json.load(f)


def build_strategy_spec(handoff: dict, now: str) -> dict:
    meta = handoff.get("metadata", {})
    result = handoff.get("result", {})
    return {
        "spec_version": "1.0",
        "strategy_id": f"strat-{meta.get('topic', 'research')}",
        "title": meta.get("topic", "Research Strategy"),
        "hypothesis": str(result.get("summary", "")),
        "objective": "Evaluate and replicate the research finding under governed promotion gates.",
        "market_scope": {
            "symbols": ["RESEARCH_UNIVERSE"],
            "frequency": "1d",
        },
        "data_dependencies": [
            {
                "ref": meta.get("source_id", "unknown"),
                "kind": "note",
            }
        ],
        "execution_profile": {
            "signal_schema_version": "1.0",
            "quantity_type": "PERCENT_PORTFOLIO",
        },
        "evaluation_plan": {
            "metrics": ["sharpe_ratio", "max_drawdown", "win_rate"],
        },
        "governance": {
            "approval_required": True,
        },
        "provenance": {
            "source_kind": "workflow",
            "created_at": now,
            "source_refs": [meta.get("source_id", "unknown")],
            "created_by": "openclaw-smoke-test",
        },
    }


def build_workflow_handoff(strategy_spec: dict, handoff: dict, now: str) -> dict:
    meta = handoff.get("metadata", {})
    return {
        "handoff_version": "1.0",
        "handoff_id": f"handoff-{strategy_spec['strategy_id']}",
        "handoff_type": "strategy_spec",
        "from_stage": "research_ingest",
        "to_stage": "replication_gate",
        "created_at": now,
        "strategy_spec": strategy_spec,
        "registry_hints": {
            "artifact_type": "strategy_spec",
            "initial_lifecycle_state": "draft",
            "lineage_ref": meta.get("source_id", "unknown"),
        },
        "governance_context": {
            "approval_required": True,
            "execution_context": "research",
        },
        "provenance": {
            "created_by": "openclaw-smoke-test",
            "created_at": now,
            "source_task_id": "OSS-001-smoke",
            "source_channel": "research_task",
            "source_persona": "openclaw",
        },
    }


def validate(instance: dict, schema: dict, resolver=None, label: str = "") -> None:
    validator = Draft7Validator(schema, resolver=resolver)
    errors = list(validator.iter_errors(instance))
    if errors:
        for err in errors:
            print(f"FAIL ({label}): {err.message}", file=sys.stderr)
        raise ValidationError(f"{label} validation failed with {len(errors)} error(s)")
    print(f"PASS: {label} validates against schema")


def main() -> int:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <raw_handoff.json> <output_dir>", file=sys.stderr)
        return 1

    raw_path = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])

    if not raw_path.exists():
        print(f"ERROR: input file not found: {raw_path}", file=sys.stderr)
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)

    with open(raw_path) as f:
        handoff = json.load(f)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    spec_schema = load_schema("strategy_spec.schema.json")
    handoff_schema = load_schema("workflow_handoff.schema.json")

    spec_file_uri = SCHEMA_DIR.resolve().as_uri() + "/strategy_spec.schema.json"
    store = {
        "https://pantheon/workflow-handoff/strategy_spec.schema.json": spec_schema,
        spec_file_uri: spec_schema,
    }
    resolver = RefResolver(
        base_uri=SCHEMA_DIR.resolve().as_uri() + "/",
        referrer=handoff_schema,
        store=store,
    )

    strategy_spec = build_strategy_spec(handoff, now)
    validate(strategy_spec, spec_schema, label="StrategySpec")

    workflow_handoff = build_workflow_handoff(strategy_spec, handoff, now)
    validate(workflow_handoff, handoff_schema, resolver=resolver, label="WorkflowHandoff")

    spec_out = out_dir / "strategy_spec.json"
    handoff_out = out_dir / "workflow_handoff.json"

    with open(spec_out, "w") as f:
        json.dump(strategy_spec, f, indent=2)
    with open(handoff_out, "w") as f:
        json.dump(workflow_handoff, f, indent=2)

    print(f"Written: {spec_out}")
    print(f"Written: {handoff_out}")
    print("ALL VALIDATIONS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
