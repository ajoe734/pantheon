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


def run_preflight(
    tmp_path: Path,
    env: dict[str, str],
    approval_race_id: str = "",
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
    for name in (*REQUIRED_SECRET_ENV_VARS, "PANTHEON_BFF_BASE_URL", "APPROVAL_RACE_ID", "SOAK_SECONDS"):
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
    assert payload["target_url"] == "https://bff.example.test"
    assert payload["soak_seconds"] == "75"
    assert payload["min_soak_seconds"] == 75.0
    assert payload["secret_values_written"] is False
    assert payload["present"]["PANTHEON_BFF_BASE_URL"] is True
    assert payload["present"]["APPROVAL_RACE_ID"] is False
    assert payload["present"]["SOAK_SECONDS"] is True
    assert payload["missing"] == [*REQUIRED_SECRET_ENV_VARS, "APPROVAL_RACE_ID"]
    assert payload["invalid"] == []
    assert payload["rbac_matrix"] == {
        "required_labels": list(RBAC_REQUIRED_LABELS),
        "present_labels": [],
        "missing_labels": list(RBAC_REQUIRED_LABELS),
        "provided_cases": 0,
        "expected_cases": len(RBAC_REQUIRED_LABELS),
    }
    assert payload["approval_race_tokens"] == {
        "token_a_present": False,
        "token_b_present": False,
        "distinct_bearers": False,
    }


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

    result = run_preflight(tmp_path, env, approval_race_id="appr-live-123")
    assert result.returncode == 0, result.stderr

    output = tmp_path / ".lovable" / "audits" / "current-run" / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    text = output.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert payload["missing"] == []
    assert payload["invalid"] == []
    assert all(payload["present"].values())
    assert payload["rbac_matrix"] == {
        "required_labels": list(RBAC_REQUIRED_LABELS),
        "present_labels": list(RBAC_REQUIRED_LABELS),
        "missing_labels": [],
        "provided_cases": len(RBAC_REQUIRED_LABELS),
        "expected_cases": len(RBAC_REQUIRED_LABELS),
    }
    assert payload["approval_race_tokens"] == {
        "token_a_present": True,
        "token_b_present": True,
        "distinct_bearers": True,
    }
    leaked_values = [
        "smoke-secret",
        *rbac_tokens.values(),
        "race-secret-a",
        "race-secret-b",
    ]
    for secret_value in leaked_values:
        assert secret_value not in text


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

    result = run_preflight(tmp_path, env, approval_race_id="appr-live-123", soak_seconds="1")
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

    result = run_preflight(tmp_path, env, approval_race_id="appr-live-123")
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

    result = run_preflight(tmp_path, env, approval_race_id="appr-live-123")
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

    result = run_preflight(tmp_path, env, approval_race_id="appr-live-123")
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
