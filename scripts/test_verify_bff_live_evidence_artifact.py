from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


RBAC_LABEL = "Authenticated: strict bearer RBAC matrix evidence passed."
DRY_RUN_LABEL = "Authenticated: strict live dry-run evidence has BffErrorEnvelope and no side effects."
APPROVAL_LABEL = "Authenticated: strict multi-operator approval race evidence is bounded."
TWO_MAN_LABEL = "Authenticated: strict two-man-sign race evidence is operator-scoped."
SSE_LABEL = "Authenticated: strict SSE soak observes heartbeat and no duplicate replay."
CURRENT_RUN_LABEL = "Evidence written to `.lovable/audits/current-run`."


def run_verifier(artifact_dir: Path) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[1]
    return subprocess.run(
        [sys.executable, str(repo_root / "scripts" / "verify_bff_live_evidence_artifact.py"), str(artifact_dir), "--json"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )


def write_summary(artifact_dir: Path, *, status: str = "pass") -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    gate3 = [
        {"label": SSE_LABEL, "status": status, "note": "sse evidence"},
        {"label": RBAC_LABEL, "status": status, "note": "rbac evidence"},
        {"label": DRY_RUN_LABEL, "status": status, "note": "dry run evidence"},
        {"label": APPROVAL_LABEL, "status": status, "note": "approval evidence"},
        {"label": TWO_MAN_LABEL, "status": status, "note": "two-man evidence"},
    ]
    gate7 = [{"label": CURRENT_RUN_LABEL, "status": "pass", "note": "3 audit file(s) found"}]
    (artifact_dir / "release-gate-summary.json").write_text(
        json.dumps({"overall": status, "gates": {"3": gate3, "7": gate7}}),
        encoding="utf-8",
    )


def write_strict_auth_json(artifact_dir: Path) -> None:
    (artifact_dir / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json").write_text(
        json.dumps(
            {
                "strict_live_evidence": True,
                "include_rbac_matrix": True,
                "include_dry_run": True,
                "include_approval_race": True,
                "include_two_man_race": True,
                "summary": {
                    "rbac_matrix_probes": 56,
                    "dry_run_probes": 7,
                    "approval_race_probes": 1,
                    "approval_race_bounded": True,
                    "two_man_race_probes": 1,
                    "two_man_race_operator_scoped": True,
                    "live_capital_side_effects": False,
                },
                "rbac_matrix": [{"ok": True} for _ in range(56)],
                "dry_run": [{"ok": True} for _ in range(7)],
                "approval_race": {"ok": True, "bounded": True},
                "two_man_race": {"ok": True, "operator_scoped": True},
            }
        ),
        encoding="utf-8",
    )


def write_strict_sse_json(artifact_dir: Path) -> None:
    (artifact_dir / "BFF-CONSOL-011-sse-replay-smoke.json").write_text(
        json.dumps(
            {
                "strict_live_evidence": True,
                "soak": {"seconds": 75.0},
                "reconnect_sequence": {"bearer_polyfill": {"ok": True, "attempt_count": 5}},
            }
        ),
        encoding="utf-8",
    )


def write_passing_artifact(artifact_dir: Path) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json").write_text(
        json.dumps(
            {
                "strict_live_evidence_preflight": True,
                "github_environment": "dev",
                "missing": [],
                "invalid": [],
                "secret_values_written": False,
            }
        ),
        encoding="utf-8",
    )
    write_summary(artifact_dir)
    write_strict_auth_json(artifact_dir)
    write_strict_sse_json(artifact_dir)


def test_verifier_accepts_complete_strict_live_artifact(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)

    result = run_verifier(artifact_dir)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["overall"] == "pass"
    assert payload["criteria"]["rbac_matrix"]["status"] == "pass"
    assert payload["criteria"]["dry_run_no_side_effects"]["status"] == "pass"
    assert payload["criteria"]["approval_race"]["status"] == "pass"
    assert payload["criteria"]["two_man_race"]["status"] == "pass"
    assert payload["criteria"]["sse_reconnect_soak"]["status"] == "pass"
    assert payload["criteria"]["current_run_only"]["status"] == "pass"
    assert payload["criteria"]["raw_secret_scan"]["status"] == "pass"


