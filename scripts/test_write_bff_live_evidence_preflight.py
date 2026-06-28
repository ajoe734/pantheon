from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REQUIRED_SECRET_ENV_VARS = (
    "PANTHEON_BFF_SMOKE_BEARER_TOKEN",
    "PANTHEON_BFF_RBAC_TOKENS_JSON",
    "PANTHEON_BFF_APPROVAL_RACE_TOKEN_A",
    "PANTHEON_BFF_APPROVAL_RACE_TOKEN_B",
)
RBAC_REQUIRED_LABELS = ("viewer", "operator", "reviewer", "approver", "admin", "empty", "unknown")
MIN_BEARER_TOKEN_LENGTH = 12

CROSS_SECRET_REQUIRED_SOURCES = (
    "smoke",
    *(f"rbac:{label}" for label in RBAC_REQUIRED_LABELS),
    "approval_race:a",
    "approval_race:b",
)


def run_preflight(
    tmp_path: Path,
    env: dict[str, str],
    approval_race_id: str = "",
    two_man_race_id: str = "",
    soak_seconds: str = "75",
) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[1]
    output = tmp_path / ".lovable" / "audits" / "current-run" / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    return subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "write_bff_live_evidence_preflight.py"),
            "--base-url",
            "https://bff.example.test",
            "--approval-race-id",
            approval_race_id,
            "--two-man-race-id",
            two_man_race_id,
            "--soak-seconds",
            soak_seconds,
            "--output",
            str(output),
        ],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def clean_env() -> dict[str, str]:
    env = os.environ.copy()
    for name in (
        *REQUIRED_SECRET_ENV_VARS,
        "PANTHEON_BFF_BASE_URL",
        "APPROVAL_RACE_ID",
        "TWO_MAN_RACE_ID",
        "SOAK_SECONDS",
        "GITHUB_REPOSITORY",
        "GITHUB_RUN_ID",
        "GITHUB_RUN_ATTEMPT",
        "GITHUB_WORKFLOW",
        "GITHUB_JOB",
        "GITHUB_REF",
        "GITHUB_REF_NAME",
        "GITHUB_SHA",
        "PANTHEON_LIVE_EVIDENCE_ENVIRONMENT",
    ):
        env.pop(name, None)
    return env


