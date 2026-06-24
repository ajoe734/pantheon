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


def dry_run_side_effect_entries() -> list[dict[str, object]]:
    def meta(kind: str) -> dict[str, object]:
        return {
            "kind": kind,
            "ok": True,
            "dryRun": True,
            "durable": False,
            "liveCapitalSideEffects": False,
        }

    def readback(family: str, digest: str) -> dict[str, object]:
        return {
            "family": f"{family}-readback-not-persisted",
            "ok": True,
            "error_envelope": True,
            "error_code": "RESOURCE_NOT_FOUND",
            "side_effect_check": {
                "kind": "readback_not_persisted",
                "ok": True,
                "target_family": family,
                "target_id_sha256_12": digest,
                "error_code": "RESOURCE_NOT_FOUND",
            },
        }

    def validation(family: str) -> dict[str, object]:
        return {
            "family": family,
            "ok": True,
            "error_envelope": True,
            "error_code": "VALIDATION_FAILED",
            "side_effect_check": {
                "kind": "validation_rejected_before_persistence",
                "ok": True,
                "error_code": "VALIDATION_FAILED",
            },
        }

    return [
        {"family": "dry-run-strategy-create", "ok": True, "error_envelope": False, "side_effect_check": meta("dry_run_preview_meta")},
        readback("dry-run-strategy-create", "abc123abc123"),
        {"family": "dry-run-ranking-formula-create", "ok": True, "error_envelope": False, "side_effect_check": meta("dry_run_preview_meta")},
        readback("dry-run-ranking-formula-create", "def456def456"),
        {"family": "dry-run-v5-intervention-claim", "ok": True, "error_envelope": False, "side_effect_check": meta("dry_run_command_meta")},
        validation("dry-run-invalid-strategy"),
        validation("dry-run-invalid-ranking-formula"),
    ]


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
                "dry_run": dry_run_side_effect_entries(),
                "approval_race": {"ok": True, "bounded": True},
                "two_man_race": {"ok": True, "operator_scoped": True},
            }
        ),
        encoding="utf-8",
    )


def strict_sse_reconnect_bearer(attempt_count: int = 5) -> dict[str, object]:
    expected_event_ids = [f"evt-{index}" for index in range(2, 2 + attempt_count)]
    cursor_event_ids = [f"evt-{index}" for index in range(1, 1 + attempt_count)]
    attempts = []
    for index, expected_event_id in enumerate(expected_event_ids):
        cursor_event_id = cursor_event_ids[index]
        attempts.append(
            {
                "mode": "bearer_polyfill",
                "ok": True,
                "attempt": index + 1,
                "cursor_event_id": cursor_event_id,
                "expected_replayed_event_id": expected_event_id,
                "observed_replayed_event_id": expected_event_id,
                "replayed_expected_event": True,
                "lineage_checks": {
                    "last_event_id_sent": True,
                    "response_channel_ok": True,
                    "response_replay_supported": True,
                    "sse_id_line_matches_observed": True,
                    "data_id_matches_observed": True,
                    "shape_id_matches_data": True,
                    "shape_event_matches_type": True,
                    "data_json_parse_ok": True,
                },
            }
        )
    return {
        "mode": "bearer_polyfill",
        "ok": True,
        "attempt_count": attempt_count,
        "cursor_event_ids": cursor_event_ids,
        "expected_event_ids": expected_event_ids,
        "observed_event_ids": list(expected_event_ids),
        "missing_expected_event_ids": [],
        "duplicate_event_ids": [],
        "cursors_advanced": True,
        "attempts": attempts,
    }


def write_strict_sse_json(artifact_dir: Path) -> None:
    (artifact_dir / "BFF-CONSOL-011-sse-replay-smoke.json").write_text(
        json.dumps(
            {
                "strict_live_evidence": True,
                "strict_live_evidence_requirements": {
                    "min_soak_seconds": 75,
                    "min_heartbeats": 1,
                    "min_reconnect_attempts": 5,
                },
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
                "reconnect_sequence": {"bearer_polyfill": strict_sse_reconnect_bearer()},
            }
        ),
        encoding="utf-8",
    )


def write_passing_artifact(artifact_dir: Path) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-LIVE-EVIDENCE-PREFLIGHT",
                "strict_live_evidence_preflight": True,
                "github_environment": "dev",
                "missing": [],
                "invalid": [],
                "output_scope": ".lovable/audits/current-run",
                "ref": "refs/heads/dev",
                "sha": "a" * 40,
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


