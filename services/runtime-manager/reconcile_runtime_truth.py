#!/usr/bin/env python3
"""Build an auditable runtime-truth report from an authoritative snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from runtime_truth_reconciler import RuntimeTruthReconciler, build_reconciliation_rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--run-id")
    args = parser.parse_args()

    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    rows = build_reconciliation_rows(snapshot)
    report = RuntimeTruthReconciler(args.audit).reconcile(rows, run_id=args.run_id)
    payload = report.to_dict()
    payload["source_counts"] = {
        key: len(snapshot.get(key) or [])
        for key in (
            "runtime_bindings", "deployment_plans", "persona_capital_bindings",
            "capital_pools", "telemetry_summaries",
        )
    }
    payload["repair_manifest"] = [
        {
            "runtime_id": record["runtime_id"],
            "runtime_binding_id": record["runtime_binding_id"],
            "patch": record["repair_patch"],
            "evidence_refs": record["evidence_refs"],
        }
        for record in report.records
        if record["disposition"] == "repair_proposed"
    ]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