def test_preflight_writes_missing_inputs_without_secret_values(tmp_path: Path) -> None:
    result = run_preflight(tmp_path, clean_env())
    assert result.returncode == 1
    assert "Missing strict live evidence inputs" in result.stderr

    output = tmp_path / ".lovable" / "audits" / "current-run" / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["task_id"] == "BFF-LIVE-EVIDENCE-PREFLIGHT"
    assert payload["strict_live_evidence_preflight"] is True
    assert payload["strict_live_evidence_run"] == {
        "github_environment": "dev",
        "github_run_id": "",
        "github_run_attempt": "",
        "github_workflow": "",
        "github_job": "",
        "repository": "",
        "ref": "",
        "sha": "",
    }
    assert payload["target_url"] == "https://bff.example.test"
    assert payload["soak_seconds"] == "75"
    assert payload["min_soak_seconds"] == 75.0
    assert payload["secret_values_written"] is False
    assert payload["present"]["PANTHEON_BFF_BASE_URL"] is True
    assert payload["present"]["APPROVAL_RACE_ID"] is False
    assert payload["present"]["TWO_MAN_RACE_ID"] is False
    assert payload["present"]["SOAK_SECONDS"] is True
    assert payload["missing"] == [*REQUIRED_SECRET_ENV_VARS, "APPROVAL_RACE_ID", "TWO_MAN_RACE_ID"]
    assert payload["invalid"] == []
    assert payload["rbac_matrix"] == {
        "required_labels": list(RBAC_REQUIRED_LABELS),
        "present_labels": [],
        "missing_labels": list(RBAC_REQUIRED_LABELS),
        "provided_cases": 0,
        "expected_cases": len(RBAC_REQUIRED_LABELS),
        "distinct_bearers": False,
        "distinct_bearer_count": 0,
        "duplicate_label_groups": [],
    }
    assert payload["approval_race_tokens"] == {
        "token_a_present": False,
        "token_b_present": False,
        "distinct_bearers": False,
    }
    assert payload["cross_secret_bearers"] == {
        "required_sources": list(CROSS_SECRET_REQUIRED_SOURCES),
        "present_sources": [],
        "missing_sources": list(CROSS_SECRET_REQUIRED_SOURCES),
        "provided_sources": 0,
        "expected_sources": len(CROSS_SECRET_REQUIRED_SOURCES),
        "distinct_bearers": False,
        "distinct_bearer_count": 0,
        "duplicate_source_groups": [],
    }
    assert payload["bearer_shape"] == {
        "required_sources": list(CROSS_SECRET_REQUIRED_SOURCES),
        "checked_sources": [],
        "valid_sources": [],
        "invalid_sources": [],
        "min_length": MIN_BEARER_TOKEN_LENGTH,
        "placeholder_values_rejected": True,
    }
    remediation = payload["operator_remediation"]
    assert payload["github_environment"] == "dev"
    assert remediation["github_environment"] == "dev"
    assert remediation["repository"] == "ajoe734/pantheon"
    assert remediation["required_secret_names"] == list(REQUIRED_SECRET_ENV_VARS)
    assert remediation["missing_secret_names"] == list(REQUIRED_SECRET_ENV_VARS)
    assert remediation["missing_workflow_inputs"] == ["APPROVAL_RACE_ID", "TWO_MAN_RACE_ID"]
    assert len(remediation["secret_set_commands"]) == len(REQUIRED_SECRET_ENV_VARS)
    assert all("--env dev" in command for command in remediation["secret_set_commands"])
    assert all("/secure/path/" in command for command in remediation["secret_set_commands"])
    dispatch = remediation["workflow_dispatch"]
    assert dispatch["recommended_workflow"] == "Pantheon Stage 0 CI"
    assert dispatch["mode"] == "live-evidence"
    assert dispatch["environment"] == "dev"
    assert "gh workflow run \"Pantheon Stage 0 CI\"" in dispatch["run_command_template"]
    assert "-f environment=dev" in dispatch["run_command_template"]