def test_verifier_rejects_dry_run_missing_side_effect_detail_even_when_summary_passes(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    auth_path = artifact_dir / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json"
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    auth["dry_run"][0].pop("side_effect_check")
    auth_path.write_text(json.dumps(auth), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["dry_run_no_side_effects"]
    assert item["status"] == "fail"
    assert "side-effect-check-missing" in item["note"]


def test_verifier_rejects_dry_run_validation_without_error_envelope(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    auth_path = artifact_dir / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json"
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    invalid = next(
        item
        for item in auth["dry_run"]
        if item["side_effect_check"]["kind"] == "validation_rejected_before_persistence"
    )
    invalid["error_envelope"] = False
    auth_path.write_text(json.dumps(auth), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["dry_run_no_side_effects"]
    assert item["status"] == "fail"
    assert "validation-error-envelope" in item["note"]


def test_verifier_fails_preflight_blocked_artifact(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-LIVE-EVIDENCE-PREFLIGHT",
                "strict_live_evidence_preflight": True,
                "github_environment": "dev",
                "missing": ["PANTHEON_BFF_SMOKE_BEARER_TOKEN", "PANTHEON_BFF_RBAC_TOKENS_JSON"],
                "invalid": [],
                "output_scope": ".lovable/audits/current-run",
                "ref": "refs/heads/dev",
                "sha": "b" * 40,
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
                "task_id": "BFF-LIVE-EVIDENCE-PREFLIGHT",
                "strict_live_evidence_preflight": True,
                "github_environment": "dev",
                "missing": [],
                "invalid": [],
                "output_scope": ".lovable/audits/current-run",
                "ref": "refs/heads/dev",
                "sha": "c" * 40,
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


def test_verifier_rejects_master_preflight_provenance_even_when_checks_pass(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    preflight_path = artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    preflight["ref"] = "refs/heads/master"
    preflight_path.write_text(json.dumps(preflight), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["overall"] == "fail"
    item = payload["criteria"]["preflight_ready"]
    assert item["status"] == "fail"
    assert item["note"] == "provenance:ref"


def test_verifier_rejects_missing_preflight_provenance_even_when_checks_pass(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    preflight_path = artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    preflight.pop("task_id")
    preflight["strict_live_evidence_preflight"] = False
    preflight["output_scope"] = ".lovable/audits/historical"
    preflight["github_environment"] = "production"
    preflight["sha"] = "not-a-sha"
    preflight_path.write_text(json.dumps(preflight), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["preflight_ready"]
    assert item["status"] == "fail"
    assert item["note"] == "provenance:task_id,strict_live_evidence_preflight,output_scope,github_environment,sha"


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


def test_verifier_rejects_sensitive_json_key_without_echoing_secret(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    leaked = "opaque-live-token-abc123456789"
    (artifact_dir / "sensitive-key.json").write_text(
        json.dumps({"auth": {"token": leaked}}),
        encoding="utf-8",
    )

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["overall"] == "fail"
    item = payload["criteria"]["raw_secret_scan"]
    assert item["status"] == "fail"
    assert "sensitive-key.json:json_key:$.auth.token" in item["note"]
    assert leaked not in item["note"]


def test_verifier_accepts_redacted_sensitive_json_keys(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    (artifact_dir / "redacted-sensitive.json").write_text(
        json.dumps(
            {
                "authorization": "[REDACTED]",
                "access_token": "<redacted>",
                "client_secret": "***",
                "api_key": "sha256_12:abcdef123456",
            }
        ),
        encoding="utf-8",
    )

    result = run_verifier(artifact_dir)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["criteria"]["raw_secret_scan"]["status"] == "pass"


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



def test_verifier_rejects_sse_without_heartbeat_or_duplicate_proof(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    sse_path = artifact_dir / "BFF-CONSOL-011-sse-replay-smoke.json"
    sse = json.loads(sse_path.read_text(encoding="utf-8"))
    blocks = sse["soak"]["bearer_polyfill"]["blocks"]
    blocks["heartbeat_count"] = 0
    blocks["duplicate_event_ids"] = ["evt-2"]
    sse_path.write_text(json.dumps(sse), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["sse_reconnect_soak"]
    assert item["status"] == "fail"
    assert "heartbeat:0/1" in item["note"]
    assert "duplicates:1" in item["note"]



def test_verifier_rejects_sse_reconnect_without_lineage_or_observed_sequence(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    sse_path = artifact_dir / "BFF-CONSOL-011-sse-replay-smoke.json"
    sse = json.loads(sse_path.read_text(encoding="utf-8"))
    bearer = sse["reconnect_sequence"]["bearer_polyfill"]
    bearer["observed_event_ids"][-1] = bearer["observed_event_ids"][0]
    bearer["duplicate_event_ids"] = [bearer["observed_event_ids"][0]]
    bearer["attempts"][-1]["lineage_checks"]["last_event_id_sent"] = False
    sse_path.write_text(json.dumps(sse), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["sse_reconnect_soak"]
    assert item["status"] == "fail"
    assert "attemptLineage:False" in item["note"]
    assert "observedSequence:False" in item["note"]
    assert "duplicates:1" in item["note"]
