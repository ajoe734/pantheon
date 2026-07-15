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
    assert fixture["contract_sha256"] == "6059a2cd651fedeb9d6582e7d165e81dbaf8cc04e2069e8d16614d65c9a66f50"
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
    assert source["active_log"] == {
        "path": "ai-activity-log.jsonl",
        "observation_mode": "volatile_read_only_parse_gate",
        "exact_sha256_binding": "forbidden",
        "exact_line_count_binding": "forbidden",
        "replay_semantics": "At each read-only observation, take a bounded open snapshot and strictly parse it. A valid result creates a timestamped observation receipt only; normal append changes neither this contract digest nor repair authority. An invalid result fails closed and requires a separately pinned incident.",
        "parser_expected_result": {
            "jsonl_result": "valid",
            "error_line": None,
            "error_column": None,
        },
    }
    boundary = contract["admission_boundary"]
    assert boundary == {
        "mode": "read_only_external_bootstrap_observation_only",
        "scratch_status_root_only": True,
        "production_status_root_forbidden": True,
        "normal_ai_status_prohibited": True,
        "normal_outbox_recovery_prohibited": True,
        "two_distinct_approvals": {
            "required": True,
            "roles": ["Human/Ops", "independent_runtime_reviewer"],
            "self_approval_forbidden": True,
        },
        "supervisor_verification": {
            "required": True,
            "must_verify": [
                "contract_sha256",
                "source_observation",
                "distinct_approval_identities",
                "clean_worktree",
                "scratch_status_root",
                "zero_production_write_paths",
                "single_run_id",
                "expiry",
            ],
        },
    }
    authorization = contract["repair_authorization"]
    assert authorization["authorized"] is False
    assert authorization["prohibited_actions"] == [
        "archive_repair",
        "archive_quarantine",
        "archive_replacement",
        "archive_recompression",
        "active_audit_mutation",
        "outbox_recovery",
        "outbox_clear",
        "outbox_mutation",
        "bootstrap_handoff_replay",
        "task_or_dependency_mutation",
        "status_state_mutation",
    ]
    non_goals = " ".join(contract["task"]["non_goals"]).lower()
    for surface in ("archive", "outbox", "handoff", "ai-status.json", "active audit", "runtime", "deployment"):
        assert surface in non_goals, surface
    print("archive audit incident packet: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