def test_preflight_passes_when_all_required_inputs_are_present(tmp_path: Path) -> None:
    env = clean_env()
    rbac_tokens = {label: f"{label}-secret" for label in RBAC_REQUIRED_LABELS}
    secret_values = {
        "PANTHEON_BFF_SMOKE_BEARER_TOKEN": "smoke-secret",
        "PANTHEON_BFF_RBAC_TOKENS_JSON": json.dumps(rbac_tokens),
        "PANTHEON_BFF_APPROVAL_RACE_TOKEN_A": "race-secret-a",
        "PANTHEON_BFF_APPROVAL_RACE_TOKEN_B": "race-secret-b",
    }
    env.update(secret_values)
    env["GITHUB_REPOSITORY"] = "example/pantheon"
    env["PANTHEON_LIVE_EVIDENCE_ENVIRONMENT"] = "staging-live"

    result = run_preflight(
        tmp_path,
        env,
        approval_race_id="appr-live-123",
        two_man_race_id="int-live-123",
    )
    assert result.returncode == 0, result.stderr

    output = tmp_path / ".lovable" / "audits" / "current-run" / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    text = output.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert payload["missing"] == []
    assert payload["invalid"] == []
    assert all(payload["present"].values())
    assert payload["approval_race_id_present"] is True
    assert payload["two_man_race_id_present"] is True
    assert payload["rbac_matrix"] == {
        "required_labels": list(RBAC_REQUIRED_LABELS),
        "present_labels": list(RBAC_REQUIRED_LABELS),
        "missing_labels": [],
        "provided_cases": len(RBAC_REQUIRED_LABELS),
        "expected_cases": len(RBAC_REQUIRED_LABELS),
        "distinct_bearers": True,
        "distinct_bearer_count": len(RBAC_REQUIRED_LABELS),
        "duplicate_label_groups": [],
    }
    assert payload["approval_race_tokens"] == {
        "token_a_present": True,
        "token_b_present": True,
        "distinct_bearers": True,
    }
    assert payload["cross_secret_bearers"] == {
        "required_sources": list(CROSS_SECRET_REQUIRED_SOURCES),
        "present_sources": list(CROSS_SECRET_REQUIRED_SOURCES),
        "missing_sources": [],
        "provided_sources": len(CROSS_SECRET_REQUIRED_SOURCES),
        "expected_sources": len(CROSS_SECRET_REQUIRED_SOURCES),
        "distinct_bearers": True,
        "distinct_bearer_count": len(CROSS_SECRET_REQUIRED_SOURCES),
        "duplicate_source_groups": [],
    }
    assert payload["bearer_shape"] == {
        "required_sources": list(CROSS_SECRET_REQUIRED_SOURCES),
        "checked_sources": list(CROSS_SECRET_REQUIRED_SOURCES),
        "valid_sources": list(CROSS_SECRET_REQUIRED_SOURCES),
        "invalid_sources": [],
        "min_length": MIN_BEARER_TOKEN_LENGTH,
        "placeholder_values_rejected": True,
    }
    remediation = payload["operator_remediation"]
    assert payload["github_environment"] == "staging-live"
    assert remediation["repository"] == "example/pantheon"
    assert remediation["github_environment"] == "staging-live"
    assert remediation["missing_secret_names"] == []
    assert remediation["missing_workflow_inputs"] == []
    assert remediation["invalid_inputs"] == []
    assert all("--repo example/pantheon" in command for command in remediation["secret_set_commands"])
    assert all("--env staging-live" in command for command in remediation["secret_set_commands"])
    assert "-f environment=staging-live" in remediation["workflow_dispatch"]["run_command_template"]
    leaked_values = [
        "smoke-secret",
        *rbac_tokens.values(),
        "race-secret-a",
        "race-secret-b",
        "appr-live-123",
        "int-live-123",
    ]
    for secret_value in leaked_values:
        assert secret_value not in text


def test_preflight_rejects_placeholder_bearers_before_live_probes(tmp_path: Path) -> None:
    env = clean_env()
    rbac_tokens = {label: f"{label}-secret" for label in RBAC_REQUIRED_LABELS}
    rbac_tokens["operator"] = "dummy-token"
    env.update(
        {
            "PANTHEON_BFF_SMOKE_BEARER_TOKEN": "bearer redacted",
            "PANTHEON_BFF_RBAC_TOKENS_JSON": json.dumps(rbac_tokens),
            "PANTHEON_BFF_APPROVAL_RACE_TOKEN_A": "***",
            "PANTHEON_BFF_APPROVAL_RACE_TOKEN_B": "race-secret-b",
        }
    )

    result = run_preflight(
        tmp_path,
        env,
        approval_race_id="appr-live-123",
        two_man_race_id="int-live-123",
    )
    assert result.returncode == 1
    assert "PANTHEON_BFF_LIVE_EVIDENCE_BEARER_SHAPE" in result.stderr
    assert "smoke=placeholder_value" in result.stderr
    assert "rbac:operator=placeholder_prefix" in result.stderr
    assert "approval_race:a=placeholder_value" in result.stderr

    output = tmp_path / ".lovable" / "audits" / "current-run" / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    text = output.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert payload["missing"] == []
    assert payload["rbac_matrix"]["distinct_bearers"] is True
    assert payload["cross_secret_bearers"]["distinct_bearers"] is True
    assert payload["bearer_shape"]["invalid_sources"] == [
        {"source": "smoke", "reason": "placeholder_value"},
        {"source": "rbac:operator", "reason": "placeholder_prefix"},
        {"source": "approval_race:a", "reason": "placeholder_value"},
    ]
    assert payload["bearer_shape"]["min_length"] == MIN_BEARER_TOKEN_LENGTH
    assert payload["invalid"] == [
        {
            "name": "PANTHEON_BFF_LIVE_EVIDENCE_BEARER_SHAPE",
            "reason": "bearer tokens must not be placeholders and must be at least 12 characters: "
            "smoke=placeholder_value; rbac:operator=placeholder_prefix; "
            "approval_race:a=placeholder_value",
        }
    ]
    for leaked_value in ["redacted", "dummy-token", "race-secret-b"]:
        assert leaked_value not in text


