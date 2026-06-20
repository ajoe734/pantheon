from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


def _strict_dry_run_items(*, with_side_effect_checks: bool = True):
    items = [
        {"family": "dry-run-strategy-create", "ok": True},
        {"family": "dry-run-strategy-create-readback-not-persisted", "ok": True, "error_envelope": True},
        {"family": "dry-run-ranking-formula-create", "ok": True},
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
        {"kind": "readback_not_persisted", "ok": True, "error_code": "RESOURCE_NOT_FOUND", "target_id_sha256_12": "abc123"},
        {
            "kind": "dry_run_preview_meta",
            "ok": True,
            "dryRun": True,
            "durable": False,
            "liveCapitalSideEffects": False,
        },
        {"kind": "readback_not_persisted", "ok": True, "error_code": "RESOURCE_NOT_FOUND", "target_id_sha256_12": "abc123"},
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


def _strict_two_man_race_item(*, operator_scoped: bool = True):
    return {
        "family": "two-man-race",
        "ok": operator_scoped,
        "operator_scoped": operator_scoped,
        "accepted_count": 2 if operator_scoped else 1,
        "replayed_count": 0,
        "distinct_command_ids": operator_scoped,
        "command_id_count": 2 if operator_scoped else 1,
        "token_source": {"kind": "provided_bearer_pair"},
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


def test_integration_gate_uploads_only_current_run_audits() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workflow = repo_root / "execute-plans" / ".github" / "workflows" / "pantheon-integration-gate.yml"
    text = workflow.read_text(encoding="utf-8")

    assert "PANTHEON_AUDIT_OUT_DIR: .lovable/audits/current-run" in text
    assert ".lovable/audits/current-run" in text
    assert ".lovable/audits/*.md" not in text
    assert "Strict SSE live soak" in text
    assert "scripts/probe_bff_sse_stream.py" in text
    assert "--strict-live-evidence" in text
    assert "--soak-seconds 75" in text
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
                "strict_live_evidence": True,
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
                    "bearer_polyfill": {
                        "ok": True,
                        "attempt_count": 2,
                        "cursors_advanced": True,
                        "duplicate_event_ids": [],
                        "missing_expected_event_ids": [],
                        "attempts": [{"ok": True}, {"ok": True}],
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
    assert sse_check["note"] == "strict:true soak:75s heartbeat:2/1 reconnect:2/2 duplicates:0 missingReplay:0"


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
                "strict_live_evidence": True,
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
    assert sse_check["note"] == "strict:true soak:75s heartbeat:2/1 reconnect:0/2 duplicates:0 missingReplay:0"


def test_root_bff_live_evidence_workflow_runs_strict_current_run_probes() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workflow = repo_root / ".github" / "workflows" / "bff-live-evidence-gate.yml"
    text = workflow.read_text(encoding="utf-8")

    assert "name: BFF Live Evidence Gate" in text
    assert "workflow_dispatch" in text
    assert "PANTHEON_AUDIT_OUT_DIR: .lovable/audits/current-run" in text
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
    assert "BFF-CONSOL-011-sse-replay-smoke.json" in text
    assert "execute-plans/scripts/aggregate-release-gate.mjs" in text
    assert "path: .lovable/audits/current-run" in text
    assert ".lovable/audits/*.md" not in text


def test_stage0_registered_workflow_can_dispatch_strict_live_evidence_mode() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workflow = repo_root / ".github" / "workflows" / "stage-0-ci.yml"
    text = workflow.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "mode:" in text
    assert "- live-evidence" in text
    assert "approval_race_id:" in text
    assert "soak_seconds:" in text
    assert "live-evidence:" in text
    assert "github.event_name == 'workflow_dispatch' && inputs.mode == 'live-evidence'" in text
    assert "github.event_name != 'workflow_dispatch' || inputs.mode != 'live-evidence'" in text
    assert "PANTHEON_AUDIT_OUT_DIR: .lovable/audits/current-run" in text
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
    assert "BFF-CONSOL-011-sse-replay-smoke.json" in text
    assert "execute-plans/scripts/aggregate-release-gate.mjs" in text
    assert "path: .lovable/audits/current-run" in text
    assert ".lovable/audits/*.md" not in text


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
                    "dry_run_probes": 7,
                    "approval_race_probes": 1,
                    "approval_race_bounded": True,
                    "two_man_race_probes": 1,
                    "two_man_race_operator_scoped": True,
                    "live_capital_side_effects": False,
                },
                "rbac_matrix": [{"family": f"rbac-{index}", "ok": True} for index in range(56)],
                "dry_run": dry_run,
                "approval_race": {
                    "family": "approval-race",
                    "ok": True,
                    "bounded": True,
                    "accepted_count": 1,
                    "safe_error_count": 1,
                    "duplicate_winners": False,
                    "token_source": {"kind": "provided_bearer_pair"},
                },
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
    assert rbac_check["note"] == "strict:true bearer:true rbac:56/56 providedCases:7/7 distinctBearers:7/7"
    assert dry_run_check["status"] == "pass"
    assert dry_run_check["note"] == "strict:true dryRun:7/7 invalidEnvelope:true sideEffectProofs:7/7 sideEffects:none"
    assert race_check["status"] == "pass"
    assert race_check["note"] == "strict:true bounded:true accepted:1 safeErrors:1 duplicateWinners:false tokenPair:true"
    assert two_man_check["status"] == "pass"
    assert two_man_check["note"] == "strict:true operatorScoped:true accepted:2 replayed:0 commandIds:2/2 tokenPair:true"


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
                    "dry_run_probes": 7,
                    "approval_race_probes": 1,
                    "approval_race_bounded": True,
                    "two_man_race_probes": 1,
                    "two_man_race_operator_scoped": True,
                    "live_capital_side_effects": False,
                },
                "rbac_matrix": [{"family": f"rbac-{index}", "ok": True} for index in range(56)],
                "dry_run": _strict_dry_run_items(),
                "approval_race": {
                    "family": "approval-race",
                    "ok": True,
                    "bounded": True,
                    "accepted_count": 1,
                    "safe_error_count": 1,
                    "duplicate_winners": False,
                    "token_source": {"kind": "provided_bearer_pair"},
                },
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
    assert rbac_check["note"] == "strict:true bearer:true rbac:56/56 providedCases:7/7 distinctBearers:6/7"


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
                    "dry_run_probes": 7,
                    "approval_race_probes": 1,
                    "approval_race_bounded": True,
                    "two_man_race_probes": 1,
                    "two_man_race_operator_scoped": False,
                    "live_capital_side_effects": False,
                },
                "rbac_matrix": [{"family": f"rbac-{index}", "ok": True} for index in range(56)],
                "dry_run": _strict_dry_run_items(),
                "approval_race": {
                    "family": "approval-race",
                    "ok": True,
                    "bounded": True,
                    "accepted_count": 1,
                    "safe_error_count": 1,
                    "duplicate_winners": False,
                    "token_source": {"kind": "provided_bearer_pair"},
                },
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
    assert two_man_check["note"] == "strict:true operatorScoped:false accepted:1 replayed:0 commandIds:1/2 tokenPair:true"


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
                    "dry_run_probes": 7,
                    "approval_race_probes": 1,
                    "approval_race_bounded": True,
                    "two_man_race_probes": 1,
                    "two_man_race_operator_scoped": True,
                    "live_capital_side_effects": False,
                },
                "rbac_matrix": [{"family": f"rbac-{index}", "ok": True} for index in range(56)],
                "dry_run": _strict_dry_run_items(with_side_effect_checks=False),
                "approval_race": {
                    "family": "approval-race",
                    "ok": True,
                    "bounded": True,
                    "accepted_count": 1,
                    "safe_error_count": 1,
                    "duplicate_winners": False,
                    "token_source": {"kind": "provided_bearer_pair"},
                },
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
    assert dry_run_check["note"] == "strict:true dryRun:7/7 invalidEnvelope:true sideEffectProofs:0/7 sideEffects:none"


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
                    "dry_run_probes": 7,
                    "approval_race_probes": 1,
                    "approval_race_bounded": True,
                    "two_man_race_probes": 1,
                    "two_man_race_operator_scoped": True,
                    "live_capital_side_effects": False,
                },
                "rbac_matrix": [{"family": f"rbac-{index}", "ok": True} for index in range(56)],
                "dry_run": dry_run,
                "approval_race": {
                    "family": "approval-race",
                    "ok": True,
                    "bounded": True,
                    "accepted_count": 0,
                    "safe_error_count": 2,
                    "duplicate_winners": False,
                    "token_source": {"kind": "provided_bearer_pair"},
                },
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
    assert race_check["note"] == "strict:true bounded:true accepted:0 safeErrors:2 duplicateWinners:false tokenPair:true"
