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


def run_preflight(tmp_path: Path, env: dict[str, str], approval_race_id: str = "") -> subprocess.CompletedProcess[str]:
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
    for name in (*REQUIRED_SECRET_ENV_VARS, "PANTHEON_BFF_BASE_URL", "APPROVAL_RACE_ID"):
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
    assert payload["secret_values_written"] is False
    assert payload["present"]["PANTHEON_BFF_BASE_URL"] is True
    assert payload["present"]["APPROVAL_RACE_ID"] is False
    assert payload["missing"] == [*REQUIRED_SECRET_ENV_VARS, "APPROVAL_RACE_ID"]


def test_preflight_passes_when_all_required_inputs_are_present(tmp_path: Path) -> None:
    env = clean_env()
    secret_values = {
        "PANTHEON_BFF_SMOKE_BEARER_TOKEN": "smoke-secret",
        "PANTHEON_BFF_RBAC_TOKENS_JSON": '{"viewer":"viewer-secret"}',
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
    assert all(payload["present"].values())
    for secret_value in secret_values.values():
        assert secret_value not in text