def test_preflight_rejects_short_bearer_tokens_before_live_probes(tmp_path: Path) -> None:
    env = clean_env()
    rbac_tokens = {label: f"{label}-secret" for label in RBAC_REQUIRED_LABELS}
    rbac_tokens["reviewer"] = "tiny"
    env.update(
        {
            "PANTHEON_BFF_SMOKE_BEARER_TOKEN": "smoke-secret",
            "PANTHEON_BFF_RBAC_TOKENS_JSON": json.dumps(rbac_tokens),
            "PANTHEON_BFF_APPROVAL_RACE_TOKEN_A": "race-secret-a",
            "PANTHEON_BFF_APPROVAL_RACE_TOKEN_B": "race-secret-b",
        }
    )

    result = run_preflight(
        tmp_path,
        env,
        approval_race_id="appr-live-123",
        two_man_race_id="int-live-123",
    )
    assert result.returncode == 1
    assert "PANTHEON_BFF_LIVE_EVIDENCE_BEARER_SHAPE" in result.stderr
    assert "rbac:reviewer=too_short_min_12" in result.stderr

    output = tmp_path / ".lovable" / "audits" / "current-run" / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    text = output.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert payload["missing"] == []
    assert payload["rbac_matrix"]["distinct_bearers"] is True
    assert payload["cross_secret_bearers"]["distinct_bearers"] is True
    assert payload["bearer_shape"]["invalid_sources"] == [
        {"source": "rbac:reviewer", "reason": "too_short_min_12"},
    ]
    assert payload["invalid"] == [
        {
            "name": "PANTHEON_BFF_LIVE_EVIDENCE_BEARER_SHAPE",
            "reason": "bearer tokens must not be placeholders and must be at least 12 characters: "
            "rbac:reviewer=too_short_min_12",
        }
    ]
    assert "tiny" not in text


def test_preflight_rejects_short_sse_soak_before_live_write_steps(tmp_path: Path) -> None:
    env = clean_env()
    env.update(
        {
            "PANTHEON_BFF_SMOKE_BEARER_TOKEN": "smoke-secret",
            "PANTHEON_BFF_RBAC_TOKENS_JSON": json.dumps(
                {label: f"{label}-secret" for label in RBAC_REQUIRED_LABELS}
            ),
            "PANTHEON_BFF_APPROVAL_RACE_TOKEN_A": "race-secret-a",
            "PANTHEON_BFF_APPROVAL_RACE_TOKEN_B": "race-secret-b",
        }
    )

    result = run_preflight(
        tmp_path,
        env,
        approval_race_id="appr-live-123",
        two_man_race_id="int-live-123",
        soak_seconds="1",
    )
    assert result.returncode == 1
    assert "Invalid strict live evidence inputs" in result.stderr

    output = tmp_path / ".lovable" / "audits" / "current-run" / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["missing"] == []
    assert payload["present"]["SOAK_SECONDS"] is True
    assert payload["soak_seconds"] == "1"
    assert payload["invalid"] == [
        {"name": "SOAK_SECONDS", "reason": "SOAK_SECONDS must be >= 75"}
    ]


