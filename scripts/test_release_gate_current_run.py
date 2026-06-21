from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


def _sha256_12(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _workflow_step(workflow_path: Path, job_id: str, step_name: str) -> dict:
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    steps = workflow["jobs"][job_id]["steps"]
    return next(step for step in steps if step.get("name") == step_name)


def _upload_artifact_paths(step: dict) -> list[str]:
    raw_path = step.get("with", {}).get("path")
    assert isinstance(raw_path, str)
    return [line.strip() for line in raw_path.splitlines() if line.strip()]


def _workflow_dispatch_inputs(workflow_path: Path) -> dict:
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    on_config = workflow.get("on") or workflow.get(True)
    return on_config["workflow_dispatch"]["inputs"]


def _assert_current_run_is_only_uploaded_audit_path(paths: list[str]) -> None:
    audit_paths = [path for path in paths if path.startswith(".lovable/audits")]
    assert audit_paths == [".lovable/audits/current-run"]
    assert all("*" not in path for path in audit_paths)
    assert all("historical" not in path for path in audit_paths)


def _strict_dry_run_items(*, with_side_effect_checks: bool = True, mismatched_readback_target: bool = False):
    strategy_id = "strategy-dry-run-001"
    ranking_formula_id = "ranking-formula-dry-run-001"
    items = [
        {"family": "dry-run-strategy-create", "ok": True, "extracted": {"data.id": strategy_id}},
        {"family": "dry-run-strategy-create-readback-not-persisted", "ok": True, "error_envelope": True},
        {"family": "dry-run-ranking-formula-create", "ok": True, "extracted": {"data.id": ranking_formula_id}},
        {"family": "dry-run-ranking-formula-create-readback-not-persisted", "ok": True, "error_envelope": True},
        {"family": "dry-run-v5-intervention-claim", "ok": True},
        {"family": "dry-run-invalid-strategy", "ok": True, "error_envelope": True},
        {"family": "dry-run-invalid-ranking-formula", "ok": True, "error_envelope": True},
    ]
    if not with_side_effect_checks:
        return items

    checks = [
        {
            "kind": "dry_run_preview_meta",
            "ok": True,
            "dryRun": True,
            "durable": False,
            "liveCapitalSideEffects": False,
        },
        {
            "kind": "readback_not_persisted",
            "ok": True,
            "error_code": "RESOURCE_NOT_FOUND",
            "target_family": "dry-run-strategy-create",
            "target_id_sha256_12": _sha256_12("other-strategy-id" if mismatched_readback_target else strategy_id),
        },
        {
            "kind": "dry_run_preview_meta",
            "ok": True,
            "dryRun": True,
            "durable": False,
            "liveCapitalSideEffects": False,
        },
        {
            "kind": "readback_not_persisted",
            "ok": True,
            "error_code": "RESOURCE_NOT_FOUND",
            "target_family": "dry-run-ranking-formula-create",
            "target_id_sha256_12": _sha256_12("other-ranking-formula-id" if mismatched_readback_target else ranking_formula_id),
        },
        {
            "kind": "dry_run_command_meta",
            "ok": True,
            "dryRun": True,
            "durable": False,
            "liveCapitalSideEffects": False,
        },
        {"kind": "validation_rejected_before_persistence", "ok": True, "error_code": "VALIDATION_FAILED"},
        {"kind": "validation_rejected_before_persistence", "ok": True, "error_code": "VALIDATION_FAILED"},
    ]
    for item, check in zip(items, checks):
        item["side_effect_check"] = check
    return items


def _strict_sse_reconnect_bearer(
    *,
    attempt_count: int = 5,
    detail_ok: bool = True,
    duplicate_observed: bool = False,
    lineage_ok: bool = True,
    event_type_link_ok: bool = True,
):
    expected_event_ids = [f"evt-{index}" for index in range(2, 2 + attempt_count)]
    cursor_event_ids = [f"evt-{index}" for index in range(1, 1 + attempt_count)]
    observed_event_ids = list(expected_event_ids)
    if duplicate_observed and len(observed_event_ids) >= 2:
        observed_event_ids[-1] = observed_event_ids[0]
    attempts = []
    for index in range(attempt_count):
        ok = detail_ok or index != attempt_count - 1
        attempts.append(
            {
                "mode": "bearer_polyfill",
                "ok": ok,
                "attempt": index + 1,
                "url_path": "/bff/events/stream?channel=approval",
                "cursor_event_id": f"evt-{index + 1}",
                "expected_replayed_event_id": expected_event_ids[index],
                "observed_replayed_event_id": observed_event_ids[index],
                "replayed_expected_event": ok and observed_event_ids[index] == expected_event_ids[index],
                "request_headers": {
                    "Last-Event-ID": (
                        "wrong-cursor"
                        if not lineage_ok and index == attempt_count - 1
                        else cursor_event_ids[index]
                    ),
                },
                "response_headers": {
                    "X-SSE-Channel": "approval",
                    "X-SSE-Replay-Supported": "true",
                },
                "first_event": {
                    "id": observed_event_ids[index],
                    "event": (
                        "approval.decided"
                        if event_type_link_ok or index != attempt_count - 1
                        else "approval.mismatched"
                    ),
                    "data": {
                        "id": observed_event_ids[index],
                        "type": "approval.decided",
                        "timestamp": f"2026-06-20T00:00:0{index}Z",
                    },
                    "shape_checks": {
                        "id_line_matches_data_id": True,
                        "event_line_matches_data_type": True,
                        "data_json_parse_ok": True,
                    },
                },
            }
        )
    return {
        "ok": True,
        "attempt_count": attempt_count,
        "cursors_advanced": True,
        "duplicate_event_ids": [],
        "missing_expected_event_ids": [],
        "cursor_event_ids": cursor_event_ids,
        "expected_event_ids": expected_event_ids,
        "observed_event_ids": observed_event_ids,
        "attempts": attempts,
    }


def _strict_rbac_matrix_items(
    *,
    with_write_side_effect_checks: bool = True,
    mismatched_write_marker_link: bool = False,
    mismatched_detail_link: bool = False,
):
    labels = ["anonymous", "viewer", "operator", "reviewer", "approver", "admin", "empty", "unknown"]
    read_paths = {
        "bff-strategies": "/bff/strategies",
        "bff-ranking-formulas": "/bff/ranking-formulas",
        "bff-agora-signals": "/bff/agora/signals",
    }
    write_paths = {
        "strategy": "/bff/strategies",
        "ranking-formula": "/bff/ranking-formulas",
        "agora-note": "/bff/agora/notes",
        "intervention-claim": "/bff/v5/interventions/int-live-rbac-matrix/claim",
    }
    write_allowed = {"operator", "reviewer", "approver", "admin"}
    items = []
    for label in labels:
        for name, path in read_paths.items():
            item = {
                "family": f"rbac-read-{label}-{name}",
                "method": "GET",
                "path": path,
                "ok": True,
                "rbac_label": label,
                "rbac_operation": "read",
                "rbac_resource": name,
                "auth_case_kind": "anonymous" if label == "anonymous" else "provided_bearer",
            }
            if label != "anonymous":
                item["request_bearer_sha256_12"] = f"rbac-{label}-hash"
            if mismatched_detail_link and label == "viewer" and name == "bff-strategies":
                item["path"] = "/bff/personas"
            items.append(item)
    for label in labels:
        for name, path in write_paths.items():
            marker_hash = _sha256_12(f"rbac-{label}-{name}-marker")
            target_marker_hash = (
                _sha256_12(f"other-rbac-{label}-{name}-marker")
                if mismatched_write_marker_link and label == "operator" and name == "strategy"
                else marker_hash
            )
            item = {
                "family": f"rbac-write-{label}-{name}",
                "method": "POST",
                "path": path,
                "ok": True,
                "error_envelope": label not in write_allowed,
                "request_marker_sha256_12": marker_hash,
                "rbac_label": label,
                "rbac_operation": "write",
                "rbac_resource": name,
                "auth_case_kind": "anonymous" if label == "anonymous" else "provided_bearer",
            }
            if label != "anonymous":
                item["request_bearer_sha256_12"] = f"rbac-{label}-hash"
            if with_write_side_effect_checks:
                if label in write_allowed:
                    item["side_effect_check"] = {
                        "kind": "rbac_dry_run_write_meta",
                        "ok": True,
                        "dryRun": True,
                        "durable": False,
                        "liveCapitalSideEffects": False,
                        "target_marker_sha256_12": target_marker_hash,
                    }
                else:
                    item["side_effect_check"] = {
                        "kind": "authorization_rejected_before_persistence",
                        "ok": True,
                        "error_code": "FORBIDDEN",
                        "target_marker_sha256_12": target_marker_hash,
                    }
            items.append(item)
    return items



def _provided_bearer_pair_source(*, distinct: bool = True):
    return {
        "kind": "provided_bearer_pair",
        "token_a_sha256_12": "race-token-a" if distinct else "same-race-token",
        "token_b_sha256_12": "race-token-b" if distinct else "same-race-token",
    }


def _strict_approval_race_item(
    *,
    accepted_count: int = 1,
    safe_error_count: int = 1,
    bounded: bool = True,
    distinct_token_pair: bool = True,
    safe_error_envelope: bool = True,
    mismatched_target_link: bool = False,
):
    target_id = "approval-race-target-001"
    target_hash = _sha256_12(target_id)
    path = f"/bff/approvals/{target_id}/decide"
    results = [
        {
            "family": f"approval-race-winner-{index}",
            "method": "POST",
            "path": path,
            "target_id_sha256_12": target_hash,
            "status": 202,
            "ok": True,
            "error_envelope": False,
            "error_code": None,
        }
        for index in range(accepted_count)
    ]
    results.extend(
        {
            "family": f"approval-race-loser-{index}",
            "method": "POST",
            "path": path,
            "target_id_sha256_12": (
                _sha256_12("other-approval-race-target")
                if mismatched_target_link and index == 0
                else target_hash
            ),
            "status": 409,
            "ok": safe_error_envelope,
            "error_envelope": safe_error_envelope,
            "error_code": "STATE_CONFLICT" if safe_error_envelope else None,
        }
        for index in range(safe_error_count)
    )
    return {
        "family": "approval-race",
        "method": "POST",
        "path": path,
        "target_id": target_id,
        "target_id_sha256_12": target_hash,
        "ok": bounded,
        "bounded": bounded,
        "accepted_count": accepted_count,
        "safe_error_count": safe_error_count,
        "duplicate_winners": accepted_count > 1,
        "token_source": _provided_bearer_pair_source(distinct=distinct_token_pair),
        "results": results,
    }


def _strict_two_man_race_item(
    *,
    operator_scoped: bool = True,
    distinct_token_pair: bool = True,
    detail_replayed: bool = False,
    detail_duplicate_command_ids: bool = False,
    mismatched_target_link: bool = False,
    duplicate_signature_link: bool = False,
):
    target_id = "two-man-target-001"
    target_hash = _sha256_12(target_id)
    path = f"/bff/v5/interventions/{target_id}/two-man-sign"
    accepted_count = 2 if operator_scoped else 1
    command_ids = ["cmd-two-man-a", "cmd-two-man-b"]
    if detail_duplicate_command_ids:
        command_ids = ["cmd-two-man-same", "cmd-two-man-same"]
    signature_hashes = [_sha256_12(f"sig-{target_id}-{index}") for index in range(2)]
    if duplicate_signature_link:
        signature_hashes = [signature_hashes[0], signature_hashes[0]]
    results = [
        {
            "family": f"two-man-race-{index}",
            "method": "POST",
            "path": path,
            "target_id_sha256_12": (
                _sha256_12("other-two-man-target")
                if mismatched_target_link and index == accepted_count - 1
                else target_hash
            ),
            "request_signature_id_sha256_12": signature_hashes[index],
            "status": 202,
            "ok": not detail_replayed,
            "extracted": {
                "data.command_id": command_ids[index],
                "meta.idempotency.replayed": detail_replayed,
            },
        }
        for index in range(accepted_count)
    ]
    return {
        "family": "two-man-race",
        "method": "POST",
        "path": path,
        "target_id": target_id,
        "target_id_sha256_12": target_hash,
        "ok": operator_scoped,
        "operator_scoped": operator_scoped,
        "accepted_count": accepted_count,
        "replayed_count": 0,
        "distinct_command_ids": operator_scoped,
        "command_id_count": 2 if operator_scoped else 1,
        "token_source": _provided_bearer_pair_source(distinct=distinct_token_pair),
        "results": results,
    }


def test_release_gate_aggregate_defaults_to_current_run_audit_dir(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "execute-plans" / "scripts" / "aggregate-release-gate.mjs"
    current_run = tmp_path / ".lovable" / "audits" / "current-run"
    historical = tmp_path / ".lovable" / "audits" / "historical"
    current_run.mkdir(parents=True)
    historical.mkdir(parents=True)
    (current_run / "current-evidence.md").write_text("current run evidence\n", encoding="utf-8")
    (historical / "old-evidence.md").write_text("stale evidence that must not be counted\n", encoding="utf-8")

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PANTHEON_AUDIT_OUT_DIR",
            "PANTHEON_RELEASE_GATE_CHECKLIST_OUT",
            "PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE",
        }
    }
    env.update(
        {
            "PANTHEON_FRONTEND_SHA": "f" * 40,
            "PANTHEON_BFF_SHA": "b" * 40,
            "PANTHEON_BFF_BASE_URL": "https://bff.example.test",
        }
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode in {0, 1}, result.stderr

    summary_path = current_run / "release-gate-summary.json"
    markdown_path = current_run / "release-gate-summary.md"
    assert summary_path.exists(), result.stdout + result.stderr
    assert markdown_path.exists(), result.stdout + result.stderr
    assert not (tmp_path / ".lovable" / "audits" / "release-gate-summary.json").exists()

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["auditDir"] == ".lovable/audits/current-run"
    evidence_check = next(
        check
        for check in summary["gates"]["7"]
        if check["label"].startswith("Evidence written to")
    )
    assert evidence_check["label"] == "Evidence written to `.lovable/audits/current-run`."
    assert evidence_check["status"] == "pass"
    assert evidence_check["note"] == "3 audit file(s) found"


def test_release_gate_counts_generated_summary_files_as_current_run_evidence(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "execute-plans" / "scripts" / "aggregate-release-gate.mjs"
    current_run = tmp_path / ".lovable" / "audits" / "current-run"
    current_run.mkdir(parents=True)

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PANTHEON_AUDIT_OUT_DIR",
            "PANTHEON_RELEASE_GATE_CHECKLIST_OUT",
            "PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE",
        }
    }
    env.update(
        {
            "PANTHEON_FRONTEND_SHA": "f" * 40,
            "PANTHEON_BFF_SHA": "b" * 40,
            "PANTHEON_BFF_BASE_URL": "https://bff.example.test",
        }
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode in {0, 1}, result.stderr

    summary = json.loads((current_run / "release-gate-summary.json").read_text(encoding="utf-8"))
    evidence_check = next(
        check
        for check in summary["gates"]["7"]
        if check["label"].startswith("Evidence written to")
    )
    assert evidence_check["status"] == "pass"
    assert evidence_check["note"] == "2 audit file(s) found"


def test_release_gate_ignores_step_outcome_evidence_outside_current_run(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "execute-plans" / "scripts" / "aggregate-release-gate.mjs"
    current_run = tmp_path / ".lovable" / "audits" / "current-run"
    historical = tmp_path / ".lovable" / "audits" / "historical"
    current_run.mkdir(parents=True)
    historical.mkdir(parents=True)

    rbac_labels = ["viewer", "operator", "reviewer", "approver", "admin", "empty", "unknown"]
    rbac_cases = {"anonymous": {"kind": "anonymous"}}
    rbac_cases.update({label: {"kind": "provided_bearer", "sha256_12": f"rbac-{label}-hash"} for label in rbac_labels})
    (historical / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-LUV-AUTHED-LIVE-001",
                "strict_live_evidence": True,
                "auth_source": {"kind": "provided_bearer"},
                "rbac_auth_source": {
                    "kind": "rbac_matrix",
                    "cases": rbac_cases,
                    "provided_bearer_count": 7,
                    "distinct_provided_bearer_count": 7,
                    "distinct_provided_bearers": True,
                    "duplicate_bearer_label_groups": [],
                },
                "include_writes": True,
                "include_rbac_matrix": True,
                "include_dry_run": True,
                "include_approval_race": True,
                "include_two_man_race": True,
                "summary": {
                    "total": 65,
                    "passed": 65,
                    "failed": 0,
                    "rbac_matrix_probes": 56,
                    "rbac_write_probes": 32,
                    "rbac_write_side_effect_proofs": 32,
                    "dry_run_probes": 7,
                    "approval_race_probes": 1,
                    "approval_race_bounded": True,
                    "two_man_race_probes": 1,
                    "two_man_race_operator_scoped": True,
                    "live_capital_side_effects": False,
                },
                "rbac_matrix": _strict_rbac_matrix_items(),
                "dry_run": _strict_dry_run_items(),
                "approval_race": _strict_approval_race_item(),
                "two_man_race": _strict_two_man_race_item(),
            }
        ),
        encoding="utf-8",
    )
    (historical / "BFF-CONSOL-011-sse-replay-smoke.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-CONSOL-011",
                "channel": "approval",
                "strict_live_evidence": True,
                "strict_live_evidence_requirements": {"min_reconnect_attempts": 5},
                "summary": {"passed": True},
                "soak": {
                    "enabled": True,
                    "seconds": 75.0,
                    "min_heartbeats": 1,
                    "bearer_polyfill": {
                        "ok": True,
                        "missing_expected_event_ids": [],
                        "blocks": {
                            "heartbeat_count": 2,
                            "duplicate_event_ids": [],
                        },
                    },
                },
                "reconnect_sequence": {
                    "bearer_polyfill": _strict_sse_reconnect_bearer(),
                },
            }
        ),
        encoding="utf-8",
    )
    (current_run / "release-gate-step-outcomes.json").write_text(
        json.dumps(
            {
                "auth_smoke": {
                    "outcome": "success",
                    "evidence": ".lovable/audits/historical/BFF-LUV-AUTHED-LIVE-001-live-smoke.json",
                },
                "sse_smoke": {
                    "outcome": "success",
                    "evidence": ".lovable/audits/historical/BFF-CONSOL-011-sse-replay-smoke.json",
                },
            }
        ),
        encoding="utf-8",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PANTHEON_AUDIT_OUT_DIR",
            "PANTHEON_RELEASE_GATE_CHECKLIST_OUT",
            "PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE",
        }
    }
    env.update(
        {
            "PANTHEON_FRONTEND_SHA": "f" * 40,
            "PANTHEON_BFF_SHA": "b" * 40,
            "PANTHEON_BFF_BASE_URL": "https://bff.example.test",
        }
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout

    summary = json.loads((current_run / "release-gate-summary.json").read_text(encoding="utf-8"))
    gate3 = summary["gates"]["3"]
    rbac_check = next(
        check
        for check in gate3
        if check["label"] == "Authenticated: strict bearer RBAC matrix evidence passed."
    )
    sse_check = next(
        check
        for check in gate3
        if check["label"] == "Authenticated: strict SSE soak observes heartbeat and no duplicate replay."
    )

    assert rbac_check["status"] == "missing"
    assert rbac_check["note"] == "authenticated strict live evidence outcome: success; JSON evidence missing"
    assert sse_check["status"] == "missing"
    assert sse_check["note"] == "sse smoke outcome: success; JSON evidence missing"
    assert ".lovable/audits/historical" not in json.dumps(summary)


def test_release_gate_surfaces_live_preflight_remediation_when_strict_probes_do_not_run(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "execute-plans" / "scripts" / "aggregate-release-gate.mjs"
    current_run = tmp_path / ".lovable" / "audits" / "current-run"
    current_run.mkdir(parents=True)
    (current_run / "BFF-LIVE-EVIDENCE-PREFLIGHT.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-LIVE-EVIDENCE-PREFLIGHT",
                "strict_live_evidence_preflight": True,
                "github_environment": "dev",
                "target_url": "https://bff.example.test",
                "missing": [
                    "PANTHEON_BFF_SMOKE_BEARER_TOKEN",
                    "PANTHEON_BFF_RBAC_TOKENS_JSON",
                    "PANTHEON_BFF_APPROVAL_RACE_TOKEN_A",
                    "PANTHEON_BFF_APPROVAL_RACE_TOKEN_B",
                    "APPROVAL_RACE_ID",
                    "TWO_MAN_RACE_ID",
                ],
                "invalid": [],
                "operator_remediation": {
                    "github_environment": "dev",
                    "repository": "ajoe734/pantheon",
                    "missing_secret_names": [
                        "PANTHEON_BFF_SMOKE_BEARER_TOKEN",
                        "PANTHEON_BFF_RBAC_TOKENS_JSON",
                        "PANTHEON_BFF_APPROVAL_RACE_TOKEN_A",
                        "PANTHEON_BFF_APPROVAL_RACE_TOKEN_B",
                    ],
                    "missing_workflow_inputs": ["APPROVAL_RACE_ID", "TWO_MAN_RACE_ID"],
                    "secret_set_commands": [
                        "gh secret set PANTHEON_BFF_SMOKE_BEARER_TOKEN --repo ajoe734/pantheon --env dev < /secure/path/PANTHEON_BFF_SMOKE_BEARER_TOKEN.txt",
                        "gh secret set PANTHEON_BFF_RBAC_TOKENS_JSON --repo ajoe734/pantheon --env dev < /secure/path/PANTHEON_BFF_RBAC_TOKENS_JSON.txt",
                        "gh secret set PANTHEON_BFF_APPROVAL_RACE_TOKEN_A --repo ajoe734/pantheon --env dev < /secure/path/PANTHEON_BFF_APPROVAL_RACE_TOKEN_A.txt",
                        "gh secret set PANTHEON_BFF_APPROVAL_RACE_TOKEN_B --repo ajoe734/pantheon --env dev < /secure/path/PANTHEON_BFF_APPROVAL_RACE_TOKEN_B.txt",
                    ],
                    "workflow_dispatch": {
                        "recommended_workflow": "Pantheon Stage 0 CI",
                        "mode": "live-evidence",
                        "environment": "dev",
                    },
                },
                "secret_values_written": False,
            }
        ),
        encoding="utf-8",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PANTHEON_AUDIT_OUT_DIR",
            "PANTHEON_RELEASE_GATE_CHECKLIST_OUT",
            "PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE",
            "PANTHEON_BFF_SMOKE_BEARER_TOKEN",
            "BFF_AUTH_TOKEN",
            "PANTHEON_TEST_OIDC_PATH",
        }
    }
    env.update(
        {
            "PANTHEON_FRONTEND_SHA": "f" * 40,
            "PANTHEON_BFF_SHA": "b" * 40,
            "PANTHEON_BFF_BASE_URL": "https://bff.example.test",
            "PANTHEON_RELEASE_GATE_RUN_URL": "https://github.example/run/1",
        }
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout

    summary = json.loads((current_run / "release-gate-summary.json").read_text(encoding="utf-8"))
    gate0 = summary["gates"]["0"]
    gate3 = summary["gates"]["3"]
    auth_check = next(
        check
        for check in gate0
        if check["label"] == "Auth token or test OIDC path available for authenticated smoke."
    )
    rbac_check = next(
        check
        for check in gate3
        if check["label"] == "Authenticated: strict bearer RBAC matrix evidence passed."
    )
    sse_check = next(
        check
        for check in gate3
        if check["label"] == "Authenticated: strict SSE soak observes heartbeat and no duplicate replay."
    )

    assert auth_check["status"] == "fail"
    assert rbac_check["status"] == "fail"
    assert sse_check["status"] == "fail"
    assert auth_check["evidence"].endswith("BFF-LIVE-EVIDENCE-PREFLIGHT.json")
    assert rbac_check["evidence"].endswith("BFF-LIVE-EVIDENCE-PREFLIGHT.json")
    assert "strict live preflight failed" in auth_check["note"]
    assert "missingSecrets:PANTHEON_BFF_SMOKE_BEARER_TOKEN" in auth_check["note"]
    assert "missingInputs:APPROVAL_RACE_ID,TWO_MAN_RACE_ID" in rbac_check["note"]
    assert "environment:dev" in sse_check["note"]
    assert "workflow:Pantheon Stage 0 CI" in sse_check["note"]
    assert "remediationCommands:4" in sse_check["note"]
    assert "smoke-secret" not in json.dumps(summary)


def test_integration_gate_uploads_only_current_run_audits() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workflow = repo_root / "execute-plans" / ".github" / "workflows" / "pantheon-integration-gate.yml"
    text = workflow.read_text(encoding="utf-8")
    upload_step = _workflow_step(workflow, "integration-gate", "Upload evidence")
    upload_paths = _upload_artifact_paths(upload_step)

    assert "PANTHEON_AUDIT_OUT_DIR: .lovable/audits/current-run" in text
    assert ".lovable/audits/current-run" in text
    assert ".lovable/audits/*.md" not in text
    assert ".lovable/audits/historical" not in text
    assert upload_step["uses"] == "actions/upload-artifact@v4"
    assert upload_paths == [".lovable/audits/current-run", "playwright-report", "test-results"]
    _assert_current_run_is_only_uploaded_audit_path(upload_paths)
    assert "Strict SSE live soak" in text
    assert "scripts/probe_bff_sse_stream.py" in text
    assert "--strict-live-evidence" in text
    assert "--soak-seconds 75" in text
    assert "--reconnect-attempts 5" in text
    assert "${PANTHEON_AUDIT_OUT_DIR}/BFF-CONSOL-011-sse-replay-smoke.json" in text


def test_release_gate_accepts_authenticated_approval_race_evidence(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "execute-plans" / "scripts" / "aggregate-release-gate.mjs"
    current_run = tmp_path / ".lovable" / "audits" / "current-run"
    current_run.mkdir(parents=True)
    (current_run / "bff-authenticated-live-smoke-2026-06-14.md").write_text(
        "\n".join(
            [
                "# Authenticated BFF Live Smoke",
                "",
                "Passed: 1/1",
                "",
                "| Pass | Status | Method | Path | Expectation | ErrorCode |",
                "|---|---:|---|---|---|---|",
                "| ✅ | 202/409 | POST | /bff/approvals/appr-race/decide#race | multi-operator race: <=1 accepted | STATE_CONFLICT |",
                "",
            ]
        ),
        encoding="utf-8",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PANTHEON_AUDIT_OUT_DIR",
            "PANTHEON_RELEASE_GATE_CHECKLIST_OUT",
            "PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE",
        }
    }
    env.update(
        {
            "PANTHEON_FRONTEND_SHA": "f" * 40,
            "PANTHEON_BFF_SHA": "b" * 40,
            "PANTHEON_BFF_BASE_URL": "https://bff.example.test",
        }
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode in {0, 1}, result.stderr

    summary = json.loads(
        (current_run / "release-gate-summary.json").read_text(encoding="utf-8")
    )
    race_check = next(
        check
        for check in summary["gates"]["3"]
        if check["label"] == "Authenticated: multi-operator approval race has no duplicate winner."
    )
    assert race_check["status"] == "pass"
    assert race_check["note"] == "1 approval race row(s)"


def test_release_gate_accepts_strict_sse_soak_evidence(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "execute-plans" / "scripts" / "aggregate-release-gate.mjs"
    current_run = tmp_path / ".lovable" / "audits" / "current-run"
    current_run.mkdir(parents=True)
    (current_run / "BFF-CONSOL-011-sse-replay-smoke.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-CONSOL-011",
                "channel": "approval",
                "strict_live_evidence": True,
                "strict_live_evidence_requirements": {"min_reconnect_attempts": 5},
                "summary": {"passed": True},
                "soak": {
                    "enabled": True,
                    "seconds": 75.0,
                    "min_heartbeats": 1,
                    "bearer_polyfill": {
                        "ok": True,
                        "missing_expected_event_ids": [],
                        "blocks": {
                            "heartbeat_count": 2,
                            "duplicate_event_ids": [],
                        },
                    },
                },
                "reconnect_sequence": {
                    "bearer_polyfill": _strict_sse_reconnect_bearer(),
                },
            }
        ),
        encoding="utf-8",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PANTHEON_AUDIT_OUT_DIR",
            "PANTHEON_RELEASE_GATE_CHECKLIST_OUT",
            "PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE",
        }
    }
    env.update(
        {
            "PANTHEON_FRONTEND_SHA": "f" * 40,
            "PANTHEON_BFF_SHA": "b" * 40,
            "PANTHEON_BFF_BASE_URL": "https://bff.example.test",
        }
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode in {0, 1}, result.stderr

    summary = json.loads(
        (current_run / "release-gate-summary.json").read_text(encoding="utf-8")
    )
    sse_check = next(
        check
        for check in summary["gates"]["3"]
        if check["label"] == "Authenticated: strict SSE soak observes heartbeat and no duplicate replay."
    )
    assert sse_check["status"] == "pass"
    assert sse_check["note"] == "strict:true soak:75s heartbeat:2/1 reconnect:5/5 attemptDetails:true attemptLineage:true observed:5/5 observedSequence:true duplicates:0 missingReplay:0"


def test_release_gate_rejects_strict_sse_soak_with_only_two_reconnect_cycles(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "execute-plans" / "scripts" / "aggregate-release-gate.mjs"
    current_run = tmp_path / ".lovable" / "audits" / "current-run"
    current_run.mkdir(parents=True)
    (current_run / "BFF-CONSOL-011-sse-replay-smoke.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-CONSOL-011",
                "channel": "approval",
                "strict_live_evidence": True,
                "strict_live_evidence_requirements": {"min_reconnect_attempts": 5},
                "summary": {"passed": True},
                "soak": {
                    "enabled": True,
                    "seconds": 75.0,
                    "min_heartbeats": 1,
                    "bearer_polyfill": {
                        "ok": True,
                        "missing_expected_event_ids": [],
                        "blocks": {
                            "heartbeat_count": 2,
                            "duplicate_event_ids": [],
                        },
                    },
                },
                "reconnect_sequence": {
                    "bearer_polyfill": _strict_sse_reconnect_bearer(attempt_count=2),
                },
            }
        ),
        encoding="utf-8",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PANTHEON_AUDIT_OUT_DIR",
            "PANTHEON_RELEASE_GATE_CHECKLIST_OUT",
            "PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE",
        }
    }
    env.update(
        {
            "PANTHEON_FRONTEND_SHA": "f" * 40,
            "PANTHEON_BFF_SHA": "b" * 40,
            "PANTHEON_BFF_BASE_URL": "https://bff.example.test",
        }
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout

    summary = json.loads(
        (current_run / "release-gate-summary.json").read_text(encoding="utf-8")
    )
    sse_check = next(
        check
        for check in summary["gates"]["3"]
        if check["label"] == "Authenticated: strict SSE soak observes heartbeat and no duplicate replay."
    )
    assert sse_check["status"] == "fail"
    assert sse_check["note"] == "strict:true soak:75s heartbeat:2/1 reconnect:2/5 attemptDetails:false attemptLineage:false observed:2/5 observedSequence:false duplicates:0 missingReplay:0"


def test_release_gate_rejects_strict_sse_soak_without_reconnect_detail_proof(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "execute-plans" / "scripts" / "aggregate-release-gate.mjs"
    current_run = tmp_path / ".lovable" / "audits" / "current-run"
    current_run.mkdir(parents=True)
    (current_run / "BFF-CONSOL-011-sse-replay-smoke.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-CONSOL-011",
                "channel": "approval",
                "strict_live_evidence": True,
                "strict_live_evidence_requirements": {"min_reconnect_attempts": 5},
                "summary": {"passed": True},
                "soak": {
                    "enabled": True,
                    "seconds": 75.0,
                    "min_heartbeats": 1,
                    "bearer_polyfill": {
                        "ok": True,
                        "missing_expected_event_ids": [],
                        "blocks": {
                            "heartbeat_count": 2,
                            "duplicate_event_ids": [],
                        },
                    },
                },
                "reconnect_sequence": {
                    "bearer_polyfill": _strict_sse_reconnect_bearer(
                        detail_ok=False,
                        duplicate_observed=True,
                    ),
                },
            }
        ),
        encoding="utf-8",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PANTHEON_AUDIT_OUT_DIR",
            "PANTHEON_RELEASE_GATE_CHECKLIST_OUT",
            "PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE",
        }
    }
    env.update(
        {
            "PANTHEON_FRONTEND_SHA": "f" * 40,
            "PANTHEON_BFF_SHA": "b" * 40,
            "PANTHEON_BFF_BASE_URL": "https://bff.example.test",
        }
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout

    summary = json.loads(
        (current_run / "release-gate-summary.json").read_text(encoding="utf-8")
    )
    sse_check = next(
        check
        for check in summary["gates"]["3"]
        if check["label"] == "Authenticated: strict SSE soak observes heartbeat and no duplicate replay."
    )
    assert sse_check["status"] == "fail"
    assert sse_check["note"] == "strict:true soak:75s heartbeat:2/1 reconnect:5/5 attemptDetails:false attemptLineage:false observed:5/5 observedSequence:false duplicates:0 missingReplay:0"


def test_release_gate_rejects_strict_sse_soak_with_unlinked_reconnect_attempt_lineage(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "execute-plans" / "scripts" / "aggregate-release-gate.mjs"
    current_run = tmp_path / ".lovable" / "audits" / "current-run"
    current_run.mkdir(parents=True)
    (current_run / "BFF-CONSOL-011-sse-replay-smoke.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-CONSOL-011",
                "channel": "approval",
                "strict_live_evidence": True,
                "strict_live_evidence_requirements": {"min_reconnect_attempts": 5},
                "summary": {"passed": True},
                "soak": {
                    "enabled": True,
                    "seconds": 75.0,
                    "min_heartbeats": 1,
                    "bearer_polyfill": {
                        "ok": True,
                        "missing_expected_event_ids": [],
                        "blocks": {
                            "heartbeat_count": 2,
                            "duplicate_event_ids": [],
                        },
                    },
                },
                "reconnect_sequence": {
                    "bearer_polyfill": _strict_sse_reconnect_bearer(lineage_ok=False),
                },
            }
        ),
        encoding="utf-8",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PANTHEON_AUDIT_OUT_DIR",
            "PANTHEON_RELEASE_GATE_CHECKLIST_OUT",
            "PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE",
        }
    }
    env.update(
        {
            "PANTHEON_FRONTEND_SHA": "f" * 40,
            "PANTHEON_BFF_SHA": "b" * 40,
            "PANTHEON_BFF_BASE_URL": "https://bff.example.test",
        }
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout

    summary = json.loads(
        (current_run / "release-gate-summary.json").read_text(encoding="utf-8")
    )
    sse_check = next(
        check
        for check in summary["gates"]["3"]
        if check["label"] == "Authenticated: strict SSE soak observes heartbeat and no duplicate replay."
    )
    assert sse_check["status"] == "fail"
    assert sse_check["note"] == "strict:true soak:75s heartbeat:2/1 reconnect:5/5 attemptDetails:true attemptLineage:false observed:5/5 observedSequence:true duplicates:0 missingReplay:0"


def test_release_gate_rejects_strict_sse_soak_with_unlinked_event_type_detail(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "execute-plans" / "scripts" / "aggregate-release-gate.mjs"
    current_run = tmp_path / ".lovable" / "audits" / "current-run"
    current_run.mkdir(parents=True)
    (current_run / "BFF-CONSOL-011-sse-replay-smoke.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-CONSOL-011",
                "channel": "approval",
                "strict_live_evidence": True,
                "strict_live_evidence_requirements": {"min_reconnect_attempts": 5},
                "summary": {"passed": True},
                "soak": {
                    "enabled": True,
                    "seconds": 75.0,
                    "min_heartbeats": 1,
                    "bearer_polyfill": {
                        "ok": True,
                        "missing_expected_event_ids": [],
                        "blocks": {
                            "heartbeat_count": 2,
                            "duplicate_event_ids": [],
                        },
                    },
                },
                "reconnect_sequence": {
                    "bearer_polyfill": _strict_sse_reconnect_bearer(event_type_link_ok=False),
                },
            }
        ),
        encoding="utf-8",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PANTHEON_AUDIT_OUT_DIR",
            "PANTHEON_RELEASE_GATE_CHECKLIST_OUT",
            "PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE",
        }
    }
    env.update(
        {
            "PANTHEON_FRONTEND_SHA": "f" * 40,
            "PANTHEON_BFF_SHA": "b" * 40,
            "PANTHEON_BFF_BASE_URL": "https://bff.example.test",
        }
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout

    summary = json.loads(
        (current_run / "release-gate-summary.json").read_text(encoding="utf-8")
    )
    sse_check = next(
        check
        for check in summary["gates"]["3"]
        if check["label"] == "Authenticated: strict SSE soak observes heartbeat and no duplicate replay."
    )
    assert sse_check["status"] == "fail"
    assert sse_check["note"] == "strict:true soak:75s heartbeat:2/1 reconnect:5/5 attemptDetails:true attemptLineage:false observed:5/5 observedSequence:true duplicates:0 missingReplay:0"


def test_release_gate_rejects_strict_sse_soak_without_reconnect_sequence(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "execute-plans" / "scripts" / "aggregate-release-gate.mjs"
    current_run = tmp_path / ".lovable" / "audits" / "current-run"
    current_run.mkdir(parents=True)
    (current_run / "BFF-CONSOL-011-sse-replay-smoke.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-CONSOL-011",
                "channel": "approval",
                "strict_live_evidence": True,
                "strict_live_evidence_requirements": {"min_reconnect_attempts": 5},
                "summary": {"passed": True},
                "soak": {
                    "enabled": True,
                    "seconds": 75.0,
                    "min_heartbeats": 1,
                    "bearer_polyfill": {
                        "ok": True,
                        "missing_expected_event_ids": [],
                        "blocks": {
                            "heartbeat_count": 2,
                            "duplicate_event_ids": [],
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PANTHEON_AUDIT_OUT_DIR",
            "PANTHEON_RELEASE_GATE_CHECKLIST_OUT",
            "PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE",
        }
    }
    env.update(
        {
            "PANTHEON_FRONTEND_SHA": "f" * 40,
            "PANTHEON_BFF_SHA": "b" * 40,
            "PANTHEON_BFF_BASE_URL": "https://bff.example.test",
        }
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout

    summary = json.loads(
        (current_run / "release-gate-summary.json").read_text(encoding="utf-8")
    )
    sse_check = next(
        check
        for check in summary["gates"]["3"]
        if check["label"] == "Authenticated: strict SSE soak observes heartbeat and no duplicate replay."
    )
    assert sse_check["status"] == "fail"
    assert sse_check["note"] == "strict:true soak:75s heartbeat:2/1 reconnect:0/5 attemptDetails:false attemptLineage:false observed:0/5 observedSequence:false duplicates:0 missingReplay:0"


def test_root_bff_live_evidence_workflow_runs_strict_current_run_probes() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workflow = repo_root / ".github" / "workflows" / "bff-live-evidence-gate.yml"
    text = workflow.read_text(encoding="utf-8")
    dispatch_inputs = _workflow_dispatch_inputs(workflow)

    assert "name: BFF Live Evidence Gate" in text
    assert "workflow_dispatch" in text
    assert dispatch_inputs["approval_race_id"]["required"] is True
    assert dispatch_inputs["two_man_race_id"]["required"] is True
    assert "PANTHEON_AUDIT_OUT_DIR: .lovable/audits/current-run" in text
    assert "PANTHEON_LIVE_EVIDENCE_ENVIRONMENT: ${{ inputs.environment }}" in text
    assert "PANTHEON_BFF_SMOKE_BEARER_TOKEN" in text
    assert "PANTHEON_BFF_RBAC_TOKENS_JSON" in text
    assert "PANTHEON_BFF_APPROVAL_RACE_TOKEN_A" in text
    assert "PANTHEON_BFF_APPROVAL_RACE_TOKEN_B" in text
    assert "scripts/write_bff_live_evidence_preflight.py" in text
    assert "BFF-LIVE-EVIDENCE-PREFLIGHT.json" in text
    assert '--soak-seconds "${{ inputs.soak_seconds }}"' in text
    assert 'test -n "$PANTHEON_BFF_SMOKE_BEARER_TOKEN"' not in text
    assert "scripts/probe_bff_authenticated_live.py" in text
    assert "--strict-live-evidence" in text
    assert "--include-writes" in text
    assert "--approval-race-id" in text
    assert "BFF-LUV-AUTHED-LIVE-001-live-smoke.json" in text
    assert "scripts/probe_bff_sse_stream.py" in text
    assert "--soak-min-heartbeats 1" in text
    assert "--reconnect-attempts 5" in text
    assert "BFF-CONSOL-011-sse-replay-smoke.json" in text
    assert "execute-plans/scripts/aggregate-release-gate.mjs" in text
    assert "path: .lovable/audits/current-run" in text
    assert ".lovable/audits/*.md" not in text
    assert ".lovable/audits/historical" not in text
    upload_step = _workflow_step(workflow, "live-evidence", "Upload current-run evidence")
    upload_paths = _upload_artifact_paths(upload_step)
    assert upload_step["uses"] == "actions/upload-artifact@v4"
    assert upload_paths == [".lovable/audits/current-run"]
    assert upload_step["with"]["if-no-files-found"] == "error"
    _assert_current_run_is_only_uploaded_audit_path(upload_paths)


def test_stage0_registered_workflow_can_dispatch_strict_live_evidence_mode() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workflow = repo_root / ".github" / "workflows" / "stage-0-ci.yml"
    text = workflow.read_text(encoding="utf-8")
    dispatch_inputs = _workflow_dispatch_inputs(workflow)

    assert "workflow_dispatch:" in text
    assert "mode:" in text
    assert "- live-evidence" in text
    assert "approval_race_id:" in text
    assert "two_man_race_id:" in text
    assert dispatch_inputs["approval_race_id"]["required"] is True
    assert dispatch_inputs["two_man_race_id"]["required"] is True
    assert "soak_seconds:" in text
    assert "live-evidence:" in text
    assert "github.event_name == 'workflow_dispatch' && inputs.mode == 'live-evidence'" in text
    assert "github.event_name != 'workflow_dispatch' || inputs.mode != 'live-evidence'" in text
    assert "PANTHEON_AUDIT_OUT_DIR: .lovable/audits/current-run" in text
    assert "PANTHEON_LIVE_EVIDENCE_ENVIRONMENT: ${{ inputs.environment }}" in text
    assert "PANTHEON_BFF_SMOKE_BEARER_TOKEN" in text
    assert "PANTHEON_BFF_RBAC_TOKENS_JSON" in text
    assert "PANTHEON_BFF_APPROVAL_RACE_TOKEN_A" in text
    assert "PANTHEON_BFF_APPROVAL_RACE_TOKEN_B" in text
    assert "scripts/write_bff_live_evidence_preflight.py" in text
    assert "BFF-LIVE-EVIDENCE-PREFLIGHT.json" in text
    assert '--soak-seconds "${{ inputs.soak_seconds }}"' in text
    assert 'test -n "$PANTHEON_BFF_SMOKE_BEARER_TOKEN"' not in text
    assert "scripts/probe_bff_authenticated_live.py" in text
    assert "--strict-live-evidence" in text
    assert "--include-writes" in text
    assert "--approval-race-id" in text
    assert "BFF-LUV-AUTHED-LIVE-001-live-smoke.json" in text
    assert "scripts/probe_bff_sse_stream.py" in text
    assert "--soak-min-heartbeats 1" in text
    assert "--reconnect-attempts 5" in text
    assert "BFF-CONSOL-011-sse-replay-smoke.json" in text
    assert "execute-plans/scripts/aggregate-release-gate.mjs" in text
    assert "path: .lovable/audits/current-run" in text
    assert ".lovable/audits/*.md" not in text
    assert ".lovable/audits/historical" not in text
    upload_step = _workflow_step(workflow, "live-evidence", "Upload current-run evidence")
    upload_paths = _upload_artifact_paths(upload_step)
    assert upload_step["uses"] == "actions/upload-artifact@v4"
    assert upload_paths == [".lovable/audits/current-run"]
    assert upload_step["with"]["if-no-files-found"] == "error"
    _assert_current_run_is_only_uploaded_audit_path(upload_paths)


def test_release_gate_accepts_strict_authenticated_live_json_evidence(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "execute-plans" / "scripts" / "aggregate-release-gate.mjs"
    current_run = tmp_path / ".lovable" / "audits" / "current-run"
    current_run.mkdir(parents=True)

    rbac_labels = ["viewer", "operator", "reviewer", "approver", "admin", "empty", "unknown"]
    rbac_cases = {"anonymous": {"kind": "anonymous"}}
    rbac_cases.update({label: {"kind": "provided_bearer", "sha256_12": f"rbac-{label}-hash"} for label in rbac_labels})
    dry_run = _strict_dry_run_items()
    (current_run / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-LUV-AUTHED-LIVE-001",
                "strict_live_evidence": True,
                "auth_source": {"kind": "provided_bearer"},
                "rbac_auth_source": {
                    "kind": "rbac_matrix",
                    "cases": rbac_cases,
                    "provided_bearer_count": 7,
                    "distinct_provided_bearer_count": 7,
                    "distinct_provided_bearers": True,
                    "duplicate_bearer_label_groups": [],
                },
                "include_writes": True,
                "include_rbac_matrix": True,
                "include_dry_run": True,
                "include_approval_race": True,
                "include_two_man_race": True,
                "summary": {
                    "total": 65,
                    "passed": 65,
                    "failed": 0,
                    "rbac_matrix_probes": 56,
                    "rbac_write_probes": 32,
                    "rbac_write_side_effect_proofs": 32,
                    "dry_run_probes": 7,
                    "approval_race_probes": 1,
                    "approval_race_bounded": True,
                    "two_man_race_probes": 1,
                    "two_man_race_operator_scoped": True,
                    "live_capital_side_effects": False,
                },
                "rbac_matrix": _strict_rbac_matrix_items(),
                "dry_run": dry_run,
                "approval_race": _strict_approval_race_item(),
                "two_man_race": _strict_two_man_race_item(),
            }
        ),
        encoding="utf-8",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PANTHEON_AUDIT_OUT_DIR",
            "PANTHEON_RELEASE_GATE_CHECKLIST_OUT",
            "PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE",
        }
    }
    env.update(
        {
            "PANTHEON_FRONTEND_SHA": "f" * 40,
            "PANTHEON_BFF_SHA": "b" * 40,
            "PANTHEON_BFF_BASE_URL": "https://bff.example.test",
        }
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode in {0, 1}, result.stderr

    summary = json.loads(
        (current_run / "release-gate-summary.json").read_text(encoding="utf-8")
    )
    gate3 = summary["gates"]["3"]
    rbac_check = next(
        check
        for check in gate3
        if check["label"] == "Authenticated: strict bearer RBAC matrix evidence passed."
    )
    dry_run_check = next(
        check
        for check in gate3
        if check["label"] == "Authenticated: strict live dry-run evidence has BffErrorEnvelope and no side effects."
    )
    race_check = next(
        check
        for check in gate3
        if check["label"] == "Authenticated: strict multi-operator approval race evidence is bounded."
    )
    two_man_check = next(
        check
        for check in gate3
        if check["label"] == "Authenticated: strict two-man-sign race evidence is operator-scoped."
    )

    assert rbac_check["status"] == "pass"
    assert rbac_check["note"] == "strict:true bearer:true rbac:56/56 matrixCoverage:56/56 detailLinks:56/56 providedCases:7/7 distinctBearers:7/7 writeSideEffectProofs:32/32 writeMarkerLinks:32/32"
    assert dry_run_check["status"] == "pass"
    assert dry_run_check["note"] == "strict:true dryRun:7/7 familyCoverage:7/7 invalidEnvelope:true readbackLinked:true sideEffectProofs:7/7 sideEffects:none"
    assert race_check["status"] == "pass"
    assert race_check["note"] == "strict:true bounded:true accepted:1 safeErrors:1 safeErrorEnvelope:1/1 results:2/2 targetLinks:2/2 duplicateWinners:false tokenPair:true tokenPairDistinct:true"
    assert two_man_check["status"] == "pass"
    assert two_man_check["note"] == "strict:true operatorScoped:true accepted:2 replayed:0 commandIds:2/2 detailAccepted:2/2 detailReplayed:0/0 detailCommandIds:2/2 results:2/2 targetLinks:2/2 signatureLinks:2/2 tokenPair:true tokenPairDistinct:true"


def test_release_gate_rejects_race_evidence_without_distinct_token_hashes(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "execute-plans" / "scripts" / "aggregate-release-gate.mjs"
    current_run = tmp_path / ".lovable" / "audits" / "current-run"
    current_run.mkdir(parents=True)

    rbac_labels = ["viewer", "operator", "reviewer", "approver", "admin", "empty", "unknown"]
    rbac_cases = {"anonymous": {"kind": "anonymous"}}
    rbac_cases.update({label: {"kind": "provided_bearer", "sha256_12": f"rbac-{label}-hash"} for label in rbac_labels})
    (current_run / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-LUV-AUTHED-LIVE-001",
                "strict_live_evidence": True,
                "auth_source": {"kind": "provided_bearer"},
                "rbac_auth_source": {
                    "kind": "rbac_matrix",
                    "cases": rbac_cases,
                    "provided_bearer_count": 7,
                    "distinct_provided_bearer_count": 7,
                    "distinct_provided_bearers": True,
                    "duplicate_bearer_label_groups": [],
                },
                "include_writes": True,
                "include_rbac_matrix": True,
                "include_dry_run": True,
                "include_approval_race": True,
                "include_two_man_race": True,
                "summary": {
                    "total": 65,
                    "passed": 65,
                    "failed": 0,
                    "rbac_matrix_probes": 56,
                    "rbac_write_probes": 32,
                    "rbac_write_side_effect_proofs": 32,
                    "dry_run_probes": 7,
                    "approval_race_probes": 1,
                    "approval_race_bounded": True,
                    "two_man_race_probes": 1,
                    "two_man_race_operator_scoped": True,
                    "live_capital_side_effects": False,
                },
                "rbac_matrix": _strict_rbac_matrix_items(),
                "dry_run": _strict_dry_run_items(),
                "approval_race": _strict_approval_race_item(distinct_token_pair=False),
                "two_man_race": _strict_two_man_race_item(distinct_token_pair=False),
            }
        ),
        encoding="utf-8",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PANTHEON_AUDIT_OUT_DIR",
            "PANTHEON_RELEASE_GATE_CHECKLIST_OUT",
            "PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE",
        }
    }
    env.update(
        {
            "PANTHEON_FRONTEND_SHA": "f" * 40,
            "PANTHEON_BFF_SHA": "b" * 40,
            "PANTHEON_BFF_BASE_URL": "https://bff.example.test",
        }
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout

    summary = json.loads(
        (current_run / "release-gate-summary.json").read_text(encoding="utf-8")
    )
    race_check = next(
        check
        for check in summary["gates"]["3"]
        if check["label"] == "Authenticated: strict multi-operator approval race evidence is bounded."
    )
    two_man_check = next(
        check
        for check in summary["gates"]["3"]
        if check["label"] == "Authenticated: strict two-man-sign race evidence is operator-scoped."
    )

    assert race_check["status"] == "fail"
    assert race_check["note"] == "strict:true bounded:true accepted:1 safeErrors:1 safeErrorEnvelope:1/1 results:2/2 targetLinks:2/2 duplicateWinners:false tokenPair:true tokenPairDistinct:false"
    assert two_man_check["status"] == "fail"
    assert two_man_check["note"] == "strict:true operatorScoped:true accepted:2 replayed:0 commandIds:2/2 detailAccepted:2/2 detailReplayed:0/0 detailCommandIds:2/2 results:2/2 targetLinks:2/2 signatureLinks:2/2 tokenPair:true tokenPairDistinct:false"


def test_release_gate_rejects_approval_race_without_safe_error_envelope(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "execute-plans" / "scripts" / "aggregate-release-gate.mjs"
    current_run = tmp_path / ".lovable" / "audits" / "current-run"
    current_run.mkdir(parents=True)

    rbac_labels = ["viewer", "operator", "reviewer", "approver", "admin", "empty", "unknown"]
    rbac_cases = {"anonymous": {"kind": "anonymous"}}
    rbac_cases.update({label: {"kind": "provided_bearer", "sha256_12": f"rbac-{label}-hash"} for label in rbac_labels})
    (current_run / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-LUV-AUTHED-LIVE-001",
                "strict_live_evidence": True,
                "auth_source": {"kind": "provided_bearer"},
                "rbac_auth_source": {
                    "kind": "rbac_matrix",
                    "cases": rbac_cases,
                    "provided_bearer_count": 7,
                    "distinct_provided_bearer_count": 7,
                    "distinct_provided_bearers": True,
                    "duplicate_bearer_label_groups": [],
                },
                "include_writes": True,
                "include_rbac_matrix": True,
                "include_dry_run": True,
                "include_approval_race": True,
                "include_two_man_race": True,
                "summary": {
                    "total": 65,
                    "passed": 65,
                    "failed": 0,
                    "rbac_matrix_probes": 56,
                    "rbac_write_probes": 32,
                    "rbac_write_side_effect_proofs": 32,
                    "dry_run_probes": 7,
                    "approval_race_probes": 1,
                    "approval_race_bounded": True,
                    "two_man_race_probes": 1,
                    "two_man_race_operator_scoped": True,
                    "live_capital_side_effects": False,
                },
                "rbac_matrix": _strict_rbac_matrix_items(),
                "dry_run": _strict_dry_run_items(),
                "approval_race": _strict_approval_race_item(safe_error_envelope=False),
                "two_man_race": _strict_two_man_race_item(),
            }
        ),
        encoding="utf-8",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PANTHEON_AUDIT_OUT_DIR",
            "PANTHEON_RELEASE_GATE_CHECKLIST_OUT",
            "PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE",
        }
    }
    env.update(
        {
            "PANTHEON_FRONTEND_SHA": "f" * 40,
            "PANTHEON_BFF_SHA": "b" * 40,
            "PANTHEON_BFF_BASE_URL": "https://bff.example.test",
        }
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout

    summary = json.loads(
        (current_run / "release-gate-summary.json").read_text(encoding="utf-8")
    )
    race_check = next(
        check
        for check in summary["gates"]["3"]
        if check["label"] == "Authenticated: strict multi-operator approval race evidence is bounded."
    )
    assert race_check["status"] == "fail"
    assert race_check["note"] == "strict:true bounded:true accepted:1 safeErrors:1 safeErrorEnvelope:0/1 results:2/2 targetLinks:2/2 duplicateWinners:false tokenPair:true tokenPairDistinct:true"


def test_release_gate_rejects_approval_race_with_unlinked_target_detail(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "execute-plans" / "scripts" / "aggregate-release-gate.mjs"
    current_run = tmp_path / ".lovable" / "audits" / "current-run"
    current_run.mkdir(parents=True)

    rbac_labels = ["viewer", "operator", "reviewer", "approver", "admin", "empty", "unknown"]
    rbac_cases = {"anonymous": {"kind": "anonymous"}}
    rbac_cases.update({label: {"kind": "provided_bearer", "sha256_12": f"rbac-{label}-hash"} for label in rbac_labels})
    (current_run / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-LUV-AUTHED-LIVE-001",
                "strict_live_evidence": True,
                "auth_source": {"kind": "provided_bearer"},
                "rbac_auth_source": {
                    "kind": "rbac_matrix",
                    "cases": rbac_cases,
                    "provided_bearer_count": 7,
                    "distinct_provided_bearer_count": 7,
                    "distinct_provided_bearers": True,
                    "duplicate_bearer_label_groups": [],
                },
                "include_writes": True,
                "include_rbac_matrix": True,
                "include_dry_run": True,
                "include_approval_race": True,
                "include_two_man_race": True,
                "summary": {
                    "total": 65,
                    "passed": 65,
                    "failed": 0,
                    "rbac_matrix_probes": 56,
                    "rbac_write_probes": 32,
                    "rbac_write_side_effect_proofs": 32,
                    "dry_run_probes": 7,
                    "approval_race_probes": 1,
                    "approval_race_bounded": True,
                    "two_man_race_probes": 1,
                    "two_man_race_operator_scoped": True,
                    "live_capital_side_effects": False,
                },
                "rbac_matrix": _strict_rbac_matrix_items(),
                "dry_run": _strict_dry_run_items(),
                "approval_race": _strict_approval_race_item(mismatched_target_link=True),
                "two_man_race": _strict_two_man_race_item(),
            }
        ),
        encoding="utf-8",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PANTHEON_AUDIT_OUT_DIR",
            "PANTHEON_RELEASE_GATE_CHECKLIST_OUT",
            "PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE",
        }
    }
    env.update(
        {
            "PANTHEON_FRONTEND_SHA": "f" * 40,
            "PANTHEON_BFF_SHA": "b" * 40,
            "PANTHEON_BFF_BASE_URL": "https://bff.example.test",
        }
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout

    summary = json.loads(
        (current_run / "release-gate-summary.json").read_text(encoding="utf-8")
    )
    race_check = next(
        check
        for check in summary["gates"]["3"]
        if check["label"] == "Authenticated: strict multi-operator approval race evidence is bounded."
    )
    assert race_check["status"] == "fail"
    assert race_check["note"] == "strict:true bounded:true accepted:1 safeErrors:1 safeErrorEnvelope:1/1 results:2/2 targetLinks:1/2 duplicateWinners:false tokenPair:true tokenPairDistinct:true"

def test_release_gate_rejects_two_man_race_with_replayed_detail(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "execute-plans" / "scripts" / "aggregate-release-gate.mjs"
    current_run = tmp_path / ".lovable" / "audits" / "current-run"
    current_run.mkdir(parents=True)

    rbac_labels = ["viewer", "operator", "reviewer", "approver", "admin", "empty", "unknown"]
    rbac_cases = {"anonymous": {"kind": "anonymous"}}
    rbac_cases.update({label: {"kind": "provided_bearer", "sha256_12": f"rbac-{label}-hash"} for label in rbac_labels})
    (current_run / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-LUV-AUTHED-LIVE-001",
                "strict_live_evidence": True,
                "auth_source": {"kind": "provided_bearer"},
                "rbac_auth_source": {
                    "kind": "rbac_matrix",
                    "cases": rbac_cases,
                    "provided_bearer_count": 7,
                    "distinct_provided_bearer_count": 7,
                    "distinct_provided_bearers": True,
                    "duplicate_bearer_label_groups": [],
                },
                "include_writes": True,
                "include_rbac_matrix": True,
                "include_dry_run": True,
                "include_approval_race": True,
                "include_two_man_race": True,
                "summary": {
                    "total": 65,
                    "passed": 65,
                    "failed": 0,
                    "rbac_matrix_probes": 56,
                    "rbac_write_probes": 32,
                    "rbac_write_side_effect_proofs": 32,
                    "dry_run_probes": 7,
                    "approval_race_probes": 1,
                    "approval_race_bounded": True,
                    "two_man_race_probes": 1,
                    "two_man_race_operator_scoped": True,
                    "live_capital_side_effects": False,
                },
                "rbac_matrix": _strict_rbac_matrix_items(),
                "dry_run": _strict_dry_run_items(),
                "approval_race": _strict_approval_race_item(),
                "two_man_race": _strict_two_man_race_item(
                    detail_replayed=True,
                    detail_duplicate_command_ids=True,
                ),
            }
        ),
        encoding="utf-8",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PANTHEON_AUDIT_OUT_DIR",
            "PANTHEON_RELEASE_GATE_CHECKLIST_OUT",
            "PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE",
        }
    }
    env.update(
        {
            "PANTHEON_FRONTEND_SHA": "f" * 40,
            "PANTHEON_BFF_SHA": "b" * 40,
            "PANTHEON_BFF_BASE_URL": "https://bff.example.test",
        }
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout

    summary = json.loads(
        (current_run / "release-gate-summary.json").read_text(encoding="utf-8")
    )
    two_man_check = next(
        check
        for check in summary["gates"]["3"]
        if check["label"] == "Authenticated: strict two-man-sign race evidence is operator-scoped."
    )
    assert two_man_check["status"] == "fail"
    assert two_man_check["note"] == "strict:true operatorScoped:true accepted:2 replayed:0 commandIds:2/2 detailAccepted:2/2 detailReplayed:2/0 detailCommandIds:1/2 results:2/2 targetLinks:2/2 signatureLinks:2/2 tokenPair:true tokenPairDistinct:true"


def test_release_gate_rejects_two_man_race_with_unlinked_target_detail(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "execute-plans" / "scripts" / "aggregate-release-gate.mjs"
    current_run = tmp_path / ".lovable" / "audits" / "current-run"
    current_run.mkdir(parents=True)

    rbac_labels = ["viewer", "operator", "reviewer", "approver", "admin", "empty", "unknown"]
    rbac_cases = {"anonymous": {"kind": "anonymous"}}
    rbac_cases.update({label: {"kind": "provided_bearer", "sha256_12": f"rbac-{label}-hash"} for label in rbac_labels})
    (current_run / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-LUV-AUTHED-LIVE-001",
                "strict_live_evidence": True,
                "auth_source": {"kind": "provided_bearer"},
                "rbac_auth_source": {
                    "kind": "rbac_matrix",
                    "cases": rbac_cases,
                    "provided_bearer_count": 7,
                    "distinct_provided_bearer_count": 7,
                    "distinct_provided_bearers": True,
                    "duplicate_bearer_label_groups": [],
                },
                "include_writes": True,
                "include_rbac_matrix": True,
                "include_dry_run": True,
                "include_approval_race": True,
                "include_two_man_race": True,
                "summary": {
                    "total": 65,
                    "passed": 65,
                    "failed": 0,
                    "rbac_matrix_probes": 56,
                    "rbac_write_probes": 32,
                    "rbac_write_side_effect_proofs": 32,
                    "dry_run_probes": 7,
                    "approval_race_probes": 1,
                    "approval_race_bounded": True,
                    "two_man_race_probes": 1,
                    "two_man_race_operator_scoped": True,
                    "live_capital_side_effects": False,
                },
                "rbac_matrix": _strict_rbac_matrix_items(),
                "dry_run": _strict_dry_run_items(),
                "approval_race": _strict_approval_race_item(),
                "two_man_race": _strict_two_man_race_item(mismatched_target_link=True),
            }
        ),
        encoding="utf-8",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PANTHEON_AUDIT_OUT_DIR",
            "PANTHEON_RELEASE_GATE_CHECKLIST_OUT",
            "PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE",
        }
    }
    env.update(
        {
            "PANTHEON_FRONTEND_SHA": "f" * 40,
            "PANTHEON_BFF_SHA": "b" * 40,
            "PANTHEON_BFF_BASE_URL": "https://bff.example.test",
        }
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout

    summary = json.loads(
        (current_run / "release-gate-summary.json").read_text(encoding="utf-8")
    )
    two_man_check = next(
        check
        for check in summary["gates"]["3"]
        if check["label"] == "Authenticated: strict two-man-sign race evidence is operator-scoped."
    )
    assert two_man_check["status"] == "fail"
    assert two_man_check["note"] == "strict:true operatorScoped:true accepted:2 replayed:0 commandIds:2/2 detailAccepted:2/2 detailReplayed:0/0 detailCommandIds:2/2 results:2/2 targetLinks:1/2 signatureLinks:1/2 tokenPair:true tokenPairDistinct:true"


def test_release_gate_rejects_two_man_race_with_duplicate_signature_link(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "execute-plans" / "scripts" / "aggregate-release-gate.mjs"
    current_run = tmp_path / ".lovable" / "audits" / "current-run"
    current_run.mkdir(parents=True)

    rbac_labels = ["viewer", "operator", "reviewer", "approver", "admin", "empty", "unknown"]
    rbac_cases = {"anonymous": {"kind": "anonymous"}}
    rbac_cases.update({label: {"kind": "provided_bearer", "sha256_12": f"rbac-{label}-hash"} for label in rbac_labels})
    (current_run / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-LUV-AUTHED-LIVE-001",
                "strict_live_evidence": True,
                "auth_source": {"kind": "provided_bearer"},
                "rbac_auth_source": {
                    "kind": "rbac_matrix",
                    "cases": rbac_cases,
                    "provided_bearer_count": 7,
                    "distinct_provided_bearer_count": 7,
                    "distinct_provided_bearers": True,
                    "duplicate_bearer_label_groups": [],
                },
                "include_writes": True,
                "include_rbac_matrix": True,
                "include_dry_run": True,
                "include_approval_race": True,
                "include_two_man_race": True,
                "summary": {
                    "total": 65,
                    "passed": 65,
                    "failed": 0,
                    "rbac_matrix_probes": 56,
                    "rbac_write_probes": 32,
                    "rbac_write_side_effect_proofs": 32,
                    "dry_run_probes": 7,
                    "approval_race_probes": 1,
                    "approval_race_bounded": True,
                    "two_man_race_probes": 1,
                    "two_man_race_operator_scoped": True,
                    "live_capital_side_effects": False,
                },
                "rbac_matrix": _strict_rbac_matrix_items(),
                "dry_run": _strict_dry_run_items(),
                "approval_race": _strict_approval_race_item(),
                "two_man_race": _strict_two_man_race_item(duplicate_signature_link=True),
            }
        ),
        encoding="utf-8",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PANTHEON_AUDIT_OUT_DIR",
            "PANTHEON_RELEASE_GATE_CHECKLIST_OUT",
            "PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE",
        }
    }
    env.update(
        {
            "PANTHEON_FRONTEND_SHA": "f" * 40,
            "PANTHEON_BFF_SHA": "b" * 40,
            "PANTHEON_BFF_BASE_URL": "https://bff.example.test",
        }
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout

    summary = json.loads(
        (current_run / "release-gate-summary.json").read_text(encoding="utf-8")
    )
    two_man_check = next(
        check
        for check in summary["gates"]["3"]
        if check["label"] == "Authenticated: strict two-man-sign race evidence is operator-scoped."
    )
    assert two_man_check["status"] == "fail"
    assert two_man_check["note"] == "strict:true operatorScoped:true accepted:2 replayed:0 commandIds:2/2 detailAccepted:2/2 detailReplayed:0/0 detailCommandIds:2/2 results:2/2 targetLinks:2/2 signatureLinks:1/2 tokenPair:true tokenPairDistinct:true"

def test_release_gate_rejects_strict_rbac_matrix_without_required_family_coverage(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "execute-plans" / "scripts" / "aggregate-release-gate.mjs"
    current_run = tmp_path / ".lovable" / "audits" / "current-run"
    current_run.mkdir(parents=True)

    rbac_labels = ["viewer", "operator", "reviewer", "approver", "admin", "empty", "unknown"]
    rbac_cases = {"anonymous": {"kind": "anonymous"}}
    rbac_cases.update({label: {"kind": "provided_bearer", "sha256_12": f"rbac-{label}-hash"} for label in rbac_labels})
    rbac_matrix = _strict_rbac_matrix_items()
    for item in rbac_matrix:
        if item["family"] == "rbac-read-viewer-bff-strategies":
            item["family"] = "rbac-read-viewer-bff-strategies-duplicate-gap"
            break

    (current_run / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-LUV-AUTHED-LIVE-001",
                "strict_live_evidence": True,
                "auth_source": {"kind": "provided_bearer"},
                "rbac_auth_source": {
                    "kind": "rbac_matrix",
                    "cases": rbac_cases,
                    "provided_bearer_count": 7,
                    "distinct_provided_bearer_count": 7,
                    "distinct_provided_bearers": True,
                    "duplicate_bearer_label_groups": [],
                },
                "include_writes": True,
                "include_rbac_matrix": True,
                "include_dry_run": True,
                "include_approval_race": True,
                "include_two_man_race": True,
                "summary": {
                    "total": 65,
                    "passed": 65,
                    "failed": 0,
                    "rbac_matrix_probes": 56,
                    "rbac_write_probes": 32,
                    "rbac_write_side_effect_proofs": 32,
                    "dry_run_probes": 7,
                    "approval_race_probes": 1,
                    "approval_race_bounded": True,
                    "two_man_race_probes": 1,
                    "two_man_race_operator_scoped": True,
                    "live_capital_side_effects": False,
                },
                "rbac_matrix": rbac_matrix,
                "dry_run": _strict_dry_run_items(),
                "approval_race": _strict_approval_race_item(),
                "two_man_race": _strict_two_man_race_item(),
            }
        ),
        encoding="utf-8",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PANTHEON_AUDIT_OUT_DIR",
            "PANTHEON_RELEASE_GATE_CHECKLIST_OUT",
            "PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE",
        }
    }
    env.update(
        {
            "PANTHEON_FRONTEND_SHA": "f" * 40,
            "PANTHEON_BFF_SHA": "b" * 40,
            "PANTHEON_BFF_BASE_URL": "https://bff.example.test",
        }
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout

    summary = json.loads(
        (current_run / "release-gate-summary.json").read_text(encoding="utf-8")
    )
    rbac_check = next(
        check
        for check in summary["gates"]["3"]
        if check["label"] == "Authenticated: strict bearer RBAC matrix evidence passed."
    )
    assert rbac_check["status"] == "fail"
    assert rbac_check["note"] == "strict:true bearer:true rbac:56/56 matrixCoverage:55/56 detailLinks:55/56 providedCases:7/7 distinctBearers:7/7 writeSideEffectProofs:32/32 writeMarkerLinks:32/32"


def test_release_gate_rejects_strict_rbac_matrix_with_unlinked_detail(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "execute-plans" / "scripts" / "aggregate-release-gate.mjs"
    current_run = tmp_path / ".lovable" / "audits" / "current-run"
    current_run.mkdir(parents=True)

    rbac_labels = ["viewer", "operator", "reviewer", "approver", "admin", "empty", "unknown"]
    rbac_cases = {"anonymous": {"kind": "anonymous"}}
    rbac_cases.update({label: {"kind": "provided_bearer", "sha256_12": f"rbac-{label}-hash"} for label in rbac_labels})
    (current_run / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-LUV-AUTHED-LIVE-001",
                "strict_live_evidence": True,
                "auth_source": {"kind": "provided_bearer"},
                "rbac_auth_source": {
                    "kind": "rbac_matrix",
                    "cases": rbac_cases,
                    "provided_bearer_count": 7,
                    "distinct_provided_bearer_count": 7,
                    "distinct_provided_bearers": True,
                    "duplicate_bearer_label_groups": [],
                },
                "include_writes": True,
                "include_rbac_matrix": True,
                "include_dry_run": True,
                "include_approval_race": True,
                "include_two_man_race": True,
                "summary": {
                    "total": 65,
                    "passed": 65,
                    "failed": 0,
                    "rbac_matrix_probes": 56,
                    "rbac_write_probes": 32,
                    "rbac_write_side_effect_proofs": 32,
                    "dry_run_probes": 7,
                    "approval_race_probes": 1,
                    "approval_race_bounded": True,
                    "two_man_race_probes": 1,
                    "two_man_race_operator_scoped": True,
                    "live_capital_side_effects": False,
                },
                "rbac_matrix": _strict_rbac_matrix_items(mismatched_detail_link=True),
                "dry_run": _strict_dry_run_items(),
                "approval_race": _strict_approval_race_item(),
                "two_man_race": _strict_two_man_race_item(),
            }
        ),
        encoding="utf-8",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PANTHEON_AUDIT_OUT_DIR",
            "PANTHEON_RELEASE_GATE_CHECKLIST_OUT",
            "PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE",
        }
    }
    env.update(
        {
            "PANTHEON_FRONTEND_SHA": "f" * 40,
            "PANTHEON_BFF_SHA": "b" * 40,
            "PANTHEON_BFF_BASE_URL": "https://bff.example.test",
        }
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout

    summary = json.loads(
        (current_run / "release-gate-summary.json").read_text(encoding="utf-8")
    )
    rbac_check = next(
        check
        for check in summary["gates"]["3"]
        if check["label"] == "Authenticated: strict bearer RBAC matrix evidence passed."
    )
    assert rbac_check["status"] == "fail"
    assert rbac_check["note"] == "strict:true bearer:true rbac:56/56 matrixCoverage:56/56 detailLinks:55/56 providedCases:7/7 distinctBearers:7/7 writeSideEffectProofs:32/32 writeMarkerLinks:32/32"


def test_release_gate_rejects_strict_rbac_matrix_without_distinct_bearers(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "execute-plans" / "scripts" / "aggregate-release-gate.mjs"
    current_run = tmp_path / ".lovable" / "audits" / "current-run"
    current_run.mkdir(parents=True)

    rbac_labels = ["viewer", "operator", "reviewer", "approver", "admin", "empty", "unknown"]
    rbac_cases = {"anonymous": {"kind": "anonymous"}}
    rbac_cases.update({label: {"kind": "provided_bearer", "sha256_12": f"rbac-{label}-hash"} for label in rbac_labels})
    rbac_cases["operator"]["sha256_12"] = rbac_cases["viewer"]["sha256_12"]
    rbac_matrix = _strict_rbac_matrix_items()
    for item in rbac_matrix:
        if item.get("rbac_label") == "operator":
            item["request_bearer_sha256_12"] = rbac_cases["operator"]["sha256_12"]
    (current_run / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-LUV-AUTHED-LIVE-001",
                "strict_live_evidence": True,
                "auth_source": {"kind": "provided_bearer"},
                "rbac_auth_source": {
                    "kind": "rbac_matrix",
                    "cases": rbac_cases,
                    "provided_bearer_count": 7,
                    "distinct_provided_bearer_count": 6,
                    "distinct_provided_bearers": False,
                    "duplicate_bearer_label_groups": [["viewer", "operator"]],
                },
                "include_writes": True,
                "include_rbac_matrix": True,
                "include_dry_run": True,
                "include_approval_race": True,
                "include_two_man_race": True,
                "summary": {
                    "total": 65,
                    "passed": 65,
                    "failed": 0,
                    "rbac_matrix_probes": 56,
                    "rbac_write_probes": 32,
                    "rbac_write_side_effect_proofs": 32,
                    "dry_run_probes": 7,
                    "approval_race_probes": 1,
                    "approval_race_bounded": True,
                    "two_man_race_probes": 1,
                    "two_man_race_operator_scoped": True,
                    "live_capital_side_effects": False,
                },
                "rbac_matrix": rbac_matrix,
                "dry_run": _strict_dry_run_items(),
                "approval_race": _strict_approval_race_item(),
                "two_man_race": _strict_two_man_race_item(),
            }
        ),
        encoding="utf-8",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PANTHEON_AUDIT_OUT_DIR",
            "PANTHEON_RELEASE_GATE_CHECKLIST_OUT",
            "PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE",
        }
    }
    env.update(
        {
            "PANTHEON_FRONTEND_SHA": "f" * 40,
            "PANTHEON_BFF_SHA": "b" * 40,
            "PANTHEON_BFF_BASE_URL": "https://bff.example.test",
        }
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout

    summary = json.loads(
        (current_run / "release-gate-summary.json").read_text(encoding="utf-8")
    )
    rbac_check = next(
        check
        for check in summary["gates"]["3"]
        if check["label"] == "Authenticated: strict bearer RBAC matrix evidence passed."
    )
    assert rbac_check["status"] == "fail"
    assert rbac_check["note"] == "strict:true bearer:true rbac:56/56 matrixCoverage:56/56 detailLinks:56/56 providedCases:7/7 distinctBearers:6/7 writeSideEffectProofs:32/32 writeMarkerLinks:32/32"


def test_release_gate_rejects_strict_rbac_matrix_without_write_side_effect_proofs(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "execute-plans" / "scripts" / "aggregate-release-gate.mjs"
    current_run = tmp_path / ".lovable" / "audits" / "current-run"
    current_run.mkdir(parents=True)

    rbac_labels = ["viewer", "operator", "reviewer", "approver", "admin", "empty", "unknown"]
    rbac_cases = {"anonymous": {"kind": "anonymous"}}
    rbac_cases.update({label: {"kind": "provided_bearer", "sha256_12": f"rbac-{label}-hash"} for label in rbac_labels})
    (current_run / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-LUV-AUTHED-LIVE-001",
                "strict_live_evidence": True,
                "auth_source": {"kind": "provided_bearer"},
                "rbac_auth_source": {
                    "kind": "rbac_matrix",
                    "cases": rbac_cases,
                    "provided_bearer_count": 7,
                    "distinct_provided_bearer_count": 7,
                    "distinct_provided_bearers": True,
                    "duplicate_bearer_label_groups": [],
                },
                "include_writes": True,
                "include_rbac_matrix": True,
                "include_dry_run": True,
                "include_approval_race": True,
                "include_two_man_race": True,
                "summary": {
                    "total": 65,
                    "passed": 65,
                    "failed": 0,
                    "rbac_matrix_probes": 56,
                    "rbac_write_probes": 32,
                    "rbac_write_side_effect_proofs": 0,
                    "dry_run_probes": 7,
                    "approval_race_probes": 1,
                    "approval_race_bounded": True,
                    "two_man_race_probes": 1,
                    "two_man_race_operator_scoped": True,
                    "live_capital_side_effects": False,
                },
                "rbac_matrix": _strict_rbac_matrix_items(with_write_side_effect_checks=False),
                "dry_run": _strict_dry_run_items(),
                "approval_race": _strict_approval_race_item(),
                "two_man_race": _strict_two_man_race_item(),
            }
        ),
        encoding="utf-8",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PANTHEON_AUDIT_OUT_DIR",
            "PANTHEON_RELEASE_GATE_CHECKLIST_OUT",
            "PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE",
        }
    }
    env.update(
        {
            "PANTHEON_FRONTEND_SHA": "f" * 40,
            "PANTHEON_BFF_SHA": "b" * 40,
            "PANTHEON_BFF_BASE_URL": "https://bff.example.test",
        }
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout

    summary = json.loads(
        (current_run / "release-gate-summary.json").read_text(encoding="utf-8")
    )
    rbac_check = next(
        check
        for check in summary["gates"]["3"]
        if check["label"] == "Authenticated: strict bearer RBAC matrix evidence passed."
    )
    assert rbac_check["status"] == "fail"
    assert rbac_check["note"] == "strict:true bearer:true rbac:56/56 matrixCoverage:56/56 detailLinks:56/56 providedCases:7/7 distinctBearers:7/7 writeSideEffectProofs:0/32 writeMarkerLinks:0/32"


def test_release_gate_rejects_strict_rbac_matrix_with_unlinked_write_marker(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "execute-plans" / "scripts" / "aggregate-release-gate.mjs"
    current_run = tmp_path / ".lovable" / "audits" / "current-run"
    current_run.mkdir(parents=True)

    rbac_labels = ["viewer", "operator", "reviewer", "approver", "admin", "empty", "unknown"]
    rbac_cases = {"anonymous": {"kind": "anonymous"}}
    rbac_cases.update({label: {"kind": "provided_bearer", "sha256_12": f"rbac-{label}-hash"} for label in rbac_labels})
    (current_run / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-LUV-AUTHED-LIVE-001",
                "strict_live_evidence": True,
                "auth_source": {"kind": "provided_bearer"},
                "rbac_auth_source": {
                    "kind": "rbac_matrix",
                    "cases": rbac_cases,
                    "provided_bearer_count": 7,
                    "distinct_provided_bearer_count": 7,
                    "distinct_provided_bearers": True,
                    "duplicate_bearer_label_groups": [],
                },
                "include_writes": True,
                "include_rbac_matrix": True,
                "include_dry_run": True,
                "include_approval_race": True,
                "include_two_man_race": True,
                "summary": {
                    "total": 65,
                    "passed": 65,
                    "failed": 0,
                    "rbac_matrix_probes": 56,
                    "rbac_write_probes": 32,
                    "rbac_write_side_effect_proofs": 32,
                    "dry_run_probes": 7,
                    "approval_race_probes": 1,
                    "approval_race_bounded": True,
                    "two_man_race_probes": 1,
                    "two_man_race_operator_scoped": True,
                    "live_capital_side_effects": False,
                },
                "rbac_matrix": _strict_rbac_matrix_items(mismatched_write_marker_link=True),
                "dry_run": _strict_dry_run_items(),
                "approval_race": _strict_approval_race_item(),
                "two_man_race": _strict_two_man_race_item(),
            }
        ),
        encoding="utf-8",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PANTHEON_AUDIT_OUT_DIR",
            "PANTHEON_RELEASE_GATE_CHECKLIST_OUT",
            "PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE",
        }
    }
    env.update(
        {
            "PANTHEON_FRONTEND_SHA": "f" * 40,
            "PANTHEON_BFF_SHA": "b" * 40,
            "PANTHEON_BFF_BASE_URL": "https://bff.example.test",
        }
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout

    summary = json.loads(
        (current_run / "release-gate-summary.json").read_text(encoding="utf-8")
    )
    rbac_check = next(
        check
        for check in summary["gates"]["3"]
        if check["label"] == "Authenticated: strict bearer RBAC matrix evidence passed."
    )
    assert rbac_check["status"] == "fail"
    assert rbac_check["note"] == "strict:true bearer:true rbac:56/56 matrixCoverage:56/56 detailLinks:56/56 providedCases:7/7 distinctBearers:7/7 writeSideEffectProofs:32/32 writeMarkerLinks:31/32"

def test_release_gate_rejects_strict_two_man_race_without_operator_scope(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "execute-plans" / "scripts" / "aggregate-release-gate.mjs"
    current_run = tmp_path / ".lovable" / "audits" / "current-run"
    current_run.mkdir(parents=True)

    rbac_labels = ["viewer", "operator", "reviewer", "approver", "admin", "empty", "unknown"]
    rbac_cases = {"anonymous": {"kind": "anonymous"}}
    rbac_cases.update({label: {"kind": "provided_bearer", "sha256_12": f"rbac-{label}-hash"} for label in rbac_labels})
    (current_run / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-LUV-AUTHED-LIVE-001",
                "strict_live_evidence": True,
                "auth_source": {"kind": "provided_bearer"},
                "rbac_auth_source": {
                    "kind": "rbac_matrix",
                    "cases": rbac_cases,
                    "provided_bearer_count": 7,
                    "distinct_provided_bearer_count": 7,
                    "distinct_provided_bearers": True,
                    "duplicate_bearer_label_groups": [],
                },
                "include_writes": True,
                "include_rbac_matrix": True,
                "include_dry_run": True,
                "include_approval_race": True,
                "include_two_man_race": True,
                "summary": {
                    "total": 65,
                    "passed": 65,
                    "failed": 0,
                    "rbac_matrix_probes": 56,
                    "rbac_write_probes": 32,
                    "rbac_write_side_effect_proofs": 32,
                    "dry_run_probes": 7,
                    "approval_race_probes": 1,
                    "approval_race_bounded": True,
                    "two_man_race_probes": 1,
                    "two_man_race_operator_scoped": False,
                    "live_capital_side_effects": False,
                },
                "rbac_matrix": _strict_rbac_matrix_items(),
                "dry_run": _strict_dry_run_items(),
                "approval_race": _strict_approval_race_item(),
                "two_man_race": _strict_two_man_race_item(operator_scoped=False),
            }
        ),
        encoding="utf-8",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PANTHEON_AUDIT_OUT_DIR",
            "PANTHEON_RELEASE_GATE_CHECKLIST_OUT",
            "PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE",
        }
    }
    env.update(
        {
            "PANTHEON_FRONTEND_SHA": "f" * 40,
            "PANTHEON_BFF_SHA": "b" * 40,
            "PANTHEON_BFF_BASE_URL": "https://bff.example.test",
        }
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout

    summary = json.loads(
        (current_run / "release-gate-summary.json").read_text(encoding="utf-8")
    )
    two_man_check = next(
        check
        for check in summary["gates"]["3"]
        if check["label"] == "Authenticated: strict two-man-sign race evidence is operator-scoped."
    )
    assert two_man_check["status"] == "fail"
    assert two_man_check["note"] == "strict:true operatorScoped:false accepted:1 replayed:0 commandIds:1/2 detailAccepted:1/2 detailReplayed:0/0 detailCommandIds:1/2 results:1/2 targetLinks:1/2 signatureLinks:1/2 tokenPair:true tokenPairDistinct:true"


def test_release_gate_rejects_strict_dry_run_without_required_family_coverage(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "execute-plans" / "scripts" / "aggregate-release-gate.mjs"
    current_run = tmp_path / ".lovable" / "audits" / "current-run"
    current_run.mkdir(parents=True)

    rbac_labels = ["viewer", "operator", "reviewer", "approver", "admin", "empty", "unknown"]
    rbac_cases = {"anonymous": {"kind": "anonymous"}}
    rbac_cases.update({label: {"kind": "provided_bearer", "sha256_12": f"rbac-{label}-hash"} for label in rbac_labels})
    dry_run = _strict_dry_run_items()
    for item in dry_run:
        if item["family"] == "dry-run-v5-intervention-claim":
            item["family"] = "dry-run-strategy-create"
            item["extracted"] = {"data.id": "strategy-dry-run-001"}
            break

    (current_run / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-LUV-AUTHED-LIVE-001",
                "strict_live_evidence": True,
                "auth_source": {"kind": "provided_bearer"},
                "rbac_auth_source": {
                    "kind": "rbac_matrix",
                    "cases": rbac_cases,
                    "provided_bearer_count": 7,
                    "distinct_provided_bearer_count": 7,
                    "distinct_provided_bearers": True,
                    "duplicate_bearer_label_groups": [],
                },
                "include_writes": True,
                "include_rbac_matrix": True,
                "include_dry_run": True,
                "include_approval_race": True,
                "include_two_man_race": True,
                "summary": {
                    "total": 65,
                    "passed": 65,
                    "failed": 0,
                    "rbac_matrix_probes": 56,
                    "rbac_write_probes": 32,
                    "rbac_write_side_effect_proofs": 32,
                    "dry_run_probes": 7,
                    "approval_race_probes": 1,
                    "approval_race_bounded": True,
                    "two_man_race_probes": 1,
                    "two_man_race_operator_scoped": True,
                    "live_capital_side_effects": False,
                },
                "rbac_matrix": _strict_rbac_matrix_items(),
                "dry_run": dry_run,
                "approval_race": _strict_approval_race_item(),
                "two_man_race": _strict_two_man_race_item(),
            }
        ),
        encoding="utf-8",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PANTHEON_AUDIT_OUT_DIR",
            "PANTHEON_RELEASE_GATE_CHECKLIST_OUT",
            "PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE",
        }
    }
    env.update(
        {
            "PANTHEON_FRONTEND_SHA": "f" * 40,
            "PANTHEON_BFF_SHA": "b" * 40,
            "PANTHEON_BFF_BASE_URL": "https://bff.example.test",
        }
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout

    summary = json.loads(
        (current_run / "release-gate-summary.json").read_text(encoding="utf-8")
    )
    dry_run_check = next(
        check
        for check in summary["gates"]["3"]
        if check["label"] == "Authenticated: strict live dry-run evidence has BffErrorEnvelope and no side effects."
    )
    assert dry_run_check["status"] == "fail"
    assert dry_run_check["note"] == "strict:true dryRun:7/7 familyCoverage:6/7 invalidEnvelope:true readbackLinked:true sideEffectProofs:7/7 sideEffects:none"


def test_release_gate_rejects_strict_dry_run_with_unlinked_readback_target(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "execute-plans" / "scripts" / "aggregate-release-gate.mjs"
    current_run = tmp_path / ".lovable" / "audits" / "current-run"
    current_run.mkdir(parents=True)

    rbac_labels = ["viewer", "operator", "reviewer", "approver", "admin", "empty", "unknown"]
    rbac_cases = {"anonymous": {"kind": "anonymous"}}
    rbac_cases.update({label: {"kind": "provided_bearer", "sha256_12": f"rbac-{label}-hash"} for label in rbac_labels})

    (current_run / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-LUV-AUTHED-LIVE-001",
                "strict_live_evidence": True,
                "auth_source": {"kind": "provided_bearer"},
                "rbac_auth_source": {
                    "kind": "rbac_matrix",
                    "cases": rbac_cases,
                    "provided_bearer_count": 7,
                    "distinct_provided_bearer_count": 7,
                    "distinct_provided_bearers": True,
                    "duplicate_bearer_label_groups": [],
                },
                "include_writes": True,
                "include_rbac_matrix": True,
                "include_dry_run": True,
                "include_approval_race": True,
                "include_two_man_race": True,
                "summary": {
                    "total": 65,
                    "passed": 65,
                    "failed": 0,
                    "rbac_matrix_probes": 56,
                    "rbac_write_probes": 32,
                    "rbac_write_side_effect_proofs": 32,
                    "dry_run_probes": 7,
                    "approval_race_probes": 1,
                    "approval_race_bounded": True,
                    "two_man_race_probes": 1,
                    "two_man_race_operator_scoped": True,
                    "live_capital_side_effects": False,
                },
                "rbac_matrix": _strict_rbac_matrix_items(),
                "dry_run": _strict_dry_run_items(mismatched_readback_target=True),
                "approval_race": _strict_approval_race_item(),
                "two_man_race": _strict_two_man_race_item(),
            }
        ),
        encoding="utf-8",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PANTHEON_AUDIT_OUT_DIR",
            "PANTHEON_RELEASE_GATE_CHECKLIST_OUT",
            "PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE",
        }
    }
    env.update(
        {
            "PANTHEON_FRONTEND_SHA": "f" * 40,
            "PANTHEON_BFF_SHA": "b" * 40,
            "PANTHEON_BFF_BASE_URL": "https://bff.example.test",
        }
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout

    summary = json.loads(
        (current_run / "release-gate-summary.json").read_text(encoding="utf-8")
    )
    dry_run_check = next(
        check
        for check in summary["gates"]["3"]
        if check["label"] == "Authenticated: strict live dry-run evidence has BffErrorEnvelope and no side effects."
    )
    assert dry_run_check["status"] == "fail"
    assert dry_run_check["note"] == "strict:true dryRun:7/7 familyCoverage:7/7 invalidEnvelope:true readbackLinked:false sideEffectProofs:7/7 sideEffects:none"


def test_release_gate_rejects_strict_dry_run_without_per_probe_side_effect_proofs(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "execute-plans" / "scripts" / "aggregate-release-gate.mjs"
    current_run = tmp_path / ".lovable" / "audits" / "current-run"
    current_run.mkdir(parents=True)

    rbac_labels = ["viewer", "operator", "reviewer", "approver", "admin", "empty", "unknown"]
    rbac_cases = {"anonymous": {"kind": "anonymous"}}
    rbac_cases.update({label: {"kind": "provided_bearer", "sha256_12": f"rbac-{label}-hash"} for label in rbac_labels})
    (current_run / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-LUV-AUTHED-LIVE-001",
                "strict_live_evidence": True,
                "auth_source": {"kind": "provided_bearer"},
                "rbac_auth_source": {
                    "kind": "rbac_matrix",
                    "cases": rbac_cases,
                    "provided_bearer_count": 7,
                    "distinct_provided_bearer_count": 7,
                    "distinct_provided_bearers": True,
                    "duplicate_bearer_label_groups": [],
                },
                "include_writes": True,
                "include_rbac_matrix": True,
                "include_dry_run": True,
                "include_approval_race": True,
                "include_two_man_race": True,
                "summary": {
                    "total": 65,
                    "passed": 65,
                    "failed": 0,
                    "rbac_matrix_probes": 56,
                    "rbac_write_probes": 32,
                    "rbac_write_side_effect_proofs": 32,
                    "dry_run_probes": 7,
                    "approval_race_probes": 1,
                    "approval_race_bounded": True,
                    "two_man_race_probes": 1,
                    "two_man_race_operator_scoped": True,
                    "live_capital_side_effects": False,
                },
                "rbac_matrix": _strict_rbac_matrix_items(),
                "dry_run": _strict_dry_run_items(with_side_effect_checks=False),
                "approval_race": _strict_approval_race_item(),
                "two_man_race": _strict_two_man_race_item(),
            }
        ),
        encoding="utf-8",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PANTHEON_AUDIT_OUT_DIR",
            "PANTHEON_RELEASE_GATE_CHECKLIST_OUT",
            "PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE",
        }
    }
    env.update(
        {
            "PANTHEON_FRONTEND_SHA": "f" * 40,
            "PANTHEON_BFF_SHA": "b" * 40,
            "PANTHEON_BFF_BASE_URL": "https://bff.example.test",
        }
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout

    summary = json.loads(
        (current_run / "release-gate-summary.json").read_text(encoding="utf-8")
    )
    dry_run_check = next(
        check
        for check in summary["gates"]["3"]
        if check["label"] == "Authenticated: strict live dry-run evidence has BffErrorEnvelope and no side effects."
    )
    assert dry_run_check["status"] == "fail"
    assert dry_run_check["note"] == "strict:true dryRun:7/7 familyCoverage:7/7 invalidEnvelope:true readbackLinked:false sideEffectProofs:0/7 sideEffects:none"


def test_release_gate_rejects_strict_approval_race_without_winner(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required to execute aggregate-release-gate.mjs")

    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "execute-plans" / "scripts" / "aggregate-release-gate.mjs"
    current_run = tmp_path / ".lovable" / "audits" / "current-run"
    current_run.mkdir(parents=True)

    rbac_labels = ["viewer", "operator", "reviewer", "approver", "admin", "empty", "unknown"]
    rbac_cases = {"anonymous": {"kind": "anonymous"}}
    rbac_cases.update({label: {"kind": "provided_bearer", "sha256_12": f"rbac-{label}-hash"} for label in rbac_labels})
    dry_run = _strict_dry_run_items()
    (current_run / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-LUV-AUTHED-LIVE-001",
                "strict_live_evidence": True,
                "auth_source": {"kind": "provided_bearer"},
                "rbac_auth_source": {
                    "kind": "rbac_matrix",
                    "cases": rbac_cases,
                    "provided_bearer_count": 7,
                    "distinct_provided_bearer_count": 7,
                    "distinct_provided_bearers": True,
                    "duplicate_bearer_label_groups": [],
                },
                "include_writes": True,
                "include_rbac_matrix": True,
                "include_dry_run": True,
                "include_approval_race": True,
                "include_two_man_race": True,
                "summary": {
                    "total": 65,
                    "passed": 65,
                    "failed": 0,
                    "rbac_matrix_probes": 56,
                    "rbac_write_probes": 32,
                    "rbac_write_side_effect_proofs": 32,
                    "dry_run_probes": 7,
                    "approval_race_probes": 1,
                    "approval_race_bounded": True,
                    "two_man_race_probes": 1,
                    "two_man_race_operator_scoped": True,
                    "live_capital_side_effects": False,
                },
                "rbac_matrix": _strict_rbac_matrix_items(),
                "dry_run": dry_run,
                "approval_race": _strict_approval_race_item(accepted_count=0, safe_error_count=2),
                "two_man_race": _strict_two_man_race_item(),
            }
        ),
        encoding="utf-8",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {
            "PANTHEON_AUDIT_OUT_DIR",
            "PANTHEON_RELEASE_GATE_CHECKLIST_OUT",
            "PANTHEON_RELEASE_GATE_CHECKLIST_TEMPLATE",
        }
    }
    env.update(
        {
            "PANTHEON_FRONTEND_SHA": "f" * 40,
            "PANTHEON_BFF_SHA": "b" * 40,
            "PANTHEON_BFF_BASE_URL": "https://bff.example.test",
        }
    )

    result = subprocess.run(
        ["node", str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout

    summary = json.loads(
        (current_run / "release-gate-summary.json").read_text(encoding="utf-8")
    )
    race_check = next(
        check
        for check in summary["gates"]["3"]
        if check["label"] == "Authenticated: strict multi-operator approval race evidence is bounded."
    )
    assert race_check["status"] == "fail"
    assert race_check["note"] == "strict:true bounded:true accepted:0 safeErrors:2 safeErrorEnvelope:2/1 results:2/2 targetLinks:2/2 duplicateWinners:false tokenPair:true tokenPairDistinct:true"