def test_verifier_fails_preflight_blocked_artifact(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json").write_text(
        json.dumps(
            {
                "strict_live_evidence_preflight": True,
                "github_environment": "dev",
                "missing": ["PANTHEON_BFF_SMOKE_BEARER_TOKEN", "PANTHEON_BFF_RBAC_TOKENS_JSON"],
                "invalid": [],
                "secret_values_written": False,
            }
        ),
        encoding="utf-8",
    )

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["overall"] == "fail"
    assert payload["criteria"]["preflight_ready"]["status"] == "fail"
    assert "PANTHEON_BFF_SMOKE_BEARER_TOKEN" in payload["criteria"]["preflight_ready"]["note"]
    assert payload["criteria"]["rbac_matrix"]["status"] == "missing"


def test_verifier_fails_when_preflight_secret_safety_flag_is_missing(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    (artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json").write_text(
        json.dumps(
            {
                "strict_live_evidence_preflight": True,
                "github_environment": "dev",
                "missing": [],
                "invalid": [],
            }
        ),
        encoding="utf-8",
    )

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["overall"] == "fail"
    assert payload["criteria"]["preflight_ready"]["status"] == "fail"
    assert "secret_values_written must be false" in payload["criteria"]["preflight_ready"]["note"]


def test_verifier_fails_when_preflight_reports_secret_values_written(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    preflight = json.loads((artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json").read_text(encoding="utf-8"))
    preflight["secret_values_written"] = True
    (artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json").write_text(
        json.dumps(preflight),
        encoding="utf-8",
    )

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["overall"] == "fail"
    assert payload["criteria"]["preflight_ready"]["status"] == "fail"
    assert "secret_values_written must be false" in payload["criteria"]["preflight_ready"]["note"]


def test_verifier_rejects_raw_bearer_material_without_echoing_secret(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    leaked = "Bearer live-secret-token-abc123456"
    (artifact_dir / "leaky.json").write_text(json.dumps({"Authorization": leaked}), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["overall"] == "fail"
    item = payload["criteria"]["raw_secret_scan"]
    assert item["status"] == "fail"
    assert "leaky.json:raw_bearer" in item["note"]
    assert "live-secret-token-abc123456" not in item["note"]


def test_verifier_rejects_jwt_shaped_material_without_echoing_secret(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    leaked = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiJsaXZlLXNtb2tlIn0."
        "signaturepart123456"
    )
    (artifact_dir / "jwt-leak.json").write_text(json.dumps({"token": leaked}), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["overall"] == "fail"
    item = payload["criteria"]["raw_secret_scan"]
    assert item["status"] == "fail"
    assert "jwt-leak.json:jwt" in item["note"]
    assert leaked not in item["note"]


def test_verifier_rejects_historical_audit_paths_even_when_checks_pass(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    historical = artifact_dir / "historical"
    historical.mkdir()
    (historical / "old-audit.json").write_text("{}", encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["overall"] == "fail"
    assert payload["criteria"]["current_run_only"]["status"] == "fail"
    assert "historical/old-audit.json" in payload["criteria"]["current_run_only"]["note"]


def test_verifier_rejects_short_sse_reconnect_even_when_summary_claims_pass(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    (artifact_dir / "BFF-CONSOL-011-sse-replay-smoke.json").write_text(
        json.dumps(
            {
                "strict_live_evidence": True,
                "soak": {"seconds": 30.0},
                "reconnect_sequence": {"bearer_polyfill": {"ok": True, "attempt_count": 1}},
            }
        ),
        encoding="utf-8",
    )

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["criteria"]["sse_reconnect_soak"]["status"] == "fail"
    assert "soak:30/75" in payload["criteria"]["sse_reconnect_soak"]["note"]