def test_preflight_rejects_incomplete_rbac_matrix_before_live_probes(tmp_path: Path) -> None:
    env = clean_env()
    env.update(
        {
            "PANTHEON_BFF_SMOKE_BEARER_TOKEN": "smoke-secret",
            "PANTHEON_BFF_RBAC_TOKENS_JSON": json.dumps({"viewer": "viewer-secret"}),
            "PANTHEON_BFF_APPROVAL_RACE_TOKEN_A": "race-secret-a",
            "PANTHEON_BFF_APPROVAL_RACE_TOKEN_B": "race-secret-b",
        }
    )

    result = run_preflight(
        tmp_path,
        env,
        approval_race_id="appr-live-123",
        two_man_race_id="int-live-123",
    )
    assert result.returncode == 1
    assert "Invalid strict live evidence inputs" in result.stderr

    output = tmp_path / ".lovable" / "audits" / "current-run" / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    text = output.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert payload["missing"] == []
    assert payload["rbac_matrix"] == {
        "required_labels": list(RBAC_REQUIRED_LABELS),
        "present_labels": ["viewer"],
        "missing_labels": ["operator", "reviewer", "approver", "admin", "empty", "unknown"],
        "provided_cases": 1,
        "expected_cases": len(RBAC_REQUIRED_LABELS),
        "distinct_bearers": False,
        "distinct_bearer_count": 1,
        "duplicate_label_groups": [],
    }
    assert payload["invalid"] == [
        {
            "name": "PANTHEON_BFF_RBAC_TOKENS_JSON",
            "reason": "missing bearer tokens for labels: operator, reviewer, approver, admin, empty, unknown",
        }
    ]
    assert "viewer-secret" not in text


def test_preflight_rejects_malformed_rbac_json_with_safe_artifact(tmp_path: Path) -> None:
    env = clean_env()
    env.update(
        {
            "PANTHEON_BFF_SMOKE_BEARER_TOKEN": "smoke-secret",
            "PANTHEON_BFF_RBAC_TOKENS_JSON": "{not-json",
            "PANTHEON_BFF_APPROVAL_RACE_TOKEN_A": "race-secret-a",
            "PANTHEON_BFF_APPROVAL_RACE_TOKEN_B": "race-secret-b",
        }
    )

    result = run_preflight(
        tmp_path,
        env,
        approval_race_id="appr-live-123",
        two_man_race_id="int-live-123",
    )
    assert result.returncode == 1
    assert "PANTHEON_BFF_RBAC_TOKENS_JSON" in result.stderr

    output = tmp_path / ".lovable" / "audits" / "current-run" / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    text = output.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert payload["missing"] == []
    assert payload["rbac_matrix"]["provided_cases"] == 0
    assert payload["rbac_matrix"]["missing_labels"] == list(RBAC_REQUIRED_LABELS)
    assert payload["invalid"][0]["name"] == "PANTHEON_BFF_RBAC_TOKENS_JSON"
    assert payload["invalid"][0]["reason"].startswith("must be valid JSON")
    assert "race-secret-a" not in text
    assert "race-secret-b" not in text


def test_preflight_rejects_duplicate_rbac_bearers_before_live_matrix(tmp_path: Path) -> None:
    env = clean_env()
    rbac_tokens = {label: f"{label}-secret" for label in RBAC_REQUIRED_LABELS}
    rbac_tokens["operator"] = rbac_tokens["viewer"]
    env.update(
        {
            "PANTHEON_BFF_SMOKE_BEARER_TOKEN": "smoke-secret",
            "PANTHEON_BFF_RBAC_TOKENS_JSON": json.dumps(rbac_tokens),
            "PANTHEON_BFF_APPROVAL_RACE_TOKEN_A": "race-secret-a",
            "PANTHEON_BFF_APPROVAL_RACE_TOKEN_B": "race-secret-b",
        }
    )

    result = run_preflight(
        tmp_path,
        env,
        approval_race_id="appr-live-123",
        two_man_race_id="int-live-123",
    )
    assert result.returncode == 1
    assert "PANTHEON_BFF_RBAC_TOKENS_JSON" in result.stderr
    assert "distinct per RBAC label" in result.stderr

    output = tmp_path / ".lovable" / "audits" / "current-run" / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    text = output.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert payload["missing"] == []
    assert payload["rbac_matrix"]["distinct_bearers"] is False
    assert payload["rbac_matrix"]["distinct_bearer_count"] == len(RBAC_REQUIRED_LABELS) - 1
    assert payload["rbac_matrix"]["duplicate_label_groups"] == [["viewer", "operator"]]
    assert payload["invalid"] == [
        {
            "name": "PANTHEON_BFF_RBAC_TOKENS_JSON",
            "reason": "bearer tokens must be distinct per RBAC label: viewer/operator",
        }
    ]
    assert "viewer-secret" not in text


