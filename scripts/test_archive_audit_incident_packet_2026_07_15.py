#!/usr/bin/env python3
"""Regression checks for the no-repair immutable-audit incident contract."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "docs/bff/execution-tasks/2026-07-15-immutable-audit-archive-incident/fixtures/archive-audit-archive-incident.v1.json"


def digest(contract: dict[str, object]) -> str:
    raw = json.dumps(contract, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def main() -> int:
    fixture = json.loads(FIXTURE.read_text())
    contract = fixture["contract"]
    assert fixture["contract_sha256"] == digest(contract)
    assert contract["task"]["id"] == "LOOP-PROD-AUDIT-ARCHIVE-INCIDENT-001"
    source = contract["source_observation"]
    assert source["gzip_sha256"] == "229007353bfe5f521c8a114a6b3dd9582442398697bbf30022fb49839bb5b6dc"
    assert source["line"] == {
        "number": 8004,
        "bytes_with_newline": 1699,
        "newline_terminated": True,
        "sha256": "735fa860c852761e9c43a170432ef4458cf0d0559f0535a3246abfaf0fdc2ae9",
        "json_valid": True,
    }
    assert source["parser_expected_result"] == {
        "gzip_integrity": "valid",
        "jsonl_result": "valid",
        "jsonl_line_count": 10650,
        "error_line": None,
        "error_column": None,
    }
    boundary = contract["admission_boundary"]
    assert boundary["scratch_status_root_only"] is True
    assert boundary["production_status_root_forbidden"] is True
    assert boundary["normal_ai_status_prohibited"] is True
    assert boundary["normal_outbox_recovery_prohibited"] is True
    assert boundary["two_distinct_approvals"] == {
        "required": True,
        "roles": ["Human/Ops", "independent_runtime_reviewer"],
        "self_approval_forbidden": True,
    }
    assert boundary["supervisor_verification"]["required"] is True
    assert contract["repair_authorization"]["authorized"] is False
    assert "bootstrap_handoff_replay" in contract["repair_authorization"]["prohibited_actions"]
    print("archive audit incident packet: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