def test_preflight_rejects_cross_secret_bearer_reuse_before_live_probes(tmp_path: Path) -> None:
    env = clean_env()
    rbac_tokens = {label: f"{label}-secret" for label in RBAC_REQUIRED_LABELS}
    rbac_tokens["viewer"] = "shared-live-secret"
    env.update(
        {
            "PANTHEON_BFF_SMOKE_BEARER_TOKEN": "Bearer shared-live-secret",
            "PANTHEON_BFF_RBAC_TOKENS_JSON": json.dumps(rbac_tokens),
            "PANTHEON_BFF_APPROVAL_RACE_TOKEN_A": "race-secret-a",
            "PANTHEON_BFF_APPROVAL_RACE_TOKEN_B": "operator-secret",
        }
    )

    result = run_preflight(
        tmp_path,
        env,
        approval_race_id="appr-live-123",
        two_man_race_id="int-live-123",
    )
    assert result.returncode == 1
    assert "PANTHEON_BFF_LIVE_EVIDENCE_BEARERS" in result.stderr
    assert "smoke/rbac:viewer" in result.stderr
    assert "rbac:operator/approval_race:b" in result.stderr

    output = tmp_path / ".lovable" / "audits" / "current-run" / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    text = output.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert payload["missing"] == []
    assert payload["cross_secret_bearers"]["provided_sources"] == len(CROSS_SECRET_REQUIRED_SOURCES)
    assert payload["cross_secret_bearers"]["distinct_bearers"] is False
    assert payload["cross_secret_bearers"]["distinct_bearer_count"] == len(CROSS_SECRET_REQUIRED_SOURCES) - 2
    assert payload["cross_secret_bearers"]["duplicate_source_groups"] == [
        ["smoke", "rbac:viewer"],
        ["rbac:operator", "approval_race:b"],
    ]
    assert payload["invalid"] == [
        {
            "name": "PANTHEON_BFF_LIVE_EVIDENCE_BEARERS",
            "reason": "bearer tokens must be unique across smoke, RBAC, and approval race sources: "
            "smoke/rbac:viewer; rbac:operator/approval_race:b",
        }
    ]
    for secret_value in ["shared-live-secret", "operator-secret", "race-secret-a"]:
        assert secret_value not in text


def test_preflight_rejects_same_approval_race_bearer_before_live_race(tmp_path: Path) -> None:
    env = clean_env()
    rbac_tokens = {label: f"{label}-secret" for label in RBAC_REQUIRED_LABELS}
    env.update(
        {
            "PANTHEON_BFF_SMOKE_BEARER_TOKEN": "smoke-secret",
            "PANTHEON_BFF_RBAC_TOKENS_JSON": json.dumps(rbac_tokens),
            "PANTHEON_BFF_APPROVAL_RACE_TOKEN_A": "Bearer same-race-secret",
            "PANTHEON_BFF_APPROVAL_RACE_TOKEN_B": "same-race-secret",
        }
    )

    result = run_preflight(
        tmp_path,
        env,
        approval_race_id="appr-live-123",
        two_man_race_id="int-live-123",
    )
    assert result.returncode == 1
    assert "PANTHEON_BFF_APPROVAL_RACE_TOKEN_A/B" in result.stderr

    output = tmp_path / ".lovable" / "audits" / "current-run" / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    text = output.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert payload["missing"] == []
    assert payload["approval_race_tokens"] == {
        "token_a_present": True,
        "token_b_present": True,
        "distinct_bearers": False,
    }
    assert payload["invalid"] == [
        {
            "name": "PANTHEON_BFF_APPROVAL_RACE_TOKEN_A/B",
            "reason": "must be distinct bearer tokens for two operators",
        }
    ]
    assert "same-race-secret" not in text
